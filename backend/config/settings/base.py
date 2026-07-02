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

# One Django app per domain (implementation_plan §2). Empty in P0 except
# `accounts` (custom user, defined before first migrate) and `matching`
# (P0 geo spike fixture); domain models land in P1.
LOCAL_APPS = [
    "accounts",
    "catalog",
    "buyers",
    "matching",
    "conversations",
    "outreach",
    "agent_context",
    "notifications",
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
# Custom user (defined before the first migrate to avoid a painful later swap)
# ---------------------------------------------------------------------------
AUTH_USER_MODEL = "accounts.AppUser"

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

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "handlers": {"console": {"class": "logging.StreamHandler"}},
    "root": {"handlers": ["console"], "level": env("LOG_LEVEL", "INFO")},
}
