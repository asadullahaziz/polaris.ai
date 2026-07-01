"""
SpikeConsumer (P0.7) — the whole review-#8 integration in one WS turn.

On each inbound ping it exercises, in one authenticated socket handler:
  (a) a 1-node async LangGraph compiled against the SHARED AsyncPostgresSaver
      (state persists — `count` increments across pings on the same thread_id);
  (b) a GeoDjango `ST_DWithin` query via the DAL (`sync_to_async`);
  (c) echoes the round-trip back to the browser;
  (d) emits an Inngest event whose function writes back over the channel layer.

Identity comes from Channels' built-in AuthMiddlewareStack (session cookie ->
scope["user"]); anonymous sockets are rejected in connect().
"""

from __future__ import annotations

import json
import logging

import inngest
from channels.generic.websocket import AsyncWebsocketConsumer

from orchestration.client import inngest_client
from polaris_agent.checkpointer import get_checkpointer
from polaris_agent.dal import count_points_within_km
from polaris_agent.graphs.spike import build_spike_graph

log = logging.getLogger(__name__)

# Downtown Seattle — centre of the spike geo query.
_SEATTLE_LON, _SEATTLE_LAT = -122.3351, 47.6080
_GEO_RADIUS_KM = 50.0


class SpikeConsumer(AsyncWebsocketConsumer):
    async def connect(self):
        user = self.scope.get("user")
        if user is None or not user.is_authenticated:
            await self.close(code=4401)  # unauthenticated
            return
        self.group_name = f"spike_{user.id}"
        await self.channel_layer.group_add(self.group_name, self.channel_name)
        await self.accept()
        await self.send(
            text_data=json.dumps(
                {"type": "spike.ready", "data": {"user": user.get_username()}}
            )
        )

    async def disconnect(self, code):
        group = getattr(self, "group_name", None)
        if group is not None:
            await self.channel_layer.group_discard(group, self.channel_name)

    async def receive(self, text_data=None, bytes_data=None):
        try:
            payload = json.loads(text_data or "{}")
        except json.JSONDecodeError:
            payload = {}
        ping = (payload.get("data") or {}).get("ping", "ping")
        user = self.scope["user"]

        # (a) async LangGraph over the shared checkpointer — state persists.
        checkpointer = await get_checkpointer()
        graph = build_spike_graph(checkpointer)
        thread_id = f"spike-{user.id}"
        state = await graph.ainvoke(
            {"ping": ping},
            config={"configurable": {"thread_id": thread_id}},
        )

        # (b) GeoDjango ST_DWithin via the DAL.
        geo_within = await count_points_within_km(
            _SEATTLE_LON, _SEATTLE_LAT, _GEO_RADIUS_KM
        )

        # (c) echo the round-trip.
        await self.send(
            text_data=json.dumps(
                {
                    "type": "spike.echo",
                    "data": {
                        "echo": state.get("echo"),
                        "count": state.get("count"),  # persistence evidence
                        "thread_id": thread_id,
                        "geo_within_50km": geo_within,
                    },
                }
            )
        )

        # (d) emit an Inngest event; spike_ping writes back over the channel layer.
        try:
            await inngest_client.send(
                inngest.Event(
                    name="spike/ping.requested",
                    data={"user_id": user.id, "group": self.group_name, "ping": ping},
                )
            )
        except Exception as exc:  # pragma: no cover - dev server may still be booting
            log.warning("inngest send failed: %s", exc)
            await self.send(
                text_data=json.dumps(
                    {"type": "inngest.error", "data": {"message": str(exc)}}
                )
            )

    async def spike_tick(self, event):
        """Channel-layer handler: Inngest spike_ping -> group_send(type='spike.tick')."""
        await self.send(
            text_data=json.dumps({"type": "inngest.tick", "data": event.get("data", {})})
        )
