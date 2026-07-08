"""
Outreach ledger + fan-out (Graph 3), reshaped 2026-07-07 to EXPLICIT recipients: the
caller (model/UI) selects who gets what — `[{user_id, listing_ids, body?}]` — and the
service enforces truth at commit. LLM-free: the invariant core (`ai.outreach_service`)
is pure/sync, so everything here runs without the Inngest dev server.

Covered:
  * `engine.rank_buyers_multi` — the deterministic per-buyer 'who matches what' merge;
  * launch → `awaiting_approval` + one ledger row per (buyer, listing) pair + engine
    score/reason annotation + a seller approval notification; strict validation;
  * `send_to_buyer` → ONE opener per buyer covering exactly the surviving listings
    (multi-listing = multiple attachments on one message) — THE per-buyer matching
    behavior: a buyer matched to A only gets A; a buyer matched to A+B gets one message
    with both attached;
  * the LEDGER GUARANTEE per pair — an already-reached (buyer, listing) drops out of a
    later campaign's attachments (partial overlap sends only the new listing);
  * idempotent replay; one-pair-chat reuse; approve/cancel gates; dispatch/outcome at
    buyer granularity; the confirm-gated `send_outreach` tool commits NOTHING on decline.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal
from typing import TypedDict

import pytest
from django.contrib.auth import get_user_model
from django.contrib.gis.geos import Point
from django.utils import timezone
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command

from ai import outreach_service as svc
from ai.models import OutreachCampaign, OutreachRecipient
from catalog.models import Listing, ListingProperty, Property, Sale
from chat.models import Chat, Message, MessageAttachment
from matching.engine import rank_buyers_multi
from notifications.models import Notification
from polaris_agent import dal
from polaris_agent.checkpointer import close_checkpointer, get_checkpointer
from polaris_agent.tools.copilot import WRITE_TOOL_NAMES, copilot_tools
from polaris_agent.tools.registry import tools_for

User = get_user_model()

GEO_A = Point(-122.330, 47.600, srid=4326)  # "downtown"
GEO_B = Point(-122.130, 47.600, srid=4326)  # ~9 mi east — outside GEO_A's 5-mi radius


def _user(email, **kw):
    return User.objects.create_user(
        email=email, password="pw-12345678", is_email_verified=True, **kw
    )


def _listing(seller, *, apn="subj", address="123 Pike St", price="450000", geo=GEO_A):
    """A seller listing over a geolocated property (so buyers can be ranked)."""
    prop = Property.objects.create(
        apn=apn,
        county_fips="53033",
        address_norm=f"norm:{apn}",
        address_raw=address,
        geom=geo,
        property_type="sfr",
        beds=3,
        sqft=2000,
        condition=2,
        last_sale_price=Decimal(price),
        last_sale_date=timezone.now().date() - dt.timedelta(days=30),
    )
    lst = Listing.objects.create(
        seller=seller, title="A home", asking_price=Decimal(price), status="active"
    )
    ListingProperty.objects.create(
        listing=lst, property=prop, asking_price=Decimal(price), sort_order=0
    )
    return lst


def _buyer_with_sale(email, *, name="Betty Buyer", days_ago=30, geo=GEO_A):
    """A buyer with a recent nearby cash purchase → lands in that area's candidate pool."""
    buyer = _user(email, full_name=name)
    Sale.objects.create(
        buyer=buyer,
        geom=geo,
        price=Decimal("440000"),
        purchased_at=timezone.now().date() - dt.timedelta(days=days_ago),
        cash_buyer=True,
        disposition="flip",
        source="test",
    )
    return buyer


def _spec(buyer, *listings, body=None):
    return {"user_id": buyer.id, "listing_ids": [lst.id for lst in listings], "body": body}


def _launch(seller, recipients, **kw):
    return svc.launch_outreach(seller.id, recipients, **kw)


