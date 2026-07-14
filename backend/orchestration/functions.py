"""
Inngest functions aggregated for the serve mount in config.urls.

  * chat.functions — the away-responder (`thread_inbound`, trigger `chat/inbound`)
  * ai.functions   — outreach fan-out (`outreach_fanout`, trigger `outreach/approved`)
"""

from __future__ import annotations

from ai.functions import functions as ai_functions
from chat.functions import functions as chat_functions

# Registered with inngest.django.serve(...) in config.urls.
functions: list = [*chat_functions, *ai_functions]
