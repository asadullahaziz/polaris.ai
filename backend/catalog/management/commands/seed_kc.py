"""
seed_kc — the Kessler County, WA demo world.

A closed fictional demo world on real King County bones: the densest N_CLUSTERS zips
are subsampled to ROWS_PER_CLUSTER properties each (~3.2k total) and rebranded as
small fictional towns. Real prices/attrs/geometry/sale dates keep the comp engine
credible; the identity layer (town names, street addresses) is synthetic.

  * Every property gets a deterministic, RNG-free street address — and
    `address_norm = normalize_address(address_raw)`, so every seeded property is
    resolvable through `lookup_property` / `resolve_geo` / `/api/properties/search`.
  * ~40 investor personas (25 history-only + 15 with buy-box + mandate) built from
    ARCHETYPES that deliberately vary the ranking features (bought-in-area, volume,
    recency, price band, cash, strategy) so `rank_buyers` shows a real spread.
    Every persona carries prose (bio/company/agent_instructions) from _seed_content,
    so live agent behavior is anchored to inspectable instructions.
  * ~15 active listings under 3 sellers, priced below market for wholesale spread,
    each with an attribute-composed description + an enriched seller mandate.
  * The hero path: kc_seller_1 owns a flagship listing whose asking price is
    calibrated at seed time to margin HERO_TARGET_MARGIN against the live comp ARV,
    so kc_buyer_1..4 (all cluster 0) deterministically diverge on it — qualify /
    hold / decline / gate-impasse — purely by strategy threshold + mandate ceiling.
  * Pre-warm: one closed deal (kc_seller_1 × kc_buyer_3) + one stale outreach
    thread, seeded through the pure-sync chat/deals services and backdated, so
    /deals and the inbox look lived-in from the first frame.

Two hard requirements:
  * Date rebase: the source sale window is linearly remapped onto the last
    ~24 months ending at the demo date, so `recency` is meaningful.
  * Idempotent / re-runnable: address generation consumes no RNG (pure index
    arithmetic over stably-sorted rows), so re-runs regenerate byte-identical rows and
    `bulk_create(ignore_conflicts=True)` is a true no-op; the behavioral layer is
    guarded by a `Sale(source=seed_kc)` sentinel. `--reset` truncates seed rows first.

Determinism: one fixed-seed RNG; every draw happens in a fixed order over
apn-sorted inputs, so the world reproduces exactly across rebuilds.
"""

from __future__ import annotations

import csv
import datetime as dt
import random
from decimal import ROUND_HALF_UP, Decimal

from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.gis.geos import Point
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.db.models import Q
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
from catalog.services import normalize_address
from matching.engine import WHOLESALE_FEE, _est_rehab, estimate_value
from users.models import UserProfile

from . import _seed_content as content

SEED_SOURCE = "seed_kc"
COUNTY_FIPS = "53033"  # engine keys comps off this; the fictional identity is naming-only
SEED_PASSWORD = "polaris123"  # dev/demo only
RNG_SEED = 1337
REBASE_MONTHS = 24
N_CLUSTERS = 8
ROWS_PER_CLUSTER = 400  # the single density knob (~3.2k properties total)
N_REGISTERED = 15
N_PROSPECTS = 25  # history-only registered users (no buy-box)
N_SELLERS = 3
N_LISTINGS = 15
LISTING_DISCOUNT = Decimal("0.80")  # asking below market → wholesale spread

# The hero path: the flagship's asking is derived from its seed-time comp ARV so its
# margin lands dead center between buy_hold's .10 threshold and brrrr's .15 — which is
# also fix_flip's decline boundary (.20 − hold band .05). One listing, four verdicts.
HERO_TARGET_MARGIN = 0.125
N_HERO_BUYERS = len(content.HERO_BUYERS)  # kc_buyer_1..4, all in cluster 0

# Listing ownership: (seller_idx, cluster_idx) per listing. kc_seller_1 (the hero
# seller the demo logs in as) owns the first five: the flagship (0), the pre-warm
# closed-deal fixer (1), the pre-warm stale-thread listing (2), plus two for variety.
SELLER_LISTING_PLAN = [
    (0, 0),  # 0: flagship — calibrated asking (see _pick_flagship)
    (0, 0),  # 1: cluster-0 fixer — the pre-warm closed deal (kc_buyer_3 flipped it)
    (0, 2),  # 2: the pre-warm stale outreach thread (kc_prospect_3's home cluster)
    (0, 1),
    (0, 3),
    (1, 4),
    (1, 5),
    (1, 1),
    (1, 6),
    (1, 7),
    (2, 2),
    (2, 3),
    (2, 6),
    (2, 7),
    (2, 5),
]
CLOSE_DAYS_CYCLE = [21, 14, 30]  # per-listing close preference (index-keyed, RNG-free)

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

