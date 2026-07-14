"""Ai websocket routes — the streamed copilot chat. Aggregated by config.routing."""

from __future__ import annotations

from django.urls import path

from .consumers import CopilotConsumer

websocket_urlpatterns = [
    path("ws/copilot/", CopilotConsumer.as_asgi()),
]
