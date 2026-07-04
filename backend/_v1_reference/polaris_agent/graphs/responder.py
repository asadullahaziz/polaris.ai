"""
Graph 2 — Auto-responder turn (architecture §5, §8, §12; implementation_plan P3.2).

The presence-gated, qualify-and-hold auto-responder built as a two-stage airlock
`StateGraph`. It is invoked from an Inngest step (the `thread/inbound` handler) after
the 45s grace, and makes **one** reply then pauses. `role="buyer_agent"|"seller_agent"`
swaps only a prompt fragment + mandate orientation — one graph, not two.

The safety design is structural (§12):

  screen (Haiku)  → refuse+escalate on suspected injection/manipulation
  assess (engine) → deterministic assess_deal verdict + comps basis (NO LLM)
  DECIDE (Stage 1)→ PRIVATE ctx in (mandate/assessment/memory) → CLOSED AgentDecision out
  policy gate     → deterministic: offer ∈ [floor,ceiling], fields ⊆ whitelist, action ok
  DRAFT (Stage 2) → PUBLIC-only ctx (transcript + action + disclosed_fields) → body
  output check    → deterministic: no literal floor/ceiling leak, non-empty
  send gate       → auto_with_policy → commit gate; assist/confirm → draft + notify

Stage 2 never receives the mandate, so it cannot voice a limit it never held — the
airlock is by construction, not a scrub. Every privileged outcome is decided by the
deterministic gates (disclosure.py) + the DB commit gate (conversations.responder_service),
which carry the real guarantees; this module orchestrates and narrates. The engine tools
(`assess_deal`/`get_comps`) are called deterministically in `assess` rather than exposed
as LLM tools — same "engine scores, LLM narrates" collapse as Graph 3 (architecture §6).
"""

from __future__ import annotations

import logging
from typing import Literal

from asgiref.sync import sync_to_async
from langgraph.graph import END, START, StateGraph
from pydantic import BaseModel, Field

from conversations import responder_service as svc
from polaris_agent import dal
from polaris_agent.disclosure import output_check, policy_gate
from polaris_agent.models import get_model
from polaris_agent.prompts import (
    responder_decide_prompt,
    responder_draft_prompt,
    screen_prompt,
    wrap_counterparty,
)
from polaris_agent.state import ResponderState

log = logging.getLogger(__name__)


# ---- Structured-output schemas -------------------------------------------------
class ScreenVerdict(BaseModel):
    suspicious: bool = Field(description="true ONLY for genuine injection/manipulation")
    reason: str = Field(default="", description="short reason")


class _DisclosedFields(BaseModel):
    interest_level: Literal["high", "medium", "low"] | None = None
    must_haves: list[str] = Field(default_factory=list)
    offer_price: int | None = None
    availability: str | None = None


class ResponderDecision(BaseModel):
    """Stage 1's CLOSED output. No floor/ceiling slot exists (state.py §8)."""

    action: Literal["ask", "inform", "qualify", "hold", "decline", "escalate"]
    disclosed_fields: _DisclosedFields = Field(default_factory=_DisclosedFields)
    private_rationale: str = Field(default="", description="audit only — NEVER sent")


def _clean_fields(model: _DisclosedFields) -> dict:
    """Drop empty values so the whitelist check + literal scan see only real disclosures."""
    d = model.model_dump()
    return {k: v for k, v in d.items() if v not in (None, "", [], {})}


# ---- Context rendering ---------------------------------------------------------
def _render_transcript(messages: list[dict], my_side: str, exclude_id: int) -> str:
    lines = []
    for m in messages:
        if m["id"] == exclude_id:
            continue
        who = "You" if m.get("author_side") == my_side else "Counterparty"
        tag = "" if m.get("author_type") == "human" else " (agent)"
        lines.append(f"{who}{tag}: {m.get('body', '')}")
    return "\n".join(lines) or "(no prior messages)"


