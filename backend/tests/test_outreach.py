"""
P2 verification (implementation_plan Phase 2 "Verification") — all LLM-free.

The deterministic ranking engine, the delivery-ledger guarantee, the fan-out
idempotency, and the launch→approve→send slice are exercised directly against the
`matching.engine.rank_buyers` + `outreach.service` core. The live Inngest fan-out and
the copilot narration/streaming are the browser/compose gate (like the P0 spike).
"""

from __future__ import annotations

import datetime as dt
import uuid
from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model
from django.contrib.gis.geos import Point
from django.utils import timezone

from buyers.models import BuyBox, BuyBoxGeo, Prospect, Purchase
from catalog.models import Listing, ListingProperty, Property
from conversations.models import Conversation, Message
from matching.engine import rank_buyers
from outreach import service
from outreach.models import OutreachCampaign, OutreachRecipient

# Downtown Seattle; buyers cluster within ~1mi, "far" cluster is out of range.
LON, LAT = -122.335, 47.608
FAR = (-120.0, 46.0)
TODAY = timezone.now().date()


def _property(lon=LON, lat=LAT, *, beds=3, sqft=2000, condition=3, price="500000") -> Property:
    return Property.objects.create(
        county_fips="53033",
        address_norm=f"t:{uuid.uuid4()}",
        address_raw="123 Test Ave, Seattle, WA 98103",
        geom=Point(lon, lat, srid=4326),
        property_type="sfr",
        beds=beds,
        sqft=sqft,
        grade=7,
        condition=condition,
        last_sale_price=Decimal(price),
        last_sale_date=TODAY - dt.timedelta(days=30),
    )


def _listing(seller, *, asking="400000", **prop_kw) -> Listing:
    prop = _property(**prop_kw)
    lst = Listing.objects.create(seller=seller, asking_price=Decimal(asking), status="active")
    ListingProperty.objects.create(listing=lst, property=prop, asking_price=Decimal(asking))
    return lst


def _seller(username="seller"):
    return get_user_model().objects.create_user(username=username, password="x")


def _purchase(*, user=None, prospect=None, lon=LON, lat=LAT, price="400000", months=3, cash=True):
    return Purchase.objects.create(
        buyer_user=user,
        buyer_prospect=prospect,
        geom=Point(lon, lat, srid=4326),
        price=Decimal(price),
        purchased_at=TODAY - dt.timedelta(days=int(months * 30)),
        cash_buyer=cash,
        disposition="flip",
        source="test",
    )


def _registered_buyer(username, *, cover=True, n_purchases=0):
    u = get_user_model().objects.create_user(
        username=username, password="x", full_name=username.title()
    )
    box = BuyBox.objects.create(
        buyer=u,
        name=f"{username} box",
        is_primary=True,
        is_active=True,
        source="manual",
        strategy="fix_flip",
        price_min=Decimal("300000"),
        price_max=Decimal("500000"),
        beds_min=2,
        sqft_min=800,
        property_types=["sfr"],
    )
    center = Point(LON, LAT, srid=4326) if cover else Point(*FAR, srid=4326)
    BuyBoxGeo.objects.create(
        buy_box=box, geo_type="radius", mode="include", center=center, radius_mi=Decimal("5.0")
    )
    for _ in range(n_purchases):
        _purchase(user=u)
    return u


def _prospect(name, *, n_purchases=1, lon=LON, lat=LAT):
    p = Prospect.objects.create(full_name=name, source="test", cash_buyer=True)
    for _ in range(n_purchases):
        _purchase(prospect=p, lon=lon, lat=lat)
    return p


