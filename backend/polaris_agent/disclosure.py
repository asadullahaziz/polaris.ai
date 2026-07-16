"""
Deterministic disclosure gates — the layers that make a fooled model harmless.
Pure functions, no LLM, unit-testable in isolation. The model proposes; code
disposes. These gates are code on purpose: a prompt edit must never be able to
weaken the airlock.

  * policy_gate  — Stage 1 may propose a decision; this code disposes. Any offer
                   must sit within the focal mandate's bound for the agent's
                   stance (buy_side ≤ ceiling; sell_side ≥ floor) and concede
                   monotonically toward the counterparty (never walk an offer
                   backwards); `accept` is valid only against a standing
                   agent-disclosed counterparty offer that is itself within our
                   bound; disclosed fields must be a subset of the closed
                   whitelist; the action must be allowed. A manipulated Stage 1
                   that proposes out-of-mandate is rejected → escalate.
  * output_check — deterministic backstop before send. Scan the drafted body for
                   the literal values of the principal's private limits (we know
                   them all) and block any leak; then `style_check` enforces the
                   human-voice contract (no AI/assistant self-reference, no em/en
                   dashes, bounded length). Catches accidental leaks and
                   robot-voice; the real guarantee is the airlock (Stage 2 never
                   sees any mandate).
  * approve_shares / render_shared_lines — the only way engine numbers reach
                   Stage 2: Stage 1 sets boolean share flags; this code verifies
                   the data really exists in tool_results and renders the strings
                   itself. The drafting model can cite figures but never author
                   them.
"""

from __future__ import annotations

import re

# The agent emits `propose` for both an opening offer and a counter.
ALLOWED_ACTIONS = {
    "ask",
    "inform",
    "qualify",
    "hold",
    "decline",
    "escalate",
    "propose",
    "accept",
    "no_reply",
}
# The closed disclosure whitelist (state.py DisclosedFields) — no floor/ceiling/memory
# slot, so there is nowhere to put a secret.
ALLOWED_DISCLOSED_FIELDS = {"must_haves", "offer_price", "availability"}

# ---- Human-voice contract (style gate) ------------------------------------------
MAX_REPLY_CHARS = 500
_DASHES = ("—", "–")  # em dash, en dash — the loudest AI tell
# Case-insensitive; word-bounded where a bare substring would false-positive
# ("maintain", "said", "brain"). The UI badge already discloses the agent — the body
# must read like a busy human, so any self-narration is rejected deterministically.
BANNED_PATTERNS = [
    re.compile(p, re.IGNORECASE)
    for p in (
        r"\bassistant\b",
        r"\ba\.?\s?i\.?\b",
        r"\bas an (?:ai|assistant|agent)\b",
        r"\blanguage model\b",
        r"\bon (?:your|his|her|their|\w+'s) behalf\b",
        r"\bon behalf of\b",
        r"\bwhile (?:he|she|they)(?:'s|'re| is| are)? (?:away|out|unavailable)\b",
        r"\bcovering for\b",
        r"\binterest (?:is|level|remains)\s*(?:high|medium|low)\b",
    )
]


def style_check(body: str) -> tuple[bool, str | None]:
    """(ok, reason). The deterministic human-voice contract on the outgoing body."""
    for d in _DASHES:
        if d in body:
            return False, "em/en dash in body (use a comma or period)"
    for pat in BANNED_PATTERNS:
        m = pat.search(body)
        if m:
            return False, f"banned phrase {m.group(0)!r} (never self-narrate as an assistant/AI)"
    if len(body) > MAX_REPLY_CHARS:
        return False, f"body too long ({len(body)} > {MAX_REPLY_CHARS} chars); cut it down"
    return True, None


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