# --- the engine merge: who matches what -----------------------------------------
@pytest.mark.django_db
def test_rank_buyers_multi_merges_per_buyer_matches():
    seller = _user("seller@x.com")
    la = _listing(seller, apn="a", address="1 A St", geo=GEO_A)
    lb = _listing(seller, apn="b", address="2 B St", geo=GEO_B)
    only_a = _buyer_with_sale("a-only@x.com", name="Amy Anear", geo=GEO_A)
    both = _buyer_with_sale("both@x.com", name="Bo Both", geo=GEO_A)
    Sale.objects.create(  # Bo also bought near B → matches both listings
        buyer=both,
        geom=GEO_B,
        price=Decimal("430000"),
        purchased_at=timezone.now().date() - dt.timedelta(days=40),
        cash_buyer=True,
        disposition="flip",
        source="test",
    )

    res = rank_buyers_multi([la.id, lb.id])
    by_id = {b["user_id"]: b for b in res["buyers"]}
    assert {m["listing_id"] for m in by_id[only_a.id]["matches"]} == {la.id}
    assert {m["listing_id"] for m in by_id[both.id]["matches"]} == {la.id, lb.id}
    # Every match carries its per-listing score + reason; best_score = max of matches.
    bo = by_id[both.id]
    assert all(m["score"] > 0 and m["reason"] for m in bo["matches"])
    assert bo["best_score"] == max(m["score"] for m in bo["matches"])


# --- launch: validate → per-pair ledger rows → awaiting approval ----------------
@pytest.mark.django_db
def test_launch_persists_pairs_annotates_scores_and_notifies():
    seller = _user("seller@x.com")
    listing = _listing(seller)
    buyer = _buyer_with_sale("buyer@x.com")

    res = _launch(seller, [_spec(buyer, listing)])
    assert res["campaign_id"] is not None
    assert res["pending_count"] == 1 and res["skipped_count"] == 0
    assert res["recipients"][0]["name"] == "Betty Buyer"

    campaign = OutreachCampaign.objects.get(id=res["campaign_id"])
    assert campaign.status == "awaiting_approval"
    assert campaign.listing_id == listing.id  # single-listing campaign keeps the FK
    rec = OutreachRecipient.objects.get(campaign=campaign, recipient_user=buyer)
    assert rec.status == "pending"
    # The engine annotated the pair (buyer ranks for this listing) — score + reason.
    assert rec.rank_score is not None and rec.rank_reason
    assert rec.draft_body and "Betty" in rec.draft_body  # templated fallback drafted

    # Nothing sent yet — no chat, no message, but the seller has an approval notice.
    assert Chat.objects.count() == 0
    assert Notification.objects.filter(user=seller, type="approval_required").count() == 1


@pytest.mark.django_db
def test_launch_respects_custom_body_and_unranked_buyers():
    seller = _user("seller@x.com")
    listing = _listing(seller)
    # A buyer the engine does NOT rank (no nearby history) — explicitly selected anyway.
    stranger = _user("faraway@x.com", full_name="Farah Far")

    res = _launch(seller, [_spec(stranger, listing, body="Hey Farah — this one's special.")])
    rec = OutreachRecipient.objects.get(campaign_id=res["campaign_id"])
    assert rec.rank_score is None and rec.rank_reason is None  # engine had no number
    assert rec.draft_body == "Hey Farah — this one's special."  # model body verbatim


@pytest.mark.django_db
def test_launch_rejects_foreign_listing_and_unknown_or_self_user():
    seller = _user("seller@x.com")
    other = _user("other@x.com")
    listing = _listing(seller)
    buyer = _buyer_with_sale("buyer@x.com")

    # Not the caller's listing.
    assert "error" in svc.launch_outreach(other.id, [_spec(buyer, listing)])
    # Unknown recipient / the seller themself.
    assert "error" in svc.launch_outreach(
        seller.id, [{"user_id": 999999, "listing_ids": [listing.id]}]
    )
    assert "error" in svc.launch_outreach(
        seller.id, [{"user_id": seller.id, "listing_ids": [listing.id]}]
    )
    # Empty selections.
    assert "error" in svc.launch_outreach(seller.id, [])
    assert OutreachCampaign.objects.count() == 0


