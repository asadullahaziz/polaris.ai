"""
Deterministic comping / valuation engine (matching_and_data §3, implementation_plan P1.5).

"The engine scores, the LLM narrates." This module is pure SQL/PostGIS/Python — no
model calls — so it is reproducible, explainable, and unit-testable without an LLM.
`polaris_agent` only *wraps* these as tools and narrates the breakdown.

Public API:
  * get_comps(subject, ...)       → nearest recent similar SOLD properties, with a
                                     staged fallback that records how far it relaxed.
  * estimate_value(subject, ...)  → a value range from comp $/sqft; `arv=True` comps
                                     against good-condition sales (the after-repair view).
  * rank_buyers(listing_id, ...)  → the buyer-matching engine (P2.1, matching_and_data §2):
                                     behavioral-first weighted score + per-feature breakdown
                                     the LLM narrates as "why this buyer." No LLM here.
  * assess_deal(listing_id, ...)  → the buyer auto-responder's wholesale math (P3.4,
                                     matching_and_data §3): spread/margin vs the buyer's
                                     strategy threshold → qualify/hold/decline verdict +
                                     rationale, feeding Stage 1 (architecture §5). No LLM.

`subject` is a `catalog.Property` or a dict of {geom, beds, sqft, grade, waterfront,
condition, pk} — so it works on a saved listing OR freshly-extracted attributes.
"""

from __future__ import annotations

import datetime as dt
import math
from collections import defaultdict
from statistics import median

from django.contrib.gis.db.models.functions import Distance
from django.contrib.gis.measure import D
from django.utils import timezone

from catalog.models import Property

COUNTY_FIPS = "53033"
_METERS_PER_MILE = 1609.344

# Staged relaxation (matching_and_data §3): widen radius → relax sqft → relax grade →
# relax recency, recording the note the LLM discloses ("had to look 3 mi out").
# (radius_mi, sqft_tol, grade_tol, months, note)
_STAGES = [
    (1.0, 0.20, 2, 6, "1mi · sqft±20% · grade±2 · 6mo"),
    (2.0, 0.20, 2, 6, "radius→2mi"),
    (3.0, 0.20, 2, 12, "radius→3mi · recency→12mo"),
    (5.0, 0.35, 2, 24, "radius→5mi · sqft±35% · recency→24mo"),
    (5.0, 0.50, 4, 24, "radius→5mi · sqft±50% · grade±4 · 24mo"),
]


def _attrs(subject) -> dict:
    if isinstance(subject, dict):
        g = subject.get
        return {
            "geom": g("geom"),
            "beds": g("beds"),
            "sqft": g("sqft"),
            "grade": g("grade"),
            "waterfront": g("waterfront"),
            "condition": g("condition"),
            "pk": g("pk") or g("id"),
        }
    return {
        "geom": getattr(subject, "geom", None),
        "beds": subject.beds,
        "sqft": subject.sqft,
        "grade": subject.grade,
        "waterfront": subject.waterfront,
        "condition": subject.condition,
        "pk": subject.pk,
    }


def _comp_dict(p: Property) -> dict:
    ppsf = float(p.last_sale_price) / p.sqft if (p.last_sale_price and p.sqft) else None
    dist_mi = None
    if getattr(p, "dist", None) is not None:
        dist_mi = round(p.dist.m / _METERS_PER_MILE, 2)
    return {
        "id": p.pk,
        "address": p.address_raw,
        "beds": p.beds,
        "baths": float(p.baths) if p.baths is not None else None,
        "sqft": p.sqft,
        "grade": p.grade,
        "condition": p.condition,
        "waterfront": p.waterfront,
        "price": float(p.last_sale_price) if p.last_sale_price is not None else None,
        "sold_on": p.last_sale_date.isoformat() if p.last_sale_date else None,
        "ppsf": round(ppsf, 1) if ppsf else None,
        "distance_mi": dist_mi,
    }


