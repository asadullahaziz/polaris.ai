"""
Matching-engine verification — comps / valuation / assess_deal / rank_buyers.

All LLM-free: a small synthetic comp cluster (fast, no CSV). The seed's idempotency
+ date rebase live in test_seed.py.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model
from django.contrib.gis.geos import Point
from django.utils import timezone

from catalog.models import Listing, ListingProperty, Property, Sale
from matching.engine import (
    assess_deal,
    estimate_value,
    get_comps,
    rank_buyers,
    rank_buyers_for_attrs,
)

User = get_user_model()


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


# --- comps / valuation --------------------------------------------------------
@pytest.mark.django_db
def test_get_comps_finds_similar_and_gates_waterfront():
    subj = _mk(-122.330, 47.600, price="500000", apn="subject")
    for i in range(8):
        _mk(
            -122.330 + 0.002 * i,
            47.601,
            sqft=1900 + 20 * i,
            price=str(480000 + 6000 * i),
            apn=f"c{i}",
        )
    _mk(-122.331, 47.600, waterfront=True, price="2000000", apn="wf")  # must be excluded

    res = get_comps(subj, min_n=5)
    assert res["met_min_n"] and res["n"] >= 5
    assert res["relaxed"].startswith("1mi")
    assert all(c["waterfront"] is False for c in res["comps"])
    assert all(c["id"] != subj.pk for c in res["comps"])


@pytest.mark.django_db
def test_estimate_value_range_is_sane():
    subj = _mk(-122.330, 47.600, sqft=2000, price="500000", apn="subject")
    for i in range(8):
        _mk(-122.330 + 0.002 * i, 47.601, sqft=2000, price=str(500000 + 10000 * i), apn=f"c{i}")

    ev = estimate_value(subj, min_n=5)
    assert ev["low"] <= ev["point"] <= ev["high"]
    assert ev["basis"]["n_comps"] >= 5
    assert 450_000 <= ev["point"] <= 650_000


@pytest.mark.django_db
def test_get_comps_fallback_when_isolated():
    subj = _mk(-120.000, 46.000, price="300000", apn="lonely")
    res = get_comps(subj, min_n=5)
    assert not res["met_min_n"]
    assert res["radius_mi"] == 5.0  # exhausted the widest stage


# --- assess_deal divergence ---------------------------------------------------
def _listing_with_comps(asking: str, *, condition=3):
    """A subject listing near a good-condition comp cluster (so ARV resolves)."""
    subj = _mk(-122.330, 47.600, sqft=2000, condition=condition, price="500000", apn="subj")
    for i in range(8):  # turnkey comps (condition 4) ~ $250/sqft → ARV ~ $500k
        _mk(
            -122.330 + 0.002 * i,
            47.601,
            sqft=2000,
            condition=4,
            price=str(490000 + 5000 * i),
            apn=f"ac{i}",
        )
    seller = User.objects.create_user(email="seller_ad@x.com", password="pw-12345678")
    listing = Listing.objects.create(seller=seller, asking_price=Decimal(asking), status="active")
    ListingProperty.objects.create(listing=listing, property=subj, asking_price=Decimal(asking))
    return listing


@pytest.mark.django_db
def test_assess_deal_qualifies_a_cheap_deal():
    listing = _listing_with_comps("300000")  # deep discount → healthy spread
    res = assess_deal(listing.id, strategy="fix_flip")
    assert res["verdict"] == "qualify"
    assert res["spread"] > 0
    assert res["arv"] is not None


@pytest.mark.django_db
def test_assess_deal_declines_an_overpriced_deal():
    listing = _listing_with_comps("495000")  # asking ~ ARV → no spread
    res = assess_deal(listing.id, strategy="fix_flip")
    assert res["verdict"] == "decline"
    assert res["spread"] < 0


@pytest.mark.django_db
def test_assess_deal_holds_when_unpriceable():
    """No comps → thin ARV → hold-and-ask, never a blind decline."""
    seller = User.objects.create_user(email="seller_h@x.com", password="pw-12345678")
    subj = _mk(-119.0, 45.0, sqft=1800, price="200000", apn="iso")  # isolated → no comps
    listing = Listing.objects.create(seller=seller, asking_price=Decimal("150000"), status="active")
    ListingProperty.objects.create(listing=listing, property=subj, asking_price=Decimal("150000"))
    res = assess_deal(listing.id, strategy="fix_flip")
    assert res["verdict"] == "hold"


# --- rank_buyers --------------------------------------------------------------
def _sale(user, lon, lat, *, days_ago, cash, price="450000", disp="flip"):
    return Sale.objects.create(
        buyer=user,
        geom=Point(lon, lat, srid=4326),
        price=Decimal(price),
        purchased_at=timezone.now().date() - dt.timedelta(days=days_ago),
        cash_buyer=cash,
        disposition=disp,
        source="test",
    )


@pytest.mark.django_db
def test_rank_buyers_orders_and_is_deterministic():
    seller = User.objects.create_user(email="s_rank@x.com", password="pw-12345678")
    subj = _mk(-122.330, 47.600, sqft=2000, condition=2, price="500000", apn="ranksubj")
    listing = Listing.objects.create(seller=seller, asking_price=Decimal("450000"), status="active")
    ListingProperty.objects.create(listing=listing, property=subj, asking_price=Decimal("450000"))

    # Strong buyer: 3 nearby, recent, all-cash flips.
    strong = User.objects.create_user(
        email="strong@x.com", password="pw-12345678", full_name="Strong"
    )
    for i in range(3):
        _sale(strong, -122.330 + 0.001 * i, 47.600, days_ago=30, cash=True)
    # Weak buyer: 1 older nearby purchase.
    weak = User.objects.create_user(email="weak@x.com", password="pw-12345678", full_name="Weak")
    _sale(weak, -122.331, 47.601, days_ago=500, cash=False)

    res = rank_buyers(listing.id)
    ranked = res["ranked"]
    ids = [r["user_id"] for r in ranked]
    assert strong.id in ids and weak.id in ids
    # Sorted by score desc; the strong buyer leads.
    scores = [r["score"] for r in ranked]
    assert scores == sorted(scores, reverse=True)
    assert ranked[0]["user_id"] == strong.id
    assert ids.index(strong.id) < ids.index(weak.id)

    # Deterministic: a second call returns identical ordering + scores.
    res2 = rank_buyers(listing.id)
    assert [(r["user_id"], r["score"]) for r in res2["ranked"]] == [
        (r["user_id"], r["score"]) for r in ranked
    ]


@pytest.mark.django_db
def test_rank_buyers_adhoc_matches_listing_based():
    """The ad-hoc entry point over raw attrs equals the persisted-listing path."""
    seller = User.objects.create_user(email="s_adhoc@x.com", password="pw-12345678")
    subj = _mk(-122.330, 47.600, sqft=2000, condition=2, price="500000", apn="adhocsubj")
    listing = Listing.objects.create(seller=seller, asking_price=Decimal("450000"), status="active")
    ListingProperty.objects.create(listing=listing, property=subj, asking_price=Decimal("450000"))

    buyer = User.objects.create_user(email="b_adhoc@x.com", password="pw-12345678", full_name="B")
    for i in range(2):
        _sale(buyer, -122.330 + 0.001 * i, 47.600, days_ago=60, cash=True)

    listing_based = rank_buyers(listing.id)
    adhoc = rank_buyers_for_attrs(
        geom=subj.geom,
        price=450000.0,
        condition=2,
        beds=subj.beds,
        sqft=subj.sqft,
        property_type="sfr",
        seller_id=seller.id,
    )
    assert [(r["user_id"], r["score"]) for r in adhoc["ranked"]] == [
        (r["user_id"], r["score"]) for r in listing_based["ranked"]
    ]