# --- send: ONE message per buyer, exactly the matched listings attached ---------
@pytest.mark.django_db
def test_send_to_buyer_opens_pair_chat_with_listing_attachment():
    seller = _user("seller@x.com")
    listing = _listing(seller)
    buyer = _buyer_with_sale("buyer@x.com")

    res = _launch(seller, [_spec(buyer, listing)])
    svc.approve_campaign(seller.id, res["campaign_id"])

    out = svc.send_to_buyer(res["campaign_id"], buyer.id)
    assert out["status"] == "sent"
    assert out["recipient_user_id"] == buyer.id
    assert out["opener_message_id"] is not None
    assert out["listing_ids"] == [listing.id]

    chat = Chat.objects.get()  # exactly one pair chat
    assert out["chat_id"] == chat.id
    rec = OutreachRecipient.objects.get(campaign_id=res["campaign_id"])
    assert rec.status == "sent" and rec.chat_id == chat.id and rec.sent_at is not None

    # The opener is an AGENT message sent on the seller's behalf, listing attached.
    msg = Message.objects.get(id=out["opener_message_id"])
    assert msg.kind == "agent" and msg.sender_id == seller.id
    assert msg.action == "inform" and msg.status == "sent"
    att = MessageAttachment.objects.get(message=msg)
    assert att.kind == "listing" and att.listing_id == listing.id

    assert Notification.objects.filter(user=buyer, type="outreach_received").count() == 1


@pytest.mark.django_db
def test_multi_listing_send_matches_each_buyer_to_their_listings():
    """THE per-buyer matching behavior: one campaign over two listings — the buyer matched
    to both gets ONE message with BOTH attached; the buyer matched to one gets only it."""
    seller = _user("seller@x.com")
    la = _listing(seller, apn="a", address="1 A St", geo=GEO_A)
    lb = _listing(seller, apn="b", address="2 B St", geo=GEO_B)
    amy = _buyer_with_sale("amy@x.com", name="Amy Anear", geo=GEO_A)
    bo = _buyer_with_sale("bo@x.com", name="Bo Both", geo=GEO_A)

    res = _launch(seller, [_spec(amy, la), _spec(bo, la, lb)])
    assert res["pending_count"] == 3  # 3 (buyer, listing) pairs
    assert OutreachCampaign.objects.get(id=res["campaign_id"]).listing_id is None  # multi
    svc.approve_campaign(seller.id, res["campaign_id"])

    out_amy = svc.send_to_buyer(res["campaign_id"], amy.id)
    out_bo = svc.send_to_buyer(res["campaign_id"], bo.id)

    # Amy: one message, ONLY listing A attached.
    msg_amy = Message.objects.get(id=out_amy["opener_message_id"])
    assert [a.listing_id for a in msg_amy.attachments.all()] == [la.id]
    # Bo: ONE message covering BOTH listings.
    assert out_bo["status"] == "sent" and set(out_bo["listing_ids"]) == {la.id, lb.id}
    msg_bo = Message.objects.get(id=out_bo["opener_message_id"])
    assert {a.listing_id for a in msg_bo.attachments.all()} == {la.id, lb.id}
    assert Message.objects.filter(kind="agent").count() == 2  # one per buyer, not per pair
    # Both of Bo's ledger rows flipped under the one send.
    assert (
        OutreachRecipient.objects.filter(
            campaign_id=res["campaign_id"], recipient_user=bo, status="sent"
        ).count()
        == 2
    )


