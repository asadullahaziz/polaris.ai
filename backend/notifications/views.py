"""
Notifications REST (P3) — the in-app notification feed the UI polls, plus mark-read.
In-app only (no email/SMS); one table, four trigger types (inbound_message /
outreach_received / approval_required / escalation).
"""

from __future__ import annotations

from django.utils import timezone
from rest_framework import mixins, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from .models import Notification
from .serializers import NotificationSerializer


class NotificationViewSet(mixins.ListModelMixin, viewsets.GenericViewSet):
    serializer_class = NotificationSerializer

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):  # drf-spectacular introspection
            return Notification.objects.none()
        return Notification.objects.filter(user=self.request.user).order_by("-created_at")

    def list(self, request, *args, **kwargs):
        qs = self.get_queryset()[:50]  # recent feed
        return Response(self.get_serializer(qs, many=True).data)

    @action(detail=True, methods=["post"])
    def read(self, request, pk=None):
        n = self.get_object()
        if n.read_at is None:
            n.read_at = timezone.now()
            n.save(update_fields=["read_at"])
        return Response({"status": "ok"})

    @action(detail=False, methods=["post"], url_path="read-all")
    def read_all(self, request):
        Notification.objects.filter(user=request.user, read_at__isnull=True).update(
            read_at=timezone.now()
        )
        return Response({"status": "ok"})
