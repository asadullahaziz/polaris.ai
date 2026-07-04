"""Notifications admin — the in-app feed."""

from __future__ import annotations

from django.contrib import admin

from .models import Notification


@admin.register(Notification)
class NotificationAdmin(admin.ModelAdmin):
    list_display = ("id", "user", "type", "chat", "read_at", "created_at")
    list_filter = ("type",)
    raw_id_fields = ("user", "chat")
