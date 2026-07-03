"""
Thin data-access layer — `polaris_agent` reaches Django models only through here,
wrapping sync ORM calls with `sync_to_async` so they are safe to call from async
graphs/consumers/tools. Keeps the package import-isolated from views and keeps the
ORM boundary in one place (base plan §"the seams").

v2 rewire (P2): copilot chats/memory → `ai.AiChat/AiMessage/AgentMemory`; listings /
property lookup / valuation / mandate → `catalog.services` + `matching.engine` (the
SAME seam the REST API calls, so the agent and API stay in lockstep); buy-box CRUD +
ranking/assessment likewise. Every function is **user-scoped** — it takes the acting
principal's id and only ever touches that user's own data.

Convention (v1, kept): a private sync `_fn` does the ORM work; the exported name is
its `sync_to_async` wrapper. Django models + cross-app services are imported lazily
inside each function so this module stays importable outside the app graph.

Deferred to later phases (kept out of P2 so the seam is honest):
  * responder_plan / thread context  → P4 (the away-responder over `chat.*`)
  * launch_outreach                   → P5 (the outreach ledger + fan-out)
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation

from asgiref.sync import sync_to_async
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

# Map an extracted condition label → the KC condition scale (1–5) the engine uses.
_CONDITION_TO_INT = {"full_gut": 1, "cosmetic": 3, "turnkey": 5}


def _to_decimal(v):
    if v in (None, ""):
        return None
    try:
        return Decimal(str(v))
    except (InvalidOperation, ValueError):
        return None


def _condition_to_int(v):
    """Accept either an int KC condition (1–5) or a label (full_gut/cosmetic/turnkey)."""
    if isinstance(v, str):
        return _CONDITION_TO_INT.get(v)
    return v


# ---- Copilot chats + transcript (system of record, architecture §9b) -----------
def _create_ai_chat(owner_id: int, title: str | None = None) -> int:
    from ai.models import AiChat

    return AiChat.objects.create(owner_id=owner_id, title=title).id


def _list_ai_chats(owner_id: int) -> list[dict]:
    from ai.models import AiChat

    rows = (
        AiChat.objects.filter(owner_id=owner_id)
        .order_by("-updated_at")
        .values("id", "title", "status", "updated_at")
    )
    return [
        {
            "id": r["id"],
            "title": r["title"],
            "status": r["status"],
            "updated_at": r["updated_at"].isoformat(),
        }
        for r in rows
    ]


def _load_transcript(ai_chat_id: int) -> list:
    """Rehydrate the LangChain message list from `ai_message` (architecture §9b)."""
    from ai.models import AiMessage

    out = []
    for m in (
        AiMessage.objects.filter(ai_chat_id=ai_chat_id)
        .order_by("created_at")
        .values("role", "content")
    ):
        if m["role"] == "user":
            out.append(HumanMessage(content=m["content"]))
        elif m["role"] == "assistant":
            out.append(AIMessage(content=m["content"]))
        elif m["role"] == "system":
            out.append(SystemMessage(content=m["content"]))
        # 'tool' rows are reserved (not persisted in P2) — skip if present.
    return out


def _save_ai_message(ai_chat_id: int, *, role: str, content: str) -> int:
    from django.utils import timezone

    from ai.models import AiChat, AiMessage

    msg = AiMessage.objects.create(ai_chat_id=ai_chat_id, role=role, content=content)
    # Bump the chat so the sidebar orders most-recent-first.
    AiChat.objects.filter(id=ai_chat_id).update(updated_at=timezone.now())
    return msg.id


def _set_title_if_empty(ai_chat_id: int, title: str) -> None:
    from ai.models import AiChat

    AiChat.objects.filter(id=ai_chat_id, title__isnull=True).update(title=title[:80])


def _owns_ai_chat(owner_id: int, ai_chat_id: int) -> bool:
    from ai.models import AiChat

    return AiChat.objects.filter(id=ai_chat_id, owner_id=owner_id).exists()


def _needs_title(ai_chat_id: int) -> bool:
    from ai.models import AiChat

    return AiChat.objects.filter(id=ai_chat_id, title__isnull=True).exists()


create_ai_chat = sync_to_async(_create_ai_chat)
list_ai_chats = sync_to_async(_list_ai_chats)
load_transcript = sync_to_async(_load_transcript)
save_ai_message = sync_to_async(_save_ai_message)
set_title_if_empty = sync_to_async(_set_title_if_empty)
owns_ai_chat = sync_to_async(_owns_ai_chat)
needs_title = sync_to_async(_needs_title)


# ---- Agent memory (per-principal; namespace-scoped + recency-capped, §9b) -------
def _read_memory(principal_id: int, namespace: str = "general", limit: int = 20) -> list[dict]:
    from ai.models import AgentMemory

    rows = (
        AgentMemory.objects.filter(principal_id=principal_id, namespace=namespace)
        .order_by("-updated_at")[:limit]
        .values("content", "namespace")
    )
    return [{"content": r["content"], "namespace": r["namespace"]} for r in rows]


def _write_memory(principal_id: int, content: str, namespace: str = "general") -> dict:
    from ai.models import AgentMemory

    m = AgentMemory.objects.create(principal_id=principal_id, namespace=namespace, content=content)
    return {"id": m.id, "namespace": namespace, "content": content}


read_memory = sync_to_async(_read_memory)
write_memory = sync_to_async(_write_memory)


# ---- User display name + global agent instructions (for the system prompt) ------
def _display_name(user_id: int) -> str | None:
    from django.contrib.auth import get_user_model

    u = get_user_model().objects.filter(id=user_id).values("full_name", "email").first()
    if not u:
        return None
    return u["full_name"] or u["email"]


def _agent_instructions(user_id: int) -> str:
    """The user's global `UserProfile.agent_instructions` (blank if unset), injected
    into the copilot system prompt (revisions §settings)."""
    from users.models import UserProfile

    row = UserProfile.objects.filter(user_id=user_id).values("agent_instructions").first()
    return (row or {}).get("agent_instructions") or ""


display_name = sync_to_async(_display_name)
agent_instructions = sync_to_async(_agent_instructions)


# ---- Listings / property lookup / valuation / mandate --------------------------
# All routed through catalog.services (the REST API's seam) so agent == API.
def _property_lookup(address: str) -> dict:
    from catalog import services

    return services.lookup_property(address)


def _listing_summary_row(lst) -> dict:
    lp = lst.listingproperty_set.select_related("property").order_by("sort_order").first()
    prop = lp.property if lp else None
    return {
        "listing_id": lst.id,
        "title": lst.title,
        "status": lst.status,
        "bundle_type": lst.bundle_type,
        "asking_price": float(lst.asking_price) if lst.asking_price is not None else None,
        "n_properties": lst.listingproperty_set.count(),
        "address": prop.address_raw if prop else None,
        "beds": prop.beds if prop else None,
        "sqft": prop.sqft if prop else None,
    }


def _list_seller_listings(seller_id: int) -> list[dict]:
    from catalog.models import Listing

    return [
        _listing_summary_row(lst)
        for lst in (
            Listing.objects.filter(seller_id=seller_id)
            .order_by("-created_at")
            .prefetch_related("listingproperty_set__property")
        )
    ]


def _get_listing_detail(listing_id: int, seller_id: int) -> dict:
    from catalog import services
    from catalog.models import Listing

    lst = (
        Listing.objects.filter(id=listing_id, seller_id=seller_id)
        .prefetch_related("listingproperty_set__property")
        .first()
    )
    if lst is None:
        return {"error": f"listing {listing_id} not found or not yours"}
    props = []
    for lp in lst.listingproperty_set.select_related("property").order_by("sort_order"):
        p = lp.property
        props.append(
            {
                "property_id": p.id,
                "address": p.address_raw,
                "beds": p.beds,
                "baths": float(p.baths) if p.baths is not None else None,
                "sqft": p.sqft,
                "condition": p.condition,
                "year_built": p.year_built,
                "asking_price": float(lp.asking_price) if lp.asking_price is not None else None,
            }
        )
    return {
        "listing_id": lst.id,
        "title": lst.title,
        "description": lst.description,
        "status": lst.status,
        "bundle_type": lst.bundle_type,
        "asking_price": float(lst.asking_price) if lst.asking_price is not None else None,
        "properties": props,
        "mandate": services.get_mandate_for_listing(lst),
    }


def _create_listing(seller_id: int, fields: dict) -> dict:
    """Create a draft listing for the user. Single property from address + attrs
    (fetch-existing dedup applies — an address that matches a seeded/known Property
    attaches it read-only, protecting the comp basis)."""
    from django.contrib.auth import get_user_model

    from catalog import services

    user = get_user_model().objects.get(id=seller_id)
    prop_item = {
        "address": (fields.get("address") or "").strip() or "Unspecified address",
        "property_type": fields.get("property_type") or "sfr",
        "beds": fields.get("beds"),
        "baths": _to_decimal(fields.get("baths")),
        "sqft": fields.get("sqft"),
        "lot_size_sqft": fields.get("lot_size_sqft"),
        "year_built": fields.get("year_built"),
        "condition": _condition_to_int(fields.get("condition")),
        "asking_price": _to_decimal(fields.get("asking_price")),
    }
    data = {
        "title": fields.get("title") or "",
        "description": fields.get("description") or "",
        "asking_price": _to_decimal(fields.get("asking_price")),
        "bundle_type": "single",
        "status": "draft",
        "properties": [prop_item],
    }
    listing = services.create_listing(user, data)
    detail = (
        listing.__class__.objects.filter(id=listing.id)
        .prefetch_related("listingproperty_set__property")
        .first()
    )
    return _listing_summary_row(detail)


def _update_listing(listing_id: int, seller_id: int, fields: dict) -> dict:
    from catalog import services
    from catalog.models import Listing

    listing = Listing.objects.filter(id=listing_id, seller_id=seller_id).first()
    if listing is None:
        return {"error": f"listing {listing_id} not found or not yours"}
    data = {
        k: fields[k]
        for k in ("title", "description", "status", "bundle_type")
        if fields.get(k) is not None
    }
    if fields.get("asking_price") is not None:
        data["asking_price"] = _to_decimal(fields.get("asking_price"))
    services.update_listing(listing, data)
    return _get_listing_detail(listing_id, seller_id)


def _get_listing_first_property(listing_id: int, seller_id: int):
    from catalog.models import ListingProperty

    lp = (
        ListingProperty.objects.filter(listing_id=listing_id, listing__seller_id=seller_id)
        .select_related("property")
        .order_by("sort_order")
        .first()
    )
    return lp.property if lp else None


def _estimate_for_listing(listing_id: int, seller_id: int, arv: bool) -> dict:
    from matching.engine import estimate_value

    prop = _get_listing_first_property(listing_id, seller_id)
    if prop is None:
        return {"error": f"listing {listing_id} not found or not yours"}
    result = estimate_value(prop, arv=arv)
    result["subject"] = {"address": prop.address_raw, "beds": prop.beds, "sqft": prop.sqft}
    return result


def _comps_for_listing(listing_id: int, seller_id: int) -> dict:
    from matching.engine import get_comps

    prop = _get_listing_first_property(listing_id, seller_id)
    if prop is None:
        return {"error": f"listing {listing_id} not found or not yours"}
    return get_comps(prop)


def _get_mandate_for_listing(listing_id: int, seller_id: int) -> dict:
    from catalog import services
    from catalog.models import Listing

    listing = Listing.objects.filter(id=listing_id, seller_id=seller_id).first()
    if listing is None:
        return {"error": f"listing {listing_id} not found or not yours"}
    return services.get_mandate_for_listing(listing)


def _set_mandate_for_listing(listing_id: int, seller_id: int, fields: dict) -> dict:
    from catalog import services
    from catalog.models import Listing

    listing = Listing.objects.filter(id=listing_id, seller_id=seller_id).first()
    if listing is None:
        return {"error": f"listing {listing_id} not found or not yours"}
    data = {}
    if fields.get("floor_price") is not None:
        data["floor_price"] = _to_decimal(fields["floor_price"])
    for k in ("must_haves", "availability_window", "instructions"):
        if fields.get(k) is not None:
            data[k] = fields[k]
    return services.set_mandate_for_listing(listing, data)


property_lookup = sync_to_async(_property_lookup)
list_seller_listings = sync_to_async(_list_seller_listings)
get_listing_detail = sync_to_async(_get_listing_detail)
create_listing = sync_to_async(_create_listing)
update_listing = sync_to_async(_update_listing)
estimate_for_listing = sync_to_async(_estimate_for_listing)
comps_for_listing = sync_to_async(_comps_for_listing)
get_mandate_for_listing = sync_to_async(_get_mandate_for_listing)
set_mandate_for_listing = sync_to_async(_set_mandate_for_listing)


# ---- Buy-box CRUD (the buyer-side deal config; deal-settings inline) ------------
_BOX_SCALAR_FIELDS = (
    "name",
    "strategy",
    "is_primary",
    "is_active",
    "price_min",
    "price_max",
    "arv_min",
    "arv_max",
    "beds_min",
    "sqft_min",
    "sqft_max",
    "year_built_min",
    "max_rehab_cost",
    "property_types",
)
_BOX_DECIMAL_FIELDS = {"price_min", "price_max", "arv_min", "arv_max", "max_rehab_cost"}


def _box_public(box) -> dict:
    m = box.mandates.first()
    return {
        "buy_box_id": box.id,
        "name": box.name,
        "strategy": box.strategy,
        "is_primary": box.is_primary,
        "is_active": box.is_active,
        "price_min": float(box.price_min) if box.price_min is not None else None,
        "price_max": float(box.price_max) if box.price_max is not None else None,
        "beds_min": box.beds_min,
        "sqft_min": box.sqft_min,
        "property_types": list(box.property_types or []),
        "n_geos": box.geos.count(),
        "mandate": (
            None
            if m is None
            else {
                "ceiling_price": float(m.ceiling_price) if m.ceiling_price is not None else None,
                "must_haves": list(m.must_haves or []),
                "instructions": m.instructions,
            }
        ),
    }


def _list_buy_boxes(user_id: int) -> list[dict]:
    from catalog.models import BuyBox

    return [
        _box_public(b)
        for b in BuyBox.objects.filter(buyer_id=user_id)
        .order_by("-is_primary", "name")
        .prefetch_related("geos", "mandates")
    ]


def _get_buy_box(user_id: int, box_id: int) -> dict:
    from catalog.models import BuyBox

    box = BuyBox.objects.filter(id=box_id, buyer_id=user_id).first()
    if box is None:
        return {"error": f"buy-box {box_id} not found or not yours"}
    return _box_public(box)


def _apply_box_scalars(box, fields: dict) -> None:
    for k in _BOX_SCALAR_FIELDS:
        if fields.get(k) is not None:
            setattr(box, k, _to_decimal(fields[k]) if k in _BOX_DECIMAL_FIELDS else fields[k])


def _apply_box_geo(box, geo: dict) -> None:
    """Create ONE BuyBoxGeo from a simple spec (named place or radius). Best-effort —
    the ranker's candidate pool uses radius geos + nearby sales, so radius matters most."""
    from django.contrib.gis.geos import Point

    from catalog.models import BuyBoxGeo

    geo_type = geo.get("geo_type")
    kwargs = {"buy_box": box, "geo_type": geo_type, "mode": geo.get("mode", "include")}
    if geo_type == "radius":
        lat, lon = geo.get("center_lat"), geo.get("center_lon")
        if lat is not None and lon is not None:
            kwargs["center"] = Point(float(lon), float(lat), srid=4326)
        kwargs["radius_mi"] = _to_decimal(geo.get("radius_mi"))
    else:
        for k in ("state_code", "county_fips", "city", "zip"):
            if geo.get(k) is not None:
                kwargs[k] = geo[k]
    BuyBoxGeo.objects.create(**kwargs)


