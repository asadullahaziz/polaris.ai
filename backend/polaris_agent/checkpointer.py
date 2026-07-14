"""
Shared LangGraph checkpointer.

A single process-wide `AsyncPostgresSaver` over a persistent
`psycopg_pool.AsyncConnectionPool`, kept separate from Django's ORM
connection. Built once and reused by every graph invocation on the ASGI worker.

Lifecycle: `config.lifespan` opens it eagerly at ASGI startup and closes it at
shutdown. Not every ASGI server delivers lifespan events, so `get_checkpointer()`
also opens it lazily on first use under a lock — correctness never depends on
lifespan being delivered.

The checkpointer's tables (`checkpoints`, `checkpoint_writes`,
`checkpoint_blobs`, `checkpoint_migrations`) live in the same Postgres DB as the
app tables but are created by `setup()`, not Django migrations — no collision.
Checkpoints are ephemeral within-turn scratch; the system of record is our own
tables.
"""

from __future__ import annotations

import asyncio
import logging

from django.conf import settings
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool

log = logging.getLogger(__name__)

_pool: AsyncConnectionPool | None = None
_checkpointer: AsyncPostgresSaver | None = None
_lock = asyncio.Lock()

# Connection kwargs required by the Postgres checkpointer:
#   * autocommit=True       -> setup()/pipeline run without an open txn
#   * prepare_threshold=0   -> avoid server-side prepared-statement churn in a pool
#   * row_factory=dict_row  -> rows come back as mappings
_CONNECTION_KWARGS = {
    "autocommit": True,
    "prepare_threshold": 0,
    "row_factory": dict_row,
}


async def open_checkpointer() -> AsyncPostgresSaver:
    """Open the pool + saver once (idempotent). Safe to call from lifespan or lazily."""
    global _pool, _checkpointer
    async with _lock:
        if _checkpointer is not None:
            return _checkpointer

        pool = AsyncConnectionPool(
            conninfo=settings.CHECKPOINTER_DB_URL,
            max_size=settings.CHECKPOINTER_POOL_MAX_SIZE,
            open=False,  # opening in the constructor is deprecated
            kwargs=_CONNECTION_KWARGS,
        )
        await pool.open(wait=True)

        checkpointer = AsyncPostgresSaver(pool)
        await checkpointer.setup()  # creates/migrates checkpointer tables once

        _pool, _checkpointer = pool, checkpointer
        log.info(
            "AsyncPostgresSaver ready (pool max_size=%s)",
            settings.CHECKPOINTER_POOL_MAX_SIZE,
        )
        return _checkpointer


async def get_checkpointer() -> AsyncPostgresSaver:
    """Return the shared checkpointer, opening it lazily on first use."""
    if _checkpointer is not None:
        return _checkpointer
    return await open_checkpointer()


async def close_checkpointer() -> None:
    """Close the pool at ASGI shutdown."""
    global _pool, _checkpointer
    async with _lock:
        if _pool is not None:
            await _pool.close()
            log.info("checkpointer pool closed")
        _pool = None
        _checkpointer = None
