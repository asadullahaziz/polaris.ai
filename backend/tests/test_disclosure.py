"""
Disclosure gates — pure, LLM-free. The layers that make a fooled model harmless:
`policy_gate` (a proposed decision must sit within the focal mandate's bound for
the stance + the closed whitelist) and `output_check` (no literal limit leaks in
the drafted body, scanned over the union of the principal's private limits — the
away-assistant may hold several mandates at once, and "never leak a limit" must
hold across all of them regardless of stance).
"""

from __future__ import annotations

from polaris_agent.disclosure import (
    approve_shares,
    output_check,
    policy_gate,
    render_shared_lines,
    style_check,
)

# Focal mandate (drives the offer bound) + the union of the principal's private limits.
BUYER_MANDATE = {"ceiling_price": 480000, "floor_price": None}
BUYER_LIMITS = [480000]
SELLER_MANDATE = {"floor_price": 700000, "ceiling_price": None}
SELLER_LIMITS = [700000]
# A principal holding BOTH an owned-listing floor and a buy-box ceiling at once.
BOTH_LIMITS = [700000, 480000]


# ---- policy_gate --------------------------------------------------------------
def test_policy_gate_allows_clean_decision():
    decision = {"action": "qualify", "disclosed_fields": {"availability": "this week"}}
    assert policy_gate(decision, BUYER_MANDATE, "buy_side") == (True, None)


def test_policy_gate_rejects_interest_level_since_removed():
    # interest_level is not in the whitelist: the action itself carries the signal.
    decision = {"action": "qualify", "disclosed_fields": {"interest_level": "high"}}
    ok, reason = policy_gate(decision, BUYER_MANDATE, "buy_side")
    assert ok is False and "whitelist" in reason


def test_policy_gate_rejects_unknown_action():
    ok, reason = policy_gate({"action": "wire_the_money"}, BUYER_MANDATE, "buy_side")
    assert ok is False and "unknown action" in reason


def test_policy_gate_rejects_field_outside_whitelist():
    decision = {"action": "inform", "disclosed_fields": {"ceiling_price": 480000}}
    ok, reason = policy_gate(decision, BUYER_MANDATE, "buy_side")
    assert ok is False and "whitelist" in reason


def test_policy_gate_rejects_offer_over_buyer_ceiling():
    decision = {"action": "qualify", "disclosed_fields": {"offer_price": 500000}}
    ok, reason = policy_gate(decision, BUYER_MANDATE, "buy_side")
    assert ok is False and "ceiling" in reason


def test_policy_gate_allows_offer_at_buyer_ceiling():
    decision = {"action": "qualify", "disclosed_fields": {"offer_price": 480000}}
    assert policy_gate(decision, BUYER_MANDATE, "buy_side")[0] is True


def test_policy_gate_rejects_offer_below_seller_floor():
    decision = {"action": "inform", "disclosed_fields": {"offer_price": 650000}}
    ok, reason = policy_gate(decision, SELLER_MANDATE, "sell_side")
    assert ok is False and "floor" in reason


def test_policy_gate_neutral_stance_has_no_price_bound():
    # A neutral turn (no focal listing) has no mandate to bound against — an offer field
    # is unusual but not gated on price; the whitelist still applies.
    decision = {"action": "inform", "disclosed_fields": {"offer_price": 999999}}
    assert policy_gate(decision, {}, "neutral") == (True, None)


# ---- output_check -------------------------------------------------------------
def test_output_check_blocks_empty_body():
    ok, reason = output_check("   ", BUYER_LIMITS, {"disclosed_fields": {}})
    assert ok is False and "empty" in reason


def test_output_check_blocks_literal_ceiling_leak_variants():
    decision = {"disclosed_fields": {}}
    for body in ("I can go up to $480,000.", "my max is 480k", "ceiling is 480000"):
        ok, reason = output_check(body, BUYER_LIMITS, decision)
        assert ok is False and "leaks" in reason, body


def test_output_check_blocks_seller_floor_leak():
    body = "The seller won't take less than $700,000."
    ok, reason = output_check(body, SELLER_LIMITS, {"disclosed_fields": {}})
    assert ok is False and "leaks" in reason


def test_output_check_scans_union_of_all_limits():
    # A principal who is both a seller (floor 700k) and a buyer (ceiling 480k): a leak of
    # either value is caught, regardless of which stance drove the turn.
    decision = {"disclosed_fields": {}}
    assert output_check("around 700k", BOTH_LIMITS, decision)[0] is False
    assert output_check("no more than $480,000", BOTH_LIMITS, decision)[0] is False


def test_output_check_passes_clean_body():
    body = "I'm interested. Can we set up a walkthrough this week?"
    assert output_check(body, BUYER_LIMITS, {"disclosed_fields": {}}) == (True, None)


def test_output_check_allows_offer_equal_to_limit_when_disclosed():
    # An offer the agent deliberately chose to state == the ceiling is not a leak.
    decision = {"disclosed_fields": {"offer_price": 480000}}
    body = "I'd offer $480,000 for it."
    assert output_check(body, BUYER_LIMITS, decision) == (True, None)


# ---- negotiation rules ----------------------------------------------------------
def test_propose_requires_offer_price():
    ok, reason = policy_gate(
        {"action": "propose", "disclosed_fields": {}}, BUYER_MANDATE, "buy_side"
    )
    assert ok is False and "offer_price" in reason


