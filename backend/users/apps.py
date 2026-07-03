from django.apps import AppConfig


class UsersConfig(AppConfig):
    """Users & auth (v2): email-login `User` (AUTH_USER_MODEL) + `UserProfile`
    (the governance knobs + shared-context store the copilot reads/writes)."""

    default_auto_field = "django.db.models.BigAutoField"
    name = "users"

    def ready(self) -> None:
        # Wire the post_save signal that guarantees every User has a profile row.
        from . import signals  # noqa: F401
