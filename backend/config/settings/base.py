"""
Base settings — shared across all environments.

Environment-specific overrides live in dev.py / prod.py. Values that differ
between deploys (secrets, hosts, DB creds) come from the environment so a fresh
clone + `docker compose up` reproduces the identical stack (implementation_plan §1).
"""

from __future__ import annotations

import os
from pathlib import Path

# backend/ (the Django project root that holds manage.py)
BASE_DIR = Path(__file__).resolve().parent.parent.parent


def env(key: str, default: str | None = None) -> str | None:
    return os.environ.get(key, default)


def env_bool(key: str, default: bool = False) -> bool:
    raw = os.environ.get(key)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def env_list(key: str, default: str = "") -> list[str]:
    raw = os.environ.get(key, default)
    return [item.strip() for item in raw.split(",") if item.strip()]


# ---------------------------------------------------------------------------
# Core
# ---------------------------------------------------------------------------
SECRET_KEY = env("DJANGO_SECRET_KEY", "dev-insecure-change-me")
DEBUG = env_bool("DJANGO_DEBUG", False)
ALLOWED_HOSTS = env_list("DJANGO_ALLOWED_HOSTS", "localhost,127.0.0.1,backend")

# ---------------------------------------------------------------------------
# Applications
# ---------------------------------------------------------------------------
DJANGO_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "django.contrib.gis",  # GeoDjango — PostGIS geometry/geography fields
]

THIRD_PARTY_APPS = [
    "rest_framework",
    "drf_spectacular",
    "corsheaders",
    "channels",
]

# One Django app per domain — the full standardized v2 layout, registered from
# P0 as clean skeletons. Each app's models land in its phase (empty models ⇒ no
# migration until then): users [P0] · catalog [P1] · ai [P2/P5] · chat [P3/P4] ·
# notifications [P3] · orchestration [P5/P6]. `matching` and `polaris_agent` are
# plain packages, NOT Django apps. The v1 port source lives in `_v1_reference/`.
LOCAL_APPS = [
    "users",
    "catalog",
    "chat",
    "notifications",
    "ai",
    "deals",
    "orchestration",
]

INSTALLED_APPS = DJANGO_APPS + THIRD_PARTY_APPS + LOCAL_APPS

MIDDLEWARE = [
    "corsheaders.middleware.CorsMiddleware",  # must precede CommonMiddleware
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

# WSGI kept for management/tooling; the served app is ASGI (Channels).
WSGI_APPLICATION = "config.wsgi.application"
ASGI_APPLICATION = "config.asgi.application"

# ---------------------------------------------------------------------------
# Custom user — email is the login (v2). AbstractBaseUser, defined before the
# first migrate to avoid a painful USERNAME_FIELD swap on a live DB.
# ---------------------------------------------------------------------------
AUTH_USER_MODEL = "users.User"

# ---------------------------------------------------------------------------
# Database — PostGIS backend (GeoDjango). App tables AND the LangGraph
# checkpointer share this one DB (implementation_plan §1).
# ---------------------------------------------------------------------------
DATABASES = {
    "default": {
        "ENGINE": "django.contrib.gis.db.backends.postgis",
        "NAME": env("POSTGRES_DB", "polaris"),
        "USER": env("POSTGRES_USER", "polaris"),
        "PASSWORD": env("POSTGRES_PASSWORD", "polaris"),
        "HOST": env("POSTGRES_HOST", "postgres"),
        "PORT": env("POSTGRES_PORT", "5432"),
        "CONN_MAX_AGE": int(env("DB_CONN_MAX_AGE", "0") or 0),
    }
}

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# ---------------------------------------------------------------------------
# Channels layer (Redis) — presence + group_send from Inngest handlers.
# ---------------------------------------------------------------------------
REDIS_URL = env("REDIS_URL", "redis://redis:6379/0")
CHANNEL_LAYERS = {
    "default": {
        "BACKEND": "channels_redis.core.RedisChannelLayer",
        "CONFIG": {"hosts": [REDIS_URL]},
    }
}

# ---------------------------------------------------------------------------
# LangGraph checkpointer pool (separate from the ORM connection).
# Consumed by polaris_agent.checkpointer at ASGI startup (P0.8).
# ---------------------------------------------------------------------------
CHECKPOINTER_DB_URL = env(
    "CHECKPOINTER_DB_URL",
    "postgresql://{u}:{p}@{h}:{port}/{db}".format(
        u=DATABASES["default"]["USER"],
        p=DATABASES["default"]["PASSWORD"],
        h=DATABASES["default"]["HOST"],
        port=DATABASES["default"]["PORT"],
        db=DATABASES["default"]["NAME"],
    ),
)
CHECKPOINTER_POOL_MAX_SIZE = int(env("CHECKPOINTER_POOL_MAX_SIZE", "10") or 10)

# ---------------------------------------------------------------------------
# DRF — session-cookie auth (implementation_plan §4.1).
# ---------------------------------------------------------------------------
REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": [
        "rest_framework.authentication.SessionAuthentication",
    ],
    "DEFAULT_PERMISSION_CLASSES": [
        "rest_framework.permissions.IsAuthenticated",
    ],
    "DEFAULT_SCHEMA_CLASS": "drf_spectacular.openapi.AutoSchema",
    # Scoped throttles for the abuse-prone anonymous auth endpoints (resend/reset).
    "DEFAULT_THROTTLE_RATES": {
        "auth_resend": env("THROTTLE_AUTH_RESEND", "10/hour"),
        "auth_reset": env("THROTTLE_AUTH_RESET", "10/hour"),
    },
}

