"""
Websocket URL routing aggregator (mounted by config.asgi under AuthMiddlewareStack).

Each phase appends its consumers here:
  * P2 — ai:   ws/copilot/            (Graph 1 streamed copilot chat)
  * P3 — chat: ws/chat/<chat_id>/     (presence + human 1:1 + agent handback)
"""

from __future__ import annotations

from ai.routing import websocket_urlpatterns as ai_ws

websocket_urlpatterns: list = [
    *ai_ws,
]
