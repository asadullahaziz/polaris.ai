"""
Graph 2 — Away-assistant responder (architecture §5, §8, §12; revisions 2026-07-03).

The presence-gated away-cover chatbot, built as a two-stage airlock `StateGraph`. It is
invoked from an Inngest step (the `chat/inbound` handler) after the grace window, and
makes **one** reply per turn. There is no fixed buyer/seller role and no bound listing:
`stance` (buy_side / sell_side / neutral) is derived from OWNERSHIP of the focal listing
and only selects which assessment runs + which mandate Stage 1 reasons from.

The generalized front half + the unchanged airlock spine (revisions §auto-responder):

  screen (Haiku)   → refuse+escalate on suspected injection/manipulation
  triage (Haiku)   → classify intent → route the conditional assess
  assess (engine)  → deterministic deal math for questions AND offers on the focal listing
  DECIDE (Stage 1) → PRIVATE ctx (mandate/assessment/deal state) → CLOSED decision
  policy gate      → deterministic: offer ∈ mandate bound + monotonic concession,
                     accept only vs a recorded in-bound offer, fields ⊆ whitelist;
                     share flags resolved into engine-rendered figure lines
  no_reply         → contentless inbound: end silently (no message, no notification)
  DRAFT (Stage 2)  → PUBLIC-only ctx (transcript + public facts + share lines) → body
  output check     → deterministic: no literal limit leak + human-voice style gate;
                     ONE retry with the violation fed back, then escalate
  commit gate      → auto_send → the DB commit gate; draft_for_approval → draft +
                     notify; `accept` ALWAYS drafts (the principal signs)

Stage 2 never receives any mandate, so it cannot voice a limit it never held — and the
output check scans the UNION of every private limit the principal holds, so "never leak a
limit" holds regardless of stance. The real guarantees live in the deterministic gates
(disclosure.py) + the DB commit gate (chat.responder_service); this module orchestrates
and narrates. Engine tools are called deterministically in `assess` (not exposed as LLM
tools) — the same "engine scores, LLM narrates" collapse as Graph 3.
"""

from __future__ import annotations

import logging
from typing import Literal

from asgiref.sync import sync_to_async
from langgraph.graph import END, START, StateGraph
from pydantic import BaseModel, Field

from chat import responder_service as svc
from polaris_agent import dal
from polaris_agent.disclosure import (
    approve_shares,
    output_check,
    policy_gate,
    render_shared_lines,
)
from polaris_agent.models import get_model
from polaris_agent.prompts import (
    responder_decide_prompt,
    responder_draft_prompt,
    responder_triage_prompt,
    screen_prompt,
    wrap_counterparty,
)
from polaris_agent.state import ResponderState

log = logging.getLogger(__name__)


# ---- Structured-output schemas -------------------------------------------------
class ScreenVerdict(BaseModel):
    suspicious: bool = Field(description="true ONLY for genuine injection/manipulation")
    reason: str = Field(default="", description="short reason")


class TriageVerdict(BaseModel):
    intent: Literal[
        "greeting_smalltalk", "listing_question", "offer_negotiation", "off_topic", "suspicious"
    ]


class _DisclosedFields(BaseModel):
    must_haves: list[str] = Field(default_factory=list)
    offer_price: int | None = None
    availability: str | None = None


class ResponderDecision(BaseModel):
    """Stage 1's CLOSED output. No floor/ceiling slot exists (state.py §airlock).
    share_* are boolean requests — the gate verifies the data exists and the GRAPH
    renders the figures; Stage 1 never authors a number for the counterparty."""

    action: Literal[
        "ask",
        "inform",
        "qualify",
        "hold",
        "decline",
        "escalate",
        "propose",
        "accept",
        "no_reply",
    ]
    disclosed_fields: _DisclosedFields = Field(default_factory=_DisclosedFields)
    share_comps: bool = Field(default=False, description="cite comp sales (sell side)")
    share_valuation: bool = Field(default=False, description="cite the market value range")
    private_rationale: str = Field(default="", description="audit only — NEVER sent")


def _clean_fields(model: _DisclosedFields) -> dict:
    """Drop empty values so the whitelist check + literal scan see only real disclosures."""
    d = model.model_dump()
    return {k: v for k, v in d.items() if v not in (None, "", [], {})}


