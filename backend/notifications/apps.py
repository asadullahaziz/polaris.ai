from django.apps import AppConfig


class NotificationsConfig(AppConfig):
    """Notification feed (inbound_message / outreach_received / approval_required / escalation)."""

    default_auto_field = "django.db.models.BigAutoField"
    name = "notifications"
