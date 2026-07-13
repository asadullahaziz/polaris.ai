"""
Graph 1 — Copilot (architecture §4; revisions §polaris-ai).

A ReAct loop (`create_react_agent`) over the shared Postgres checkpointer with the
principal-bound copilot tool subset and the composed, domain-aware system prompt. The
agent is built once per socket (bound to one user); each turn runs on a fresh per-turn
`thread_id` and the transcript is rehydrated from `ai_message` — so the checkpoint is
pure within-turn scratch (architecture §9b), EXCEPT across a confirm-every-write
interrupt: the write tool raises `interrupt()`, the graph pauses on that turn's
`thread_id`, and the consumer resumes it with the user's decision (`Command(resume=…)`).
The checkpointer is what makes that pause/resume durable within the turn.
"""

from __future__ import annotations

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.prebuilt import create_react_agent

from polaris_agent.models import get_model
from polaris_agent.tools.registry import tools_for


def build_copilot_agent(
    checkpointer: BaseCheckpointSaver,
    *,
    principal_id: int,
    system_prompt: str,
):
    """`system_prompt` arrives pre-composed (prompt_store.compose_copilot_system —
    Langfuse-fetched with the code constants as fallback) so the graph layer stays
    free of prompt fetching; the tools stay principal-bound closures (the security
    seam — a tool call can never touch another user's data)."""
    return create_react_agent(
        model=get_model("workhorse"),
        tools=tools_for("copilot", principal_id),
        prompt=system_prompt,
        checkpointer=checkpointer,
    )
