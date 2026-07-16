"""
catalog REST — property dedup lookup + the user's own listings (multi-property
create, detail, on-demand valuation/comps, and the per-listing deal mandate).

Views are thin: they validate and delegate to `catalog.services` — the same seam
the copilot tools call, so the agent and the API stay in lockstep.
"""

from __future__ import annotations

from django.db.models import Q
from rest_framework import mixins, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from matching.engine import estimate_value, get_comps, rank_buyers_for_attrs

from . import services
from .models import Listing
from .serializers import (
    BuyBoxWriteSerializer,
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


def _num(value, cast):
    try:
        return cast(value) if value not in (None, "") else None
    except (TypeError, ValueError):
        return None


class PropertySearchView(APIView):
    """GET /api/properties/search?q=…&limit=… — closed-world address autocomplete
    (typeahead) over the known Property universe. Same service seam as the copilot's
    `search_properties` tool (agent == API)."""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        q = request.query_params.get("q", "")
        limit = _num(request.query_params.get("limit"), int) or 8
        return Response({"results": services.search_properties(q, limit)})


class BuyerRankView(APIView):
    """GET /api/buyers/rank?address=…&price=…&beds=…&sqft=…&condition=…&property_type=…
    &limit=… — the `/buyers` ad-hoc matcher (no listing persisted). Delegates to the same
    engine entry point the copilot's `find_buyers` tool uses (agent == API): address→geo
    via the known Property universe (no geocoder), then `rank_buyers_for_attrs`. An
    unresolvable address degrades to `ranked: []` with `resolved: false`, not an error."""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        address = (request.query_params.get("address") or "").strip()
        if not address:
            return Response({"detail": "address required"}, status=400)
        q = request.query_params
        geom = services.resolve_geo(address)
        result = rank_buyers_for_attrs(
            geom=geom,
            price=_num(q.get("price"), float),
            condition=_num(q.get("condition"), int),
            beds=_num(q.get("beds"), int),
            sqft=_num(q.get("sqft"), int),
            property_type=q.get("property_type") or None,
            seller_id=request.user.id,
            limit=_num(q.get("limit"), int) or 10,
        )
        result["resolved"] = geom is not None
        return Response(result)


class ListingViewSet(
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    mixins.CreateModelMixin,
    mixins.UpdateModelMixin,
    viewsets.GenericViewSet,
):
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        qs = (
            Listing.objects.select_related("seller")
            .order_by("-created_at")
            .prefetch_related("listingproperty_set__property", "media")
        )
        if self.action in ("list", "retrieve"):
            # Marketplace visibility: the user's own listings (any status) + everyone
            # else's active ones. ?mine=1 narrows the list back to own-only. Mutations
            # and the seller-only actions (valuation/comps/mandate) stay owner-scoped
            # below — and the detail serializer withholds the mandate from non-owners.
            if self.action == "list" and self.request.query_params.get("mine") in (
                "1",
                "true",
                "True",
            ):
                return qs.filter(seller=self.request.user)
            return qs.filter(Q(seller=self.request.user) | Q(status="active"))
        return qs.filter(seller=self.request.user)

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
        return Response(
            ListingDetailSerializer(detail, context={"request": request}).data, status=201
        )

    def update(self, request, *args, **kwargs):
        listing = self.get_object()  # 404s if not owned
        ser = ListingUpdateSerializer(data=request.data, partial=kwargs.get("partial", False))
        ser.is_valid(raise_exception=True)
        services.update_listing(listing, ser.validated_data)
        detail = self.get_queryset().get(id=listing.id)
        return Response(ListingDetailSerializer(detail, context={"request": request}).data)

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


class BuyBoxViewSet(viewsets.ViewSet):
    """The user's buy-boxes (criteria + inline deal-settings + geos), for `/settings ›
    Buy-boxes`. Thin: it validates and delegates to `catalog.services` — the same seam the
    copilot's buy-box tools call, so the agent and the API stay in lockstep. User-scoped:
    a user only ever sees/edits their own boxes (services filters by `buyer_id`; a foreign
    id returns an error dict → 404)."""

    permission_classes = [IsAuthenticated]

    def list(self, request):
        return Response(services.list_buy_boxes(request.user.id))

    def retrieve(self, request, pk=None):
        result = services.get_buy_box(request.user.id, int(pk))
        return Response(result, status=404 if "error" in result else 200)

    def create(self, request):
        ser = BuyBoxWriteSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        return Response(services.create_buy_box(request.user.id, ser.validated_data), status=201)

    def update(self, request, pk=None):
        ser = BuyBoxWriteSerializer(data=request.data, partial=True)
        ser.is_valid(raise_exception=True)
        result = services.update_buy_box(request.user.id, int(pk), ser.validated_data)
        return Response(result, status=404 if "error" in result else 200)

    def partial_update(self, request, pk=None):
        return self.update(request, pk=pk)

    def destroy(self, request, pk=None):
        result = services.delete_buy_box(request.user.id, int(pk))
        return Response(result, status=404 if "error" in result else 200)