# ---- Context rendering ---------------------------------------------------------
def _render_transcript(messages: list[dict], exclude_id: int) -> str:
    # No "(assistant)" tag: that word must never echo into a draft (style gate bans it).
    lines = []
    for m in messages:
        if m.get("id") == exclude_id:
            continue
        who = "You" if m.get("is_principal") else "Counterparty"
        lines.append(f"{who}: {m.get('body', '')}")
    return "\n".join(lines) or "(no prior messages)"


def _facts_line(listing: dict) -> str:
    facts = ", ".join(
        f"{k}={v}"
        for k, v in [
            ("address", listing.get("address")),
            ("beds", listing.get("beds")),
            ("baths", listing.get("baths")),
            ("sqft", listing.get("sqft")),
            ("condition", listing.get("condition")),
            ("year_built", listing.get("year_built")),
            ("asking", listing.get("asking_price")),
        ]
        if v is not None
    )
    return facts or "details sparse"


def _public_block(state: ResponderState) -> str:
    focal = state.get("focal_listing") or {}
    listing_line = (
        f"Focal listing (public): {_facts_line(focal)}"
        if focal
        else "No specific listing in focus."
    )
    n_others = max(0, len(state.get("listings") or []) - (1 if focal else 0))
    if n_others:
        listing_line += f"  ({n_others} other listing(s) have come up in this chat.)"
    transcript = _render_transcript(state.get("transcript") or [], state["inbound_message_id"])
    inbound_body = (state.get("inbound") or {}).get("body", "")
    return (
        f"{listing_line}\n\n"
        f"Conversation so far:\n{transcript}\n\n"
        f"New inbound message to respond to:\n{wrap_counterparty(inbound_body)}"
    )


def _valuation_line(val: dict) -> str:
    v = (val or {}).get("value") or {}
    point = v.get("point")
    if point is None:
        return "market valuation: unavailable (thin comps)"
    comps = val.get("comps") or []
    lo_hi = ""
    if v.get("low") is not None and v.get("high") is not None:
        lo_hi = f" (range ${int(v['low']):,} to ${int(v['high']):,})"
    line = f"market value ~${int(point):,}{lo_hi} from {val.get('n_comps') or len(comps)} comps"
    arv_point = (val.get("arv") or {}).get("point")
    if arv_point is not None:
        line += f"; ARV ~${int(arv_point):,}"
    return line


def _private_block(state: ResponderState) -> str:
    fm = state.get("focal_mandate") or {}
    stance = state.get("stance")
    limit_line = ""
    if stance == "buy_side" and fm.get("ceiling_price") is not None:
        limit_line = (
            f"Your principal's MAX price (ceiling), SECRET: ${int(fm['ceiling_price']):,} "
            "— never reveal or exceed.\n"
        )
    elif stance == "sell_side" and fm.get("floor_price") is not None:
        limit_line = (
            f"Your principal's MIN price (floor), SECRET: ${int(fm['floor_price']):,} "
            "— never reveal or go below.\n"
        )
    must = ", ".join(fm.get("must_haves") or []) or "none stated"
    ask_about = ", ".join(state.get("missing_must_haves") or []) or "none"
    tr = state.get("tool_results") or {}
    assess = tr.get("assess_deal") or {}
    assess_line = "none"
    if assess:
        assess_line = (
            f"verdict={assess.get('verdict')}, spread={assess.get('spread')}, "
            f"margin={assess.get('margin_pct')}, {assess.get('rationale', '')}"
        )
        if assess.get("max_offer") is not None:
            assess_line += (
                f"\nYour grounded MAX offer (PRIVATE, from the deal math): "
                f"${int(assess['max_offer']):,}. Anchor first proposals meaningfully "
                "below it and never propose above it."
            )
    val_line = _valuation_line(tr["valuation"]) if tr.get("valuation") else "none"
    mem = "; ".join(x.get("content", "") for x in (state.get("memory") or [])) or "none"
    instr = state.get("agent_instructions") or "none"

    deal = state.get("deal") or {}
    neg = state.get("negotiation") or {}
    deal_line = "no tracked deal yet"
    if deal:
        mine = neg.get("my_last_offer")
        theirs = neg.get("their_last_offer")
        deal_line = (
            f"stage={deal.get('stage')}, "
            f"our last disclosed offer={'$' + format(mine, ',') if mine is not None else 'none'}, "
            f"their standing recorded offer={'$' + format(theirs, ',') if theirs is not None else 'none (accept is not possible; propose or escalate)'}"
        )
    others = state.get("other_active_deals") or 0
    urgency_line = f"Other active buyers on this listing: {others}" + (
        " (you may honestly mention other interest)"
        if others >= 1
        else " (do NOT claim other interest)"
    )
    return (
        "PRIVATE CONTEXT — your principal's only; NEVER disclose any of this:\n"
        f"{limit_line}"
        f"Must-haves: {must}\n"
        f"Unaddressed must-haves you MAY ask about: {ask_about}\n"
        f"Mandate instructions: {fm.get('instructions') or 'none'}\n"
        f"Your principal's standing instructions: {instr}\n"
        f"Deal pipeline (mini CRM): {deal_line}\n"
        f"{urgency_line}\n"
        f"Deterministic deal assessment: {assess_line}\n"
        f"Deterministic valuation: {val_line}\n"
        f"Your memory of this principal: {mem}"
    )


