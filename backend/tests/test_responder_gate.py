"""
P3 — the sender-based commit-gate invariant (the "exactly one autonomous reply"
guarantee), LLM-free. Exercises `chat/responder_service.py` directly: the LLM turn
(Graph 2) is P4; this is the DB guarantee that stands on its own.

Covered: one reply then cap; presence stands down; the cap resets ONLY on the
principal's own human message (not the counterparty's); escalate posts nothing to the
counterparty; decline is terminal; a draft is owner-only then approve sends exactly once;
the dedup ON-CONFLICT layer in isolation.
"""

from __future__ import annotations

import pytest
from django.contrib.auth import get_user_model

from chat import responder_service as svc
from chat import services
from chat.models import Message
from notifications.models import Notification

User = get_user_model()

ABSENT = lambda chat_id, user_id: False  # noqa: E731 - test presence stub
PRESENT = lambda chat_id, user_id: True  # noqa: E731


@pytest.fixture
def pair(db):
    """principal P (the human the away-agent covers) + counterparty C, sharing one chat."""
    p = User.objects.create_user(email="principal@x.com", password="pw-12345678", full_name="P")
    c = User.objects.create_user(email="counter@x.com", password="pw-12345678", full_name="C")
    chat, _ = services.get_or_create_chat(p.id, c.id)
    return p, c, chat


def _inbound(chat_id, sender_id, body="hi"):
    return services.post_human_message(chat_id, sender_id, body)


def _commit(chat, principal, counterparty, inbound_id, *, action="inform", **kw):
    return svc.commit_reply(
        chat.id,
        principal_id=principal.id,
        action=action,
        body=kw.pop("body", "on it, thanks"),
        disclosed_fields=kw.pop("disclosed_fields", {}),
        inbound_message_id=inbound_id,
        counterparty_user_id=counterparty.id,
        presence_fn=kw.pop("presence_fn", ABSENT),
        **kw,
    )


@pytest.mark.django_db
def test_commit_reply_sends_one_then_caps(pair):
    p, c, chat = pair
    inbound = _inbound(chat.id, c.id)

    r1 = _commit(chat, p, c, inbound["id"])
    assert r1["status"] == "sent"

    # The principal has now covered once since the human last spoke → cap reached.
    assert svc.reply_cap_reached(chat.id, p.id) is True

    # A second inbound from the counterparty while the human is still away must NOT farm
    # a second reply — the gate stands down on the cap.
    inbound2 = _inbound(chat.id, c.id, "you there?")
    r2 = _commit(chat, p, c, inbound2["id"])
    assert r2["status"] == "stood_down_cap"

    assert Message.objects.filter(chat_id=chat.id, kind="agent", status="sent").count() == 1


@pytest.mark.django_db
def test_presence_stands_down(pair):
    p, c, chat = pair
    inbound = _inbound(chat.id, c.id)
    r = _commit(chat, p, c, inbound["id"], presence_fn=PRESENT)
    assert r["status"] == "stood_down_present"
    assert not Message.objects.filter(chat_id=chat.id, kind="agent").exists()


@pytest.mark.django_db
def test_cap_resets_only_on_same_sender_human(pair):
    p, c, chat = pair
    inbound = _inbound(chat.id, c.id)
    assert _commit(chat, p, c, inbound["id"])["status"] == "sent"
    assert svc.reply_cap_reached(chat.id, p.id) is True

    # The COUNTERPARTY messaging again does NOT reset the principal's cap.
    _inbound(chat.id, c.id, "hello?")
    assert svc.reply_cap_reached(chat.id, p.id) is True

    # The PRINCIPAL's own human message (the takeover) zeroes their count.
    _inbound(chat.id, p.id, "I've got it from here")
    assert svc.reply_cap_reached(chat.id, p.id) is False


@pytest.mark.django_db
def test_escalate_sets_status_and_notifies_without_posting(pair):
    p, c, chat = pair
    n_before = Message.objects.filter(chat_id=chat.id).count()
    res = svc.escalate(chat.id, p.id, "counterparty pressed again; agent already replied once")
    assert res["status"] == "escalated"

    chat.refresh_from_db()
    assert chat.status == "escalated"
    assert chat.terminal == "needs_decision"
    # No cross-boundary message is posted on escalation.
    assert Message.objects.filter(chat_id=chat.id).count() == n_before
    # The principal is notified; the counterparty is not.
    assert Notification.objects.filter(user=p, type="escalation", chat_id=chat.id).exists()
    assert not Notification.objects.filter(user=c, chat_id=chat.id).exists()


@pytest.mark.django_db
def test_decline_is_terminal(pair):
    p, c, chat = pair
    inbound = _inbound(chat.id, c.id, "will you take 100k?")
    r = _commit(chat, p, c, inbound["id"], action="decline", terminal="no_fit", body="not a fit")
    assert r["status"] == "sent"
    chat.refresh_from_db()
    assert chat.terminal == "no_fit"


@pytest.mark.django_db
def test_qualify_notifies_both_parties(pair):
    p, c, chat = pair
    inbound = _inbound(chat.id, c.id, "cash, close in 10 days")
    _commit(chat, p, c, inbound["id"], action="qualify", body="that works")
    # Counterparty gets an inbound_message notification; the principal gets the qualify flag.
    assert Notification.objects.filter(user=c, type="inbound_message", chat_id=chat.id).exists()
    assert Notification.objects.filter(user=p, type="inbound_message", chat_id=chat.id).exists()


@pytest.mark.django_db
def test_draft_is_owner_only_then_approve_sends_once(pair):
    p, c, chat = pair
    inbound = _inbound(chat.id, c.id)
    d = svc.persist_draft(
        chat.id,
        principal_id=p.id,
        action="qualify",
        body="draft reply",
        disclosed_fields={},
        inbound_message_id=inbound["id"],
    )
    assert d["status"] == "draft"
    msg_id = d["message_id"]

    # The draft is visible ONLY to its owner (the principal), never the counterparty.
    owner_view = services.list_messages(chat.id, p.id)
    counter_view = services.list_messages(chat.id, c.id)
    assert any(m["id"] == msg_id and m["status"] == "draft" for m in owner_view)
    assert all(m["id"] != msg_id for m in counter_view)
    assert Notification.objects.filter(user=p, type="approval_required", chat_id=chat.id).exists()

    # Approve → sent (the takeover). Idempotent second approve returns sent, no dup.
    a1 = svc.approve_draft(p.id, msg_id)
    assert a1["status"] == "sent"
    a2 = svc.approve_draft(p.id, msg_id)
    assert a2["status"] == "sent"
    sent = Message.objects.filter(chat_id=chat.id, kind="agent", status="sent")
    assert sent.count() == 1 and sent.first().id == msg_id


@pytest.mark.django_db
def test_dedup_on_conflict_is_a_no_op(pair, monkeypatch):
    """Isolate the dedup layer from the cap layer: with the cap forced open, a replayed
    commit for the SAME inbound recomputes the same dedup_key → silent no-op."""
    p, c, chat = pair
    monkeypatch.setattr(svc, "reply_cap_reached", lambda *a, **k: False)
    inbound = _inbound(chat.id, c.id)

    r1 = _commit(chat, p, c, inbound["id"])
    r2 = _commit(chat, p, c, inbound["id"])  # replay — same dedup_key
    assert r1["status"] == "sent"
    assert r2["status"] == "duplicate"
    assert Message.objects.filter(chat_id=chat.id, kind="agent").count() == 1
