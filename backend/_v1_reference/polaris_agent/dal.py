"""
Thin data-access layer (implementation_plan §2): `polaris_agent` reaches Django
models only through here, wrapping sync ORM calls with `sync_to_async` so they are
safe to call from async graphs/consumers/tools. Keeps the package import-isolated
from views and keeps the ORM boundary in one place.

Conventions: a private sync `_fn` does the ORM work; the exported name is its
`sync_to_async` wrapper. `message` is the system of record for transcripts
(architecture §9b) — graphs rehydrate from here, never from the checkpoint.
"""

from __future__ import annotations

import uuid
from decimal import Decimal, InvalidOperation

from asgiref.sync import sync_to_async
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

# Map an extracted condition label → the KC condition scale (1–5) used by the engine.
_CONDITION_TO_INT = {"full_gut": 1, "cosmetic": 3, "turnkey": 5}


# ---- P0 spike geo query (kept) -------------------------------------------------
def _count_points_within_km(lon: float, lat: float, km: float) -> int:
    from django.contrib.gis.geos import Point
    from django.contrib.gis.measure import D

    from matching.models import SpikePoint

    center = Point(lon, lat, srid=4326)
    return SpikePoint.objects.filter(geom__dwithin=(center, D(km=km))).count()


count_points_within_km = sync_to_async(_count_points_within_km)


# ---- Copilot conversations + transcript (system of record) ---------------------
def _create_copilot(owner_id: int, title: str | None = None) -> int:
    from conversations.models import Conversation

    conv = Conversation.objects.create(kind="copilot", owner_id=owner_id, title=title)
    return conv.id


def _list_copilots(owner_id: int) -> list[dict]:
    from conversations.models import Conversation

    rows = (
        Conversation.objects.filter(kind="copilot", owner_id=owner_id)
        .order_by("-updated_at")
        .values("id", "title", "updated_at")
    )
    return [
        {"id": r["id"], "title": r["title"], "updated_at": r["updated_at"].isoformat()}
        for r in rows
    ]


def _load_transcript(conversation_id: int) -> list:
    """Rehydrate the LangChain message list from the `message` table (architecture §9b)."""
    from conversations.models import Message

    out = []
    for m in (
        Message.objects.filter(conversation_id=conversation_id, status="sent")
        .order_by("created_at")
        .values("author_type", "body")
    ):
        if m["author_type"] == "human":
            out.append(HumanMessage(content=m["body"]))
        elif m["author_type"] == "agent":
            out.append(AIMessage(content=m["body"]))
        else:
            out.append(SystemMessage(content=m["body"]))
    return out


def _save_message(
    conversation_id: int,
    *,
    author_type: str,
    body: str,
    author_side: str | None = None,
    author_id: int | None = None,
    action: str | None = None,
    status: str = "sent",
) -> int:
    from django.utils import timezone

    from conversations.models import Conversation, Message

    msg = Message.objects.create(
        conversation_id=conversation_id,
        author_type=author_type,
        author_side=author_side,
        author_id=author_id,
        action=action,
        body=body,
        status=status,
        sent_at=timezone.now() if status == "sent" else None,
    )
    # Bump the conversation so the sidebar orders most-recent-first.
    Conversation.objects.filter(id=conversation_id).update(updated_at=timezone.now())
    return msg.id


def _set_title_if_empty(conversation_id: int, title: str) -> None:
    from conversations.models import Conversation

    Conversation.objects.filter(id=conversation_id, title__isnull=True).update(title=title[:80])


def _owns_copilot(owner_id: int, conversation_id: int) -> bool:
    from conversations.models import Conversation

    return Conversation.objects.filter(
        id=conversation_id, kind="copilot", owner_id=owner_id
    ).exists()


def _needs_title(conversation_id: int) -> bool:
    from conversations.models import Conversation

    return Conversation.objects.filter(id=conversation_id, title__isnull=True).exists()


create_copilot = sync_to_async(_create_copilot)
list_copilots = sync_to_async(_list_copilots)
load_transcript = sync_to_async(_load_transcript)
save_message = sync_to_async(_save_message)
set_title_if_empty = sync_to_async(_set_title_if_empty)
owns_copilot = sync_to_async(_owns_copilot)
needs_title = sync_to_async(_needs_title)


