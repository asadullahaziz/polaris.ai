"""
Deterministic comping / valuation engine (matching_and_data §3, implementation_plan P1.5).

"The engine scores, the LLM narrates." This module is pure SQL/PostGIS/Python — no
model calls — so it is reproducible, explainable, and unit-testable without an LLM.
`polaris_agent` only *wraps* these as tools and narrates the breakdown.

Public API (P1):
  * get_comps(subject, ...)       → nearest recent similar SOLD properties, with a
                                     staged fallback that records how far it relaxed.
  * estimate_value(subject, ...)  → a value range from comp $/sqft; `arv=True` comps
                                     against good-condition sales (the after-repair view).

`subject` is a `catalog.Property` or a dict of {geom, beds, sqft, grade, waterfront,
condition, pk} — so it works on a saved listing OR freshly-extracted attributes.
"""

from __future__ import annotations

import datetime as dt
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
