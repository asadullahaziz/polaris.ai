"""
Per-graph tool registry: each graph gets only the tools it is allowed to call.
Only the copilot exposes tools to the LLM; the responder and outreach graphs
call the engine deterministically.
"""

from __future__ import annotations

from polaris_agent.tools.copilot import copilot_tools


def tools_for(graph: str, principal_id: int) -> list:
    """Return the tool set for `graph`, bound to `principal_id` where relevant."""
    if graph == "copilot":
        return copilot_tools(principal_id)
    # No other graph exposes tools to the LLM.
    return []
