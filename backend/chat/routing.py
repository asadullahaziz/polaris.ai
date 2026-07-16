"""Chat websocket routes — the human 1:1 socket (presence + chat + agent handback).
Aggregated by config.routing."""

from __future__ import annotations

from django.urls import path

from .consumers import ChatConsumer

websocket_urlpatterns = [
    path("ws/chat/<int:chat_id>/", ChatConsumer.as_asgi()),
]
