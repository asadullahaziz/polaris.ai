"""
Disclosure gates — pure, LLM-free (architecture §12). The layers that make a *fooled*
model harmless: `policy_gate` (a proposed decision must sit within mandate bounds + the
closed whitelist) and `output_check` (no literal limit leaks in the drafted body).

Consumed by the away-responder in P4; ported + unit-tested here (P2) as pure functions.
"""

from __future__ import annotations

from polaris_agent.disclosure import output_check, policy_gate

BUYER_MANDATE = {"ceiling_price": 480000, "floor_price": None}
SELLER_MANDATE = {"floor_price": 700000, "ceiling_price": None}


# ---- policy_gate --------------------------------------------------------------
def test_policy_gate_allows_clean_decision():
    decision = {"action": "qualify", "disclosed_fields": {"interest_level": "high"}}
    assert policy_gate(decision, BUYER_MANDATE, "buyer_agent") == (True, None)


def test_policy_gate_rejects_unknown_action():
    ok, reason = policy_gate({"action": "wire_the_money"}, BUYER_MANDATE, "buyer_agent")
    assert ok is False and "unknown action" in reason


def test_policy_gate_rejects_field_outside_whitelist():
    decision = {"action": "inform", "disclosed_fields": {"ceiling_price": 480000}}
    ok, reason = policy_gate(decision, BUYER_MANDATE, "buyer_agent")
    assert ok is False and "whitelist" in reason


def test_policy_gate_rejects_offer_over_buyer_ceiling():
    decision = {"action": "qualify", "disclosed_fields": {"offer_price": 500000}}
    ok, reason = policy_gate(decision, BUYER_MANDATE, "buyer_agent")
    assert ok is False and "ceiling" in reason


def test_policy_gate_allows_offer_at_buyer_ceiling():
    decision = {"action": "qualify", "disclosed_fields": {"offer_price": 480000}}
    assert policy_gate(decision, BUYER_MANDATE, "buyer_agent")[0] is True


def test_policy_gate_rejects_offer_below_seller_floor():
    decision = {"action": "inform", "disclosed_fields": {"offer_price": 650000}}
    ok, reason = policy_gate(decision, SELLER_MANDATE, "seller_agent")
    assert ok is False and "floor" in reason


# ---- output_check -------------------------------------------------------------
def test_output_check_blocks_empty_body():
    ok, reason = output_check("   ", BUYER_MANDATE, {"disclosed_fields": {}}, "buyer_agent")
    assert ok is False and "empty" in reason


def test_output_check_blocks_literal_ceiling_leak_variants():
    decision = {"disclosed_fields": {}}
    for body in ("I can go up to $480,000.", "my max is 480k", "ceiling is 480000"):
        ok, reason = output_check(body, BUYER_MANDATE, decision, "buyer_agent")
        assert ok is False and "leaks" in reason, body


def test_output_check_blocks_seller_floor_leak():
    body = "The seller won't take less than $700,000."
    ok, reason = output_check(body, SELLER_MANDATE, {"disclosed_fields": {}}, "seller_agent")
    assert ok is False and "leaks" in reason


def test_output_check_passes_clean_body():
    body = "Great — I'm interested and would love to schedule a walkthrough this week."
    assert output_check(body, BUYER_MANDATE, {"disclosed_fields": {}}, "buyer_agent") == (
        True,
        None,
    )


def test_output_check_allows_offer_equal_to_limit_when_disclosed():
    # An offer the agent deliberately chose to state == the ceiling is not a leak.
    decision = {"disclosed_fields": {"offer_price": 480000}}
    body = "I'd offer $480,000 for it."
    assert output_check(body, BUYER_MANDATE, decision, "buyer_agent") == (True, None)