# ---- Agent memory (per-principal; namespace-scoped + recency-capped, §9b) -------
def _read_memory(principal_id: int, namespace: str = "general", limit: int = 20) -> list[dict]:
    from agent_context.models import AgentMemory

    rows = (
        AgentMemory.objects.filter(principal_id=principal_id, namespace=namespace)
        .order_by("-updated_at")[:limit]
        .values("content", "namespace", "updated_at")
    )
    return [{"content": r["content"], "namespace": r["namespace"]} for r in rows]


def _write_memory(principal_id: int, content: str, namespace: str = "general") -> dict:
    from agent_context.models import AgentMemory

    m = AgentMemory.objects.create(principal_id=principal_id, namespace=namespace, content=content)
    return {"id": m.id, "namespace": namespace, "content": content}


read_memory = sync_to_async(_read_memory)
write_memory = sync_to_async(_write_memory)


# ---- Listings / intake / mandate ----------------------------------------------
def _to_decimal(v):
    if v in (None, ""):
        return None
    try:
        return Decimal(str(v))
    except (InvalidOperation, ValueError):
        return None


def _create_listing_from_fields(seller_id: int, fields: dict) -> dict:
    from catalog.models import Listing, ListingProperty, Property

    address = (fields.get("address") or "").strip() or "Unspecified address"
    condition = fields.get("condition")
    condition_int = _CONDITION_TO_INT.get(condition) if isinstance(condition, str) else condition

    prop = Property.objects.create(
        county_fips="53033",
        address_norm=f"intake:{uuid.uuid4()}",  # intake isn't deduped vs the KC universe
        address_raw=address,
        property_type=fields.get("property_type") or "sfr",
        beds=fields.get("beds"),
        baths=_to_decimal(fields.get("baths")),
        sqft=fields.get("sqft"),
        lot_size_sqft=fields.get("lot_size_sqft"),
        year_built=fields.get("year_built"),
        condition=condition_int,
    )
    asking = _to_decimal(fields.get("asking_price"))
    listing = Listing.objects.create(seller_id=seller_id, asking_price=asking, status="draft")
    ListingProperty.objects.create(listing=listing, property=prop, asking_price=asking)
    return {"listing_id": listing.id, "property_id": prop.id, "address": address}


def _list_seller_listings(seller_id: int) -> list[dict]:
    from catalog.models import Listing

    out = []
    for lst in (
        Listing.objects.filter(seller_id=seller_id)
        .order_by("-created_at")
        .prefetch_related("listingproperty_set__property")
    ):
        lp = lst.listingproperty_set.first()
        prop = lp.property if lp else None
        out.append(
            {
                "listing_id": lst.id,
                "status": lst.status,
                "asking_price": float(lst.asking_price) if lst.asking_price is not None else None,
                "address": prop.address_raw if prop else None,
                "beds": prop.beds if prop else None,
                "sqft": prop.sqft if prop else None,
            }
        )
    return out


def _get_listing_property(listing_id: int, seller_id: int | None = None):
    from catalog.models import ListingProperty

    qs = ListingProperty.objects.filter(listing_id=listing_id).select_related("property", "listing")
    if seller_id is not None:
        qs = qs.filter(listing__seller_id=seller_id)
    lp = qs.first()
    return lp.property if lp else None


def _estimate_for_listing(listing_id: int, seller_id: int | None, arv: bool) -> dict:
    from matching.engine import estimate_value

    prop = _get_listing_property(listing_id, seller_id)
    if prop is None:
        return {"error": f"listing {listing_id} not found or not yours"}
    result = estimate_value(prop, arv=arv)
    result["subject"] = {"address": prop.address_raw, "beds": prop.beds, "sqft": prop.sqft}
    return result


def _comps_for_listing(listing_id: int, seller_id: int | None) -> dict:
    from matching.engine import get_comps

    prop = _get_listing_property(listing_id, seller_id)
    if prop is None:
        return {"error": f"listing {listing_id} not found or not yours"}
    return get_comps(prop)


