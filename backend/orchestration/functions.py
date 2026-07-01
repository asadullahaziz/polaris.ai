"""
Inngest functions aggregated for the serve mount in config.urls.

P0: a single `spike_ping` that closes the loop event -> function -> channel
layer -> socket, proving Inngest round-trips back to the browser (review #8).
Each domain app adds its own functions.py in later phases; this list aggregates
them (P2 outreach = fan-out; P3 conversations = auto-responder).
"""

from __future__ import annotations

import logging

import inngest
from channels.layers import get_channel_layer

from outreach.functions import functions as outreach_functions

from .client import inngest_client

log = logging.getLogger(__name__)


@inngest_client.create_function(
    fn_id="spike-ping",
    trigger=inngest.TriggerEvent(event="spike/ping.requested"),
    retries=1,
)
async def spike_ping(ctx: inngest.Context) -> dict:
    data = ctx.event.data
    group = data.get("group") or f"spike_{data.get('user_id')}"
    ping = data.get("ping", "")

    # A durable step (proves step execution); the tick text is templated, no LLM.
    message = await ctx.step.run("compose-tick", lambda: f"inngest saw ping:{ping}")

    layer = get_channel_layer()
    await layer.group_send(
        group,
        {"type": "spike.tick", "data": {"message": message, "source": "inngest"}},
    )
    return {"delivered": True, "group": group}


# Registered with inngest.django.serve(...) in config.urls.
functions = [spike_ping, *outreach_functions]
