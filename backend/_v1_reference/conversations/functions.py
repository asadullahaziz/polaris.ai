"""
Auto-responder Inngest handler (implementation_plan P3.1, architecture §5/§9a).

`thread_inbound` fires on the `thread/inbound` event (emitted whenever a cross-side
message lands in a shared thread — an outreach opener, or a human reply). It:

  1. **Debounces** with the presence grace: `step.wait_for_event("thread/focused", …)`
     matched to this conversation, timeout = the grace window. If the human shows up
     (focus/typing) inside the window → stand down (architecture §9a).
  2. Loads the responder plan (which SIDE answers, its mandate, transcript) and, if a
     reply is warranted, runs the counterparty's Graph 2 turn.

This handler is deliberately **thin durable glue**. The "exactly one reply" guarantee
is the DB commit gate (`conversations.responder_service`), not this code — so an
at-least-once retry that re-runs the whole turn recomputes the same `dedup_key` and the
insert is a no-op (architecture §9b). No `step.run` around the turn: re-running it is by
design, and it's idempotent.
"""

from __future__ import annotations

import datetime as dt
import logging

import inngest
from asgiref.sync import sync_to_async
from channels.layers import get_channel_layer
from django.conf import settings

from orchestration.client import inngest_client
from polaris_agent import dal

from . import responder_service as svc

log = logging.getLogger(__name__)


def _grace() -> dt.timedelta:
    return dt.timedelta(seconds=int(getattr(settings, "RESPONDER_GRACE_SECONDS", 45)))


async def _broadcast(conversation_id: int, payload: dict) -> None:
    """Push to the thread's WS group so both parties see the agent's message live."""
    try:
        layer = get_channel_layer()
        await layer.group_send(f"thread_{conversation_id}", payload)
    except Exception as exc:  # noqa: BLE001 - WS is best-effort; the message is persisted
        log.warning("thread broadcast failed: %s", exc)


@inngest_client.create_function(
    fn_id="thread-inbound",
    trigger=inngest.TriggerEvent(event="thread/inbound"),
    concurrency=[inngest.Concurrency(limit=10)],
    retries=2,
)
async def thread_inbound(ctx: inngest.Context) -> dict:
    conv_id = int(ctx.event.data["conversation_id"])
    inbound_id = int(ctx.event.data["inbound_message_id"])

    # (1) Presence grace / debounce. Any thread.focus / typing on THIS thread → stand down.
    focused = await ctx.step.wait_for_event(
        "await-focus",
        event="thread/focused",
        if_exp=f"async.data.conversation_id == {conv_id}",
        timeout=_grace(),
    )
    if focused is not None:
        return {"stood_down": "human present within grace"}

    plan = await dal.responder_plan(conv_id, inbound_id)
    if "skip" in plan:
        return {"skipped": plan["skip"]}

    # Early presence re-check saves the LLM turn; the commit gate re-checks atomically.
    from conversations.presence import is_present

    if await is_present(conv_id, plan["principal_id"]):
        return {"stood_down": "principal present"}

    # Already covered once since the human last spoke, and the counterparty is pressing
    # again while the human is still away → hand to the human, don't farm a 2nd reply
    # (architecture §5; verification: "2nd counterparty message while absent → escalate").
    if await sync_to_async(svc.reply_cap_reached)(conv_id, plan["side"]):
        await sync_to_async(svc.escalate)(
            conv_id, plan["principal_id"], "counterparty messaged again; agent already replied once"
        )
        return {"outcome": "escalated", "reason": "reply cap"}

    from polaris_agent.graphs.responder import run_responder

    final = await run_responder(plan)
    outcome = final.get("outcome")
    result = final.get("commit_result") or {}

    if outcome == "sent" and result.get("message_id"):
        await _broadcast(
            conv_id,
            {
                "type": "thread.message",
                "data": {
                    "id": result["message_id"],
                    "conversation_id": conv_id,
                    "author_type": "agent",
                    "author_side": plan["side"],
                    "action": result.get("action"),
                    "body": result.get("body"),
                },
            },
        )
    return {"outcome": outcome, "conversation_id": conv_id}


functions = [thread_inbound]