def _set_mandate_for_listing(listing_id: int, seller_id: int, fields: dict) -> dict:
    from agent_context.models import Mandate
    from catalog.models import Listing

    if not Listing.objects.filter(id=listing_id, seller_id=seller_id).exists():
        return {"error": f"listing {listing_id} not found or not yours"}
    defaults = {
        k: v
        for k, v in {
            "floor_price": _to_decimal(fields.get("floor_price")),
            "instructions": fields.get("instructions"),
            "autonomy": fields.get("autonomy"),
            "auto_reply": fields.get("auto_reply"),
        }.items()
        if v is not None
    }
    mandate, created = Mandate.objects.update_or_create(listing_id=listing_id, defaults=defaults)
    return {
        "mandate_id": mandate.id,
        "created": created,
        "floor_price": float(mandate.floor_price) if mandate.floor_price is not None else None,
        "autonomy": mandate.autonomy,
        "auto_reply": mandate.auto_reply,
        "instructions": mandate.instructions,
    }


def _get_mandate_for_listing(listing_id: int, seller_id: int) -> dict:
    from agent_context.models import Mandate

    m = Mandate.objects.filter(listing_id=listing_id, listing__seller_id=seller_id).first()
    if m is None:
        return {"mandate": None}
    return {
        "floor_price": float(m.floor_price) if m.floor_price is not None else None,
        "autonomy": m.autonomy,
        "auto_reply": m.auto_reply,
        "instructions": m.instructions,
    }


create_listing_from_fields = sync_to_async(_create_listing_from_fields)
list_seller_listings = sync_to_async(_list_seller_listings)
estimate_for_listing = sync_to_async(_estimate_for_listing)
comps_for_listing = sync_to_async(_comps_for_listing)
set_mandate_for_listing = sync_to_async(_set_mandate_for_listing)
get_mandate_for_listing = sync_to_async(_get_mandate_for_listing)


# ---- Outreach (P2) -------------------------------------------------------------
def _launch_outreach(
    seller_id: int, listing_id: int, conversation_id=None, limit: int = 10
) -> dict:
    from outreach.service import launch_outreach

    return launch_outreach(seller_id, listing_id, conversation_id=conversation_id, limit=limit)


launch_outreach = sync_to_async(_launch_outreach)


# ---- Auto-responder (P3, Graph 2) ---------------------------------------------
# The responder is presence-gated + role-configurable. These loaders decide the
# responding SIDE (opposite the inbound author), load that side's PRIVATE mandate +
# strategy + memory, and the PUBLIC transcript — the PUBLIC/PRIVATE split of §8. No LLM.

_DISPOSITION_TO_STRATEGY = {"flip": "fix_flip", "hold": "buy_hold", "brrrr": "brrrr"}


def _thread_messages(conversation_id: int) -> list[dict]:
    """The PUBLIC transcript (sent messages only) as plain dicts, oldest first."""
    from conversations.models import Message

    return [
        dict(m)
        for m in Message.objects.filter(conversation_id=conversation_id, status="sent")
        .order_by("created_at", "id")
        .values("id", "author_type", "author_side", "action", "body")
    ]


def _listing_summary(listing_id: int) -> dict:
    """Public listing facts (visible to both parties) for prompt context."""
    from catalog.models import ListingProperty

    lp = (
        ListingProperty.objects.filter(listing_id=listing_id)
        .select_related("property", "listing")
        .first()
    )
    if lp is None or lp.property is None:
        return {}
    p = lp.property
    ask = lp.listing.asking_price if lp.listing.asking_price is not None else lp.asking_price
    return {
        "address": p.address_raw,
        "beds": p.beds,
        "baths": float(p.baths) if p.baths is not None else None,
        "sqft": p.sqft,
        "condition": p.condition,
        "year_built": p.year_built,
        "asking_price": float(ask) if ask is not None else None,
    }


def _mandate_dict(m) -> dict:
    return {
        "floor_price": int(m.floor_price) if m.floor_price is not None else None,
        "ceiling_price": int(m.ceiling_price) if m.ceiling_price is not None else None,
        "must_haves": list(m.must_haves or []),
        "availability_window": m.availability_window,
        "autonomy": m.autonomy,
        "auto_reply": m.auto_reply,
        "instructions": m.instructions or "",
    }


