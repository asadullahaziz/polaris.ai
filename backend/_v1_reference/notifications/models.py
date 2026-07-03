"""
notifications — notification (data_model_decisions Decision 7).

In-app only (no email/SMS). One table covers all four triggers: new inbound
message, new outreach received, an agent action awaiting approval, and an
escalation / handback.
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
    conversation = models.ForeignKey(
        "conversations.Conversation",
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
