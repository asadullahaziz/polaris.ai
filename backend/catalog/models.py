"""
Core domain models: Property, Listing, BuyBox, Sale, Mandate.

Property is the canonical physical asset — one row per real-world home; the seeded
comp universe backing valuation and assess_deal. Listing is a seller's intent to
sell one or more properties (single/package/portfolio via ListingProperty) — the
schema is bundle-native. BuyBox holds an investor's acquisition criteria, Sale a
buyer's purchase history (the behavioral ranking signal), and Mandate the private
per-deal parameters.

Enums are app-enforced TextField choices. GeoDjango geography fields carry a GiST
index by default.
"""

from __future__ import annotations

from django.conf import settings
from django.contrib.gis.db import models
from django.contrib.postgres.fields import ArrayField

# ---------------------------------------------------------------------------
# Enumerations (app-enforced)
# ---------------------------------------------------------------------------
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

STRATEGIES = [
    ("fix_flip", "fix_flip"),
    ("buy_hold", "buy_hold"),
    ("brrrr", "brrrr"),
    ("wholesale", "wholesale"),
    ("new_construction", "new_construction"),
]
BUY_BOX_SOURCES = [("manual", "manual"), ("ai_inferred", "ai_inferred")]
GEO_TYPES = [
    ("state", "state"),
    ("county", "county"),
    ("city", "city"),
    ("zip", "zip"),
    ("metro", "metro"),
    ("radius", "radius"),
    ("polygon", "polygon"),
]
GEO_MODES = [("include", "include"), ("exclude", "exclude")]
DISPOSITIONS = [
    ("flip", "flip"),
    ("hold", "hold"),
    ("brrrr", "brrrr"),
    ("unknown", "unknown"),
]


# ---------------------------------------------------------------------------
# Property / Listing / ListingProperty / ListingMedia
# ---------------------------------------------------------------------------
class Property(models.Model):
    """The canonical physical asset. One row per real-world property (dedup by
    parcel+county, address fallback). Matched/comp properties are shared read-only
    references — never mutated by the listing flow (protects the comp basis)."""

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
    arv = models.DecimalField(  # derived from comps
        max_digits=12, decimal_places=2, null=True, blank=True
    )
    last_sale_price = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    last_sale_date = models.DateField(null=True, blank=True)  # date-rebased by the seed
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
            # Fallback dedup when APN is missing (the fetch-existing dedup key).
            models.UniqueConstraint(fields=["address_norm"], name="uniq_property_address"),
        ]

    def __str__(self) -> str:  # pragma: no cover - trivial
        return self.address_raw or f"property:{self.pk}"


class Listing(models.Model):
    """A user's intent to dispo. Covers one (single) or many (package/portfolio)."""

    seller = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="listings",
    )
    title = models.TextField(blank=True, default="")
    description = models.TextField(blank=True, default="")
    asking_price = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    bundle_type = models.TextField(default="single", choices=BUNDLE_TYPES)
    status = models.TextField(default="draft", choices=LISTING_STATUSES)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    properties = models.ManyToManyField(
        Property, through="ListingProperty", related_name="listings"
    )

    class Meta:
        db_table = "listing"

    def __str__(self) -> str:  # pragma: no cover - trivial
        return f"listing:{self.pk} ({self.bundle_type}/{self.status})"


# The per-listing current-state override fields. A seller can restate these for THIS
# listing (post-renovation condition, a correction, an addition) without mutating the
# shared Property row (the comp basis). NULL on any field = inherit the base Property
# value; the engine values the effective subject = Property base ⊕ these overrides.
_LP_OVERRIDE_FIELDS = (
    "condition",
    "grade",
    "sqft",
    "beds",
    "baths",
    "year_built",
    "yr_renovated",
)


