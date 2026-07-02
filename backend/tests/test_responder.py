"""
P3 verification (implementation_plan Phase 3 "Verification") — LLM-free, like P2.

The load-bearing guarantees are all deterministic and tested here without a model:
  * the "exactly one autonomous reply" INVARIANT lives in the pure-sync commit gate
    (conversations.responder_service): dedup, the reply cap, presence, escalation;
  * the DISCLOSURE gates (policy gate + literal-leak output check) are pure functions;
  * assess_deal is deterministic wholesale math (drives the qualify/hold/decline
    divergence the demo shows).
The two-stage airlock's LLM turn (Graph 2) is the browser/compose gate, exactly as the
Inngest fan-out + copilot streaming were for P0/P2.
"""

from __future__ import annotations

import datetime as dt
import uuid
from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model
from django.contrib.gis.geos import Point
from django.utils import timezone

from catalog.models import Listing, ListingProperty, Property
from conversations import responder_service as svc
from conversations.models import Conversation, Message
from matching.engine import assess_deal
from polaris_agent import disclosure

LON, LAT = -122.335, 47.608
TODAY = timezone.now().date()


# ---------------------------------------------------------------------------
# fixtures / helpers
# ---------------------------------------------------------------------------
def _seller(username="seller"):
    return get_user_model().objects.create_user(username=username, password="x", full_name="Seller")


def _buyer(username="buyer"):
    return get_user_model().objects.create_user(username=username, password="x", full_name="Buyer")


def _property(*, beds=3, sqft=2000, condition=3, price="600000"):
    return Property.objects.create(
        county_fips="53033",
        address_norm=f"t:{uuid.uuid4()}",
        address_raw="123 Test Ave, Seattle, WA 98103",
        geom=Point(LON, LAT, srid=4326),
        property_type="sfr",
        beds=beds,
        sqft=sqft,
        grade=7,
        condition=condition,
        last_sale_price=Decimal(price),
        last_sale_date=TODAY - dt.timedelta(days=30),
    )


def _listing(seller, *, asking="400000", condition=3):
    prop = _property(condition=condition)
    lst = Listing.objects.create(seller=seller, asking_price=Decimal(asking), status="active")
    ListingProperty.objects.create(listing=lst, property=prop, asking_price=Decimal(asking))
    return lst, prop


def _comps(n=8, *, price=600000, condition=5, beds=3, sqft=2000):
    """Good-condition comps near the subject so estimate_value(arv=True) meets min_n."""
    for i in range(n):
        Property.objects.create(
            county_fips="53033",
            address_norm=f"c:{uuid.uuid4()}",
            address_raw=f"comp {i}",
            geom=Point(LON + 0.001 * i, LAT, srid=4326),
            property_type="sfr",
            beds=beds,
            sqft=sqft,
            grade=7,
            condition=condition,
            last_sale_price=Decimal(price + i * 500),
            last_sale_date=TODAY - dt.timedelta(days=20 + i),
        )


def _thread(seller, buyer, listing):
    return Conversation.objects.create(
        kind="thread", listing=listing, counterparty_user=buyer, status="open"
    )


def _opener(conv, seller, body="Off-market deal — interested?"):
    return Message.objects.create(
        conversation=conv,
        author_type="agent",
        author_side="seller",
        author_id=seller.id,
        action="inform",
        body=body,
        status="sent",
        sent_at=timezone.now(),
        dedup_key=f"outreach:{conv.listing_id}:u{conv.counterparty_user_id}",
    )


def _human(conv, user, side, body="hi"):
    return Message.objects.create(
        conversation=conv,
        author_type="human",
        author_side=side,
        author_id=user.id,
        body=body,
        status="sent",
        sent_at=timezone.now(),
    )


def _reply_kwargs(
    conv, buyer, inbound, *, action="qualify", body="Interested — can you share the condition?"
):
    return dict(
        side="buyer",
        principal_id=buyer.id,
        action=action,
        body=body,
        disclosed_fields={"interest_level": "high"},
        inbound_message_id=inbound.id,
        counterparty_user_id=conv.listing.seller_id,
        reply_to_id=inbound.id,
    )


