"""
P2 copilot — LLM-free coverage of the dal seams (the plumbing the ReAct agent rides
on), the tool suite wiring, and the crown-jewel new mechanism: confirm-every-write via
a LangGraph human-in-the-loop interrupt (a write commits ONLY after an explicit approve
resume). No model is called anywhere here.
"""

from __future__ import annotations

from typing import TypedDict

import pytest
from django.contrib.auth import get_user_model
from langchain_core.messages import AIMessage, HumanMessage
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command

from catalog.models import BuyBox, Listing, Mandate
from polaris_agent import dal
from polaris_agent.checkpointer import close_checkpointer, get_checkpointer
from polaris_agent.tools.copilot import WRITE_TOOL_NAMES, copilot_tools
from polaris_agent.tools.registry import tools_for

User = get_user_model()


@pytest.fixture
def user(db):
    return User.objects.create_user(
        email="seller@x.com", password="pw-12345678", is_email_verified=True, full_name="Sal Seller"
    )


@pytest.fixture
def other(db):
    return User.objects.create_user(email="other@x.com", password="pw-12345678")


# ============================ copilot chat lifecycle ============================
@pytest.mark.django_db
def test_ai_chat_lifecycle_and_transcript_rehydration(user):
    chat_id = dal._create_ai_chat(user.id)
    assert dal._owns_ai_chat(user.id, chat_id) is True
    assert dal._needs_title(chat_id) is True

    dal._save_ai_message(chat_id, role="user", content="value my house")
    dal._save_ai_message(chat_id, role="assistant", content="sure — what's the address?")

    history = dal._load_transcript(chat_id)
    assert [type(m) for m in history] == [HumanMessage, AIMessage]
    assert history[0].content == "value my house"
    assert history[1].content == "sure — what's the address?"

    dal._set_title_if_empty(chat_id, "Valuing a house")
    assert dal._needs_title(chat_id) is False
    # set_title_if_empty is a no-op once titled.
    dal._set_title_if_empty(chat_id, "SHOULD NOT WIN")
    chats = dal._list_ai_chats(user.id)
    assert chats[0]["id"] == chat_id and chats[0]["title"] == "Valuing a house"


@pytest.mark.django_db
def test_ai_chat_ownership_scoping(user, other):
    chat_id = dal._create_ai_chat(user.id)
    assert dal._owns_ai_chat(other.id, chat_id) is False
    assert dal._list_ai_chats(other.id) == []


# ============================ agent memory =====================================
@pytest.mark.django_db
def test_memory_roundtrip_is_namespace_scoped(user):
    dal._write_memory(user.id, "prefers cash buyers", namespace="seller")
    dal._write_memory(user.id, "unrelated note", namespace="general")
    seller_mem = dal._read_memory(user.id, namespace="seller")
    assert [m["content"] for m in seller_mem] == ["prefers cash buyers"]


@pytest.mark.django_db
def test_agent_instructions_default_blank(user):
    # Signal creates the profile; agent_instructions defaults to "".
    assert dal._agent_instructions(user.id) == ""


# ============================ listings / mandate ===============================
@pytest.mark.django_db
def test_create_list_detail_and_mandate_roundtrip(user):
    summary = dal._create_listing(
        user.id,
        {"address": "123 Test St, Seattle WA", "beds": 3, "sqft": 1800, "asking_price": 425000},
    )
    lid = summary["listing_id"]
    assert summary["address"] == "123 Test St, Seattle WA"
    assert Listing.objects.filter(id=lid, seller=user).exists()

    listings = dal._list_seller_listings(user.id)
    assert any(row["listing_id"] == lid for row in listings)

    detail = dal._get_listing_detail(lid, user.id)
    assert len(detail["properties"]) == 1
    assert detail["mandate"]["exists"] is False

    set_res = dal._set_mandate_for_listing(
        lid,
        user.id,
        {"floor_price": 400000, "must_haves": ["clear title"], "instructions": "cash pref"},
    )
    assert set_res["floor_price"] == 400000.0
    assert dal._get_mandate_for_listing(lid, user.id)["must_haves"] == ["clear title"]