def _upsert_box_mandate(box, fields: dict) -> None:
    from catalog.models import Mandate

    data = {}
    if fields.get("ceiling_price") is not None:
        data["ceiling_price"] = _to_decimal(fields["ceiling_price"])
    for k in ("must_haves", "instructions"):
        if fields.get(k) is not None:
            data[k] = fields[k]
    if data:
        Mandate.objects.update_or_create(buy_box=box, defaults=data)


def _create_buy_box(user_id: int, fields: dict) -> dict:
    from django.db import transaction

    from catalog.models import BuyBox

    with transaction.atomic():
        box = BuyBox(
            buyer_id=user_id,
            name=fields.get("name") or "My buy-box",
            strategy=fields.get("strategy") or "fix_flip",
        )
        _apply_box_scalars(box, fields)
        box.save()
        if fields.get("geo"):
            _apply_box_geo(box, fields["geo"])
        _upsert_box_mandate(box, fields)
    return _get_buy_box(user_id, box.id)


def _update_buy_box(user_id: int, box_id: int, fields: dict) -> dict:
    from django.db import transaction

    from catalog.models import BuyBox

    box = BuyBox.objects.filter(id=box_id, buyer_id=user_id).first()
    if box is None:
        return {"error": f"buy-box {box_id} not found or not yours"}
    with transaction.atomic():
        _apply_box_scalars(box, fields)
        box.save()
        if fields.get("geo"):
            _apply_box_geo(box, fields["geo"])
        _upsert_box_mandate(box, fields)
    return _get_buy_box(user_id, box_id)