# ---------------------------------------------------------------------------
# P2.1 — ranking: deterministic, behavioral-first, degrades for prospects
# ---------------------------------------------------------------------------
@pytest.mark.django_db
def test_rank_buyers_orders_by_behavior_and_is_deterministic():
    seller = _seller()
    listing = _listing(seller)

    strong = _registered_buyer("strong", cover=True, n_purchases=5)  # nearby + covering box
    _prospect("Mid Prospect", n_purchases=3)  # ranks on pure behavior
    _prospect("Weak Prospect", n_purchases=1)
    _registered_buyer("faraway", cover=False, n_purchases=0)  # out of pool

    res = rank_buyers(listing.id, limit=10)
    names = [r["name"] for r in res["ranked"]]

    assert "Faraway" not in names  # neither nearby history nor covering buy-box
    assert names[0] == strong.full_name  # most nearby buys + cash + completeness
    assert names.index("Mid Prospect") < names.index("Weak Prospect")

    # The behavioral signal is real, not incidental ordering.
    top = res["ranked"][0]
    assert top["n_nearby"] == 5 and top["features"]["bought_in_area"] > 0

    # Prospects rank on pure behavior — no buy-box bonus.
    mid_row = next(r for r in res["ranked"] if r["name"] == "Mid Prospect")
    assert mid_row["registered"] is False
    assert mid_row["buy_box_completeness"] == 0.0
    assert mid_row["reason"]  # a human "why this buyer" line
    assert set(res["weights"]) and "bought_in_area" in res["ranked"][0]["features"]

    # Determinism: same inputs → identical order + scores.
    again = rank_buyers(listing.id, limit=10)
    assert [(r["name"], r["score"]) for r in again["ranked"]] == [
        (r["name"], r["score"]) for r in res["ranked"]
    ]


@pytest.mark.django_db
def test_rank_buyers_empty_pool_is_safe():
    seller = _seller()
    listing = _listing(seller)
    res = rank_buyers(listing.id)
    assert res["ranked"] == [] and res["n_candidates"] == 0


# ---------------------------------------------------------------------------
# P2.2 — launch persists a draft batch awaiting approval
# ---------------------------------------------------------------------------
@pytest.mark.django_db
def test_launch_outreach_persists_awaiting_approval():
    seller = _seller()
    listing = _listing(seller)
    _registered_buyer("b1", n_purchases=4)
    _prospect("P1", n_purchases=2)

    res = service.launch_outreach(seller.id, listing.id)
    campaign = OutreachCampaign.objects.get(id=res["campaign_id"])
    assert campaign.status == "awaiting_approval"
    assert res["pending_count"] >= 2
    recs = OutreachRecipient.objects.filter(campaign=campaign)
    assert recs.count() == res["pending_count"] + res["skipped_count"]
    assert all(r.draft_body and r.rank_reason for r in recs)  # openers drafted, reasons stored


@pytest.mark.django_db
def test_launch_outreach_rejects_foreign_listing():
    owner, intruder = _seller("owner"), _seller("intruder")
    listing = _listing(owner)
    assert "error" in service.launch_outreach(intruder.id, listing.id)


# ---------------------------------------------------------------------------
# P2.6 — the delivery-ledger guarantee
# ---------------------------------------------------------------------------
def _recipient(campaign, listing, *, user=None, prospect=None, status="pending"):
    return OutreachRecipient.objects.create(
        campaign=campaign,
        listing=listing,
        recipient_user=user,
        recipient_prospect=prospect,
        draft_body="hi",
        rank_reason="bought nearby",
        status=status,
    )


@pytest.mark.django_db
def test_ledger_never_double_sends_across_campaigns():
    seller = _seller()
    listing = _listing(seller)
    buyer = _registered_buyer("b1", n_purchases=1)

    c1 = OutreachCampaign.objects.create(listing=listing, seller=seller, status="sending")
    c2 = OutreachCampaign.objects.create(listing=listing, seller=seller, status="sending")
    r1 = _recipient(c1, listing, user=buyer)
    r2 = _recipient(c2, listing, user=buyer)

    assert service.send_recipient(r1.id)["status"] == "sent"
    assert service.send_recipient(r2.id)["status"] == "skipped"  # already contacted

    sent = OutreachRecipient.objects.filter(listing=listing, recipient_user=buyer, status="sent")
    assert sent.count() == 1  # the ledger guarantee


@pytest.mark.django_db
def test_cancelled_proposal_does_not_block_a_later_send():
    seller = _seller()
    listing = _listing(seller)
    buyer = _registered_buyer("b1", n_purchases=1)

    c1 = OutreachCampaign.objects.create(listing=listing, seller=seller, status="cancelled")
    _recipient(c1, listing, user=buyer, status="cancelled")  # a dead proposal

    c2 = OutreachCampaign.objects.create(listing=listing, seller=seller, status="sending")
    r2 = _recipient(c2, listing, user=buyer)
    assert service.send_recipient(r2.id)["status"] == "sent"  # cancelled didn't block


