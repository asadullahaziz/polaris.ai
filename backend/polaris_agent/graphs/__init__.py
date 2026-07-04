"""
LangGraph graphs (architecture §4–5). P2 = Graph 1 (copilot); Graph 2 (responder) → P4.

P2 deferral (see the "P2 as-built deferral ledger" in
`.claude/plans/polaris_ai_v2_revisions_2026-07-03.md`): `polaris_agent/state.py` (the
responder-state schema) is NOT ported here — it is Graph-2-only and the responder graph is
redesigned in P4 (stance-not-role, conditional assess), so it lands with that build.
`disclosure.py` (pure gates) WAS ported + unit-tested in P2.
"""

from __future__ import annotations