def get_comps(subject, *, min_n: int = 5, limit: int = 25) -> dict:
    """Nearest recent SOLD properties similar to `subject`. Auto-expands until it has
    at least `min_n`, recording which stage of relaxation was used."""
    a = _attrs(subject)
    today = timezone.now().date()
    result_rows: list[Property] = []
    used = _STAGES[-1]

    for stage in _STAGES:
        radius_mi, sqft_tol, grade_tol, months, note = stage
        qs = Property.objects.filter(
            county_fips=COUNTY_FIPS,
            last_sale_price__isnull=False,
            sqft__isnull=False,
            sqft__gt=0,
        )
        if a["pk"]:
            qs = qs.exclude(pk=a["pk"])
        if a["geom"] is not None:
            qs = qs.filter(geom__dwithin=(a["geom"], D(mi=radius_mi)))
        if a["beds"] is not None:
            qs = qs.filter(beds__gte=a["beds"] - 1, beds__lte=a["beds"] + 1)
        if a["sqft"]:
            qs = qs.filter(
                sqft__gte=int(a["sqft"] * (1 - sqft_tol)),
                sqft__lte=int(a["sqft"] * (1 + sqft_tol)),
            )
        if a["grade"] is not None:
            qs = qs.filter(grade__gte=a["grade"] - grade_tol, grade__lte=a["grade"] + grade_tol)
        if a["waterfront"] is not None:
            qs = qs.filter(waterfront=a["waterfront"])  # never comp waterfront vs non-
        qs = qs.filter(last_sale_date__gte=today - dt.timedelta(days=int(months * 30.44)))

        if a["geom"] is not None:
            qs = qs.annotate(dist=Distance("geom", a["geom"])).order_by("dist")
        else:
            qs = qs.order_by("-last_sale_date")

        result_rows = list(qs[:limit])
        used = stage
        if len(result_rows) >= min_n:
            break

    return {
        "comps": [_comp_dict(p) for p in result_rows],
        "n": len(result_rows),
        "radius_mi": used[0],
        "relaxed": used[4],
        "min_n": min_n,
        "met_min_n": len(result_rows) >= min_n,
    }


