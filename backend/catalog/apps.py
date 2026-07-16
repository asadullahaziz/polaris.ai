from django.apps import AppConfig


class CatalogConfig(AppConfig):
    """Property, Listing, ListingProperty, ListingMedia, BuyBox, BuyBoxGeo, Sale, Mandate."""

    default_auto_field = "django.db.models.BigAutoField"
    name = "catalog"
