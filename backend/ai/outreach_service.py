"""
Outreach service (Graph 3 · P5) — the deterministic, invariant-bearing core, kept
**pure and synchronous** so the ledger / idempotency guarantees are unit-testable
without the Inngest dev server. Ported from v1 `_v1_reference/outreach/service.py` and
rewired to the v2 chat schema (registered users only; the thread is the ONE pair
`chat.Chat`; the opener is an agent message + a `MessageAttachment(kind=listing)` — there
is no `subject_listing`).

The split (architecture §6, §9):
  * `launch_outreach`  → rank (engine) → dedup against the delivery ledger → draft
    per-buyer openers (templated, no LLM) → persist campaign(`awaiting_approval`) +
    recipients(`pending`, with draft/score/reason) + a notification. Called from the
    copilot `launch_outreach` tool; the turn narrates the result and ENDS.
  * `approve_campaign` / `cancel_campaign` → the send-gate (batch level). Approval flips
    the campaign to `sending`; the CALLER (REST view or copilot tool) emits the durable
    `outreach/approved` event — the service stays free of Inngest so it's testable.
  * `send_recipient`   → the per-buyer send: the LEDGER GUARANTEE lives here (skip-if-
    already-sent + the partial-unique `status='sent'` constraint) plus opener idempotency
    (`dedup_key` + ON CONFLICT DO NOTHING). Called once per Inngest fan-out step; safe to
    replay.

Nothing here calls a model. The only LLM in Graph 3 is the copilot's narration and the
one post-fan-out summary (`ai/functions.py`).
"""

from __future__ import annotations

from decimal import Decimal

from django.db import IntegrityError, transaction
from django.utils import timezone


# ---------------------------------------------------------------------------
# Launch: rank → dedup → draft → persist (awaiting approval)
# ---------------------------------------------------------------------------
def build_opener(prop, asking_price, name: str, reason: str) -> str:
    """A templated per-buyer opener (sent verbatim on approval). Deterministic — no LLM at
    launch (that would be N blocking calls); the copilot narrates the shortlist instead,
    and the reason personalizes each opener."""
    beds = f"{prop.beds}bd" if prop and prop.beds else "investment"
    sqft = f"/{prop.sqft:,} sqft" if prop and prop.sqft else ""
    where = (prop.address_raw if prop and prop.address_raw else "King County").split(",")[0]
    ask = f"${float(asking_price):,.0f}" if asking_price is not None else "an attractive price"
    first = name.split()[0] if name else "there"
    return (
        f"Hi {first}, I've got an off-market {beds}{sqft} deal at {where}, asking {ask} — "
        f"priced below recent comps. Flagging you because you {reason}. Want the numbers?"
    )


