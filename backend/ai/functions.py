"""
Outreach fan-out Inngest function.

Triggered by `outreach/approved` (emitted by the approve REST action + the copilot
`launch_outreach_campaign` tool). This is the durable post-approval half of outreach —
the seller may close the tab; it outlives the copilot turn. It is deliberately thin
glue: every guarantee (per-pair ledger, opener idempotency, one-chat-per-pair, one
message per buyer) lives in `ai.outreach_service.send_to_buyer`, which is pure/sync and
unit-tested without this dev server.

Three jobs:
  1. one durable step per buyer → `send_to_buyer` (retries, concurrency) — one opener
     message per buyer covering every listing that buyer matched and the ledger allows;
  2. templated progress ticks pushed to the seller's copilot chat over the WS channel
     layer (`copilot_{seller}` group) — no model call per tick;
  3. exactly one final NL summary — one model narration (templated fallback), persisted as
     an assistant message in the copilot `ai.AiChat` and pushed as `outreach.summary`.

For every buyer actually reached, emit `chat/inbound {chat_id, inbound_message_id}` so
the away-responder covers if the buyer is away (the opener is an inbound to their side).
"""

from __future__ import annotations

import logging

import inngest
from asgiref.sync import sync_to_async
from channels.layers import get_channel_layer

from orchestration.client import inngest_client
from polaris_agent import dal

from . import outreach_service as service

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
    # Snapshot the dispatch info once, as a durable step. `buyer_ids` only includes
    # still-pending recipients, so recomputing it on an Inngest replay (every request
    # after a completed step re-runs this function body) would drop each buyer the
    # moment their send step flipped them to 'sent' — the loop below would never
    # replay their iteration, silently skipping the chat/inbound arm + ticks.
    info = await ctx.step.run(
        "dispatch-info", sync_to_async(service.campaign_dispatch_info), campaign_id
    )
    if info is None or info["status"] != "sending":
        return {"skipped": True, "reason": "campaign not in sending state"}

    seller_id = info["seller_id"]
    ai_chat_id = info["copilot_ai_chat_id"]
    buyer_ids = info["buyer_ids"]
    total = len(buyer_ids)
    sent = skipped = 0

    for i, uid in enumerate(buyer_ids, start=1):
        try:
            res = await ctx.step.run(
                f"send-u{uid}", sync_to_async(service.send_to_buyer), campaign_id, uid
            )
        except Exception as exc:  # ensure the summary always runs (demo safety)
            log.exception("send_to_buyer %s failed: %s", uid, exc)
            res = {"status": "error"}
        if res.get("status") == "sent":
            sent += 1
            # Every recipient is a registered user → arm their presence-gated
            # away-responder: the opener is an inbound to their side. A durable step, not
            # a raw client.send — raw sends between steps re-fire on every replay (one
            # duplicate away-agent turn per replay), and are skipped entirely if their
            # loop iteration never replays.
            if res.get("chat_id") and res.get("opener_message_id"):
                await ctx.step.send_event(
                    f"arm-u{uid}",
                    inngest.Event(
                        name="chat/inbound",
                        data={
                            "chat_id": res["chat_id"],
                            "inbound_message_id": res["opener_message_id"],
                        },
                    ),
                )
        elif res.get("status") in ("skipped", "already_sent"):
            skipped += 1

        text = f"Sent {sent}/{total}" + (f", {skipped} skipped" if skipped else "")
        await _tick(
            seller_id,
            {
                "type": "outreach.progress",
                "data": {
                    "campaign_id": campaign_id,
                    "ai_chat_id": ai_chat_id,
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
    if ai_chat_id is not None:
        message_id = await dal.save_ai_message(ai_chat_id, role="assistant", content=body)
    await _tick(
        seller_id,
        {
            "type": "outreach.summary",
            "data": {
                "campaign_id": campaign_id,
                "ai_chat_id": ai_chat_id,
                "message_id": message_id,
                "body": body,
                "outcome": outcome,
            },
        },
    )
    return {"campaign_id": campaign_id, "outcome": outcome}


async def _summary_text(info: dict, outcome: dict) -> str:
    addresses = info.get("listing_addresses") or []
    if len(addresses) == 1:
        label = addresses[0]
    else:
        shown = ", ".join(addresses[:3]) + ("…" if len(addresses) > 3 else "")
        label = f"{len(addresses)} listings ({shown})"
    templated = (
        f"Outreach on {label}: opened {outcome['sent']} chat(s)"
        + (f", {outcome['skipped']} already in contact" if outcome["skipped"] else "")
        + (f", {outcome['failed']} failed" if outcome["failed"] else "")
        + "."
    )
    try:
        from polaris_agent import observability, prompt_store
        from polaris_agent.models import get_model

        cp = await prompt_store.acompile(
            "outreach/summary",
            label=label,
            sent=str(outcome["sent"]),
            skipped=str(outcome["skipped"]),
            failed=str(outcome["failed"]),
        )
        ai_chat_id = info.get("copilot_ai_chat_id")
        with observability.trace_turn(
            "outreach-summary",
            user_id=str(info["seller_id"]) if info.get("seller_id") else None,
            session_id=f"copilot:{ai_chat_id}" if ai_chat_id is not None else None,
            tags=["outreach-summary"],
            metadata={"prompt_version": cp.version, "prompt_fallback": cp.is_fallback},
            input={"listings": label, **outcome},
        ) as trace:
            resp = await get_model("workhorse").ainvoke(
                cp.text, config=observability.callback_config()
            )
            text = (resp.content or "").strip() or templated
            trace.record(output=text)
        return text
    except Exception as exc:  # pragma: no cover - provider may be down in dev
        log.warning("summary narration failed, using template: %s", exc)
        return templated


functions = [outreach_fanout]