ABSENT = lambda *_: False  # noqa: E731 - human away
PRESENT = lambda *_: True  # noqa: E731 - human here (takeover)


# ===========================================================================
# assess_deal (P3.4) — deterministic wholesale math
# ===========================================================================
@pytest.mark.django_db
def test_assess_deal_qualifies_a_wide_spread():
    seller = _seller()
    lst, _ = _listing(seller, asking="400000", condition=3)
    _comps(price=600000)  # ARV ~ $300/sqft × 2000 = ~$600k
    res = assess_deal(lst.id, strategy="fix_flip")
    assert res["verdict"] == "qualify"
    assert res["spread"] and res["spread"] > 0
    assert res["margin_pct"] >= res["threshold"]


@pytest.mark.django_db
def test_assess_deal_declines_a_thin_spread():
    seller = _seller()
    lst, _ = _listing(seller, asking="560000", condition=3)  # asking near ARV → no room
    _comps(price=600000)
    res = assess_deal(lst.id, strategy="fix_flip")
    assert res["verdict"] == "decline"


@pytest.mark.django_db
def test_assess_deal_holds_when_comps_are_thin():
    seller = _seller()
    lst, _ = _listing(seller, asking="400000")  # no comps loaded → can't price → hold
    res = assess_deal(lst.id, strategy="fix_flip")
    assert res["verdict"] == "hold"
    assert res["spread"] is None


@pytest.mark.django_db
def test_assess_deal_diverges_by_strategy_on_one_listing():
    """The demo's divergence mechanism: the SAME listing clears a patient strategy's bar
    but only holds for an aggressive one — because the threshold differs (deterministic)."""
    seller = _seller()
    lst, _ = _listing(seller, asking="440000", condition=3)  # margin ~0.167
    _comps(price=600000)
    flip = assess_deal(lst.id, strategy="fix_flip")  # threshold 0.20
    hold = assess_deal(lst.id, strategy="buy_hold")  # threshold 0.10
    assert flip["margin_pct"] == hold["margin_pct"]  # same deal math…
    assert flip["verdict"] == "hold" and hold["verdict"] == "qualify"  # …different verdict
    # Determinism.
    assert assess_deal(lst.id, strategy="fix_flip") == flip


# ===========================================================================
# Disclosure gates (P3.2/§12) — pure, no model
# ===========================================================================
def test_policy_gate_rejects_out_of_mandate_offer():
    mandate = {"ceiling_price": 500000, "floor_price": None}
    over = {"action": "qualify", "disclosed_fields": {"offer_price": 520000}}
    ok, reason = disclosure.policy_gate(over, mandate, "buyer_agent")
    assert not ok and "ceiling" in reason
    under = {"action": "qualify", "disclosed_fields": {"offer_price": 480000}}
    assert disclosure.policy_gate(under, mandate, "buyer_agent")[0] is True


def test_policy_gate_rejects_seller_below_floor_and_bad_fields():
    m = {"floor_price": 300000}
    low = {"action": "propose", "disclosed_fields": {"offer_price": 250000}}
    # action itself is not in the v1 set → rejected before the price check
    assert disclosure.policy_gate(low, m, "seller_agent")[0] is False
    below = {"action": "inform", "disclosed_fields": {"offer_price": 250000}}
    assert disclosure.policy_gate(below, m, "seller_agent")[0] is False
    leaky = {"action": "inform", "disclosed_fields": {"ceiling_price": 999}}
    assert disclosure.policy_gate(leaky, m, "seller_agent")[0] is False  # not whitelisted


def test_output_check_blocks_literal_limit_leaks_every_format():
    m = {"ceiling_price": 500000}
    decision = {"disclosed_fields": {}}
    for leak in ["I can go to 500000", "up to $500,000", "around 500k", "max is $500k"]:
        ok, _ = disclosure.output_check(leak, m, decision, "buyer_agent")
        assert not ok, leak
    ok, _ = disclosure.output_check(
        "Happy to move quickly if the numbers work.", m, decision, "buyer_agent"
    )
    assert ok
    assert disclosure.output_check("", m, decision, "buyer_agent")[0] is False  # empty