SPECTACULAR_SETTINGS = {
    "TITLE": "Polaris AI API",
    "DESCRIPTION": "Property/real-estate portal API (session-cookie auth).",
    "VERSION": "0.1.0",
    "SERVE_INCLUDE_SCHEMA": False,
}

# ---------------------------------------------------------------------------
# Session / CSRF — SPA on a sibling origin (localhost:3000 -> :8000 is
# same-site, so SameSite=Lax cookies are sent on credentialed requests).
# ---------------------------------------------------------------------------
SESSION_COOKIE_SAMESITE = "Lax"
CSRF_COOKIE_SAMESITE = "Lax"
CSRF_COOKIE_HTTPONLY = False  # the SPA must read csrftoken -> X-CSRFToken
CSRF_TRUSTED_ORIGINS = env_list(
    "CSRF_TRUSTED_ORIGINS", "http://localhost:3000,http://127.0.0.1:3000"
)

# django-cors-headers — credentialed cross-origin from the Next.js dev origin.
CORS_ALLOWED_ORIGINS = env_list(
    "CORS_ALLOWED_ORIGINS", "http://localhost:3000,http://127.0.0.1:3000"
)
CORS_ALLOW_CREDENTIALS = True

# ---------------------------------------------------------------------------
# Object storage (MinIO in compose; swappable to S3/R2/GCS by config).
# Inert in P0 — no uploads yet.
# ---------------------------------------------------------------------------
AWS_STORAGE_BUCKET_NAME = env("S3_BUCKET", "polaris-media")
AWS_S3_ENDPOINT_URL = env("S3_ENDPOINT_URL", "http://minio:9000")
AWS_ACCESS_KEY_ID = env("S3_ACCESS_KEY", "minioadmin")
AWS_SECRET_ACCESS_KEY = env("S3_SECRET_KEY", "minioadmin")
AWS_S3_REGION_NAME = env("S3_REGION", "us-east-1")
AWS_S3_USE_SSL = env_bool("S3_USE_SSL", False)
AWS_S3_ADDRESSING_STYLE = "path"  # MinIO requires path-style addressing

STORAGES = {
    "default": {
        "BACKEND": "storages.backends.s3.S3Storage",
        "OPTIONS": {
            "bucket_name": AWS_STORAGE_BUCKET_NAME,
            "endpoint_url": AWS_S3_ENDPOINT_URL,
            "access_key": AWS_ACCESS_KEY_ID,
            "secret_key": AWS_SECRET_ACCESS_KEY,
            "region_name": AWS_S3_REGION_NAME,
            "addressing_style": AWS_S3_ADDRESSING_STYLE,
            "url_protocol": "http:" if not AWS_S3_USE_SSL else "https:",
        },
    },
    "staticfiles": {
        "BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage",
    },
}

