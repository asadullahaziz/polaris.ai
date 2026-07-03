"""
ai — the copilot's own tables + per-principal agent memory (v2 P2).

The copilot session lives in `ai_chat`/`ai_message`, kept **deliberately separate**
from the human `chat` tables (revisions §polaris-ai): here you talk to *your* AI;
`chat` is human↔human where the away-responder covers for you. Splitting the fused
v1 `conversation` into `ai.*` (copilot) + `chat.*` (human) removes v1's kind-CHECK
and the buyer/seller shape it carried.

`ai_message.role` is LLM-native (`user|assistant|system|tool`) — no author_side, no
CHECK — so the transcript rehydrates straight into LangChain messages (dal).

Deferred to their phases (kept off this migration so it stays clean):
  * `AgentActionLog` → P4 (its `conversation` FK targets `chat.Chat`, which doesn't
    exist until P3; and only the responder writes private_rationale to it).
  * `OutreachCampaign`/`OutreachRecipient` → P5 (the outreach ledger + fan-out).
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
    """One turn of the copilot transcript — the SYSTEM OF RECORD (architecture §9b):
    graphs rehydrate from here, never from the LangGraph checkpoint. `tool_calls`
    reserves room for a tool-trace audit (unused text-only path in P2)."""

    ai_chat = models.ForeignKey(AiChat, on_delete=models.CASCADE, related_name="messages")
    role = models.TextField(choices=AI_MESSAGE_ROLES)  # user | assistant | system | tool
    content = models.TextField(blank=True, default="")
    tool_calls = models.JSONField(default=list, blank=True)  # reserved (P2 persists text only)
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
