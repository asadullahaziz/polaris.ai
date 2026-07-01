"""
Graph state schemas (architecture §8).

The one with real structure is Graph 2 (the auto-responder). Its load-bearing
property is the **PUBLIC vs PRIVATE split**: the counterparty's turn only ever
receives `thread_messages` / `inbound`; the `mandate` / `memory` / `tool_results`
/ `private_rationale` never serialize across the disclosure boundary. The same
split runs *inside* one turn — Stage 1 (decide) reads PRIVATE+PUBLIC and emits a
**closed** `AgentDecision`; Stage 2 (draft) reads PUBLIC + that decision only and
produces the `body`. So the limits are unreachable to the drafting model **by
construction** (§12).

Copilot and outreach states stay simple (message list + working scratch), so they
don't need a schema here — only Graph 2 does.
"""

from __future__ import annotations

from typing import Literal, TypedDict

# v1 = qualify-and-hold. propose/counter/accept are STRETCH (price negotiation, §D #20).
Action = Literal["ask", "inform", "qualify", "hold", "decline", "escalate"]
Autonomy = Literal["assist", "confirm_batch", "auto_with_policy"]
Terminal = Literal["matched", "no_fit", "needs_decision"]
Role = Literal["buyer_agent", "seller_agent"]


class Mandate(TypedDict, total=False):
    """The agent's PRIVATE playbook for one deal context. Never crosses to Stage 2."""

    floor_price: int | None  # seller floor
    ceiling_price: int | None  # buyer ceiling
    must_haves: list[str]
    availability_window: str | None
    autonomy: Autonomy
    auto_reply: bool
    instructions: str  # free-text the LLM reads (features §B #6)


# The ONLY thing that crosses the airlock. CLOSED whitelist: floor/ceiling/memory are
# absent BY CONSTRUCTION, so a structured-output model cannot emit them even if a
# prompt-injection asks it to. `offer_price` is a concrete offer the agent CHOSE to
# make (disclosable) — never a limit.
class DisclosedFields(TypedDict, total=False):
    interest_level: Literal["high", "medium", "low"]
    must_haves: list[str]
    offer_price: int
    availability: str
    # NO floor_price / ceiling_price / memory — nowhere to put a secret.


# STAGE 1 (decide) — PRIVATE context in, structured action out. Writes NO prose.
class AgentDecision(TypedDict):
    action: Action
    disclosed_fields: DisclosedFields
    private_rationale: str  # logged to agent_action_log; NEVER forwarded or posted


# STAGE 2 (draft) — PUBLIC-only context in, prose out.
class AgentMessage(TypedDict):
    body: str  # NL into the thread; the mandate was NOT in this context


class ResponderState(TypedDict, total=False):
    role: Role
    side: Literal["buyer", "seller"]  # author_side the agent writes as
    principal_id: int  # the agent's own user (buyer or seller side)
    counterparty_kind: Literal["user", "prospect"]
    counterparty_id: int | None
    counterparty_user_id: int | None  # the counterparty's user id (None for prospects)
    listing_id: int
    conversation_id: int  # = thread id
    inbound_message_id: int
    strategy: str | None  # buyer's dominant strategy → assess_deal threshold

    # PUBLIC — crosses the disclosure boundary
    thread_messages: list[dict]
    inbound: dict | None
    listing: dict  # public listing facts (address/beds/sqft/condition/asking)

    # PRIVATE — never leaves this agent (never handed to Stage 2)
    mandate: Mandate
    memory: list[dict]
    tool_results: dict  # deal assessment, comps, etc.

    # working / control
    decision: AgentDecision | None  # STAGE 1 (private) — action, NO prose
    drafted: AgentMessage | None  # STAGE 2 (public-only ctx) — the rendered body
    dedup_key: str  # autoreply:{conversation_id}:{inbound_id} — idempotent emit
    screen_flagged: bool  # Haiku injection screen tripped → escalate
    gate_error: str | None  # why the deterministic gate rejected (→ escalate)
    outcome: str | None  # sent | draft | escalated | stood_down_present | stood_down_cap
    commit_result: dict  # raw return of the commit gate / draft persist
    terminal: Terminal | None
    escalation_reason: str | None
