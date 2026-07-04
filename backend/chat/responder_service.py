"""
Auto-responder invariant core (architecture §4.5/§5) — the "exactly one autonomous
reply" guarantee, ported from v1 with the field swap (author-triple → `kind`+`sender`).

Deliberately **pure and synchronous** so the invariant is unit-testable without Inngest,
LangGraph, or a live socket. The LLM turn (Graph 2 · P4) runs *before and outside* the
commit gate; only this critical section is locked.

The invariant is a DATABASE guarantee, not agent logic. Three layers, only the last is
the guarantee (architecture §5):
  1. Debounce (Inngest grace) — avoids wasted work; lives in the P4 handler.
  2. Re-check — presence + cap re-read before the turn; avoids wasted tokens.
  3. **Commit gate (here)** — one txn, `pg_advisory_xact_lock(chat_id)`, that re-checks
     presence-absent, re-checks the reply cap, and inserts the message with `dedup_key`
     under `ON CONFLICT DO NOTHING`. A retried Inngest step recomputes the same
     `dedup_key` → the insert is a silent no-op → never a second message.

**Sender-based cap (2-party ⇒ unambiguous):** "agent messages with `sender=principal`
since the last human message with `sender=principal` < N=1." The **principal is the
OTHER member** (the human the agent covers for). Human takeover needs no special code:
the principal's own next human message zeroes the count (its `sender` matches); the
counterparty's messages never reset it (different `sender`) — the other side can't farm
extra replies. `author_side` is gone entirely.
"""

from __future__ import annotations

import logging

from django.db import IntegrityError, connection, transaction
from django.utils import timezone

log = logging.getLogger(__name__)

REPLY_CAP = 1  # N in "principal's agent replies since last principal human < N" (§5)


def dedup_key(chat_id: int, inbound_message_id: int) -> str:
    """The idempotency key for one autonomous reply to one inbound (architecture §5)."""
    return f"autoreply:{chat_id}:{inbound_message_id}"


def _default_present(chat_id: int, user_id: int | None) -> bool:
    """Redis-backed presence check (lazy import so the service stays testable offline)."""
    if user_id is None:
        return False
    from chat.presence import is_present_sync

    return is_present_sync(chat_id, user_id)


def reply_cap_reached(chat_id: int, principal_id: int, *, n: int = REPLY_CAP) -> bool:
    """Has the principal's agent already replied `n` times since the principal's last
    human message? The cap is a QUERY, not a stored flag — recomputed fresh (and inside
    the commit txn) so it's correct under retries and concurrency. The counterparty's
    messages don't reset it."""
    from .models import Message

    last_human_id = (
        Message.objects.filter(
            chat_id=chat_id, kind="human", sender_id=principal_id, status="sent"
        )
        .order_by("-id")
        .values_list("id", flat=True)
        .first()
    )
    agent_q = Message.objects.filter(
        chat_id=chat_id, kind="agent", sender_id=principal_id, status="sent"
    )
    if last_human_id is not None:
        agent_q = agent_q.filter(id__gt=last_human_id)
    return agent_q.count() >= n


def log_action(
    principal_id: int,
    chat_id: int | None,
    action_type: str,
    summary: str,
    *,
    private_rationale: str | None = None,
    payload: dict | None = None,
) -> None:
    """Append-only audit (the away-agent's private reasoning trail). **Deferred to P4**:
    the `ai.AgentActionLog` model + its `chat` FK land with the responder graph (deferral
    ledger). Lazy import ⇒ a clean no-op until then, so the commit gate is complete and
    testable now without a dangling table."""
    try:
        from ai.models import AgentActionLog  # present from P4
    except ImportError:
        return
    body = dict(payload or {})
    if private_rationale is not None:
        body["private_rationale"] = private_rationale
    AgentActionLog.objects.create(
        principal_id=principal_id,
        chat_id=chat_id,
        action_type=action_type,
        summary=summary,
        payload=body,
    )


