from django.apps import AppConfig


class NotificationsConfig(AppConfig):
    """Notification feed (inbound_message / outreach_received / approval_required / escalation).

    Model + REST land in P3."""

    default_auto_field = "django.db.models.BigAutoField"
    name = "notifications"
