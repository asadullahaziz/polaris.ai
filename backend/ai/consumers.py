"""
CopilotConsumer — Graph 1 over the WebSocket (ports v1 `conversations/copilot.py`,
rewired for `ai.*` + confirm-every-write).

One socket per session; identity from AuthMiddlewareStack (session cookie →
scope["user"]). The ReAct copilot agent is built once per connection (bound to the
user + their global agent_instructions). Each turn:
  1. persist the human message (system of record: `ai_message`),
  2. rehydrate the transcript from `ai_message` (architecture §9b) — block-structured
     since 2026-07-10, so past tool calls/results ARE model context (windowed in dal),
  3. stream the assistant's tokens (`copilot.token`) + tool activity (`copilot.tool`
     start/end with a human label); a new LLM call within the turn streams a "\n\n"
     separator first so segments never glue together,
  4. if a WRITE tool raises a confirm interrupt, PAUSE: emit `copilot.confirm` and wait
     for the client's `copilot.confirm_response`, then resume with `Command(resume=…)`;
  5. once the turn completes with no pending interrupt, persist the turn's BLOCKS
     (assistant segments + tool results, from the graph state) and emit `copilot.done`
     (with an auto-title). The flattened token buffer is only the fallback persist.

WS envelope: {type, conversation_id?, data}.
  C→S  copilot.send             {conversation_id?|null, body}
  C→S  copilot.confirm_response {approved: bool}
  S→C  copilot.ready | copilot.created | copilot.token | copilot.tool |
       copilot.confirm | copilot.done | copilot.error
"""

from __future__ import annotations

import asyncio
import json
import logging

from channels.generic.websocket import AsyncWebsocketConsumer
from django.conf import settings
from django.utils import timezone
from langgraph.types import Command