@pytest.mark.django_db
def test_send_to_buyer_is_idempotent_on_replay():
    """Inngest is at-least-once → a replayed send must never double-post the opener or the
    attachments, and the ledger rows stay a single SENT each."""
    seller = _user("seller@x.com")
    la = _listing(seller, apn="a", address="1 A St")
    lb = _listing(seller, apn="b", address="2 B St", geo=GEO_B)
    buyer = _buyer_with_sale("buyer@x.com")

    res = _launch(seller, [_spec(buyer, la, lb)])
    svc.approve_campaign(seller.id, res["campaign_id"])

    first = svc.send_to_buyer(res["campaign_id"], buyer.id)
    replay = svc.send_to_buyer(res["campaign_id"], buyer.id)
    assert first["status"] == "sent"
    assert replay["status"] == "already_sent"
    assert replay["chat_id"] == first["chat_id"]
    assert replay["opener_message_id"] == first["opener_message_id"]

    # Exactly one opener, two attachments, one outreach_received notification.
    assert Message.objects.filter(chat_id=first["chat_id"], kind="agent").count() == 1
    assert MessageAttachment.objects.count() == 2
    assert Notification.objects.filter(user=buyer, type="outreach_received").count() == 1


@pytest.mark.django_db
def test_ledger_partial_overlap_sends_only_the_new_listing():
    """The pair-level ledger: buyer already reached for listing A → a later campaign
    selecting (A, B) for them stages A as skipped and the send attaches ONLY B."""
    seller = _user("seller@x.com")
    la = _listing(seller, apn="a", address="1 A St")
    lb = _listing(seller, apn="b", address="2 B St", geo=GEO_B)
    buyer = _buyer_with_sale("buyer@x.com")

    r1 = _launch(seller, [_spec(buyer, la)])
    svc.approve_campaign(seller.id, r1["campaign_id"])
    assert svc.send_to_buyer(r1["campaign_id"], buyer.id)["status"] == "sent"

    # Campaign 2 selects BOTH listings for the same buyer.
    r2 = _launch(seller, [_spec(buyer, la, lb)])
    assert r2["pending_count"] == 1 and r2["skipped_count"] == 1  # A skipped at launch
    statuses = {row["listing_id"]: row["status"] for row in r2["recipients"][0]["listings"]}
    assert statuses[la.id] == "skipped_already_contacted" and statuses[lb.id] == "pending"

    svc.approve_campaign(seller.id, r2["campaign_id"])
    out = svc.send_to_buyer(r2["campaign_id"], buyer.id)
    assert out["status"] == "sent" and out["listing_ids"] == [lb.id]
    msg = Message.objects.get(id=out["opener_message_id"])
    assert [a.listing_id for a in msg.attachments.all()] == [lb.id]  # ONLY the new one

    # Full overlap (a third campaign, only A) → nothing to send at all.
    r3 = _launch(seller, [_spec(buyer, la)])
    svc.approve_campaign(seller.id, r3["campaign_id"])
    assert svc.send_to_buyer(r3["campaign_id"], buyer.id)["status"] == "skipped"
    # Still exactly one attachment per (listing, buyer) pair across all campaigns.
    attached = list(MessageAttachment.objects.values_list("listing_id", flat=True))
    assert sorted(attached) == sorted([la.id, lb.id])


@pytest.mark.django_db
def test_send_layer_recheck_blocks_a_stale_pending_row():
    """Even if a stale pending row is forced through, the send layer re-checks the ledger."""
    seller = _user("seller@x.com")
    listing = _listing(seller)
    buyer = _buyer_with_sale("buyer@x.com")

    r1 = _launch(seller, [_spec(buyer, listing)])
    svc.approve_campaign(seller.id, r1["campaign_id"])
    assert svc.send_to_buyer(r1["campaign_id"], buyer.id)["status"] == "sent"

    r2 = _launch(seller, [_spec(buyer, listing)])
    rec2 = OutreachRecipient.objects.get(campaign_id=r2["campaign_id"])
    rec2.status = "pending"
    rec2.save(update_fields=["status"])
    assert svc.send_to_buyer(r2["campaign_id"], buyer.id)["status"] == "skipped"
    assert Message.objects.filter(kind="agent").count() == 1