def _delete_buy_box(user_id: int, box_id: int) -> dict:
    from catalog.models import BuyBox

    box = BuyBox.objects.filter(id=box_id, buyer_id=user_id).first()
    if box is None:
        return {"error": f"buy-box {box_id} not found or not yours"}
    box.delete()  # cascades geos + its mandate
    return {"deleted": True, "buy_box_id": box_id}


list_buy_boxes = sync_to_async(_list_buy_boxes)
get_buy_box = sync_to_async(_get_buy_box)
create_buy_box = sync_to_async(_create_buy_box)
update_buy_box = sync_to_async(_update_buy_box)
delete_buy_box = sync_to_async(_delete_buy_box)


# ---- Buyer ranking + deal assessment (deterministic engine) --------------------
def _rank_buyers(listing_id: int, seller_id: int, limit: int = 10) -> dict:
    from catalog.models import Listing
    from matching.engine import rank_buyers

    if not Listing.objects.filter(id=listing_id, seller_id=seller_id).exists():
        return {"error": f"listing {listing_id} not found or not yours", "ranked": []}
    return rank_buyers(listing_id, limit=limit)


def _find_buyers(seller_id: int, address: str, price=None, **attrs) -> dict:
    """Ad-hoc buyer ranking (the `/buyers` matcher) — no persisted listing. Resolves
    the address to a point via the known Property universe (no geocoder)."""
    from catalog import services
    from matching.engine import rank_buyers_for_attrs

    geom = services.resolve_geo(address)
    return rank_buyers_for_attrs(
        geom=geom,
        price=float(price) if price is not None else None,
        condition=_condition_to_int(attrs.get("condition")),
        beds=attrs.get("beds"),
        sqft=attrs.get("sqft"),
        property_type=attrs.get("property_type"),
        seller_id=seller_id,
        limit=attrs.get("limit", 10),
    )


def _assess_deal_for_listing(listing_id: int, seller_id: int, strategy: str | None) -> dict:
    from catalog.models import Listing
    from matching.engine import assess_deal

    if not Listing.objects.filter(id=listing_id, seller_id=seller_id).exists():
        return {"error": f"listing {listing_id} not found or not yours", "verdict": "hold"}
    return assess_deal(listing_id, strategy=strategy)


rank_buyers = sync_to_async(_rank_buyers)
find_buyers = sync_to_async(_find_buyers)
assess_deal_for_listing = sync_to_async(_assess_deal_for_listing)
