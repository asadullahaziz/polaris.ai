"""Notifications URL routes — /api/notifications/… (mounted by config.urls)."""

from __future__ import annotations

from rest_framework.routers import DefaultRouter

from .views import NotificationViewSet

app_name = "notifications"

router = DefaultRouter()
router.register("notifications", NotificationViewSet, basename="notification")

urlpatterns = router.urls
