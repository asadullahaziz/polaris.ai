from django.apps import AppConfig


class AgentContextConfig(AppConfig):
    """Shared context store: mandate, agent_memory, agent_action_log (P1/P3)."""

    default_auto_field = "django.db.models.BigAutoField"
    name = "agent_context"
