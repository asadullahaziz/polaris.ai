"""
Graph 1 — Copilot (architecture §4, implementation_plan P1.3).

A ReAct loop (`create_react_agent`) over the shared checkpointer with the
principal-bound copilot tool subset and the composed copilot system prompt. The
agent is built once per socket (bound to one user); each turn runs on a fresh
per-turn `thread_id`, and the transcript is rehydrated from the `message` table —
so the checkpoint is pure within-turn scratch (architecture §9b).
"""

from __future__ import annotations

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.prebuilt import create_react_agent

from polaris_agent.models import get_model
from polaris_agent.prompts import copilot_system_prompt
from polaris_agent.tools.registry import tools_for


def build_copilot_agent(
    checkpointer: BaseCheckpointSaver, *, principal_id: int, display_name: str | None = None
):
    return create_react_agent(
        model=get_model("workhorse"),
        tools=tools_for("copilot", principal_id),
        prompt=copilot_system_prompt(display_name=display_name),
        checkpointer=checkpointer,
    )