def _percentiles(values: list[float]) -> tuple[float, float, float]:
    s = sorted(values)
    return s[len(s) // 4], median(s), s[min(len(s) - 1, len(s) * 3 // 4)]


def estimate_value(subject, *, arv: bool = False, min_n: int = 5) -> dict:
    """Value range from comp $/sqft × subject sqft. `arv=True` restricts to
    good-condition comps (the after-repair view: comped against turnkey sales)."""
    a = _attrs(subject)
    comp_res = get_comps(subject, min_n=min_n)
    comps = comp_res["comps"]

    if arv:
        good = [c for c in comps if (c.get("condition") or 0) >= 4]
        comps = good if len(good) >= 3 else comps  # fall back if too few turnkey comps

    ppsfs = [c["ppsf"] for c in comps if c["ppsf"]]
    basis = {
        "n_comps": len(ppsfs),
        "radius_mi": comp_res["radius_mi"],
        "relaxed": comp_res["relaxed"],
        "arv": arv,
        "met_min_n": comp_res["met_min_n"],
    }
    if not ppsfs or not a["sqft"]:
        return {
            "low": None,
            "point": None,
            "high": None,
            "basis": basis,
            "comps": comp_res["comps"],
        }

    lo, mid, hi = _percentiles(ppsfs)
    sqft = a["sqft"]
    basis.update(
        {"ppsf_low": round(lo, 1), "ppsf_median": round(mid, 1), "ppsf_high": round(hi, 1)}
    )
    return {
        "low": round(lo * sqft),
        "point": round(mid * sqft),
        "high": round(hi * sqft),
        "basis": basis,
        "comps": comp_res["comps"],
    }


# =====================================================================================
# Buyer ranking (P2.1, matching_and_data §2) — deterministic + explainable.
#
# "The engine scores, the LLM narrates." score = Σ (weight × normalized_feature). The
# behavioral features (geo + price + strategy + recency + volume = 0.83) dominate, so
# the engine is behavioral-first and degrades gracefully for prospects (who have no
# buy-box). Buy-box completeness is a registered-only bonus/tie-breaker.
# =====================================================================================

RANK_WEIGHTS = {
    "bought_in_area": 0.28,  # strongest signal: nearby prior purchases
    "price_band": 0.18,  # listing price vs the buyer's historical/buy-box band
    "strategy": 0.15,  # listing condition vs the buyer's dominant strategy
    "recency": 0.12,  # exp-decay on months since the last buy
    "volume": 0.10,  # log-scaled purchase count
    "cash": 0.07,  # cash buyers close
    "relationship": 0.10,  # warm > cold (prior thread/deal with THIS seller)
}
BUY_BOX_BONUS = 0.05  # registered-only tie-breaker on top of the Σ=1.0 base

# Condition bands (KC 1–5) each strategy targets — grounds "strategy fit" in real data.
_FLIP_CONDITIONS = {1, 2, 3}  # fix_flip / brrrr want value-add
_HOLD_CONDITIONS = {3, 4, 5}  # buy_hold wants turnkey-ish
_DISPOSITION_TO_STRATEGY = {"flip": "fix_flip", "hold": "buy_hold", "brrrr": "brrrr"}


def _bought_in_area(n_near: int) -> float:
    return 1.0 - 0.6**n_near if n_near else 0.0  # 1→.40 2→.64 3→.78 4→.87


def _price_band_fit(price, lo, hi) -> float:
    if price is None or lo is None or hi is None or hi < lo:
        return 0.5  # neutral when we can't tell
    if lo <= price <= hi:
        return 1.0
    mid = (lo + hi) / 2 or 1.0
    dist = (lo - price) if price < lo else (price - hi)
    return max(0.0, 1.0 - dist / (0.5 * mid + 1.0))


def _strategy_fit(strategy: str | None, condition: int | None) -> float:
    if strategy is None:
        return 0.5
    if condition is None:
        return 0.6
    band = _FLIP_CONDITIONS if strategy in ("fix_flip", "brrrr") else _HOLD_CONDITIONS
    if condition in band:
        return 1.0
    return 0.5 if any(abs(condition - c) == 1 for c in band) else 0.3


def _recency(months: float | None) -> float:
    return math.exp(-months / 12.0) if months is not None else 0.0


def _volume(n: int) -> float:
    return min(1.0, math.log(1 + n) / math.log(13)) if n else 0.0  # 12 buys → 1.0


def _dominant_strategy(dispositions: list[str], box_strategy: str | None) -> str | None:
    counts: dict[str, int] = defaultdict(int)
    for d in dispositions:
        s = _DISPOSITION_TO_STRATEGY.get(d)
        if s:
            counts[s] += 1
    if counts:
        return max(counts, key=lambda s: (counts[s], s))
    return box_strategy


def _completeness(box, prop, price) -> float:
    if box is None:
        return 0.0
    checks: list[bool] = []
    if box.beds_min is not None:
        checks.append(prop.beds is not None and prop.beds >= box.beds_min)
    if box.sqft_min is not None:
        checks.append(prop.sqft is not None and prop.sqft >= box.sqft_min)
    if box.price_min is not None and price is not None:
        checks.append(price >= float(box.price_min))
    if box.price_max is not None and price is not None:
        checks.append(price <= float(box.price_max))
    if box.property_types:
        checks.append(prop.property_type in box.property_types)
    return sum(1 for c in checks if c) / len(checks) if checks else 0.0


def _reason(feats: dict, ctx: dict) -> str:
    """Turn the top-contributing features into a human "why this buyer" line."""
    bits: list[str] = []
    ordered = sorted(feats.items(), key=lambda kv: RANK_WEIGHTS.get(kv[0], 0) * kv[1], reverse=True)
    for name, val in ordered:
        if val <= 0:
            continue
        if name == "bought_in_area" and ctx["n_near"]:
            s = "s" if ctx["n_near"] != 1 else ""
            bits.append(f"bought {ctx['n_near']} nearby home{s} within {ctx['radius']:g} mi")
        elif name == "recency" and ctx["months"] is not None:
            bits.append(f"active recently (~{ctx['months']}mo ago)")
        elif name == "cash" and ctx["cash"]:
            bits.append("all-cash")
        elif name == "volume" and ctx["n_total"] >= 2:
            bits.append(f"{ctx['n_total']} deals on record")
        elif name == "strategy" and val >= 0.9 and ctx["strategy"]:
            bits.append(f"{ctx['strategy'].replace('_', ' ')} fits this deal")
        elif name == "price_band" and val >= 0.9:
            bits.append("in their price band")
        elif name == "relationship" and val > 0:
            bits.append("prior deal with you")
        if len(bits) >= 3:
            break
    return "; ".join(bits) or "matches on location and history"


def rank_buyers(listing_id: int, *, limit: int = 10, radius_mi: float = 5.0) -> dict:
    """Rank likely buyers for a listing. Candidate pool = registered buyers (covering
    buy-box or nearby history) + prospects (nearby history). Behavioral-first weighted
    score with a per-feature breakdown for narration. Deterministic."""
    from catalog.models import Listing, ListingProperty

    listing = Listing.objects.filter(id=listing_id).select_related("seller").first()
    if listing is None:
        return {"error": f"listing {listing_id} not found", "ranked": []}
    lp = ListingProperty.objects.filter(listing_id=listing_id).select_related("property").first()
    prop = lp.property if lp else None
    if prop is None or prop.geom is None:
        return {
            "listing_id": listing_id,
            "ranked": [],
            "note": "listing has no geolocated property",
        }

    geom = prop.geom
    price = (
        float(listing.asking_price)
        if listing.asking_price is not None
        else (float(prop.last_sale_price) if prop.last_sale_price is not None else None)
    )
    today = timezone.now().date()

    user_ids, prospect_ids = _candidate_pool(geom, radius_mi)
    if not user_ids and not prospect_ids:
        return {"listing_id": listing_id, "ranked": [], "n_candidates": 0, "weights": RANK_WEIGHTS}

    per_user, per_prospect = _load_purchases(user_ids, prospect_ids, geom, radius_mi)
    boxes = _load_boxes(user_ids)
    rel_users, rel_prospects = _relationships(listing.seller_id, user_ids, prospect_ids)
    names_u, names_p, prospect_cash = _load_names(user_ids, prospect_ids)

    ranked: list[dict] = []
    for uid in user_ids:
        ranked.append(
            _score_candidate(
                key=("user", uid),
                name=names_u.get(uid, f"Buyer {uid}"),
                registered=True,
                purchases=per_user.get(uid, []),
                box=boxes.get(uid),
                related=uid in rel_users,
                prop=prop,
                price=price,
                today=today,
                radius_mi=radius_mi,
            )
        )
    for pid in prospect_ids:
        ranked.append(
            _score_candidate(
                key=("prospect", pid),
                name=names_p.get(pid, f"Prospect {pid}"),
                registered=False,
                purchases=per_prospect.get(pid, []),
                box=None,
                related=pid in rel_prospects,
                prop=prop,
                price=price,
                today=today,
                radius_mi=radius_mi,
                prospect_cash=prospect_cash.get(pid),
            )
        )

    # Sort by score desc; name asc as a stable, deterministic tie-break.
    ranked.sort(key=lambda r: (-r["score"], r["name"]))
    return {
        "listing_id": listing_id,
        "n_candidates": len(ranked),
        "radius_mi": radius_mi,
        "weights": RANK_WEIGHTS,
        "ranked": ranked[:limit],
    }


def _candidate_pool(geom, radius_mi: float) -> tuple[set[int], set[int]]:
    from buyers.models import BuyBoxGeo, Purchase

    user_ids: set[int] = set()
    prospect_ids: set[int] = set()
    for row in Purchase.objects.filter(geom__dwithin=(geom, D(mi=radius_mi))).values(
        "buyer_user_id", "buyer_prospect_id"
    ):
        if row["buyer_user_id"]:
            user_ids.add(row["buyer_user_id"])
        if row["buyer_prospect_id"]:
            prospect_ids.add(row["buyer_prospect_id"])
    # Registered buyers whose radius buy-box covers the listing (even with no nearby buy).
    for g in (
        BuyBoxGeo.objects.filter(geo_type="radius", center__isnull=False, buy_box__is_active=True)
        .annotate(d=Distance("center", geom))
        .select_related("buy_box")
    ):
        if (
            g.radius_mi is not None
            and g.d is not None
            and g.d.m <= float(g.radius_mi) * _METERS_PER_MILE
        ):
            user_ids.add(g.buy_box.buyer_id)
    return user_ids, prospect_ids


def _load_purchases(user_ids, prospect_ids, geom, radius_mi):
    from django.db.models import Q

    from buyers.models import Purchase

    per_user: dict[int, list[dict]] = defaultdict(list)
    per_prospect: dict[int, list[dict]] = defaultdict(list)
    threshold_m = float(radius_mi) * _METERS_PER_MILE
    qs = (
        Purchase.objects.filter(
            Q(buyer_user_id__in=user_ids) | Q(buyer_prospect_id__in=prospect_ids)
        )
        .annotate(dist=Distance("geom", geom))
        .values(
            "buyer_user_id",
            "buyer_prospect_id",
            "price",
            "purchased_at",
            "cash_buyer",
            "disposition",
            "dist",
        )
    )
    for r in qs:
        dist_m = r["dist"].m if r["dist"] is not None else None
        rec = {
            "price": float(r["price"]) if r["price"] is not None else None,
            "purchased_at": r["purchased_at"],
            "cash_buyer": r["cash_buyer"],
            "disposition": r["disposition"],
            "near": dist_m is not None and dist_m <= threshold_m,
        }
        if r["buyer_user_id"]:
            per_user[r["buyer_user_id"]].append(rec)
        elif r["buyer_prospect_id"]:
            per_prospect[r["buyer_prospect_id"]].append(rec)
    return per_user, per_prospect


def _load_boxes(user_ids):
    from buyers.models import BuyBox

    boxes: dict[int, object] = {}
    for b in BuyBox.objects.filter(buyer_id__in=user_ids, is_active=True).order_by("-is_primary"):
        boxes.setdefault(b.buyer_id, b)  # keep the primary (ordered first)
    return boxes


def _relationships(seller_id, user_ids, prospect_ids):
    from conversations.models import Conversation

    base = Conversation.objects.filter(kind="thread", listing__seller_id=seller_id)
    rel_users = set(
        base.filter(counterparty_user_id__in=user_ids).values_list(
            "counterparty_user_id", flat=True
        )
    )
    rel_prospects = set(
        base.filter(counterparty_prospect_id__in=prospect_ids).values_list(
            "counterparty_prospect_id", flat=True
        )
    )
    return rel_users, rel_prospects


def _load_names(user_ids, prospect_ids):
    from django.contrib.auth import get_user_model

    from buyers.models import Prospect

    names_u = {}
    for u in get_user_model().objects.filter(id__in=user_ids).values("id", "full_name", "username"):
        names_u[u["id"]] = u["full_name"] or u["username"]
    names_p, prospect_cash = {}, {}
    for p in Prospect.objects.filter(id__in=prospect_ids).values(
        "id", "full_name", "entity_name", "cash_buyer"
    ):
        names_p[p["id"]] = p["full_name"] or p["entity_name"] or f"Prospect {p['id']}"
        prospect_cash[p["id"]] = p["cash_buyer"]
    return names_u, names_p, prospect_cash


def _score_candidate(
    *,
    key,
    name,
    registered,
    purchases,
    box,
    related,
    prop,
    price,
    today,
    radius_mi,
    prospect_cash=None,
) -> dict:
    n_total = len(purchases)
    n_near = sum(1 for p in purchases if p["near"])
    dates = [p["purchased_at"] for p in purchases if p["purchased_at"]]
    months = round((today - max(dates)).days / 30.44) if dates else None
    cash_votes = [p["cash_buyer"] for p in purchases if p["cash_buyer"] is not None]
    is_cash = (
        (sum(1 for c in cash_votes if c) / len(cash_votes) >= 0.5)
        if cash_votes
        else bool(prospect_cash)
    )
    p_prices = [p["price"] for p in purchases if p["price"] is not None]
    hist_lo, hist_hi = (min(p_prices), max(p_prices)) if p_prices else (None, None)
    if hist_lo is None and box is not None:
        hist_lo = float(box.price_min) if box.price_min is not None else None
        hist_hi = float(box.price_max) if box.price_max is not None else None
    strategy = _dominant_strategy(
        [p["disposition"] for p in purchases if p["disposition"]],
        getattr(box, "strategy", None),
    )

    feats = {
        "bought_in_area": _bought_in_area(n_near),
        "price_band": _price_band_fit(price, hist_lo, hist_hi),
        "strategy": _strategy_fit(strategy, prop.condition),
        "recency": _recency(months),
        "volume": _volume(n_total),
        "cash": 1.0 if is_cash else 0.3,
        "relationship": 1.0 if related else 0.0,
    }
    score = sum(RANK_WEIGHTS[k] * v for k, v in feats.items())
    completeness = _completeness(box, prop, price) if registered else 0.0
    score += BUY_BOX_BONUS * completeness

    ctx = {
        "n_near": n_near,
        "n_total": n_total,
        "months": months,
        "cash": is_cash,
        "strategy": strategy,
        "radius": radius_mi,
    }
    kind, ident = key
    return {
        "kind": kind,
        "user_id": ident if kind == "user" else None,
        "prospect_id": ident if kind == "prospect" else None,
        "name": name,
        "registered": registered,
        "score": round(score, 4),
        "reason": _reason(feats, ctx),
        "features": {k: round(v, 3) for k, v in feats.items()},
        "buy_box_completeness": round(completeness, 3),
        "n_purchases": n_total,
        "n_nearby": n_near,
        "cash": is_cash,
    }


# =====================================================================================
# Deal assessment (P3.4, matching_and_data §3) — the buyer auto-responder's
# qualify / hold / decline verdict. Deterministic wholesale math over the same comp
# engine `estimate_value` uses, so Stage 1's decision is grounded, not vibes
# (architecture §5). No LLM: the model narrates this verdict, it never computes it.
# =====================================================================================

# Rehab $/sqft by KC condition (1=full gut … 5=turnkey): a value-add deal costs more
# to bring to ARV-ready. Grounds est_rehab in the subject's real condition.
_REHAB_PSF_BY_CONDITION = {1: 60, 2: 45, 3: 25, 4: 10, 5: 0}
_DEFAULT_REHAB_PSF = 30  # unknown condition → a mid estimate

WHOLESALE_FEE = 10_000  # the assignment fee a wholesaler expects to clear on top

# Minimum spread margin (spread / ARV) each strategy needs before a deal "bites".
_MARGIN_THRESHOLD_BY_STRATEGY = {
    "fix_flip": 0.20,
    "brrrr": 0.15,
    "buy_hold": 0.10,
    "wholesale": 0.12,
}
_DEFAULT_MARGIN_THRESHOLD = 0.15
_HOLD_BAND = 0.05  # within this of the threshold → borderline → hold (don't decline)


def _est_rehab(condition, sqft) -> int | None:
    if not sqft:
        return None
    psf = _REHAB_PSF_BY_CONDITION.get(condition, _DEFAULT_REHAB_PSF)
    return int(psf * sqft)


def assess_deal(listing_id: int, *, strategy: str | None = None, min_n: int = 5) -> dict:
    """Wholesale spread vs the buyer's strategy threshold → qualify / hold / decline.

    `strategy` (the buyer's dominant strategy) picks the margin threshold; the caller
    derives it from the buyer's buy-box / purchase history. Missing inputs (no ARV,
    thin comps, no asking) → **hold and ask**, never a blind decline.
    """
    from catalog.models import Listing, ListingProperty

    listing = Listing.objects.filter(id=listing_id).first()
    if listing is None:
        return {"verdict": "hold", "error": f"listing {listing_id} not found"}
    lp = ListingProperty.objects.filter(listing_id=listing_id).select_related("property").first()
    prop = lp.property if lp else None
    if prop is None:
        return {"verdict": "hold", "error": "listing has no property"}

    asking = (
        float(listing.asking_price)
        if listing.asking_price is not None
        else (float(lp.asking_price) if lp.asking_price is not None else None)
    )
    arv_res = estimate_value(prop, arv=True, min_n=min_n)
    arv = arv_res["point"]
    threshold = _MARGIN_THRESHOLD_BY_STRATEGY.get(strategy, _DEFAULT_MARGIN_THRESHOLD)
    est_rehab = _est_rehab(prop.condition, prop.sqft)

    base = {
        "arv": arv,
        "asking": asking,
        "est_rehab": est_rehab,
        "wholesale_fee": WHOLESALE_FEE,
        "strategy": strategy,
        "threshold": threshold,
        "basis": arv_res["basis"],
    }

    # Can't price the deal → hold and chase info, don't guess a decline.
    if arv is None or asking is None or est_rehab is None or not arv_res["basis"]["met_min_n"]:
        missing = []
        if arv is None:
            missing.append("no ARV")
        if not arv_res["basis"]["met_min_n"]:
            missing.append("thin comps")
        if asking is None:
            missing.append("no asking price")
        if est_rehab is None:
            missing.append("unknown size")
        return {
            **base,
            "verdict": "hold",
            "spread": None,
            "margin_pct": None,
            "rationale": (
                "Can't fully price this yet ("
                + ", ".join(missing)
                + ") — need more listing detail before qualifying."
            ),
        }

    spread = arv - asking - est_rehab - WHOLESALE_FEE
    margin_pct = spread / arv if arv else 0.0
    if margin_pct >= threshold:
        verdict = "qualify"
    elif margin_pct >= threshold - _HOLD_BAND:
        verdict = "hold"
    else:
        verdict = "decline"

    rationale = (
        f"ARV ~${arv:,.0f}, asking ${asking:,.0f}, est. rehab ${est_rehab:,.0f} "
        f"(condition {prop.condition if prop.condition is not None else '?'}), "
        f"${WHOLESALE_FEE:,.0f} fee → spread ${spread:,.0f} ({margin_pct:.0%} margin) "
        f"vs {threshold:.0%} target"
        + (f" for {strategy.replace('_', ' ')}" if strategy else "")
        + f": {verdict}."
    )
    return {
        **base,
        "verdict": verdict,
        "spread": round(spread),
        "margin_pct": round(margin_pct, 4),
        "rationale": rationale,
    }
