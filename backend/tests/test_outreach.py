"""
P5 — outreach ledger + fan-out (Graph 3), rewired to the v2 chat schema. LLM-free: the
whole invariant core (`ai.outreach_service`) is pure/sync, so the ledger guarantee, opener
idempotency, and the one-pair-chat + listing-attachment rewire are testable without the
Inngest dev server. Plus the confirm-gate on the copilot's `launch_outreach` tool.

Covered:
  * launch → `awaiting_approval` + ranked recipients + a seller approval notification;
  * send_recipient → opens the ONE pair `chat.Chat`, posts the opener as an agent message
    with the listing attached, notifies the buyer, and returns the arming ids;
  * the LEDGER GUARANTEE — a listing reaches each buyer at most once, ever (idempotent
    replay + a second campaign skips-already-contacted);
  * a second outreach to the same buyer reuses the same pair chat (accrues the listing);
  * approve/cancel gates; dispatch-info / outcome / finish tallies;
  * the copilot `launch_outreach` tool is confirm-gated and commits NOTHING on decline.
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
from notifications.models import Notification
from polaris_agent.checkpointer import close_checkpointer, get_checkpointer
from polaris_agent.tools.copilot import WRITE_TOOL_NAMES, copilot_tools
from polaris_agent.tools.registry import tools_for

User = get_user_model()

GEO = Point(-122.330, 47.600, srid=4326)


def _user(email, **kw):
    return User.objects.create_user(
        email=email, password="pw-12345678", is_email_verified=True, **kw
    )


def _listing(seller, *, apn="subj", address="123 Pike St", price="450000"):
    """A seller listing over a geolocated property (so buyers can be ranked)."""
    prop = Property.objects.create(
        apn=apn,
        county_fips="53033",
        address_norm=f"norm:{apn}",
        address_raw=address,
        geom=GEO,
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
    ListingProperty.objects.create(listing=lst, property=prop, asking_price=Decimal(price), sort_order=0)
    return lst


def _buyer_with_sale(email, *, name="Betty Buyer", days_ago=30):
    """A buyer with a recent nearby cash purchase → lands in the candidate pool + ranks."""
    buyer = _user(email, full_name=name)
    Sale.objects.create(
        buyer=buyer,
        geom=GEO,
        price=Decimal("440000"),
        purchased_at=timezone.now().date() - dt.timedelta(days=days_ago),
        cash_buyer=True,
        disposition="flip",
        source="test",
    )
    return buyer


def _launch(seller, listing, **kw):
    res = svc.launch_outreach(seller.id, listing.id, **kw)
    return res


# --- launch: rank → draft → persist awaiting_approval --------------------------
@pytest.mark.django_db
def test_launch_persists_awaiting_approval_and_notifies():
    seller = _user("seller@x.com")
    listing = _listing(seller)
    buyer = _buyer_with_sale("buyer@x.com")

    res = _launch(seller, listing)
    assert res["campaign_id"] is not None
    assert res["pending_count"] == 1
    names = {r["name"] for r in res["ranked"]}
    assert "Betty Buyer" in names

    campaign = OutreachCampaign.objects.get(id=res["campaign_id"])
    assert campaign.status == "awaiting_approval"
    rec = OutreachRecipient.objects.get(campaign=campaign, recipient_user=buyer)
    assert rec.status == "pending"
    assert rec.draft_body and "Betty" in rec.draft_body  # personalized opener drafted
    assert rec.rank_score is not None

    # Nothing sent yet — no chat, no message, but the seller has an approval notice.
    assert Chat.objects.count() == 0
    assert Notification.objects.filter(user=seller, type="approval_required").count() == 1


@pytest.mark.django_db
def test_launch_rejects_foreign_or_ungeocoded_listing():
    seller = _user("seller@x.com")
    other = _user("other@x.com")
    listing = _listing(seller)
    # Not the caller's listing.
    assert "error" in svc.launch_outreach(other.id, listing.id)

    # A listing whose property has no geom cannot be ranked.
    bare = Listing.objects.create(seller=seller, asking_price=Decimal("1"), status="active")
    prop = Property.objects.create(address_norm="nogeo", address_raw="nowhere")
    ListingProperty.objects.create(listing=bare, property=prop, sort_order=0)
    assert "error" in svc.launch_outreach(seller.id, bare.id)


# --- send: open the ONE pair chat + opener-as-attachment -----------------------
@pytest.mark.django_db
def test_send_recipient_opens_pair_chat_with_listing_attachment():
    seller = _user("seller@x.com")
    listing = _listing(seller)
    buyer = _buyer_with_sale("buyer@x.com")

    res = _launch(seller, listing)
    svc.approve_campaign(seller.id, res["campaign_id"])
    rec = OutreachRecipient.objects.get(campaign_id=res["campaign_id"], recipient_user=buyer)

    out = svc.send_recipient(rec.id)
    assert out["status"] == "sent"
    assert out["recipient_user_id"] == buyer.id
    assert out["opener_message_id"] is not None

    # Exactly one pair chat between seller & buyer.
    chat = Chat.objects.get()
    assert out["chat_id"] == chat.id
    rec.refresh_from_db()
    assert rec.status == "sent" and rec.chat_id == chat.id and rec.sent_at is not None

    # The opener is an AGENT message sent on the seller's behalf, listing attached.
    msg = Message.objects.get(id=out["opener_message_id"])
    assert msg.kind == "agent" and msg.sender_id == seller.id
    assert msg.action == "inform" and msg.status == "sent"
    att = MessageAttachment.objects.get(message=msg)
    assert att.kind == "listing" and att.listing_id == listing.id

    # The buyer is notified.
    assert Notification.objects.filter(user=buyer, type="outreach_received").count() == 1


@pytest.mark.django_db
def test_send_recipient_is_idempotent_on_replay():
    """Inngest is at-least-once → a replayed send must never double-post the opener or the
    attachment, and the ledger row stays a single SENT."""
    seller = _user("seller@x.com")
    listing = _listing(seller)
    buyer = _buyer_with_sale("buyer@x.com")

    res = _launch(seller, listing)
    svc.approve_campaign(seller.id, res["campaign_id"])
    rec = OutreachRecipient.objects.get(campaign_id=res["campaign_id"], recipient_user=buyer)

    first = svc.send_recipient(rec.id)
    replay = svc.send_recipient(rec.id)
    assert replay["status"] == "already_sent"
    assert replay["chat_id"] == first["chat_id"]

    # Exactly one opener, one attachment, one outreach_received notification.
    assert Message.objects.filter(chat_id=first["chat_id"], kind="agent").count() == 1
    assert MessageAttachment.objects.count() == 1
    assert Notification.objects.filter(user=buyer, type="outreach_received").count() == 1


@pytest.mark.django_db
def test_ledger_blocks_second_campaign_reaching_the_same_buyer():
    """The delivery ledger: a listing reaches each buyer at most once, EVER, across
    campaigns. A second launch marks the already-reached buyer skipped."""
    seller = _user("seller@x.com")
    listing = _listing(seller)
    buyer = _buyer_with_sale("buyer@x.com")

    r1 = _launch(seller, listing)
    svc.approve_campaign(seller.id, r1["campaign_id"])
    rec1 = OutreachRecipient.objects.get(campaign_id=r1["campaign_id"], recipient_user=buyer)
    assert svc.send_recipient(rec1.id)["status"] == "sent"

    # A fresh campaign for the SAME listing → the buyer is already-contacted at launch.
    r2 = _launch(seller, listing)
    assert r2["pending_count"] == 0 and r2["skipped_count"] == 1
    rec2 = OutreachRecipient.objects.get(campaign_id=r2["campaign_id"], recipient_user=buyer)
    assert rec2.status == "skipped_already_contacted"

    # And even if a stale pending row is forced through, the send layer re-checks the ledger.
    rec2.status = "pending"
    rec2.save(update_fields=["status"])
    assert svc.send_recipient(rec2.id)["status"] == "skipped"
    # Still exactly one opener for that (listing, buyer).
    assert Message.objects.filter(kind="agent").count() == 1


@pytest.mark.django_db
def test_second_outreach_to_same_buyer_reuses_the_pair_chat():
    """Two different listings to the same buyer share the ONE pair chat (revisions #3):
    the second opener attaches its listing to the same chat, not a new one."""
    seller = _user("seller@x.com")
    l1 = _listing(seller, apn="a", address="1 A St")
    l2 = _listing(seller, apn="b", address="2 B St")
    buyer = _buyer_with_sale("buyer@x.com")

    r1 = _launch(seller, l1)
    svc.approve_campaign(seller.id, r1["campaign_id"])
    rec1 = OutreachRecipient.objects.get(campaign_id=r1["campaign_id"], recipient_user=buyer)
    out1 = svc.send_recipient(rec1.id)

    r2 = _launch(seller, l2)
    svc.approve_campaign(seller.id, r2["campaign_id"])
    rec2 = OutreachRecipient.objects.get(campaign_id=r2["campaign_id"], recipient_user=buyer)
    out2 = svc.send_recipient(rec2.id)

    assert out1["chat_id"] == out2["chat_id"]
    assert Chat.objects.count() == 1
    # Both listings now hang off messages in the same chat.
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
    _buyer_with_sale("buyer@x.com")

    res = _launch(seller, listing)
    cid = res["campaign_id"]

    # Cancel moves pending → cancelled and the campaign → cancelled.
    cancelled = svc.cancel_campaign(seller.id, cid)
    assert cancelled["status"] == "cancelled"
    assert OutreachRecipient.objects.filter(campaign_id=cid, status="cancelled").count() == 1
    # Approving a cancelled campaign is rejected.
    assert "error" in svc.approve_campaign(seller.id, cid)

    # A fresh one approves cleanly, then re-approval is rejected.
    res2 = _launch(seller, listing)
    assert svc.approve_campaign(seller.id, res2["campaign_id"])["status"] == "sending"
    assert "error" in svc.approve_campaign(seller.id, res2["campaign_id"])


# --- fan-out support: dispatch info / outcome / finish -------------------------
@pytest.mark.django_db
def test_dispatch_info_outcome_and_finish():
    seller = _user("seller@x.com")
    listing = _listing(seller)
    b1 = _buyer_with_sale("b1@x.com", name="Ann A")
    b2 = _buyer_with_sale("b2@x.com", name="Bob B")

    ai_chat_id = None
    res = _launch(seller, listing, copilot_ai_chat_id=ai_chat_id)
    svc.approve_campaign(seller.id, res["campaign_id"])

    info = svc.campaign_dispatch_info(res["campaign_id"])
    assert info["status"] == "sending" and info["seller_id"] == seller.id
    assert info["listing_address"] == "123 Pike St"
    # Two pending recipients, highest rank first.
    assert len(info["recipient_ids"]) == 2

    for rid in info["recipient_ids"]:
        svc.send_recipient(rid)

    outcome = svc.finish_campaign(res["campaign_id"])
    assert outcome["sent"] == 2 and outcome["total"] == 2
    OutreachCampaign.objects.get(id=res["campaign_id"]).refresh_from_db()
    assert OutreachCampaign.objects.get(id=res["campaign_id"]).status == "done"
    assert {b1.id, b2.id} == set(
        OutreachRecipient.objects.filter(campaign_id=res["campaign_id"]).values_list(
            "recipient_user_id", flat=True
        )
    )


# --- copilot tool wiring + confirm-gate ---------------------------------------
@pytest.mark.django_db
def test_launch_outreach_tool_is_registered_and_confirm_gated():
    seller = _user("seller@x.com")
    names = {t.name for t in tools_for("copilot", seller.id)}
    assert "launch_outreach" in names
    assert "launch_outreach" in WRITE_TOOL_NAMES


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
def seller_with_buyer(db):
    seller = _user("seller@x.com")
    listing = _listing(seller)
    _buyer_with_sale("buyer@x.com")
    return seller, listing


@pytest.mark.django_db(transaction=True)
async def test_launch_outreach_tool_declined_commits_nothing(reset_checkpointer, seller_with_buyer):
    from asgiref.sync import sync_to_async

    seller, listing = seller_with_buyer
    tool = next(t for t in copilot_tools(seller.id) if t.name == "launch_outreach")
    checkpointer = await get_checkpointer()
    graph = _one_tool_graph(checkpointer, tool)
    cfg = {"configurable": {"thread_id": "outreach-decline-1"}}

    # Ranks, then pauses at the confirm interrupt — nothing persisted yet.
    paused = await graph.ainvoke({"args": {"listing_id": listing.id}}, config=cfg)
    assert "__interrupt__" in paused
    payload = paused["__interrupt__"][0].value
    assert payload["action"] == "launch_outreach" and payload["proposal"]["buyers"]
    assert await sync_to_async(OutreachCampaign.objects.count)() == 0

    # Decline → no campaign is created, no chat opened.
    done = await graph.ainvoke(Command(resume={"approved": False}), config=cfg)
    assert done["result"]["status"] == "cancelled"
    assert await sync_to_async(OutreachCampaign.objects.count)() == 0
    assert await sync_to_async(Chat.objects.count)() == 0

    await close_checkpointer()