@pytest.mark.django_db
def test_listing_seams_are_owner_scoped(user, other):
    lid = dal._create_listing(user.id, {"address": "1 A St", "asking_price": 100000})["listing_id"]
    assert "error" in dal._get_listing_detail(lid, other.id)
    assert "error" in dal._set_mandate_for_listing(lid, other.id, {"floor_price": 1})
    assert "error" in dal._estimate_for_listing(lid, other.id, False)
    assert "error" in dal._rank_buyers(lid, other.id)
    assert "error" in dal._assess_deal_for_listing(lid, other.id, None)


@pytest.mark.django_db
def test_rank_and_assess_degrade_gracefully_without_geo(user):
    """A plain-address listing has no geolocated property + no comps: the engine returns
    structured 'nothing to rank/price' payloads, never a crash."""
    lid = dal._create_listing(user.id, {"address": "9 Nowhere Rd", "asking_price": 250000})[
        "listing_id"
    ]
    ranked = dal._rank_buyers(lid, user.id)
    assert "ranked" in ranked and ranked["ranked"] == []
    assessed = dal._assess_deal_for_listing(lid, user.id, "fix_flip")
    assert assessed["verdict"] == "hold"
    # ad-hoc find_buyers on an unknown address resolves no geo → graceful empty.
    assert dal._find_buyers(user.id, "somewhere unmapped")["ranked"] == []


# ============================ buy-box CRUD =====================================
@pytest.mark.django_db
def test_buy_box_crud_with_geo_and_mandate(user, other):
    created = dal._create_buy_box(
        user.id,
        {
            "name": "KC flips",
            "strategy": "fix_flip",
            "price_max": 500000,
            "ceiling_price": 480000,
            "must_haves": ["clear title"],
            "geo": {"geo_type": "radius", "center_lat": 47.6, "center_lon": -122.3, "radius_mi": 5},
        },
    )
    box_id = created["buy_box_id"]
    assert created["mandate"]["ceiling_price"] == 480000.0
    assert created["n_geos"] == 1
    assert BuyBox.objects.filter(id=box_id, buyer=user).exists()

    assert any(b["buy_box_id"] == box_id for b in dal._list_buy_boxes(user.id))

    updated = dal._update_buy_box(
        user.id, box_id, {"price_max": 550000, "instructions": "no flood zone"}
    )
    assert updated["price_max"] == 550000.0
    assert updated["mandate"]["instructions"] == "no flood zone"

    # owner-scoped: a different user can neither read nor delete this box.
    assert "error" in dal._get_buy_box(other.id, box_id)
    assert "error" in dal._delete_buy_box(other.id, box_id)

    deleted = dal._delete_buy_box(user.id, box_id)
    assert deleted["deleted"] is True
    assert not BuyBox.objects.filter(id=box_id).exists()
    # its mandate cascaded away too.
    assert not Mandate.objects.filter(buy_box_id=box_id).exists()


# ============================ tool suite wiring ================================
@pytest.mark.django_db
def test_tool_suite_covers_reads_and_confirm_gated_writes(user):
    tools = tools_for("copilot", user.id)
    names = {t.name for t in tools}
    # A representative slice of the API-mirroring surface (revisions §polaris-ai).
    expected_reads = {
        "lookup_property",
        "list_my_listings",
        "estimate_market_value",
        "get_comps",
        "list_my_buy_boxes",
        "rank_buyers_for_listings",
        "find_buyers",
        "assess_deal",
        "read_memory",
        "list_chats",
    }
    assert expected_reads <= names
    # Every declared write tool is present and marked confirm-gated.
    assert WRITE_TOOL_NAMES <= names
    # write_memory is a low-stakes write — present but NOT confirm-gated.
    assert "write_memory" in names and "write_memory" not in WRITE_TOOL_NAMES


