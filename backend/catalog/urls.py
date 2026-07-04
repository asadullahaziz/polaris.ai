"""catalog URL routes: property dedup lookup + the listings API."""

from __future__ import annotations

from django.urls import path
from rest_framework.routers import DefaultRouter

from .views import BuyBoxViewSet, BuyerRankView, ListingViewSet, PropertyLookupView

app_name = "catalog"

router = DefaultRouter()
router.register("listings", ListingViewSet, basename="listing")
router.register("buy-boxes", BuyBoxViewSet, basename="buy-box")

urlpatterns = [
    path("properties/lookup", PropertyLookupView.as_view(), name="property-lookup"),
    path("buyers/rank", BuyerRankView.as_view(), name="buyer-rank"),
    *router.urls,
]
