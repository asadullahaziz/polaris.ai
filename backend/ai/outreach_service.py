"""
Outreach service (Graph 3 · P5, reshaped 2026-07-07) — the deterministic, invariant-
bearing core, kept **pure and synchronous** so the ledger / idempotency guarantees are
unit-testable without the Inngest dev server.

Reshaped to EXPLICIT recipients (the decomposed tool design): the model/UI selects who
gets what — `recipients = [{user_id, listing_ids, body?}]` — and this layer enforces
truth at commit: ownership, registered users, the delivery ledger, and idempotent sends.
Ranking is NOT done in here anymore (it's a separate read — `engine.rank_buyers_multi`);
the engine is only consulted to ANNOTATE rows with authoritative score/reason per
(listing, buyer), never to choose.

The split (architecture §6, §9):
  * `launch_outreach`  → validate → ledger-dedup per (buyer, listing) pair → persist
    campaign(`awaiting_approval`) + one recipient row per pair (per-buyer opener body,
    model-drafted or templated fallback) + a notification.
  * `approve_campaign` / `cancel_campaign` → the send-gate (batch level). Approval flips
    the campaign to `sending`; the CALLER (REST view or copilot tool) emits the durable
    `outreach/approved` event — the service stays free of Inngest so it's testable.
  * `send_to_buyer`    → the per-BUYER send: ONE opener message per buyer covering all
    their surviving listings (multi-listing = multiple attachments). The LEDGER GUARANTEE
    lives per (listing, buyer): already-reached pairs drop out; survivors flip to SENT
    under the partial-unique constraint; a buyer whose pairs ALL drop gets no message.
    Opener idempotency via `dedup_key=outreach:c{campaign}:u{buyer}` + ON CONFLICT.
    Called once per Inngest fan-out step; safe to replay.

Nothing here calls a model. The only LLM in Graph 3 is the copilot's narration/drafting
and the one post-fan-out summary (`ai/functions.py`).
"""

from __future__ import annotations

import logging
from decimal import Decimal

from django.db import IntegrityError, transaction
from django.db.models import F, Max
from django.utils import timezone

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# The templated fallback opener (used when the caller supplies no body)
# ---------------------------------------------------------------------------
def _deal_phrase(prop, asking_price) -> str:
    beds = f"{prop.beds}bd" if prop and prop.beds else "investment"
    sqft = f"/{prop.sqft:,} sqft" if prop and prop.sqft else ""
    where = (prop.address_raw if prop and prop.address_raw else "the area").split(",")[0]
    ask = f"${float(asking_price):,.0f}" if asking_price is not None else "an attractive price"
    return f"a {beds}{sqft} deal at {where}, asking {ask}"


def build_opener(deals: list[tuple], name: str) -> str:
    """Deterministic fallback opener over one or more (property, asking_price) deals.
    Kept generic (no engine reason) so the confirm-card preview and the sent text are
    IDENTICAL — the copilot normally drafts a personalized body instead."""
    first = name.split()[0] if name else "there"
    phrases = [_deal_phrase(p, ap) for p, ap in deals]
    if len(phrases) == 1:
        middle = f"an off-market deal that fits what you buy: {phrases[0]}"
    else:
        middle = f"{len(phrases)} off-market deals that fit what you buy: " + "; ".join(phrases)
    return f"Hi {first}, I've got {middle} — priced below recent comps. Want the numbers?"