# ============================ chats: resolver + follow-up sends =================
def _pair_chat_with_history(principal, counterparty, *, listing=None, last_from_principal=True):
    """A (principal, counterparty) chat where the last message direction is controlled —
    the fixture for `awaiting_reply` semantics."""
    from chat.services import get_or_create_chat, post_human_message

    chat, _ = get_or_create_chat(principal.id, counterparty.id)
    post_human_message(
        chat.id,
        principal.id,
        "opener",
        attachment_listing_ids=[listing.id] if listing else None,
    )
    if not last_from_principal:
        post_human_message(chat.id, counterparty.id, "interested — tell me more")
    return chat


@pytest.mark.django_db
def test_list_chats_filters_by_name_listing_and_awaiting_reply(user, other):
    buyer2 = User.objects.create_user(
        email="kate@x.com", password="pw-12345678", full_name="Kate Brennan"
    )
    lid = dal._create_listing(user.id, {"address": "100 Alder St", "asking_price": 300000})[
        "listing_id"
    ]
    from catalog.models import Listing

    listing = Listing.objects.get(id=lid)
    awaiting = _pair_chat_with_history(user, other, listing=listing, last_from_principal=True)
    answered = _pair_chat_with_history(user, buyer2, last_from_principal=False)

    rows = dal._list_chats(user.id)
    assert {r["chat_id"] for r in rows} == {awaiting.id, answered.id}
    by_id = {r["chat_id"]: r for r in rows}
    assert by_id[awaiting.id]["awaiting_reply"] is True
    assert by_id[answered.id]["awaiting_reply"] is False
    assert by_id[answered.id]["last_message"]["from_me"] is False

    assert [r["chat_id"] for r in dal._list_chats(user.id, awaiting_reply_only=True)] == [
        awaiting.id
    ]
    assert [r["chat_id"] for r in dal._list_chats(user.id, counterparty="kate")] == [answered.id]
    assert [r["chat_id"] for r in dal._list_chats(user.id, involves_listing_id=lid)] == [
        awaiting.id
    ]
    # The counterparty sees the same chats from their side; a stranger sees none.
    assert dal._list_chats(buyer2.id)[0]["counterparty"]["name"] == "Sal Seller"


@pytest.mark.django_db
def test_send_chat_messages_is_member_scoped_and_replay_safe(user, other):
    stranger_a = User.objects.create_user(email="sa@x.com", password="pw-12345678")
    stranger_b = User.objects.create_user(email="sb@x.com", password="pw-12345678")
    from chat.models import Message
    from chat.services import get_or_create_chat

    mine, _ = get_or_create_chat(user.id, other.id)
    not_mine, _ = get_or_create_chat(stranger_a.id, stranger_b.id)

    # preview: only my chats resolve (the tool refuses before the confirm card otherwise).
    names = dal._preview_chat_sends(user.id, [mine.id, not_mine.id, 999999])
    assert set(names) == {mine.id}

    sends = [
        {"chat_id": mine.id, "body": "Following up — still interested?", "listing_ids": []},
        {"chat_id": not_mine.id, "body": "should never land", "listing_ids": []},
    ]
    res = dal._send_chat_messages(user.id, sends, "copilot:thread-1")
    assert res["sent"] == 1
    by_chat = {r["chat_id"]: r for r in res["results"]}
    assert by_chat[mine.id]["status"] == "sent"
    assert by_chat[not_mine.id]["status"] == "error"
    assert Message.objects.filter(chat=not_mine).count() == 0
    sent_msg = Message.objects.get(chat=mine)
    assert sent_msg.kind == "agent" and sent_msg.sender_id == user.id

    # Same turn (same prefix) replayed → duplicate, nothing double-sent.
    replay = dal._send_chat_messages(user.id, sends[:1], "copilot:thread-1")
    assert replay["sent"] == 0
    assert replay["results"][0]["status"] == "duplicate"
    assert Message.objects.filter(chat=mine).count() == 1

    # A NEW turn (new thread_id) may follow up again — repeatable by design.
    again = dal._send_chat_messages(user.id, sends[:1], "copilot:thread-2")
    assert again["sent"] == 1
    assert Message.objects.filter(chat=mine).count() == 2