@transaction.atomic
def launch_outreach(
    seller_id: int, listing_id: int, *, copilot_ai_chat_id=None, limit: int = 10
) -> dict:
    """Rank buyers, draft openers, and persist a draft campaign awaiting approval.
    Returns a compact shortlist for the copilot to narrate."""
    from catalog.models import Listing, ListingProperty
    from matching.engine import rank_buyers
    from notifications.models import Notification

    from .models import OutreachCampaign, OutreachRecipient

    listing = Listing.objects.filter(id=listing_id, seller_id=seller_id).first()
    if listing is None:
        return {"error": f"listing {listing_id} not found or not yours"}
    lp = ListingProperty.objects.filter(listing_id=listing_id).select_related("property").first()
    prop = lp.property if lp else None
    if prop is None or prop.geom is None:
        return {"error": "listing has no geolocated property, so buyers can't be ranked"}

    ranked = rank_buyers(listing_id, limit=limit)
    rows = ranked.get("ranked", [])
    if not rows:
        return {
            "campaign_id": None,
            "ranked": [],
            "note": "No matching buyers found near this listing.",
        }

    campaign = OutreachCampaign.objects.create(
        listing=listing,
        seller_id=seller_id,
        copilot_ai_chat_id=copilot_ai_chat_id,
        status="awaiting_approval",
    )

    out: list[dict] = []
    pending = skipped = 0
    for r in rows:
        already = _ledger_already_sent(listing_id, r["user_id"])
        status = "skipped_already_contacted" if already else "pending"
        draft = build_opener(prop, listing.asking_price, r["name"], r["reason"])
        OutreachRecipient.objects.create(
            campaign=campaign,
            listing=listing,
            recipient_user_id=r["user_id"],
            rank_score=Decimal(str(r["score"])),
            rank_reason=r["reason"],
            draft_body=draft,
            status=status,
        )
        if already:
            skipped += 1
        else:
            pending += 1
        out.append(
            {
                "name": r["name"],
                "score": r["score"],
                "reason": r["reason"],
                "status": status,
            }
        )

    Notification.objects.create(
        user_id=seller_id,
        type="approval_required",
        payload={"campaign_id": campaign.id, "listing_id": listing_id, "pending": pending},
    )
    return {
        "campaign_id": campaign.id,
        "listing": {"id": listing_id, "address": prop.address_raw, "asking_price": _f(listing.asking_price)},
        "pending_count": pending,
        "skipped_count": skipped,
        "ranked": out,
        "note": "Draft campaign saved, awaiting your approval. Approve it to open the chats.",
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
# Send one recipient — the ledger guarantee + opener idempotency
# ---------------------------------------------------------------------------
@transaction.atomic
def send_recipient(recipient_id: int) -> dict:
    """Open the (seller, buyer) pair chat and post the opener, exactly once, ever. Safe to
    replay (Inngest at-least-once). Returns {status, chat_id, ...}."""
    from .models import OutreachRecipient

    rec = (
        OutreachRecipient.objects.select_related("campaign", "listing")
        .filter(id=recipient_id)
        .first()
    )
    if rec is None:
        return {"status": "missing"}
    if rec.status == "sent":
        return {"status": "already_sent", "chat_id": rec.chat_id}
    if rec.status == "cancelled":
        return {"status": "cancelled"}

    # Layer 1: skip if this listing already reached this buyer (another campaign).
    if _ledger_already_sent(rec.listing_id, rec.recipient_user_id):
        rec.status = "skipped_already_contacted"
        rec.save(update_fields=["status"])
        return {"status": "skipped"}

    chat = _get_or_create_pair_chat(rec)

    # Layer 2 (the guarantee): flip to SENT under the partial-unique ledger constraint.
    # A concurrent send that also passed layer 1 loses the race here → skip.
    try:
        with transaction.atomic():
            rec.status = "sent"
            rec.chat = chat
            rec.sent_at = timezone.now()
            rec.save(update_fields=["status", "chat", "sent_at"])
    except IntegrityError:
        rec.refresh_from_db()
        rec.status = "skipped_already_contacted"
        rec.chat = chat
        rec.save(update_fields=["status", "chat"])
        return {"status": "skipped", "chat_id": chat.id}

    opener_id = _insert_opener(chat, rec)
    from notifications.models import Notification

    Notification.objects.create(
        user_id=rec.recipient_user_id,
        type="outreach_received",
        chat=chat,
        payload={"listing_id": rec.listing_id, "campaign_id": rec.campaign_id},
    )
    return {
        "status": "sent",
        "chat_id": chat.id,
        # For the fan-out to arm the buyer's away-responder (Graph 2): the opener is an
        # inbound to the buyer's side. Every recipient is a registered user in v2.
        "recipient_user_id": rec.recipient_user_id,
        "opener_message_id": opener_id,
    }


def _get_or_create_pair_chat(rec):
    """The ONE chat for the (seller, buyer) pair — reused across listings/campaigns
    (revisions decision #3). A second outreach to the same buyer reopens this same chat
    and just attaches the new listing."""
    from chat.services import get_or_create_chat

    chat, _ = get_or_create_chat(rec.campaign.seller_id, rec.recipient_user_id)
    return chat


def _opener_dedup_key(rec) -> str:
    """Namespaced so it never collides with human (`human:…`) or autoreply (`autoreply:…`)
    dedup keys on the shared `uniq_msg_dedup` index. One opener per (listing, buyer)."""
    return f"outreach:{rec.listing_id}:u{rec.recipient_user_id}"


def _insert_opener(chat, rec) -> int | None:
    """Insert the opener idempotently (dedup_key + ON CONFLICT DO NOTHING) with the
    listing attached, so a fan-out replay never double-posts. Returns the opener message id
    (for the away-responder trigger) — the existing row's id on a replay."""
    from chat.models import Chat, Message, MessageAttachment

    key = _opener_dedup_key(rec)
    existing = Message.objects.filter(dedup_key=key).values_list("id", flat=True).first()
    if existing is not None:  # replay — the opener (and its attachment) already exist
        return existing
    try:
        with transaction.atomic():
            msg = Message.objects.create(
                chat=chat,
                kind="agent",  # sent by Polaris on the seller's behalf
                sender_id=rec.campaign.seller_id,
                action="inform",
                body=rec.draft_body or "",
                status="sent",
                sent_at=timezone.now(),
                dedup_key=key,
            )
            MessageAttachment.objects.create(
                message=msg, kind="listing", listing_id=rec.listing_id, sort_order=0
            )
    except IntegrityError:  # lost the race to a concurrent replay
        return Message.objects.filter(dedup_key=key).values_list("id", flat=True).first()
    Chat.objects.filter(id=chat.id).update(updated_at=timezone.now())
    return msg.id


# ---------------------------------------------------------------------------
# Fan-out support (read by the Inngest function)
# ---------------------------------------------------------------------------
def campaign_dispatch_info(campaign_id: int) -> dict | None:
    """Everything the Inngest fan-out needs, without holding ORM objects across steps."""
    from catalog.models import ListingProperty

    from .models import OutreachCampaign

    c = OutreachCampaign.objects.filter(id=campaign_id).first()
    if c is None:
        return None
    lp = ListingProperty.objects.filter(listing_id=c.listing_id).select_related("property").first()
    address = lp.property.address_raw if lp and lp.property else f"listing {c.listing_id}"
    return {
        "campaign_id": c.id,
        "seller_id": c.seller_id,
        "copilot_ai_chat_id": c.copilot_ai_chat_id,
        "status": c.status,
        "listing_id": c.listing_id,
        "listing_address": address,
        "recipient_ids": list(
            c.recipients.filter(status="pending")
            .order_by("-rank_score")
            .values_list("id", flat=True)
        ),
    }


def campaign_outcome(campaign_id: int) -> dict:
    """Tallied counts for the progress ticks + final summary."""
    from django.db.models import Count

    from .models import OutreachRecipient

    counts = {
        row["status"]: row["n"]
        for row in OutreachRecipient.objects.filter(campaign_id=campaign_id)
        .values("status")
        .annotate(n=Count("id"))
    }
    return {
        "sent": counts.get("sent", 0),
        "skipped": counts.get("skipped_already_contacted", 0),
        "failed": counts.get("failed", 0),
        "pending": counts.get("pending", 0),
        "total": sum(counts.values()),
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
