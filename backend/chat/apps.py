from django.apps import AppConfig


class ChatConfig(AppConfig):
    """Free-form 1:1 chat: Chat, ChatMember, Message, MessageAttachment, plus presence,
    the responder commit gate, the WS consumer, and the Inngest functions."""

    default_auto_field = "django.db.models.BigAutoField"
    name = "chat"
