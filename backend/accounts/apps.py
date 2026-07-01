from django.apps import AppConfig


class AccountsConfig(AppConfig):
    """Users & auth: app_user (custom AUTH_USER_MODEL), preferences (P1)."""

    default_auto_field = "django.db.models.BigAutoField"
    name = "accounts"
