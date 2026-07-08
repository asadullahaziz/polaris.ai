"""
Ai REST — the copilot's own chats + transcripts, and the read view of agent memory.

The copilot *turn* runs over the WebSocket (`CopilotConsumer`); these endpoints back
the sidebar (list sessions), rehydrate a session's transcript on load, let a user
delete a session, and expose the private memory the copilot reads/writes. All
user-scoped (a user only ever sees their own rows).
"""

from __future__ import annotations

import logging

import inngest
from django.conf import settings
from rest_framework import mixins, viewsets
from rest_framework.decorators import action
from rest_framework.generics import ListAPIView
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from orchestration.client import inngest_client
from polaris_agent import dal

from . import outreach_service
from .models import AgentMemory, AiChat, OutreachCampaign
from .serializers import (
    AgentMemorySerializer,
    AiChatDetailSerializer,
    AiChatSummarySerializer,
    OutreachCampaignSerializer,
)

log = logging.getLogger(__name__)


class AiChatViewSet(
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    mixins.DestroyModelMixin,
    viewsets.GenericViewSet,
):
    """List / retrieve (with transcript) / delete the user's copilot sessions."""

    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        qs = AiChat.objects.filter(owner=self.request.user).order_by("-updated_at")
        if self.action == "retrieve":
            qs = qs.prefetch_related("messages")
        return qs

    def get_serializer_class(self):
        return AiChatDetailSerializer if self.action == "retrieve" else AiChatSummarySerializer

    def retrieve(self, request, *args, **kwargs):
        """Rehydrate one session. Lazily expire a parked confirm nobody answered first, so a
        reopened chat shows a greyed 'expired' card (not a live one) and its composer isn't
        gated forever — no background job needed."""
        instance = self.get_object()
        if dal._expire_pending_confirm_if_stale(instance.id, settings.COPILOT_CONFIRM_TTL_SECONDS):
            instance = self.get_object()  # re-load so messages + pending_confirm are current
        return Response(self.get_serializer(instance).data)

    @action(detail=True, methods=["get"])
    def messages(self, request, pk=None):
        """Just the transcript for one session (oldest first)."""
        chat = self.get_object()  # 404s if not owned
        detail = AiChatDetailSerializer(chat)
        return Response(detail.data["messages"])


class AgentMemoryListView(ListAPIView):
    """The user's durable agent memory (optionally filtered by ?namespace=)."""

    permission_classes = [IsAuthenticated]
    serializer_class = AgentMemorySerializer

    def get_queryset(self):
        qs = AgentMemory.objects.filter(principal=self.request.user).order_by("-updated_at")
        namespace = self.request.query_params.get("namespace")
        if namespace:
            qs = qs.filter(namespace=namespace)
        return qs


class OutreachCampaignViewSet(
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    viewsets.GenericViewSet,
):
    """The seller's outreach campaigns + the ranked shortlist. Approval is the batch-level
    send gate: it flips the campaign to `sending` and emits the durable `outreach/approved`
    event — a fresh event, not a parked wait, so it survives a closed tab (architecture §6)."""

    permission_classes = [IsAuthenticated]
    serializer_class = OutreachCampaignSerializer

    def get_queryset(self):
        return (
            OutreachCampaign.objects.filter(seller=self.request.user)
            .order_by("-created_at")
            .prefetch_related(
                "recipients__recipient_user",
                "listing__listingproperty_set__property",
            )
        )

    @action(detail=True, methods=["post"])
    def approve(self, request, pk=None):
        self.get_object()  # 404s if not the seller's campaign
        result = outreach_service.approve_campaign(request.user.id, int(pk))
        if "error" in result:
            return Response(result, status=409)
        # Emit the fan-out event (durable). Best-effort: the campaign is already 'sending',
        # so surface a warning rather than 500 if the dev server is booting.
        try:
            inngest_client.send_sync(
                inngest.Event(name="outreach/approved", data={"campaign_id": int(pk)})
            )
        except Exception as exc:  # pragma: no cover - dev server may be booting
            log.warning("failed to emit outreach/approved: %s", exc)
            result["warning"] = "queued locally; fan-out event not delivered to Inngest"
        return Response(result)

    @action(detail=True, methods=["post"])
    def cancel(self, request, pk=None):
        self.get_object()
        result = outreach_service.cancel_campaign(request.user.id, int(pk))
        return Response(result, status=409 if "error" in result else 200)
