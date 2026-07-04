"""Ai URL routes — copilot chats + agent memory. Mounted at /api/ai/ by config.urls."""

from __future__ import annotations

from django.urls import path
from rest_framework.routers import DefaultRouter

from .views import AgentMemoryListView, AiChatViewSet, OutreachCampaignViewSet

app_name = "ai"

router = DefaultRouter()
router.register("chats", AiChatViewSet, basename="ai-chat")
router.register("outreach/campaigns", OutreachCampaignViewSet, basename="outreach-campaign")

urlpatterns = [
    path("memory/", AgentMemoryListView.as_view(), name="memory"),
    *router.urls,
]
