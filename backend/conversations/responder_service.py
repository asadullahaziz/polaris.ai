"""
Auto-responder invariant core (implementation_plan P3.3/P3.5/P3.6, architecture §4.5/§5).

Deliberately **pure and synchronous** — exactly like `outreach/service.py` — so the
"exactly one autonomous reply" invariant is unit-testable without Inngest, LangGraph,
or a live socket. The LLM turn (Graph 2) runs *before and outside* the commit gate;
only this critical section is locked.

The invariant is a DATABASE guarantee, not agent logic. Three layers, only the last
is the guarantee (architecture §5):
  1. Debounce (Inngest 45s grace) — avoids wasted work; lives in the handler.
  2. Re-check — presence + cap re-read before the turn; avoids wasted tokens.
  3. **Commit gate (here)** — one txn, `pg_advisory_xact_lock(conversation_id)`, that
     re-checks presence absent, re-checks the reply cap, and inserts the message with
     `dedup_key` under `ON CONFLICT DO NOTHING`. A retried Inngest step recomputes the
     same `dedup_key` → the insert is a silent no-op → never a second message.

Human takeover needs no special code: the cap window resets at the last **same-side
human** message, so the principal's own next message zeroes the count (features §F #32).
A counterparty message never resets it — the other side can't farm extra replies.
"""

from __future__ import annotations

import logging

from django.db import IntegrityError, connection, transaction
from django.utils import timezone

log = logging.getLogger(__name__)

REPLY_CAP = 1  # N in "own-side agent replies since last same-side human < N" (architecture §5)


def dedup_key(conversation_id: int, inbound_message_id: int) -> str:
    """The idempotency key for one autonomous reply to one inbound (architecture §5)."""
    return f"autoreply:{conversation_id}:{inbound_message_id}"


def _default_present(conversation_id: int, user_id: int | None) -> bool:
    """Redis-backed presence check (lazy import so the service stays testable offline)."""
    if user_id is None:
        return False
    from conversations.presence import is_present_sync

    return is_present_sync(conversation_id, user_id)


def reply_cap_reached(conversation_id: int, side: str, *, n: int = REPLY_CAP) -> bool:
    """Has this side's agent already replied `n` times since the last same-side human?

    The cap is a QUERY, not a stored flag — recomputed fresh (and inside the commit txn)
    so it's correct under retries and concurrency. Counterparty messages don't reset it.
    """
    from .models import Message

    last_human_id = (
        Message.objects.filter(
            conversation_id=conversation_id,
            author_type="human",
            author_side=side,
            status="sent",
        )
        .order_by("-id")
        .values_list("id", flat=True)
        .first()
    )
    agent_q = Message.objects.filter(
        conversation_id=conversation_id,
        author_type="agent",
        author_side=side,
        status="sent",
    )
    if last_human_id is not None:
        agent_q = agent_q.filter(id__gt=last_human_id)
    return agent_q.count() >= n


def log_action(
    principal_id: int,
    conversation_id: int | None,
    action_type: str,
    summary: str,
    *,
    private_rationale: str | None = None,
    payload: dict | None = None,
) -> None:
    """Append-only audit (agent_action_log). `private_rationale` is stored, NEVER posted."""
    from agent_context.models import AgentActionLog

    body = dict(payload or {})
    if private_rationale is not None:
        body["private_rationale"] = private_rationale
    AgentActionLog.objects.create(
        principal_id=principal_id,
        conversation_id=conversation_id,
        action_type=action_type,
        summary=summary,
        payload=body,
    )


@transaction.atomic
def commit_reply(
    conversation_id: int,
    *,
    side: str,
    principal_id: int,
    action: str,
    body: str,
    disclosed_fields: dict,
    inbound_message_id: int,
    counterparty_user_id: int | None = None,
    reply_to_id: int | None = None,
    terminal: str | None = None,
    private_rationale: str | None = None,
    presence_fn=None,
) -> dict:
    """The commit gate — the guarantee. Returns {status, ...}. Statuses:
    sent | stood_down_present | stood_down_cap | duplicate."""
    from .models import Conversation, Message

    presence_fn = presence_fn or _default_present
    key = dedup_key(conversation_id, inbound_message_id)

    # Serialize every commit for this conversation: the LLM work already ran outside.
    with transaction.atomic():
        with connection.cursor() as cur:
            cur.execute("SELECT pg_advisory_xact_lock(%s)", [conversation_id])

        # (1) Presence re-checked atomically — a human who returned mid-turn wins.
        if presence_fn(conversation_id, principal_id):
            return {"status": "stood_down_present"}

        # (2) Reply cap re-checked inside the lock (closes the TOCTOU race).
        if reply_cap_reached(conversation_id, side):
            return {"status": "stood_down_cap"}

        # (3) Idempotent insert. A replayed Inngest step recomputes the same dedup_key.
        try:
            with transaction.atomic():
                msg = Message.objects.create(
                    conversation_id=conversation_id,
                    author_type="agent",
                    author_side=side,
                    author_id=principal_id,
                    action=action,
                    body=body,
                    disclosed_fields=disclosed_fields or {},
                    status="sent",
                    sent_at=timezone.now(),
                    reply_to_id=reply_to_id,
                    dedup_key=key,
                )
        except IntegrityError:
            return {"status": "duplicate"}

        updates = {"updated_at": timezone.now()}
        if terminal is not None:
            updates["terminal"] = terminal
        Conversation.objects.filter(id=conversation_id).update(**updates)

    # System-of-record side effects (outside the lock is fine — they're additive).
    from notifications.models import Notification

    if counterparty_user_id is not None:
        Notification.objects.create(
            user_id=counterparty_user_id,
            type="inbound_message",
            conversation_id=conversation_id,
            payload={"message_id": msg.id, "action": action, "by": "agent"},
        )
    # On qualify, flag the principal: their agent found a live deal — take it forward.
    if action == "qualify":
        Notification.objects.create(
            user_id=principal_id,
            type="inbound_message",
            conversation_id=conversation_id,
            payload={"message_id": msg.id, "note": "your agent qualified this deal"},
        )
    log_action(
        principal_id,
        conversation_id,
        "sent",
        f"auto-{action} on conversation {conversation_id}",
        private_rationale=private_rationale,
        payload={
            "message_id": msg.id,
            "action": action,
            "disclosed_fields": disclosed_fields or {},
        },
    )
    return {
        "status": "sent",
        "message_id": msg.id,
        "conversation_id": conversation_id,
        "action": action,
        "body": body,
        "disclosed_fields": disclosed_fields or {},
    }