@pytest.mark.django_db
def test_second_outreach_to_same_buyer_reuses_the_pair_chat():
    """Two campaigns to the same buyer share the ONE pair chat (revisions #3): the second
    opener attaches its listing to the same chat, not a new one."""
    seller = _user("seller@x.com")
    l1 = _listing(seller, apn="a", address="1 A St")
    l2 = _listing(seller, apn="b", address="2 B St")
    buyer = _buyer_with_sale("buyer@x.com")

    r1 = _launch(seller, [_spec(buyer, l1)])
    svc.approve_campaign(seller.id, r1["campaign_id"])
    out1 = svc.send_to_buyer(r1["campaign_id"], buyer.id)

    r2 = _launch(seller, [_spec(buyer, l2)])
    svc.approve_campaign(seller.id, r2["campaign_id"])
    out2 = svc.send_to_buyer(r2["campaign_id"], buyer.id)

    assert out1["chat_id"] == out2["chat_id"]
    assert Chat.objects.count() == 1
    attached = set(
        MessageAttachment.objects.filter(message__chat_id=out1["chat_id"]).values_list(
            "listing_id", flat=True
        )
    )
    assert attached == {l1.id, l2.id}


# --- approve / cancel gates ----------------------------------------------------
@pytest.mark.django_db
def test_approve_and_cancel_gates():
    seller = _user("seller@x.com")
    listing = _listing(seller)
    buyer = _buyer_with_sale("buyer@x.com")

    res = _launch(seller, [_spec(buyer, listing)])
    cid = res["campaign_id"]

    # Cancel moves pending → cancelled and the campaign → cancelled.
    cancelled = svc.cancel_campaign(seller.id, cid)
    assert cancelled["status"] == "cancelled"
    assert OutreachRecipient.objects.filter(campaign_id=cid, status="cancelled").count() == 1
    assert svc.send_to_buyer(cid, buyer.id)["status"] == "cancelled"
    # Approving a cancelled campaign is rejected.
    assert "error" in svc.approve_campaign(seller.id, cid)

    # A fresh one approves cleanly, then re-approval is rejected.
    res2 = _launch(seller, [_spec(buyer, listing)])
    assert svc.approve_campaign(seller.id, res2["campaign_id"])["status"] == "sending"
    assert "error" in svc.approve_campaign(seller.id, res2["campaign_id"])


# --- fan-out support: dispatch info / outcome / finish (buyer granularity) ------
@pytest.mark.django_db
def test_dispatch_info_outcome_and_finish_are_buyer_grained():
    seller = _user("seller@x.com")
    la = _listing(seller, apn="a", address="1 A St")
    lb = _listing(seller, apn="b", address="2 B St", geo=GEO_B)
    b1 = _buyer_with_sale("b1@x.com", name="Ann A", days_ago=20)  # fresher → ranks higher
    b2 = _buyer_with_sale("b2@x.com", name="Bob B", days_ago=300)

    res = _launch(seller, [_spec(b1, la, lb), _spec(b2, la)])
    svc.approve_campaign(seller.id, res["campaign_id"])

    info = svc.campaign_dispatch_info(res["campaign_id"])
    assert info["status"] == "sending" and info["seller_id"] == seller.id
    assert info["listing_ids"] == sorted([la.id, lb.id])
    assert info["listing_addresses"] == ["1 A St", "2 B St"]
    # The send unit is the BUYER (2 buyers, 3 pairs), best rank first.
    assert info["buyer_ids"] == [b1.id, b2.id]

    for uid in info["buyer_ids"]:
        svc.send_to_buyer(res["campaign_id"], uid)

    outcome = svc.finish_campaign(res["campaign_id"])
    assert outcome["sent"] == 2 and outcome["total"] == 2  # buyers, not pairs
    assert outcome["pairs"]["sent"] == 3  # pair-level counts ride along
    assert OutreachCampaign.objects.get(id=res["campaign_id"]).status == "done"


# --- copilot tool wiring + confirm-gate ---------------------------------------
@pytest.mark.django_db
def test_send_outreach_tool_is_registered_and_confirm_gated():
    seller = _user("seller@x.com")
    names = {t.name for t in tools_for("copilot", seller.id)}
    assert "send_outreach" in names and "rank_buyers_for_listings" in names
    assert "send_outreach" in WRITE_TOOL_NAMES
    assert "launch_outreach" not in names  # the bundled rank-inside-write tool is gone


