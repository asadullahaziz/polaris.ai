from django.contrib.auth.models import AbstractUser
from django.db import models


class AppUser(AbstractUser):
    """
    Custom user = AUTH_USER_MODEL (`accounts.AppUser`).

    P0: a minimal AbstractUser subclass. It is defined *before* the first
    migrate on purpose — swapping the user model after initial migrations is
    painful, so we pay the cost now (Django's own recommendation).

    P1 adds the domain fields from data_model_decisions Decision 1
    (e.g. `preferences` JSON). No role column — a user's "side" is derived from
    which object a thread hangs off (implementation_plan §6.1).
    """

    class Meta:
        db_table = "app_user"

    def __str__(self) -> str:  # pragma: no cover - trivial
        return self.get_username()
