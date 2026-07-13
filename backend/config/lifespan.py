"""
ASGI lifespan handler — eagerly opens the shared checkpointer pool at startup
and closes it at shutdown (P0.8).

Uvicorn emits lifespan events; some servers (e.g. Daphne) do not. The pool is
therefore *also* opened lazily on first use in polaris_agent.checkpointer, so
correctness never depends on lifespan being delivered — this handler just makes
startup eager and shutdown clean.
"""

from __future__ import annotations

import asyncio
import logging

from polaris_agent import observability, prompt_store
from polaris_agent.checkpointer import close_checkpointer, open_checkpointer

log = logging.getLogger(__name__)


async def lifespan_app(scope, receive, send):
    assert scope["type"] == "lifespan"
    while True:
        message = await receive()
        if message["type"] == "lifespan.startup":
            # Eagerly open the pool, but NEVER make startup fatal: get_checkpointer()
            # opens lazily on first use, so a transient DB hiccup here must not crash
            # the server (uvicorn --lifespan on treats startup.failed as fatal).
            try:
                await open_checkpointer()
                log.info("checkpointer pool opened at startup")
            except Exception:  # pragma: no cover
                log.warning(
                    "eager checkpointer startup failed; will open lazily on first use",
                    exc_info=True,
                )
            # Warm the Langfuse prompt cache so runtime reads are memory-speed.
            # Same posture: eager but never fatal — fallbacks cover a cold cache.
            if prompt_store.enabled():
                try:
                    await asyncio.to_thread(prompt_store.warm_up)
                    log.info("langfuse prompt cache warmed")
                except Exception:  # pragma: no cover
                    log.warning(
                        "langfuse prompt warm-up failed; code fallbacks cover",
                        exc_info=True,
                    )
            await send({"type": "lifespan.startup.complete"})
        elif message["type"] == "lifespan.shutdown":
            try:
                await close_checkpointer()
                observability.shutdown()  # flush any buffered Langfuse traces
            finally:
                await send({"type": "lifespan.shutdown.complete"})
            return
