"""
Chat presence — two independent signals, both Redis keys with a TTL so a dead socket
self-heals.

  * `presence:{chat_id}:{user_id}` — "this chat is open." Drives the counterparty's
    "● online / ○ away" dot. The ChatConsumer sets it on connect + tab focus/typing and
    clears it on blur/disconnect.
  * `composing:{chat_id}:{user_id}` — "the reply box is focused." This is the signal that
    silences the away-agent (the human has clicked in to take this one over). The
    ChatConsumer sets it on box-focus/typing (with a client heartbeat while focused) and
    clears it on blur/disconnect; the commit gate re-reads it inside its transaction.

The two are decoupled on purpose: merely reading a chat leaves you "online" to the
counterparty while your away-agent still covers — it stands down only once you focus the
reply box. Each key has both a sync (commit gate) and async (WS consumer) client so both
callers are native.

Fail-safe = absent: a missing/ambiguous signal ⇒ the human is treated as away and the
away-agent covers — but the Inngest grace debounces this so a transient blip can't
fire an irreversible send.
"""

from __future__ import annotations

import logging

from django.conf import settings

log = logging.getLogger(__name__)

# Refreshed on every focus/typing event (and a client heartbeat while the box stays
# focused); if the socket dies without a blur, the key expires on its own so a stale
# signal can't silence the agent — or strand the online dot — forever.
PRESENCE_TTL = 120  # seconds


def _key(chat_id: int, user_id: int) -> str:
    return f"presence:{chat_id}:{user_id}"


def _composing_key(chat_id: int, user_id: int) -> str:
    return f"composing:{chat_id}:{user_id}"


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
    except Exception as exc:  # noqa: BLE001 - fail-safe absent
        log.warning("presence read failed, treating as absent: %s", exc)
        return False


def is_composing_sync(chat_id: int, user_id: int) -> bool:
    """The commit gate's stand-down check: is the human's reply box focused?"""
    try:
        return bool(_sync().exists(_composing_key(chat_id, user_id)))
    except Exception as exc:  # noqa: BLE001 - fail-safe absent
        log.warning("composing read failed, treating as absent: %s", exc)
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
    except Exception as exc:  # noqa: BLE001 - fail-safe absent
        log.warning("presence read failed, treating as absent: %s", exc)
        return False


# ---- composing (reply box focused → silences the away-agent) --------------------
async def set_composing(chat_id: int, user_id: int) -> None:
    try:
        await _aclient().set(_composing_key(chat_id, user_id), "1", ex=PRESENCE_TTL)
    except Exception as exc:  # noqa: BLE001 - best-effort; absence is the safe default
        log.warning("composing set failed: %s", exc)


async def clear_composing(chat_id: int, user_id: int) -> None:
    try:
        await _aclient().delete(_composing_key(chat_id, user_id))
    except Exception as exc:  # noqa: BLE001
        log.warning("composing clear failed: %s", exc)


async def is_composing(chat_id: int, user_id: int) -> bool:
    try:
        return bool(await _aclient().exists(_composing_key(chat_id, user_id)))
    except Exception as exc:  # noqa: BLE001 - fail-safe absent
        log.warning("composing read failed, treating as absent: %s", exc)
        return False