# ============================ confirm-every-write (interrupt/resume) ============
class _S(TypedDict, total=False):
    args: dict
    result: dict


def _tool_by_name(user_id: int, name: str):
    return next(t for t in copilot_tools(user_id) if t.name == name)


def _one_tool_graph(checkpointer, tool):
    """A minimal graph whose single node runs one copilot tool. The tool raises the
    confirm interrupt; the checkpointer makes the pause/resume durable — exactly the
    shape the CopilotConsumer drives, without an LLM."""

    async def call(state: _S) -> _S:
        return {"result": await tool.coroutine(**state["args"])}

    g = StateGraph(_S)
    g.add_node("call", call)
    g.add_edge(START, "call")
    g.add_edge("call", END)
    return g.compile(checkpointer=checkpointer)


@pytest.mark.django_db(transaction=True)
async def test_write_tool_interrupts_then_commits_on_approve(reset_checkpointer, user):
    checkpointer = await get_checkpointer()
    graph = _one_tool_graph(checkpointer, _tool_by_name(user.id, "create_listing"))
    cfg = {"configurable": {"thread_id": "confirm-approve-1"}}
    args = {"address": "77 Confirm Ave", "beds": 4, "asking_price": 600000}

    # First run pauses at the confirm interrupt — nothing written yet.
    paused = await graph.ainvoke({"args": args}, config=cfg)
    assert "__interrupt__" in paused
    payload = paused["__interrupt__"][0].value
    assert payload["kind"] == "confirm_write" and payload["action"] == "create_listing"
    from asgiref.sync import sync_to_async

    assert await sync_to_async(Listing.objects.filter(seller=user).count)() == 0

    # Approve → the SAME thread resumes and the write commits exactly once.
    done = await graph.ainvoke(Command(resume={"approved": True}), config=cfg)
    assert done["result"]["address"] == "77 Confirm Ave"
    assert await sync_to_async(Listing.objects.filter(seller=user).count)() == 1

    await close_checkpointer()


@pytest.mark.django_db(transaction=True)
async def test_write_tool_declined_commits_nothing(reset_checkpointer, user):
    checkpointer = await get_checkpointer()
    graph = _one_tool_graph(checkpointer, _tool_by_name(user.id, "create_listing"))
    cfg = {"configurable": {"thread_id": "confirm-decline-1"}}

    paused = await graph.ainvoke({"args": {"address": "declined", "asking_price": 1}}, config=cfg)
    assert "__interrupt__" in paused

    done = await graph.ainvoke(Command(resume={"approved": False}), config=cfg)
    assert done["result"]["status"] == "cancelled"
    from asgiref.sync import sync_to_async

    assert await sync_to_async(Listing.objects.filter(seller=user).count)() == 0

    await close_checkpointer()


@pytest.mark.django_db(transaction=True)
async def test_send_chat_messages_interrupts_with_drafts_then_sends_on_approve(
    reset_checkpointer, user, other
):
    """The batch follow-up write rides the same confirm gate: the interrupt payload
    carries the per-recipient drafts (what the card renders), nothing is sent until the
    approve resume, and the commit posts kind='agent' messages as the principal."""
    from asgiref.sync import sync_to_async

    from chat.models import Message
    from chat.services import get_or_create_chat

    chat, _ = await sync_to_async(get_or_create_chat)(user.id, other.id)
    checkpointer = await get_checkpointer()
    graph = _one_tool_graph(checkpointer, _tool_by_name(user.id, "send_chat_messages"))
    cfg = {"configurable": {"thread_id": "confirm-followup-1"}}
    args = {
        "messages": [
            {"chat_id": chat.id, "body": "Any thoughts on the Alder St deal?", "listing_ids": []}
        ]
    }

    paused = await graph.ainvoke({"args": args}, config=cfg)
    assert "__interrupt__" in paused
    payload = paused["__interrupt__"][0].value
    assert payload["kind"] == "confirm_write" and payload["action"] == "send_chat_messages"
    drafts = payload["proposal"]["messages"]
    assert len(drafts) == 1 and drafts[0]["to"] and drafts[0]["chat_id"] == chat.id
    count = await sync_to_async(Message.objects.filter(chat=chat).count)()
    assert count == 0  # nothing sent while paused

    done = await graph.ainvoke(Command(resume={"approved": True}), config=cfg)
    assert done["result"]["status"] == "sent" and done["result"]["sent"] == 1
    msg = await sync_to_async(Message.objects.get)(chat=chat)
    assert msg.kind == "agent" and msg.sender_id == user.id

    await close_checkpointer()


