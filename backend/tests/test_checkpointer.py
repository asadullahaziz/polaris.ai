"""
Checkpointer persistence smoke — the one leg of the deleted P0 spike worth keeping.

Proves the shared process-wide `AsyncPostgresSaver` (the riskiest seam: a Postgres
pool separate from Django's ORM connection) compiles against a real LangGraph graph
and persists state across invocations on the same `thread_id`. No LLM, no spike app —
a trivial inline counter graph stands in for the old spike graph.
"""

from __future__ import annotations

from typing import TypedDict

import pytest
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph

from polaris_agent.checkpointer import close_checkpointer, get_checkpointer


class _State(TypedDict, total=False):
    ping: str
    echo: str
    count: int


async def _echo(state: _State) -> _State:
    count = int(state.get("count", 0)) + 1
    return {"echo": f"pong:{state.get('ping', '')}", "count": count}


def _build_graph(checkpointer: BaseCheckpointSaver):
    builder = StateGraph(_State)
    builder.add_node("echo", _echo)
    builder.add_edge(START, "echo")
    builder.add_edge("echo", END)
    return builder.compile(checkpointer=checkpointer)


@pytest.mark.django_db(transaction=True)
async def test_checkpointer_persists(reset_checkpointer):
    checkpointer = await get_checkpointer()
    graph = _build_graph(checkpointer)
    cfg = {"configurable": {"thread_id": "pytest-thread-1"}}

    s1 = await graph.ainvoke({"ping": "a"}, config=cfg)
    assert s1["count"] == 1
    assert s1["echo"] == "pong:a"

    # Second invoke on the SAME thread must resume from the persisted checkpoint.
    s2 = await graph.ainvoke({"ping": "b"}, config=cfg)
    assert s2["count"] == 2

    # A checkpoint row exists for this thread (persistence, not just in-memory).
    tup = await checkpointer.aget_tuple(cfg)
    assert tup is not None

    await close_checkpointer()
