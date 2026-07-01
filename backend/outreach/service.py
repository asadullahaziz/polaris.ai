"""
Outreach service (implementation_plan P2.2/P2.4/P2.6) — the deterministic,
invariant-bearing core of Graph 3, deliberately kept **pure and synchronous** so the
ledger/idempotency guarantees are unit-testable without the Inngest dev server.

The split (architecture §6, §9):
  * `launch_outreach`  → rank (engine) → dedup against the delivery ledger → draft
    per-buyer openers (templated, no LLM) → persist campaign(`awaiting_approval`) +
    recipients(`pending`, with draft/score/reason) + a notification. Called from the
    copilot `launch_outreach` tool; the turn narrates the result and ENDS.
  * `approve_campaign` / `cancel_campaign` → the send-gate (batch level).
  * `send_recipient`   → the per-buyer send: the LEDGER GUARANTEE lives here
    (skip-if-already-sent + the partial-unique `status='sent'` constraint) plus the
    opener message idempotency (`dedup_key` + ON CONFLICT DO NOTHING). Called once
    per Inngest fan-out step; safe to replay.

Nothing here calls a model. The only LLM in Graph 3 is the copilot's narration and
the one post-fan-out summary (outreach/functions.py).
"""

from __future__ import annotations

from decimal import Decimal

from django.db import IntegrityError, transaction
from django.utils import timezone


# ---------------------------------------------------------------------------
# Launch: rank → dedup → draft → persist (awaiting approval)
# ---------------------------------------------------------------------------
def build_opener(prop, asking_price, name: str, reason: str) -> str:
    """A templated per-buyer opener (sent verbatim on approval). Deterministic — no
    LLM at launch (that would be N blocking calls); the copilot narrates the shortlist
    instead, and the reason personalizes each opener."""
    beds = f"{prop.beds}bd" if prop and prop.beds else "investment"
    sqft = f"/{prop.sqft:,} sqft" if prop and prop.sqft else ""
    where = (prop.address_raw if prop and prop.address_raw else "King County").split(",")[0]
    ask = f"${float(asking_price):,.0f}" if asking_price is not None else "an attractive price"
    return (
        f"Hi {name.split()[0] if name else 'there'}, I've got an off-market {beds}{sqft} "
        f"deal at {where}, asking {ask} — priced below recent comps. "
        f"Flagging you because you {reason}. Want the numbers?"
    )


@transaction.atomic
def launch_outreach(seller_id: int, listing_id: int, *, conversation_id=None, limit: int = 10) -> dict:
    """Rank buyers, draft openers, and persist a draft campaign awaiting approval.
    Returns a compact shortlist for the copilot to narrate."""
    from agent_context.models import AgentActionLog
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
        copilot_conversation_id=conversation_id,
        status="awaiting_approval",
    )

    out: list[dict] = []
    pending = skipped = 0
    for r in rows:
        already = _ledger_already_sent(listing_id, r["user_id"], r["prospect_id"])
        status = "skipped_already_contacted" if already else "pending"
        draft = build_opener(prop, listing.asking_price, r["name"], r["reason"])
        OutreachRecipient.objects.create(
            campaign=campaign,
            listing=listing,
            recipient_user_id=r["user_id"],
            recipient_prospect_id=r["prospect_id"],
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
                "kind": r["kind"],
                "registered": r["registered"],
                "score": r["score"],
                "reason": r["reason"],
                "status": status,
            }
        )

    Notification.objects.create(
        user_id=seller_id,
        type="approval_required",
        conversation_id=conversation_id,
        payload={"campaign_id": campaign.id, "listing_id": listing_id, "pending": pending},
    )
    AgentActionLog.objects.create(
        principal_id=seller_id,
        conversation_id=conversation_id,
        action_type="ranked",
        summary=f"Ranked {len(rows)} buyers for listing {listing_id}; {pending} to contact",
        payload={"campaign_id": campaign.id, "pending": pending, "skipped": skipped},
    )

    address = prop.address_raw
    return {
        "campaign_id": campaign.id,
        "listing": {"id": listing_id, "address": address, "asking_price": _f(listing.asking_price)},
        "pending_count": pending,
        "skipped_count": skipped,
        "ranked": out,
        "note": "Draft campaign saved, awaiting your approval. Approve it to open the threads.",
    }


