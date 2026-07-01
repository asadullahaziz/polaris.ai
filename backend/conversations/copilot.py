"""
CopilotConsumer (implementation_plan P1.3) — Graph 1 over the WebSocket.

One socket per session; identity from AuthMiddlewareStack (session cookie →
scope["user"]). The ReAct copilot agent is built once per connection (bound to the
user). Each turn:
  1. persist the human message (system of record),
  2. rehydrate the transcript from the `message` table (architecture §9b),
  3. stream the assistant's tokens (`copilot.token`) — structured-output / tool-call
     chunks carry empty content and are naturally skipped,
  4. persist the assistant message and emit `copilot.done` (with an auto-title).

WS envelope (implementation_plan §4.2): {type, conversation_id?, data}.
  C→S  copilot.send   {conversation_id?|null, body}
  S→C  copilot.ready | copilot.created | copilot.token | copilot.done | copilot.error
"""

from __future__ import annotations

import json
import logging

from channels.generic.websocket import AsyncWebsocketConsumer

from polaris_agent import dal
from polaris_agent.checkpointer import get_checkpointer
from polaris_agent.graphs.copilot import build_copilot_agent
from polaris_agent.models import get_model

log = logging.getLogger(__name__)


class CopilotConsumer(AsyncWebsocketConsumer):
    async def connect(self):
        user = self.scope.get("user")
        if user is None or not user.is_authenticated:
            await self.close(code=4401)  # unauthenticated
            return
        self.user = user
        checkpointer = await get_checkpointer()
        name = await dal.display_name(user.id)
        self.agent = build_copilot_agent(checkpointer, principal_id=user.id, display_name=name)
        await self.accept()
        await self._send("copilot.ready", {"user": user.get_username()})

    async def _send(self, type_: str, data: dict) -> None:
        await self.send(text_data=json.dumps({"type": type_, "data": data}))

    async def receive(self, text_data=None, bytes_data=None):
        try:
            payload = json.loads(text_data or "{}")
        except json.JSONDecodeError:
            return
        if payload.get("type") != "copilot.send":
            return
        data = payload.get("data") or {}
        body = (data.get("body") or "").strip()
        if not body:
            return
        try:
            await self._turn(data.get("conversation_id"), body)
        except Exception as exc:  # surface, don't drop the socket
            log.exception("copilot turn failed")
            await self._send("copilot.error", {"detail": str(exc)})

    async def _turn(self, conv_id, body: str) -> None:
        user = self.user
        if conv_id is None:
            conv_id = await dal.create_copilot(user.id)
            await self._send("copilot.created", {"conversation_id": conv_id})
        elif not await dal.owns_copilot(user.id, conv_id):
            await self._send("copilot.error", {"detail": "conversation not found"})
            return

        needs_title = await dal.needs_title(conv_id)
        await dal.save_message(conv_id, author_type="human", body=body, author_id=user.id)
        history = await dal.load_transcript(conv_id)  # includes the new human message

        buf: list[str] = []
        async for chunk, _meta in self.agent.astream(
            {"messages": history},
            config={"configurable": {"thread_id": f"copilot:{conv_id}:{len(history)}"}},
            stream_mode="messages",
        ):
            # Stream only the assistant's natural-language tokens. `messages` mode also
            # yields ToolMessages (raw tool-result JSON) and tool-call AIMessageChunks
            # (empty content); both must be skipped from the user-facing stream.
            if getattr(chunk, "type", "") != "AIMessageChunk":
                continue
            text = chunk.content
            if isinstance(text, str) and text:
                buf.append(text)
                await self._send("copilot.token", {"conversation_id": conv_id, "token": text})

        answer = "".join(buf).strip() or "(no response)"
        msg_id = await dal.save_message(
            conv_id, author_type="agent", body=answer, author_id=user.id
        )
        title = await self._title(conv_id, body) if needs_title else None
        await self._send(
            "copilot.done", {"conversation_id": conv_id, "message_id": msg_id, "title": title}
        )

    async def _title(self, conv_id: int, first_msg: str) -> str:
        """Auto-name the chat from its first message (Haiku), best-effort."""
        try:
            resp = await get_model("bulk").ainvoke(
                "Write a 3-6 word title (no quotes, no trailing punctuation) for a "
                f"real-estate chat that begins with:\n{first_msg[:400]}"
            )
            title = (resp.content or "").strip().lstrip("#").strip().strip('"')[:80] or first_msg[
                :40
            ]
        except Exception:
            title = first_msg[:40]
        await dal.set_title_if_empty(conv_id, title)
        return title
