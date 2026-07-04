from django.apps import AppConfig


class CatalogConfig(AppConfig):
    """Listings & properties: property, listing, listing_property, listing_media (P1)."""

    default_auto_field = "django.db.models.BigAutoField"
    name = "catalog"
