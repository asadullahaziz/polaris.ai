"""
accounts — `app_user` (data_model_decisions Decision 3).

ONE account type, no roles: a user's "side" (buyer/seller) is derived from which
object a thread hangs off, never stored (features §A #1, implementation_plan §6.1).
AppUser subclasses AbstractUser (auth = session cookies, so password/username come
from Django); the DDL's domain columns are added on top.

Deviations from the reference DDL (data_model_decisions §Decision 3), by design:
  * `id` is Django's BigAutoField (bigserial) rather than IDENTITY — same semantics.
  * `email` keeps AbstractUser's semantics (blank allowed for demo/superusers); the
    DDL's UNIQUE(lower(email)) is enforced as a functional constraint scoped to
    non-empty emails, so blank-email fixtures don't collide.
  * first_name/last_name come from AbstractUser; `full_name` is kept per the DDL and
    populated by the seed.
"""

from __future__ import annotations

from django.contrib.auth.models import AbstractUser
from django.db import models
from django.db.models.functions import Lower

# preferred_channel values (features §E #25). in_app only is live in v1.
CHANNELS = [
    ("in_app", "in_app"),
    ("sms", "sms"),
    ("email", "email"),
    ("whatsapp", "whatsapp"),
]


class AppUser(AbstractUser):
    # Domain columns from the DDL (auth columns come from AbstractUser).
    full_name = models.TextField(null=True, blank=True)
    phone = models.TextField(null=True, blank=True)
    preferred_channel = models.TextField(default="in_app", choices=CHANNELS)
    # Half the shared context store (features §E #22): UI and agent tools read/write
    # this same column — single source of truth.
    preferences = models.JSONField(default=dict, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "app_user"
        constraints = [
            # DDL: CREATE UNIQUE INDEX uniq_user_email ON app_user (lower(email)).
            # Scoped to non-empty emails so blank-email fixtures/superusers don't clash.
            models.UniqueConstraint(
                Lower("email"),
                name="uniq_user_email",
                condition=~models.Q(email=""),
            ),
        ]

    def __str__(self) -> str:  # pragma: no cover - trivial
        return self.get_username()
