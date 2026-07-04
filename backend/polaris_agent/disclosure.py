"""
Deterministic disclosure gates (architecture §5, §12) — the layers that make a
*fooled* model harmless. Pure functions, no LLM, unit-testable in isolation. "The
model proposes; code disposes."

  * policy_gate  — GUARANTEE (bounds blast radius). Stage 1 may PROPOSE a decision;
                   this code DISPOSES. Any offer must sit within the focal mandate's
                   bound for the agent's **stance** (buy_side ≤ ceiling; sell_side ≥
                   floor); disclosed fields must be a subset of the closed whitelist;
                   the action must be allowed. A manipulated Stage 1 that proposes
                   out-of-mandate is rejected → escalate. (§12 layer 2)
  * output_check — deterministic backstop before send. Scan the drafted body for the
                   LITERAL values of the principal's private limits (we know them all)
                   and block any leak; confirm the body is non-empty. Catches accidental
                   literal leaks; the real guarantee is the airlock (Stage 2 never sees
                   any mandate). (§12 layer 5)

v2 rewire (P4, revisions §disclosure): `role → stance` in `policy_gate`, and
`output_check` scans the **union of the principal's private limits** (a list of ints)
rather than a single mandate's floor/ceiling — because the away-assistant has no fixed
role and may hold several mandates at once (owned-listing floors + buy-box ceilings), so
"never leak a limit" must hold across all of them regardless of stance.
"""

from __future__ import annotations

# The v1 qualify-and-hold action set (architecture §5). propose/counter/accept are stretch.
ALLOWED_ACTIONS = {"ask", "inform", "qualify", "hold", "decline", "escalate"}
# The closed disclosure whitelist (state.py DisclosedFields) — no floor/ceiling/memory slot.
ALLOWED_DISCLOSED_FIELDS = {"interest_level", "must_haves", "offer_price", "availability"}


def _secret_values(private_limits, disclosed_offer: int | None) -> list[int]:
    """The limit value(s) that must never appear in a cross-boundary body, minus any
    offer the agent deliberately chose to disclose (an offer equal to a limit is not a
    leak). `private_limits` is the union of every floor/ceiling the principal holds."""
    out: list[int] = []
    for v in private_limits or []:
        if v is None:
            continue
        iv = int(v)
        if iv != (disclosed_offer if disclosed_offer is not None else -1):
            out.append(iv)
    return out


def _literal_variants(v: int) -> list[str]:
    """Common textual renderings of a dollar figure a model might emit ($500,000 / 500k / …)."""
    out = {str(v), f"{v:,}", f"${v:,}", f"${v}"}
    if v >= 1000:
        for k in {v // 1000, round(v / 1000)}:
            out |= {f"{k}k", f"${k}k"}
    return [s.lower() for s in out]


def policy_gate(decision: dict, mandate: dict, stance: str) -> tuple[bool, str | None]:
    """(ok, reason). ok=False → the graph escalates instead of sending. `mandate` is the
    FOCAL mandate driving this decision; `stance` ∈ {buy_side, sell_side, neutral}."""
    action = decision.get("action")
    if action not in ALLOWED_ACTIONS:
        return False, f"unknown action {action!r}"

    fields = decision.get("disclosed_fields") or {}
    extra = set(fields) - ALLOWED_DISCLOSED_FIELDS
    if extra:
        return False, f"disclosed fields outside whitelist: {sorted(extra)}"

    offer = fields.get("offer_price")
    if offer is not None:
        offer = int(offer)
        floor = mandate.get("floor_price")
        ceiling = mandate.get("ceiling_price")
        if stance == "buy_side" and ceiling is not None and offer > int(ceiling):
            return False, "offer exceeds the buyer's ceiling"
        if stance == "sell_side" and floor is not None and offer < int(floor):
            return False, "offer below the seller's floor"
    return True, None


def output_check(body: str, private_limits, decision: dict) -> tuple[bool, str | None]:
    """(ok, reason). Deterministic literal scan for ANY leaked private limit (the union
    across all the principal's mandates) + a non-empty check."""
    if not body or not body.strip():
        return False, "empty draft"
    fields = decision.get("disclosed_fields") or {}
    raw_offer = fields.get("offer_price")
    disclosed_offer = int(raw_offer) if raw_offer is not None else None

    hay = body.lower()
    for v in _secret_values(private_limits, disclosed_offer):
        for variant in _literal_variants(v):
            if variant in hay:
                return False, f"body leaks a mandate limit ({v})"
    return True, None
