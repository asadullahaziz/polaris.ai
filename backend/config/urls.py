"""
Root URL configuration.

  /admin/                 Django admin
  /api/health/            unauthenticated health probe (compose healthcheck)
  /api/auth/...           email-login session auth (register/verify/login/reset/…)
  /api/schema[/...]       drf-spectacular OpenAPI schema + Swagger UI
  /api/inngest            Inngest serve mount (functions registered here)

Domain routes (listings/chat/ai/deals/notifications) are mounted per app below.
"""

from __future__ import annotations

import inngest.django
from django.contrib import admin
from django.urls import include, path
from drf_spectacular.views import SpectacularAPIView, SpectacularSwaggerView

from config.views import health
from orchestration.client import inngest_client
from orchestration.functions import functions as inngest_functions

urlpatterns = [
    path("admin/", admin.site.urls),
    path("api/health/", health, name="health"),
    path("api/auth/", include("users.urls")),
    path("api/", include("catalog.urls")),  # /api/listings/... + /api/properties/lookup
    path("api/ai/", include("ai.urls")),  # /api/ai/chats/... + /api/ai/memory/
    path("api/", include("chat.urls")),  # /api/chats/...
    path("api/", include("deals.urls")),  # /api/deals/... (mini CRM)
    path("api/", include("notifications.urls")),  # /api/notifications/...
    path("api/schema/", SpectacularAPIView.as_view(), name="schema"),
    path(
        "api/schema/swagger-ui/",
        SpectacularSwaggerView.as_view(url_name="schema"),
        name="swagger-ui",
    ),
    # Inngest serve mount (defaults to path "api/inngest").
    inngest.django.serve(inngest_client, inngest_functions),
]
