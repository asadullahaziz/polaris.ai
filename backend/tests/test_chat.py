"""
P3 — free-form human 1:1 chat: one chat per pair, listing attachments accruing over the
life of that one chat, the find-or-create entry point, inbox/read-state, member scoping,
and the engine `_relationships` chat-pair signal. LLM-free (pure ORM/REST).
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model
from django.contrib.gis.geos import Point
from django.utils import timezone
from rest_framework.test import APIClient

from catalog.models import Listing, ListingProperty, Property, Sale
from chat import services
from chat.models import Chat, ChatMember, Message, make_pair_key
from matching.engine import rank_buyers

User = get_user_model()

CHATS = "/api/chats/"


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


# --- one chat per pair ---------------------------------------------------------
@pytest.mark.django_db
def test_one_chat_per_pair_from_either_direction():
    a, b = _user("a@x.com"), _user("b@x.com")
    chat1, created1 = services.get_or_create_chat(a.id, b.id)
    assert created1 is True
    # From the opposite direction → the SAME chat, not a new one.
    chat2, created2 = services.get_or_create_chat(b.id, a.id)
    assert created2 is False
    assert chat1.id == chat2.id
    assert chat1.pair_key == make_pair_key(a.id, b.id)
    assert Chat.objects.count() == 1
    assert ChatMember.objects.filter(chat=chat1).count() == 2


@pytest.mark.django_db
def test_cannot_chat_with_self():
    a = _user("solo@x.com")
    with pytest.raises(ValueError):
        services.get_or_create_chat(a.id, a.id)


@pytest.mark.django_db
def test_multiple_listing_attachments_accrue_in_one_chat():
    seller, buyer = _user("seller@x.com"), _user("buyer@x.com")
    l1, l2 = _listing(seller, "1 A St"), _listing(seller, "2 B St")
    chat, _ = services.get_or_create_chat(seller.id, buyer.id)

    services.post_human_message(chat.id, seller.id, "here's the first", attachment_listing_ids=[l1.id])
    services.post_human_message(chat.id, seller.id, "and another", attachment_listing_ids=[l2.id])

    msgs = services.list_messages(chat.id, buyer.id)
    attached = [a["listing_id"] for m in msgs for a in m["attachments"]]
    # Both listings accrue over the life of the ONE chat (not per-listing threads).
    assert set(attached) == {l1.id, l2.id}
    assert Chat.objects.count() == 1
    # The inline card carries public facts for rendering.
    first = next(a for m in msgs for a in m["attachments"] if a["listing_id"] == l1.id)
    assert first["listing"]["title"] == "A home"


@pytest.mark.django_db
def test_post_agent_message_kind_attachment_and_dedup():
    """The copilot follow-up seam: kind='agent' + sender=principal, listing attached,
    and a repeated dedup_key is a silent no-op (resume-replay safety)."""
    seller, buyer = _user("s2@x.com"), _user("b2@x.com")
    lst = _listing(seller, "9 Follow-up Ln")
    chat, _ = services.get_or_create_chat(seller.id, buyer.id)

    saved = services.post_agent_message(
        chat.id,
        seller.id,
        "Just checking in on 9 Follow-up Ln — any questions?",
        attachment_listing_ids=[lst.id],
        dedup_key="copilot:test-thread:1",
    )
    assert saved["duplicate"] is False
    assert saved["kind"] == "agent" and saved["sender"] == seller.id
    assert [a["listing_id"] for a in saved["attachments"]] == [lst.id]

    replay = services.post_agent_message(
        chat.id, seller.id, "Just checking in again", dedup_key="copilot:test-thread:1"
    )
    assert replay == {"duplicate": True}
    assert Message.objects.filter(chat=chat).count() == 1


# --- find-or-create over REST --------------------------------------------------
@pytest.mark.django_db
def test_rest_find_or_create_is_idempotent_and_posts_opener():
    a, b = _user("contact_a@x.com"), _user("contact_b@x.com")
    lst = _listing(a)
    ca = _client(a)

    r1 = ca.post(
        CHATS, {"counterparty_id": b.id, "body": "interested in your place", "listing_id": lst.id},
        format="json",
    )
    assert r1.status_code == 201, r1.data
    chat_id = r1.data["id"]

    # A second "Contact seller" (even from b's side) reopens the SAME chat.
    r2 = _client(b).post(CHATS, {"counterparty_id": a.id, "body": "hi back"}, format="json")
    assert r2.status_code == 201
    assert r2.data["id"] == chat_id
    assert Chat.objects.count() == 1

    # The opener landed with the listing attached.
    msgs = ca.get(f"{CHATS}{chat_id}/messages/")
    assert msgs.status_code == 200
    opener = msgs.data[0]
    assert opener["body"] == "interested in your place"
    assert opener["attachments"][0]["listing_id"] == lst.id


@pytest.mark.django_db
def test_rest_reject_self_and_unknown_counterparty():
    a = _user("self@x.com")
    ca = _client(a)
    assert ca.post(CHATS, {"counterparty_id": a.id}, format="json").status_code == 400
    assert ca.post(CHATS, {"counterparty_id": 999999}, format="json").status_code == 404


# --- inbox + read state --------------------------------------------------------
@pytest.mark.django_db
def test_inbox_unread_then_read():
    a, b = _user("inbox_a@x.com"), _user("inbox_b@x.com")
    chat, _ = services.get_or_create_chat(a.id, b.id)
    # b sends a@ → unread for a.
    _client(b).post(f"{CHATS}{chat.id}/messages/", {"body": "ping"}, format="json")

    ca = _client(a)
    inbox = ca.get(CHATS).data
    assert len(inbox) == 1
    assert inbox[0]["id"] == chat.id
    assert inbox[0]["unread"] is True
    assert inbox[0]["counterparty"]["id"] == b.id
    assert inbox[0]["last_message"]["body"] == "ping"

    # a reads it → no longer unread; a's own last message wouldn't count as unread anyway.
    assert ca.post(f"{CHATS}{chat.id}/read/").status_code == 200
    assert ca.get(CHATS).data[0]["unread"] is False


@pytest.mark.django_db
def test_messages_and_chat_scoped_to_members():
    a, b, x = _user("m_a@x.com"), _user("m_b@x.com"), _user("m_x@x.com")
    chat, _ = services.get_or_create_chat(a.id, b.id)
    services.post_human_message(chat.id, a.id, "members only")

    cx = _client(x)  # a non-member
    assert cx.get(f"{CHATS}{chat.id}/messages/").status_code == 404
    assert cx.get(f"{CHATS}{chat.id}/").status_code == 404
    assert cx.get(CHATS).data == []
    # A non-member cannot post into the chat.
    assert cx.post(f"{CHATS}{chat.id}/messages/", {"body": "sneak"}, format="json").status_code == 404


# --- engine relationship signal (chat-pair basis) ------------------------------
@pytest.mark.django_db
def test_engine_relationships_uses_chat_pair():
    seller = _user("rel_seller@x.com")
    buyer = _user("rel_buyer@x.com", full_name="Warm Buyer")

    subj = Property.objects.create(
        apn="relsubj", address_norm="rel subj", address_raw="rel subj",
        geom=Point(-122.33, 47.60, srid=4326), property_type="sfr", beds=3, sqft=2000, condition=2,
    )
    listing = Listing.objects.create(seller=seller, asking_price=Decimal("450000"), status="active")
    ListingProperty.objects.create(listing=listing, property=subj, asking_price=Decimal("450000"))
    # The buyer has a nearby recent purchase (so they enter the candidate pool).
    Sale.objects.create(
        buyer=buyer, geom=Point(-122.33, 47.60, srid=4326), price=Decimal("440000"),
        purchased_at=timezone.now().date() - dt.timedelta(days=30), cash_buyer=True,
        disposition="flip", source="test",
    )

    # No chat yet → relationship signal is cold.
    cold = next(r for r in rank_buyers(listing.id)["ranked"] if r["user_id"] == buyer.id)
    assert cold["features"]["relationship"] == 0.0

    # Open a chat pair between seller and buyer → the 0.10 warm signal turns live.
    services.get_or_create_chat(seller.id, buyer.id)
    warm = next(r for r in rank_buyers(listing.id)["ranked"] if r["user_id"] == buyer.id)
    assert warm["features"]["relationship"] == 1.0
    assert warm["score"] > cold["score"]
