"""
catalog REST — property dedup lookup + the user's own listings (multi-property
create, detail, on-demand valuation/comps, and the per-listing deal mandate).

Views are thin: they validate and delegate to `catalog.services` (the same seam
the P2 copilot tools call), so the agent and the API stay in lockstep.
"""

from __future__ import annotations

from rest_framework import mixins, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from matching.engine import estimate_value, get_comps

from . import services
from .models import Listing
from .serializers import (
    ListingCreateSerializer,
    ListingDetailSerializer,
    ListingSummarySerializer,
    ListingUpdateSerializer,
    MandateSerializer,
)


class PropertyLookupView(APIView):
    """GET /api/properties/lookup?address=… → fetch-existing dedup (read-only)."""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        address = request.query_params.get("address", "")
        return Response(services.lookup_property(address))


class ListingViewSet(
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    mixins.CreateModelMixin,
    mixins.UpdateModelMixin,
    viewsets.GenericViewSet,
):
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        return (
            Listing.objects.filter(seller=self.request.user)
            .order_by("-created_at")
            .prefetch_related("listingproperty_set__property", "media")
        )

    def get_serializer_class(self):
        if self.action == "list":
            return ListingSummarySerializer
        if self.action == "create":
            return ListingCreateSerializer
        if self.action in ("update", "partial_update"):
            return ListingUpdateSerializer
        return ListingDetailSerializer

    def create(self, request, *args, **kwargs):
        ser = ListingCreateSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        listing = services.create_listing(request.user, ser.validated_data)
        detail = self.get_queryset().get(id=listing.id)
        return Response(ListingDetailSerializer(detail).data, status=201)

    def update(self, request, *args, **kwargs):
        listing = self.get_object()  # 404s if not owned
        ser = ListingUpdateSerializer(data=request.data, partial=kwargs.get("partial", False))
        ser.is_valid(raise_exception=True)
        services.update_listing(listing, ser.validated_data)
        detail = self.get_queryset().get(id=listing.id)
        return Response(ListingDetailSerializer(detail).data)

    def _first_property(self, listing):
        lp = listing.listingproperty_set.select_related("property").order_by("sort_order").first()
        return lp.property if lp else None

    @action(detail=True, methods=["get"])
    def valuation(self, request, pk=None):
        """On-demand market value + comps for the listing (arv=1 for after-repair)."""
        prop = self._first_property(self.get_object())
        if prop is None:
            return Response({"detail": "listing has no property"}, status=400)
        arv = request.query_params.get("arv") in ("1", "true", "True")
        ev = estimate_value(prop, arv=arv)
        ev["comps"] = ev["comps"][:8]
        return Response(ev)

    @action(detail=True, methods=["get"])
    def comps(self, request, pk=None):
        prop = self._first_property(self.get_object())
        if prop is None:
            return Response({"detail": "listing has no property"}, status=400)
        return Response(get_comps(prop))

    @action(detail=True, methods=["get", "put"])
    def mandate(self, request, pk=None):
        listing = self.get_object()  # 404s if not owned
        if request.method == "GET":
            return Response(services.get_mandate_for_listing(listing))
        ser = MandateSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        return Response(services.set_mandate_for_listing(listing, ser.validated_data))
