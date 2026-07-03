from django.apps import AppConfig


class ConversationsConfig(AppConfig):
    """Chat + presence + copilot streaming: conversation, message,
    conversation_read_state (P1+). Hosts the Channels WS consumers."""

    default_auto_field = "django.db.models.BigAutoField"
    name = "conversations"