def policy_gate(
    decision: dict, mandate: dict, stance: str, *, negotiation: dict | None = None
) -> tuple[bool, str | None]:
    """(ok, reason). ok=False → the graph escalates instead of sending. `mandate` is the
    FOCAL mandate driving this decision; `stance` ∈ {buy_side, sell_side, neutral};
    `negotiation` = {my_last_offer, their_last_offer} (agent-disclosed, from the deal)."""
    action = decision.get("action")
    if action not in ALLOWED_ACTIONS:
        return False, f"unknown action {action!r}"

    fields = decision.get("disclosed_fields") or {}
    extra = set(fields) - ALLOWED_DISCLOSED_FIELDS
    if extra:
        return False, f"disclosed fields outside whitelist: {sorted(extra)}"

    neg = negotiation or {}
    floor = mandate.get("floor_price")
    ceiling = mandate.get("ceiling_price")

    offer = fields.get("offer_price")
    if action == "propose" and offer is None:
        return False, "propose without offer_price"
    if offer is not None:
        offer = int(offer)
        # (1) Within the mandate bound for our stance.
        if stance == "buy_side" and ceiling is not None and offer > int(ceiling):
            return False, "offer exceeds the buyer's ceiling"
        if stance == "sell_side" and floor is not None and offer < int(floor):
            return False, "offer below the seller's floor"
        # (2) Monotonic concession: never walk our own offer backwards.
        mine = neg.get("my_last_offer")
        if mine is not None:
            if stance == "buy_side" and offer < int(mine):
                return False, "offer regresses below our previous offer"
            if stance == "sell_side" and offer > int(mine):
                return False, "offer regresses above our previous offer"

    if action == "accept":
        theirs = neg.get("their_last_offer")
        if theirs is None:
            return False, "accept without a standing counterparty offer on record"
        theirs = int(theirs)
        if stance == "buy_side" and ceiling is not None and theirs > int(ceiling):
            return False, "standing offer exceeds the buyer's ceiling"
        if stance == "sell_side" and floor is not None and theirs < int(floor):
            return False, "standing offer below the seller's floor"

    return True, None


def output_check(body: str, private_limits, decision: dict) -> tuple[bool, str | None]:
    """(ok, reason). Deterministic literal scan for ANY leaked private limit (the union
    across all the principal's mandates), a non-empty check, then the style gate."""
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
    return style_check(body)


# ---- Share flags: how engine numbers cross to Stage 2 ---------------------------
def approve_shares(decision: dict, tool_results: dict) -> dict:
    """{"comps": bool, "valuation": bool} — a share flag survives only when Stage 1
    requested it AND the engine data actually exists. Unfulfillable flags are silently
    stripped (the reply just cites nothing) rather than escalating."""
    tr = tool_results or {}
    val = (tr.get("valuation") or {}).get("value") or {}
    has_comps = bool((tr.get("valuation") or {}).get("comps"))
    return {
        "comps": bool(decision.get("share_comps")) and has_comps,
        "valuation": bool(decision.get("share_valuation")) and val.get("point") is not None,
    }


def render_shared_lines(tool_results: dict, approved: dict) -> list[str]:
    """Deterministic, engine-authored figure lines for Stage 2 to cite. Hyphens only —
    these strings feed the drafting prompt and must themselves pass the style gate."""
    tr = tool_results or {}
    val = tr.get("valuation") or {}
    v = val.get("value") or {}
    lines: list[str] = []
    if approved.get("valuation") and v.get("point") is not None:
        rng = ""
        if v.get("low") is not None and v.get("high") is not None:
            rng = f" (range ${int(v['low']):,} to ${int(v['high']):,})"
        n = val.get("n_comps") or len(val.get("comps") or [])
        lines.append(
            f"Recent comps put market value around ${int(v['point']):,}{rng}, based on {n} sales."
        )
        arv_v = (val.get("arv") or {}).get("point")
        if arv_v is not None:
            lines.append(f"Renovated comps support an ARV around ${int(arv_v):,}.")
    if approved.get("comps"):
        for c in (val.get("comps") or [])[:3]:
            price = c.get("price")
            addr = c.get("address")
            if price is None or not addr:
                continue
            dist = c.get("distance_mi")
            dist_s = f", {float(dist):.1f} mi away" if dist is not None else ""
            sold = c.get("sold_on")
            sold_s = f", sold {sold}" if sold else ""
            lines.append(f"Comp: {addr} at ${int(float(price)):,}{dist_s}{sold_s}.")
    return lines
