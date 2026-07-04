"""
Inngest functions aggregated for the serve mount in config.urls.

Later phases append their own; this list aggregates them so the serve mount stays stable:
  * P4 — chat.functions:  the away-responder (`thread_inbound`, trigger `chat/inbound`)
  * P5 — ai.functions:    outreach fan-out (not built yet)
"""

from __future__ import annotations

from chat.functions import functions as chat_functions

# Registered with inngest.django.serve(...) in config.urls.
functions: list = [*chat_functions]