def _dominant_strategy_for_user(user_id: int) -> str | None:
    from collections import Counter

    from buyers.models import Purchase

    rows = Purchase.objects.filter(buyer_user_id=user_id).values_list("disposition", flat=True)
    counts = Counter(
        _DISPOSITION_TO_STRATEGY.get(d) for d in rows if _DISPOSITION_TO_STRATEGY.get(d)
    )
    return counts.most_common(1)[0][0] if counts else None


def _side_mandate_and_strategy(role: str, listing_id: int, principal_id: int):
    """(mandate_dict|None, strategy|None) for the responding side. Buyer's mandate hangs
    off their primary buy-box; seller's off the listing."""
    from agent_context.models import Mandate
    from buyers.models import BuyBox

    if role == "seller_agent":
        m = Mandate.objects.filter(listing_id=listing_id).first()
        return (_mandate_dict(m) if m else None), None

    box = (
        BuyBox.objects.filter(buyer_id=principal_id, is_active=True).order_by("-is_primary").first()
    )
    mandate = strategy = None
    if box is not None:
        m = box.mandates.first()
        mandate = _mandate_dict(m) if m else None
        strategy = box.strategy
    if strategy is None:
        strategy = _dominant_strategy_for_user(principal_id)
    return mandate, strategy


def _responder_plan(conversation_id: int, inbound_message_id: int) -> dict:
    """Everything Graph 2 needs to run, or {'skip': reason} when no autonomous reply is
    warranted (prospect counterparty, auto_reply off, already-terminal thread, …)."""
    from conversations.models import Conversation, Message

    conv = (
        Conversation.objects.filter(id=conversation_id, kind="thread")
        .select_related("listing")
        .first()
    )
    if conv is None:
        return {"skip": "not a thread"}
    if conv.status in ("escalated", "closed"):
        return {"skip": f"conversation {conv.status}"}

    inbound = (
        Message.objects.filter(id=inbound_message_id, conversation_id=conversation_id)
        .values("id", "author_type", "author_side", "body")
        .first()
    )
    if inbound is None:
        return {"skip": "inbound not found"}

    listing_id = conv.listing_id
    seller_id = conv.listing.seller_id if conv.listing_id else None

    # Responder side is the OPPOSITE of the inbound author's side.
    if inbound["author_side"] == "seller":
        role, side, principal_id = "buyer_agent", "buyer", conv.counterparty_user_id
        counterparty_user_id = seller_id
        if principal_id is None:
            return {"skip": "buyer-side counterparty is a prospect (no agent)"}
    elif inbound["author_side"] == "buyer":
        role, side, principal_id = "seller_agent", "seller", seller_id
        counterparty_user_id = conv.counterparty_user_id
        if principal_id is None:
            return {"skip": "no seller principal"}
    else:
        return {"skip": f"inbound has no side ({inbound['author_side']!r})"}

    # Never answer our own side's message (e.g. our own outreach opener).
    if inbound["author_type"] == "agent" and inbound["author_side"] == side:
        return {"skip": "inbound authored by this side"}

    mandate, strategy = _side_mandate_and_strategy(role, listing_id, principal_id)
    if mandate is None:
        return {"skip": "no mandate for this side"}
    if not mandate.get("auto_reply", False):
        return {"skip": "auto_reply disabled"}

    return {
        "role": role,
        "side": side,
        "principal_id": principal_id,
        "counterparty_user_id": counterparty_user_id,
        "counterparty_kind": "user" if conv.counterparty_user_id else "prospect",
        "counterparty_id": conv.counterparty_user_id or conv.counterparty_prospect_id,
        "listing_id": listing_id,
        "conversation_id": conversation_id,
        "inbound_message_id": inbound_message_id,
        "inbound": inbound,
        "strategy": strategy,
        "mandate": mandate,
        "memory": _read_memory(principal_id, namespace=side, limit=10),
        "thread_messages": _thread_messages(conversation_id),
        "listing": _listing_summary(listing_id),
    }


def _assess_deal(listing_id: int, strategy: str | None) -> dict:
    from matching.engine import assess_deal

    return assess_deal(listing_id, strategy=strategy)