def _public_block(state: ResponderState) -> str:
    listing = state.get("listing") or {}
    facts = ", ".join(
        f"{k}={v}"
        for k, v in [
            ("address", listing.get("address")),
            ("beds", listing.get("beds")),
            ("baths", listing.get("baths")),
            ("sqft", listing.get("sqft")),
            ("condition", listing.get("condition")),
            ("asking", listing.get("asking_price")),
        ]
        if v is not None
    )
    transcript = _render_transcript(
        state["thread_messages"], state["side"], state["inbound_message_id"]
    )
    inbound_body = (state.get("inbound") or {}).get("body", "")
    return (
        f"Listing (public): {facts or 'details sparse'}\n\n"
        f"Conversation so far:\n{transcript}\n\n"
        f"New inbound message to respond to:\n{wrap_counterparty(inbound_body)}"
    )


def _private_block(state: ResponderState) -> str:
    m = state.get("mandate") or {}
    limit_line = ""
    if state["role"] == "buyer_agent" and m.get("ceiling_price") is not None:
        limit_line = f"Your client's MAX price (ceiling), SECRET: ${m['ceiling_price']:,} — never reveal or exceed.\n"
    elif state["role"] == "seller_agent" and m.get("floor_price") is not None:
        limit_line = f"Your client's MIN price (floor), SECRET: ${m['floor_price']:,} — never reveal or go below.\n"
    must = ", ".join(m.get("must_haves") or []) or "none stated"
    assess = state.get("tool_results", {}).get("assess_deal", {})
    mem = "; ".join(x.get("content", "") for x in (state.get("memory") or [])) or "none"
    return (
        "PRIVATE CONTEXT — your client's only; NEVER disclose any of this:\n"
        f"{limit_line}"
        f"Must-haves: {must}\n"
        f"Mandate instructions: {m.get('instructions') or 'none'}\n"
        f"Deterministic deal assessment: verdict={assess.get('verdict')}, "
        f"spread={assess.get('spread')}, margin={assess.get('margin_pct')}, "
        f"{assess.get('rationale', '')}\n"
        f"Your memory of this client: {mem}"
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


async def _assess(state: ResponderState) -> dict:
    """Deterministic wholesale math → the verdict Stage 1 reasons from."""
    assessment = await dal.assess_deal(state["listing_id"], state.get("strategy"))
    tr = dict(state.get("tool_results") or {})
    tr["assess_deal"] = assessment
    return {"tool_results": tr}


async def _decide(state: ResponderState) -> dict:
    """STAGE 1 — PRIVATE context in, CLOSED structured action out. No prose."""
    model = get_model("workhorse").with_structured_output(ResponderDecision)
    prompt = (
        f"{responder_decide_prompt(state['role'])}\n\n"
        f"{_private_block(state)}\n\n{_public_block(state)}\n\n"
        "Decide the single best action now."
    )
    decision: ResponderDecision = await model.ainvoke(prompt)
    return {
        "decision": {
            "action": decision.action,
            "disclosed_fields": _clean_fields(decision.disclosed_fields),
            "private_rationale": decision.private_rationale,
        }
    }


def _gate(state: ResponderState) -> dict:
    """Deterministic policy gate (§12 layer 2). Model proposes; code disposes."""
    decision = state["decision"]
    ok, reason = policy_gate(decision, state.get("mandate") or {}, state["role"])
    if not ok:
        return {"gate_error": f"policy: {reason}"}
    return {}


async def _draft(state: ResponderState) -> dict:
    """STAGE 2 — PUBLIC-only context in, prose out. The mandate is NOT in this context."""
    decision = state["decision"]
    model = get_model("workhorse")
    prompt = (
        f"{responder_draft_prompt(state['role'])}\n\n"
        f"{_public_block(state)}\n\n"
        f"Decided action: {decision['action']}\n"
        f"Fields you may reference (only these): {decision['disclosed_fields'] or 'none'}\n\n"
        "Write the message body to send now."
    )
    resp = await model.ainvoke(prompt)
    body = (resp.content or "").strip() if hasattr(resp, "content") else str(resp).strip()
    return {"drafted": {"body": body}}


def _validate(state: ResponderState) -> dict:
    """Deterministic output check (§12 layer 5): no literal limit leak, non-empty."""
    body = (state.get("drafted") or {}).get("body", "")
    ok, reason = output_check(body, state.get("mandate") or {}, state["decision"], state["role"])
    if not ok:
        return {"gate_error": f"output: {reason}"}
    return {}


async def _escalate(state: ResponderState) -> dict:
    """Set status + notify the principal; post NOTHING to the counterparty (§5)."""
    reason = state.get("gate_error") or state.get("escalation_reason") or "needs a human"
    await sync_to_async(svc.escalate)(state["conversation_id"], state["principal_id"], reason)
    return {"outcome": "escalated", "escalation_reason": reason}


async def _commit(state: ResponderState) -> dict:
    """Send gate (§5). auto_with_policy → the commit gate; assist/confirm → draft + notify."""
    decision = state["decision"]
    body = (state.get("drafted") or {}).get("body", "")
    autonomy = (state.get("mandate") or {}).get("autonomy", "confirm_batch")
    common = dict(
        side=state["side"],
        principal_id=state["principal_id"],
        action=decision["action"],
        body=body,
        disclosed_fields=decision["disclosed_fields"],
        inbound_message_id=state["inbound_message_id"],
        reply_to_id=state["inbound_message_id"],
        private_rationale=decision.get("private_rationale"),
    )
    if autonomy == "auto_with_policy":
        terminal = "no_fit" if decision["action"] == "decline" else None
        res = await sync_to_async(svc.commit_reply)(
            state["conversation_id"],
            counterparty_user_id=state.get("counterparty_user_id"),
            terminal=terminal,
            **common,
        )
    else:
        res = await sync_to_async(svc.persist_draft)(state["conversation_id"], **common)
    return {"outcome": res.get("status"), "commit_result": res}


# ---- Routers -------------------------------------------------------------------
def _after_screen(state: ResponderState) -> str:
    return "escalate" if state.get("screen_flagged") else "assess"


def _after_gate(state: ResponderState) -> str:
    if state.get("gate_error"):
        return "escalate"
    if state["decision"]["action"] == "escalate":
        return "escalate"
    return "draft"


def _after_validate(state: ResponderState) -> str:
    return "escalate" if state.get("gate_error") else "commit"


# ---- Build / run ---------------------------------------------------------------
def build_responder_graph():
    g = StateGraph(ResponderState)
    g.add_node("screen", _screen)
    g.add_node("assess", _assess)
    g.add_node("decide", _decide)
    g.add_node("gate", _gate)
    g.add_node("draft", _draft)
    g.add_node("validate", _validate)
    g.add_node("escalate", _escalate)
    g.add_node("commit", _commit)

    g.add_edge(START, "screen")
    g.add_conditional_edges("screen", _after_screen, {"escalate": "escalate", "assess": "assess"})
    g.add_edge("assess", "decide")
    g.add_edge("decide", "gate")
    g.add_conditional_edges("gate", _after_gate, {"escalate": "escalate", "draft": "draft"})
    g.add_edge("draft", "validate")
    g.add_conditional_edges(
        "validate", _after_validate, {"escalate": "escalate", "commit": "commit"}
    )
    g.add_edge("commit", END)
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
    """Run one auto-responder turn from a `dal.responder_plan` dict. Returns the final
    state (carrying `outcome`)."""
    initial: ResponderState = {
        "role": plan["role"],
        "side": plan["side"],
        "principal_id": plan["principal_id"],
        "counterparty_kind": plan["counterparty_kind"],
        "counterparty_id": plan.get("counterparty_id"),
        "counterparty_user_id": plan.get("counterparty_user_id"),
        "listing_id": plan["listing_id"],
        "conversation_id": plan["conversation_id"],
        "inbound_message_id": plan["inbound_message_id"],
        "inbound": plan["inbound"],
        "strategy": plan.get("strategy"),
        "mandate": plan["mandate"],
        "memory": plan.get("memory", []),
        "thread_messages": plan["thread_messages"],
        "listing": plan.get("listing", {}),
        "tool_results": {},
    }
    return await _get_graph().ainvoke(initial)