@transaction.atomic
def commit_reply(
    chat_id: int,
    *,
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
    from .models import Chat, Message

    presence_fn = presence_fn or _default_present
    key = dedup_key(chat_id, inbound_message_id)

    # Serialize every commit for this chat: the LLM work already ran outside.
    with transaction.atomic():
        with connection.cursor() as cur:
            cur.execute("SELECT pg_advisory_xact_lock(%s)", [chat_id])

        # (1) Presence re-checked atomically — a human who returned mid-turn wins.
        if presence_fn(chat_id, principal_id):
            return {"status": "stood_down_present"}

        # (2) Reply cap re-checked inside the lock (closes the TOCTOU race).
        if reply_cap_reached(chat_id, principal_id):
            return {"status": "stood_down_cap"}

        # (3) Idempotent insert. A replayed Inngest step recomputes the same dedup_key.
        try:
            with transaction.atomic():
                msg = Message.objects.create(
                    chat_id=chat_id,
                    kind="agent",
                    sender_id=principal_id,
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
        Chat.objects.filter(id=chat_id).update(**updates)

    # System-of-record side effects (outside the lock is fine — they're additive).
    from notifications.models import Notification

    if counterparty_user_id is not None:
        Notification.objects.create(
            user_id=counterparty_user_id,
            type="inbound_message",
            chat_id=chat_id,
            payload={"message_id": msg.id, "action": action, "by": "agent"},
        )
    # On qualify, flag the principal: their agent found a live deal — take it forward.
    if action == "qualify":
        Notification.objects.create(
            user_id=principal_id,
            type="inbound_message",
            chat_id=chat_id,
            payload={"message_id": msg.id, "note": "your agent qualified this deal"},
        )
    log_action(
        principal_id,
        chat_id,
        "sent",
        f"auto-{action} on chat {chat_id}",
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
        "chat_id": chat_id,
        "action": action,
        "body": body,
        "disclosed_fields": disclosed_fields or {},
    }


@transaction.atomic
def persist_draft(
    chat_id: int,
    *,
    principal_id: int,
    action: str,
    body: str,
    disclosed_fields: dict,
    inbound_message_id: int,
    reply_to_id: int | None = None,
    private_rationale: str | None = None,
) -> dict:
    """Send-gate for `draft_for_approval` autonomy (architecture §5): the auto-responder
    only fires when the human is absent, so an approval-required level can't be satisfied
    in-flight. Persist a `draft` message + an approval notification and END — that draft
    IS the awaiting-approval object; the human approving/sending it later is the takeover.
    Nothing is parked."""
    from notifications.models import Notification

    from .models import Message

    key = dedup_key(chat_id, inbound_message_id)
    try:
        with transaction.atomic():
            msg = Message.objects.create(
                chat_id=chat_id,
                kind="agent",
                sender_id=principal_id,
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
        chat_id=chat_id,
        payload={"message_id": msg.id, "action": action},
    )
    log_action(
        principal_id,
        chat_id,
        "drafted",
        f"drafted {action} for approval on chat {chat_id}",
        private_rationale=private_rationale,
        payload={"message_id": msg.id, "action": action},
    )
    return {"status": "draft", "message_id": msg.id, "chat_id": chat_id, "body": body}


@transaction.atomic
def escalate(
    chat_id: int,
    principal_id: int,
    reason: str,
    *,
    terminal: str | None = "needs_decision",
) -> dict:
    """Hand to the human WITHOUT posting anything to the counterparty (architecture §5):
    set the chat status + a notification. No cross-boundary message on escalation."""
    from notifications.models import Notification

    from .models import Chat

    updates = {"status": "escalated", "updated_at": timezone.now()}
    if terminal is not None:
        updates["terminal"] = terminal
    Chat.objects.filter(id=chat_id).update(**updates)
    Notification.objects.create(
        user_id=principal_id,
        type="escalation",
        chat_id=chat_id,
        payload={"reason": reason},
    )
    log_action(
        principal_id,
        chat_id,
        "escalated",
        f"escalated chat {chat_id}",
        private_rationale=reason,
    )
    return {"status": "escalated", "reason": reason}


@transaction.atomic
def approve_draft(user_id: int, message_id: int) -> dict:
    """The human approves a `draft_for_approval` draft → flip it to sent (the takeover
    path). Idempotent: an already-sent draft returns sent. Ownership-checked by `sender`."""
    from .models import Chat, Message

    msg = (
        Message.objects.select_for_update()
        .filter(id=message_id, kind="agent", sender_id=user_id)
        .first()
    )
    if msg is None:
        return {"error": "draft not found"}
    if msg.status == "sent":
        return {"status": "sent", "message_id": msg.id, "chat_id": msg.chat_id}
    msg.status = "sent"
    msg.sent_at = timezone.now()
    msg.save(update_fields=["status", "sent_at"])
    Chat.objects.filter(id=msg.chat_id).update(updated_at=timezone.now())
    return {
        "status": "sent",
        "message_id": msg.id,
        "chat_id": msg.chat_id,
        "body": msg.body,
        "action": msg.action,
    }
