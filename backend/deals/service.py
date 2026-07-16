"""
Deal-stage bookkeeping (mini CRM) — pure and synchronous, like `chat/responder_service`,
so every transition is unit-testable without Inngest, LangGraph, or a socket.

Called from four seams (all defensive at the call site — deal bookkeeping must never
break message delivery or the exactly-one-reply invariant):
  * ai/outreach_service.send_to_buyer  → ensure_deal(stage="contacted") per sent pair
  * chat/services.post_human_message / post_agent_message → on_message(...)
  * chat/responder_service.commit_reply / approve_draft → on_message + apply_agent_action

Automatic transitions are forward-only (`advance_stage`); `set_stage_manual` is the
human override and may move anywhere. `lost` is reachable automatically only from the
active stages; `closed` is manual-only.
"""

from __future__ import annotations

import logging

from django.db import IntegrityError
from django.utils import timezone

log = logging.getLogger(__name__)

# Forward order of the automatic pipeline. `lost` sits outside it (see advance_stage).
_ORDER = {"contacted": 0, "engaged": 1, "negotiating": 2, "agreed": 3, "closed": 4}
ACTIVE_STAGES = ("contacted", "engaged", "negotiating")
ALL_STAGES = ("contacted", "engaged", "negotiating", "agreed", "closed", "lost")


def _chat_member_ids(chat_id: int) -> list[int]:
    from chat.models import ChatMember

    return list(ChatMember.objects.filter(chat_id=chat_id).values_list("user_id", flat=True))


def ensure_deal(
    *,
    listing_id: int,
    buyer_id: int,
    seller_id: int,
    chat_id: int | None = None,
    stage: str = "contacted",
):
    """Get-or-create the (listing, buyer) deal, race-safe under the unique constraint.
    Backfills a missing chat link and forward-advances an existing deal that is behind
    the requested stage (never regresses)."""
    from .models import Deal

    try:
        deal, created = Deal.objects.get_or_create(
            listing_id=listing_id,
            buyer_id=buyer_id,
            defaults={"seller_id": seller_id, "chat_id": chat_id, "stage": stage},
        )
    except IntegrityError:  # concurrent create lost the race — the row exists now
        deal, created = Deal.objects.get(listing_id=listing_id, buyer_id=buyer_id), False

    if not created:
        updates: list[str] = []
        if deal.chat_id is None and chat_id is not None:
            deal.chat_id = chat_id
            updates.append("chat")
        if updates:
            deal.save(update_fields=[*updates, "updated_at"])
        advance_stage(deal, stage)
    return deal


def advance_stage(deal, target: str):
    """System path: forward-only. No-op unless `target` is strictly ahead of the current
    stage; `closed`/`lost` are sticky; `lost` is reachable only from an active stage
    (a deal already agreed falls through only by human override)."""
    current = deal.stage
    if current in ("closed", "lost"):
        return deal
    if target == "lost":
        if current not in ACTIVE_STAGES:
            return deal
    elif _ORDER.get(target, -1) <= _ORDER.get(current, 99):
        return deal
    deal.stage = target
    deal.stage_changed_at = timezone.now()
    deal.save(update_fields=["stage", "stage_changed_at", "updated_at"])
    return deal


def set_stage_manual(deal, stage: str):
    """Human override — any stage, any direction. A CRM must let its owner correct it."""
    if stage not in ALL_STAGES:
        raise ValueError(f"unknown stage {stage!r}")
    deal.stage = stage
    deal.stage_changed_at = timezone.now()
    deal.save(update_fields=["stage", "stage_changed_at", "updated_at"])
    return deal


def record_disclosed_offer(deal, *, by_user_id: int, price):
    """Record an agent-disclosed offer on the correct side. Human free-text offers are
    never parsed into this."""
    field = "last_offer_by_buyer" if by_user_id == deal.buyer_id else "last_offer_by_seller"
    setattr(deal, field, price)
    deal.save(update_fields=[field, "updated_at"])
    return deal


