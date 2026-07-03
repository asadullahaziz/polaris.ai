"""
catalog — property / listing / listing_property / listing_media
(data_model_decisions Decision 1 & 8).

`property` = the canonical physical asset (one row per real-world home; KC seed
loads ~20k here as the comp universe). `listing` = a user's intent to dispo,
covering one (single) or many (package/portfolio) properties via the M2M
`listing_property`. v1 builds/demos `single` only; the schema is bundle-native.

TEXT columns → TextField (the DDL uses TEXT, app-enforced enums). CHAR(n) →
CharField(max_length=n). NUMERIC(p,s) → DecimalField. Geography → GeoDjango
fields (spatial_index on by default = the GiST index).
"""

from __future__ import annotations

from django.conf import settings
from django.contrib.gis.db import models

PROPERTY_TYPES = ["sfr", "duplex", "multifamily", "condo", "land"]
BUNDLE_TYPES = [("single", "single"), ("package", "package"), ("portfolio", "portfolio")]
LISTING_STATUSES = [
    ("draft", "draft"),
    ("active", "active"),
    ("under_contract", "under_contract"),
    ("paused", "paused"),
    ("closed", "closed"),
    ("withdrawn", "withdrawn"),
]
MEDIA_KINDS = [("photo", "photo"), ("document", "document")]


class Property(models.Model):
    """The canonical physical asset. ONE row per real-world property (dedup by
    parcel+county, address fallback)."""

    apn = models.TextField(null=True, blank=True)  # assessor parcel number
    county_fips = models.CharField(max_length=5, null=True, blank=True)
    address_norm = models.TextField()  # normalized for dedup
    address_raw = models.TextField()
    geom = models.PointField(geography=True, srid=4326, null=True, blank=True)

    # physical attributes used for buy-box matching / comping:
    property_type = models.TextField(null=True, blank=True)  # sfr, duplex, ...
    beds = models.SmallIntegerField(null=True, blank=True)
    baths = models.DecimalField(max_digits=3, decimal_places=1, null=True, blank=True)
    sqft = models.IntegerField(null=True, blank=True)
    lot_size_sqft = models.IntegerField(null=True, blank=True)
    year_built = models.SmallIntegerField(null=True, blank=True)
    yr_renovated = models.SmallIntegerField(null=True, blank=True)  # 0/NULL = never
    floors = models.DecimalField(max_digits=2, decimal_places=1, null=True, blank=True)
    sqft_above = models.IntegerField(null=True, blank=True)
    sqft_basement = models.IntegerField(null=True, blank=True)
    condition = models.SmallIntegerField(null=True, blank=True)  # KC 1–5
    grade = models.SmallIntegerField(null=True, blank=True)  # KC 1–13
    waterfront = models.BooleanField(null=True, blank=True)  # comp gate + premium
    view_rating = models.SmallIntegerField(null=True, blank=True)  # KC 0–4
    arv = models.DecimalField(  # DERIVED via comps (matching_and_data §3)
        max_digits=12, decimal_places=2, null=True, blank=True
    )
    last_sale_price = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    last_sale_date = models.DateField(null=True, blank=True)  # REBASED (§4.4)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "property"
        constraints = [
            # Same parcel in same county = same property.
            models.UniqueConstraint(
                fields=["county_fips", "apn"],
                condition=models.Q(apn__isnull=False),
                name="uniq_property_parcel",
            ),
            # Fallback dedup when APN is missing (synthetic addresses are unique).
            models.UniqueConstraint(fields=["address_norm"], name="uniq_property_address"),
        ]

    def __str__(self) -> str:  # pragma: no cover - trivial
        return self.address_raw or f"property:{self.pk}"


class Listing(models.Model):
    """A user's intent to dispo. Covers one (single) or many (package/portfolio)."""

    seller = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,  # DDL: no ON DELETE → protect the referenced user
        related_name="listings",
    )
    asking_price = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    bundle_type = models.TextField(default="single", choices=BUNDLE_TYPES)
    status = models.TextField(default="draft", choices=LISTING_STATUSES)
    created_at = models.DateTimeField(auto_now_add=True)

    properties = models.ManyToManyField(
        Property, through="ListingProperty", related_name="listings"
    )

    class Meta:
        db_table = "listing"

    def __str__(self) -> str:  # pragma: no cover - trivial
        return f"listing:{self.pk} ({self.bundle_type}/{self.status})"


class ListingProperty(models.Model):
    """M2M listing↔property. A property may appear in several listings (no per-user
    uniqueness); it just can't appear twice in ONE listing (the composite PK)."""

    pk = models.CompositePrimaryKey("listing_id", "property_id")
    listing = models.ForeignKey(Listing, on_delete=models.CASCADE)
    property = models.ForeignKey(Property, on_delete=models.PROTECT)
    asking_price = models.DecimalField(  # optional per-property price (package)
        max_digits=12, decimal_places=2, null=True, blank=True
    )

    class Meta:
        db_table = "listing_property"
        # FK on `property` already carries an index (= listing_property_property_idx).

    def __str__(self) -> str:  # pragma: no cover - trivial
        return f"lp:{self.listing_id}×{self.property_id}"


class ListingMedia(models.Model):
    """Photos/documents for a listing (URL only; bytes in object storage / MinIO)."""

    listing = models.ForeignKey(Listing, on_delete=models.CASCADE, related_name="media")
    kind = models.TextField(default="photo", choices=MEDIA_KINDS)
    url = models.TextField()
    sort_order = models.SmallIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "listing_media"
        indexes = [
            models.Index(fields=["listing", "sort_order"], name="listing_media_listing_idx"),
        ]

    def __str__(self) -> str:  # pragma: no cover - trivial
        return f"media:{self.pk} ({self.kind})"
