"""
seed_kc — the demo-data contract (matching_and_data §4), ported to the v2 schema.

Real comps, synthetic buyers — **registered users only** (v2 removed prospects):
  * Every King County sale → a `catalog.Property` row (the ~20k comp universe, §4.2).
  * ~40 synthesized investor personas as real email users — ~25 "history only"
    (the ex-prospects) + ~15 with a buy-box + mandate — with sampled `Sale` history.
  * ~15 active listings under ~3 seed sellers, priced below market for real wholesale
    spread (§4.4), each with a per-listing mandate.

Two hard requirements:
  * **Date rebase (§4.4):** the source sale window is linearly remapped onto the last
    ~24 months ending at the demo date, so `recency` is meaningful.
  * **Idempotent / re-runnable:** properties upsert via bulk ignore_conflicts; the
    behavioral layer is guarded by a `Sale(source=seed_kc)` sentinel so a re-run is a
    no-op. `--reset` truncates seed-owned rows first.

Determinism: a fixed-seed RNG so the demo's qualify/hold/decline divergence reproduces.
"""

from __future__ import annotations

import csv
import datetime as dt
import random
from decimal import ROUND_HALF_UP, Decimal

from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.gis.geos import Point
from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from catalog.models import (
    BuyBox,
    BuyBoxGeo,
    Listing,
    ListingProperty,
    Mandate,
    Property,
    Sale,
)

SEED_SOURCE = "seed_kc"
COUNTY_FIPS = "53033"
SEED_PASSWORD = "polaris123"  # dev/demo only; documented in CLAUDE.md
RNG_SEED = 1337
REBASE_MONTHS = 24
N_CLUSTERS = 8
N_REGISTERED = 15
N_PROSPECTS = 25  # ex-prospects, now registered users with history only
N_SELLERS = 3
N_LISTINGS = 15
LISTING_DISCOUNT = Decimal("0.80")  # asking below market → wholesale spread

STRATEGIES = ["fix_flip", "buy_hold", "brrrr"]
DISPOSITION_BY_STRATEGY = {"fix_flip": "flip", "buy_hold": "hold", "brrrr": "brrrr"}
CONDITION_LEVELS_BY_STRATEGY = {
    "fix_flip": ["full_gut", "cosmetic"],
    "buy_hold": ["turnkey", "cosmetic"],
    "brrrr": ["full_gut", "cosmetic"],
}
TARGET_METRICS_BY_STRATEGY = {
    "fix_flip": {"min_spread": 40000, "roi_min": 20},
    "buy_hold": {"cap_rate_min": 6, "cash_flow_min": 200},
    "brrrr": {"coc_min": 12, "min_spread": 30000},
}
# A few readable city labels for common KC zips (addresses are synthetic; §5 caveat).
ZIP_CITY = {
    "98103": "Seattle",
    "98115": "Seattle",
    "98117": "Seattle",
    "98118": "Seattle",
    "98133": "Seattle",
    "98052": "Redmond",
    "98034": "Kirkland",
    "98006": "Bellevue",
    "98038": "Maple Valley",
    "98042": "Kent",
    "98023": "Federal Way",
    "98059": "Renton",
}
STREETS = ["Alder", "Maple", "Cedar", "Rainier", "Madison", "Pine", "Union", "Cherry"]


def _q2(x) -> Decimal:
    return Decimal(x).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def _i(v):
    """Null-safe int (KC CSV has occasional empty cells)."""
    v = (v or "").strip()
    return int(float(v)) if v else None


def _f(v):
    v = (v or "").strip()
    return float(v) if v else None


