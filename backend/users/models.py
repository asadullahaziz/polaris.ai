"""
users — `User` (email login) + `UserProfile` (v2 target schema §users).

v2 replaces v1's `AbstractUser`/username-keyed `AppUser` with an
`AbstractBaseUser` whose `USERNAME_FIELD` is the email — removing the vestigial
`username` hack v1 apologized for. ONE account type, no stored role: a user's
"side" (buyer/seller) is derived at read time, never stored.

The governance knobs the away-responder + copilot read (`auto_reply_when_away`,
`agent_autonomy`, `agent_instructions`) live on `UserProfile`, not on `Mandate`
(revisions 2026-07-03): they are user-level, not per-deal.
"""

from __future__ import annotations

from django.contrib.auth.models import AbstractBaseUser, PermissionsMixin
from django.db import models
from django.utils import timezone

from .managers import UserManager

# preferred_channel values (features §E #25). in_app only is live in v2.
CHANNELS = [
    ("in_app", "in_app"),
    ("sms", "sms"),
    ("email", "email"),
    ("whatsapp", "whatsapp"),
]

# agent_autonomy — whether the agent auto-sends or drafts for the user's approval
# (moved off Mandate; user-level per revisions).
AUTONOMY_CHOICES = [
    ("draft_for_approval", "draft_for_approval"),
    ("auto_send", "auto_send"),
]


class User(AbstractBaseUser, PermissionsMixin):
    """Email-login account. `email` is unique (and stored lowercased by the
    manager/serializers) so it doubles as the login and is case-insensitive."""

    email = models.EmailField(unique=True, max_length=254)

    # Domain/profile columns kept lean on the auth row.
    full_name = models.TextField(blank=True, default="")
    phone = models.TextField(blank=True, default="")
    preferred_channel = models.CharField(max_length=16, default="in_app", choices=CHANNELS)

    # Auth lifecycle. Login is gated on `is_email_verified` (hand-rolled verify).
    is_email_verified = models.BooleanField(default=False)
    is_active = models.BooleanField(default=True)
    is_staff = models.BooleanField(default=False)

    date_joined = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(auto_now=True)

    objects = UserManager()

    USERNAME_FIELD = "email"
    REQUIRED_FIELDS: list[str] = []

    class Meta:
        db_table = "app_user"

    def __str__(self) -> str:  # pragma: no cover - trivial
        return self.email

    def get_full_name(self) -> str:
        return self.full_name or self.email

    def get_short_name(self) -> str:
        return self.full_name.split(" ")[0] if self.full_name else self.email

    @property
    def display_name(self) -> str:
        """What the agent/UI calls this user — full name if set, else the email."""
        return self.full_name or self.email


class UserProfile(models.Model):
    """The lean auth row's companion: the shared-context store the copilot
    reads/writes, plus the user-level AI governance knobs (revisions §users)."""

    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name="profile")

    # Shared context store (features §E #22): UI and agent tools read/write this
    # same JSON — single source of truth.
    preferences = models.JSONField(default=dict, blank=True)

    # Public-ish profile fields (settings › Account).
    bio = models.TextField(blank=True, default="")
    company = models.TextField(blank=True, default="")
    avatar_url = models.TextField(blank=True, default="")

    # AI governance (settings › AI). User-level, not per-deal.
    #   auto_reply_when_away — the away-responder enable; presence decides "away".
    #   agent_autonomy       — auto-send vs. draft-for-approval for agent replies.
    #   agent_instructions   — global free-text guidance injected into BOTH the
    #                          copilot and the away-responder prompts, layered
    #                          UNDER per-deal Mandate.instructions.
    #   agent_reply_cap      — max away-agent replies (sender=this user) since this
    #                          user's last human message, before the agent escalates
    #                          instead. Bounds the agent↔agent away-cover loop; read
    #                          as the default `n` in `reply_cap_reached` (P4).
    auto_reply_when_away = models.BooleanField(default=True)
    agent_autonomy = models.CharField(max_length=32, default="auto_send", choices=AUTONOMY_CHOICES)
    agent_instructions = models.TextField(blank=True, default="")
    # Default 6 (2026-07-08, was 3): negotiation needs a propose/counter exchange's
    # worth of headroom before the agent hands to the human.
    agent_reply_cap = models.PositiveSmallIntegerField(default=6)

    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "user_profile"

    def __str__(self) -> str:  # pragma: no cover - trivial
        return f"profile:{self.user_id}"