# ---------------------------------------------------------------------------
# Launch: validate → ledger-dedup per pair → persist (awaiting approval)
# ---------------------------------------------------------------------------
@transaction.atomic
def launch_outreach(seller_id: int, recipients: list[dict], *, copilot_ai_chat_id=None) -> dict:
    """Persist a draft campaign from EXPLICIT selections. `recipients` =
    [{"user_id", "listing_ids", "body"?}] — each buyer gets one opener covering exactly
    the listing_ids given for them (the caller sends each buyer only what they match).
    Validation is strict (any foreign listing / unknown user fails the whole launch);
    already-contacted (buyer, listing) pairs are staged as skipped, never re-sent."""
    from django.contrib.auth import get_user_model

    from catalog.models import Listing, ListingProperty
    from matching.engine import rank_buyers
    from notifications.models import Notification

    from .models import OutreachCampaign, OutreachRecipient

    recipients = [r for r in (recipients or []) if r.get("listing_ids")]
    if not recipients:
        return {"error": "no recipients (each needs a user_id and at least one listing_id)"}

    all_listing_ids = sorted({int(lid) for r in recipients for lid in r["listing_ids"]})
    owned = {
        lst.id: lst for lst in Listing.objects.filter(id__in=all_listing_ids, seller_id=seller_id)
    }
    missing = [lid for lid in all_listing_ids if lid not in owned]
    if missing:
        return {"error": f"listing(s) {missing} not found or not yours"}

    props: dict[int, object] = {}  # listing_id -> first property (address/template basis)
    for lp in (
        ListingProperty.objects.filter(listing_id__in=all_listing_ids)
        .select_related("property")
        .order_by("listing_id", "sort_order")
    ):
        props.setdefault(lp.listing_id, lp.property)

    User = get_user_model()
    user_ids = [int(r["user_id"]) for r in recipients]
    users = {u.id: u for u in User.objects.filter(id__in=user_ids)}
    bad = sorted({uid for uid in user_ids if uid not in users or uid == seller_id})
    if bad:
        return {"error": f"recipient user(s) {bad} not found (or the seller themself)"}

    # Engine annotation — authoritative score/reason per (listing, buyer). The caller
    # SELECTED; the engine NUMBERS. Pairs the engine doesn't rank stay None (still valid
    # to contact — e.g. a buyer the user named explicitly).
    ann: dict[tuple[int, int], tuple[float, str]] = {}
    for lid in all_listing_ids:
        prop = props.get(lid)
        if prop is None or getattr(prop, "geom", None) is None:
            continue
        for row in rank_buyers(lid, limit=1000).get("ranked", []):
            ann[(lid, row["user_id"])] = (row["score"], row["reason"])

    campaign = OutreachCampaign.objects.create(
        listing_id=all_listing_ids[0] if len(all_listing_ids) == 1 else None,
        seller_id=seller_id,
        copilot_ai_chat_id=copilot_ai_chat_id,
        status="awaiting_approval",
    )

    out: list[dict] = []
    pending = skipped = 0
    for r in recipients:
        uid = int(r["user_id"])
        name = users[uid].display_name
        lids = list(dict.fromkeys(int(x) for x in r["listing_ids"]))
        statuses: dict[int, str] = {}
        pending_lids: list[int] = []
        for lid in lids:
            if _ledger_already_sent(lid, uid):
                statuses[lid] = "skipped_already_contacted"
                skipped += 1
            else:
                statuses[lid] = "pending"
                pending_lids.append(lid)
                pending += 1
        body = (r.get("body") or "").strip() or build_opener(
            [(props.get(lid), owned[lid].asking_price) for lid in (pending_lids or lids)], name
        )
        for lid in lids:
            sr = ann.get((lid, uid))
            OutreachRecipient.objects.create(
                campaign=campaign,
                listing_id=lid,
                recipient_user_id=uid,
                rank_score=Decimal(str(sr[0])) if sr else None,
                rank_reason=sr[1] if sr else None,
                draft_body=body,
                status=statuses[lid],
            )
        out.append(
            {
                "user_id": uid,
                "name": name,
                "listings": [{"listing_id": lid, "status": statuses[lid]} for lid in lids],
                "body": body,
            }
        )

    Notification.objects.create(
        user_id=seller_id,
        type="approval_required",
        payload={"campaign_id": campaign.id, "listing_ids": all_listing_ids, "pending": pending},
    )
    return {
        "campaign_id": campaign.id,
        "listings": [
            {
                "id": lid,
                "address": props[lid].address_raw if props.get(lid) else None,
                "asking_price": _f(owned[lid].asking_price),
            }
            for lid in all_listing_ids
        ],
        "pending_count": pending,
        "skipped_count": skipped,
        "recipients": out,
        "note": (
            "Draft campaign saved, awaiting your approval."
            if pending
            else "Every (buyer, listing) pair here was already contacted — nothing to send."
        ),
    }


def _ledger_already_sent(listing_id, user_id) -> bool:
    """Has this listing already reached this buyer (a SENT ledger row, any campaign)?"""
    from .models import OutreachRecipient

    return OutreachRecipient.objects.filter(
        listing_id=listing_id, recipient_user_id=user_id, status="sent"
    ).exists()


# ---------------------------------------------------------------------------
# Approve / cancel (the batch-level send gate)
# ---------------------------------------------------------------------------
def approve_campaign(seller_id: int, campaign_id: int) -> dict:
    from .models import OutreachCampaign

    c = OutreachCampaign.objects.filter(id=campaign_id, seller_id=seller_id).first()
    if c is None:
        return {"error": "campaign not found"}
    if c.status != "awaiting_approval":
        return {"error": f"campaign is already {c.status}"}
    c.status = "sending"
    c.save(update_fields=["status"])
    return {"campaign_id": c.id, "status": "sending", "copilot_ai_chat_id": c.copilot_ai_chat_id}


