"""
conversations — conversation / message / conversation_read_state
(data_model_decisions Decision 4).

One `conversation` table, two kinds: `thread` (shared, marketplace-style, scoped
to one listing × one counterparty) and `copilot` (private user↔own agent,
multi-session). `message` is the SYSTEM OF RECORD for the transcript (graphs
rehydrate from it, never the checkpoint — architecture §9b). Every message
records its author unambiguously (author_type × author_side × author_id).
"""

from __future__ import annotations

from django.conf import settings
from django.db import models

KINDS = [("thread", "thread"), ("copilot", "copilot")]
CHANNELS = [
    ("in_app", "in_app"),
    ("sms", "sms"),
    ("email", "email"),
    ("whatsapp", "whatsapp"),
]
CONV_STATUSES = [
    ("open", "open"),
    ("paused", "paused"),
    ("escalated", "escalated"),
    ("closed", "closed"),
]
TERMINALS = [
    ("matched", "matched"),
    ("no_fit", "no_fit"),
    ("needs_decision", "needs_decision"),
]
AUTHOR_TYPES = [("human", "human"), ("agent", "agent"), ("system", "system")]
AUTHOR_SIDES = [("seller", "seller"), ("buyer", "buyer")]
# v1 (qualify-and-hold). propose/counter/accept are reserved (stretch negotiation).
ACTIONS = [
    ("ask", "ask"),
    ("inform", "inform"),
    ("qualify", "qualify"),
    ("hold", "hold"),
    ("decline", "decline"),
    ("escalate", "escalate"),
    ("propose", "propose"),
    ("counter", "counter"),
    ("accept", "accept"),
]
MSG_STATUSES = [("draft", "draft"), ("sent", "sent")]


class Conversation(models.Model):
    kind = models.TextField(choices=KINDS)
    channel = models.TextField(default="in_app", choices=CHANNELS)
    # copilot chat label (sidebar); auto-named from the 1st message. NULL for threads.
    title = models.TextField(null=True, blank=True)

    # SHARED THREAD (kind='thread'): one listing × one counterparty.
    listing = models.ForeignKey(
        "catalog.Listing",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="conversations",
    )
    counterparty_user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="counterparty_conversations",
    )
    counterparty_prospect = models.ForeignKey(
        "buyers.Prospect",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="conversations",
    )

    # COPILOT (kind='copilot'): private user↔own agent. MULTI-SESSION (many per user).
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="copilot_conversations",
    )

    status = models.TextField(default="open", choices=CONV_STATUSES)
    terminal = models.TextField(null=True, blank=True, choices=TERMINALS)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "conversation"
        constraints = [
            models.CheckConstraint(
                name="conversation_shape",
                condition=(
                    (
                        models.Q(kind="thread")
                        & models.Q(listing__isnull=False)
                        & models.Q(owner__isnull=True)
                        & (
                            models.Q(
                                counterparty_user__isnull=False,
                                counterparty_prospect__isnull=True,
                            )
                            | models.Q(
                                counterparty_user__isnull=True,
                                counterparty_prospect__isnull=False,
                            )
                        )
                    )
                    | (
                        models.Q(kind="copilot")
                        & models.Q(owner__isnull=False)
                        & models.Q(listing__isnull=True)
                        & models.Q(counterparty_user__isnull=True)
                        & models.Q(counterparty_prospect__isnull=True)
                    )
                ),
            ),
            # One thread per (listing, counterparty) — fuses UI thread & agent conversation.
            models.UniqueConstraint(
                fields=["listing", "counterparty_user"],
                condition=models.Q(counterparty_user__isnull=False),
                name="uniq_thread_user",
            ),
            models.UniqueConstraint(
                fields=["listing", "counterparty_prospect"],
                condition=models.Q(counterparty_prospect__isnull=False),
                name="uniq_thread_prospect",
            ),
        ]
        indexes = [
            # Backs the copilot sidebar (most-recent first). Copilot is multi-session
            # (no uniqueness) — this is just a list index.
            models.Index(
                fields=["owner", "-updated_at"],
                name="copilot_by_owner_idx",
                condition=models.Q(kind="copilot"),
            ),
        ]

    def __str__(self) -> str:  # pragma: no cover - trivial
        return f"conversation:{self.pk} ({self.kind})"


class Message(models.Model):
    conversation = models.ForeignKey(
        Conversation, on_delete=models.CASCADE, related_name="messages"
    )

    # WHO authored it — trust depends on this never being ambiguous.
    author_type = models.TextField(choices=AUTHOR_TYPES)  # human | agent | system
    author_side = models.TextField(null=True, blank=True, choices=AUTHOR_SIDES)
    author = models.ForeignKey(  # acting principal (NULL: system; prospects never author)
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="authored_messages",
    )

    action = models.TextField(null=True, blank=True, choices=ACTIONS)
    body = models.TextField()  # natural-language message (PUBLIC)
    disclosed_fields = models.JSONField(default=dict, blank=True)  # whitelist ONLY
    reply_to = models.ForeignKey(  # inbound this answers (auto-responder)
        "self", on_delete=models.SET_NULL, null=True, blank=True, related_name="replies"
    )

    status = models.TextField(default="sent", choices=MSG_STATUSES)  # draft | sent
    channel = models.TextField(default="in_app", choices=CHANNELS)
    created_at = models.DateTimeField(auto_now_add=True)
    sent_at = models.DateTimeField(null=True, blank=True)  # immutable after (no edit/unsend)
    dedup_key = models.TextField(null=True, blank=True)  # idempotency (architecture §5)

    class Meta:
        db_table = "message"
        indexes = [
            models.Index(fields=["conversation", "created_at"], name="message_conversation_idx"),
        ]
        constraints = [
            # Idempotent emit under Inngest at-least-once; also caps one auto-reply/inbound.
            models.UniqueConstraint(
                fields=["dedup_key"],
                condition=models.Q(dedup_key__isnull=False),
                name="uniq_message_dedup",
            ),
        ]

    def __str__(self) -> str:  # pragma: no cover - trivial
        return f"message:{self.pk} ({self.author_type})"


class ConversationReadState(models.Model):
    """Per-user read/unread state for the inbox."""

    pk = models.CompositePrimaryKey("conversation_id", "user_id")
    conversation = models.ForeignKey(Conversation, on_delete=models.CASCADE)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    last_read_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "conversation_read_state"

    def __str__(self) -> str:  # pragma: no cover - trivial
        return f"read_state:{self.conversation_id}×{self.user_id}"
