"""
P6 seam adds — the three small REST endpoints the ShadCN frontend needs that P0–P5
didn't expose: the `/buyers` ad-hoc rank endpoint (agent == API for `find_buyers`),
`agent_reply_cap` on the /me profile surface (edited in `/settings › AI`), and
`discard-draft` (the third leg of the chat draft-approval UI: approve / edit-and-send /
discard). LLM-free.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model
from django.contrib.gis.geos import Point
from django.utils import timezone
from rest_framework.test import APIClient

from catalog.models import BuyBox, Property, Sale
from chat import responder_service as svc
from chat import services as chat_services

User = get_user_model()

RANK = "/api/buyers/rank"
ME = "/api/auth/me/"


def _user(email, **kw):
    return User.objects.create_user(
        email=email, password="pw-12345678", is_email_verified=True, **kw
    )


def _client(user):
    c = APIClient()
    c.force_authenticate(user=user)
    return c


# --- /api/buyers/rank -----------------------------------------------------------
@pytest.mark.django_db
def test_rank_requires_address():
    c = _client(_user("s@x.com"))
    assert c.get(RANK).status_code == 400


@pytest.mark.django_db
def test_rank_unknown_address_degrades_to_empty():
    c = _client(_user("s@x.com"))
    res = c.get(RANK, {"address": "nowhere at all"})
    assert res.status_code == 200
    assert res.data["ranked"] == [] and res.data["resolved"] is False


@pytest.mark.django_db
def test_rank_resolves_address_and_ranks_nearby_buyer():
    seller = _user("s@x.com")
    buyer = _user("b@x.com", full_name="Cash Buyer")

    subject = Property.objects.create(
        apn="subj",
        county_fips="53033",
        address_norm="123 main st seattle",
        address_raw="123 Main St, Seattle",
        geom=Point(-122.330, 47.600, srid=4326),
        property_type="sfr",
        beds=3,
        sqft=2000,
    )
    bought = Property.objects.create(
        apn="hist",
        county_fips="53033",
        address_norm="125 main st seattle",
        address_raw="125 Main St, Seattle",
        geom=Point(-122.331, 47.601, srid=4326),
        property_type="sfr",
        beds=3,
        sqft=1900,
    )
    Sale.objects.create(
        buyer=buyer,
        property=bought,
        geom=bought.geom,
        price=Decimal("450000"),
        purchased_at=timezone.now().date() - dt.timedelta(days=90),
        cash_buyer=True,
    )
    BuyBox.objects.create(buyer=buyer, name="KC", strategy="fix_flip", is_active=True)

    res = _client(seller).get(RANK, {"address": subject.address_raw, "price": 400000})
    assert res.status_code == 200, res.data
    assert res.data["resolved"] is True
    ranked = res.data["ranked"]
    assert any(r["user_id"] == buyer.id for r in ranked)
    top = next(r for r in ranked if r["user_id"] == buyer.id)
    assert top["name"] == "Cash Buyer" and top["score"] > 0 and top["reason"]


# --- agent_reply_cap on /me -------------------------------------------------------
@pytest.mark.django_db
def test_agent_reply_cap_round_trips_through_me():
    u = _user("cap@x.com")
    c = _client(u)

    assert c.get(ME).data["profile"]["agent_reply_cap"] == 3  # model default

    patched = c.patch(ME, {"agent_reply_cap": 5}, format="json")
    assert patched.status_code == 200
    assert patched.data["profile"]["agent_reply_cap"] == 5

    # The responder's cap resolution reads the same field.
    from users.models import UserProfile

    assert UserProfile.objects.get(user=u).agent_reply_cap == 5
    assert c.patch(ME, {"agent_reply_cap": 0}, format="json").status_code == 400


# --- discard-draft ----------------------------------------------------------------
@pytest.fixture
def draft(db):
    """principal P with an away-agent draft in the pair chat with counterparty C."""
    p = _user("p@x.com")
    c = _user("c2@x.com")
    chat, _ = chat_services.get_or_create_chat(p.id, c.id)
    inbound = chat_services.post_human_message(chat.id, c.id, "would you take 400k?")
    d = svc.persist_draft(
        chat.id,
        principal_id=p.id,
        action="inform",
        body="Let me check with them.",
        disclosed_fields={},
        inbound_message_id=inbound["id"],
    )
    return p, c, chat, d["message_id"]


@pytest.mark.django_db
def test_discard_draft_deletes_owner_draft(draft):
    p, c, chat, msg_id = draft
    res = _client(p).post(f"/api/chats/{chat.id}/discard-draft/", {"message_id": msg_id})
    assert res.status_code == 200 and res.data["status"] == "discarded"

    # Gone from the owner's transcript; approve after discard finds nothing.
    bodies = [m["id"] for m in chat_services.list_messages(chat.id, p.id)]
    assert msg_id not in bodies
    assert "error" in svc.approve_draft(p.id, msg_id)


@pytest.mark.django_db
def test_discard_draft_rejects_non_owner_and_sent(draft):
    p, c, chat, msg_id = draft
    # The counterparty can't discard the principal's draft (owner-only via sender).
    assert (
        _client(c).post(f"/api/chats/{chat.id}/discard-draft/", {"message_id": msg_id}).status_code
        == 404
    )
    # Once approved (sent), discard is a no-op error — a sent message is never deleted.
    svc.approve_draft(p.id, msg_id)
    res = _client(p).post(f"/api/chats/{chat.id}/discard-draft/", {"message_id": msg_id})
    assert res.status_code == 404
    from chat.models import Message

    assert Message.objects.filter(id=msg_id, status="sent").exists()