# ---- Nodes ---------------------------------------------------------------------
async def _screen(state: ResponderState) -> dict:
    """Haiku injection/manipulation screen on the inbound (§12 layer 4)."""
    inbound_body = (state.get("inbound") or {}).get("body", "")
    try:
        model = get_model("bulk").with_structured_output(ScreenVerdict)
        verdict: ScreenVerdict = await model.ainvoke(
            f"{screen_prompt()}\n\n{wrap_counterparty(inbound_body)}"
        )
        return {"screen_flagged": bool(verdict.suspicious), "escalation_reason": verdict.reason}
    except Exception as exc:  # noqa: BLE001 - screen is a mitigation; never block the turn on it
        log.warning("injection screen failed, continuing unflagged: %s", exc)
        return {"screen_flagged": False}


async def _triage(state: ResponderState) -> dict:
    """Classify the inbound intent (the only LLM step in the generalized front half).
    Stance + focal listing are already resolved deterministically in `responder_plan`."""
    inbound_body = (state.get("inbound") or {}).get("body", "")
    try:
        model = get_model("bulk").with_structured_output(TriageVerdict)
        verdict: TriageVerdict = await model.ainvoke(
            f"{responder_triage_prompt()}\n\n{wrap_counterparty(inbound_body)}"
        )
        return {"intent": verdict.intent}
    except Exception as exc:  # noqa: BLE001 - fail to a safe, non-assessing intent
        log.warning("triage failed, defaulting to listing_question: %s", exc)
        return {"intent": "listing_question"}


async def _assess(state: ResponderState) -> dict:
    """Deterministic deal math — ONLY reached for an offer on a specific listing. Buy-side
    → the wholesale verdict; sell-side → market value + comps to defend the price."""
    tr = dict(state.get("tool_results") or {})
    focal_id = state.get("focal_listing_id")
    if state.get("stance") == "buy_side":
        tr["assess_deal"] = await dal.responder_assess(focal_id, state.get("strategy"))
    elif state.get("stance") == "sell_side":
        tr["valuation"] = await dal.responder_estimate(focal_id)
    return {"tool_results": tr}


async def _decide(state: ResponderState) -> dict:
    """STAGE 1 — PRIVATE context in, CLOSED structured action out. No prose."""
    model = get_model("workhorse").with_structured_output(ResponderDecision)
    deal_stage = (state.get("deal") or {}).get("stage")
    prompt = (
        f"{responder_decide_prompt(state.get('stance', 'neutral'), deal_stage)}\n\n"
        f"Inbound intent (triage): {state.get('intent')}\n\n"
        f"{_private_block(state)}\n\n{_public_block(state)}\n\n"
        "Decide the single best action now."
    )
    decision: ResponderDecision = await model.ainvoke(prompt)
    return {
        "decision": {
            "action": decision.action,
            "disclosed_fields": _clean_fields(decision.disclosed_fields),
            "share_comps": decision.share_comps,
            "share_valuation": decision.share_valuation,
            "private_rationale": decision.private_rationale,
        }
    }


def _gate(state: ResponderState) -> dict:
    """Deterministic policy gate (§12 layer 2). Model proposes; code disposes. On pass,
    resolve the share flags into engine-rendered figure lines for Stage 2 — the only
    path by which numbers cross the airlock."""
    decision = state["decision"]
    ok, reason = policy_gate(
        decision,
        state.get("focal_mandate") or {},
        state.get("stance"),
        negotiation=state.get("negotiation"),
    )
    if not ok:
        return {"gate_error": f"policy: {reason}"}
    approved = approve_shares(decision, state.get("tool_results") or {})
    return {"share_lines": render_shared_lines(state.get("tool_results") or {}, approved)}


