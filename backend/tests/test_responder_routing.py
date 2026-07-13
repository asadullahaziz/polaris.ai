"""
Graph 2 routing + commit dispatch (LLM-free): the widened `_after_triage` assess
matrix; `_after_gate` routing `no_reply` to the silent node; the `_after_validate`
retry-once state machine (style violation → one redraft → escalate); and `_commit`
forcing `persist_draft` for `accept` REGARDLESS of autonomy (the principal signs) with
the recommendation in the approval context. Service calls are monkeypatched — no DB,
no LLM.
"""

from __future__ import annotations

from asgiref.sync import async_to_sync

from polaris_agent.graphs import responder as g


# ---- _after_triage ---------------------------------------------------------------
def test_after_triage_assesses_questions_and_offers_only_with_focal_and_stance():
    base = {"focal_listing_id": 7, "stance": "sell_side"}
    assert g._after_triage({**base, "intent": "listing_question"}) == "assess"
    assert g._after_triage({**base, "intent": "offer_negotiation"}) == "assess"
    assert g._after_triage({**base, "intent": "greeting_smalltalk"}) == "decide"
    assert g._after_triage({**base, "intent": "off_topic"}) == "decide"
    assert g._after_triage({**base, "intent": "suspicious"}) == "escalate"
    # No focal listing or no stance → nothing to assess.
    assert g._after_triage({"intent": "listing_question", "stance": "sell_side"}) == "decide"
    assert (
        g._after_triage({"intent": "listing_question", "focal_listing_id": 7, "stance": "neutral"})
        == "decide"
    )


# ---- _after_gate -----------------------------------------------------------------
def test_after_gate_routes_no_reply_and_escalate():
    assert (
        g._after_gate({"gate_error": "policy: nope", "decision": {"action": "ask"}}) == "escalate"
    )
    assert g._after_gate({"decision": {"action": "escalate"}}) == "escalate"
    assert g._after_gate({"decision": {"action": "no_reply"}}) == "no_reply"
    assert g._after_gate({"decision": {"action": "propose"}}) == "draft"


# ---- _after_validate (retry-once) --------------------------------------------------
def test_validate_retry_once_then_escalate():
    decision = {"action": "inform", "disclosed_fields": {}}
    # First violation: feedback set, no gate_error → route back to draft.
    out1 = g._validate(
        {"drafted": {"body": "Sounds good — got it."}, "decision": decision, "draft_attempts": 0}
    )
    assert out1["draft_attempts"] == 1 and "dash" in out1["draft_feedback"]
    assert g._after_validate({**out1}) == "draft"
    # Second violation: gate_error → escalate.
    out2 = g._validate(
        {"drafted": {"body": "Still — bad."}, "decision": decision, "draft_attempts": 1}
    )
    assert "gate_error" in out2
    assert g._after_validate(out2) == "escalate"
    # Clean body: feedback cleared → commit.
    out3 = g._validate(
        {
            "drafted": {"body": "Works for me. When can you walk it?"},
            "decision": decision,
            "draft_attempts": 1,
            "draft_feedback": "old",
        }
    )
    assert out3 == {"draft_feedback": None}
    assert g._after_validate(out3) == "commit"


# ---- _commit: accept always drafts --------------------------------------------------
def _run_commit(state, monkeypatch):
    calls = {}

    def fake_commit_reply(chat_id, **kw):
        calls["commit_reply"] = kw
        return {"status": "sent", "message_id": 1}

    def fake_persist_draft(chat_id, **kw):
        calls["persist_draft"] = kw
        return {"status": "draft", "message_id": 2}

    monkeypatch.setattr(g.svc, "commit_reply", fake_commit_reply)
    monkeypatch.setattr(g.svc, "persist_draft", fake_persist_draft)
    result = async_to_sync(g._commit)(state)
    return result, calls


def _state(action, autonomy, **extra):
    return {
        "chat_id": 1,
        "principal_id": 10,
        "counterparty_user_id": 20,
        "inbound_message_id": 99,
        "autonomy": autonomy,
        "decision": {"action": action, "disclosed_fields": {}, "private_rationale": ""},
        "drafted": {"body": "ok"},
        **extra,
    }


def test_accept_always_persists_draft_even_under_auto_send(monkeypatch):
    state = _state(
        "accept",
        "auto_send",
        stance="sell_side",
        negotiation={"their_last_offer": 612000},
        focal_mandate={"floor_price": 600000},
    )
    result, calls = _run_commit(state, monkeypatch)
    assert result["outcome"] == "draft"
    assert "commit_reply" not in calls
    rec = calls["persist_draft"]["approval_context"]["recommendation"]
    assert "$612,000" in rec and "floor" in rec


def test_non_accept_auto_send_commits_with_intent_and_focal(monkeypatch):
    state = _state("propose", "auto_send", intent="offer_negotiation", focal_listing_id=7)
    result, calls = _run_commit(state, monkeypatch)
    assert result["outcome"] == "sent"
    assert calls["commit_reply"]["intent"] == "offer_negotiation"
    assert calls["commit_reply"]["focal_listing_id"] == 7


def test_draft_for_approval_still_drafts(monkeypatch):
    result, calls = _run_commit(_state("inform", "draft_for_approval"), monkeypatch)
    assert result["outcome"] == "draft"
    assert "commit_reply" not in calls


# ---- _escalate: owner-facing headline ----------------------------------------------
def _run_escalate(state, monkeypatch):
    captured = {}

    def fake_escalate(chat_id, principal_id, reason, **kw):
        captured.update(chat_id=chat_id, principal_id=principal_id, reason=reason, **kw)
        return {"status": "escalated", "reason": reason}

    monkeypatch.setattr(g.svc, "escalate", fake_escalate)
    result = async_to_sync(g._escalate)({"chat_id": 1, "principal_id": 10, **state})
    return result, captured


def test_escalate_headline_names_counterparty_and_note(monkeypatch):
    """A decide-stage escalation notifies with WHO reached out + the owner-facing
    escalation_note; the private_rationale goes to the audit log, not the headline."""
    result, captured = _run_escalate(
        {
            "counterparty_name": "Maya Chen",
            "decision": {
                "action": "escalate",
                "escalation_note": "They are asking for the roof age and service records.",
                "private_rationale": "internal reasoning, audit only",
            },
        },
        monkeypatch,
    )
    assert result["outcome"] == "escalated"
    assert captured["reason"] == (
        "Maya Chen has reached out. They are asking for the roof age and service records."
    )
    assert captured["private_rationale"] == "internal reasoning, audit only"


def test_escalate_gate_error_beats_note_and_stale_reason(monkeypatch):
    _, captured = _run_escalate(
        {
            "counterparty_name": "Maya Chen",
            "gate_error": "output: leaked a private limit",
            "escalation_reason": "stale screen rationale that must not surface",
            "decision": {"action": "inform", "escalation_note": "note"},
        },
        monkeypatch,
    )
    assert captured["reason"] == "Maya Chen has reached out. output: leaked a private limit"


def test_escalate_falls_back_without_name_or_note(monkeypatch):
    _, captured = _run_escalate({}, monkeypatch)
    assert captured["reason"] == "The counterparty has reached out. Your reply is needed."
