"""
notifications — the in-app notification feed (data_model_decisions Decision 7).

In-app only (no email/SMS). One table covers all four triggers: a new inbound
message, a new outreach received, an agent action awaiting approval, and an
escalation / handback. v2 rewire: the `conversation` FK now targets `chat.Chat`
(the human 1:1 chat) instead of the fused v1 conversation table.
"""

from __future__ import annotations

from django.conf import settings
from django.db import models

NOTIFICATION_TYPES = [
    ("inbound_message", "inbound_message"),
    ("outreach_received", "outreach_received"),
    ("approval_required", "approval_required"),
    ("escalation", "escalation"),
]


class Notification(models.Model):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="notifications"
    )
    type = models.TextField(choices=NOTIFICATION_TYPES)
    chat = models.ForeignKey(
        "chat.Chat",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="notifications",
    )
    payload = models.JSONField(default=dict, blank=True)
    read_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "notification"
        indexes = [
            models.Index(
                fields=["user", "-created_at"],
                name="notification_user_unread_idx",
                condition=models.Q(read_at__isnull=True),
            ),
        ]

    def __str__(self) -> str:  # pragma: no cover - trivial
        return f"notification:{self.pk} ({self.type})"