from polaris_agent import dal
from polaris_agent.checkpointer import get_checkpointer
from polaris_agent.graphs.copilot import build_copilot_agent
from polaris_agent.models import get_model
from polaris_agent.tools.labels import tool_label

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
        # Join the per-user group so the outreach fan-out (P5) can push progress ticks +
        # the final summary into this chat. Non-fatal + bounded: a channel-layer hiccup
        # must never take down the chat (the group only carries secondary pushes).
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

    # ---- Channel-layer handlers: outreach fan-out (P5) → this socket ----------------
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
                conv_id = data.get("conversation_id")
                # First auto-expire a stale parked confirm (nobody answered it) so it can't
                # block new messages forever — leaves an 'expired' card, clears the pointer.
                if conv_id is not None:
                    await dal.expire_pending_confirm_if_stale(
                        conv_id, settings.COPILOT_CONFIRM_TTL_SECONDS
                    )
                # Reject if a confirm is still parked — in memory OR durably (a fresh socket
                # after a reload has no in-memory flag) — so a new turn can't orphan it.
                if self._pending is not None or (
                    conv_id is not None and await dal.load_pending_confirm(conv_id)
                ):
                    await self._send(
                        "copilot.error",
                        {"detail": "finish the pending confirmation before sending again"},
                    )
                    return
                body = (data.get("body") or "").strip()
                if body:
                    await self._turn(conv_id, body)
            elif mtype == "copilot.confirm_response":
                await self._resume(bool(data.get("approved")), data.get("conversation_id"))
        except Exception as exc:  # surface, don't drop the socket
            log.exception("copilot turn failed")
            self._pending = None
            # Drop any durable record too — an errored turn is retried from scratch (the
            # checkpoint is ephemeral scratch), never left stuck blocking the conversation.
            conv_id = (payload.get("data") or {}).get("conversation_id")
            if conv_id is not None:
                try:
                    await dal.clear_pending_confirm(conv_id)
                except Exception:  # noqa: BLE001 - best-effort cleanup
                    pass
            await self._send("copilot.error", {"detail": str(exc)})

    # ---- The turn (with a durable pause on write confirmations) -------------------
    async def _pump(self, inp, cfg: dict, buf: list[str], conv_id: int):
        """Stream one (re)entry of the agent. Forwards NL tokens + tool activity
        (`copilot.tool` start/end with a human label); a new LLM call after the first
        streams a "\\n\\n" separator so segments never glue ("sending.Got their IDs").
        Returns the tuple of pending Interrupts if the turn paused for a write
        confirmation, else None."""
        unset = object()  # distinguishes "no LLM call seen this pump" from an id of None
        last_msg_id = unset
        async for mode, data in self.agent.astream(
            inp, config=cfg, stream_mode=["messages", "updates"]
        ):
            if mode == "messages":
                chunk, _meta = data
                ctype = getattr(chunk, "type", "")
                if ctype == "tool":  # a ToolMessage landing = the tool finished
                    await self._send(
                        "copilot.tool",
                        {
                            "conversation_id": conv_id,
                            "status": "end",
                            "name": getattr(chunk, "name", None),
                            "label": tool_label(getattr(chunk, "name", None)),
                        },
                    )
                    continue
                if ctype != "AIMessageChunk":
                    continue
                for tc in getattr(chunk, "tool_call_chunks", None) or []:
                    if tc.get("name"):  # first chunk of a call carries the name
                        await self._send(
                            "copilot.tool",
                            {
                                "conversation_id": conv_id,
                                "status": "start",
                                "name": tc["name"],
                                "label": tool_label(tc["name"]),
                            },
                        )
                text = chunk.content
                if isinstance(text, str) and text:
                    chunk_id = getattr(chunk, "id", None)
                    # A new LLM call while text is already buffered (a later segment of
                    # this turn, or the resume after a confirm pause) → separate it.
                    if buf and (last_msg_id is unset or chunk_id != last_msg_id):
                        buf.append("\n\n")
                        await self._send(
                            "copilot.token", {"conversation_id": conv_id, "token": "\n\n"}
                        )
                    last_msg_id = chunk_id
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
            await self._enter_pending(
                conv_id, cfg, buf, needs_title, body, interrupts, len(history)
            )
            return
        await self._finalize(conv_id, buf, needs_title, body, cfg, len(history))

    async def _persist_blocks_upto_now(self, conv_id, cfg, persisted_len):
        """Persist the graph-state messages beyond `persisted_len` as transcript block
        rows. Returns (last_text_msg_id, new_persisted_len); (None, persisted_len)
        unchanged if the state read fails or nothing new landed."""
        try:
            state = await self.agent.aget_state(cfg)
            msgs = state.values.get("messages") or []
            new = msgs[persisted_len:]
            if not new:
                return None, persisted_len
            msg_id = await dal.save_turn_blocks(conv_id, new)
            return msg_id, len(msgs)
        except Exception:  # noqa: BLE001 - never lose the reply over the audit trail
            log.exception("block persist failed")
            return None, persisted_len

    async def _resume(self, approved: bool, conv_id=None) -> None:
        p = self._pending
        if not p and conv_id is not None:
            # Fresh socket after a nav/reload. If the parked confirm expired while nobody
            # answered, refuse to resume — its checkpoint is stale scratch and approving a
            # long-dead proposal would be a "zombie" write. Expiry leaves an 'expired' card.
            if await dal.expire_pending_confirm_if_stale(
                conv_id, settings.COPILOT_CONFIRM_TTL_SECONDS
            ):
                await self._send(
                    "copilot.error", {"detail": "this request expired — please ask again"}
                )
                return
            # Otherwise rehydrate the parked turn from the DB so we can resume the exact
            # interrupt (checkpoint keyed by the stored cfg.thread_id).
            p = await dal.load_pending_confirm(conv_id)
        if not p:
            await self._send("copilot.error", {"detail": "no pending confirmation"})
            return
        self._pending = None
        # Record the decision as a durable, model-invisible transcript row BEFORE re-pumping,
        # so it lands between the user's request and the assistant's reply on rehydrate.
        await dal.save_confirm_outcome(
            p["conv_id"], p["value"], "approved" if approved else "declined"
        )
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
                p["conv_id"],
                p["cfg"],
                p["buf"],
                p["needs_title"],
                p["first_body"],
                interrupts,
                p.get("persisted_len"),
            )
            return
        await self._finalize(
            p["conv_id"],
            p["buf"],
            p["needs_title"],
            p["first_body"],
            p["cfg"],
            p.get("persisted_len"),
        )

    async def _enter_pending(
        self, conv_id, cfg, buf, needs_title, first_body, interrupts, persisted_len=None
    ) -> None:
        # Persist the blocks produced so far BEFORE parking, so the timeline stays in
        # order (preamble → tool call → confirm card → …) and an expired confirm keeps
        # its preamble. A dangling tool call (paused, no result yet) is fine — the
        # transcript loader strips broken pairs on rehydrate.
        if persisted_len is not None:
            _, persisted_len = await self._persist_blocks_upto_now(conv_id, cfg, persisted_len)
        self._pending = {
            "conv_id": conv_id,
            "cfg": cfg,
            "buf": buf,
            "needs_title": needs_title,
            "first_body": first_body,
            "persisted_len": persisted_len,
            "ids": [i.id for i in interrupts],
            "value": interrupts[0].value,  # the confirm-card render payload
            "created_at": timezone.now().isoformat(),  # TTL clock for lazy expiry
        }
        # Persist the whole resumable payload so the card + parked turn survive a nav /
        # reload / restart. Everything here is JSON-serializable (cfg/buf/ids/value).
        await dal.save_pending_confirm(conv_id, self._pending)
        await self._send(
            "copilot.confirm", {"conversation_id": conv_id, "value": interrupts[0].value}
        )

    async def _finalize(
        self, conv_id, buf, needs_title, first_body, cfg=None, persisted_len=None
    ) -> None:
        # Primary: persist the turn's remaining BLOCKS (assistant segments + tool
        # results) from the graph state — this is what gives the model tool memory on
        # later turns and the UI its activity chips on rehydrate. Fallback: the
        # flattened token buffer (a pre-deploy parked confirm with no persisted_len, or
        # a state read hiccup with nothing block-persisted yet).
        msg_id = None
        blocks_landed = False
        if cfg is not None and persisted_len is not None:
            msg_id, new_len = await self._persist_blocks_upto_now(conv_id, cfg, persisted_len)
            blocks_landed = new_len > persisted_len
        if msg_id is None and not blocks_landed:
            answer = "".join(buf).strip() or "(no response)"
            msg_id = await dal.save_ai_message(conv_id, role="assistant", content=answer)
        await dal.clear_pending_confirm(conv_id)  # turn resolved (approved or declined)
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