# ============ durable pending-confirm (survives nav / reload / restart) =========
@pytest.mark.django_db
def test_pending_confirm_dal_roundtrip(user):
    """The parked-turn payload persists on the AiChat row and clears cleanly. This is what
    lets a fresh socket rehydrate the confirm + resume the interrupt after a reload."""
    chat_id = dal._create_ai_chat(user.id)
    assert dal._load_pending_confirm(chat_id) is None

    payload = {
        "conv_id": chat_id,
        "cfg": {"configurable": {"thread_id": f"copilot:{chat_id}:1", "ai_chat_id": chat_id}},
        "buf": ["Sure, "],
        "needs_title": True,
        "first_body": "list 1 A St",
        "ids": ["abc123"],
        "value": {"kind": "confirm_write", "action": "create_listing", "summary": "x", "proposal": {}},
    }
    dal._save_pending_confirm(chat_id, payload)
    loaded = dal._load_pending_confirm(chat_id)
    assert loaded["cfg"]["configurable"]["thread_id"] == f"copilot:{chat_id}:1"
    assert loaded["value"]["action"] == "create_listing"

    dal._clear_pending_confirm(chat_id)
    assert dal._load_pending_confirm(chat_id) is None


@pytest.mark.django_db
def test_detail_serializer_exposes_only_the_confirm_render_payload(user):
    """The REST detail exposes the card payload (so a reopened session rebuilds it) but
    never the internal cfg/thread_id/buf, and NULL when nothing is parked."""
    from ai.models import AiChat
    from ai.serializers import AiChatDetailSerializer

    chat = AiChat.objects.create(owner=user)
    assert AiChatDetailSerializer(chat).data["pending_confirm"] is None

    value = {
        "kind": "confirm_write",
        "action": "create_listing",
        "summary": "Create listing",
        "proposal": {"fields": {"address": "1 A St"}},
    }
    chat.pending_confirm = {"cfg": {"secret": 1}, "buf": ["internal"], "value": value}
    chat.save(update_fields=["pending_confirm"])

    exposed = AiChatDetailSerializer(chat).data["pending_confirm"]
    assert exposed == value
    assert "cfg" not in exposed and "buf" not in exposed  # internals never leak


@pytest.mark.django_db(transaction=True)
async def test_parked_confirm_resumes_from_the_durable_record_on_a_fresh_graph(
    reset_checkpointer, user
):
    """The reload contract: a turn paused under one 'socket' resumes + commits from a
    BRAND-NEW graph object (no in-memory state) using only the durable record + the shared
    Postgres checkpointer — proving the parked interrupt survives a nav/reload/restart."""
    from asgiref.sync import sync_to_async

    checkpointer = await get_checkpointer()
    chat_id = await dal.create_ai_chat(user.id)
    cfg = {"configurable": {"thread_id": f"copilot:{chat_id}:1"}}
    args = {"address": "88 Durable Way", "beds": 3, "asking_price": 500000}

    # First "socket": pause at the confirm interrupt, then persist the resumable payload.
    graph1 = _one_tool_graph(checkpointer, _tool_by_name(user.id, "create_listing"))
    paused = await graph1.ainvoke({"args": args}, config=cfg)
    assert "__interrupt__" in paused
    await dal.save_pending_confirm(
        chat_id,
        {
            "conv_id": chat_id,
            "cfg": cfg,
            "buf": [],
            "needs_title": True,
            "first_body": "list 88 Durable Way",
            "ids": [i.id for i in paused["__interrupt__"]],
            "value": paused["__interrupt__"][0].value,
        },
    )
    assert await sync_to_async(Listing.objects.filter(seller=user).count)() == 0

    # Fresh "socket": no in-memory pending, a new graph object, same checkpointer. Rehydrate
    # cfg from the DB and approve → the SAME parked turn resumes and commits exactly once.
    record = await dal.load_pending_confirm(chat_id)
    graph2 = _one_tool_graph(checkpointer, _tool_by_name(user.id, "create_listing"))
    done = await graph2.ainvoke(Command(resume={"approved": True}), config=record["cfg"])
    assert done["result"]["address"] == "88 Durable Way"
    assert await sync_to_async(Listing.objects.filter(seller=user).count)() == 1

    await dal.clear_pending_confirm(chat_id)
    assert await dal.load_pending_confirm(chat_id) is None

    await close_checkpointer()


