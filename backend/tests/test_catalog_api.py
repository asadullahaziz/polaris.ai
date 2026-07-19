"""
Catalog REST — multi-property create, detail, fetch-existing dedup lookup, mandate.

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
def test_attach_existing_property_with_overrides_keeps_base_untouched(client):
    existing = Property.objects.create(
        address_raw="600 Oak St", address_norm="600 oak st", beds=3, sqft=1800, condition=2
    )
    resp = client.post(
        LISTINGS,
        {
            "asking_price": "500000",
            "properties": [
                {
                    "property_id": existing.id,
                    "asking_price": "500000",
                    "overrides": {"condition": 5, "sqft": 2100},
                }
            ],
        },
        format="json",
    )
    assert resp.status_code == 201, resp.data
    pp = resp.data["properties"][0]
    assert pp["overrides"] == {"condition": 5, "sqft": 2100}
    assert pp["effective"]["condition"] == 5 and pp["effective"]["sqft"] == 2100
    assert set(pp["seller_stated_fields"]) == {"condition", "sqft"}
    # base Property still shows the immutable comp-basis values
    assert pp["property"]["condition"] == 2 and pp["property"]["sqft"] == 1800
    existing.refresh_from_db()
    assert existing.condition == 2 and existing.sqft == 1800
    assert Property.objects.count() == 1


@pytest.mark.django_db
def test_patch_listing_property_overrides_roundtrips_and_clears(client):
    existing = Property.objects.create(
        address_raw="12 Elm St", address_norm="12 elm st", beds=3, sqft=1500, condition=2
    )
    lid = client.post(
        LISTINGS,
        {"asking_price": "300000", "properties": [{"property_id": existing.id}]},
        format="json",
    ).data["id"]
    url = f"{LISTINGS}{lid}/properties/{existing.id}/"

    resp = client.patch(url, {"condition": 5, "yr_renovated": 2026}, format="json")
    assert resp.status_code == 200, resp.data
    pp = resp.data["properties"][0]
    assert pp["overrides"]["condition"] == 5 and pp["overrides"]["yr_renovated"] == 2026
    assert pp["effective"]["condition"] == 5

    # an explicit null clears one override, leaving the rest
    resp2 = client.patch(url, {"condition": None}, format="json")
    assert resp2.status_code == 200
    ov2 = resp2.data["properties"][0]["overrides"]
    assert "condition" not in ov2 and ov2["yr_renovated"] == 2026

    existing.refresh_from_db()  # base comp-basis row never touched
    assert existing.condition == 2 and existing.sqft == 1500


@pytest.mark.django_db
def test_patch_override_validation_rejects_out_of_range(client):
    existing = Property.objects.create(address_raw="9 Fir St", address_norm="9 fir st", condition=3)
    lid = client.post(
        LISTINGS,
        {"asking_price": "200000", "properties": [{"property_id": existing.id}]},
        format="json",
    ).data["id"]
    resp = client.patch(
        f"{LISTINGS}{lid}/properties/{existing.id}/", {"condition": 9}, format="json"
    )
    assert resp.status_code == 400  # condition must be 1-5


@pytest.mark.django_db
def test_patch_property_not_on_listing_is_404(client):
    existing = Property.objects.create(address_raw="3 Ash St", address_norm="3 ash st", condition=3)
    lid = client.post(
        LISTINGS,
        {"asking_price": "200000", "properties": [{"property_id": existing.id}]},
        format="json",
    ).data["id"]
    resp = client.patch(f"{LISTINGS}{lid}/properties/999999/", {"condition": 4}, format="json")
    assert resp.status_code == 404


@pytest.mark.django_db
def test_valuation_action_returns_condition_aware_current_value(client):
    """Through the real REST stack: the valuation action values the effective subject and
    returns a condition-aware current value that climbs when the seller states turnkey."""
    import datetime as dt
    from decimal import Decimal

    from django.contrib.gis.geos import Point
    from django.utils import timezone

    def mk(apn, lon, price="500000", **kw):
        return Property.objects.create(
            apn=apn,
            county_fips="53033",
            address_norm=f"v:{apn}",
            address_raw=f"comp {apn}",
            geom=Point(lon, 47.60, srid=4326),
            property_type="sfr",
            waterfront=False,
            last_sale_price=Decimal(price),
            last_sale_date=timezone.now().date() - dt.timedelta(days=30),
            **kw,
        )

    subj = mk("vsub", -122.330, beds=3, sqft=2000, grade=7, condition=2)
    for i in range(8):  # turnkey comp cluster so ARV resolves
        mk(
            f"vc{i}",
            -122.330 + 0.002 * i,
            price=str(490000 + 5000 * i),
            beds=3,
            sqft=2000,
            grade=7,
            condition=4,
        )
    lid = client.post(
        LISTINGS,
        {"asking_price": "450000", "properties": [{"property_id": subj.id}]},
        format="json",
    ).data["id"]

    base = client.get(f"{LISTINGS}{lid}/valuation/").data
    assert base["current_value"]["point"] is not None
    assert base["current_value"]["seller_stated"] is False
    base_cv = base["current_value"]["point"]

    # seller states turnkey → less rehab → current value climbs, flagged seller-stated
    client.patch(f"{LISTINGS}{lid}/properties/{subj.id}/", {"condition": 5}, format="json")
    after = client.get(f"{LISTINGS}{lid}/valuation/").data
    assert after["current_value"]["point"] > base_cv
    assert after["current_value"]["seller_stated"] is True


@pytest.mark.django_db
def test_patch_property_overrides_is_owner_scoped(client, other_client):
    existing = Property.objects.create(
        address_raw="7 Oakwood", address_norm="7 oakwood", condition=3
    )
    lid = client.post(
        LISTINGS,
        {
            "asking_price": "200000",
            "status": "active",
            "properties": [{"property_id": existing.id}],
        },
        format="json",
    ).data["id"]
    # another user cannot edit overrides on someone else's listing
    resp = other_client.patch(
        f"{LISTINGS}{lid}/properties/{existing.id}/", {"condition": 5}, format="json"
    )
    assert resp.status_code == 404


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
    """Listings are a marketplace: everyone sees active listings; drafts stay private.
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

    # Non-owner detail: visible for active, but the private mandate is withheld.
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