def test_output_check_allows_a_deliberately_disclosed_offer_equal_to_limit():
    """An offer the agent CHOSE to make can equal a limit — that's a disclosure, not a leak."""
    m = {"ceiling_price": 500000}
    decision = {"disclosed_fields": {"offer_price": 500000}}
    ok, _ = disclosure.output_check("I can offer $500,000.", m, decision, "buyer_agent")
    assert ok


# ===========================================================================
# The commit gate (P3.3) — the "exactly one reply" INVARIANT
# ===========================================================================
@pytest.mark.django_db
def test_commit_gate_sends_one_and_only_one():
    seller, buyer = _seller(), _buyer()
    lst, _ = _listing(seller)
    conv = _thread(seller, buyer, lst)
    inbound = _opener(conv, seller)

    r1 = svc.commit_reply(conv.id, presence_fn=ABSENT, **_reply_kwargs(conv, buyer, inbound))
    r2 = svc.commit_reply(conv.id, presence_fn=ABSENT, **_reply_kwargs(conv, buyer, inbound))

    assert r1["status"] == "sent"
    # A replay of the same inbound never yields a 2nd message — caught by cap or dedup_key.
    assert r2["status"] in ("stood_down_cap", "duplicate")
    assert (
        Message.objects.filter(
            conversation=conv, author_type="agent", author_side="buyer", status="sent"
        ).count()
        == 1
    )


@pytest.mark.django_db
def test_human_takeover_mid_turn_stands_down():
    """Presence re-checked inside the commit txn: a human who returned wins the race."""
    seller, buyer = _seller(), _buyer()
    lst, _ = _listing(seller)
    conv = _thread(seller, buyer, lst)
    inbound = _opener(conv, seller)

    res = svc.commit_reply(conv.id, presence_fn=PRESENT, **_reply_kwargs(conv, buyer, inbound))
    assert res["status"] == "stood_down_present"
    # The buyer-side agent posted nothing (the seller-side opener is unrelated).
    assert not Message.objects.filter(
        conversation=conv, author_type="agent", author_side="buyer"
    ).exists()


@pytest.mark.django_db
def test_reply_cap_counts_and_resets_only_on_same_side_human():
    seller, buyer = _seller(), _buyer()
    lst, _ = _listing(seller)
    conv = _thread(seller, buyer, lst)
    inbound = _opener(conv, seller)

    assert svc.reply_cap_reached(conv.id, "buyer") is False  # nothing yet
    svc.commit_reply(conv.id, presence_fn=ABSENT, **_reply_kwargs(conv, buyer, inbound))
    assert svc.reply_cap_reached(conv.id, "buyer") is True  # one agent reply, no human since

    # A COUNTERPARTY (seller) message does NOT reset the buyer-side cap.
    _human(conv, seller, "seller", "you there?")
    assert svc.reply_cap_reached(conv.id, "buyer") is True

    # The SAME-SIDE human (the buyer) taking over resets it (takeover needs no special code).
    _human(conv, buyer, "buyer", "I'll take it from here")
    assert svc.reply_cap_reached(conv.id, "buyer") is False


@pytest.mark.django_db
def test_second_inbound_while_absent_escalates_not_replies():
    """After one auto-reply, the reply cap is reached → the handler escalates instead of a
    2nd reply. We assert the two service primitives the handler composes."""
    seller, buyer = _seller(), _buyer()
    lst, _ = _listing(seller)
    conv = _thread(seller, buyer, lst)
    inbound = _opener(conv, seller)
    svc.commit_reply(conv.id, presence_fn=ABSENT, **_reply_kwargs(conv, buyer, inbound))

    assert svc.reply_cap_reached(conv.id, "buyer") is True
    out = svc.escalate(conv.id, buyer.id, "counterparty pressed again")
    assert out["status"] == "escalated"
    conv.refresh_from_db()
    assert conv.status == "escalated"
    # Escalation posts NOTHING new — still just the one buyer-side agent reply.
    assert (
        Message.objects.filter(
            conversation=conv, author_type="agent", author_side="buyer", status="sent"
        ).count()
        == 1
    )
    from notifications.models import Notification

    assert Notification.objects.filter(user=buyer, type="escalation").exists()