async def _no_reply(state: ResponderState) -> dict:
    """End the turn silently — no message, no notification, no re-arm. Audit only."""
    decision = state.get("decision") or {}
    await sync_to_async(svc.log_action)(
        state["principal_id"],
        state["chat_id"],
        "no_reply",
        f"silent close on chat {state['chat_id']}",
        private_rationale=decision.get("private_rationale"),
    )
    return {"outcome": "no_reply"}


async def _draft(state: ResponderState) -> dict:
    """STAGE 2 — PUBLIC-only context in, prose out. NO mandate in this context. The
    only numbers available are the gate-approved, engine-rendered `share_lines`."""
    decision = state["decision"]
    model = get_model("workhorse")
    share_lines = state.get("share_lines") or []
    shares_block = (
        "Verified figures you may cite (use them naturally, change nothing):\n"
        + "\n".join(share_lines)
        + "\n\n"
        if share_lines
        else ""
    )
    feedback = state.get("draft_feedback")
    feedback_block = (
        f"Your previous draft was rejected: {feedback}. Rewrite the message fixing this.\n\n"
        if feedback
        else ""
    )
    prompt = (
        f"{responder_draft_prompt(state.get('stance', 'neutral'), state.get('display_name'))}\n\n"
        f"{_public_block(state)}\n\n"
        f"{shares_block}"
        f"{feedback_block}"
        f"Decided action: {decision['action']}\n"
        f"Fields you may reference (only these): {decision['disclosed_fields'] or 'none'}\n\n"
        "Write the message body to send now."
    )
    resp = await model.ainvoke(prompt)
    body = (resp.content or "").strip() if hasattr(resp, "content") else str(resp).strip()
    return {"drafted": {"body": body}}


def _validate(state: ResponderState) -> dict:
    """Deterministic output check (§12 layer 5): no literal leak of ANY private limit,
    plus the human-voice style gate. One retry with the violation fed back, then
    escalate — a style slip costs a redraft, never a robot message."""
    body = (state.get("drafted") or {}).get("body", "")
    ok, reason = output_check(body, state.get("private_limits") or [], state["decision"])
    if ok:
        return {"draft_feedback": None}
    if state.get("draft_attempts", 0) == 0:
        return {"draft_attempts": 1, "draft_feedback": reason}
    return {"gate_error": f"output: {reason}"}


async def _escalate(state: ResponderState) -> dict:
    """Set status + notify the principal; post NOTHING to the counterparty (§5)."""
    reason = state.get("gate_error") or state.get("escalation_reason") or "needs a human"
    await sync_to_async(svc.escalate)(state["chat_id"], state["principal_id"], reason)
    return {"outcome": "escalated", "escalation_reason": reason}


def _accept_recommendation(state: ResponderState) -> str:
    """Owner-only recommendation for the accept approval card (private numbers fine)."""
    neg = state.get("negotiation") or {}
    theirs = neg.get("their_last_offer")
    fm = state.get("focal_mandate") or {}
    if theirs is None:
        return "Your agent recommends accepting the standing offer."
    amount = f"${int(theirs):,}"
    if state.get("stance") == "sell_side" and fm.get("floor_price") is not None:
        return f"Offer {amount} clears your floor. Your agent recommends accepting."
    if state.get("stance") == "buy_side" and fm.get("ceiling_price") is not None:
        return f"Offer {amount} is within your ceiling. Your agent recommends accepting."
    return f"Your agent recommends accepting the standing offer of {amount}."


async def _commit(state: ResponderState) -> dict:
    """Send gate (§5). auto_send → the DB commit gate; draft_for_approval → draft +
    notify. `accept` ALWAYS drafts regardless of autonomy — the principal signs a deal,
    the agent never closes alone."""
    decision = state["decision"]
    body = (state.get("drafted") or {}).get("body", "")
    autonomy = state.get("autonomy", "draft_for_approval")
    common = dict(
        principal_id=state["principal_id"],
        action=decision["action"],
        body=body,
        disclosed_fields=decision["disclosed_fields"],
        inbound_message_id=state["inbound_message_id"],
        reply_to_id=state["inbound_message_id"],
        private_rationale=decision.get("private_rationale"),
    )
    if decision["action"] == "accept":
        res = await sync_to_async(svc.persist_draft)(
            state["chat_id"],
            approval_context={"recommendation": _accept_recommendation(state)},
            **common,
        )
    elif autonomy == "auto_send":
        terminal = "no_fit" if decision["action"] == "decline" else None
        res = await sync_to_async(svc.commit_reply)(
            state["chat_id"],
            counterparty_user_id=state.get("counterparty_user_id"),
            terminal=terminal,
            intent=state.get("intent"),
            focal_listing_id=state.get("focal_listing_id"),
            **common,
        )
    else:
        res = await sync_to_async(svc.persist_draft)(state["chat_id"], **common)
    return {"outcome": res.get("status"), "commit_result": res}


