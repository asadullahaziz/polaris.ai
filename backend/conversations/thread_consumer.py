"""
ThreadConsumer (implementation_plan P3.8, architecture §4.2/§9a) — the human-facing
shared-thread socket: one per open thread (`ws/thread/<id>/`).

It carries three things on one socket (§4.2):
  * **presence** — opening the thread = present; `thread.focus`/`typing` refresh it and
    emit `thread/focused` (which cancels the auto-responder's 45s grace); `thread.blur`
    and disconnect clear it. Presence is the signal that silences the agent (§9a).
  * **sending** — `message.send` persists the human message (system of record), broadcasts
    it live to both parties, and emits `thread/inbound` so the *counterparty's* presence-
    gated auto-responder (Graph 2) can cover if they're away.
  * **receiving** — agent/human messages land via the `thread_<id>` channel-layer group
    (the Inngest commit gate broadcasts the agent's reply here).

Identity is the session cookie (AuthMiddlewareStack → scope["user"]); non-participants
are rejected in connect().
"""

from __future__ import annotations

import json
import logging

import inngest
from channels.generic.websocket import AsyncWebsocketConsumer

from orchestration.client import inngest_client
from polaris_agent import dal

from . import presence

log = logging.getLogger(__name__)


class ThreadConsumer(AsyncWebsocketConsumer):
    async def connect(self):
        user = self.scope.get("user")
        if user is None or not user.is_authenticated:
            await self.close(code=4401)
            return
        try:
            self.conversation_id = int(self.scope["url_route"]["kwargs"]["conversation_id"])
        except (KeyError, ValueError):
            await self.close(code=4400)
            return

        part = await dal.thread_participant(self.conversation_id, user.id)
        if part is None:
            await self.close(code=4403)  # not a participant of this thread
            return

        self.user = user
        self.side = part["side"]
        self.counterparty_user_id = part["counterparty_user_id"]
        self.group_name = f"thread_{self.conversation_id}"
        await self.channel_layer.group_add(self.group_name, self.channel_name)
        await self.accept()
        await self._send(
            "thread.ready", {"conversation_id": self.conversation_id, "side": self.side}
        )
        # Opening the thread IS presence. Set it and let the counterparty know.
        await self._become_present()

    async def disconnect(self, code):
        if getattr(self, "group_name", None) is None:
            return
        await presence.clear_present(self.conversation_id, self.user.id)
        await self._broadcast_presence(present=False)
        await self.channel_layer.group_discard(self.group_name, self.channel_name)

    async def _send(self, type_: str, data: dict) -> None:
        await self.send(text_data=json.dumps({"type": type_, "data": data}))

    async def _become_present(self) -> None:
        await presence.set_present(self.conversation_id, self.user.id)
        await self._broadcast_presence(present=True)
        # Cancel any in-flight auto-responder grace for this thread (§9a).
        try:
            await inngest_client.send(
                inngest.Event(
                    name="thread/focused",
                    data={"conversation_id": self.conversation_id, "user_id": self.user.id},
                )
            )
        except Exception as exc:  # noqa: BLE001 - grace-cancel is best-effort
            log.warning("thread/focused emit failed: %s", exc)

    async def _broadcast_presence(self, *, present: bool) -> None:
        try:
            await self.channel_layer.group_send(
                self.group_name,
                {
                    "type": "thread.presence",
                    "data": {
                        "conversation_id": self.conversation_id,
                        "user_id": self.user.id,
                        "present": present,
                    },
                },
            )
        except Exception as exc:  # noqa: BLE001
            log.warning("presence broadcast failed: %s", exc)

    async def receive(self, text_data=None, bytes_data=None):
        try:
            payload = json.loads(text_data or "{}")
        except json.JSONDecodeError:
            return
        mtype = payload.get("type")
        data = payload.get("data") or {}

        if mtype in ("thread.focus", "typing"):
            await self._become_present()
        elif mtype == "thread.blur":
            await presence.clear_present(self.conversation_id, self.user.id)
            await self._broadcast_presence(present=False)
        elif mtype == "message.send":
            await self._handle_send(data)

    async def _handle_send(self, data: dict) -> None:
        body = (data.get("body") or "").strip()
        if not body:
            return
        saved = await dal.save_thread_message(
            self.conversation_id,
            self.user.id,
            self.side,
            body,
            client_dedup_uuid=data.get("client_dedup_uuid"),
        )
        if saved.get("duplicate"):
            return  # double-tap; already broadcast on the first send
        # Live to both parties.
        await self.channel_layer.group_send(
            self.group_name,
            {
                "type": "thread.message",
                "data": {
                    "id": saved["id"],
                    "conversation_id": self.conversation_id,
                    "author_type": "human",
                    "author_side": self.side,
                    "body": body,
                },
            },
        )
        # Arm the counterparty's auto-responder (fires only if they're away — §5/§9a).
        try:
            await inngest_client.send(
                inngest.Event(
                    name="thread/inbound",
                    data={
                        "conversation_id": self.conversation_id,
                        "inbound_message_id": saved["id"],
                    },
                )
            )
        except Exception as exc:  # noqa: BLE001
            log.warning("thread/inbound emit failed: %s", exc)

    # ---- channel-layer handlers --------------------------------------------------
    async def thread_message(self, event):
        """A persisted message (human or agent) → deliver as message.new."""
        await self._send("message.new", event.get("data", {}))

    async def thread_presence(self, event):
        d = event.get("data", {})
        # Only surface the COUNTERPARTY's presence to this client (not our own echo).
        if d.get("user_id") != self.user.id:
            await self._send("presence", d)
