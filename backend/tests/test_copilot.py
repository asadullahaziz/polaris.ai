"""
Copilot plumbing (implementation_plan P1.3/P1.4/P1.8) — the DAL round-trips the WS
consumer and tools depend on, without an LLM (fast + deterministic). The live
LLM path (streaming, tool use, comp narration) is exercised separately.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model
from django.utils import timezone
from langchain_core.messages import AIMessage, HumanMessage

from catalog.models import Property
from polaris_agent import dal


@pytest.mark.django_db
def test_conversation_transcript_roundtrip():
    user = get_user_model().objects.create_user(username="seller", password="x")
    conv = dal._create_copilot(user.id)

    assert dal._owns_copilot(user.id, conv) is True
    assert dal._needs_title(conv) is True

    dal._save_message(conv, author_type="human", body="what's it worth?", author_id=user.id)
    dal._save_message(conv, author_type="agent", body="around $500k", author_id=user.id)

    msgs = dal._load_transcript(conv)
    assert [type(m) for m in msgs] == [HumanMessage, AIMessage]
    assert msgs[0].content == "what's it worth?"

    dal._set_title_if_empty(conv, "Pricing chat")
    assert dal._needs_title(conv) is False
    assert conv in [c["id"] for c in dal._list_copilots(user.id)]


@pytest.mark.django_db
def test_memory_is_per_principal_across_chats():
    user = get_user_model().objects.create_user(username="u", password="x")
    assert dal._read_memory(user.id) == []
    dal._write_memory(user.id, "prefers cash buyers")
    # A different copilot chat, same principal → memory is still visible.
    dal._create_copilot(user.id)
    contents = [m["content"] for m in dal._read_memory(user.id)]
    assert "prefers cash buyers" in contents


@pytest.mark.django_db
def test_intake_mandate_and_valuation_roundtrip():
    user = get_user_model().objects.create_user(username="s2", password="x")
    res = dal._create_listing_from_fields(
        user.id,
        {
            "address": "123 Test St",
            "beds": 3,
            "sqft": 2000,
            "condition": "cosmetic",
            "asking_price": 400000,
        },
    )
    listing_id = res["listing_id"]
    assert any(row["listing_id"] == listing_id for row in dal._list_seller_listings(user.id))

    # Mandate set + read back.
    dal._set_mandate_for_listing(listing_id, user.id, {"floor_price": 380000, "autonomy": "assist"})
    m = dal._get_mandate_for_listing(listing_id, user.id)
    assert m["floor_price"] == 380000.0 and m["autonomy"] == "assist"

    # Comps for valuation: a few similar recent sales → a sane range.
    today = timezone.now().date()
    for i in range(6):
        Property.objects.create(
            county_fips="53033",
            address_norm=f"c{i}",
            address_raw=f"comp {i}",
            beds=3,
            sqft=2000,
            condition=3,
            last_sale_price=Decimal(500000 + 5000 * i),
            last_sale_date=today - dt.timedelta(days=20),
        )
    ev = dal._estimate_for_listing(listing_id, user.id, arv=False)
    assert ev["low"] <= ev["point"] <= ev["high"]
    assert ev["basis"]["n_comps"] >= 5

    # Ownership is enforced.
    other = get_user_model().objects.create_user(username="s3", password="x")
    assert "error" in dal._estimate_for_listing(listing_id, other.id, arv=False)