# ---------------------------------------------------------------------------
# P2.4 — fan-out idempotency (replay sends each opener once)
# ---------------------------------------------------------------------------
@pytest.mark.django_db
def test_send_recipient_is_idempotent_on_replay():
    seller = _seller()
    listing = _listing(seller)
    buyer = _registered_buyer("b1", n_purchases=1)
    c = OutreachCampaign.objects.create(listing=listing, seller=seller, status="sending")
    r = _recipient(c, listing, user=buyer)

    first = service.send_recipient(r.id)
    second = service.send_recipient(r.id)  # Inngest at-least-once replay

    assert first["status"] == "sent"
    assert second["status"] == "already_sent"
    threads = Conversation.objects.filter(kind="thread", listing=listing, counterparty_user=buyer)
    assert threads.count() == 1  # one thread (uniqueness)
    openers = Message.objects.filter(
        conversation=threads.first(), dedup_key=f"outreach:{listing.id}:u{buyer.id}"
    )
    assert openers.count() == 1  # one opener (dedup_key ON CONFLICT DO NOTHING)


@pytest.mark.django_db
def test_prospect_send_opens_one_way_thread_without_notification():
    seller = _seller()
    listing = _listing(seller)
    prospect = _prospect("P1", n_purchases=1)
    c = OutreachCampaign.objects.create(listing=listing, seller=seller, status="sending")
    r = _recipient(c, listing, prospect=prospect)

    assert service.send_recipient(r.id)["status"] == "sent"
    thread = Conversation.objects.get(
        kind="thread", listing=listing, counterparty_prospect=prospect
    )
    assert Message.objects.filter(conversation=thread).count() == 1


# ---------------------------------------------------------------------------
# P2 E2E (service-level): launch → skip-already-contacted → approve → fan-out
# ---------------------------------------------------------------------------
@pytest.mark.django_db
def test_launch_skips_already_contacted_buyer():
    seller = _seller()
    listing = _listing(seller)
    buyer = _registered_buyer("b1", n_purchases=3)

    # A prior sent ledger row for this buyer+listing.
    prior = OutreachCampaign.objects.create(listing=listing, seller=seller, status="done")
    _recipient(prior, listing, user=buyer, status="sent")

    res = service.launch_outreach(seller.id, listing.id)
    rec = OutreachRecipient.objects.get(campaign_id=res["campaign_id"], recipient_user=buyer)
    assert rec.status == "skipped_already_contacted"
    assert res["skipped_count"] >= 1


@pytest.mark.django_db
def test_full_launch_approve_fanout_slice():
    seller = _seller()
    listing = _listing(seller)
    _registered_buyer("b1", n_purchases=4)
    _registered_buyer("b2", n_purchases=2)
    _prospect("P1", n_purchases=2)

    launched = service.launch_outreach(seller.id, listing.id)
    campaign_id = launched["campaign_id"]
    assert launched["pending_count"] >= 3

    approved = service.approve_campaign(seller.id, campaign_id)
    assert approved["status"] == "sending"

    info = service.campaign_dispatch_info(campaign_id)
    for rid in info["recipient_ids"]:
        service.send_recipient(rid)

    outcome = service.finish_campaign(campaign_id)
    assert outcome["sent"] == launched["pending_count"]
    assert OutreachCampaign.objects.get(id=campaign_id).status == "done"
    # One shared thread opened per sent recipient.
    assert Conversation.objects.filter(kind="thread", listing=listing).count() == outcome["sent"]


@pytest.mark.django_db
def test_cancel_campaign_marks_pending_cancelled():
    seller = _seller()
    listing = _listing(seller)
    _registered_buyer("b1", n_purchases=2)
    res = service.launch_outreach(seller.id, listing.id)
    out = service.cancel_campaign(seller.id, res["campaign_id"])
    assert out["status"] == "cancelled"
    assert not OutreachRecipient.objects.filter(
        campaign_id=res["campaign_id"], status="pending"
    ).exists()
