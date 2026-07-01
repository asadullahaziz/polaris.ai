"""
Outreach REST (implementation_plan P2.3). The seller reviews the ranked shortlist
(persisted by `launch_outreach`) and approves or cancels the batch. Approval is the
batch-level send gate: it flips the campaign to `sending` and emits the Inngest
`outreach/approved` event — a fresh event, not a parked wait, so it survives a closed
tab (architecture §6).
"""

from __future__ import annotations

import logging

import inngest
from rest_framework import mixins, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from orchestration.client import inngest_client

from . import service
from .models import OutreachCampaign
from .serializers import OutreachCampaignSerializer

log = logging.getLogger(__name__)


class OutreachCampaignViewSet(
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    viewsets.GenericViewSet,
):
    serializer_class = OutreachCampaignSerializer

    def get_queryset(self):
        return (
            OutreachCampaign.objects.filter(seller=self.request.user)
            .order_by("-created_at")
            .prefetch_related(
                "recipients__recipient_user",
                "recipients__recipient_prospect",
                "listing__listingproperty_set__property",
            )
        )

    @action(detail=True, methods=["post"])
    def approve(self, request, pk=None):
        self.get_object()  # 404s if not the seller's campaign
        result = service.approve_campaign(request.user.id, int(pk))
        if "error" in result:
            return Response(result, status=409)
        # Emit the fan-out event (durable). Best-effort: the campaign is already
        # 'sending', so surface a warning rather than 500 if the dev server is down.
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
        result = service.cancel_campaign(request.user.id, int(pk))
        return Response(result, status=409 if "error" in result else 200)
