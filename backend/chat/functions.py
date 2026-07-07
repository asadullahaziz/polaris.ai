"""
Away-responder Inngest handler (architecture §5/§9a; revisions 2026-07-03/-04).

`thread_inbound` fires on **`chat/inbound`** `{chat_id, inbound_message_id}` (emitted by
`ChatConsumer._handle_send` + `chat.views._broadcast_and_arm` whenever a message lands in
a chat). It:

  1. **Debounces** with the presence grace: `step.wait_for_event("chat/focused", …)`
     matched to this chat, timeout = the grace window. If the covered human shows up
     (focus/typing) inside the window → stand down (architecture §9a).
  2. Loads the principal-centric plan and, if a reply is warranted, runs the away
     assistant's Graph 2 turn (presence + cap re-checked; the DB commit gate re-checks
     both atomically).
  3. **Bounded agent↔agent loop (revisions 2026-07-04):** after a reply is actually
     *sent*, re-emits `chat/inbound` with the NEW agent message as the inbound — arming
     the *counterparty's* presence-gated away-agent. If both humans are away the two
     assistants converse, bounded by the per-user reply cap (`UserProfile.agent_reply_cap`,
     default 3): each principal's agent sends at most N since that principal last spoke,
     then the next inbound-while-away **escalates** instead. Termination is guaranteed
     (worst case ≈ 2N agent turns, then both sides escalate).

This handler is deliberately **thin durable glue**. The "exactly one reply per turn"
guarantee is the DB commit gate (`chat.responder_service`), not this code — so an
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
from . import services

log = logging.getLogger(__name__)


def _grace() -> dt.timedelta:
    return dt.timedelta(seconds=int(getattr(settings, "RESPONDER_GRACE_SECONDS", 45)))


def _serialize(message_id: int) -> dict:
    """Load + serialize a persisted message on the sync ORM path (attachments included)."""
    from .models import Message

    return services.serialize_message(Message.objects.get(id=message_id))


async def _broadcast(chat_id: int, data: dict) -> None:
    """Push to the chat's WS group so both parties see the agent's message live. The
    ChatConsumer.chat_message handler forwards it to clients as `message.new`."""
    try:
        layer = get_channel_layer()
        await layer.group_send(f"chat_{chat_id}", {"type": "chat.message", "data": data})
    except Exception as exc:  # noqa: BLE001 - WS is best-effort; the message is persisted
        log.warning("chat broadcast failed: %s", exc)


async def _arm_counterparty(chat_id: int, agent_message_id: int) -> None:
    """The single wire that enables the bounded agent↔agent loop: re-emit `chat/inbound`
    with the just-sent agent message as the inbound, arming the counterparty's away-agent
    (fires only if THEY are away + under their own cap; else that turn escalates)."""
    try:
        await inngest_client.send(
            inngest.Event(
                name="chat/inbound",
                data={"chat_id": chat_id, "inbound_message_id": agent_message_id},
            )
        )
    except Exception as exc:  # noqa: BLE001 - re-arm is best-effort; the reply is persisted
        log.warning("bounded-loop re-arm emit failed: %s", exc)


@inngest_client.create_function(
    fn_id="chat-inbound",
    trigger=inngest.TriggerEvent(event="chat/inbound"),
    concurrency=[inngest.Concurrency(limit=10)],
    retries=2,
)
async def thread_inbound(ctx: inngest.Context) -> dict:
    chat_id = int(ctx.event.data["chat_id"])
    inbound_id = int(ctx.event.data["inbound_message_id"])

    # (1) Presence grace / debounce. Any chat.focus / typing on THIS chat → stand down.
    # Inngest rejects sub-second waits, so a grace of 0 (a valid demo setting) means
    # "no debounce" — skip the wait; the presence re-check below still guards.
    grace = _grace()
    if grace >= dt.timedelta(seconds=1):
        focused = await ctx.step.wait_for_event(
            "await-focus",
            event="chat/focused",
            if_exp=f"async.data.chat_id == {chat_id}",
            timeout=grace,
        )
        if focused is not None:
            return {"stood_down": "human present within grace"}

    plan = await dal.responder_plan(chat_id, inbound_id)
    if "skip" in plan:
        return {"skipped": plan["skip"]}

    principal_id = plan["principal_id"]

    # Early presence re-check saves the LLM turn; the commit gate re-checks atomically.
    from chat.presence import is_present

    if await is_present(chat_id, principal_id):
        return {"stood_down": "principal present"}

    # Already at the principal's reply cap since they last spoke, and the counterparty is
    # pressing again while the principal is away → hand to the human, don't farm another
    # reply (architecture §5; caps the bounded loop at the principal's own N).
    if await sync_to_async(svc.reply_cap_reached)(chat_id, principal_id):
        await sync_to_async(svc.escalate)(
            chat_id, principal_id, "counterparty messaged again; agent already at its reply cap"
        )
        return {"outcome": "escalated", "reason": "reply cap"}

    from polaris_agent.graphs.responder import run_responder

    final = await run_responder(plan)
    outcome = final.get("outcome")
    result = final.get("commit_result") or {}

    if outcome == "sent" and result.get("message_id"):
        data = await sync_to_async(_serialize)(result["message_id"])
        await _broadcast(chat_id, data)
        # Bounded agent↔agent loop: arm the counterparty's away-agent with this reply.
        await _arm_counterparty(chat_id, result["message_id"])

    return {"outcome": outcome, "chat_id": chat_id}


functions = [thread_inbound]
