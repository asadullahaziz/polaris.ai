"""
Chat REST — the inbox list, one chat's transcript, read-state, the find-or-create
entry point, and draft approval. The live turn rides the WebSocket (ChatConsumer); this
is the history + list the UI reads, plus the REST paths the entry points (Contact seller
/ Chat from /buyers / outreach) and the human's draft-approval (takeover) use.

Views return plain dicts (keyed off `sender`/`kind`, never a side), so drf-spectacular is
told the shape explicitly via a placeholder serializer + OBJECT responses.
"""

from __future__ import annotations

import logging

import inngest
from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer
from django.contrib.auth import get_user_model
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import extend_schema, extend_schema_view
from rest_framework import serializers, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from orchestration.client import inngest_client

from . import responder_service as svc
from . import services

log = logging.getLogger(__name__)


def _broadcast_and_arm(chat_id: int, message_payload: dict, inbound_message_id: int) -> None:
    """Push a persisted message to the chat's WS group + arm the counterparty's away-
    responder. Best-effort — the DB write already happened; transport is secondary."""
    try:
        async_to_sync(get_channel_layer().group_send)(
            f"chat_{chat_id}", {"type": "chat.message", "data": message_payload}
        )
    except Exception as exc:  # noqa: BLE001
        log.warning("chat broadcast failed: %s", exc)
    try:
        inngest_client.send_sync(
            inngest.Event(
                name="chat/inbound",
                data={"chat_id": chat_id, "inbound_message_id": inbound_message_id},
            )
        )
    except Exception as exc:  # noqa: BLE001
        log.warning("chat/inbound emit failed: %s", exc)


class _ChatDictSerializer(serializers.Serializer):
    """Placeholder so drf-spectacular has a serializer to probe; the views return plain
    dicts typed via @extend_schema(responses=OBJECT)."""


@extend_schema_view(
    list=extend_schema(responses=OpenApiTypes.OBJECT),
    create=extend_schema(responses=OpenApiTypes.OBJECT),
    retrieve=extend_schema(responses=OpenApiTypes.OBJECT),
    messages=extend_schema(responses=OpenApiTypes.OBJECT),
    read=extend_schema(responses=OpenApiTypes.OBJECT),
    approve_draft=extend_schema(responses=OpenApiTypes.OBJECT),
    discard_draft=extend_schema(responses=OpenApiTypes.OBJECT),
)
class ChatViewSet(viewsets.GenericViewSet):
    serializer_class = _ChatDictSerializer

    # ---- list / find-or-create / retrieve ------------------------------------
    def list(self, request):
        return Response(services.list_inbox(request.user.id))

    def create(self, request):
        """Find-or-create the ONE chat with a counterparty and (optionally) post an
        opener with a listing attached. This is the entry point for "Contact seller"
        (auto-attaches the listing), "Chat" from /buyers, etc."""
        counterparty_id = request.data.get("counterparty_id")
        if counterparty_id is None:
            return Response({"detail": "counterparty_id required"}, status=400)
        try:
            counterparty_id = int(counterparty_id)
        except (TypeError, ValueError):
            return Response({"detail": "counterparty_id must be an integer"}, status=400)
        if counterparty_id == request.user.id:
            return Response({"detail": "cannot start a chat with yourself"}, status=400)
        if not get_user_model().objects.filter(id=counterparty_id).exists():
            return Response({"detail": "counterparty not found"}, status=404)

        chat, _created = services.get_or_create_chat(request.user.id, counterparty_id)

        body = (request.data.get("body") or "").strip()
        listing_ids = request.data.get("attachment_listing_ids") or []
        single = request.data.get("listing_id")
        if single is not None:
            listing_ids = [*listing_ids, single]
        if body or listing_ids:
            saved = services.post_human_message(
                chat.id, request.user.id, body, attachment_listing_ids=listing_ids
            )
            if not saved.get("duplicate"):
                _broadcast_and_arm(
                    chat.id, {k: v for k, v in saved.items() if k != "duplicate"}, saved["id"]
                )
        header = services.chat_header(chat.id, request.user.id)
        return Response(header, status=201)

    def retrieve(self, request, pk=None):
        header = services.chat_header(int(pk), request.user.id)
        if header is None:
            return Response({"detail": "not found"}, status=404)
        return Response(header)

    # ---- transcript (GET) + send (POST) --------------------------------------
    @action(detail=True, methods=["get", "post"])
    def messages(self, request, pk=None):
        chat_id = int(pk)
        if request.method == "GET":
            msgs = services.list_messages(chat_id, request.user.id)
            if msgs is None:
                return Response({"detail": "not found"}, status=404)
            return Response(msgs)
        # POST — send a human message over REST (entry-point / non-WS fallback).
        if services.chat_membership(chat_id, request.user.id) is None:
            return Response({"detail": "not found"}, status=404)
        body = (request.data.get("body") or "").strip()
        listing_ids = request.data.get("attachment_listing_ids") or []
        if not body and not listing_ids:
            return Response({"detail": "empty message"}, status=400)
        saved = services.post_human_message(
            chat_id,
            request.user.id,
            body,
            attachment_listing_ids=listing_ids,
            client_dedup_uuid=request.data.get("client_dedup_uuid"),
        )
        if not saved.get("duplicate"):
            _broadcast_and_arm(
                chat_id, {k: v for k, v in saved.items() if k != "duplicate"}, saved["id"]
            )
        return Response(saved, status=201)

    @action(detail=True, methods=["post"])
    def read(self, request, pk=None):
        if not services.mark_read(int(pk), request.user.id):
            return Response({"detail": "not found"}, status=404)
        return Response({"status": "ok"})

    @action(detail=True, methods=["post"], url_path="approve-draft")
    def approve_draft(self, request, pk=None):
        """Approve a `draft_for_approval` agent draft → send it (the takeover)."""
        chat_id = int(pk)
        if services.chat_membership(chat_id, request.user.id) is None:
            return Response({"detail": "not found"}, status=404)
        msg_id = request.data.get("message_id")
        if msg_id is None:
            return Response({"detail": "message_id required"}, status=400)
        res = svc.approve_draft(request.user.id, int(msg_id))
        if "error" in res:
            return Response(res, status=404)
        # Serialize the now-sent message and broadcast + arm the counterparty's responder.
        from .models import Message

        msg = Message.objects.filter(id=res["message_id"]).first()
        if msg is not None:
            _broadcast_and_arm(chat_id, services.serialize_message(msg), res["message_id"])
        return Response(res)

    @action(detail=True, methods=["post"], url_path="discard-draft")
    def discard_draft(self, request, pk=None):
        """Discard a `draft_for_approval` agent draft without sending it (owner-only)."""
        chat_id = int(pk)
        if services.chat_membership(chat_id, request.user.id) is None:
            return Response({"detail": "not found"}, status=404)
        msg_id = request.data.get("message_id")
        if msg_id is None:
            return Response({"detail": "message_id required"}, status=400)
        res = svc.discard_draft(request.user.id, int(msg_id))
        if "error" in res:
            return Response(res, status=404)
        return Response(res)
