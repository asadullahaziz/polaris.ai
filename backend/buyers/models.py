"""
buyers — prospect / buy_box / buy_box_geo / purchase
(data_model_decisions Decision 2 & 3).

Two buyer classes: registered `app_user`s (with buy-boxes + agent) and dataset
`prospect`s (history only, one-way reachable). `purchase` = the primary behavioral
ranking signal, attached to a user OR a prospect (buyer-or-prospect pattern:
two nullable FKs + a CHECK that exactly one is set).
"""

from __future__ import annotations

from django.conf import settings
from django.contrib.gis.db import models
from django.contrib.postgres.fields import ArrayField

CHANNELS = [
    ("in_app", "in_app"),
    ("sms", "sms"),
    ("email", "email"),
    ("whatsapp", "whatsapp"),
]
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


class Prospect(models.Model):
    """Dataset contacts: NOT platform users, NO agent, one-way reachable."""

    full_name = models.TextField(null=True, blank=True)
    entity_name = models.TextField(null=True, blank=True)  # "Acme Holdings LLC"
    email = models.TextField(null=True, blank=True)
    phone = models.TextField(null=True, blank=True)
    whatsapp = models.TextField(null=True, blank=True)
    preferred_channel = models.TextField(default="email", choices=CHANNELS)
    cash_buyer = models.BooleanField(null=True, blank=True)  # derived signal
    signals = models.JSONField(default=dict, blank=True)  # {deal_count, price_band...}
    source = models.TextField(null=True, blank=True)  # seed/provenance
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "prospect"

    def __str__(self) -> str:  # pragma: no cover - trivial
        return self.full_name or self.entity_name or f"prospect:{self.pk}"


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


class Purchase(models.Model):
    """Past purchases: the behavioral ranking signal. buyer-or-prospect pattern."""

    buyer_user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="purchases",
    )
    buyer_prospect = models.ForeignKey(
        Prospect,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="purchases",
    )
    property = models.ForeignKey(
        "catalog.Property",
        on_delete=models.PROTECT,  # DDL: no ON DELETE
        null=True,
        blank=True,
        related_name="purchases",
    )

    # denormalized attrs — OPTIONAL when property is set (read via the join); kept for
    # purchases with no linked property row (a prospect's external history).
    price = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    property_type = models.TextField(null=True, blank=True)
    beds = models.SmallIntegerField(null=True, blank=True)
    city = models.TextField(null=True, blank=True)
    state_code = models.CharField(max_length=2, null=True, blank=True)
    zip = models.CharField(max_length=5, null=True, blank=True)
    geom = models.PointField(geography=True, srid=4326, null=True, blank=True)
    purchased_at = models.DateField(null=True, blank=True)  # REBASED (§4.4)
    cash_buyer = models.BooleanField(null=True, blank=True)
    disposition = models.TextField(null=True, blank=True, choices=DISPOSITIONS)
    source = models.TextField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "purchase"
        constraints = [
            # Exactly one of buyer_user / buyer_prospect is set.
            models.CheckConstraint(
                name="purchase_one_buyer",
                condition=(
                    models.Q(buyer_user__isnull=False, buyer_prospect__isnull=True)
                    | models.Q(buyer_user__isnull=True, buyer_prospect__isnull=False)
                ),
            ),
        ]

    def __str__(self) -> str:  # pragma: no cover - trivial
        return f"purchase:{self.pk} (${self.price})"