class Command(BaseCommand):
    help = "Seed the King County comp universe + synthetic investor personas + listings."

    def add_arguments(self, parser):
        parser.add_argument(
            "--reset",
            action="store_true",
            help="Delete all seed-owned rows first, then reseed (fresh dates).",
        )

    def handle(self, *args, **opts):
        self.rng = random.Random(RNG_SEED)
        if opts["reset"]:
            self._reset()

        rows = self._parse_and_rebase()
        self._load_properties(rows)

        User = get_user_model()
        if Sale.objects.filter(source=SEED_SOURCE).exists():
            self.stdout.write(
                self.style.WARNING(
                    "behavioral seed already present — skipping personas/listings "
                    "(use --reset to rebuild). Properties are idempotent."
                )
            )
            self._summary()
            return

        # apn (== KC id) → property pk, for linking sales/listings.
        pk_by_apn = dict(Property.objects.filter(county_fips=COUNTY_FIPS).values_list("apn", "id"))
        clusters = self._build_clusters(rows, pk_by_apn)
        self._seed_behavioral(clusters, User)
        self._summary()

    # ---- parse + date rebase --------------------------------------------------
    def _parse_and_rebase(self) -> list[dict]:
        path = settings.BASE_DIR / "seed" / "data" / "king_county_sales.csv"
        latest: dict[str, dict] = {}  # dedup repeated id → latest sale
        with open(path, newline="") as f:
            for r in csv.DictReader(f):
                # Skip rows missing the comp essentials (price + coordinates + date).
                if not (
                    r.get("price", "").strip()
                    and r.get("lat", "").strip()
                    and r.get("long", "").strip()
                    and r.get("date", "").strip()
                ):
                    continue
                d = dt.date(int(r["date"][:4]), int(r["date"][4:6]), int(r["date"][6:8]))
                prev = latest.get(r["id"])
                if prev is None or d > prev["_date"]:
                    r["_date"] = d
                    latest[r["id"]] = r
        rows = list(latest.values())

        src_min = min(r["_date"] for r in rows)
        src_max = max(r["_date"] for r in rows)
        span = (src_max - src_min).days or 1
        dst_max = timezone.now().date()
        dst_min = dst_max - dt.timedelta(days=int(REBASE_MONTHS * 30.44))
        dst_span = (dst_max - dst_min).days
        for r in rows:
            frac = (r["_date"] - src_min).days / span
            r["_rebased"] = dst_min + dt.timedelta(days=round(frac * dst_span))
        self.stdout.write(
            f"parsed {len(rows)} unique properties; dates {src_min}→{src_max} "
            f"rebased to {dst_min}→{dst_max}"
        )
        return rows

    # ---- load the comp universe ----------------------------------------------
    def _load_properties(self, rows: list[dict]) -> None:
        objs = []
        for r in rows:
            lon, lat = float(r["long"]), float(r["lat"])
            objs.append(
                Property(
                    apn=r["id"],
                    county_fips=COUNTY_FIPS,
                    address_norm=f"kc:{r['id']}",
                    address_raw=f"KC parcel {r['id']} (zip {r['zipcode']})",
                    geom=Point(lon, lat, srid=4326),
                    property_type="sfr",
                    beds=_i(r["bedrooms"]),
                    baths=(
                        Decimal(str(_f(r["bathrooms"]))).quantize(
                            Decimal("0.1"), rounding=ROUND_HALF_UP
                        )
                        if _f(r["bathrooms"]) is not None
                        else None
                    ),
                    sqft=_i(r["sqft_living"]),
                    lot_size_sqft=_i(r["sqft_lot"]),
                    year_built=_i(r["yr_built"]),
                    yr_renovated=_i(r["yr_renovated"]),
                    floors=(Decimal(str(_f(r["floors"]))) if _f(r["floors"]) is not None else None),
                    sqft_above=_i(r["sqft_above"]),
                    sqft_basement=_i(r["sqft_basement"]),
                    condition=_i(r["condition"]),
                    grade=_i(r["grade"]),
                    waterfront=(
                        bool(_i(r["waterfront"])) if _i(r["waterfront"]) is not None else None
                    ),
                    view_rating=_i(r["view"]),
                    last_sale_price=_q2(r["price"]),
                    last_sale_date=r["_rebased"],
                )
            )
        before = Property.objects.filter(county_fips=COUNTY_FIPS).count()
        Property.objects.bulk_create(objs, batch_size=2000, ignore_conflicts=True)
        after = Property.objects.filter(county_fips=COUNTY_FIPS).count()
        self.stdout.write(f"properties: {after} total ({after - before} newly inserted)")

    # ---- cluster the pool by the densest zips --------------------------------
    def _build_clusters(self, rows: list[dict], pk_by_apn: dict) -> list[dict]:
        by_zip: dict[str, list[dict]] = {}
        for r in rows:
            pk = pk_by_apn.get(r["id"])
            if pk is None:
                continue
            r["_pk"] = pk
            by_zip.setdefault(r["zipcode"], []).append(r)
        top = sorted(by_zip.items(), key=lambda kv: len(kv[1]), reverse=True)[:N_CLUSTERS]
        clusters = []
        for z, zrows in top:
            prices = sorted(float(r["price"]) for r in zrows)
            lat = sum(float(r["lat"]) for r in zrows) / len(zrows)
            lon = sum(float(r["long"]) for r in zrows) / len(zrows)
            clusters.append(
                {
                    "zip": z,
                    "rows": zrows,
                    "centroid": (lon, lat),
                    "p25": prices[len(prices) // 4],
                    "p75": prices[len(prices) * 3 // 4],
                }
            )
        return clusters

    # ---- personas (users), sales, buy-boxes, listings ------------------------
    @transaction.atomic
    def _seed_behavioral(self, clusters: list[dict], User) -> None:
        sales: list[Sale] = []

        def sample_sales(cluster, strategy, buyer):
            lo, hi = cluster["p25"], cluster["p75"]
            pool = [r for r in cluster["rows"] if lo <= float(r["price"]) <= hi] or cluster["rows"]
            n = min(len(pool), self.rng.randint(10, 15))
            cash = self.rng.random() < (0.9 if strategy == "fix_flip" else 0.5)
            for r in self.rng.sample(pool, n):
                sales.append(
                    Sale(
                        buyer=buyer,
                        property_id=r["_pk"],
                        price=_q2(r["price"]),
                        property_type="sfr",
                        beds=_i(r["bedrooms"]),
                        city=ZIP_CITY.get(cluster["zip"], "King County"),
                        state_code="WA",
                        zip=cluster["zip"],
                        geom=Point(float(r["long"]), float(r["lat"]), srid=4326),
                        purchased_at=r["_rebased"],
                        cash_buyer=cash,
                        disposition=DISPOSITION_BY_STRATEGY[strategy],
                        source=SEED_SOURCE,
                    )
                )

        # ~25 ex-prospect users — history only, rank on pure behavior (no buy-box).
        for i in range(N_PROSPECTS):
            cluster = clusters[i % len(clusters)]
            strategy = STRATEGIES[i % len(STRATEGIES)]
            u = User.objects.create_user(
                email=f"kc_prospect_{i + 1}@polaris.local",
                password=SEED_PASSWORD,
                full_name=f"Prospect {i + 1}",
                is_email_verified=True,
            )
            sample_sales(cluster, strategy, buyer=u)

        # ~15 registered buyers — history + buy-box + mandate.
        for i in range(N_REGISTERED):
            cluster = clusters[i % len(clusters)]
            strategy = STRATEGIES[i % len(STRATEGIES)]
            u = User.objects.create_user(
                email=f"kc_buyer_{i + 1}@polaris.local",
                password=SEED_PASSWORD,
                full_name=f"KC Buyer {i + 1}",
                is_email_verified=True,
            )
            sample_sales(cluster, strategy, buyer=u)
            lon, lat = cluster["centroid"]
            box = BuyBox.objects.create(
                buyer=u,
                name=f"{ZIP_CITY.get(cluster['zip'], cluster['zip'])} {strategy}",
                is_primary=True,
                is_active=True,
                source="manual",
                strategy=strategy,
                price_min=_q2(cluster["p25"] * 0.9),
                price_max=_q2(cluster["p75"] * 1.1),
                beds_min=2,
                sqft_min=800,
                property_types=["sfr"],
                condition_levels=CONDITION_LEVELS_BY_STRATEGY[strategy],
                target_metrics=TARGET_METRICS_BY_STRATEGY[strategy],
                funding_type="cash" if strategy == "fix_flip" else "hard_money",
                close_days=self.rng.choice([14, 21, 30]),
            )
            BuyBoxGeo.objects.create(
                buy_box=box,
                geo_type="radius",
                mode="include",
                center=Point(lon, lat, srid=4326),
                radius_mi=Decimal("5.0"),
            )
            Mandate.objects.create(
                buy_box=box,
                ceiling_price=box.price_max,
                instructions=(
                    f"Screening {strategy} deals near {cluster['zip']}. "
                    "Qualify strong spreads, ask for missing info, hold borderline."
                ),
            )

        Sale.objects.bulk_create(sales, batch_size=1000)

        # ~3 seed sellers + ~15 active listings priced below market (real spread).
        sellers = [
            User.objects.create_user(
                email=f"kc_seller_{s + 1}@polaris.local",
                password=SEED_PASSWORD,
                full_name=f"KC Seller {s + 1}",
                is_email_verified=True,
            )
            for s in range(N_SELLERS)
        ]
        used_pks: set[int] = set()
        made = 0
        for i in range(N_LISTINGS):
            cluster = clusters[i % len(clusters)]
            candidates = [r for r in cluster["rows"] if r["_pk"] not in used_pks]
            if not candidates:
                continue
            r = self.rng.choice(candidates)
            used_pks.add(r["_pk"])
            prop = Property.objects.get(pk=r["_pk"])
            # Give listing properties a readable synthetic street address (§5).
            prop.address_raw = (
                f"{self.rng.randint(100, 9999)} {self.rng.choice(STREETS)} Ave, "
                f"{ZIP_CITY.get(cluster['zip'], 'King County')}, WA {cluster['zip']}"
            )
            prop.save(update_fields=["address_raw"])
            asking = _q2(float(prop.last_sale_price) * float(LISTING_DISCOUNT))
            listing = Listing.objects.create(
                seller=sellers[i % len(sellers)],
                title=prop.address_raw,
                asking_price=asking,
                bundle_type="single",
                status="active",
            )
            ListingProperty.objects.create(
                listing=listing, property=prop, asking_price=asking, sort_order=0
            )
            Mandate.objects.create(
                listing=listing,
                floor_price=_q2(float(asking) * 0.95),
                instructions=(
                    f"Dispo {prop.address_raw}. Asking ${asking:,.0f}. "
                    "Surface qualified cash buyers; don't go below floor."
                ),
            )
            made += 1
        self.stdout.write(f"listings: {made} active under {len(sellers)} sellers")

    def _summary(self) -> None:
        User = get_user_model()
        self.stdout.write(
            self.style.SUCCESS(
                "seed_kc done — "
                f"properties={Property.objects.filter(county_fips=COUNTY_FIPS).count()}, "
                f"buyer_users={User.objects.filter(email__startswith='kc_buyer_').count()}, "
                f"history_users={User.objects.filter(email__startswith='kc_prospect_').count()}, "
                f"sales={Sale.objects.filter(source=SEED_SOURCE).count()}, "
                f"active_listings={Listing.objects.filter(status='active').count()}"
            )
        )

    # ---- reset ----------------------------------------------------------------
    def _reset(self) -> None:
        User = get_user_model()
        # Order matters: clear PROTECT references before their targets.
        Sale.objects.filter(source=SEED_SOURCE).delete()
        Listing.objects.filter(seller__email__startswith="kc_seller_").delete()
        BuyBox.objects.filter(buyer__email__startswith="kc_buyer_").delete()
        User.objects.filter(email__startswith="kc_").delete()
        deleted, _ = Property.objects.filter(county_fips=COUNTY_FIPS).delete()
        self.stdout.write(
            self.style.WARNING(f"--reset: cleared seed rows ({deleted} property-tree rows)")
        )