class ListingProperty(models.Model):
    """M2M listing↔property. A property may appear in several listings (no per-user
    uniqueness); it just can't appear twice in one listing (the composite PK).

    Besides the join, the row carries the seller's per-listing current-state overrides
    (`_LP_OVERRIDE_FIELDS`) — the mutable "what I'm selling now" layer that shadows the
    immutable base Property without touching it. See `effective_attrs`."""

    pk = models.CompositePrimaryKey("listing_id", "property_id")
    listing = models.ForeignKey(Listing, on_delete=models.CASCADE)
    property = models.ForeignKey(Property, on_delete=models.PROTECT)
    asking_price = models.DecimalField(  # optional per-property price (package)
        max_digits=12, decimal_places=2, null=True, blank=True
    )
    sort_order = models.SmallIntegerField(default=0)

    # --- current-state overrides (NULL = inherit base Property) ---
    condition = models.SmallIntegerField(null=True, blank=True)  # KC 1–5
    grade = models.SmallIntegerField(null=True, blank=True)  # KC 1–13
    sqft = models.IntegerField(null=True, blank=True)
    beds = models.SmallIntegerField(null=True, blank=True)
    baths = models.DecimalField(max_digits=3, decimal_places=1, null=True, blank=True)
    year_built = models.SmallIntegerField(null=True, blank=True)
    yr_renovated = models.SmallIntegerField(null=True, blank=True)

    class Meta:
        db_table = "listing_property"
        # FK on `property` already carries an index (= listing_property_property_idx).

    def __str__(self) -> str:  # pragma: no cover - trivial
        return f"lp:{self.listing_id}×{self.property_id}"

    def effective_attrs(self) -> dict:
        """The effective subject = base Property ⊕ per-listing overrides — the single
        overlay the engine and the responder DAL value against. An override wins only when
        explicitly set (`is not None`, so a legitimate 0 counts; NULL inherits the base).
        Identity/geo/waterfront/lot are never overridable and come straight from the base.

        `seller_stated_fields` lists which attributes are seller-restated for THIS listing —
        the deterministic provenance the disclosure layer uses to caveat any counterparty-
        facing figure derived from them."""
        p = self.property

        def ov(name):
            v = getattr(self, name)
            return v if v is not None else getattr(p, name)

        return {
            # base-only (never overridable):
            "pk": p.pk,
            "geom": p.geom,
            "property_type": p.property_type,
            "waterfront": p.waterfront,
            "lot_size_sqft": p.lot_size_sqft,
            # overridable current-state:
            "beds": ov("beds"),
            "baths": ov("baths"),
            "sqft": ov("sqft"),
            "grade": ov("grade"),
            "condition": ov("condition"),
            "year_built": ov("year_built"),
            "yr_renovated": ov("yr_renovated"),
            # provenance (which fields the seller restated):
            "seller_stated_fields": [
                f for f in _LP_OVERRIDE_FIELDS if getattr(self, f) is not None
            ],
        }


class ListingMedia(models.Model):
    """Photos/documents for a listing. The row stays URL-only; photo files live in
    MinIO/S3 via presigned browser PUTs (`catalog.storage`), and foreign URLs
    (seed images, legacy pasted links) are equally valid."""

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


# ---------------------------------------------------------------------------
# BuyBox / BuyBoxGeo
# ---------------------------------------------------------------------------
class BuyBox(models.Model):
    """An investor's acquisition criteria. A buyer has many; one may be primary."""

    buyer = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="buy_boxes"
    )
    name = models.TextField()  # "Dallas flips", "TX rentals"
    is_primary = models.BooleanField(default=False)
    is_active = models.BooleanField(default=True)
    source = models.TextField(default="manual", choices=BUY_BOX_SOURCES)
    strategy = models.TextField(choices=STRATEGIES)

    price_min = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    price_max = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    arv_min = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    arv_max = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    beds_min = models.SmallIntegerField(null=True, blank=True)
    baths_min = models.DecimalField(max_digits=3, decimal_places=1, null=True, blank=True)
    sqft_min = models.IntegerField(null=True, blank=True)
    sqft_max = models.IntegerField(null=True, blank=True)
    year_built_min = models.SmallIntegerField(null=True, blank=True)
    lot_size_min = models.IntegerField(null=True, blank=True)
    max_rehab_cost = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    property_types = ArrayField(models.TextField(), default=list, blank=True)
    condition_levels = ArrayField(models.TextField(), default=list, blank=True)

    # strategy-dependent return targets (avoids many sparse columns):
    # {"cap_rate_min":7, "coc_min":10, "roi_min":18, "cash_flow_min":250, "min_spread":30000}
    target_metrics = models.JSONField(default=dict, blank=True)

    funding_type = models.TextField(null=True, blank=True)  # cash | hard_money | conventional
    close_days = models.SmallIntegerField(null=True, blank=True)
    notify_settings = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "buy_box"
        constraints = [
            # At most one primary box per buyer.
            models.UniqueConstraint(
                fields=["buyer"],
                condition=models.Q(is_primary=True),
                name="one_primary_box_per_buyer",
            ),
        ]

    def __str__(self) -> str:  # pragma: no cover - trivial
        return f"buy_box:{self.pk} ({self.name})"