# ---- Routers -------------------------------------------------------------------
def _after_screen(state: ResponderState) -> str:
    return "escalate" if state.get("screen_flagged") else "triage"


def _after_triage(state: ResponderState) -> str:
    if state.get("intent") == "suspicious":
        return "escalate"
    # Run the engine for anything substantive about the focal listing — a question
    # about price/condition/value deserves data just as much as an offer does.
    if (
        state.get("intent") in ("listing_question", "offer_negotiation")
        and state.get("focal_listing_id") is not None
        and state.get("stance") != "neutral"
    ):
        return "assess"
    return "decide"


def _after_gate(state: ResponderState) -> str:
    if state.get("gate_error"):
        return "escalate"
    action = state["decision"]["action"]
    if action == "escalate":
        return "escalate"
    if action == "no_reply":
        return "no_reply"
    return "draft"


def _after_validate(state: ResponderState) -> str:
    if state.get("gate_error"):
        return "escalate"
    if state.get("draft_feedback"):
        return "draft"  # one retry with the violation fed back
    return "commit"


# ---- Build / run ---------------------------------------------------------------
def build_responder_graph():
    g = StateGraph(ResponderState)
    g.add_node("screen", _screen)
    g.add_node("triage", _triage)
    g.add_node("assess", _assess)
    g.add_node("decide", _decide)
    g.add_node("gate", _gate)
    g.add_node("no_reply", _no_reply)
    g.add_node("draft", _draft)
    g.add_node("validate", _validate)
    g.add_node("escalate", _escalate)
    g.add_node("commit", _commit)

    g.add_edge(START, "screen")
    g.add_conditional_edges("screen", _after_screen, {"escalate": "escalate", "triage": "triage"})
    g.add_conditional_edges(
        "triage", _after_triage, {"escalate": "escalate", "assess": "assess", "decide": "decide"}
    )
    g.add_edge("assess", "decide")
    g.add_edge("decide", "gate")
    g.add_conditional_edges(
        "gate", _after_gate, {"escalate": "escalate", "no_reply": "no_reply", "draft": "draft"}
    )
    g.add_edge("draft", "validate")
    g.add_conditional_edges(
        "validate",
        _after_validate,
        {"escalate": "escalate", "draft": "draft", "commit": "commit"},
    )
    g.add_edge("commit", END)
    g.add_edge("no_reply", END)
    g.add_edge("escalate", END)
    # No checkpointer: Graph 2 is one-shot; durability = Inngest retries + message
    # idempotency, not the checkpoint (architecture §9b).
    return g.compile()


_graph = None


def _get_graph():
    global _graph
    if _graph is None:
        _graph = build_responder_graph()
    return _graph


async def run_responder(plan: dict) -> dict:
    """Run one away-responder turn from a `dal.responder_plan` dict. Returns the final
    state (carrying `outcome` + `commit_result`)."""
    initial: ResponderState = {
        "principal_id": plan["principal_id"],
        "counterparty_user_id": plan.get("counterparty_user_id"),
        "chat_id": plan["chat_id"],
        "inbound_message_id": plan["inbound_message_id"],
        "inbound": plan["inbound"],
        "stance": plan["stance"],
        "focal_listing_id": plan.get("focal_listing_id"),
        "focal_listing": plan.get("focal_listing", {}),
        "listings": plan.get("listings", []),
        "strategy": plan.get("strategy"),
        "autonomy": plan.get("autonomy", "draft_for_approval"),
        "agent_instructions": plan.get("agent_instructions", ""),
        "mandates": plan.get("mandates", []),
        "focal_mandate": plan.get("focal_mandate", {}),
        "private_limits": plan.get("private_limits", []),
        "missing_must_haves": plan.get("missing_must_haves", []),
        "memory": plan.get("memory", []),
        "transcript": plan.get("transcript", []),
        "display_name": plan.get("display_name"),
        "tool_results": {},
        "deal": plan.get("deal"),
        "negotiation": plan.get("negotiation"),
        "other_active_deals": plan.get("other_active_deals", 0),
        "share_lines": [],
        "draft_attempts": 0,
        "draft_feedback": None,
    }
    return await _get_graph().ainvoke(initial)
