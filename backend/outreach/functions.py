"""
Outreach fan-out Inngest function (implementation_plan P2.4/P2.5, architecture §6/§9).

Triggered by `outreach.approved` (emitted by the approve REST view). This is the
durable post-approval half of Graph 3 — the seller may close the tab; it outlives
the copilot turn. It is deliberately **thin glue**: every guarantee (ledger,
opener idempotency, thread uniqueness) lives in `outreach.service.send_recipient`,
which is pure/sync and unit-tested without this dev server.

Three jobs, split along the §9 boundary:
  1. one durable step per recipient → `send_recipient` (retries, concurrency);
  2. **templated** progress ticks pushed to the seller's copilot chat over the WS
     channel layer — NO model call per tick;
  3. exactly one final NL summary — one model narration (templated fallback),
     persisted as an agent message and pushed as `outreach.summary`.
"""

from __future__ import annotations

import logging

import inngest
from asgiref.sync import sync_to_async
from channels.layers import get_channel_layer

from orchestration.client import inngest_client
from polaris_agent import dal

from . import service

log = logging.getLogger(__name__)


def _group(seller_id: int) -> str:
    return f"copilot_{seller_id}"


async def _tick(seller_id: int, payload: dict) -> None:
    """Push a templated progress/summary event to the seller's copilot socket group."""
    try:
        layer = get_channel_layer()
        await layer.group_send(_group(seller_id), payload)
    except Exception as exc:  # pragma: no cover - WS is best-effort, never breaks the fan-out
        log.warning("outreach tick failed: %s", exc)


@inngest_client.create_function(
    fn_id="outreach-fanout",
    trigger=inngest.TriggerEvent(event="outreach/approved"),
    concurrency=[inngest.Concurrency(limit=5)],
    retries=2,
)
async def outreach_fanout(ctx: inngest.Context) -> dict:
    campaign_id = ctx.event.data["campaign_id"]
    info = await sync_to_async(service.campaign_dispatch_info)(campaign_id)
    if info is None or info["status"] != "sending":
        return {"skipped": True, "reason": "campaign not in sending state"}

    seller_id = info["seller_id"]
    conv_id = info["conversation_id"]
    recipient_ids = info["recipient_ids"]
    total = len(recipient_ids)
    sent = skipped = 0

    for i, rid in enumerate(recipient_ids, start=1):
        try:
            res = await ctx.step.run(f"send-{rid}", sync_to_async(service.send_recipient), rid)
        except Exception as exc:  # ensure the summary always runs (demo safety)
            log.exception("send_recipient %s failed: %s", rid, exc)
            res = {"status": "error"}
        if res.get("status") == "sent":
            sent += 1
            # A registered buyer has an agent → arm the presence-gated auto-responder
            # (Graph 2, P3): the opener is an inbound to their side. Prospects have no
            # agent, so they're skipped. Best-effort; duplicate emits are idempotent.
            if res.get("recipient_user_id") and res.get("opener_message_id"):
                try:
                    await inngest_client.send(
                        inngest.Event(
                            name="thread/inbound",
                            data={
                                "conversation_id": res["conversation_id"],
                                "inbound_message_id": res["opener_message_id"],
                            },
                        )
                    )
                except Exception as exc:  # noqa: BLE001 - never break the fan-out on this
                    log.warning("thread/inbound emit failed for recipient %s: %s", rid, exc)
        elif res.get("status") in ("skipped", "already_sent"):
            skipped += 1

        text = f"Sent {sent}/{total}" + (f", {skipped} skipped" if skipped else "")
        await _tick(
            seller_id,
            {
                "type": "outreach.progress",
                "data": {
                    "campaign_id": campaign_id,
                    "conversation_id": conv_id,
                    "sent": sent,
                    "skipped": skipped,
                    "total": total,
                    "text": text,
                    "done": i == total,
                },
            },
        )

    outcome = await sync_to_async(service.finish_campaign)(campaign_id)

    # Exactly one model turn: phrase the outcome. Templated fallback keeps the demo green.
    body = await _summary_text(info, outcome)
    message_id = None
    if conv_id is not None:
        message_id = await dal.save_message(
            conv_id, author_type="agent", body=body, author_id=seller_id
        )
    await _tick(
        seller_id,
        {
            "type": "outreach.summary",
            "data": {
                "campaign_id": campaign_id,
                "conversation_id": conv_id,
                "message_id": message_id,
                "body": body,
                "outcome": outcome,
            },
        },
    )
    return {"campaign_id": campaign_id, "outcome": outcome}


async def _summary_text(info: dict, outcome: dict) -> str:
    templated = (
        f"Outreach on {info['listing_address']}: opened {outcome['sent']} thread(s)"
        + (f", {outcome['skipped']} already in contact" if outcome["skipped"] else "")
        + (f", {outcome['failed']} failed" if outcome["failed"] else "")
        + "."
    )
    try:
        from polaris_agent.models import get_model

        resp = await get_model("workhorse").ainvoke(
            "You are Polaris, a real-estate copilot. In 1-2 warm, concrete sentences, "
            "summarize this outreach result for the seller. Use the exact numbers; invent "
            "nothing.\n\n"
            f"Listing: {info['listing_address']}\n"
            f"Threads opened (reached): {outcome['sent']}\n"
            f"Skipped (already in contact): {outcome['skipped']}\n"
            f"Failed: {outcome['failed']}\n"
        )
        return (resp.content or "").strip() or templated
    except Exception as exc:  # pragma: no cover - provider may be down in dev
        log.warning("summary narration failed, using template: %s", exc)
        return templated


functions = [outreach_fanout]
