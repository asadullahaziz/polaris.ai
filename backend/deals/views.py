"""
Deals REST — the pipeline the UI's /deals table reads: one row per (listing, buyer)
deal the requester is a party to, plus a manual stage override. Stage auto-transitions
live in `deals/service.py`; this surface only lists and overrides. Plain-dict
responses, same drf-spectacular posture as `chat/views.py`.
"""

from __future__ import annotations

from django.db.models import Prefetch, Q
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import extend_schema, extend_schema_view
from rest_framework import serializers, viewsets
from rest_framework.response import Response

from . import service
from .models import Deal


class _DealDictSerializer(serializers.Serializer):
    """Placeholder so drf-spectacular has a serializer to probe; the views return plain
    dicts typed via @extend_schema(responses=OBJECT)."""


def _listing_summary(listing) -> dict:
    lps = getattr(listing, "listing_properties_all", [])
    address = lps[0].property.address_raw if lps and lps[0].property is not None else None
    return {
        "id": listing.id,
        "title": listing.title,
        "address": address,
        "asking_price": float(listing.asking_price) if listing.asking_price is not None else None,
        "status": listing.status,
    }


def serialize_deal(deal: Deal, viewer_id: int) -> dict:
    side = "selling" if deal.seller_id == viewer_id else "buying"
    counterparty = deal.buyer if side == "selling" else deal.seller
    # ISO strings, not datetimes: this dict also crosses the copilot tool boundary,
    # where a raw datetime would break JSON serialization.
    return {
        "id": deal.id,
        "side": side,
        "stage": deal.stage,
        "stage_changed_at": deal.stage_changed_at.isoformat(),
        "listing": _listing_summary(deal.listing),
        "counterparty": {
            "id": counterparty.id,
            "name": counterparty.full_name or counterparty.email,
        },
        "last_offer_by_buyer": (
            float(deal.last_offer_by_buyer) if deal.last_offer_by_buyer is not None else None
        ),
        "last_offer_by_seller": (
            float(deal.last_offer_by_seller) if deal.last_offer_by_seller is not None else None
        ),
        "agreed_price": float(deal.agreed_price) if deal.agreed_price is not None else None,
        "chat_id": deal.chat_id,
        "created_at": deal.created_at.isoformat(),
        "updated_at": deal.updated_at.isoformat(),
    }


def _base_queryset(user_id: int):
    from catalog.models import ListingProperty

    return (
        Deal.objects.filter(Q(buyer_id=user_id) | Q(seller_id=user_id))
        .select_related("listing", "buyer", "seller")
        .prefetch_related(
            Prefetch(
                "listing__listingproperty_set",
                queryset=ListingProperty.objects.select_related("property").order_by("sort_order"),
                to_attr="listing_properties_all",
            )
        )
        .order_by("-updated_at")
    )


@extend_schema_view(
    list=extend_schema(responses=OpenApiTypes.OBJECT),
    partial_update=extend_schema(responses=OpenApiTypes.OBJECT),
)
class DealViewSet(viewsets.GenericViewSet):
    serializer_class = _DealDictSerializer

    def list(self, request):
        qs = _base_queryset(request.user.id)
        side = request.query_params.get("side")
        if side == "selling":
            qs = qs.filter(seller_id=request.user.id)
        elif side == "buying":
            qs = qs.filter(buyer_id=request.user.id)
        stage = request.query_params.get("stage")
        if stage:
            qs = qs.filter(stage=stage)
        listing = request.query_params.get("listing")
        if listing:
            qs = qs.filter(listing_id=int(listing))
        return Response([serialize_deal(d, request.user.id) for d in qs[:200]])

    def partial_update(self, request, pk=None):
        """Manual stage override — any stage, any direction (the human corrects the CRM)."""
        deal = _base_queryset(request.user.id).filter(pk=pk).first()
        if deal is None:
            return Response({"detail": "not found"}, status=404)
        stage = request.data.get("stage")
        if stage not in service.ALL_STAGES:
            return Response(
                {"detail": f"stage must be one of {list(service.ALL_STAGES)}"}, status=400
            )
        service.set_stage_manual(deal, stage)
        return Response(serialize_deal(deal, request.user.id))
