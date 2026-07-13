"""
Langfuse tracing seams (observability only — never load-bearing).

Every helper degrades to a no-op when Langfuse is disabled (no keys /
LANGFUSE_ENABLED=false) or misbehaves: a tracing failure must never break a
copilot turn, a responder reply, or the test suite (which runs keyless and
fully offline).

Conventions (the eval seams — keep these stable, dashboards and future
datasets key off them):
  * trace names: ``copilot-turn`` · ``responder-turn`` · ``outreach-summary``
    · ``copilot-title``
  * sessions:    ``copilot:{conv_id}`` (AI chat) · ``chat:{chat_id}`` (1:1)
  * tags:        surface (``copilot``/``responder``) + stance + ``resume`` +
    ``fallback-prompt`` (any compiled prompt fell back — doubles as an outage
    signal)
"""

from __future__ import annotations

import logging
import threading
from contextlib import contextmanager

from polaris_agent import prompt_store

log = logging.getLogger(__name__)

_handler = None
_handler_lock = threading.Lock()


def tracing_enabled() -> bool:
    return prompt_store.enabled()


def _get_handler():
    """Process-wide LangChain callback handler (stateless across runs)."""
    global _handler
    if _handler is None:
        with _handler_lock:
            if _handler is None:
                prompt_store.langfuse_client()  # register the SDK's global client
                from langfuse.langchain import CallbackHandler

                _handler = CallbackHandler()
    return _handler


def callback_config(cfg: dict | None = None, *, tags: list[str] | None = None) -> dict:
    """Return a NEW config dict with the Langfuse callback attached; the input
    is never mutated. That matters: the copilot persists its bare config across
    confirm-every-write pauses (``_enter_pending`` → DB), and a live handler
    object must never land in that JSON. Passthrough copy when tracing is off."""
    out = dict(cfg or {})
    if not tracing_enabled():
        return out
    try:
        handler = _get_handler()
    except Exception:  # noqa: BLE001 - tracing is never load-bearing
        log.warning("langfuse callback unavailable; running untraced", exc_info=True)
        return out
    out["callbacks"] = list(out.get("callbacks") or []) + [handler]
    if tags:
        metadata = dict(out.get("metadata") or {})
        metadata["langfuse_tags"] = list(tags)
        out["metadata"] = metadata
    return out


class TraceHandle:
    """What ``trace_turn`` yields. ``record(**metadata)`` attaches outcome
    metadata to the trace after the run; a no-op when tracing is off."""

    def __init__(self, span=None):
        self._span = span

    def record(self, *, output=None, **metadata) -> None:
        """Attach the turn's outcome: `output` becomes the trace-level output
        (what the Traces list shows), everything else lands in metadata."""
        if self._span is None:
            return
        try:
            kwargs = {"metadata": {k: v for k, v in metadata.items() if v is not None}}
            if output is not None:
                kwargs["output"] = output
            self._span.update_trace(**kwargs)
        except Exception:  # noqa: BLE001
            log.warning("langfuse trace metadata update failed", exc_info=True)

    def annotate(
        self, *, user_id=None, session_id=None, tags=None, metadata=None, input=None
    ) -> None:
        if self._span is None:
            return
        try:
            kwargs = {}
            if user_id:
                kwargs["user_id"] = str(user_id)
            if session_id:
                kwargs["session_id"] = session_id
            if tags:
                kwargs["tags"] = list(tags)
            if metadata:
                kwargs["metadata"] = {k: v for k, v in metadata.items() if v is not None}
            if input is not None:
                kwargs["input"] = input
            if kwargs:
                self._span.update_trace(**kwargs)
        except Exception:  # noqa: BLE001
            log.warning("langfuse trace annotate failed", exc_info=True)


@contextmanager
def trace_turn(
    name: str,
    *,
    user_id: str | None = None,
    session_id: str | None = None,
    tags: list[str] | None = None,
    metadata: dict | None = None,
    input=None,
):
    """Open one trace around an agent turn. LangChain generations from a
    ``callback_config``-decorated invocation nest inside it (OTEL context).
    Pass `input` (the meaningful ask — a user message, an inbound body; never
    a whole context dump) and `handle.record(output=…)` so the Traces list is
    readable without opening each trace."""
    if not tracing_enabled():
        yield TraceHandle()
        return
    try:
        client = prompt_store.langfuse_client()
    except Exception:  # noqa: BLE001
        log.warning("langfuse client unavailable; running untraced", exc_info=True)
        yield TraceHandle()
        return
    with client.start_as_current_span(name=name) as span:
        handle = TraceHandle(span)
        handle.annotate(
            user_id=user_id, session_id=session_id, tags=tags, metadata=metadata, input=input
        )
        yield handle


def shutdown() -> None:
    """Flush buffered traces (ASGI lifespan shutdown). Safe to call anytime."""
    if not tracing_enabled():
        return
    try:
        prompt_store.langfuse_client().flush()
    except Exception:  # noqa: BLE001
        log.warning("langfuse flush failed at shutdown", exc_info=True)
