"""
Ai REST — the copilot's own chats + transcripts, and the read view of agent memory.

The copilot *turn* runs over the WebSocket (`CopilotConsumer`); these endpoints back
the sidebar (list sessions), rehydrate a session's transcript on load, let a user
delete a session, and expose the private memory the copilot reads/writes. All
user-scoped (a user only ever sees their own rows).
"""

from __future__ import annotations

from rest_framework import mixins, viewsets
from rest_framework.decorators import action
from rest_framework.generics import ListAPIView
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from .models import AgentMemory, AiChat
from .serializers import AgentMemorySerializer, AiChatDetailSerializer, AiChatSummarySerializer


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