def cancel_campaign(seller_id: int, campaign_id: int) -> dict:
    from .models import OutreachCampaign, OutreachRecipient

    c = OutreachCampaign.objects.filter(id=campaign_id, seller_id=seller_id).first()
    if c is None:
        return {"error": "campaign not found"}
    if c.status in ("done", "cancelled"):
        return {"error": f"campaign is already {c.status}"}
    OutreachRecipient.objects.filter(campaign=c, status="pending").update(status="cancelled")
    c.status = "cancelled"
    c.save(update_fields=["status"])
    return {"campaign_id": c.id, "status": "cancelled"}


# ---------------------------------------------------------------------------
# Send one BUYER — the ledger guarantee per pair, ONE message per buyer
# ---------------------------------------------------------------------------
@transaction.atomic
def send_to_buyer(campaign_id: int, user_id: int) -> dict:
    """Open the (seller, buyer) pair chat and post ONE opener covering every listing in
    this campaign that survives the ledger for this buyer. Per-pair guarantee: a listing
    reaches a buyer once, ever — an already-reached pair drops out of the attachments
    (a buyer whose pairs ALL drop gets no message at all). Safe to replay (Inngest
    at-least-once): flips + message + notification are one transaction, and the opener
    insert is idempotent under `outreach:c{campaign}:u{buyer}`."""

    from .models import OutreachRecipient

    rows = list(
        OutreachRecipient.objects.select_related("campaign")
        .filter(campaign_id=campaign_id, recipient_user_id=user_id)
        .order_by(F("rank_score").desc(nulls_last=True), "listing_id")
    )
    if not rows:
        return {"status": "missing"}
    if all(r.status == "cancelled" for r in rows):
        return {"status": "cancelled"}

    chat = _get_or_create_pair_chat(rows[0].campaign.seller_id, user_id)

    newly_flipped: list = []
    for r in rows:
        if r.status != "pending":
            continue
        # Layer 1: skip if this (listing, buyer) pair was reached by another campaign.
        if _ledger_already_sent(r.listing_id, user_id):
            r.status = "skipped_already_contacted"
            r.chat = chat
            r.save(update_fields=["status", "chat"])
            continue
        # Layer 2 (the guarantee): flip to SENT under the partial-unique ledger
        # constraint. A concurrent send that also passed layer 1 loses the race → skip.
        try:
            with transaction.atomic():
                r.status = "sent"
                r.chat = chat
                r.sent_at = timezone.now()
                r.save(update_fields=["status", "chat", "sent_at"])
            newly_flipped.append(r)
        except IntegrityError:
            r.refresh_from_db()
            r.status = "skipped_already_contacted"
            r.chat = chat
            r.save(update_fields=["status", "chat"])

    sent_rows = [r for r in rows if r.status == "sent"]
    if not sent_rows:
        return {"status": "skipped", "chat_id": chat.id}

    key = f"outreach:c{campaign_id}:u{user_id}"
    opener_id, created = _insert_opener(
        chat,
        rows[0].campaign.seller_id,
        sent_rows[0].draft_body or "",
        [r.listing_id for r in sent_rows],
        key,
    )
    if created:
        from notifications.models import Notification

        Notification.objects.create(
            user_id=user_id,
            type="outreach_received",
            chat=chat,
            payload={
                "listing_ids": [r.listing_id for r in sent_rows],
                "campaign_id": campaign_id,
            },
        )
    # Mini CRM: every sent (listing, buyer) pair opens a pipeline deal. Idempotent
    # under the (listing, buyer) unique constraint; defensive so a deals bug never
    # fails the fan-out step.
    try:
        from deals.service import ensure_deal

        for r in sent_rows:
            ensure_deal(
                listing_id=r.listing_id,
                buyer_id=user_id,
                seller_id=rows[0].campaign.seller_id,
                chat_id=chat.id,
                stage="contacted",
            )
    except Exception:  # noqa: BLE001
        log.warning("deal creation failed for campaign %s buyer %s", campaign_id, user_id)
    return {
        "status": "sent" if (created or newly_flipped) else "already_sent",
        "chat_id": chat.id,
        # For the fan-out to arm the buyer's away-responder (Graph 2): the opener is an
        # inbound to the buyer's side.
        "recipient_user_id": user_id,
        "opener_message_id": opener_id,
        "listing_ids": [r.listing_id for r in sent_rows],
    }


