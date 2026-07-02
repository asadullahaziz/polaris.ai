"""
Deterministic disclosure gates (architecture §5, §12) — the layers that make a
*fooled* model harmless. Pure functions, no LLM, unit-testable in isolation. "The
model proposes; code disposes."

  * policy_gate  — GUARANTEE (bounds blast radius). Stage 1 may PROPOSE a decision;
                   this code DISPOSES. Any offer must sit within the mandate's
                   [floor, ceiling] for the agent's side; disclosed fields must be a
                   subset of the closed whitelist; the action must be allowed. A
                   manipulated Stage 1 that proposes out-of-mandate is rejected →
                   escalate. (§12 layer 2)
  * output_check — deterministic backstop before send. Scan the drafted body for the
                   LITERAL floor/ceiling values (we know them) and block any leak;
                   confirm the body is non-empty. Catches accidental literal leaks;
                   the real guarantee is the airlock (Stage 2 never sees the mandate).
                   (§12 layer 5)
"""

from __future__ import annotations

# The v1 qualify-and-hold action set (architecture §5). propose/counter/accept are stretch.
ALLOWED_ACTIONS = {"ask", "inform", "qualify", "hold", "decline", "escalate"}
# The closed disclosure whitelist (state.py DisclosedFields) — no floor/ceiling/memory slot.
ALLOWED_DISCLOSED_FIELDS = {"interest_level", "must_haves", "offer_price", "availability"}


def _secret_values(mandate: dict, role: str, disclosed_offer: int | None) -> list[int]:
    """The limit(s) that must never appear in a cross-boundary body, minus any offer the
    agent deliberately chose to disclose (an offer equal to a limit is not a leak)."""
    vals: list[int] = []
    for key in ("floor_price", "ceiling_price"):
        v = mandate.get(key)
        if v is not None and int(v) != (disclosed_offer if disclosed_offer is not None else -1):
            vals.append(int(v))
    return vals


def _literal_variants(v: int) -> list[str]:
    """Common textual renderings of a dollar figure a model might emit ($500,000 / 500k / …)."""
    out = {str(v), f"{v:,}", f"${v:,}", f"${v}"}
    if v >= 1000:
        for k in {v // 1000, round(v / 1000)}:
            out |= {f"{k}k", f"${k}k"}
    return [s.lower() for s in out]


def policy_gate(decision: dict, mandate: dict, role: str) -> tuple[bool, str | None]:
    """(ok, reason). ok=False → the graph escalates instead of sending."""
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
        if role == "buyer_agent" and ceiling is not None and offer > int(ceiling):
            return False, "offer exceeds the buyer's ceiling"
        if role == "seller_agent" and floor is not None and offer < int(floor):
            return False, "offer below the seller's floor"
    return True, None


def output_check(body: str, mandate: dict, decision: dict, role: str) -> tuple[bool, str | None]:
    """(ok, reason). Deterministic literal scan for leaked limits + non-empty check."""
    if not body or not body.strip():
        return False, "empty draft"
    fields = decision.get("disclosed_fields") or {}
    raw_offer = fields.get("offer_price")
    disclosed_offer = int(raw_offer) if raw_offer is not None else None

    hay = body.lower()
    for v in _secret_values(mandate, role, disclosed_offer):
        for variant in _literal_variants(v):
            if variant in hay:
                return False, f"body leaks a mandate limit ({v})"
    return True, None
