"""
P1.5 engine + P1.SEED verification (implementation_plan P1 "Verification").

Engine tests use a small synthetic cluster (fast, no LLM, no CSV). The seed test
runs the real seed_kc to lock in idempotency + the date rebase.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

import pytest
from django.contrib.gis.geos import Point
from django.core.management import call_command
from django.utils import timezone

from buyers.models import Purchase
from catalog.models import Property
from matching.engine import estimate_value, get_comps


def _mk(lon, lat, *, beds=3, sqft=2000, grade=7, condition=3, waterfront=False, price, apn):
    return Property.objects.create(
        apn=apn,
        county_fips="53033",
        address_norm=f"t:{apn}",
        address_raw=f"comp {apn}",
        geom=Point(lon, lat, srid=4326),
        property_type="sfr",
        beds=beds,
        sqft=sqft,
        grade=grade,
        condition=condition,
        waterfront=waterfront,
        last_sale_price=Decimal(price),
        last_sale_date=timezone.now().date() - dt.timedelta(days=30),
    )


@pytest.mark.django_db
def test_get_comps_finds_similar_and_gates_waterfront():
    # Subject near downtown Seattle.
    subj = _mk(-122.330, 47.600, price="500000", apn="subject")
    # 8 similar non-waterfront comps within ~0.2 mi (varied ppsf 240–320).
    for i in range(8):
        _mk(
            -122.330 + 0.002 * i,
            47.601,
            sqft=1900 + 20 * i,
            price=str(480000 + 6000 * i),
            apn=f"c{i}",
        )
    # A waterfront comp right next door with an inflated price — must be EXCLUDED.
    _mk(-122.331, 47.600, waterfront=True, price="2000000", apn="wf")

    res = get_comps(subj, min_n=5)
    assert res["met_min_n"] and res["n"] >= 5
    assert res["relaxed"].startswith("1mi")  # base stage sufficed
    assert all(c["waterfront"] is False for c in res["comps"])  # waterfront gated out
    assert all(c["id"] != subj.pk for c in res["comps"])  # subject excluded


@pytest.mark.django_db
def test_estimate_value_range_is_sane():
    subj = _mk(-122.330, 47.600, sqft=2000, price="500000", apn="subject")
    for i in range(8):
        _mk(-122.330 + 0.002 * i, 47.601, sqft=2000, price=str(500000 + 10000 * i), apn=f"c{i}")

    ev = estimate_value(subj, min_n=5)
    assert ev["low"] <= ev["point"] <= ev["high"]
    assert ev["basis"]["n_comps"] >= 5
    # comps are ~ $250–290/sqft on 2000 sqft → point in a believable band.
    assert 450_000 <= ev["point"] <= 650_000


@pytest.mark.django_db
def test_get_comps_fallback_when_isolated():
    # Subject far from any comp → every stage relaxes, still nothing found.
    subj = _mk(-120.000, 46.000, price="300000", apn="lonely")
    res = get_comps(subj, min_n=5)
    assert not res["met_min_n"]
    assert res["radius_mi"] == 5.0  # exhausted the widest stage


@pytest.mark.django_db
def test_seed_kc_idempotent_and_date_rebased():
    call_command("seed_kc")
    n_prop = Property.objects.filter(county_fips="53033").count()
    n_buy = Purchase.objects.count()
    assert n_prop > 20_000
    assert n_buy > 0

    # Re-run must be a no-op (idempotency).
    call_command("seed_kc")
    assert Property.objects.filter(county_fips="53033").count() == n_prop
    assert Purchase.objects.count() == n_buy

    # Date rebase: newest sale lands within ~24 months of the demo date.
    today = timezone.now().date()
    newest = Property.objects.filter(county_fips="53033").latest("last_sale_date").last_sale_date
    assert today - dt.timedelta(days=800) <= newest <= today
