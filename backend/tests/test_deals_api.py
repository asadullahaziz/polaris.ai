"""
/api/deals/ (mini CRM REST) — list scoping (only a party's own deals), side/stage/
listing filters, the serialized row shape, PATCH stage override (membership-checked),
and 404/400 behavior.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model
from rest_framework.test import APIClient

from catalog.models import Listing, ListingProperty, Property
from chat import services as chat_services
from deals import service as deal_svc

User = get_user_model()

DEALS = "/api/deals/"


def _user(email, **kw):
    return User.objects.create_user(
        email=email, password="pw-12345678", is_email_verified=True, **kw
    )


def _client(user):
    c = APIClient()
    c.force_authenticate(user=user)
    return c


def _listing(seller, address="1 Pike St", price="400000"):
    lst = Listing.objects.create(
        seller=seller, title="A home", asking_price=Decimal(price), status="active"
    )
    prop = Property.objects.create(address_raw=address, address_norm=address.lower(), beds=3)
    ListingProperty.objects.create(listing=lst, property=prop, sort_order=0)
    return lst


@pytest.fixture
def world(db):
    seller = _user("seller@x.com", full_name="Sal Seller")
    buyer = _user("buyer@x.com", full_name="Betty Buyer")
    listing = _listing(seller)
    chat, _ = chat_services.get_or_create_chat(seller.id, buyer.id)
    deal = deal_svc.ensure_deal(
        listing_id=listing.id, buyer_id=buyer.id, seller_id=seller.id, chat_id=chat.id
    )
    return seller, buyer, listing, chat, deal


@pytest.mark.django_db
def test_list_scopes_to_own_deals_with_side_and_shape(world):
    seller, buyer, listing, chat, deal = world
    rows = _client(seller).get(DEALS).data
    assert len(rows) == 1
    row = rows[0]
    assert row["side"] == "selling"
    assert row["stage"] == "contacted"
    assert row["listing"]["address"] == "1 Pike St"
    assert row["counterparty"]["name"] == "Betty Buyer"
    assert row["chat_id"] == chat.id

    assert _client(buyer).get(DEALS).data[0]["side"] == "buying"

    outsider = _user("nosy@x.com")
    assert _client(outsider).get(DEALS).data == []


@pytest.mark.django_db
def test_list_filters(world):
    seller, buyer, listing, chat, deal = world
    c = _client(seller)
    assert len(c.get(DEALS, {"side": "selling"}).data) == 1
    assert c.get(DEALS, {"side": "buying"}).data == []
    assert len(c.get(DEALS, {"stage": "contacted"}).data) == 1
    assert c.get(DEALS, {"stage": "agreed"}).data == []
    assert len(c.get(DEALS, {"listing": listing.id}).data) == 1


@pytest.mark.django_db
def test_patch_stage_override(world):
    seller, buyer, listing, chat, deal = world
    c = _client(seller)
    res = c.patch(f"{DEALS}{deal.id}/", {"stage": "closed"}, format="json")
    assert res.status_code == 200 and res.data["stage"] == "closed"
    # Any direction: the human corrects the CRM.
    res = c.patch(f"{DEALS}{deal.id}/", {"stage": "engaged"}, format="json")
    assert res.data["stage"] == "engaged"


@pytest.mark.django_db
def test_patch_validates_stage_and_membership(world):
    seller, buyer, listing, chat, deal = world
    assert (
        _client(seller)
        .patch(f"{DEALS}{deal.id}/", {"stage": "on_the_moon"}, format="json")
        .status_code
        == 400
    )
    outsider = _user("nosy2@x.com")
    assert (
        _client(outsider)
        .patch(f"{DEALS}{deal.id}/", {"stage": "closed"}, format="json")
        .status_code
        == 404
    )
