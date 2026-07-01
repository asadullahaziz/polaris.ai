"""
Shared context store REST (implementation_plan P1.2, features §E #22): the UI reads
and writes the SAME `agent_memory` the agent's read_memory/write_memory tools use.
"""

from __future__ import annotations

from rest_framework import mixins, viewsets

from .models import AgentMemory
from .serializers import MemorySerializer


class MemoryViewSet(
    mixins.ListModelMixin,
    mixins.CreateModelMixin,
    mixins.DestroyModelMixin,
    viewsets.GenericViewSet,
):
    serializer_class = MemorySerializer

    def get_queryset(self):
        qs = AgentMemory.objects.filter(principal=self.request.user).order_by("-updated_at")
        ns = self.request.query_params.get("namespace")
        return qs.filter(namespace=ns) if ns else qs

    def perform_create(self, serializer):
        serializer.save(principal=self.request.user)
