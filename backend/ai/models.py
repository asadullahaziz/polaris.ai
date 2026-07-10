"""
ai — the copilot's own tables + per-principal agent memory (v2 P2).

The copilot session lives in `ai_chat`/`ai_message`, kept **deliberately separate**
from the human `chat` tables (revisions §polaris-ai): here you talk to *your* AI;
`chat` is human↔human where the away-responder covers for you. Splitting the fused
v1 `conversation` into `ai.*` (copilot) + `chat.*` (human) removes v1's kind-CHECK
and the buyer/seller shape it carried.

`ai_message.role` is LLM-native (`user|assistant|system|tool`) — no author_side, no
CHECK — so the transcript rehydrates straight into LangChain messages (dal).

`AgentActionLog` (added P4) is the away-responder's PRIVATE audit trail over a human
`chat.Chat` — its `private_rationale` (folded into `payload`) is owner-only and never
crosses the disclosure boundary. Written by `chat.responder_service.log_action` after
every commit / draft / escalate.

`OutreachCampaign`/`OutreachRecipient` (added P5) are the seller's outreach ledger —
one `send_outreach` = one campaign over one or more listings → N **registered** buyers,
each buyer paired with exactly the listing(s) they matched.
`OutreachRecipient` IS the delivery ledger: a partial-unique on SENT rows guarantees a
listing reaches each buyer at most once, ever, across campaigns (a cancelled proposal
doesn't block a later legitimate send). v2 rewire: registered users only (prospects are
gone), the opened thread is the ONE pair `chat.Chat` (opener posted as an agent message
+ a `MessageAttachment(kind=listing)`, not a `subject_listing`), and the copilot chat the
launch was fired from is an `ai.AiChat` (where progress ticks + the final summary land).
"""

from __future__ import annotations

from django.conf import settings
from django.db import models

# ai_chat lifecycle. Kept tiny — a copilot session is open until the user archives it.
AI_CHAT_STATUSES = [("open", "open"), ("archived", "archived")]

# LLM-native message roles (no author_side; the copilot is single-principal).
AI_MESSAGE_ROLES = [
    ("user", "user"),
    ("assistant", "assistant"),
    ("system", "system"),
    ("tool", "tool"),
]


class AiChat(models.Model):
    """A copilot session (v1 `conversation` kind='copilot'). Multi-session per user;
    the sidebar orders by `-updated_at`. `title` is Haiku-auto-named from the first
    message (NULL until named)."""

    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="ai_chats"
    )
    title = models.TextField(null=True, blank=True)  # NULL until auto-titled
    status = models.TextField(default="open", choices=AI_CHAT_STATUSES)
    # A parked confirm-every-write turn (LangGraph interrupt), NULL when none. Holds the
    # whole resumable pending payload {conv_id, cfg, buf, needs_title, first_body, ids,
    # value} so the confirm card + the paused agent turn survive a page nav / reload /
    # server restart (the checkpoint keyed by cfg.thread_id resumes the exact interrupt).
    # Only `value` (the render payload) is ever exposed over REST.
    pending_confirm = models.JSONField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "ai_chat"
        indexes = [
            # Backs the copilot sidebar (most-recent first), scoped per owner.
            models.Index(fields=["owner", "-updated_at"], name="ai_chat_owner_idx"),
        ]

    def __str__(self) -> str:  # pragma: no cover - trivial
        return f"ai_chat:{self.pk} ({self.title or 'untitled'})"


class AiMessage(models.Model):
    """One BLOCK of the copilot transcript — the SYSTEM OF RECORD (architecture §9b):
    graphs rehydrate from here, never from the LangGraph checkpoint. Block-structured
    since 2026-07-10: each assistant LLM call is its own row (its tool calls in
    `tool_calls` as a list), each tool result a role='tool' row (`tool_calls` = a dict
    {kind:'tool_result', tool_call_id, name, label} with the result as `content`) — so
    the model remembers past tool traffic and the UI renders activity chips on reopen.
    Resolved confirm cards also live as role='tool' rows (dict kind='confirm_write'),
    UI-only, never re-fed to the model."""

    ai_chat = models.ForeignKey(AiChat, on_delete=models.CASCADE, related_name="messages")
    role = models.TextField(choices=AI_MESSAGE_ROLES)  # user | assistant | system | tool
    content = models.TextField(blank=True, default="")
    tool_calls = models.JSONField(default=list, blank=True)  # list (assistant) | dict (tool)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "ai_message"
        indexes = [
            models.Index(fields=["ai_chat", "created_at"], name="ai_message_chat_idx"),
        ]

    def __str__(self) -> str:  # pragma: no cover - trivial
        return f"ai_message:{self.pk} ({self.role})"


class AgentMemory(models.Model):
    """Per-principal long-term memory — PRIVATE, the system of record reached via the
    copilot's read_memory/write_memory tools (NOT a LangGraph BaseStore, architecture
    §9b). Namespace-scoped + recency-read so future chats stay consistent."""

    principal = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="memories"
    )
    namespace = models.TextField(default="general")  # 'general' | 'buyer' | 'seller' | …
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