# The Kessler County towns — one per source zip (zips stay real: they survive
# normalization and guarantee cross-town address uniqueness). Rename to taste.
TOWNS = {
    "98103": "Norhaven",
    "98115": "Eastmere",
    "98117": "Windmere",
    "98118": "Southglen",
    "98133": "Kilbourne",
    "98052": "Redfern",
    "98034": "Kirkwell",
    "98006": "Bellamy",
    "98038": "Maple Hollow",
    "98042": "Carverton",
    "98023": "Fernway",
    "98059": "Renwick",
}
TOWN_FALLBACK = "Kessler"

# Street grid for synthetic addresses. Names must never collide with
# normalize_address suffix/directional tokens; suffixes are already canonical
# short forms so normalize(raw) round-trips cleanly.
STREET_NAMES = [
    "Alder",
    "Maple",
    "Cedar",
    "Bramble",
    "Juniper",
    "Rowan",
    "Hollis",
    "Ashfern",
    "Kestrel",
    "Larkspur",
    "Meridian",
    "Quarry",
]
STREET_SUFFIXES = ["St", "Ave", "Ln", "Dr", "Ct"]  # 12 × 5 = 60 streets per town

# Buyer archetypes — deliberate variance across exactly the features the ranking
# engine scores, so every town ranks with a visible spread instead of a flat wall
# of look-alike buyers. window_mo = (newest, oldest) months back for sampled sales;
# band = price percentile range within the buyer's home town.
ARCHETYPES = {
    "anchor_flipper": {
        "strategy": "fix_flip",
        "n": (12, 15),
        "window_mo": (0, 6),
        "band": (0.25, 0.75),
        "cash_p": 0.95,
    },
    "steady_landlord": {
        "strategy": "buy_hold",
        "n": (6, 9),
        "window_mo": (0, 24),
        "band": (0.25, 0.75),
        "cash_p": 0.30,
    },
    "brrrr_operator": {
        "strategy": "brrrr",
        "n": (5, 8),
        "window_mo": (0, 12),
        "band": (0.10, 0.50),
        "cash_p": 0.60,
    },
    "newcomer": {
        "strategy": None,
        "n": (2, 3),
        "window_mo": (0, 3),
        "band": (0.10, 0.60),
        "cash_p": 0.90,
    },
    "lapsed": {
        "strategy": None,
        "n": (7, 10),
        "window_mo": (12, 24),
        "band": (0.40, 0.90),
        "cash_p": 0.40,
    },
    # Registered-only: buy-box covers the home town but purchase history sits in the
    # NEIGHBORING town — ranks low with a distinct "no local history" story.
    "out_of_towner": {
        "strategy": None,
        "n": (2, 4),
        "window_mo": (0, 12),
        "band": (0.25, 0.75),
        "cash_p": 0.50,
    },
}
# Prospects (history-only) cycle the middle of the pack; registered buyers get the
# top archetype in round 0 and a deliberately weak one in round 1, so the ranked
# table shows a kc_buyer on top AND near the bottom of every town.
PROSPECT_ROUNDS = ["steady_landlord", "brrrr_operator", "newcomer"]
BUYER_ROUND_1 = ["lapsed", "out_of_towner"]  # alternated by cluster parity

PROSPECT_NAMES = [
    "Marcus Webb",
    "Dana Whitfield",
    "Ray Okafor",
    "Lena Vasquez",
    "Tom Berrigan",
    "Aisha Clarke",
    "Victor Hale",
    "Priya Raman",
    "Cole Jastrow",
    "Maribel Santos",
    "Doug Fenwick",
    "Renee Caldwell",
    "Sam Oyelaran",
    "Kate Brennan",
    "Omar Haddad",
    "Jill Navarro",
    "Pete Lindqvist",
    "Tanya Brooks",
    "Hugo Reyes",
    "Wendy Marsh",
    "Felix Grant",
    "Nora Adeyemi",
    "Chad Willis",
    "Ivy Chen",
    "Gus Palmer",
]
BUYER_NAMES = [
    "Erin Kowalski",
    "Andre Bishop",
    "Sofia Marchetti",
    "Jake Tran",
    "Monica Ellison",
    "Dev Patel",
    "Claire Rutkowski",
    "Luis Herrera",
    "Becca Stone",
    "Nate Kimura",
    "Olivia Frost",
    "Reggie Coleman",
    "Hana Yusuf",
    "Brett Salazar",
    "Gina Moretti",
]
SELLER_NAMES = ["Walt Emerson", "Rosa Delgado", "Curtis Vann"]


