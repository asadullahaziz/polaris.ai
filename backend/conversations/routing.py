"""Websocket URL routing (mounted by config.asgi under AuthMiddlewareStack)."""

from django.urls import path

from .consumers import SpikeConsumer

websocket_urlpatterns = [
    # P0 spike socket. P1 replaces/extends with the single per-session socket
    # (chat + presence + copilot) at `ws/`.
    path("ws/spike/", SpikeConsumer.as_asgi()),
]
