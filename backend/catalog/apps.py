from django.apps import AppConfig


class CatalogConfig(AppConfig):
    """Property, Listing, ListingProperty, ListingMedia, BuyBox, BuyBoxGeo, Sale, Mandate.

    Models + engine wiring land in P1."""

    default_auto_field = "django.db.models.BigAutoField"
    name = "catalog"
