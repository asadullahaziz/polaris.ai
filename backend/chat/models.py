"""
Free-form human 1:1 messaging.

One chat per user-pair: two registered users share exactly one `Chat`, keyed by a
canonical `pair_key` ("minId:maxId"). Listings attach to individual messages
(`MessageAttachment`, kind=listing) and accrue over the life of the chat — there is
no per-listing thread and no author "side": with one chat per pair and a single
acting `sender`, side disappears entirely.

`Message` is the system of record for the transcript (the away-responder rehydrates
from here — never from a checkpoint). Every message records `kind`
(human/agent/system) and `sender` (the member it speaks for; null for system), which
is all the sender-based reply-cap invariant needs (`chat/responder_service.py`).
"""

from __future__ import annotations

from django.conf import settings
from django.db import models

# Chat lifecycle.
CHAT_STATUSES = [
    ("open", "open"),
    ("paused", "paused"),
    ("escalated", "escalated"),
    ("closed", "closed"),
]
# Terminal outcome once a deal resolves inside a chat (nullable — most chats never hit one).
TERMINALS = [
    ("matched", "matched"),
    ("no_fit", "no_fit"),
    ("needs_decision", "needs_decision"),
]

# Who produced a message.
MESSAGE_KINDS = [("human", "human"), ("agent", "agent"), ("system", "system")]

# What an agent message does; drives the UI action chips and deal bookkeeping.
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

# kind=listing is wired; file/photo are reserved.
ATTACHMENT_KINDS = [("listing", "listing"), ("file", "file"), ("photo", "photo")]


def make_pair_key(a_id: int, b_id: int) -> str:
    """Canonical, order-independent key for the 2 members ("minId:maxId"). Backs the
    one-chat-per-pair uniqueness so a chat opened from either direction is the same row."""
    lo, hi = sorted((int(a_id), int(b_id)))
    return f"{lo}:{hi}"


class Chat(models.Model):
    """A free-form 1:1 conversation between two registered users. Exactly one per pair
    (`pair_key` unique). Listings are attached per-message, not bound to the chat."""

    pair_key = models.TextField()  # canonical "minId:maxId" of the two members
    status = models.TextField(default="open", choices=CHAT_STATUSES)
    terminal = models.TextField(null=True, blank=True, choices=TERMINALS)
    # Who an escalation waits on: set by responder_service.escalate. Only this member's
    # next human message reopens the chat — the counterparty pressing again can't
    # reopen-and-re-escalate forever.
    escalated_for = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "chat"
        constraints = [
            # One chat per user-pair — a second "Contact seller" reopens the same chat.
            models.UniqueConstraint(fields=["pair_key"], name="uniq_chat_pair"),
        ]

    def __str__(self) -> str:  # pragma: no cover - trivial
        return f"chat:{self.pk} ({self.pair_key})"


class ChatMember(models.Model):
    """Exactly two rows per chat (enforced in the create seam, `chat.services`). Carries
    per-user read state."""

    pk = models.CompositePrimaryKey("chat_id", "user_id")
    chat = models.ForeignKey(Chat, on_delete=models.CASCADE, related_name="members")
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="chat_memberships"
    )
    # Null = has never opened the chat (everything unread). Set explicitly on `read`.
    last_read_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "chat_member"

    def __str__(self) -> str:  # pragma: no cover - trivial
        return f"chat_member:{self.chat_id}×{self.user_id}"


class Message(models.Model):
    """One turn of the transcript (the system of record). `sender` is the member this
    speaks for: for a `human` message the author; for an `agent` message the principal
    the away-responder covers (so the sender-based cap resets only on the principal's
    own human message); null for `system`."""

    chat = models.ForeignKey(Chat, on_delete=models.CASCADE, related_name="messages")

    kind = models.TextField(choices=MESSAGE_KINDS)  # human | agent | system
    sender = models.ForeignKey(  # acting principal; NULL only for system
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="sent_messages",
    )

    action = models.TextField(null=True, blank=True, choices=ACTIONS)
    body = models.TextField()  # natural-language message (PUBLIC)
    disclosed_fields = models.JSONField(default=dict, blank=True)  # whitelist audit ONLY
    reply_to = models.ForeignKey(  # the inbound this answers (auto-responder)
        "self", on_delete=models.SET_NULL, null=True, blank=True, related_name="replies"
    )

    status = models.TextField(default="sent", choices=MSG_STATUSES)  # draft | sent
    created_at = models.DateTimeField(auto_now_add=True)
    sent_at = models.DateTimeField(null=True, blank=True)  # immutable after (no edit/unsend)
    # Idempotency: agent autoreply (`autoreply:{chat}:{inbound}`) and a human client
    # double-tap (`human:{chat}:{sender}:{uuid}`) share this one index.
    dedup_key = models.TextField(null=True, blank=True)

    class Meta:
        db_table = "chat_message"
        indexes = [
            models.Index(fields=["chat", "created_at"], name="chat_message_chat_idx"),
        ]
        constraints = [
            # Idempotent emit under Inngest at-least-once; also caps one auto-reply/inbound.
            models.UniqueConstraint(
                fields=["dedup_key"],
                condition=models.Q(dedup_key__isnull=False),
                name="uniq_msg_dedup",
            ),
        ]

    def __str__(self) -> str:  # pragma: no cover - trivial
        return f"chat_message:{self.pk} ({self.kind})"


class MessageAttachment(models.Model):
    """A listing shared inside a message — first-class responder context (the away-
    responder reads every attachment to resolve the focal listing), not decoration.
    `kind=listing` is wired; file/photo are reserved."""

    message = models.ForeignKey(Message, on_delete=models.CASCADE, related_name="attachments")
    kind = models.TextField(default="listing", choices=ATTACHMENT_KINDS)
    listing = models.ForeignKey(
        "catalog.Listing",
        on_delete=models.SET_NULL,  # keep the message if the listing is later removed
        null=True,
        blank=True,
        related_name="message_attachments",
    )
    sort_order = models.IntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "message_attachment"
        indexes = [
            models.Index(fields=["message", "sort_order"], name="msg_attach_msg_idx"),
        ]

    def __str__(self) -> str:  # pragma: no cover - trivial
        return f"message_attachment:{self.pk} ({self.kind})"
