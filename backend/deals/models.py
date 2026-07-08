"""
deals — the mini CRM (2026-07-08). One `Deal` per (listing, buyer): the pipeline unit
a real dispo/acquisitions agent tracks. Stages are SYSTEM-DERIVED from message events
(deals/service.py seams), forward-only on the automatic path, and always human-
overridable (`set_stage_manual`). The away-agent reads the focal deal's stage +
standing offers as PRIVATE context; nothing here crosses the disclosure boundary.

Offers: `last_offer_by_*` records AGENT-DISCLOSED offers only (`disclosed_fields.
offer_price` at commit/approve). Human free-text offers are not parsed — so against a
human counterparty the `accept` gate can't fire and the agent escalates with a
recommendation instead.
"""

from __future__ import annotations

from django.conf import settings
from django.db import models
from django.utils import timezone

DEAL_STAGES = [
    ("contacted", "contacted"),  # opener sent / seller pitched
    ("engaged", "engaged"),  # counterparty replied / buyer inquired
    ("negotiating", "negotiating"),  # an offer is on the table
    ("agreed", "agreed"),  # price agreed in principle; humans do paperwork
    ("closed", "closed"),  # done (manual only)
    ("lost", "lost"),  # passed / dead
]


class Deal(models.Model):
    listing = models.ForeignKey("catalog.Listing", on_delete=models.CASCADE, related_name="deals")
    buyer = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="deals_as_buyer"
    )
    # Denormalized from listing.seller at create so the pipeline survives listing edits.
    seller = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="deals_as_seller"
    )
    chat = models.ForeignKey(
        "chat.Chat", on_delete=models.SET_NULL, null=True, blank=True, related_name="deals"
    )

    stage = models.TextField(default="contacted", choices=DEAL_STAGES)
    stage_changed_at = models.DateTimeField(default=timezone.now)

    last_offer_by_buyer = models.DecimalField(
        max_digits=12, decimal_places=2, null=True, blank=True
    )
    last_offer_by_seller = models.DecimalField(
        max_digits=12, decimal_places=2, null=True, blank=True
    )
    agreed_price = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "deal"
        constraints = [
            models.UniqueConstraint(fields=["listing", "buyer"], name="uniq_deal_listing_buyer"),
        ]
        indexes = [
            models.Index(fields=["seller", "-updated_at"], name="deal_seller_idx"),
            models.Index(fields=["buyer", "-updated_at"], name="deal_buyer_idx"),
            models.Index(fields=["chat"], name="deal_chat_idx"),
        ]

    def __str__(self) -> str:  # pragma: no cover - trivial
        return f"deal:{self.pk} listing={self.listing_id} buyer={self.buyer_id} ({self.stage})"