# ---------------------------------------------------------------------------
# Password validation / i18n / static
# ---------------------------------------------------------------------------
AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"

# ---------------------------------------------------------------------------
# Provider-agnostic LLM wiring (polaris_agent.models). #5 deferred: keep both
# OpenRouter and native-Anthropic paths swappable by config.
# ---------------------------------------------------------------------------
LLM_PROVIDER = env("LLM_PROVIDER", "openrouter")  # openrouter | anthropic
OPENROUTER_API_KEY = env("OPENROUTER_API_KEY")
ANTHROPIC_API_KEY = env("ANTHROPIC_API_KEY")

# ---------------------------------------------------------------------------
# Email — hand-rolled verification / password-reset tokens (auth design §Auth).
# SendGrid via Django's SMTP EmailBackend in dev/prod; the pytest test runner
# swaps in the locmem backend automatically, so `mail.outbox` works offline.
# When no SendGrid key is present we fall back to the console backend (dev.py),
# so a fresh clone never fails to "send" a verification email.
# ---------------------------------------------------------------------------
SENDGRID_API_KEY = env("SENDGRID_API_KEY")
EMAIL_BACKEND = env("EMAIL_BACKEND", "django.core.mail.backends.smtp.EmailBackend")
EMAIL_HOST = env("EMAIL_HOST", "smtp.sendgrid.net")
EMAIL_PORT = int(env("EMAIL_PORT", "587") or 587)
EMAIL_USE_TLS = env_bool("EMAIL_USE_TLS", True)
EMAIL_HOST_USER = env("EMAIL_HOST_USER", "apikey")  # SendGrid: literal "apikey"
EMAIL_HOST_PASSWORD = SENDGRID_API_KEY or ""
DEFAULT_FROM_EMAIL = env("DEFAULT_FROM_EMAIL", "Polaris AI <no-reply@polaris.local>")

# SPA base — verification/reset links point the browser back at Next.js routes
# (/verify?token=… and /reset?token=…) which POST the confirm endpoints.
FRONTEND_BASE_URL = env("FRONTEND_BASE_URL", "http://localhost:3000")

# Signed-token lifetimes (seconds). Verification is generous; reset is short.
EMAIL_VERIFY_MAX_AGE = int(env("EMAIL_VERIFY_MAX_AGE", str(60 * 60 * 24 * 3)) or 0)  # 3 days
PASSWORD_RESET_MAX_AGE = int(env("PASSWORD_RESET_MAX_AGE", str(60 * 60)) or 0)  # 1 hour

# ---------------------------------------------------------------------------
# Inngest — dev server URL is read from the INNGEST_DEV env var by the SDK.
# ---------------------------------------------------------------------------
INNGEST_APP_ID = env("INNGEST_APP_ID", "polaris")
INNGEST_IS_PRODUCTION = env_bool("INNGEST_IS_PRODUCTION", False)

# ---------------------------------------------------------------------------
# Auto-responder (Graph 2) presence grace window (architecture §9a). The Inngest
# handler waits this long for the human to show up before covering for them. Spec
# is 45s; env-overridable so a live demo can shorten it.
# ---------------------------------------------------------------------------
RESPONDER_GRACE_SECONDS = int(env("RESPONDER_GRACE_SECONDS", "45") or 45)

# ---------------------------------------------------------------------------
# Copilot confirm-every-write TTL. A pending write-confirm nobody answers auto-expires
# after this long (lazy: enforced on reopen / next send / resume — no background job), so
# an approval popup never hangs forever. Default 24h; env-overridable so a demo can shorten
# it. Must stay LONGER than any future checkpointer TTL (see the deferred Redis note).
# ---------------------------------------------------------------------------
COPILOT_CONFIRM_TTL_SECONDS = int(env("COPILOT_CONFIRM_TTL_SECONDS", "86400") or 86400)

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "handlers": {"console": {"class": "logging.StreamHandler"}},
    "root": {"handlers": ["console"], "level": env("LOG_LEVEL", "INFO")},
}
