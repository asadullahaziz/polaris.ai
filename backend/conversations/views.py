"""
Copilot conversation REST (implementation_plan P1.2) — the sidebar + history the
copilot UI reads; the live turn itself runs over the WebSocket (P1.3).
"""

from __future__ import annotations

from rest_framework import mixins, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from .models import Conversation, Message
from .serializers import ConversationSerializer, MessageSerializer


class CopilotConversationViewSet(
    mixins.ListModelMixin,
    mixins.CreateModelMixin,
    mixins.RetrieveModelMixin,
    mixins.UpdateModelMixin,  # rename (PATCH title)
    mixins.DestroyModelMixin,  # archive/delete a chat
    viewsets.GenericViewSet,
):
    serializer_class = ConversationSerializer

    def get_queryset(self):
        return Conversation.objects.filter(kind="copilot", owner=self.request.user).order_by(
            "-updated_at"
        )

    def perform_create(self, serializer):
        serializer.save(owner=self.request.user, kind="copilot")

    @action(detail=True, methods=["get"])
    def messages(self, request, pk=None):
        conv = self.get_object()  # 404s if not owned
        msgs = Message.objects.filter(conversation=conv, status="sent").order_by("created_at")
        return Response(MessageSerializer(msgs, many=True).data)
