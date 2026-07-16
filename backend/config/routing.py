"""
Websocket URL routing aggregator (mounted by config.asgi under AuthMiddlewareStack).

  * ai:   ws/copilot/            streamed copilot chat
  * chat: ws/chat/<chat_id>/     presence + human 1:1 + agent handback
"""

from __future__ import annotations

from ai.routing import websocket_urlpatterns as ai_ws
from chat.routing import websocket_urlpatterns as chat_ws

websocket_urlpatterns: list = [
    *ai_ws,
    *chat_ws,
]
