from django.apps import AppConfig


class ChatConfig(AppConfig):
    """Free-form 1:1 chat: Chat, ChatMember, Message, MessageAttachment (+ presence, responder_service, consumers, functions).

    Schema + commit-gate land in P3; the away-responder in P4."""

    default_auto_field = "django.db.models.BigAutoField"
    name = "chat"