@pytest.mark.django_db
def test_rank_buyers_for_listings_dal_is_owner_scoped():
    seller = _user("seller@x.com")
    other = _user("other@x.com")
    la = _listing(seller, apn="a", address="1 A St")
    _buyer_with_sale("buyer@x.com")

    res = dal._rank_buyers_for_listings(seller.id, [la.id])
    assert res["listings"][0]["address"] == "1 A St"
    assert res["buyers"] and res["buyers"][0]["matches"][0]["listing_id"] == la.id

    foreign = dal._rank_buyers_for_listings(other.id, [la.id])
    assert foreign["buyers"] == [] and foreign["errors"]


@pytest.mark.django_db
def test_preview_outreach_flags_already_contacted_pairs():
    seller = _user("seller@x.com")
    la = _listing(seller, apn="a", address="1 A St")
    lb = _listing(seller, apn="b", address="2 B St", geo=GEO_B)
    buyer = _buyer_with_sale("buyer@x.com")

    r1 = _launch(seller, [_spec(buyer, la)])
    svc.approve_campaign(seller.id, r1["campaign_id"])
    svc.send_to_buyer(r1["campaign_id"], buyer.id)

    prev = dal._preview_outreach(seller.id, [_spec(buyer, la, lb)])
    flags = {
        row["listing_id"]: row["already_contacted"] for row in prev["recipients"][0]["listings"]
    }
    assert flags == {la.id: True, lb.id: False}
    assert prev["recipients"][0]["name"] == "Betty Buyer"
    # Validation errors surface as {error} (no confirm card).
    assert "error" in dal._preview_outreach(
        seller.id, [{"user_id": 999999, "listing_ids": [la.id]}]
    )


class _S(TypedDict, total=False):
    args: dict
    result: dict


def _one_tool_graph(checkpointer, tool):
    async def call(state: _S) -> _S:
        return {"result": await tool.coroutine(**state["args"])}

    g = StateGraph(_S)
    g.add_node("call", call)
    g.add_edge(START, "call")
    g.add_edge("call", END)
    return g.compile(checkpointer=checkpointer)


@pytest.fixture
def seller_listing_buyer(db):
    seller = _user("seller@x.com")
    listing = _listing(seller)
    buyer = _buyer_with_sale("buyer@x.com")
    return seller, listing, buyer


@pytest.mark.django_db(transaction=True)
async def test_send_outreach_tool_declined_commits_nothing(
    reset_checkpointer, seller_listing_buyer
):
    from asgiref.sync import sync_to_async

    seller, listing, buyer = seller_listing_buyer
    tool = next(t for t in copilot_tools(seller.id) if t.name == "send_outreach")
    checkpointer = await get_checkpointer()
    graph = _one_tool_graph(checkpointer, tool)
    cfg = {"configurable": {"thread_id": "outreach-decline-1"}}
    args = {
        "recipients": [{"user_id": buyer.id, "listing_ids": [listing.id], "body": "custom opener"}]
    }

    # Validates + resolves, then pauses at the confirm interrupt — nothing persisted yet.
    paused = await graph.ainvoke({"args": args}, config=cfg)
    assert "__interrupt__" in paused
    payload = paused["__interrupt__"][0].value
    assert payload["action"] == "send_outreach"
    card = payload["proposal"]["recipients"][0]
    assert card["name"] == "Betty Buyer" and card["body"] == "custom opener"
    assert card["listings"][0]["already_contacted"] is False
    assert await sync_to_async(OutreachCampaign.objects.count)() == 0

    # Decline → no campaign is created, no chat opened.
    done = await graph.ainvoke(Command(resume={"approved": False}), config=cfg)
    assert done["result"]["status"] == "cancelled"
    assert await sync_to_async(OutreachCampaign.objects.count)() == 0
    assert await sync_to_async(Chat.objects.count)() == 0

    await close_checkpointer()