def on_message(chat_id: int, sender_id: int | None, listing_ids=None) -> None:
    """The every-message seam. (a) Attached listings create/advance deals: the listing
    owner pitching → contacted; anyone else sharing it → they're the buyer, engaged.
    Deals exist only when the listing's seller is one of the two chat members.
    (b) Any message from a deal's buyer advances contacted → engaged."""
    if sender_id is None:  # system message
        return
    from catalog.models import Listing

    from .models import Deal

    members = _chat_member_ids(chat_id)
    if len(members) != 2 or sender_id not in members:
        return
    other_id = next(uid for uid in members if uid != sender_id)

    for lid in listing_ids or []:
        row = Listing.objects.filter(id=lid).values("id", "seller_id").first()
        if row is None:
            continue
        seller_id = row["seller_id"]
        if seller_id == sender_id:
            buyer_id, stage = other_id, "contacted"  # owner pitching their listing
        elif seller_id == other_id:
            buyer_id, stage = sender_id, "engaged"  # buyer inquiring about it
        else:
            continue  # third-party listing — no pipeline between these two
        ensure_deal(
            listing_id=lid, buyer_id=buyer_id, seller_id=seller_id, chat_id=chat_id, stage=stage
        )

    # The buyer replying (anything) is engagement.
    for deal in Deal.objects.filter(chat_id=chat_id, stage="contacted", buyer_id=sender_id):
        advance_stage(deal, "engaged")


def focal_deal(chat_id: int, focal_listing_id: int | None):
    """The deal an agent turn is about: by (chat, focal listing) when known; else the
    most-recently-attached listing on the chat that has a deal; else the chat's single
    deal if unambiguous."""
    from chat.models import MessageAttachment

    from .models import Deal

    if focal_listing_id is not None:
        deal = Deal.objects.filter(chat_id=chat_id, listing_id=focal_listing_id).first()
        if deal is not None:
            return deal

    # Newest attachment wins — same ordering as dal._chat_listings.
    attached = list(
        MessageAttachment.objects.filter(
            message__chat_id=chat_id, kind="listing", listing__isnull=False
        )
        .order_by("-message__created_at", "-sort_order", "-id")
        .values_list("listing_id", flat=True)[:10]
    )
    for lid in attached:
        deal = Deal.objects.filter(chat_id=chat_id, listing_id=lid).first()
        if deal is not None:
            return deal

    deals = list(Deal.objects.filter(chat_id=chat_id)[:2])
    return deals[0] if len(deals) == 1 else None


def apply_agent_action(
    chat_id: int,
    principal_id: int,
    action: str,
    disclosed_fields: dict | None,
    *,
    intent: str | None = None,
    focal_listing_id: int | None = None,
) -> None:
    """Stage/offer side-effects of ONE agent message — shared by `commit_reply` (auto
    send) and `approve_draft` (human-approved send), so both paths keep the CRM true."""
    deal = focal_deal(chat_id, focal_listing_id)
    if deal is None:
        return

    offer = (disclosed_fields or {}).get("offer_price")
    if offer is not None:
        record_disclosed_offer(deal, by_user_id=principal_id, price=offer)

    if action in ("propose", "counter") or intent == "offer_negotiation":
        advance_stage(deal, "negotiating")
    if action == "accept":
        standing = (
            deal.last_offer_by_seller if principal_id == deal.buyer_id else deal.last_offer_by_buyer
        )
        if standing is not None and deal.agreed_price is None:
            deal.agreed_price = standing
            deal.save(update_fields=["agreed_price", "updated_at"])
        advance_stage(deal, "agreed")
    if action == "decline":
        advance_stage(deal, "lost")


def responder_context(chat_id: int, focal_listing_id: int | None, principal_id: int) -> dict:
    """What the responder graph needs from the CRM, stance-mapped to the principal.
    Private context.
    `other_active_deals` grounds honest urgency ("there's other interest") — the count
    of OTHER live deals on the same listing."""
    from .models import Deal

    deal = focal_deal(chat_id, focal_listing_id)
    if deal is None:
        return {"deal": None, "negotiation": None, "other_active_deals": 0}

    principal_is_buyer = principal_id == deal.buyer_id
    mine = deal.last_offer_by_buyer if principal_is_buyer else deal.last_offer_by_seller
    theirs = deal.last_offer_by_seller if principal_is_buyer else deal.last_offer_by_buyer
    other_active = (
        Deal.objects.filter(listing_id=deal.listing_id, stage__in=ACTIVE_STAGES)
        .exclude(pk=deal.pk)
        .count()
    )
    return {
        "deal": {
            "id": deal.id,
            "listing_id": deal.listing_id,
            "stage": deal.stage,
            "agreed_price": int(deal.agreed_price) if deal.agreed_price is not None else None,
        },
        "negotiation": {
            "my_last_offer": int(mine) if mine is not None else None,
            "their_last_offer": int(theirs) if theirs is not None else None,
        },
        "other_active_deals": other_active,
    }