# Actions the away-responder records against a human chat (mirrors the commit gate's
# terminal outcomes). Not a hard CHECK — it's an append-only audit, kept flexible.
AGENT_ACTION_TYPES = [
    ("sent", "sent"),  # an autonomous reply committed
    ("drafted", "drafted"),  # a reply parked for the principal's approval
    ("escalated", "escalated"),  # handed to the human, nothing posted to the counterparty
    ("no_reply", "no_reply"),  # contentless inbound closed silently (nothing posted)
]


class AgentActionLog(models.Model):
    """Append-only PRIVATE audit of the away-responder acting on a human `chat.Chat`
    (architecture §5). Every commit / draft / escalate appends one row for the
    **principal** (the user the agent covers for). `payload` carries the whitelisted
    disclosure audit AND the Stage-1 `private_rationale` — which is owner-only and MUST
    never be shown to the counterparty (it never crosses the airlock; this is where it
    lives instead). Written by `chat.responder_service.log_action`."""

    principal = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="agent_actions"
    )
    chat = models.ForeignKey(
        "chat.Chat",
        on_delete=models.SET_NULL,  # keep the audit row if the chat is later removed
        null=True,
        blank=True,
        related_name="agent_actions",
    )
    action_type = models.TextField(choices=AGENT_ACTION_TYPES)
    summary = models.TextField(blank=True, default="")  # short human-readable line
    payload = models.JSONField(default=dict, blank=True)  # disclosure audit + private_rationale
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "agent_action_log"
        indexes = [
            # Backs the principal's private "what did my agent do" timeline.
            models.Index(fields=["principal", "-created_at"], name="agent_action_principal_idx"),
            models.Index(fields=["chat", "created_at"], name="agent_action_chat_idx"),
        ]

    def __str__(self) -> str:  # pragma: no cover - trivial
        return f"agent_action:{self.pk} ({self.action_type})"


# =====================================================================================
# Outreach (P5) — the seller's buyer-discovery fan-out ledger (Graph 3).
# =====================================================================================
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
# In-app only in v1/v2; kept for forward-compat (notifications are in-app only).
CHANNELS = [
    ("in_app", "in_app"),
    ("sms", "sms"),
    ("email", "email"),
    ("whatsapp", "whatsapp"),
]


class OutreachCampaign(models.Model):
    """One `send_outreach` = one campaign → N buyers, each with the listing(s) they
    matched (the per-(buyer, listing) sets live on the recipient rows), staged
    `awaiting_approval` until the seller approves the batch (the send gate). `listing`
    is set when the campaign covers exactly ONE listing (display convenience);
    NULL = multi-listing."""

    listing = models.ForeignKey(
        "catalog.Listing",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="outreach_campaigns",
    )
    seller = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="outreach_campaigns"
    )
    # The copilot session this launch fired from — where the fan-out pushes progress ticks
    # + the final summary over the WS (`copilot_{seller}` group). NULL if launched outside
    # the copilot. v2: `ai.AiChat`, not the fused v1 conversation.
    copilot_ai_chat = models.ForeignKey(
        "ai.AiChat",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="outreach_campaigns",
    )
    status = models.TextField(default="awaiting_approval", choices=CAMPAIGN_STATUSES)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "outreach_campaign"

    def __str__(self) -> str:  # pragma: no cover - trivial
        return f"campaign:{self.pk} ({self.status})"


class OutreachRecipient(models.Model):
    """The DELIVERY LEDGER. v2: registered users only (prospects are gone), so
    `recipient_user` is required and the buyer-or-prospect CHECK disappears. The SENT
    partial-unique is the ledger guarantee — a listing reaches each buyer once, ever."""

    campaign = models.ForeignKey(
        OutreachCampaign, on_delete=models.CASCADE, related_name="recipients"
    )
    listing = models.ForeignKey(
        "catalog.Listing", on_delete=models.CASCADE, related_name="outreach_recipients"
    )
    recipient_user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="outreach_received",
    )

    rank_score = models.DecimalField(  # likelihood-to-buy (deterministic)
        max_digits=6, decimal_places=4, null=True, blank=True
    )
    rank_reason = models.TextField(null=True, blank=True)  # "why this buyer"
    draft_body = models.TextField(null=True, blank=True)  # shown for approval; sent verbatim
    channel = models.TextField(default="in_app", choices=CHANNELS)
    status = models.TextField(default="pending", choices=RECIPIENT_STATUSES)
    # The ONE pair chat this outreach opened (v2: chat.Chat, not a per-listing thread).
    chat = models.ForeignKey(
        "chat.Chat",
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
            # Ledger guarantee — on SENT rows only (a cancelled proposal never blocks a
            # later legitimate send).
            models.UniqueConstraint(
                fields=["listing", "recipient_user"],
                condition=models.Q(status="sent"),
                name="uniq_ledger_user",
            ),
        ]

    def __str__(self) -> str:  # pragma: no cover - trivial
        return f"recipient:{self.pk} ({self.status})"
