"""
Chat presence (architecture §9a) — "presence = this chat open."

Kept in Redis, keyed `presence:{chat_id}:{user_id}`, with a TTL so a dead socket
self-heals. The ChatConsumer sets it on focus/typing and clears it on blur/disconnect
(async paths); the commit gate re-reads it inside its transaction (sync path,
architecture §5). Two clients (sync + async) so both callers are native.

**Fail-safe = absent** (architecture §9a): a missing/ambiguous signal ⇒ the human is
treated as away and the away-agent covers — but the Inngest grace (P4) debounces this so
a transient blip can't fire an irreversible send.
"""

from __future__ import annotations

import logging

from django.conf import settings

log = logging.getLogger(__name__)

# Refreshed on every focus/typing event; if the socket dies without a blur, presence
# expires on its own so a stale "present" can't silence the agent forever.
PRESENCE_TTL = 120  # seconds


def _key(chat_id: int, user_id: int) -> str:
    return f"presence:{chat_id}:{user_id}"


# ---- sync (commit gate) --------------------------------------------------------
_sync_client = None


def _sync():
    global _sync_client
    if _sync_client is None:
        import redis

        _sync_client = redis.Redis.from_url(settings.REDIS_URL)
    return _sync_client


def is_present_sync(chat_id: int, user_id: int) -> bool:
    try:
        return bool(_sync().exists(_key(chat_id, user_id)))
    except Exception as exc:  # noqa: BLE001 - fail-safe absent (§9a)
        log.warning("presence read failed, treating as absent: %s", exc)
        return False


# ---- async (WS consumer) -------------------------------------------------------
_async_client = None


def _aclient():
    global _async_client
    if _async_client is None:
        import redis.asyncio as aioredis

        _async_client = aioredis.from_url(settings.REDIS_URL)
    return _async_client


async def set_present(chat_id: int, user_id: int) -> None:
    try:
        await _aclient().set(_key(chat_id, user_id), "1", ex=PRESENCE_TTL)
    except Exception as exc:  # noqa: BLE001 - best-effort; absence is the safe default
        log.warning("presence set failed: %s", exc)


async def clear_present(chat_id: int, user_id: int) -> None:
    try:
        await _aclient().delete(_key(chat_id, user_id))
    except Exception as exc:  # noqa: BLE001
        log.warning("presence clear failed: %s", exc)


async def is_present(chat_id: int, user_id: int) -> bool:
    try:
        return bool(await _aclient().exists(_key(chat_id, user_id)))
    except Exception as exc:  # noqa: BLE001 - fail-safe absent (§9a)
        log.warning("presence read failed, treating as absent: %s", exc)
        return False
