from django.apps import AppConfig


class AiConfig(AppConfig):
    """Copilot chats (AiChat/AiMessage), memory (AgentMemory/AgentActionLog), and the outreach ledger (OutreachCampaign/OutreachRecipient) + copilot/outreach REST + fan-out functions.

    Copilot lands in P2; outreach in P5."""

    default_auto_field = "django.db.models.BigAutoField"
    name = "ai"
