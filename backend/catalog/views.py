"""
catalog REST — property dedup lookup + the user's own listings (multi-property
create, detail, on-demand valuation/comps, and the per-listing deal mandate).

Views are thin: they validate and delegate to `catalog.services` — the same seam
the copilot tools call, so the agent and the API stay in lockstep.
"""

from __future__ import annotations

from django.conf import settings
from django.db.models import Q
from drf_spectacular.utils import extend_schema
from rest_framework import mixins, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView

from matching.engine import (
    estimate_current_value,
    estimate_value,
    get_comps,
    rank_buyers_for_attrs,
)

from . import services, storage
from .models import Listing
from .serializers import (
    BuyBoxWriteSerializer,
    ListingCreateSerializer,
    ListingDetailSerializer,
    ListingMediaAttachSerializer,
    ListingPropertyOverrideSerializer,
    ListingSummarySerializer,
    ListingUpdateSerializer,
    MandateSerializer,
    MediaPresignRequestSerializer,
    MediaPresignResponseSerializer,
)


class MediaPresignView(APIView):
    """POST /api/uploads/presign → a presigned direct-to-storage PUT for a listing
    photo. The object key is user-scoped + random (listings/{user_id}/{uuid}.{ext})
    and the ContentType is signed, so the browser must PUT with exactly the
    returned headers. The upload itself never touches this backend."""

    permission_classes = [IsAuthenticated]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "uploads"

    @extend_schema(
        request=MediaPresignRequestSerializer,
        responses={200: MediaPresignResponseSerializer},
    )
    def post(self, request):
        ser = MediaPresignRequestSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        ct = ser.validated_data["content_type"]
        key = storage.build_key(request.user.id, ct)
        return Response(
            {
                "upload_url": storage.presign_put(key, ct),
                "public_url": storage.public_url(key),
                "key": key,
                "headers": {"Content-Type": ct},
                "expires_in": settings.STORAGE_PRESIGN_EXPIRY,
                "max_bytes": settings.STORAGE_MAX_UPLOAD_MB * 1024 * 1024,
            }
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

    def _first_lp(self, listing):
        """First listing-property (base Property + the seller's per-listing overrides)."""
        return listing.listingproperty_set.select_related("property").order_by("sort_order").first()

    @action(detail=True, methods=["get"])
    def valuation(self, request, pk=None):
        """On-demand market value + comps for the listing (arv=1 for after-repair), valued
        on the effective subject (base ⊕ seller overrides). `current_value` is the
        condition-aware as-is number that moves with a renovation."""
        lp = self._first_lp(self.get_object())
        if lp is None or lp.property is None:
            return Response({"detail": "listing has no property"}, status=400)
        eff = lp.effective_attrs()
        arv = request.query_params.get("arv") in ("1", "true", "True")
        ev = estimate_value(eff, arv=arv)
        ev["comps"] = ev["comps"][:8]
        ev["current_value"] = estimate_current_value(eff)
        return Response(ev)

    @action(detail=True, methods=["get"])
    def comps(self, request, pk=None):
        lp = self._first_lp(self.get_object())
        if lp is None or lp.property is None:
            return Response({"detail": "listing has no property"}, status=400)
        return Response(get_comps(lp.effective_attrs()))

    @action(detail=True, methods=["get", "put"])
    def mandate(self, request, pk=None):
        listing = self.get_object()  # 404s if not owned
        if request.method == "GET":
            return Response(services.get_mandate_for_listing(listing))
        ser = MandateSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        return Response(services.set_mandate_for_listing(listing, ser.validated_data))

    @action(detail=True, methods=["post"], url_path="media")
    def media(self, request, pk=None):
        """POST /api/listings/{id}/media/ — attach photos to an existing listing."""
        listing = self.get_object()  # 404s if not owned
        ser = ListingMediaAttachSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        services.add_listing_media(listing, ser.validated_data["media"])
        detail = self.get_queryset().get(id=listing.id)
        return Response(
            ListingDetailSerializer(detail, context={"request": request}).data, status=201
        )

    @action(
        detail=True,
        methods=["delete"],
        url_path=r"media/(?P<media_id>\d+)",
        url_name="media-delete",
    )
    def delete_media(self, request, pk=None, media_id=None):
        """DELETE /api/listings/{id}/media/{media_id}/ — remove a photo, with
        best-effort storage cleanup for our-bucket URLs."""
        listing = self.get_object()  # 404s if not owned
        if not services.remove_listing_media(listing, int(media_id)):
            return Response({"detail": "media not found"}, status=404)
        detail = self.get_queryset().get(id=listing.id)
        return Response(ListingDetailSerializer(detail, context={"request": request}).data)

    @extend_schema(request=ListingPropertyOverrideSerializer, responses=ListingDetailSerializer)
    @action(detail=True, methods=["patch"], url_path=r"properties/(?P<property_id>\d+)")
    def property_overrides(self, request, pk=None, property_id=None):
        """PATCH /api/listings/{id}/properties/{property_id}/ — set the seller's
        per-listing current-state overrides for one property (post-reno condition, a
        correction, an addition). Never mutates the shared Property. Owner-scoped via
        get_object(); delegates to the same services seam the copilot tool uses."""
        listing = self.get_object()  # 404s if not owned
        ser = ListingPropertyOverrideSerializer(data=request.data, partial=True)
        ser.is_valid(raise_exception=True)
        result = services.update_listing_property(listing, int(property_id), ser.validated_data)
        if "error" in result:
            return Response(result, status=404)
        detail = self.get_queryset().get(id=listing.id)
        return Response(ListingDetailSerializer(detail, context={"request": request}).data)


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
