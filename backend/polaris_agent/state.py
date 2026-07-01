"""
Graph state schemas (TypedDicts). P0 has none of the real ones — the spike graph
carries its own `SpikeState` in graphs/spike.py.

P1+ moves the architecture §8 state schemas here (copilot state; responder state
with the PUBLIC/PRIVATE split + the closed `DisclosedFields` whitelist; outreach
batch state). Placeholder kept so the module path is stable.
"""

from __future__ import annotations
