from django.apps import AppConfig


class AiConfig(AppConfig):
    """Copilot chats (AiChat/AiMessage), agent memory (AgentMemory/AgentActionLog), and
    the outreach ledger (OutreachCampaign/OutreachRecipient), plus their REST views and
    the fan-out functions."""

    default_auto_field = "django.db.models.BigAutoField"
    name = "ai"
