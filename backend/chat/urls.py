"""Chat URL routes — /api/chats/… (mounted by config.urls)."""

from __future__ import annotations

from rest_framework.routers import DefaultRouter

from .views import ChatViewSet

app_name = "chat"

router = DefaultRouter()
router.register("chats", ChatViewSet, basename="chat")

urlpatterns = router.urls