# ============ resolved/expired confirm → durable, model-invisible transcript row =====
@pytest.mark.django_db
def test_confirm_outcome_row_is_visible_to_ui_but_skipped_by_the_model(user):
    """A resolved confirm persists as a role='tool' row carrying the card payload + outcome.
    `_load_transcript` SKIPS it (never re-fed to the model), but the REST detail exposes it
    (via `tool_calls`) in timeline order so the FE re-renders a greyed 'Approved' card."""
    from ai.models import AiChat
    from ai.serializers import AiChatDetailSerializer

    chat_id = dal._create_ai_chat(user.id)
    dal._save_ai_message(chat_id, role="user", content="create a listing")
    value = {
        "kind": "confirm_write",
        "action": "create_listing",
        "summary": "Create listing",
        "proposal": {"fields": {"address": "1 A St"}},
    }
    dal._save_confirm_outcome(chat_id, value, "approved")
    dal._save_ai_message(chat_id, role="assistant", content="Done — created it")

    # The model's rehydrated history skips the tool row entirely.
    history = dal._load_transcript(chat_id)
    assert [type(m).__name__ for m in history] == ["HumanMessage", "AIMessage"]

    # The REST detail keeps it, in order, with the structured payload + resolution.
    data = AiChatDetailSerializer(AiChat.objects.get(id=chat_id)).data
    assert [r["role"] for r in data["messages"]] == ["user", "tool", "assistant"]
    tc = next(r for r in data["messages"] if r["role"] == "tool")["tool_calls"]
    assert tc["kind"] == "confirm_write" and tc["action"] == "create_listing"
    assert tc["resolution"] == "approved"


@pytest.mark.django_db
def test_pending_confirm_expires_when_stale(user):
    """A confirm nobody answers auto-expires: a fresh one is untouched; an old one is cleared
    and leaves a durable 'expired' card so it never hangs forever."""
    from datetime import timedelta

    from django.utils import timezone

    from ai.models import AiChat

    chat_id = dal._create_ai_chat(user.id)
    value = {"kind": "confirm_write", "action": "create_listing", "summary": "x", "proposal": {}}

    # Fresh (created now) → NOT expired, pointer stays.
    dal._save_pending_confirm(
        chat_id, {"conv_id": chat_id, "value": value, "created_at": timezone.now().isoformat()}
    )
    assert dal._expire_pending_confirm_if_stale(chat_id, 3600) is False
    assert dal._load_pending_confirm(chat_id) is not None

    # Old (2h ago) with a 1h TTL → expired: pointer cleared + an 'expired' outcome row.
    dal._save_pending_confirm(
        chat_id,
        {
            "conv_id": chat_id,
            "value": value,
            "created_at": (timezone.now() - timedelta(hours=2)).isoformat(),
        },
    )
    assert dal._expire_pending_confirm_if_stale(chat_id, 3600) is True
    assert dal._load_pending_confirm(chat_id) is None
    tool_rows = [m for m in AiChat.objects.get(id=chat_id).messages.all() if m.role == "tool"]
    assert any(m.tool_calls.get("resolution") == "expired" for m in tool_rows)
