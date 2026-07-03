"""Chat models — Free-form 1:1 chat: Chat, ChatMember, Message, MessageAttachment (+ presence, responder_service, consumers, functions).

Schema + commit-gate land in P3; the away-responder in P4.

Empty skeleton in P0 (no models yet ⇒ no migration); the phase that owns this app
ports/writes its models here against the v2 schema (see _v1_reference/ for the
v1 source)."""

from __future__ import annotations  # noqa: F401
