"""
P5 (buy-box REST lift) — the `/settings › Buy-boxes` API + the agent==API guarantee.

The buy-box create/read/update/delete logic was lifted from the copilot `dal` into
`catalog.services` so BOTH the REST endpoint and the copilot's buy-box tools call the ONE
seam. These tests exercise the REST surface (CRUD, inline deal-settings + geo, owner
scoping) and assert **parity**: a box written over REST is visible through the copilot dal
seam with the identical shape, and vice-versa. LLM-free.
"""

from __future__ import annotations

import pytest
from django.contrib.auth import get_user_model
from rest_framework.test import APIClient

from catalog import services
from catalog.models import BuyBox, BuyBoxGeo, Mandate
from polaris_agent import dal

User = get_user_model()

BOXES = "/api/buy-boxes/"


def _user(email, **kw):
    return User.objects.create_user(email=email, password="pw-12345678", is_email_verified=True, **kw)


def _client(user):
    c = APIClient()
    c.force_authenticate(user=user)
    return c


def _full_payload():
    return {
        "name": "KC flips",
        "strategy": "fix_flip",
        "price_max": 500000,
        "beds_min": 3,
        "property_types": ["sfr"],
        "ceiling_price": 480000,
        "must_haves": ["clear title"],
        "geo": {"geo_type": "radius", "center_lat": 47.6, "center_lon": -122.3, "radius_mi": 5},
    }


# --- REST CRUD ----------------------------------------------------------------
@pytest.mark.django_db
def test_create_lists_and_reads_with_geo_and_inline_mandate():
    user = _user("box@x.com")
    c = _client(user)

    created = c.post(BOXES, _full_payload(), format="json")
    assert created.status_code == 201, created.data
    box_id = created.data["buy_box_id"]
    assert created.data["strategy"] == "fix_flip"
    assert created.data["mandate"]["ceiling_price"] == 480000.0
    assert created.data["mandate"]["must_haves"] == ["clear title"]
    assert created.data["n_geos"] == 1
    geo = created.data["geos"][0]
    assert geo["geo_type"] == "radius" and geo["radius_mi"] == 5.0
    assert geo["center_lat"] == pytest.approx(47.6) and geo["center_lon"] == pytest.approx(-122.3)

    # It persisted (box + geo + mandate).
    assert BuyBox.objects.filter(id=box_id, buyer=user).exists()
    assert BuyBoxGeo.objects.filter(buy_box_id=box_id).count() == 1
    assert Mandate.objects.filter(buy_box_id=box_id).exists()

    # List + retrieve see it.
    listed = c.get(BOXES)
    assert any(b["buy_box_id"] == box_id for b in listed.data)
    assert c.get(f"{BOXES}{box_id}/").data["buy_box_id"] == box_id


@pytest.mark.django_db
def test_update_edits_scalars_and_inline_mandate():
    user = _user("box@x.com")
    c = _client(user)
    box_id = c.post(BOXES, _full_payload(), format="json").data["buy_box_id"]

    updated = c.patch(
        f"{BOXES}{box_id}/", {"price_max": 550000, "instructions": "no flood zone"}, format="json"
    )
    assert updated.status_code == 200
    assert updated.data["price_max"] == 550000.0
    assert updated.data["mandate"]["instructions"] == "no flood zone"
    # The pre-existing must_haves survive a partial update (mandate upsert, not replace).
    assert updated.data["mandate"]["must_haves"] == ["clear title"]


@pytest.mark.django_db
def test_delete_cascades_geo_and_mandate():
    user = _user("box@x.com")
    c = _client(user)
    box_id = c.post(BOXES, _full_payload(), format="json").data["buy_box_id"]

    deleted = c.delete(f"{BOXES}{box_id}/")
    assert deleted.status_code == 200 and deleted.data["deleted"] is True
    assert not BuyBox.objects.filter(id=box_id).exists()
    assert not BuyBoxGeo.objects.filter(buy_box_id=box_id).exists()
    assert not Mandate.objects.filter(buy_box_id=box_id).exists()
    assert c.get(f"{BOXES}{box_id}/").status_code == 404


@pytest.mark.django_db
def test_minimal_create_defaults_and_no_geo():
    user = _user("box@x.com")
    c = _client(user)
    created = c.post(BOXES, {"name": "Rentals", "strategy": "buy_hold"}, format="json")
    assert created.status_code == 201
    assert created.data["n_geos"] == 0 and created.data["geos"] == []
    assert created.data["mandate"] is None


# --- owner scoping ------------------------------------------------------------
@pytest.mark.django_db
def test_buy_boxes_are_owner_scoped():
    owner, other = _user("owner@x.com"), _user("other@x.com")
    box_id = _client(owner).post(BOXES, _full_payload(), format="json").data["buy_box_id"]

    co = _client(other)
    assert co.get(BOXES).data == []  # not in the stranger's list
    assert co.get(f"{BOXES}{box_id}/").status_code == 404
    assert co.patch(f"{BOXES}{box_id}/", {"price_max": 1}, format="json").status_code == 404
    assert co.delete(f"{BOXES}{box_id}/").status_code == 404
    # untouched
    assert BuyBox.objects.get(id=box_id).price_max == 500000


# --- agent == API parity ------------------------------------------------------
@pytest.mark.django_db
def test_rest_and_copilot_dal_share_one_seam():
    """A box written over REST is visible through the copilot dal seam (same shape), and a
    box written through the dal is visible over REST — one seam, `catalog.services`."""
    user = _user("box@x.com")
    c = _client(user)

    # REST-created → copilot dal sees it identically.
    rest_id = c.post(BOXES, _full_payload(), format="json").data["buy_box_id"]
    via_dal = dal._get_buy_box(user.id, rest_id)
    assert via_dal["buy_box_id"] == rest_id
    assert via_dal["mandate"]["ceiling_price"] == 480000.0
    assert via_dal == c.get(f"{BOXES}{rest_id}/").data

    # dal-created (the copilot's flat contract) → REST lists + reads it.
    dal_id = dal._create_buy_box(user.id, {"name": "Agent box", "strategy": "brrrr"})["buy_box_id"]
    ids = {b["buy_box_id"] for b in c.get(BOXES).data}
    assert {rest_id, dal_id} <= ids
    assert c.get(f"{BOXES}{dal_id}/").data["name"] == "Agent box"