def _get_or_create_pair_chat(seller_id: int, buyer_id: int):
    """The ONE chat for the (seller, buyer) pair — reused across listings/campaigns
    (revisions decision #3). A repeat outreach to the same buyer reopens this same chat
    and just attaches the new listing(s)."""
    from chat.services import get_or_create_chat

    chat, _ = get_or_create_chat(seller_id, buyer_id)
    return chat


def _insert_opener(chat, seller_id: int, body: str, listing_ids: list[int], key: str):
    """Insert the opener idempotently (dedup_key + ON CONFLICT DO NOTHING) with every
    surviving listing attached, so a fan-out replay never double-posts. Returns
    (message_id, created) — the existing row's id on a replay."""
    from chat.models import Chat, Message, MessageAttachment

    existing = Message.objects.filter(dedup_key=key).values_list("id", flat=True).first()
    if existing is not None:  # replay — the opener (and its attachments) already exist
        return existing, False
    try:
        with transaction.atomic():
            msg = Message.objects.create(
                chat=chat,
                kind="agent",  # sent by Polaris on the seller's behalf
                sender_id=seller_id,
                action="inform",
                body=body,
                status="sent",
                sent_at=timezone.now(),
                dedup_key=key,
            )
            MessageAttachment.objects.bulk_create(
                [
                    MessageAttachment(message=msg, kind="listing", listing_id=lid, sort_order=i)
                    for i, lid in enumerate(listing_ids)
                ]
            )
    except IntegrityError:  # lost the race to a concurrent replay
        return Message.objects.filter(dedup_key=key).values_list("id", flat=True).first(), False
    Chat.objects.filter(id=chat.id).update(updated_at=timezone.now())
    return msg.id, True


# ---------------------------------------------------------------------------
# Fan-out support (read by the Inngest function)
# ---------------------------------------------------------------------------
def campaign_dispatch_info(campaign_id: int) -> dict | None:
    """Everything the Inngest fan-out needs, without holding ORM objects across steps.
    The send unit is the BUYER: `buyer_ids` = distinct pending recipients, best rank
    first."""
    from catalog.models import ListingProperty

    from .models import OutreachCampaign

    c = OutreachCampaign.objects.filter(id=campaign_id).first()
    if c is None:
        return None
    listing_ids = sorted(set(c.recipients.values_list("listing_id", flat=True)))
    addresses: dict[int, str] = {}
    for lp in (
        ListingProperty.objects.filter(listing_id__in=listing_ids)
        .select_related("property")
        .order_by("listing_id", "sort_order")
    ):
        addresses.setdefault(lp.listing_id, lp.property.address_raw)
    buyer_ids = [
        row["recipient_user_id"]
        for row in c.recipients.filter(status="pending")
        .values("recipient_user_id")
        .annotate(best=Max("rank_score"))
        .order_by(F("best").desc(nulls_last=True), "recipient_user_id")
    ]
    return {
        "campaign_id": c.id,
        "seller_id": c.seller_id,
        "copilot_ai_chat_id": c.copilot_ai_chat_id,
        "status": c.status,
        "listing_ids": listing_ids,
        "listing_addresses": [addresses.get(lid, f"listing {lid}") for lid in listing_ids],
        "buyer_ids": buyer_ids,
    }


def campaign_outcome(campaign_id: int) -> dict:
    """Tallied at BUYER granularity (a buyer counts as reached if ≥1 of their pairs
    sent), for the progress ticks + final summary. Pair-level counts ride along."""
    from .models import OutreachRecipient

    per_buyer: dict[int, set[str]] = {}
    pair_counts: dict[str, int] = {}
    for row in OutreachRecipient.objects.filter(campaign_id=campaign_id).values(
        "recipient_user_id", "status"
    ):
        per_buyer.setdefault(row["recipient_user_id"], set()).add(row["status"])
        pair_counts[row["status"]] = pair_counts.get(row["status"], 0) + 1

    sent = skipped = failed = pending = 0
    for statuses in per_buyer.values():
        if "sent" in statuses:
            sent += 1
        elif "pending" in statuses:
            pending += 1
        elif "failed" in statuses:
            failed += 1
        elif "skipped_already_contacted" in statuses:
            skipped += 1
    return {
        "sent": sent,
        "skipped": skipped,
        "failed": failed,
        "pending": pending,
        "total": len(per_buyer),
        "pairs": pair_counts,
    }


def finish_campaign(campaign_id: int) -> dict:
    from .models import OutreachCampaign

    c = OutreachCampaign.objects.filter(id=campaign_id).first()
    if c is not None and c.status == "sending":
        c.status = "done"
        c.save(update_fields=["status"])
    return campaign_outcome(campaign_id)


def _f(v):
    return float(v) if v is not None else None
