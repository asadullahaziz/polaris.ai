from django.apps import AppConfig


class OrchestrationConfig(AppConfig):
    """Inngest client singleton + serve mount. Aggregates each app's
    functions.py. No models."""

    default_auto_field = "django.db.models.BigAutoField"
    name = "orchestration"
