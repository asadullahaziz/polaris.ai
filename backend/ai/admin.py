"""Ai admin registrations (light — aids demo/debugging)."""

from __future__ import annotations

from django.contrib import admin

from .models import AgentMemory, AiChat, AiMessage


@admin.register(AiChat)
class AiChatAdmin(admin.ModelAdmin):
    list_display = ("id", "owner", "title", "status", "updated_at")
    list_filter = ("status",)
    search_fields = ("title", "owner__email")


@admin.register(AiMessage)
class AiMessageAdmin(admin.ModelAdmin):
    list_display = ("id", "ai_chat", "role", "created_at")
    list_filter = ("role",)


@admin.register(AgentMemory)
class AgentMemoryAdmin(admin.ModelAdmin):
    list_display = ("id", "principal", "namespace", "updated_at")
    list_filter = ("namespace",)
    search_fields = ("content", "principal__email")
