"""Chat admin — the free-form 1:1 chat + its transcript."""

from __future__ import annotations

from django.contrib import admin

from .models import Chat, ChatMember, Message, MessageAttachment


class ChatMemberInline(admin.TabularInline):
    model = ChatMember
    extra = 0
    raw_id_fields = ("user",)


@admin.register(Chat)
class ChatAdmin(admin.ModelAdmin):
    list_display = ("id", "pair_key", "status", "terminal", "updated_at")
    list_filter = ("status", "terminal")
    inlines = [ChatMemberInline]


@admin.register(Message)
class MessageAdmin(admin.ModelAdmin):
    list_display = ("id", "chat", "kind", "sender", "action", "status", "created_at")
    list_filter = ("kind", "status", "action")
    raw_id_fields = ("chat", "sender", "reply_to")


admin.site.register(MessageAttachment)