@transaction.atomic
def persist_draft(
    conversation_id: int,
    *,
    side: str,
    principal_id: int,
    action: str,
    body: str,
    disclosed_fields: dict,
    inbound_message_id: int,
    reply_to_id: int | None = None,
    private_rationale: str | None = None,
) -> dict:
    """Send-gate for `assist`/`confirm_batch` autonomy (architecture §5): the auto-responder
    only fires when the human is absent, so an approval-required level can't be satisfied
    in-flight. Persist a `draft` message + an approval notification and END — that draft IS
    the awaiting-approval object; the human approving/sending it later is the takeover.
    Nothing is parked."""
    from notifications.models import Notification

    from .models import Message

    key = dedup_key(conversation_id, inbound_message_id)
    try:
        with transaction.atomic():
            msg = Message.objects.create(
                conversation_id=conversation_id,
                author_type="agent",
                author_side=side,
                author_id=principal_id,
                action=action,
                body=body,
                disclosed_fields=disclosed_fields or {},
                status="draft",  # NOT sent — awaits human approval
                reply_to_id=reply_to_id,
                dedup_key=key,
            )
    except IntegrityError:
        return {"status": "duplicate"}

    Notification.objects.create(
        user_id=principal_id,
        type="approval_required",
        conversation_id=conversation_id,
        payload={"message_id": msg.id, "action": action},
    )
    log_action(
        principal_id,
        conversation_id,
        "drafted",
        f"drafted {action} for approval on conversation {conversation_id}",
        private_rationale=private_rationale,
        payload={"message_id": msg.id, "action": action},
    )
    return {
        "status": "draft",
        "message_id": msg.id,
        "conversation_id": conversation_id,
        "body": body,
    }


@transaction.atomic
def escalate(
    conversation_id: int,
    principal_id: int,
    reason: str,
    *,
    terminal: str | None = "needs_decision",
) -> dict:
    """Hand to the human WITHOUT posting anything to the counterparty (architecture §5):
    set conversation status + a notification. No cross-boundary message on escalation."""
    from notifications.models import Notification

    from .models import Conversation

    updates = {"status": "escalated", "updated_at": timezone.now()}
    if terminal is not None:
        updates["terminal"] = terminal
    Conversation.objects.filter(id=conversation_id).update(**updates)
    Notification.objects.create(
        user_id=principal_id,
        type="escalation",
        conversation_id=conversation_id,
        payload={"reason": reason},
    )
    log_action(
        principal_id,
        conversation_id,
        "escalated",
        f"escalated conversation {conversation_id}",
        private_rationale=reason,
    )
    return {"status": "escalated", "reason": reason}


@transaction.atomic
def approve_draft(user_id: int, message_id: int) -> dict:
    """The human approves an `assist`/`confirm` draft → flip it to sent (the takeover path).
    Idempotent: an already-sent draft returns sent. Ownership-checked by author."""
    from .models import Conversation, Message

    msg = (
        Message.objects.select_for_update()
        .filter(id=message_id, author_type="agent", author_id=user_id)
        .first()
    )
    if msg is None:
        return {"error": "draft not found"}
    if msg.status == "sent":
        return {"status": "sent", "message_id": msg.id, "conversation_id": msg.conversation_id}
    msg.status = "sent"
    msg.sent_at = timezone.now()
    msg.save(update_fields=["status", "sent_at"])
    Conversation.objects.filter(id=msg.conversation_id).update(updated_at=timezone.now())
    return {
        "status": "sent",
        "message_id": msg.id,
        "conversation_id": msg.conversation_id,
        "body": msg.body,
        "action": msg.action,
        "author_side": msg.author_side,
    }
