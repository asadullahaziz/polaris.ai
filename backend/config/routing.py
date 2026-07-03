"""
Websocket URL routing aggregator (mounted by config.asgi under AuthMiddlewareStack).

P0 has no sockets. Later phases append their consumers:
  * P2 — ai:   ws/copilot/            (Graph 1 streamed copilot chat)
  * P3 — chat: ws/chat/<chat_id>/     (presence + human 1:1 + agent handback)
"""

from __future__ import annotations

websocket_urlpatterns: list = []