def _q2(x) -> Decimal:
    return Decimal(x).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def _i(v):
    """Null-safe int (KC CSV has occasional empty cells)."""
    v = (v or "").strip()
    return int(float(v)) if v else None


def _f(v):
    v = (v or "").strip()
    return float(v) if v else None


def _pct(prices: list[float], frac: float) -> float:
    """Percentile by index over a pre-sorted price list."""
    return prices[min(len(prices) - 1, int(len(prices) * frac))]


def _town_of(address_raw: str) -> str:
    """The town token of a seeded address ('{no} {street}, {town}, WA {zip}')."""
    parts = address_raw.split(",")
    return parts[1].strip() if len(parts) >= 2 else TOWN_FALLBACK


class Command(BaseCommand):
    help = "Seed the Kessler County demo world: subsampled comps + investor personas + listings."

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

        # Legacy guard: a pre-Kessler DB carries `kc:<id>` norms that would coexist
        # (not conflict) with the new street norms → duplicate universe. Bail loudly.
        if Property.objects.filter(
            county_fips=COUNTY_FIPS, address_norm__startswith="kc:"
        ).exists():
            self.stdout.write(
                self.style.ERROR(
                    "legacy KC seed detected — run `make seed-reset` "
                    "(or `make down-v && make up-d`) to rebuild the Kessler County world."
                )
            )
            return

        rows = self._parse_and_rebase()
        clusters = self._select_clusters(rows)
        self._assign_addresses(clusters)
        self._load_properties(clusters)

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
        for c in clusters:
            kept = []
            for r in c["rows"]:
                pk = pk_by_apn.get(r["id"])
                if pk is not None:
                    r["_pk"] = pk
                    kept.append(r)
            c["rows"] = kept
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
            f"parsed {len(rows)} unique source properties; dates {src_min}→{src_max} "
            f"rebased to {dst_min}→{dst_max}"
        )
        return rows

    # ---- pick the towns: densest zips, subsampled ------------------------------
    def _select_clusters(self, rows: list[dict]) -> list[dict]:
        by_zip: dict[str, list[dict]] = {}
        for r in rows:
            by_zip.setdefault(r["zipcode"], []).append(r)
        # Explicit tiebreak (count desc, zip asc) so cluster order is stable.
        top = sorted(by_zip.items(), key=lambda kv: (-len(kv[1]), kv[0]))[:N_CLUSTERS]
        clusters = []
        for z, zrows in top:
            zrows.sort(key=lambda r: r["id"])  # stable order before the one RNG draw
            sample = self.rng.sample(zrows, min(ROWS_PER_CLUSTER, len(zrows)))
            sample.sort(key=lambda r: r["id"])  # stable downstream iteration order
            prices = sorted(float(r["price"]) for r in sample)
            lat = sum(float(r["lat"]) for r in sample) / len(sample)
            lon = sum(float(r["long"]) for r in sample) / len(sample)
            town = TOWNS.get(z, TOWN_FALLBACK)
            if z not in TOWNS:
                self.stdout.write(
                    self.style.WARNING(f"zip {z} missing from TOWNS — using {TOWN_FALLBACK}")
                )
            clusters.append(
                {
                    "zip": z,
                    "town": town,
                    "rows": sample,
                    "prices": prices,
                    "centroid": (lon, lat),
                    "p25": _pct(prices, 0.25),
                    "p75": _pct(prices, 0.75),
                }
            )
        return clusters

    # ---- synthetic street addresses (RNG-free, provably unique) ---------------
    def _assign_addresses(self, clusters: list[dict]) -> None:
        """Deterministic grid: street = i mod 60, block = i div 60. Within a town,
        rows sharing a street differ in block → house numbers differ by ≥4. Across
        towns the zip differs and survives normalization. No RNG → re-runs regenerate
        byte-identical addresses, keeping ignore_conflicts a true no-op."""
        streets = [f"{name} {suffix}" for suffix in STREET_SUFFIXES for name in STREET_NAMES]
        n = len(streets)
        for c in clusters:
            for i, r in enumerate(c["rows"]):
                house_no = 100 + 4 * (i // n) + (i % n) % 4
                r["_address_raw"] = f"{house_no} {streets[i % n]}, {c['town']}, WA {c['zip']}"

    # ---- load the comp universe ------------------------------------------------
    def _load_properties(self, clusters: list[dict]) -> None:
        objs = []
        for c in clusters:
            for r in c["rows"]:
                lon, lat = float(r["long"]), float(r["lat"])
                objs.append(
                    Property(
                        apn=r["id"],
                        county_fips=COUNTY_FIPS,
                        address_norm=normalize_address(r["_address_raw"]),
                        address_raw=r["_address_raw"],
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
                        floors=(
                            Decimal(str(_f(r["floors"]))) if _f(r["floors"]) is not None else None
                        ),
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
        # ignore_conflicts would silently DROP collided rows — fail loudly instead.
        if len({o.address_norm for o in objs}) != len(objs):
            raise CommandError("synthetic address_norm collision — check the street grid")
        before = Property.objects.filter(county_fips=COUNTY_FIPS).count()
        Property.objects.bulk_create(objs, batch_size=2000, ignore_conflicts=True)
        after = Property.objects.filter(county_fips=COUNTY_FIPS).count()
        self.stdout.write(f"properties: {after} total ({after - before} newly inserted)")

    # ---- personas (users), sales, buy-boxes, listings ------------------------
    # ---- the flagship (hero-path anchor) --------------------------------------
    def _pick_flagship(self, clusters: list[dict]) -> dict:
        """Deterministically pick the hero listing's property in cluster 0 and
        calibrate its asking from the LIVE comp ARV: asking = ARV·(1−M) − rehab − fee,
        so assess_deal's margin lands at HERO_TARGET_MARGIN for every strategy and the
        hero buyers' verdicts diverge by threshold alone. RNG-free: first apn-ordered
        candidate wins. Runs before the buyer loop — hero mandate ceilings derive from
        the flagship's asking/floor."""
        rows = clusters[0]["rows"]  # already apn-sorted

        def _candidates(conditions: set[int]):
            for r in rows:
                if (
                    _i(r["condition"]) in conditions
                    and (_i(r["bedrooms"]) or 0) >= 3
                    and 1400 <= (_i(r["sqft_living"]) or 0) <= 2200
                    and (_i(r["yr_built"]) or 9999) < 1990
                ):
                    yield r

        # Condition 3 first: 1.0 strategy-fit for every strategy, and its $25/sqft
        # rehab keeps the margin story legible. Relax to 2-3 before failing loudly.
        for conditions in ({3}, {2, 3}):
            for r in _candidates(conditions):
                prop = Property.objects.get(pk=r["_pk"])
                val = estimate_value(prop, arv=True)
                arv = val["point"]
                if arv is None or not val["basis"]["met_min_n"]:
                    continue
                rehab = _est_rehab(prop.condition, prop.sqft)
                if rehab is None:
                    continue
                asking = round((arv * (1 - HERO_TARGET_MARGIN) - rehab - WHOLESALE_FEE) / 1000)
                asking *= 1000
                if asking <= 0:
                    continue
                floor = round(asking * 0.95 / 1000) * 1000
                return {"row": r, "prop": prop, "arv": arv, "asking": asking, "floor": floor}
        raise CommandError("no flagship candidate in cluster 0 — the hero path cannot be built")

    @transaction.atomic
    def _seed_behavioral(self, clusters: list[dict], User) -> None:
        sales: list[Sale] = []
        today = timezone.now().date()
        flagship = self._pick_flagship(clusters)

        def sample_sales(cluster, archetype: dict, strategy: str, buyer) -> None:
            """Sample the archetype's slice of the town: price sub-band ∩ recency
            window, widening (drop window → drop band) if the slice runs thin."""
            rows = cluster["rows"]
            lo = _pct(cluster["prices"], archetype["band"][0])
            hi = _pct(cluster["prices"], archetype["band"][1])
            newest = today - dt.timedelta(days=int(archetype["window_mo"][0] * 30.44))
            oldest = today - dt.timedelta(days=int(archetype["window_mo"][1] * 30.44))
            in_band = [r for r in rows if lo <= float(r["price"]) <= hi]
            in_both = [r for r in in_band if oldest <= r["_rebased"] <= newest]
            n = self.rng.randint(*archetype["n"])
            pool = in_both if len(in_both) >= n else (in_band if len(in_band) >= n else rows)
            n = min(len(pool), n)
            cash = self.rng.random() < archetype["cash_p"]
            for r in self.rng.sample(pool, n):
                sales.append(
                    Sale(
                        buyer=buyer,
                        property_id=r["_pk"],
                        price=_q2(r["price"]),
                        property_type="sfr",
                        beds=_i(r["bedrooms"]),
                        city=cluster["town"],
                        state_code="WA",
                        zip=cluster["zip"],
                        geom=Point(float(r["long"]), float(r["lat"]), srid=4326),
                        purchased_at=r["_rebased"],
                        cash_buyer=cash,
                        disposition=DISPOSITION_BY_STRATEGY[strategy],
                        source=SEED_SOURCE,
                    )
                )

        # ~25 history-only users — rank on pure behavior (no buy-box).
        # Profile texture only (bio/company); no agent_instructions — they are extras,
        # not demo drivers.
        for i in range(N_PROSPECTS):
            cluster = clusters[i % len(clusters)]
            arch_key = PROSPECT_ROUNDS[(i // len(clusters)) % len(PROSPECT_ROUNDS)]
            archetype = ARCHETYPES[arch_key]
            strategy = archetype["strategy"] or STRATEGIES[i % len(STRATEGIES)]
            u = User.objects.create_user(
                email=f"kc_prospect_{i + 1}@polaris.local",
                password=SEED_PASSWORD,
                full_name=PROSPECT_NAMES[i % len(PROSPECT_NAMES)],
                is_email_verified=True,
            )
            persona = content.buyer_persona(arch_key, u.full_name, cluster["town"])
            UserProfile.objects.filter(user=u).update(
                bio=persona["bio"], company=persona["company"]
            )
            sample_sales(cluster, archetype, strategy, buyer=u)

        # ~15 registered buyers — history + buy-box + mandate. The first four are the
        # hero cohort: all in cluster 0 so every one ranks for the flagship, with
        # strategies/ceilings engineered to diverge on it (see content.HERO_BUYERS).
        # The rest keep the classic rounds over clusters 1..7: round 0 = the town's
        # anchor flipper (top of the ranked table); round 1 = lapsed/out-of-towner
        # (bottom of it), so registered demo users still bracket the spread.
        for i in range(N_REGISTERED):
            spec = content.HERO_BUYERS[i] if i < N_HERO_BUYERS else None
            if spec is not None:
                cluster_idx = 0
                arch_key = spec["arch_key"]
                archetype = ARCHETYPES[arch_key]
                strategy = spec["strategy"]
            else:
                j = i - N_HERO_BUYERS
                cluster_idx = 1 + j % (len(clusters) - 1)
                arch_key = (
                    "anchor_flipper"
                    if j < len(clusters) - 1
                    else BUYER_ROUND_1[j % len(BUYER_ROUND_1)]
                )
                archetype = ARCHETYPES[arch_key]
                strategy = archetype["strategy"] or STRATEGIES[i % len(STRATEGIES)]
            cluster = clusters[cluster_idx]
            u = User.objects.create_user(
                email=f"kc_buyer_{i + 1}@polaris.local",
                password=SEED_PASSWORD,
                full_name=BUYER_NAMES[i % len(BUYER_NAMES)],
                is_email_verified=True,
            )
            persona = content.buyer_persona(arch_key, u.full_name, cluster["town"])
            UserProfile.objects.filter(user=u).update(
                bio=persona["bio"],
                company=persona["company"],
                agent_instructions=persona["agent_instructions"],
            )
            # Out-of-towners buy in the NEIGHBORING town; everyone else at home.
            sales_cluster = (
                clusters[(cluster_idx + 1) % len(clusters)]
                if arch_key == "out_of_towner"
                else cluster
            )
            sample_sales(sales_cluster, archetype, strategy, buyer=u)
            lon, lat = cluster["centroid"]
            if spec is not None:
                # Bracket the flagship's calibrated asking so price-band fit is real.
                box_lo, box_hi = flagship["asking"] * 0.75, flagship["asking"] * 1.2
                funding, close_days = spec["funding"], spec["close_days"]
            else:
                box_lo = _pct(cluster["prices"], archetype["band"][0]) * 0.9
                box_hi = _pct(cluster["prices"], archetype["band"][1]) * 1.1
                funding = "cash" if strategy == "fix_flip" else "hard_money"
                close_days = self.rng.choice([14, 21, 30])
            box = BuyBox.objects.create(
                buyer=u,
                name=f"{cluster['town']} {strategy}",
                is_primary=True,
                is_active=True,
                source="manual",
                strategy=strategy,
                price_min=_q2(box_lo),
                price_max=_q2(box_hi),
                beds_min=2,
                sqft_min=800,
                property_types=["sfr"],
                condition_levels=CONDITION_LEVELS_BY_STRATEGY[strategy],
                target_metrics=TARGET_METRICS_BY_STRATEGY[strategy],
                funding_type=funding,
                close_days=close_days,
            )
            BuyBoxGeo.objects.create(
                buy_box=box,
                geo_type="radius",
                mode="include",
                center=Point(lon, lat, srid=4326),
                radius_mi=Decimal("5.0"),
            )
            if spec is not None:
                base, factor = spec["ceiling"]
                if base == "asking":
                    ceiling = _q2(flagship["asking"] * factor)
                elif base == "floor":
                    ceiling = _q2(flagship["floor"] * factor)
                else:
                    ceiling = box.price_max
            else:
                ceiling = box.price_max
            mc = content.buyer_mandate_content(arch_key, cluster["town"])
            Mandate.objects.create(
                buy_box=box,
                ceiling_price=ceiling,
                instructions=mc["instructions"],
                must_haves=mc["must_haves"],
                availability_window=mc["availability_window"],
            )

        Sale.objects.bulk_create(sales, batch_size=1000)

        # ~3 seed sellers + ~15 active listings priced below market (real spread).
        sellers = [
            User.objects.create_user(
                email=f"kc_seller_{s + 1}@polaris.local",
                password=SEED_PASSWORD,
                full_name=SELLER_NAMES[s % len(SELLER_NAMES)],
                is_email_verified=True,
            )
            for s in range(N_SELLERS)
        ]
        for s, u in enumerate(sellers):
            persona = content.SELLER_PERSONAS[s % len(content.SELLER_PERSONAS)]
            UserProfile.objects.filter(user=u).update(
                bio=persona["bio"],
                company=persona["company"],
                agent_instructions=persona["agent_instructions"],
            )

        def _pick_fixer(rows: list[dict], used: set[int]) -> dict | None:
            """First apn-ordered cluster row that reads as a flip (condition ≤ 2) —
            the pre-warm closed deal wants a property coherent with a flipper buyer."""
            for r in rows:
                if r["_pk"] in used:
                    continue
                cond = _i(r["condition"])
                if cond is not None and cond <= 2 and (_i(r["bedrooms"]) or 0) >= 2:
                    return r
            return None

        used_pks: set[int] = {flagship["row"]["_pk"]}
        listings: list[Listing] = []
        for i, (seller_idx, cluster_idx) in enumerate(SELLER_LISTING_PLAN):
            cluster = clusters[cluster_idx]
            if i == 0:
                r, prop = flagship["row"], flagship["prop"]
                asking = _q2(flagship["asking"])
            else:
                candidates = [c for c in cluster["rows"] if c["_pk"] not in used_pks]
                if not candidates:
                    continue
                r = (_pick_fixer(cluster["rows"], used_pks) if i == 1 else None) or self.rng.choice(
                    candidates
                )
                used_pks.add(r["_pk"])
                prop = Property.objects.get(pk=r["_pk"])
                asking = _q2(float(prop.last_sale_price) * float(LISTING_DISCOUNT))
            close_days = CLOSE_DAYS_CYCLE[i % len(CLOSE_DAYS_CYCLE)]
            listing = Listing.objects.create(
                seller=sellers[seller_idx],
                title=prop.address_raw,
                description=content.compose_description(
                    prop, cluster["town"], close_days=close_days, variant=i
                ),
                asking_price=asking,
                bundle_type="single",
                status="active",
            )
            ListingProperty.objects.create(
                listing=listing, property=prop, asking_price=asking, sort_order=0
            )
            floor = _q2(flagship["floor"]) if i == 0 else _q2(float(asking) * 0.95)
            sm = content.SELLER_MANDATES[seller_idx % len(content.SELLER_MANDATES)]
            Mandate.objects.create(
                listing=listing,
                floor_price=floor,
                instructions=sm["instructions"],
                must_haves=sm["must_haves"],
                availability_window=sm["availability_window"],
            )
            listings.append(listing)
        self.stdout.write(f"listings: {len(listings)} active under {len(sellers)} sellers")

        self._prewarm(sellers, listings, User)

    # ---- pre-warm: a light lived-in history ------------------------------------
    def _prewarm(self, sellers: list, listings: list[Listing], User) -> None:
        """Seed interaction history through the pure-sync service layer (no Inngest/WS
        side effects): one CLOSED deal with a real transcript (the hero seller ×
        kc_buyer_3, the flipper — which also lights rank_buyers' relationship weight
        on the flagship) and one STALE contacted thread that never got an answer.
        Timestamps are backdated relative to now, the same regime as the date rebase."""
        if len(listings) < 3:
            return
        from chat import services as chat_svc
        from chat.models import Chat, Message
        from deals import service as deal_svc
        from deals.models import Deal

        walt = sellers[0]
        sofia = User.objects.get(email="kc_buyer_3@polaris.local")
        prospect = User.objects.get(email="kc_prospect_3@polaris.local")
        l2, l3 = listings[1], listings[2]
        prop2 = l2.listingproperty_set.order_by("sort_order").first().property
        prop3 = l3.listingproperty_set.order_by("sort_order").first().property
        # The one deliberately disclosed figure; clears the listing's 0.95 floor.
        offer = round(float(l2.asking_price) * 0.97 / 1000) * 1000

        # A. The closed deal — pitch → cash reply → propose → verbal accept → closed.
        chat, _ = chat_svc.get_or_create_chat(walt.id, sofia.id)
        ctx = {
            "address": prop2.address_raw,
            "beds": prop2.beds or 3,
            "offer": f"${offer:,}",
            "town": _town_of(prop2.address_raw),
        }
        deal = None
        msg_ids: list[int] = []
        for idx, (kind, action, tpl) in enumerate(content.PREWARM["closed"]):
            body = tpl.format(**ctx)
            if kind == "agent":
                res = chat_svc.post_agent_message(
                    chat.id,
                    walt.id,
                    body,
                    attachment_listing_ids=[l2.id] if idx == 0 else None,
                    dedup_key=f"seed:prewarm:closed:{l2.id}:{idx}",
                    action=action,
                )
            else:
                res = chat_svc.post_human_message(
                    chat.id, sofia.id, body, client_dedup_uuid=f"seed-prewarm-{l2.id}-{idx}"
                )
            msg_ids.append(res["id"])
            if idx == 2:  # the propose names the figure → /deals shows a standing offer
                deal = Deal.objects.get(listing=l2, buyer=sofia)
                # record_disclosed_offer is agent-path-only elsewhere; the pre-warm
                # uses it as an authoring shortcut for the deal card's offer column.
                deal_svc.record_disclosed_offer(deal, by_user_id=walt.id, price=offer)
                deal_svc.advance_stage(deal, "negotiating")
            elif idx == 3:
                deal.agreed_price = offer
                deal.save(update_fields=["agreed_price", "updated_at"])
                deal_svc.advance_stage(deal, "agreed")
        deal_svc.set_stage_manual(deal, "closed")  # closed is manual-only by design
        chat_svc.mark_read(chat.id, walt.id)
        chat_svc.mark_read(chat.id, sofia.id)

        # B. The stale thread — one opener, never answered; sits at `contacted`.
        chat2, _ = chat_svc.get_or_create_chat(walt.id, prospect.id)
        chat_svc.post_agent_message(
            chat2.id,
            walt.id,
            content.PREWARM["stale"].format(address=prop3.address_raw, beds=prop3.beds or 3),
            attachment_listing_ids=[l3.id],
            dedup_key=f"seed:prewarm:stale:{l3.id}",
            action="inform",
        )
        chat_svc.mark_read(chat2.id, walt.id)

        # C. Backdate (fixed day offsets → deterministic per rebuild).
        now = timezone.now()
        for mid, days in zip(msg_ids, [9, 8, 7, 7, 6]):
            ts = now - dt.timedelta(days=days)
            Message.objects.filter(id=mid).update(created_at=ts, sent_at=ts)
        stale_ts = now - dt.timedelta(days=6)
        Message.objects.filter(chat_id=chat2.id).update(created_at=stale_ts, sent_at=stale_ts)
        Chat.objects.filter(id__in=[chat.id, chat2.id]).update(updated_at=stale_ts)
        Deal.objects.filter(listing=l2, buyer=sofia).update(
            created_at=now - dt.timedelta(days=9), stage_changed_at=stale_ts, updated_at=stale_ts
        )
        Deal.objects.filter(listing=l3, buyer=prospect).update(
            created_at=stale_ts, stage_changed_at=stale_ts, updated_at=stale_ts
        )
        self.stdout.write("prewarm: 1 closed deal + 1 stale thread (backdated)")

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
        # Demo cheat-sheet (DB-derived so it prints on the sentinel-skip path too).
        listed = [
            lp.property.address_raw
            for lst in Listing.objects.filter(status="active").order_by("id")[:3]
            if (lp := lst.listingproperty_set.order_by("sort_order").first()) is not None
        ]
        unlisted = list(
            Property.objects.filter(county_fips=COUNTY_FIPS, listingproperty__isnull=True)
            .order_by("id")
            .values_list("address_raw", flat=True)[:2]
        )
        if listed or unlisted:
            self.stdout.write("\n── Kessler County demo cheat-sheet ──")
            for a in listed:
                self.stdout.write(f"  listed:   {a}")
            for a in unlisted:
                self.stdout.write(f"  unlisted: {a}")
            self.stdout.write(
                "  logins:   kc_seller_1@polaris.local / kc_buyer_1@polaris.local "
                f"(password {SEED_PASSWORD}); demo / demo12345"
            )
        # The hero path (DB-derived, so it prints on the sentinel-skip path too).
        walt = User.objects.filter(email="kc_seller_1@polaris.local").first()
        flag = (
            Listing.objects.filter(seller=walt, status="active").order_by("id").first()
            if walt
            else None
        )
        if flag is not None:
            lp = flag.listingproperty_set.order_by("sort_order").first()
            mandate = Mandate.objects.filter(listing=flag).first()
            floor = mandate.floor_price if mandate else None
            self.stdout.write("\n── hero path ──")
            self.stdout.write(
                f"  flagship: {lp.property.address_raw if lp else flag.title} "
                f"(listing #{flag.id}) — asking ${flag.asking_price:,.0f}"
                + (f", floor ${floor:,.0f}" if floor is not None else "")
            )
            for i, spec in enumerate(content.HERO_BUYERS):
                self.stdout.write(
                    f"  kc_buyer_{i + 1} ({spec['strategy']}): {spec['expected']}"
                )
            self.stdout.write(
                "  prewarm:  closed deal kc_seller_1 × kc_buyer_3; "
                "stale thread kc_seller_1 × kc_prospect_3"
            )

    # ---- reset ----------------------------------------------------------------
    def _reset(self) -> None:
        User = get_user_model()
        # Order matters: clear PROTECT references before their targets.
        Sale.objects.filter(source=SEED_SOURCE).delete()
        # Message.sender is PROTECT: any chat a kc_ user is a member of (the pre-warm
        # transcripts, plus anything a live demo session produced) must be deleted
        # BEFORE the users, or User.delete() raises ProtectedError. Deleting the Chat
        # cascades members/messages/attachments; Deal.chat is SET_NULL and deals
        # themselves cascade with their listing/users below.
        from chat.models import Chat, ChatMember

        seed_chat_ids = set(
            ChatMember.objects.filter(user__email__startswith="kc_").values_list(
                "chat_id", flat=True
            )
        )
        Chat.objects.filter(id__in=seed_chat_ids).delete()
        # Any listing that attaches a seed (county) property must be deleted before the
        # properties themselves — ListingProperty.property is PROTECT. This includes
        # listings created during a demo session under a non-seed seller (e.g. `demo`);
        # filtering by seller email alone leaves those rows PROTECTing the properties.
        # values_list over the OR-join can repeat listing ids, so materialize to a set
        # (a distinct() queryset can't be .delete()'d).
        seed_listing_ids = set(
            Listing.objects.filter(
                Q(seller__email__startswith="kc_seller_")
                | Q(listingproperty__property__county_fips=COUNTY_FIPS)
            ).values_list("id", flat=True)
        )
        Listing.objects.filter(id__in=seed_listing_ids).delete()
        BuyBox.objects.filter(buyer__email__startswith="kc_buyer_").delete()
        User.objects.filter(email__startswith="kc_").delete()
        deleted, _ = Property.objects.filter(county_fips=COUNTY_FIPS).delete()
        self.stdout.write(
            self.style.WARNING(f"--reset: cleared seed rows ({deleted} property-tree rows)")
        )
