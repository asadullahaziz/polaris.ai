"""
Deals mini-CRM service (deals/service.py) — pure, LLM-free. Covered: ensure_deal
idempotency + the (listing, buyer) uniqueness; forward-only advance (backward no-op;
closed/lost sticky; lost only from active stages); manual override any direction;
the on_message matrix (owner attach → contacted, non-owner attach → engaged, buyer
activity contacted → engaged, seller activity doesn't advance, third-party listings
create nothing); apply_agent_action (propose → negotiating + offer recorded; accept →
agreed + agreed_price from the counterparty's standing offer; decline → lost; triage
intent alone moves to negotiating); focal_deal resolution; responder_context stance
mapping + the honest-urgency count.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model

from catalog.models import Listing, ListingProperty, Property
from chat import services as chat_services
from deals import service as svc
from deals.models import Deal

User = get_user_model()


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


# ---- ensure_deal ----------------------------------------------------------------
@pytest.mark.django_db
def test_ensure_deal_idempotent_and_unique(world):
    seller, buyer, listing, chat = world
    d1 = svc.ensure_deal(
        listing_id=listing.id, buyer_id=buyer.id, seller_id=seller.id, chat_id=chat.id
    )
    d2 = svc.ensure_deal(listing_id=listing.id, buyer_id=buyer.id, seller_id=seller.id)
    assert d1.id == d2.id
    assert Deal.objects.filter(listing=listing, buyer=buyer).count() == 1


@pytest.mark.django_db
def test_ensure_deal_backfills_chat_and_advances_but_never_regresses(world):
    seller, buyer, listing, chat = world
    d = svc.ensure_deal(listing_id=listing.id, buyer_id=buyer.id, seller_id=seller.id)
    assert d.chat_id is None
    d = svc.ensure_deal(
        listing_id=listing.id,
        buyer_id=buyer.id,
        seller_id=seller.id,
        chat_id=chat.id,
        stage="engaged",
    )
    assert d.chat_id == chat.id and d.stage == "engaged"
    # A later "contacted" ensure (e.g. a second outreach) must not regress the stage.
    d = svc.ensure_deal(
        listing_id=listing.id, buyer_id=buyer.id, seller_id=seller.id, stage="contacted"
    )
    assert d.stage == "engaged"


# ---- advance_stage / set_stage_manual --------------------------------------------
@pytest.mark.django_db
def test_advance_stage_is_forward_only_and_terminal_sticky(world):
    seller, buyer, listing, chat = world
    d = svc.ensure_deal(listing_id=listing.id, buyer_id=buyer.id, seller_id=seller.id)

    svc.advance_stage(d, "negotiating")
    assert d.stage == "negotiating"
    svc.advance_stage(d, "engaged")  # backward → no-op
    assert d.stage == "negotiating"

    svc.advance_stage(d, "agreed")
    svc.advance_stage(d, "lost")  # lost is NOT auto-reachable from agreed
    assert d.stage == "agreed"

    svc.advance_stage(d, "closed")
    svc.advance_stage(d, "negotiating")  # closed is sticky
    assert d.stage == "closed"


@pytest.mark.django_db
def test_lost_reachable_from_active_and_sticky(world):
    seller, buyer, listing, chat = world
    d = svc.ensure_deal(listing_id=listing.id, buyer_id=buyer.id, seller_id=seller.id)
    svc.advance_stage(d, "lost")
    assert d.stage == "lost"
    svc.advance_stage(d, "negotiating")  # lost is sticky on the auto path
    assert d.stage == "lost"


@pytest.mark.django_db
def test_manual_override_moves_anywhere(world):
    seller, buyer, listing, chat = world
    d = svc.ensure_deal(listing_id=listing.id, buyer_id=buyer.id, seller_id=seller.id)
    svc.advance_stage(d, "lost")
    svc.set_stage_manual(d, "negotiating")  # the human corrects the CRM
    assert d.stage == "negotiating"
    with pytest.raises(ValueError):
        svc.set_stage_manual(d, "sold_to_mars")


# ---- on_message ------------------------------------------------------------------
@pytest.mark.django_db
def test_owner_attachment_creates_contacted_deal(world):
    seller, buyer, listing, chat = world
    svc.on_message(chat.id, seller.id, listing_ids=[listing.id])
    d = Deal.objects.get(listing=listing, buyer=buyer)
    assert d.stage == "contacted" and d.seller_id == seller.id and d.chat_id == chat.id


@pytest.mark.django_db
def test_non_owner_attachment_creates_engaged_deal(world):
    seller, buyer, listing, chat = world
    # The buyer shares the seller's listing into their chat (inquiry).
    svc.on_message(chat.id, buyer.id, listing_ids=[listing.id])
    d = Deal.objects.get(listing=listing, buyer=buyer)
    assert d.stage == "engaged"


@pytest.mark.django_db
def test_third_party_listing_creates_no_deal(world):
    seller, buyer, listing, chat = world
    third = _user("third@x.com")
    other_listing = _listing(third, address="9 Elm St")
    svc.on_message(chat.id, buyer.id, listing_ids=[other_listing.id])
    assert not Deal.objects.filter(listing=other_listing).exists()


@pytest.mark.django_db
def test_buyer_activity_advances_contacted_to_engaged_but_seller_does_not(world):
    seller, buyer, listing, chat = world
    d = svc.ensure_deal(
        listing_id=listing.id, buyer_id=buyer.id, seller_id=seller.id, chat_id=chat.id
    )
    svc.on_message(chat.id, seller.id)  # seller follow-up: still just contacted
    d.refresh_from_db()
    assert d.stage == "contacted"
    svc.on_message(chat.id, buyer.id)  # any buyer message = engagement
    d.refresh_from_db()
    assert d.stage == "engaged"


# ---- apply_agent_action ----------------------------------------------------------
@pytest.mark.django_db
def test_propose_records_offer_and_moves_to_negotiating(world):
    seller, buyer, listing, chat = world
    svc.ensure_deal(listing_id=listing.id, buyer_id=buyer.id, seller_id=seller.id, chat_id=chat.id)
    svc.apply_agent_action(
        chat.id, buyer.id, "propose", {"offer_price": 380000}, focal_listing_id=listing.id
    )
    d = Deal.objects.get(listing=listing, buyer=buyer)
    assert d.stage == "negotiating" and int(d.last_offer_by_buyer) == 380000


@pytest.mark.django_db
def test_intent_alone_moves_to_negotiating(world):
    seller, buyer, listing, chat = world
    svc.ensure_deal(listing_id=listing.id, buyer_id=buyer.id, seller_id=seller.id, chat_id=chat.id)
    svc.apply_agent_action(
        chat.id, seller.id, "inform", {}, intent="offer_negotiation", focal_listing_id=listing.id
    )
    assert Deal.objects.get(listing=listing, buyer=buyer).stage == "negotiating"


@pytest.mark.django_db
def test_accept_sets_agreed_and_agreed_price_from_counterparty_offer(world):
    seller, buyer, listing, chat = world
    svc.ensure_deal(listing_id=listing.id, buyer_id=buyer.id, seller_id=seller.id, chat_id=chat.id)
    # The buyer's agent disclosed an offer; the seller side accepts it.
    svc.apply_agent_action(
        chat.id, buyer.id, "propose", {"offer_price": 395000}, focal_listing_id=listing.id
    )
    svc.apply_agent_action(chat.id, seller.id, "accept", {}, focal_listing_id=listing.id)
    d = Deal.objects.get(listing=listing, buyer=buyer)
    assert d.stage == "agreed" and int(d.agreed_price) == 395000


@pytest.mark.django_db
def test_decline_moves_to_lost(world):
    seller, buyer, listing, chat = world
    svc.ensure_deal(listing_id=listing.id, buyer_id=buyer.id, seller_id=seller.id, chat_id=chat.id)
    svc.apply_agent_action(chat.id, buyer.id, "decline", {}, focal_listing_id=listing.id)
    assert Deal.objects.get(listing=listing, buyer=buyer).stage == "lost"


@pytest.mark.django_db
def test_apply_agent_action_without_deal_is_a_noop(world):
    seller, buyer, listing, chat = world
    svc.apply_agent_action(chat.id, buyer.id, "propose", {"offer_price": 1}, focal_listing_id=None)
    assert Deal.objects.count() == 0


# ---- focal_deal / responder_context ----------------------------------------------
@pytest.mark.django_db
def test_focal_deal_prefers_focal_listing_then_single_deal(world):
    seller, buyer, listing, chat = world
    d = svc.ensure_deal(
        listing_id=listing.id, buyer_id=buyer.id, seller_id=seller.id, chat_id=chat.id
    )
    assert svc.focal_deal(chat.id, listing.id).id == d.id
    assert svc.focal_deal(chat.id, None).id == d.id  # single-deal chat fallback


@pytest.mark.django_db
def test_responder_context_maps_offers_to_stance_and_counts_rivals(world):
    seller, buyer, listing, chat = world
    d = svc.ensure_deal(
        listing_id=listing.id, buyer_id=buyer.id, seller_id=seller.id, chat_id=chat.id
    )
    svc.record_disclosed_offer(d, by_user_id=buyer.id, price=380000)
    svc.record_disclosed_offer(d, by_user_id=seller.id, price=410000)
    # A rival buyer's active deal on the same listing → honest urgency for the seller.
    rival = _user("rival@x.com")
    svc.ensure_deal(listing_id=listing.id, buyer_id=rival.id, seller_id=seller.id)

    ctx_seller = svc.responder_context(chat.id, listing.id, seller.id)
    assert ctx_seller["negotiation"] == {"my_last_offer": 410000, "their_last_offer": 380000}
    assert ctx_seller["other_active_deals"] == 1
    assert ctx_seller["deal"]["stage"] == "contacted"

    ctx_buyer = svc.responder_context(chat.id, listing.id, buyer.id)
    assert ctx_buyer["negotiation"] == {"my_last_offer": 380000, "their_last_offer": 410000}
