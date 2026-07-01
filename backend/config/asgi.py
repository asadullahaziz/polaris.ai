"""
ASGI entrypoint — the single served application (P0.6).

ProtocolTypeRouter fans out by scope type:
  * http      -> Django (DRF + Inngest serve mount + admin)
  * websocket -> Channels: AllowedHostsOriginValidator(AuthMiddlewareStack(...))
                 Built-in AuthMiddlewareStack reads the `sessionid` cookie into
                 scope["user"] — no custom socket auth (implementation_plan §4.2).
  * lifespan  -> opens/closes the shared LangGraph checkpointer pool (P0.8).
"""

import os

from django.core.asgi import get_asgi_application

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.dev")

# Initialise Django (populates apps) BEFORE importing consumers/models.
django_asgi_app = get_asgi_application()

from channels.auth import AuthMiddlewareStack  # noqa: E402
from channels.routing import ProtocolTypeRouter, URLRouter  # noqa: E402
from channels.security.websocket import AllowedHostsOriginValidator  # noqa: E402

from config.lifespan import lifespan_app  # noqa: E402
from conversations.routing import websocket_urlpatterns  # noqa: E402

application = ProtocolTypeRouter(
    {
        "http": django_asgi_app,
        "websocket": AllowedHostsOriginValidator(
            AuthMiddlewareStack(URLRouter(websocket_urlpatterns))
        ),
        "lifespan": lifespan_app,
    }
)
