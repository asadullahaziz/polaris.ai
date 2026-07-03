"""
Shared-thread REST (implementation_plan P2.8-deferred / P3.8/P3.10): the inbox list,
one thread's transcript, and draft approval. The live turn rides the WebSocket
(ThreadConsumer); this is the history + list the UI reads, plus the human's approve
action for `assist`/`confirm` drafts (the takeover).
"""

from __future__ import annotations

import logging

import inngest
from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer
from django.db.models import Q
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import extend_schema, extend_schema_view
from rest_framework import serializers, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from orchestration.client import inngest_client
from polaris_agent import dal

from . import responder_service as svc
from .models import Conversation, Message

log = logging.getLogger(__name__)


def _counterparty_label(conv: Conversation, my_side: str) -> tuple[str, str]:
    if my_side == "buyer":
        s = conv.listing.seller
        return (s.full_name or s.get_username()), "user"
    if conv.counterparty_user_id:
        u = conv.counterparty_user
        return (u.full_name or u.get_username()), "user"
    if conv.counterparty_prospect_id:
        p = conv.counterparty_prospect
        return (p.full_name or p.entity_name or f"Prospect {p.id}"), "prospect"
    return "Buyer", "unknown"


def _listing_address(conv: Conversation) -> str | None:
    if not conv.listing_id:
        return None
    lp = conv.listing.listingproperty_set.select_related("property").first()
    return lp.property.address_raw if lp and lp.property else None


# Returns plain dicts (not model serializers), so tell drf-spectacular the shape
# explicitly rather than have it probe for a serializer_class.
class _ThreadDictSerializer(serializers.Serializer):
    """Placeholder so drf-spectacular has a serializer to probe; the views return the
    plain dicts typed via @extend_schema(responses=OBJECT) below."""


@extend_schema_view(
    list=extend_schema(responses=OpenApiTypes.OBJECT),
    retrieve=extend_schema(responses=OpenApiTypes.OBJECT),
    messages=extend_schema(responses=OpenApiTypes.OBJECT),
    mandate=extend_schema(responses=OpenApiTypes.OBJECT),
    approve_draft=extend_schema(responses=OpenApiTypes.OBJECT),
)
class ThreadViewSet(viewsets.GenericViewSet):
    serializer_class = _ThreadDictSerializer

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):  # drf-spectacular introspection: no request
            return Conversation.objects.none()
        u = self.request.user
        return (
            Conversation.objects.filter(kind="thread")
            .filter(Q(listing__seller=u) | Q(counterparty_user=u))
            .select_related("listing__seller", "counterparty_user", "counterparty_prospect")
            .prefetch_related("listing__listingproperty_set__property")
            .order_by("-updated_at")
        )

    def _my_side(self, conv: Conversation) -> str:
        return (
            "seller" if conv.listing and conv.listing.seller_id == self.request.user.id else "buyer"
        )

    def _serialize(self, conv: Conversation) -> dict:
        my_side = self._my_side(conv)
        name, kind = _counterparty_label(conv, my_side)
        last = (
            Message.objects.filter(conversation=conv, status="sent")
            .order_by("-created_at", "-id")
            .values("body", "author_type", "author_side", "created_at")
            .first()
        )
        return {
            "id": conv.id,
            "listing_id": conv.listing_id,
            "listing_address": _listing_address(conv),
            "my_side": my_side,
            "counterparty_name": name,
            "counterparty_kind": kind,
            "status": conv.status,
            "terminal": conv.terminal,
            "updated_at": conv.updated_at.isoformat(),
            "last_message": (
                {**last, "created_at": last["created_at"].isoformat()} if last else None
            ),
        }

    def list(self, request):
        return Response([self._serialize(c) for c in self.get_queryset()])

    def retrieve(self, request, pk=None):
        return Response(self._serialize(self.get_object()))

    @action(detail=True, methods=["get"])
    def messages(self, request, pk=None):
        conv = self.get_object()  # 404s if not a participant
        qs = (
            Message.objects.filter(conversation=conv)
            .filter(Q(status="sent") | Q(status="draft", author_id=request.user.id))
            .order_by("created_at", "id")
        )
        return Response(
            [
                {
                    "id": m.id,
                    "author_type": m.author_type,
                    "author_side": m.author_side,
                    "action": m.action,
                    "body": m.body,
                    "status": m.status,
                    "created_at": m.created_at.isoformat(),
                }
                for m in qs
            ]
        )

    @action(detail=True, methods=["get", "put"])
    def mandate(self, request, pk=None):
        """The current user's side-mandate for this thread — powers the auto-reply /
        autonomy toggle (P3.9). Seller edits the listing mandate; buyer their buy-box's."""
        conv = self.get_object()
        if request.method == "GET":
            return Response(dal._get_thread_mandate(conv.id, request.user.id))
        fields = {k: request.data.get(k) for k in ("auto_reply", "autonomy", "instructions")}
        return Response(dal._set_thread_mandate(conv.id, request.user.id, fields))

    @action(detail=True, methods=["post"], url_path="approve-draft")
    def approve_draft(self, request, pk=None):
        """Approve an `assist`/`confirm` agent draft → send it (the takeover)."""
        conv = self.get_object()
        msg_id = request.data.get("message_id")
        if msg_id is None:
            return Response({"detail": "message_id required"}, status=400)
        res = svc.approve_draft(request.user.id, int(msg_id))
        if "error" in res:
            return Response(res, status=404)
        # Broadcast the now-sent message and arm the counterparty's auto-responder.
        try:
            async_to_sync(get_channel_layer().group_send)(
                f"thread_{conv.id}",
                {
                    "type": "thread.message",
                    "data": {
                        "id": res["message_id"],
                        "conversation_id": conv.id,
                        "author_type": "agent",
                        "author_side": res.get("author_side"),
                        "action": res.get("action"),
                        "body": res.get("body"),
                    },
                },
            )
            inngest_client.send_sync(
                inngest.Event(
                    name="thread/inbound",
                    data={"conversation_id": conv.id, "inbound_message_id": res["message_id"]},
                )
            )
        except Exception as exc:  # noqa: BLE001 - the DB flip already happened
            log.warning("approve-draft broadcast/emit failed: %s", exc)
        return Response(res)
