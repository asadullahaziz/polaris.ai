"""Development settings — loaded by default (manage.py / asgi.py)."""

from __future__ import annotations

import os

# Load a local .env if present (compose injects env vars directly, so this is
# only for bare `manage.py` runs outside the container).
try:
    from dotenv import load_dotenv

    load_dotenv()
except Exception:  # pragma: no cover - dotenv optional
    pass

# Default DEBUG on for dev unless explicitly disabled.
os.environ.setdefault("DJANGO_DEBUG", "true")

from .base import *  # noqa: E402,F401,F403
from .base import env_list  # noqa: E402

DEBUG = True

# Permissive hosts for local/dev containers.
ALLOWED_HOSTS = env_list("DJANGO_ALLOWED_HOSTS", "*") or ["*"]

# Without a SendGrid key (fresh clone), print verification/reset emails to the
# console instead of failing an SMTP connection. Real SendGrid kicks in as soon
# as SENDGRID_API_KEY is set. (Tests use locmem via the pytest test runner.)
import os as _os  # noqa: E402

if not _os.environ.get("SENDGRID_API_KEY") and not _os.environ.get("EMAIL_BACKEND"):
    EMAIL_BACKEND = "django.core.mail.backends.console.EmailBackend"

# In DEBUG, Channels' AllowedHostsOriginValidator additionally allows
# localhost/127.0.0.1 websocket origins automatically.
