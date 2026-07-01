"""
Per-graph tool registry (architecture §7). Each graph gets only the tools it is
allowed to call. P0 is a stub; P1+ registers real tools here.
"""

from __future__ import annotations

# graph name -> list of tool callables. Populated in P1+.
TOOLS_BY_GRAPH: dict[str, list] = {
    "copilot": [],
    "responder": [],
    "outreach": [],
}


def tools_for(graph: str) -> list:
    return TOOLS_BY_GRAPH.get(graph, [])
