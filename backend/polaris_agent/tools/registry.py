"""
Per-graph tool registry (architecture §7). Each graph gets only the tools it is
allowed to call. P1 wires the copilot subset (principal-bound); the responder and
outreach subsets land in P2/P3.
"""

from __future__ import annotations

from polaris_agent.tools.copilot import copilot_tools


def tools_for(graph: str, principal_id: int) -> list:
    """Return the tool set for `graph`, bound to `principal_id` where relevant."""
    if graph == "copilot":
        return copilot_tools(principal_id)
    # responder / outreach subsets are gated in later phases.
    return []