def _ledger_already_sent(listing_id, user_id, prospect_id) -> bool:
    """Has this listing already reached this buyer (a SENT ledger row, any campaign)?"""
    from .models import OutreachRecipient

    q = OutreachRecipient.objects.filter(listing_id=listing_id, status="sent")
    if user_id:
        return q.filter(recipient_user_id=user_id).exists()
    return q.filter(recipient_prospect_id=prospect_id).exists()


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
    return {"campaign_id": c.id, "status": "sending", "conversation_id": c.copilot_conversation_id}


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
    """Open the (listing, buyer) thread and post the opener, exactly once, ever.
    Safe to replay (Inngest at-least-once). Returns {status, conversation_id}."""
    from .models import OutreachRecipient

    rec = (
        OutreachRecipient.objects.select_related("campaign", "listing")
        .filter(id=recipient_id)
        .first()
    )
    if rec is None:
        return {"status": "missing"}
    if rec.status == "sent":
        return {"status": "already_sent", "conversation_id": rec.conversation_id}
    if rec.status == "cancelled":
        return {"status": "cancelled"}

    # Layer 1: skip if this listing already reached this buyer (another campaign).
    if _ledger_already_sent(rec.listing_id, rec.recipient_user_id, rec.recipient_prospect_id):
        rec.status = "skipped_already_contacted"
        rec.save(update_fields=["status"])
        return {"status": "skipped"}

    conv = _get_or_create_thread(rec)

    # Layer 2 (the guarantee): flip to SENT under the partial-unique ledger constraint.
    # A concurrent send that also passed layer 1 loses the race here → skip.
    try:
        with transaction.atomic():
            rec.status = "sent"
            rec.conversation = conv
            rec.sent_at = timezone.now()
            rec.save(update_fields=["status", "conversation", "sent_at"])
    except IntegrityError:
        rec.refresh_from_db()
        rec.status = "skipped_already_contacted"
        rec.conversation = conv
        rec.save(update_fields=["status", "conversation"])
        return {"status": "skipped", "conversation_id": conv.id}

    _insert_opener(conv, rec)
    if rec.recipient_user_id:  # prospects are one-way (no platform user to notify)
        from notifications.models import Notification

        Notification.objects.create(
            user_id=rec.recipient_user_id,
            type="outreach_received",
            conversation=conv,
            payload={"listing_id": rec.listing_id},
        )
    return {"status": "sent", "conversation_id": conv.id}


def _get_or_create_thread(rec):
    """The (listing × counterparty) thread. Uniqueness (uniq_thread_*) enforces one."""
    from conversations.models import Conversation

    if rec.recipient_user_id:
        conv, _ = Conversation.objects.get_or_create(
            kind="thread",
            listing_id=rec.listing_id,
            counterparty_user_id=rec.recipient_user_id,
            defaults={"status": "open"},
        )
    else:
        conv, _ = Conversation.objects.get_or_create(
            kind="thread",
            listing_id=rec.listing_id,
            counterparty_prospect_id=rec.recipient_prospect_id,
            defaults={"status": "open"},
        )
    return conv


def _opener_dedup_key(rec) -> str:
    tag = f"u{rec.recipient_user_id}" if rec.recipient_user_id else f"p{rec.recipient_prospect_id}"
    return f"outreach:{rec.listing_id}:{tag}"


def _insert_opener(conv, rec) -> None:
    """Insert the opener message idempotently (dedup_key + ON CONFLICT DO NOTHING),
    so a fan-out replay never double-posts."""
    from conversations.models import Conversation, Message

    Message.objects.bulk_create(
        [
            Message(
                conversation=conv,
                author_type="agent",  # sent by Polaris on the seller's behalf
                author_side="seller",
                author_id=rec.campaign.seller_id,
                action="inform",
                body=rec.draft_body or "",
                status="sent",
                sent_at=timezone.now(),
                dedup_key=_opener_dedup_key(rec),
            )
        ],
        ignore_conflicts=True,
    )
    Conversation.objects.filter(id=conv.id).update(updated_at=timezone.now())


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
        "conversation_id": c.copilot_conversation_id,
        "status": c.status,
        "listing_id": c.listing_id,
        "listing_address": address,
        "recipient_ids": list(
            c.recipients.filter(status="pending").order_by("-rank_score").values_list("id", flat=True)
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
