"""
Root URL configuration.

  /admin/                 Django admin
  /api/health/            unauthenticated health probe (compose healthcheck)
  /api/auth/...           session-cookie auth (login/logout/me/csrf)
  /api/schema[/...]       drf-spectacular OpenAPI schema + Swagger UI
  /api/inngest            Inngest serve mount (functions registered here)
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
    path("api/auth/", include("accounts.urls")),
    path("api/", include("catalog.urls")),  # /api/listings/...
    path("api/copilot/", include("conversations.urls")),  # /api/copilot/conversations/...
    path("api/outreach/", include("outreach.urls")),  # /api/outreach/campaigns/...
    path("api/context/", include("agent_context.urls")),  # /api/context/memory/...
    path("api/schema/", SpectacularAPIView.as_view(), name="schema"),
    path(
        "api/schema/swagger-ui/",
        SpectacularSwaggerView.as_view(url_name="schema"),
        name="swagger-ui",
    ),
    # Inngest serve mount (defaults to path "api/inngest").
    inngest.django.serve(inngest_client, inngest_functions),
]
