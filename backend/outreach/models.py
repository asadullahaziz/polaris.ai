"""
outreach — outreach_campaign / outreach_recipient (data_model_decisions Decision 5).

One `launch_outreach` = one campaign for one listing → N buyers. `outreach_recipient`
IS the delivery ledger: partial-unique on SENT rows guarantees a listing reaches
each buyer at most once, ever, across campaigns (a cancelled proposal doesn't block
a later legitimate send). buyer-or-prospect pattern.
"""

from __future__ import annotations

from django.conf import settings
from django.db import models

CAMPAIGN_STATUSES = [
    ("awaiting_approval", "awaiting_approval"),
    ("sending", "sending"),
    ("done", "done"),
    ("cancelled", "cancelled"),
]
RECIPIENT_STATUSES = [
    ("pending", "pending"),
    ("sent", "sent"),
    ("skipped_already_contacted", "skipped_already_contacted"),
    ("failed", "failed"),
    ("cancelled", "cancelled"),
]
CHANNELS = [
    ("in_app", "in_app"),
    ("sms", "sms"),
    ("email", "email"),
    ("whatsapp", "whatsapp"),
]


class OutreachCampaign(models.Model):
    listing = models.ForeignKey(
        "catalog.Listing", on_delete=models.CASCADE, related_name="outreach_campaigns"
    )
    seller = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="outreach_campaigns"
    )
    status = models.TextField(default="awaiting_approval", choices=CAMPAIGN_STATUSES)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "outreach_campaign"

    def __str__(self) -> str:  # pragma: no cover - trivial
        return f"campaign:{self.pk} ({self.status})"


class OutreachRecipient(models.Model):
    """The DELIVERY LEDGER. buyer-or-prospect pattern."""

    campaign = models.ForeignKey(
        OutreachCampaign, on_delete=models.CASCADE, related_name="recipients"
    )
    listing = models.ForeignKey(
        "catalog.Listing", on_delete=models.CASCADE, related_name="outreach_recipients"
    )
    recipient_user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="outreach_received",
    )
    recipient_prospect = models.ForeignKey(
        "buyers.Prospect",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="outreach_received",
    )

    rank_score = models.DecimalField(  # likelihood-to-buy (deterministic)
        max_digits=6, decimal_places=4, null=True, blank=True
    )
    rank_reason = models.TextField(null=True, blank=True)  # "why this buyer"
    draft_body = models.TextField(null=True, blank=True)  # shown for approval; sent verbatim
    channel = models.TextField(default="in_app", choices=CHANNELS)
    status = models.TextField(default="pending", choices=RECIPIENT_STATUSES)
    conversation = models.ForeignKey(  # thread this opened
        "conversations.Conversation",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="outreach_recipients",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    sent_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "outreach_recipient"
        constraints = [
            models.CheckConstraint(
                name="outreach_recipient_one_buyer",
                condition=(
                    models.Q(recipient_user__isnull=False, recipient_prospect__isnull=True)
                    | models.Q(recipient_user__isnull=True, recipient_prospect__isnull=False)
                ),
            ),
            # Ledger guarantee is on SENT rows only.
            models.UniqueConstraint(
                fields=["listing", "recipient_user"],
                condition=models.Q(recipient_user__isnull=False, status="sent"),
                name="uniq_ledger_user",
            ),
            models.UniqueConstraint(
                fields=["listing", "recipient_prospect"],
                condition=models.Q(recipient_prospect__isnull=False, status="sent"),
                name="uniq_ledger_prospect",
            ),
        ]

    def __str__(self) -> str:  # pragma: no cover - trivial
        return f"recipient:{self.pk} ({self.status})"