@pytest.mark.django_db
def test_decline_sets_terminal_no_fit():
    seller, buyer = _seller(), _buyer()
    lst, _ = _listing(seller)
    conv = _thread(seller, buyer, lst)
    inbound = _opener(conv, seller)
    svc.commit_reply(
        conv.id,
        presence_fn=ABSENT,
        terminal="no_fit",
        **_reply_kwargs(conv, buyer, inbound, action="decline", body="Not a fit for us — thanks."),
    )
    conv.refresh_from_db()
    assert conv.terminal == "no_fit"


# ===========================================================================
# Send gate: draft (assist/confirm) + approval (the takeover)
# ===========================================================================
@pytest.mark.django_db
def test_persist_draft_then_approve_sends_once():
    seller, buyer = _seller(), _buyer()
    lst, _ = _listing(seller)
    conv = _thread(seller, buyer, lst)
    inbound = _opener(conv, seller)

    d = svc.persist_draft(
        conv.id,
        side="buyer",
        principal_id=buyer.id,
        action="ask",
        body="What's the condition and any repairs?",
        disclosed_fields={},
        inbound_message_id=inbound.id,
        reply_to_id=inbound.id,
    )
    assert d["status"] == "draft"
    msg = Message.objects.get(id=d["message_id"])
    assert msg.status == "draft"  # NOT sent — awaits approval
    from notifications.models import Notification

    assert Notification.objects.filter(user=buyer, type="approval_required").exists()

    ap = svc.approve_draft(buyer.id, msg.id)
    assert ap["status"] == "sent"
    msg.refresh_from_db()
    assert msg.status == "sent" and msg.sent_at is not None
    # Idempotent.
    assert svc.approve_draft(buyer.id, msg.id)["status"] == "sent"


# ===========================================================================
# responder_plan (P3.2 routing) — who answers, gated on prospect/auto_reply
# ===========================================================================
@pytest.mark.django_db
def test_responder_plan_targets_buyer_for_a_seller_opener():
    from agent_context.models import Mandate
    from buyers.models import BuyBox, BuyBoxGeo
    from polaris_agent import dal

    seller, buyer = _seller(), _buyer()
    lst, _ = _listing(seller)
    box = BuyBox.objects.create(
        buyer=buyer,
        name="box",
        is_primary=True,
        is_active=True,
        source="manual",
        strategy="fix_flip",
        price_min=Decimal("300000"),
        price_max=Decimal("550000"),
        beds_min=2,
        sqft_min=800,
        property_types=["sfr"],
    )
    BuyBoxGeo.objects.create(
        buy_box=box,
        geo_type="radius",
        mode="include",
        center=Point(LON, LAT, srid=4326),
        radius_mi=Decimal("5.0"),
    )
    Mandate.objects.create(
        buy_box=box, ceiling_price=Decimal("550000"), autonomy="auto_with_policy", auto_reply=True
    )
    conv = _thread(seller, buyer, lst)
    inbound = _opener(conv, seller)

    plan = dal._responder_plan(conv.id, inbound.id)
    assert "skip" not in plan
    assert plan["role"] == "buyer_agent" and plan["side"] == "buyer"
    assert plan["principal_id"] == buyer.id
    assert plan["counterparty_user_id"] == seller.id
    assert plan["mandate"]["ceiling_price"] == 550000
    assert plan["strategy"] == "fix_flip"

    # auto_reply off → skip.
    Mandate.objects.filter(buy_box=box).update(auto_reply=False)
    assert "skip" in dal._responder_plan(conv.id, inbound.id)


@pytest.mark.django_db
def test_responder_plan_skips_prospect_counterparty():
    from buyers.models import Prospect
    from polaris_agent import dal

    seller = _seller()
    lst, _ = _listing(seller)
    prospect = Prospect.objects.create(full_name="P", source="test")
    conv = Conversation.objects.create(
        kind="thread", listing=lst, counterparty_prospect=prospect, status="open"
    )
    inbound = Message.objects.create(
        conversation=conv,
        author_type="agent",
        author_side="seller",
        author_id=seller.id,
        action="inform",
        body="hi",
        status="sent",
        sent_at=timezone.now(),
    )
    plan = dal._responder_plan(conv.id, inbound.id)
    assert "skip" in plan  # prospects have no agent (one-way)
