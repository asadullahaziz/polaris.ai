"""
The trivial 1-node graph used by the P0 spike (P0.15).

No LLM — its only job is to prove the graph compiles against the *shared*
`AsyncPostgresSaver` and that state persists across invocations on the same
`thread_id`. Because each ping passes only `{"ping": ...}`, `count` is restored
from the checkpoint and incremented, so an incrementing `count` across pings is
direct evidence the checkpointer is persisting state (the review #8 gate).
"""

from __future__ import annotations

from typing import TypedDict

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph


class SpikeState(TypedDict, total=False):
    ping: str
    echo: str
    count: int


async def _echo(state: SpikeState) -> SpikeState:
    count = int(state.get("count", 0)) + 1
    return {"echo": f"pong:{state.get('ping', '')}", "count": count}


def build_spike_graph(checkpointer: BaseCheckpointSaver):
    builder = StateGraph(SpikeState)
    builder.add_node("echo", _echo)
    builder.add_edge(START, "echo")
    builder.add_edge("echo", END)
    return builder.compile(checkpointer=checkpointer)
