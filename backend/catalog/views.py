"""
Listing REST (implementation_plan P1.2): the user's own listings, intake, an
on-demand valuation, and the per-listing mandate — the same mandate the agent's
`set_mandate` / `check_mandate` tools read/write (shared context store).
"""

from __future__ import annotations

from rest_framework import mixins, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from agent_context.serializers import MandateSerializer
from matching.engine import estimate_value, get_comps
from polaris_agent import dal

from .models import Listing
from .serializers import ListingIntakeSerializer, ListingSerializer


class ListingViewSet(
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    mixins.CreateModelMixin,
    viewsets.GenericViewSet,
):
    def get_queryset(self):
        return (
            Listing.objects.filter(seller=self.request.user)
            .order_by("-created_at")
            .prefetch_related("listingproperty_set__property")
        )

    def get_serializer_class(self):
        return ListingIntakeSerializer if self.action == "create" else ListingSerializer

    def create(self, request, *args, **kwargs):
        ser = ListingIntakeSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        res = dal._create_listing_from_fields(request.user.id, ser.validated_data)
        listing = self.get_queryset().get(id=res["listing_id"])
        return Response(ListingSerializer(listing).data, status=201)

    def _property(self, listing):
        lp = listing.listingproperty_set.first()
        return lp.property if lp else None

    @action(detail=True, methods=["get"])
    def valuation(self, request, pk=None):
        """On-demand market value + comps for the listing (arv=1 for after-repair)."""
        prop = self._property(self.get_object())
        if prop is None:
            return Response({"detail": "listing has no property"}, status=400)
        arv = request.query_params.get("arv") in ("1", "true", "True")
        ev = estimate_value(prop, arv=arv)
        ev["comps"] = ev["comps"][:8]
        return Response(ev)

    @action(detail=True, methods=["get"])
    def comps(self, request, pk=None):
        prop = self._property(self.get_object())
        if prop is None:
            return Response({"detail": "listing has no property"}, status=400)
        return Response(get_comps(prop))

    @action(detail=True, methods=["get", "put"])
    def mandate(self, request, pk=None):
        listing = self.get_object()  # 404s if not owned
        if request.method == "GET":
            return Response(dal._get_mandate_for_listing(listing.id, request.user.id))
        ser = MandateSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        return Response(
            dal._set_mandate_for_listing(listing.id, request.user.id, ser.validated_data)
        )
