"""
P1 catalog REST — multi-property create, detail, fetch-existing dedup lookup, mandate.

LLM-free (pure ORM/REST). The engine paths are covered in test_matching.
"""

from __future__ import annotations

import pytest
from django.contrib.auth import get_user_model
from rest_framework.test import APIClient

from catalog.models import Property

User = get_user_model()

LOOKUP = "/api/properties/lookup"
SEARCH = "/api/properties/search"
LISTINGS = "/api/listings/"


@pytest.fixture
def owner(db):
    return User.objects.create_user(
        email="owner@x.com", password="pw-12345678", is_email_verified=True, full_name="Owner"
    )


@pytest.fixture
def client(owner):
    c = APIClient()
    c.force_authenticate(user=owner)
    return c


@pytest.mark.django_db
def test_create_multi_property_listing_and_detail(client):
    payload = {
        "title": "Two-home package",
        "description": "A small bundle",
        "asking_price": "750000",
        "bundle_type": "package",
        "properties": [
            {
                "address": "123 Maple Ave, Seattle WA",
                "beds": 3,
                "sqft": 1800,
                "asking_price": "400000",
            },
            {
                "address": "77 Cedar St, Seattle WA",
                "beds": 2,
                "sqft": 1200,
                "asking_price": "350000",
            },
        ],
        "mandate": {
            "floor_price": "700000",
            "must_haves": ["clear title"],
            "instructions": "cash pref",
        },
    }
    resp = client.post(LISTINGS, payload, format="json")
    assert resp.status_code == 201, resp.data
    lid = resp.data["id"]
    assert len(resp.data["properties"]) == 2
    assert resp.data["bundle_type"] == "package"
    assert resp.data["mandate"]["exists"] is True
    assert resp.data["mandate"]["floor_price"] == 700000.0

    # Two Property rows were created (one per address).
    assert Property.objects.count() == 2

    # Detail route returns the same nested shape.
    detail = client.get(f"{LISTINGS}{lid}/")
    assert detail.status_code == 200
    assert len(detail.data["properties"]) == 2
    addrs = {p["property"]["address_raw"] for p in detail.data["properties"]}
    assert "123 Maple Ave, Seattle WA" in addrs


@pytest.mark.django_db
def test_property_lookup_dedup_is_case_and_suffix_insensitive(client):
    # Create a listing → creates a Property with a normalized address.
    client.post(
        LISTINGS,
        {"asking_price": "400000", "properties": [{"address": "123 Maple Avenue, Seattle WA"}]},
        format="json",
    )
    # A differently-cased / abbreviated spelling of the same address hits the dedup.
    hit = client.get(LOOKUP, {"address": "123 maple ave seattle wa"})
    assert hit.status_code == 200
    assert hit.data["found"] is True
    assert hit.data["property"]["address_raw"] == "123 Maple Avenue, Seattle WA"

    miss = client.get(LOOKUP, {"address": "999 Nowhere Blvd"})
    assert miss.data["found"] is False


@pytest.mark.django_db
def test_property_search_typeahead(client):
    from catalog.services import normalize_address

    for raw in ("412 Alder St, Norhaven, WA 98115", "204 Maple Ave, Norhaven, WA 98115"):
        Property.objects.create(address_raw=raw, address_norm=normalize_address(raw), beds=3)

    # Fragment hits (case-insensitive, partial).
    res = client.get(SEARCH, {"q": "alder"})
    assert res.status_code == 200
    assert [r["address_raw"] for r in res.data["results"]] == ["412 Alder St, Norhaven, WA 98115"]

    # Suffix canonicalization: "Alder Street" finds "Alder St".
    res = client.get(SEARCH, {"q": "412 Alder Street"})
    assert len(res.data["results"]) == 1

    # Town-name search spans properties; limit caps the page.
    res = client.get(SEARCH, {"q": "norhaven", "limit": 1})
    assert len(res.data["results"]) == 1

    # Too-short queries return nothing (no full-table dumps).
    assert client.get(SEARCH, {"q": "x"}).data["results"] == []

    # Auth required.
    assert APIClient().get(SEARCH, {"q": "alder"}).status_code in (401, 403)


