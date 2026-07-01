"""Websocket URL routing (mounted by config.asgi under AuthMiddlewareStack)."""

from django.urls import path

from .consumers import SpikeConsumer
from .copilot import CopilotConsumer

websocket_urlpatterns = [
    # P0 spike socket (kept as the review-#8 anchor).
    path("ws/spike/", SpikeConsumer.as_asgi()),
    # P1 copilot socket (Graph 1: streamed chat with the user's own agent).
    path("ws/copilot/", CopilotConsumer.as_asgi()),
]