def test_propose_monotonic_buy_side_never_regresses():
    neg = {"my_last_offer": 400000, "their_last_offer": None}
    down = {"action": "propose", "disclosed_fields": {"offer_price": 390000}}
    up = {"action": "propose", "disclosed_fields": {"offer_price": 420000}}
    assert policy_gate(down, BUYER_MANDATE, "buy_side", negotiation=neg)[0] is False
    assert policy_gate(up, BUYER_MANDATE, "buy_side", negotiation=neg)[0] is True


def test_propose_monotonic_sell_side_never_regresses():
    neg = {"my_last_offer": 750000, "their_last_offer": None}
    up = {"action": "propose", "disclosed_fields": {"offer_price": 760000}}
    down = {"action": "propose", "disclosed_fields": {"offer_price": 720000}}
    assert policy_gate(up, SELLER_MANDATE, "sell_side", negotiation=neg)[0] is False
    assert policy_gate(down, SELLER_MANDATE, "sell_side", negotiation=neg)[0] is True


def test_propose_still_bounded_by_mandate_even_when_monotonic():
    neg = {"my_last_offer": 470000, "their_last_offer": None}
    decision = {"action": "propose", "disclosed_fields": {"offer_price": 490000}}
    ok, reason = policy_gate(decision, BUYER_MANDATE, "buy_side", negotiation=neg)
    assert ok is False and "ceiling" in reason


def test_accept_requires_standing_offer():
    ok, reason = policy_gate(
        {"action": "accept", "disclosed_fields": {}}, SELLER_MANDATE, "sell_side"
    )
    assert ok is False and "standing" in reason


def test_accept_rejects_out_of_bound_standing_offer():
    neg = {"my_last_offer": None, "their_last_offer": 650000}  # below the 700k floor
    ok, reason = policy_gate(
        {"action": "accept", "disclosed_fields": {}}, SELLER_MANDATE, "sell_side", negotiation=neg
    )
    assert ok is False and "floor" in reason


def test_accept_allows_in_bound_standing_offer():
    neg = {"my_last_offer": None, "their_last_offer": 720000}
    assert (
        policy_gate(
            {"action": "accept", "disclosed_fields": {}},
            SELLER_MANDATE,
            "sell_side",
            negotiation=neg,
        )[0]
        is True
    )


def test_no_reply_is_an_allowed_action():
    assert policy_gate({"action": "no_reply", "disclosed_fields": {}}, {}, "neutral")[0] is True


# ---- style gate (the human-voice contract) --------------------------------------
def test_style_check_rejects_em_and_en_dashes():
    assert style_check("Sounds good — I'll check.")[0] is False
    assert style_check("The range is 400–450.")[0] is False


def test_style_check_rejects_ai_self_narration():
    for body in (
        "I'm Walt's assistant covering while he's out.",
        "As an AI, I can't say.",
        "I'm replying on your behalf today.",
        "I'm covering for Walt this week.",
        "Erin's interest is medium at this stage.",
    ):
        ok, reason = style_check(body)
        assert ok is False, body


def test_style_check_word_bounds_do_not_false_positive():
    # "maintain" contains 'ai'; "said" contains 'ai'-adjacent letters — none should trip.
    body = "We maintain the roof yearly, as the inspector said. Repairs are done."
    assert style_check(body) == (True, None)


def test_style_check_rejects_overlong_body():
    ok, reason = style_check("word " * 150)
    assert ok is False and "long" in reason


def test_output_check_runs_style_after_leak_scan():
    ok, reason = output_check("Deal — let's talk.", BUYER_LIMITS, {"disclosed_fields": {}})
    assert ok is False and "dash" in reason


# ---- share flags ----------------------------------------------------------------
_VALUATION = {
    "value": {"low": 410000, "point": 430000, "high": 450000},
    "arv": {"low": 500000, "point": 520000, "high": 540000},
    "n_comps": 7,
    "comps": [
        {"address": "12 Cedar Ln", "price": 425000, "distance_mi": 0.4, "sold_on": "2026-03-02"},
        {"address": "9 Birch Ct", "price": 441000, "distance_mi": 0.8, "sold_on": "2026-01-15"},
    ],
}


def test_approve_shares_strips_unfulfillable_flags():
    decision = {"share_comps": True, "share_valuation": True}
    assert approve_shares(decision, {}) == {"comps": False, "valuation": False}
    assert approve_shares(decision, {"valuation": _VALUATION}) == {
        "comps": True,
        "valuation": True,
    }


def test_approve_shares_respects_unset_flags():
    approved = approve_shares({}, {"valuation": _VALUATION})
    assert approved == {"comps": False, "valuation": False}


def test_render_shared_lines_are_engine_authored_and_style_clean():
    approved = {"comps": True, "valuation": True}
    lines = render_shared_lines({"valuation": _VALUATION}, approved)
    assert any("$430,000" in line for line in lines)  # the value point
    assert any("ARV" in line for line in lines)
    assert any("12 Cedar Ln" in line for line in lines)
    for line in lines:  # the strings feed prompts — they must pass the style gate too
        assert style_check(line) == (True, None), line


def test_render_shared_lines_empty_when_nothing_approved():
    assert (
        render_shared_lines({"valuation": _VALUATION}, {"comps": False, "valuation": False}) == []
    )
