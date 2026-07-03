"""Ai models — Copilot chats (AiChat/AiMessage), memory (AgentMemory/AgentActionLog), and the outreach ledger (OutreachCampaign/OutreachRecipient) + copilot/outreach REST + fan-out functions.

Copilot lands in P2; outreach in P5.

Empty skeleton in P0 (no models yet ⇒ no migration); the phase that owns this app
ports/writes its models here against the v2 schema (see _v1_reference/ for the
v1 source)."""

from __future__ import annotations  # noqa: F401
