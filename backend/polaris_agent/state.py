"""
State schema for the away-assistant responder graph.

The load-bearing property is the PUBLIC vs PRIVATE split. Stage 1 (`decide`)
reads private + public context and emits a closed decision; Stage 2 (`draft`)
receives only the public fields (`transcript` / `inbound` / `focal_listing`)
plus that decision. `mandates` / `focal_mandate` / `private_limits` / `memory` /
`tool_results` / `private_rationale` never cross the disclosure boundary, so no
limit is reachable to the drafting model by construction — and because
`private_limits` is the union of every limit the principal holds, "never leak a
limit" holds for any stance.

There is no fixed buyer/seller role and no single bound listing:
  * `stance` — derived deterministically from ownership of the focal listing
    (owned by principal → sell_side; not owned but a buy-box match → buy_side;
    none → neutral). It only selects which assessment runs and which mandate
    Stage 1 reasons from; it is not a stored role.
  * `principal_id` — the other `ChatMember` (the human the agent covers for);
    the sender-based reply cap resets only on this user's own human message.
  * `focal_listing_id` — the most-recently referenced listing attachment (chats
    are free-form and accrue many listings over time); `listings` carries them all.
"""

from __future__ import annotations

from typing import Literal, TypedDict

# propose = a concrete offer/counter within mandate; accept = take the counterparty's
# standing offer (always routed to the human to sign); no_reply = end the turn silently
# (contentless inbound).
Action = Literal[
    "ask", "inform", "qualify", "hold", "decline", "escalate", "propose", "accept", "no_reply"
]
# User-level autonomy (UserProfile.agent_autonomy).
Autonomy = Literal["draft_for_approval", "auto_send"]
Terminal = Literal["matched", "no_fit", "needs_decision"]
# Ownership-derived orientation, not a stored role. neutral = no focal listing.
Stance = Literal["sell_side", "buy_side", "neutral"]
# Triage intent (the only LLM step in the generalized front half).
Intent = Literal[
    "greeting_smalltalk", "listing_question", "offer_negotiation", "off_topic", "suspicious"
]


class Mandate(TypedDict, total=False):
    """One deal context's private parameters (a listing floor or a buy-box ceiling).
    Never crosses to Stage 2. Governance knobs (autonomy/auto_reply) live on the
    principal's `UserProfile`, not here."""

    floor_price: int | None  # seller floor (owned-listing mandate)
    ceiling_price: int | None  # buyer ceiling (buy-box mandate)
    must_haves: list[str]
    availability_window: str | None
    instructions: str  # free-text the LLM reads


# The only thing that crosses the airlock. Closed whitelist: no floor/ceiling/memory
# slot exists, so a structured-output model cannot emit a limit even if injected.
# `offer_price` is a concrete offer the agent chose to make (disclosable) — not a limit.
class DisclosedFields(TypedDict, total=False):
    must_haves: list[str]
    offer_price: int
    availability: str
    # No floor_price / ceiling_price / memory — nowhere to put a secret.


# Stage 1 (decide) — private context in, structured action out. Writes no prose.
# share_comps / share_valuation are boolean disclosure requests: when gate-approved
# (the data actually exists in tool_results), the graph renders deterministic engine
# strings into Stage 2's public context — no free numeric slot a fooled model could
# stuff a secret into.
class AgentDecision(TypedDict):
    action: Action
    disclosed_fields: DisclosedFields
    share_comps: bool
    share_valuation: bool
    private_rationale: str  # logged to agent_action_log; NEVER forwarded or posted
    escalation_note: str  # escalate only: owner-facing "what they need" → notification


# Stage 2 (draft) — public-only context in, prose out.
class AgentMessage(TypedDict):
    body: str  # NL into the chat; the mandate was never in this context


class ResponderState(TypedDict, total=False):
    # --- identity / routing (all principal-centric; no role/side) ---------------
    principal_id: int  # the away human the agent covers for (the OTHER ChatMember)
    counterparty_user_id: int | None  # the human who sent the inbound
    counterparty_name: str | None  # their display name → the escalation headline
    chat_id: int
    inbound_message_id: int
    stance: Stance  # ownership-derived; selects assessment + focal mandate
    focal_listing_id: int | None  # most-recent referenced listing (None → neutral)
    strategy: str | None  # buyer's dominant strategy → assess_deal threshold
    autonomy: Autonomy  # UserProfile.agent_autonomy → send vs. draft-for-approval
    display_name: str | None  # the principal's name, for the Stage-2 self-introduction

    # --- PUBLIC — crosses the disclosure boundary -------------------------------
    transcript: list[dict]  # sent messages: {id, kind, sender, body, is_principal}
    inbound: dict | None  # the message being answered
    listings: list[dict]  # all attached listings' public facts + owned_by_principal
    focal_listing: dict  # the focal listing's public facts (address/beds/sqft/…)

    # --- PRIVATE — never leaves this agent (never handed to Stage 2) ------------
    mandates: list[dict]  # every in-play mandate the principal holds
    focal_mandate: dict  # the one Stage 1 reasons from for this decision
    private_limits: list[int]  # union of every floor/ceiling value → output-check scan
    missing_must_haves: list[str]  # unaddressed focal must-haves Stage 1 may ask about
    memory: list[dict]
    agent_instructions: str  # UserProfile.agent_instructions (global guidance)
    tool_results: dict  # assessment / valuation / comps (incl. the private max_offer)
    deal: dict | None  # focal deal (mini CRM): {id, listing_id, stage, agreed_price}
    negotiation: dict | None  # {my_last_offer, their_last_offer} — agent-disclosed only
    other_active_deals: int  # live deals on the focal listing → honest urgency

    # --- working / control ------------------------------------------------------
    intent: Intent | None  # triage classification (routes the conditional assess)
    decision: AgentDecision | None  # Stage 1 (private) — action, no prose
    drafted: AgentMessage | None  # Stage 2 (public-only ctx) — the rendered body
    share_lines: list[str]  # gate-approved, engine-rendered figures (public by construction)
    draft_attempts: int  # style/output-check retry budget (one retry, then escalate)
    draft_feedback: str | None  # the violation fed back into the retry draft
    screen_flagged: bool  # Haiku injection screen tripped → escalate
    gate_error: str | None  # why a deterministic gate rejected (→ escalate)
    outcome: str | None  # sent | draft | no_reply | escalated | stood_down_* | duplicate
    commit_result: dict  # raw return of the commit gate / draft persist
    escalation_reason: str | None
