"""
agent_context — mandate / agent_memory / agent_action_log
(data_model_decisions Decision 6).

The agent's PRIVATE context store. `mandate` = the agent's playbook for one deal
context (a listing OR a buy-box, exactly one). `agent_memory` = per-principal
long-term memory, the system of record reached via read_memory/write_memory tools
(NOT LangGraph BaseStore — architecture §9b). `agent_action_log` = append-only
audit holding private_rationale (never posted to a message). None of these are
ever serialized into a `message` — the disclosure boundary is structural.
"""

from __future__ import annotations

from django.conf import settings
from django.contrib.postgres.fields import ArrayField
from django.db import models

AUTONOMY_LEVELS = [
    ("assist", "assist"),
    ("confirm_batch", "confirm_batch"),
    ("auto_with_policy", "auto_with_policy"),
]


class Mandate(models.Model):
    """Governs ONE deal context: a seller's listing OR a buyer's buy-box."""

    listing = models.ForeignKey(
        "catalog.Listing",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="mandates",
    )
    buy_box = models.ForeignKey(
        "buyers.BuyBox",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="mandates",
    )

    floor_price = models.DecimalField(  # seller floor
        max_digits=12, decimal_places=2, null=True, blank=True
    )
    ceiling_price = models.DecimalField(  # buyer ceiling (may mirror buy_box.price_max)
        max_digits=12, decimal_places=2, null=True, blank=True
    )
    must_haves = ArrayField(models.TextField(), default=list, blank=True)
    availability_window = models.TextField(null=True, blank=True)
    autonomy = models.TextField(default="confirm_batch", choices=AUTONOMY_LEVELS)
    auto_reply = models.BooleanField(default=True)  # presence-gated auto-responder on/off
    instructions = models.TextField(default="", blank=True)  # free-text the LLM reads
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "mandate"
        constraints = [
            models.CheckConstraint(
                name="mandate_one_target",
                condition=(
                    models.Q(listing__isnull=False, buy_box__isnull=True)
                    | models.Q(listing__isnull=True, buy_box__isnull=False)
                ),
            ),
            models.UniqueConstraint(
                fields=["listing"],
                condition=models.Q(listing__isnull=False),
                name="uniq_mandate_listing",
            ),
            models.UniqueConstraint(
                fields=["buy_box"],
                condition=models.Q(buy_box__isnull=False),
                name="uniq_mandate_buy_box",
            ),
        ]

    def __str__(self) -> str:  # pragma: no cover - trivial
        target = f"listing:{self.listing_id}" if self.listing_id else f"buy_box:{self.buy_box_id}"
        return f"mandate:{self.pk} ({target})"


class AgentMemory(models.Model):
    """Per-principal long-term memory. PRIVATE. System of record (architecture §9b)."""

    principal = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="memories"
    )
    namespace = models.TextField(default="general")  # 'buyer' | 'seller' | listing-scoped
    content = models.TextField()  # the remembered fact/note
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "agent_memory"
        indexes = [
            models.Index(fields=["principal", "namespace"], name="agent_memory_principal_idx"),
        ]

    def __str__(self) -> str:  # pragma: no cover - trivial
        return f"memory:{self.pk} ({self.namespace})"


class AgentActionLog(models.Model):
    """Append-only audit of agent actions. PRIVATE. Holds private_rationale."""

    principal = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="action_logs"
    )
    conversation = models.ForeignKey(
        "conversations.Conversation",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="action_logs",
    )
    action_type = models.TextField()  # ranked | drafted | sent | escalated | ...
    summary = models.TextField()
    payload = models.JSONField(default=dict, blank=True)  # incl. private_rationale
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "agent_action_log"
        indexes = [
            models.Index(fields=["principal", "-created_at"], name="agent_action_log_princ_idx"),
        ]

    def __str__(self) -> str:  # pragma: no cover - trivial
        return f"action_log:{self.pk} ({self.action_type})"
