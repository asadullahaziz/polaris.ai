"""
ChatConsumer — the human 1:1 socket: one per open chat (`ws/chat/<id>/`).

It carries three things on one socket:
  * presence — two decoupled signals. `presence` ("chat open"): set on connect + tab
    `chat.focus`, cleared on `chat.blur`/disconnect → drives the counterparty's online dot.
    `composing` ("reply box focused"): set on `compose.focus`/`typing` (client heartbeats
    while focused), cleared on `compose.blur`/disconnect → this is the signal that silences
    the away-agent, and its set emits `chat/focused` (which cancels the responder's grace).
    Reading a chat keeps you online but leaves the agent covering until you focus the box.
  * sending — `message.send` persists the human message (system of record), broadcasts
    it live to both parties, and emits `chat/inbound` so the counterparty's presence-
    gated away-responder can cover if they're away.
  * receiving — human/agent messages land via the `chat_<id>` channel-layer group
    (the commit gate broadcasts the agent's reply here).

Identity is the session cookie (AuthMiddlewareStack → scope["user"]); non-members are
rejected in connect(). The `chat/focused`/`chat/inbound` emits are best-effort, never
fatal to the chat.
"""

from __future__ import annotations

import json
import logging

import inngest
from asgiref.sync import sync_to_async
from channels.generic.websocket import AsyncWebsocketConsumer

from orchestration.client import inngest_client

from . import presence, services

log = logging.getLogger(__name__)


class ChatConsumer(AsyncWebsocketConsumer):
    async def connect(self):
        user = self.scope.get("user")
        if user is None or not user.is_authenticated:
            await self.close(code=4401)
            return
        try:
            self.chat_id = int(self.scope["url_route"]["kwargs"]["chat_id"])
        except (KeyError, ValueError):
            await self.close(code=4400)
            return

        membership = await sync_to_async(services.chat_membership)(self.chat_id, user.id)
        if membership is None:
            await self.close(code=4403)  # not a member of this chat
            return

        self.user = user
        self.counterparty_user_id = membership["counterparty_user_id"]
        self.group_name = f"chat_{self.chat_id}"
        await self.channel_layer.group_add(self.group_name, self.channel_name)
        await self.accept()
        await self._send("chat.ready", {"chat_id": self.chat_id})
        # Opening the chat = "online" to the counterparty. It does NOT silence the agent —
        # that waits for the reply box to be focused (composing).
        await self._become_present()

    async def disconnect(self, code):
        if getattr(self, "group_name", None) is None:
            return
        await presence.clear_present(self.chat_id, self.user.id)
        await presence.clear_composing(self.chat_id, self.user.id)
        await self._broadcast_presence(present=False)
        await self.channel_layer.group_discard(self.group_name, self.channel_name)

    async def _send(self, type_: str, data: dict) -> None:
        await self.send(text_data=json.dumps({"type": type_, "data": data}))

    async def _become_present(self) -> None:
        """Chat open + visible → 'online' to the counterparty. Does not touch the agent."""
        await presence.set_present(self.chat_id, self.user.id)
        await self._broadcast_presence(present=True)

    async def _become_composing(self) -> None:
        """Reply box focused → the human is taking over: silence the away-agent and cancel
        any in-flight grace for this chat. Private to the gate — no presence broadcast."""
        await presence.set_composing(self.chat_id, self.user.id)
        try:
            await inngest_client.send(
                inngest.Event(
                    name="chat/focused",
                    data={"chat_id": self.chat_id, "user_id": self.user.id},
                )
            )
        except Exception as exc:  # noqa: BLE001 - grace-cancel is best-effort
            log.warning("chat/focused emit failed: %s", exc)

    async def _broadcast_presence(self, *, present: bool) -> None:
        try:
            await self.channel_layer.group_send(
                self.group_name,
                {
                    "type": "chat.presence",
                    "data": {
                        "chat_id": self.chat_id,
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

        if mtype == "chat.focus":  # tab visible → online dot only
            await self._become_present()
        elif mtype == "chat.blur":  # tab hidden → offline dot
            await presence.clear_present(self.chat_id, self.user.id)
            await self._broadcast_presence(present=False)
        elif mtype == "compose.focus":  # reply box focused → silence the agent
            await self._become_composing()
        elif mtype == "compose.blur":  # reply box blurred → let the agent cover again
            await presence.clear_composing(self.chat_id, self.user.id)
        elif mtype == "typing":  # typing ⇒ box focused (composing) and clearly present
            await self._become_composing()
            await self._become_present()
        elif mtype == "message.send":
            await self._handle_send(data)

    async def _handle_send(self, data: dict) -> None:
        body = (data.get("body") or "").strip()
        listing_ids = data.get("attachment_listing_ids") or []
        if not body and not listing_ids:
            return
        saved = await sync_to_async(services.post_human_message)(
            self.chat_id,
            self.user.id,
            body,
            attachment_listing_ids=listing_ids,
            client_dedup_uuid=data.get("client_dedup_uuid"),
        )
        if saved.get("duplicate"):
            return  # double-tap; already broadcast on the first send
        payload = {k: v for k, v in saved.items() if k != "duplicate"}
        # Live to both parties.
        await self.channel_layer.group_send(
            self.group_name, {"type": "chat.message", "data": payload}
        )
        # Arm the counterparty's away-responder (fires only if they're away).
        try:
            await inngest_client.send(
                inngest.Event(
                    name="chat/inbound",
                    data={"chat_id": self.chat_id, "inbound_message_id": saved["id"]},
                )
            )
        except Exception as exc:  # noqa: BLE001
            log.warning("chat/inbound emit failed: %s", exc)

    # ---- channel-layer handlers --------------------------------------------------
    async def chat_message(self, event):
        """A persisted message (human or agent) → deliver as message.new."""
        await self._send("message.new", event.get("data", {}))

    async def chat_presence(self, event):
        d = event.get("data", {})
        # Only surface the counterparty's presence to this client (not our own echo).
        if d.get("user_id") != self.user.id:
            await self._send("presence", d)
