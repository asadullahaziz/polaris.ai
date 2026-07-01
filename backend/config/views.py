"""Project-level views (health check for compose healthchecks)."""

from __future__ import annotations

from django.db import connection
from django.http import JsonResponse


def health(_request):
    """Liveness/readiness probe used by the compose healthcheck (unauthenticated)."""
    db_ok = True
    try:
        with connection.cursor() as cur:
            cur.execute("SELECT 1")
            cur.fetchone()
    except Exception:  # pragma: no cover
        db_ok = False
    status = 200 if db_ok else 503
    return JsonResponse({"status": "ok" if db_ok else "degraded", "db": db_ok}, status=status)
