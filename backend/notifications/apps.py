from django.apps import AppConfig


class NotificationsConfig(AppConfig):
    """In-app notifications: notification (REST + WS push)."""

    default_auto_field = "django.db.models.BigAutoField"
    name = "notifications"
