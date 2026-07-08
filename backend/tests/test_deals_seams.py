"""
Deal wiring at the four seams (LLM-free): outreach send → contacted deals (replay-
safe); the human/agent message paths create/advance deals; `commit_reply` applies deal
side-effects exactly once under the dedup guard; `approve_draft` applies the SAME
side-effects (accept → agreed; decline → lost + terminal no_fit) so the human-approved
path keeps the CRM true; persist_draft's approval_context lands in the notification.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model

from ai import outreach_service as outreach
from ai.models import OutreachCampaign
from catalog.models import Listing, ListingProperty, Property
from chat import responder_service as svc
from chat import services as chat_services
from deals import service as deal_svc
from deals.models import Deal
from notifications.models import Notification

User = get_user_model()

ABSENT = lambda chat_id, user_id: False  # noqa: E731 - test presence stub


def _user(email, **kw):
    return User.objects.create_user(email=email, password="pw-12345678", **kw)


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
    return seller, buyer, listing, chat


# ---- outreach seam ---------------------------------------------------------------
@pytest.mark.django_db
def test_outreach_send_creates_contacted_deal_and_replays_safely(db):
    seller = _user("s2@x.com")
    buyer = _user("b2@x.com")
    listing = _listing(seller, address="7 Oak Ave")
    outreach.launch_outreach(
        seller.id, [{"user_id": buyer.id, "listing_ids": [listing.id], "body": "hi"}]
    )
    campaign_id = OutreachCampaign.objects.get(seller=seller).id

    r1 = outreach.send_to_buyer(campaign_id, buyer.id)
    assert r1["status"] == "sent"
    d = Deal.objects.get(listing=listing, buyer=buyer)
    assert d.stage == "contacted" and d.chat_id == r1["chat_id"]

    r2 = outreach.send_to_buyer(campaign_id, buyer.id)  # Inngest replay
    assert Deal.objects.filter(listing=listing, buyer=buyer).count() == 1
    assert r2["status"] in ("sent", "already_sent", "skipped")


# ---- human/agent message seams -----------------------------------------------------
@pytest.mark.django_db
def test_human_attachment_message_creates_deal(world):
    seller, buyer, listing, chat = world
    chat_services.post_human_message(
        chat.id, buyer.id, "is this still available?", attachment_listing_ids=[listing.id]
    )
    assert Deal.objects.get(listing=listing, buyer=buyer).stage == "engaged"


@pytest.mark.django_db
def test_agent_message_seam_creates_deal(world):
    seller, buyer, listing, chat = world
    chat_services.post_agent_message(
        chat.id,
        seller.id,
        "thought of you for this one",
        attachment_listing_ids=[listing.id],
        dedup_key="copilot:test:1",
    )
    assert Deal.objects.get(listing=listing, buyer=buyer).stage == "contacted"


# ---- commit_reply seam --------------------------------------------------------------
@pytest.mark.django_db
def test_commit_reply_applies_deal_effects_once_under_dedup(world):
    seller, buyer, listing, chat = world
    deal_svc.ensure_deal(
        listing_id=listing.id, buyer_id=buyer.id, seller_id=seller.id, chat_id=chat.id
    )
    inbound = chat_services.post_human_message(chat.id, seller.id, "any interest at 400?")

    kwargs = dict(
        principal_id=buyer.id,
        action="propose",
        body="I can do 380 if the roof checks out. Workable?",
        disclosed_fields={"offer_price": 380000},
        inbound_message_id=inbound["id"],
        counterparty_user_id=seller.id,
        intent="offer_negotiation",
        focal_listing_id=listing.id,
        presence_fn=ABSENT,
    )
    r1 = svc.commit_reply(chat.id, **kwargs)
    assert r1["status"] == "sent"
    d = Deal.objects.get(listing=listing, buyer=buyer)
    assert d.stage == "negotiating" and int(d.last_offer_by_buyer) == 380000

    # A replayed step recomputes the same dedup_key → duplicate → effects do NOT re-fire.
    d.last_offer_by_buyer = None
    d.save(update_fields=["last_offer_by_buyer"])
    r2 = svc.commit_reply(chat.id, **kwargs)
    assert r2["status"] == "duplicate"
    d.refresh_from_db()
    assert d.last_offer_by_buyer is None


# ---- approve_draft seam --------------------------------------------------------------
@pytest.mark.django_db
def test_approved_accept_draft_moves_deal_to_agreed(world):
    seller, buyer, listing, chat = world
    d = deal_svc.ensure_deal(
        listing_id=listing.id, buyer_id=buyer.id, seller_id=seller.id, chat_id=chat.id
    )
    deal_svc.record_disclosed_offer(d, by_user_id=buyer.id, price=390000)
    inbound = chat_services.post_human_message(chat.id, buyer.id, "390k, final.")

    draft = svc.persist_draft(
        chat.id,
        principal_id=seller.id,
        action="accept",
        body="390 works. Let's write it up.",
        disclosed_fields={},
        inbound_message_id=inbound["id"],
        approval_context={"recommendation": "Offer $390,000 clears your floor."},
    )
    assert draft["status"] == "draft"
    note = Notification.objects.get(user=seller, type="approval_required")
    assert note.payload["recommendation"] == "Offer $390,000 clears your floor."
    d.refresh_from_db()
    # The buyer's own inbound advanced contacted → engaged; the DRAFT itself must not
    # move the deal further — only the approved send does.
    assert d.stage == "engaged"

    res = svc.approve_draft(seller.id, draft["message_id"])
    assert res["status"] == "sent"
    d.refresh_from_db()
    assert d.stage == "agreed" and int(d.agreed_price) == 390000


@pytest.mark.django_db
def test_approved_decline_draft_sets_lost_and_terminal(world):
    seller, buyer, listing, chat = world
    deal_svc.ensure_deal(
        listing_id=listing.id, buyer_id=buyer.id, seller_id=seller.id, chat_id=chat.id
    )
    inbound = chat_services.post_human_message(chat.id, seller.id, "still interested?")
    draft = svc.persist_draft(
        chat.id,
        principal_id=buyer.id,
        action="decline",
        body="Passing on this one, numbers don't work for us.",
        disclosed_fields={},
        inbound_message_id=inbound["id"],
    )
    svc.approve_draft(buyer.id, draft["message_id"])
    chat.refresh_from_db()
    assert chat.terminal == "no_fit"
    assert Deal.objects.get(listing=listing, buyer=buyer).stage == "lost"