@pytest.mark.django_db
def test_attach_existing_property_is_not_mutated(client):
    existing = Property.objects.create(
        address_raw="500 Pine St", address_norm="500 pine st", beds=4, sqft=2500, condition=5
    )
    resp = client.post(
        LISTINGS,
        {
            "asking_price": "600000",
            "properties": [{"property_id": existing.id, "asking_price": "600000"}],
        },
        format="json",
    )
    assert resp.status_code == 201
    assert len(resp.data["properties"]) == 1
    assert resp.data["properties"][0]["property"]["id"] == existing.id
    # The shared comp-basis Property row is untouched.
    existing.refresh_from_db()
    assert existing.beds == 4 and existing.sqft == 2500 and existing.condition == 5
    # No duplicate Property was created.
    assert Property.objects.count() == 1


@pytest.mark.django_db
def test_listing_requires_at_least_one_property(client):
    resp = client.post(LISTINGS, {"asking_price": "100000", "properties": []}, format="json")
    assert resp.status_code == 400


@pytest.fixture
def other_client(db):
    other = User.objects.create_user(
        email="other@x.com", password="pw-12345678", is_email_verified=True, full_name="Other"
    )
    c = APIClient()
    c.force_authenticate(user=other)
    return c


@pytest.mark.django_db
def test_marketplace_visibility(client, other_client):
    """Listings are a marketplace: everyone sees ACTIVE listings; drafts stay private.
    The mandate (floor/ceiling/instructions) is serialized only for the owner."""
    active = client.post(
        LISTINGS,
        {
            "asking_price": "400000",
            "properties": [{"address": "1 A St"}],
            "mandate": {"floor_price": "380000"},
        },
        format="json",
    ).data  # status defaults to active
    draft = client.post(
        LISTINGS,
        {"asking_price": "200000", "status": "draft", "properties": [{"address": "2 B St"}]},
        format="json",
    ).data

    # The other user's list shows the active listing (with seller identity), not the draft.
    rows = other_client.get(LISTINGS).data
    ids = {r["id"] for r in rows}
    assert active["id"] in ids and draft["id"] not in ids
    row = next(r for r in rows if r["id"] == active["id"])
    assert row["seller"]["name"] == "Owner"

    # ?mine=1 narrows back to own-only.
    assert other_client.get(LISTINGS, {"mine": "1"}).data == []
    mine = client.get(LISTINGS, {"mine": "1"}).data
    assert {r["id"] for r in mine} == {active["id"], draft["id"]}

    # Non-owner detail: visible for active, but the PRIVATE mandate is withheld.
    det = other_client.get(f"{LISTINGS}{active['id']}/")
    assert det.status_code == 200
    assert det.data["mandate"] is None
    assert det.data["seller"]["name"] == "Owner"
    assert other_client.get(f"{LISTINGS}{draft['id']}/").status_code == 404

    # The owner still gets their mandate on the same route.
    own = client.get(f"{LISTINGS}{active['id']}/")
    assert own.data["mandate"]["exists"] is True
    assert own.data["mandate"]["floor_price"] == 380000.0


@pytest.mark.django_db
def test_mutations_and_seller_tools_stay_owner_scoped(client, other_client):
    lid = client.post(
        LISTINGS, {"asking_price": "400000", "properties": [{"address": "1 A St"}]}, format="json"
    ).data[
        "id"
    ]  # active → visible to the other user, but never editable
    assert (
        other_client.patch(f"{LISTINGS}{lid}/", {"title": "hijack"}, format="json").status_code
        == 404
    )
    assert (
        other_client.put(f"{LISTINGS}{lid}/", {"title": "hijack"}, format="json").status_code == 404
    )
    assert other_client.get(f"{LISTINGS}{lid}/mandate/").status_code == 404
    assert (
        other_client.put(
            f"{LISTINGS}{lid}/mandate/", {"floor_price": "1"}, format="json"
        ).status_code
        == 404
    )
    assert other_client.get(f"{LISTINGS}{lid}/valuation/").status_code == 404
    assert other_client.get(f"{LISTINGS}{lid}/comps/").status_code == 404