class BuyBoxGeo(models.Model):
    """Geography for a buy box — many per box, mixed types (place / radius / polygon)."""

    buy_box = models.ForeignKey(BuyBox, on_delete=models.CASCADE, related_name="geos")
    geo_type = models.TextField(choices=GEO_TYPES)
    mode = models.TextField(default="include", choices=GEO_MODES)

    # named-place targeting:
    state_code = models.CharField(max_length=2, null=True, blank=True)
    county_fips = models.CharField(max_length=5, null=True, blank=True)
    city = models.TextField(null=True, blank=True)
    zip = models.CharField(max_length=5, null=True, blank=True)

    # radius targeting (geo_type = radius):
    center = models.PointField(geography=True, srid=4326, null=True, blank=True)
    radius_mi = models.DecimalField(max_digits=6, decimal_places=2, null=True, blank=True)

    # polygon targeting (geo_type = polygon):
    area = models.PolygonField(geography=True, srid=4326, null=True, blank=True)

    class Meta:
        db_table = "buy_box_geo"
        # GiST indexes on `center`/`area` are created by GeoDjango (spatial_index).

    def __str__(self) -> str:  # pragma: no cover - trivial
        return f"buy_box_geo:{self.pk} ({self.geo_type}/{self.mode})"


# ---------------------------------------------------------------------------
# Sale — the primary behavioral ranking signal.
# ---------------------------------------------------------------------------
class Sale(models.Model):
    """A past purchase by a registered buyer: the behavioral ranking signal."""

    buyer = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="sales",
    )
    property = models.ForeignKey(
        Property,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="sales",
    )

    # denormalized attrs — optional when property is set (read via the join); kept for
    # sales with no linked property row (external history).
    price = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    property_type = models.TextField(null=True, blank=True)
    beds = models.SmallIntegerField(null=True, blank=True)
    city = models.TextField(null=True, blank=True)
    state_code = models.CharField(max_length=2, null=True, blank=True)
    zip = models.CharField(max_length=5, null=True, blank=True)
    geom = models.PointField(geography=True, srid=4326, null=True, blank=True)
    purchased_at = models.DateField(null=True, blank=True)  # date-rebased by the seed
    cash_buyer = models.BooleanField(null=True, blank=True)
    disposition = models.TextField(null=True, blank=True, choices=DISPOSITIONS)
    source = models.TextField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "sale"
        indexes = [
            models.Index(fields=["buyer"], name="sale_buyer_idx"),
        ]

    def __str__(self) -> str:  # pragma: no cover - trivial
        return f"sale:{self.pk} (${self.price})"


# ---------------------------------------------------------------------------
# Mandate — pure per-deal parameters (governance knobs live on UserProfile).
# ---------------------------------------------------------------------------
class Mandate(models.Model):
    """Governs one deal context: a seller's listing XOR a buyer's buy-box."""

    listing = models.ForeignKey(
        Listing,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="mandates",
    )
    buy_box = models.ForeignKey(
        BuyBox,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="mandates",
    )

    floor_price = models.DecimalField(  # seller floor
        max_digits=12, decimal_places=2, null=True, blank=True
    )
    ceiling_price = models.DecimalField(  # buyer ceiling (may mirror buy_box.price_max)
        max_digits=12, decimal_places=2, null=True, blank=True
    )
    must_haves = ArrayField(models.TextField(), default=list, blank=True)
    availability_window = models.TextField(null=True, blank=True)
    instructions = models.TextField(default="", blank=True)  # free-text the LLM reads
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "mandate"
        constraints = [
            models.CheckConstraint(
                name="mandate_one_target",
                condition=(
                    models.Q(listing__isnull=False, buy_box__isnull=True)
                    | models.Q(listing__isnull=True, buy_box__isnull=False)
                ),
            ),
            models.UniqueConstraint(
                fields=["listing"],
                condition=models.Q(listing__isnull=False),
                name="uniq_mandate_listing",
            ),
            models.UniqueConstraint(
                fields=["buy_box"],
                condition=models.Q(buy_box__isnull=False),
                name="uniq_mandate_buy_box",
            ),
        ]

    def __str__(self) -> str:  # pragma: no cover - trivial
        target = f"listing:{self.listing_id}" if self.listing_id else f"buy_box:{self.buy_box_id}"
        return f"mandate:{self.pk} ({target})"
