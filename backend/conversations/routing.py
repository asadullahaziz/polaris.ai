"""Websocket URL routing (mounted by config.asgi under AuthMiddlewareStack)."""

from django.urls import path

from .consumers import SpikeConsumer
from .copilot import CopilotConsumer
from .thread_consumer import ThreadConsumer

websocket_urlpatterns = [
    # P0 spike socket (kept as the review-#8 anchor).
    path("ws/spike/", SpikeConsumer.as_asgi()),
    # P1 copilot socket (Graph 1: streamed chat with the user's own agent).
    path("ws/copilot/", CopilotConsumer.as_asgi()),
    # P3 shared-thread socket (presence + chat + auto-responder handback).
    path("ws/thread/<int:conversation_id>/", ThreadConsumer.as_asgi()),
]
