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
def _launch_outreach(seller_id: int, listing_id: int, conversation_id=None, limit: int = 10) -> dict:
    from outreach.service import launch_outreach

    return launch_outreach(seller_id, listing_id, conversation_id=conversation_id, limit=limit)


launch_outreach = sync_to_async(_launch_outreach)


# ---- User display name (for the system prompt) --------------------------------
def _display_name(user_id: int) -> str | None:
    from django.contrib.auth import get_user_model

    u = get_user_model().objects.filter(id=user_id).values("full_name", "username").first()
    if not u:
        return None
    return u["full_name"] or u["username"]


display_name = sync_to_async(_display_name)
