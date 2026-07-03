"""
Inngest functions aggregated for the serve mount in config.urls.

P0: empty registry — no durable functions yet. Later phases append their own:
  * P5 — ai.functions:   outreach fan-out
  * P4 — chat.functions: the away-responder (thread_inbound)
This list aggregates them; the serve mount stays stable across phases.
"""

from __future__ import annotations

# Registered with inngest.django.serve(...) in config.urls.
functions: list = []