def _thread_participant(conversation_id: int, user_id: int) -> dict | None:
    """Who is `user_id` in this thread? {side, counterparty_user_id, ...} or None if not a
    participant. Side is derived (no roles table): listing seller → seller; the
    counterparty_user → buyer."""
    from conversations.models import Conversation

    conv = (
        Conversation.objects.filter(id=conversation_id, kind="thread")
        .select_related("listing")
        .first()
    )
    if conv is None:
        return None
    seller_id = conv.listing.seller_id if conv.listing_id else None
    if seller_id is not None and user_id == seller_id:
        return {
            "side": "seller",
            "seller_id": seller_id,
            "counterparty_user_id": conv.counterparty_user_id,
            "listing_id": conv.listing_id,
        }
    if conv.counterparty_user_id is not None and user_id == conv.counterparty_user_id:
        return {
            "side": "buyer",
            "seller_id": seller_id,
            "counterparty_user_id": seller_id,
            "listing_id": conv.listing_id,
        }
    return None


def _save_thread_message(
    conversation_id: int, user_id: int, side: str, body: str, client_dedup_uuid: str | None = None
) -> dict:
    """Persist a HUMAN message into a shared thread (system of record). `client_dedup_uuid`
    dedupes a double-tap/retry via the message dedup_key."""
    from django.db import IntegrityError
    from django.utils import timezone

    from conversations.models import Conversation, Message

    key = f"human:{conversation_id}:{client_dedup_uuid}" if client_dedup_uuid else None
    try:
        msg = Message.objects.create(
            conversation_id=conversation_id,
            author_type="human",
            author_side=side,
            author_id=user_id,
            body=body,
            status="sent",
            sent_at=timezone.now(),
            dedup_key=key,
        )
    except IntegrityError:
        existing = (
            Message.objects.filter(dedup_key=key)
            .values("id", "author_type", "author_side", "body")
            .first()
        )
        return {"duplicate": True, **(existing or {})}
    Conversation.objects.filter(id=conversation_id).update(updated_at=timezone.now())
    return {"id": msg.id, "author_type": "human", "author_side": side, "body": body}


def _thread_side_mandate(conversation_id: int, user_id: int):
    """(mandate_obj|None, side|None). The current user's side mandate — seller's off the
    listing, buyer's off their primary buy-box."""
    from agent_context.models import Mandate
    from buyers.models import BuyBox

    part = _thread_participant(conversation_id, user_id)
    if part is None:
        return None, None
    if part["side"] == "seller":
        return Mandate.objects.filter(listing_id=part["listing_id"]).first(), "seller"
    box = BuyBox.objects.filter(buyer_id=user_id, is_active=True).order_by("-is_primary").first()
    return (box.mandates.first() if box else None), "buyer"


def _get_thread_mandate(conversation_id: int, user_id: int) -> dict:
    m, side = _thread_side_mandate(conversation_id, user_id)
    if side is None:
        return {"error": "not a participant"}
    if m is None:
        return {"side": side, "has_mandate": False}
    return {
        "side": side,
        "has_mandate": True,
        "auto_reply": m.auto_reply,
        "autonomy": m.autonomy,
        "instructions": m.instructions,
    }


def _set_thread_mandate(conversation_id: int, user_id: int, fields: dict) -> dict:
    """The auto-reply toggle + autonomy/instructions edit (P3.9), for either side."""
    m, side = _thread_side_mandate(conversation_id, user_id)
    if side is None:
        return {"error": "not a participant"}
    if m is None:
        return {"error": "no mandate on this side to update"}
    changed = []
    for k in ("auto_reply", "autonomy", "instructions"):
        if fields.get(k) is not None:
            setattr(m, k, fields[k])
            changed.append(k)
    if changed:
        m.save(update_fields=changed + ["updated_at"])
    return _get_thread_mandate(conversation_id, user_id)


thread_messages = sync_to_async(_thread_messages)
responder_plan = sync_to_async(_responder_plan)
assess_deal = sync_to_async(_assess_deal)
thread_participant = sync_to_async(_thread_participant)
save_thread_message = sync_to_async(_save_thread_message)


# ---- User display name (for the system prompt) --------------------------------
def _display_name(user_id: int) -> str | None:
    from django.contrib.auth import get_user_model

    u = get_user_model().objects.filter(id=user_id).values("full_name", "username").first()
    if not u:
        return None
    return u["full_name"] or u["username"]


display_name = sync_to_async(_display_name)
