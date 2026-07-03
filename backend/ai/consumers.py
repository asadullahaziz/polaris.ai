"""
CopilotConsumer — Graph 1 over the WebSocket (ports v1 `conversations/copilot.py`,
rewired for `ai.*` + confirm-every-write).

One socket per session; identity from AuthMiddlewareStack (session cookie →
scope["user"]). The ReAct copilot agent is built once per connection (bound to the
user + their global agent_instructions). Each turn:
  1. persist the human message (system of record: `ai_message`),
  2. rehydrate the transcript from `ai_message` (architecture §9b),
  3. stream the assistant's tokens (`copilot.token`) — tool-call / structured-output
     chunks carry empty content and are naturally skipped,
  4. if a WRITE tool raises a confirm interrupt, PAUSE: emit `copilot.confirm` and wait
     for the client's `copilot.confirm_response`, then resume with `Command(resume=…)`;
  5. once the turn completes with no pending interrupt, persist the assistant message and
     emit `copilot.done` (with an auto-title).

WS envelope: {type, conversation_id?, data}.
  C→S  copilot.send             {conversation_id?|null, body}
  C→S  copilot.confirm_response {approved: bool}
  S→C  copilot.ready | copilot.created | copilot.token | copilot.confirm |
       copilot.done | copilot.error
"""

from __future__ import annotations

import asyncio
import json
import logging

from channels.generic.websocket import AsyncWebsocketConsumer
from langgraph.types import Command

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
        self.group_name = f"copilot_{user.id}"
        self._group_joined = False
        self._pending: dict | None = None  # set while a write awaits confirmation
        checkpointer = await get_checkpointer()
        name = await dal.display_name(user.id)
        instructions = await dal.agent_instructions(user.id)
        self.agent = build_copilot_agent(
            checkpointer,
            principal_id=user.id,
            display_name=name,
            agent_instructions=instructions,
        )
        await self.accept()
        await self._send("copilot.ready", {"user": user.get_username()})
        # Join the per-user group so the P5 outreach fan-out can push progress ticks +
        # the final summary into this chat. Non-fatal + bounded: a channel-layer hiccup
        # must never take down the chat (the group only carries secondary pushes). Inert
        # until P5 wires the fan-out.
        try:
            await asyncio.wait_for(
                self.channel_layer.group_add(self.group_name, self.channel_name), timeout=3
            )
            self._group_joined = True
        except Exception as exc:  # noqa: BLE001 - degrade gracefully, keep the chat alive
            log.warning("copilot group_add failed; live outreach ticks off this session: %s", exc)

    async def disconnect(self, code):
        if getattr(self, "_group_joined", False):
            try:
                await self.channel_layer.group_discard(self.group_name, self.channel_name)
            except Exception:  # noqa: BLE001 - best-effort cleanup
                pass

    async def _send(self, type_: str, data: dict) -> None:
        await self.send(text_data=json.dumps({"type": type_, "data": data}))

    # ---- Channel-layer handlers: P5 outreach fan-out → this socket (inert until P5) --
    async def outreach_progress(self, event):
        await self._send("outreach.progress", event.get("data", {}))

    async def outreach_summary(self, event):
        await self._send("outreach.summary", event.get("data", {}))

    # ---- Inbound frames -----------------------------------------------------------
    async def receive(self, text_data=None, bytes_data=None):
        try:
            payload = json.loads(text_data or "{}")
        except json.JSONDecodeError:
            return
        mtype = payload.get("type")
        data = payload.get("data") or {}
        try:
            if mtype == "copilot.send":
                if self._pending is not None:
                    await self._send(
                        "copilot.error",
                        {"detail": "finish the pending confirmation before sending again"},
                    )
                    return
                body = (data.get("body") or "").strip()
                if body:
                    await self._turn(data.get("conversation_id"), body)
            elif mtype == "copilot.confirm_response":
                await self._resume(bool(data.get("approved")))
        except Exception as exc:  # surface, don't drop the socket
            log.exception("copilot turn failed")
            self._pending = None
            await self._send("copilot.error", {"detail": str(exc)})

    # ---- The turn (with a durable pause on write confirmations) -------------------
    async def _pump(self, inp, cfg: dict, buf: list[str], conv_id: int):
        """Stream one (re)entry of the agent. Forwards NL tokens; returns the tuple of
        pending Interrupts if the turn paused for a write confirmation, else None."""
        async for mode, data in self.agent.astream(
            inp, config=cfg, stream_mode=["messages", "updates"]
        ):
            if mode == "messages":
                chunk, _meta = data
                if getattr(chunk, "type", "") != "AIMessageChunk":
                    continue
                text = chunk.content
                if isinstance(text, str) and text:
                    buf.append(text)
                    await self._send("copilot.token", {"conversation_id": conv_id, "token": text})
            elif mode == "updates":
                if isinstance(data, dict) and "__interrupt__" in data:
                    return data["__interrupt__"]
        return None

    async def _turn(self, conv_id, body: str) -> None:
        user = self.user
        if conv_id is None:
            conv_id = await dal.create_ai_chat(user.id)
            await self._send("copilot.created", {"conversation_id": conv_id})
        elif not await dal.owns_ai_chat(user.id, conv_id):
            await self._send("copilot.error", {"detail": "conversation not found"})
            return

        needs_title = await dal.needs_title(conv_id)
        await dal.save_ai_message(conv_id, role="user", content=body)
        history = await dal.load_transcript(conv_id)  # includes the new human message
        # A stable per-turn thread_id: the checkpoint persists across the confirm pause,
        # then the next turn (new len) starts fresh scratch (architecture §9b).
        cfg = {
            "configurable": {
                "thread_id": f"copilot:{conv_id}:{len(history)}",
                "ai_chat_id": conv_id,
            }
        }
        buf: list[str] = []
        interrupts = await self._pump({"messages": history}, cfg, buf, conv_id)
        if interrupts is not None:
            await self._enter_pending(conv_id, cfg, buf, needs_title, body, interrupts)
            return
        await self._finalize(conv_id, buf, needs_title, body)

    async def _resume(self, approved: bool) -> None:
        p = self._pending
        if not p:
            await self._send("copilot.error", {"detail": "no pending confirmation"})
            return
        self._pending = None
        ids = p["ids"]
        resume_val = {"approved": approved}
        # One pending write → resume with the bare value; multiple (parallel tool calls
        # in one super-step) → route the same decision to each by interrupt id.
        command = (
            Command(resume=resume_val)
            if len(ids) <= 1
            else Command(resume={iid: resume_val for iid in ids})
        )
        interrupts = await self._pump(command, p["cfg"], p["buf"], p["conv_id"])
        if interrupts is not None:
            await self._enter_pending(
                p["conv_id"], p["cfg"], p["buf"], p["needs_title"], p["first_body"], interrupts
            )
            return
        await self._finalize(p["conv_id"], p["buf"], p["needs_title"], p["first_body"])

    async def _enter_pending(self, conv_id, cfg, buf, needs_title, first_body, interrupts) -> None:
        self._pending = {
            "conv_id": conv_id,
            "cfg": cfg,
            "buf": buf,
            "needs_title": needs_title,
            "first_body": first_body,
            "ids": [i.id for i in interrupts],
        }
        await self._send(
            "copilot.confirm", {"conversation_id": conv_id, "value": interrupts[0].value}
        )

    async def _finalize(self, conv_id, buf, needs_title, first_body) -> None:
        answer = "".join(buf).strip() or "(no response)"
        msg_id = await dal.save_ai_message(conv_id, role="assistant", content=answer)
        title = await self._title(conv_id, first_body) if needs_title else None
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
