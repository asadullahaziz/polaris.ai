"""
Thin data-access layer — `polaris_agent` reaches Django models only through here,
wrapping sync ORM calls with `sync_to_async` so they are safe to call from async
graphs/consumers/tools. Keeps the package import-isolated from views and keeps the
ORM boundary in one place (base plan §"the seams").

v2 rewire (P2): copilot chats/memory → `ai.AiChat/AiMessage/AgentMemory`; listings /
property lookup / valuation / mandate → `catalog.services` + `matching.engine` (the
SAME seam the REST API calls, so the agent and API stay in lockstep); buy-box CRUD
(lifted into `catalog.services` in P5, so `/settings › Buy-boxes` REST == the agent) +
ranking/assessment likewise. Every function is **user-scoped** — it takes the acting
principal's id and only ever touches that user's own data.

Convention (v1, kept): a private sync `_fn` does the ORM work; the exported name is
its `sync_to_async` wrapper. Django models + cross-app services are imported lazily
inside each function so this module stays importable outside the app graph.

v2 rewire (P4): `responder_plan` is **principal-centric** over `chat.*` (no role, no
`subject_listing`) — principal = the other `ChatMember`, stance from focal-listing
ownership, context = full transcript + all listing attachments + the principal's in-play
mandates. `responder_assess`/`responder_estimate` give the graph its deterministic deal
math over ANY listing (the focal one is owned by the counterparty on the buy side).

v2 rewire (P5): `launch_outreach`/`approve_campaign` wrap `ai.outreach_service` (the pure
ledger core) so the copilot's confirm-gated outreach tool reaches it through this seam.
The durable `outreach/approved` emit stays in the CALLER (the tool / REST view) — Inngest
never leaks into `dal`.
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


# ---- Parked confirm-every-write turn (durable HITL, survives reload/restart) -----
# The whole resumable pending payload lives on the AiChat row so a fresh socket can
# rehydrate `self._pending` and resume the exact interrupt (same cfg.thread_id).
def _save_pending_confirm(ai_chat_id: int, data: dict) -> None:
    from ai.models import AiChat

    AiChat.objects.filter(id=ai_chat_id).update(pending_confirm=data)


def _load_pending_confirm(ai_chat_id: int) -> dict | None:
    from ai.models import AiChat

    row = AiChat.objects.filter(id=ai_chat_id).values("pending_confirm").first()
    return (row or {}).get("pending_confirm") or None


def _clear_pending_confirm(ai_chat_id: int) -> None:
    from ai.models import AiChat

    AiChat.objects.filter(id=ai_chat_id).update(pending_confirm=None)


# ---- Resolved/expired confirm → a durable, model-invisible transcript artifact ---
def _save_confirm_outcome(ai_chat_id: int, value: dict, resolution: str) -> int:
    """Persist a RESOLVED write-confirm so a reopened chat re-renders a greyed
    'Approved/Declined/Expired' card in the timeline. Stored as role='tool', which
    `_load_transcript` SKIPS — visible in the UI, never re-fed to the model. The structured
    card payload + outcome ride in `tool_calls` (the FE reads it back)."""
    from django.utils import timezone

    from ai.models import AiChat, AiMessage

    label = {"approved": "Approved", "declined": "Declined", "expired": "Expired"}.get(
        resolution, resolution.title()
    )
    summary = (value or {}).get("summary") or (value or {}).get("action") or "write"
    msg = AiMessage.objects.create(
        ai_chat_id=ai_chat_id,
        role="tool",
        content=f"{label}: {summary}",
        tool_calls={**(value or {}), "resolution": resolution},
    )
    AiChat.objects.filter(id=ai_chat_id).update(updated_at=timezone.now())
    return msg.id


def _expire_pending_confirm_if_stale(ai_chat_id: int, ttl_seconds: int) -> bool:
    """If a parked confirm is older than `ttl_seconds`, expire it: leave a durable 'expired'
    card and clear the pending pointer. Returns True if it expired. Lazy — called on reopen /
    next send / resume, so no background job is needed (the orphaned LangGraph checkpoint is
    the deferred Redis/prune concern, not a correctness issue here)."""
    from datetime import datetime
    from datetime import timezone as dt_timezone

    from django.utils import timezone

    from ai.models import AiChat

    row = AiChat.objects.filter(id=ai_chat_id).values("pending_confirm").first()
    pending = (row or {}).get("pending_confirm")
    if not pending:
        return False
    created_raw = pending.get("created_at")
    if not created_raw:
        return False  # legacy record without a stamp — never auto-expire
    try:
        created = datetime.fromisoformat(created_raw)
    except ValueError:
        return False
    if created.tzinfo is None:  # guard a naive stamp
        created = created.replace(tzinfo=dt_timezone.utc)
    if (timezone.now() - created).total_seconds() < ttl_seconds:
        return False
    _save_confirm_outcome(ai_chat_id, pending.get("value") or {}, "expired")
    AiChat.objects.filter(id=ai_chat_id).update(pending_confirm=None)
    return True


create_ai_chat = sync_to_async(_create_ai_chat)
list_ai_chats = sync_to_async(_list_ai_chats)
load_transcript = sync_to_async(_load_transcript)
save_ai_message = sync_to_async(_save_ai_message)
set_title_if_empty = sync_to_async(_set_title_if_empty)
owns_ai_chat = sync_to_async(_owns_ai_chat)
needs_title = sync_to_async(_needs_title)
save_pending_confirm = sync_to_async(_save_pending_confirm)
load_pending_confirm = sync_to_async(_load_pending_confirm)
clear_pending_confirm = sync_to_async(_clear_pending_confirm)
save_confirm_outcome = sync_to_async(_save_confirm_outcome)
expire_pending_confirm_if_stale = sync_to_async(_expire_pending_confirm_if_stale)


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


def _search_properties(q: str, limit: int = 8) -> list[dict]:
    from catalog import services

    return services.search_properties(q, limit)


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
search_properties = sync_to_async(_search_properties)
list_seller_listings = sync_to_async(_list_seller_listings)
get_listing_detail = sync_to_async(_get_listing_detail)
create_listing = sync_to_async(_create_listing)
update_listing = sync_to_async(_update_listing)
estimate_for_listing = sync_to_async(_estimate_for_listing)
comps_for_listing = sync_to_async(_comps_for_listing)
get_mandate_for_listing = sync_to_async(_get_mandate_for_listing)
set_mandate_for_listing = sync_to_async(_set_mandate_for_listing)


# ---- Buy-box CRUD (delegates to catalog.services — the shared API seam, P5) -------
# Lifted into `catalog.services` in P5 so the `/settings › Buy-boxes` REST and these
# copilot tools share ONE seam (agent == API). dal stays the user-scoped async wrapper;
# the copilot's flat `fields` contract (scalars + inline ceiling/must_haves/instructions
# + a single `geo`) is unchanged — it IS the services input shape.
def _list_buy_boxes(user_id: int) -> list[dict]:
    from catalog import services

    return services.list_buy_boxes(user_id)


def _get_buy_box(user_id: int, box_id: int) -> dict:
    from catalog import services

    return services.get_buy_box(user_id, box_id)


def _create_buy_box(user_id: int, fields: dict) -> dict:
    from catalog import services

    return services.create_buy_box(user_id, fields)


def _update_buy_box(user_id: int, box_id: int, fields: dict) -> dict:
    from catalog import services

    return services.update_buy_box(user_id, box_id, fields)


def _delete_buy_box(user_id: int, box_id: int) -> dict:
    from catalog import services

    return services.delete_buy_box(user_id, box_id)


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


def _launch_outreach(seller_id: int, listing_id: int, ai_chat_id=None, limit: int = 10) -> dict:
    """Rank + draft + persist an `awaiting_approval` campaign (the pure ledger core). The
    caller approves + emits `outreach/approved` separately (Inngest stays out of dal)."""
    from ai.outreach_service import launch_outreach

    return launch_outreach(seller_id, listing_id, copilot_ai_chat_id=ai_chat_id, limit=limit)


def _approve_campaign(seller_id: int, campaign_id: int) -> dict:
    """Flip an `awaiting_approval` campaign to `sending` (the batch send gate)."""
    from ai.outreach_service import approve_campaign

    return approve_campaign(seller_id, campaign_id)


rank_buyers = sync_to_async(_rank_buyers)
find_buyers = sync_to_async(_find_buyers)
assess_deal_for_listing = sync_to_async(_assess_deal_for_listing)
launch_outreach = sync_to_async(_launch_outreach)
approve_campaign = sync_to_async(_approve_campaign)


# ---- Away-responder (Graph 2) — principal-centric plan + deal math (P4) ----------
# The away-assistant covers one human's chats while they're away. It reads over the WHOLE
# free-form chat: the full transcript, every listing ever attached, and the principal's
# in-play mandates. No fixed role, no bound listing (revisions §auto-responder).
_DISPOSITION_TO_STRATEGY = {"flip": "fix_flip", "hold": "buy_hold", "brrrr": "brrrr"}


def _mandate_dict(m) -> dict:
    """A mandate row → the PRIVATE dict the responder reasons from (v2 drops
    auto_reply/autonomy — those are user-level on UserProfile now)."""
    return {
        "floor_price": int(m.floor_price) if m.floor_price is not None else None,
        "ceiling_price": int(m.ceiling_price) if m.ceiling_price is not None else None,
        "must_haves": list(m.must_haves or []),
        "availability_window": m.availability_window,
        "instructions": m.instructions or "",
    }


def _listing_public(listing, principal_id: int) -> dict:
    """Public listing facts (visible to BOTH parties) + an ownership flag. `owned_by_
    principal` is what derives stance — the away-assistant defends a listing it owns and
    evaluates one it doesn't."""
    lp = listing.listingproperty_set.select_related("property").order_by("sort_order").first()
    prop = lp.property if lp else None
    ask = listing.asking_price if listing.asking_price is not None else (lp.asking_price if lp else None)
    return {
        "listing_id": listing.id,
        "title": listing.title,
        "address": prop.address_raw if prop else None,
        "beds": prop.beds if prop else None,
        "baths": float(prop.baths) if prop and prop.baths is not None else None,
        "sqft": prop.sqft if prop else None,
        "condition": prop.condition if prop else None,
        "year_built": prop.year_built if prop else None,
        "asking_price": float(ask) if ask is not None else None,
        "owned_by_principal": listing.seller_id == principal_id,
    }


def _chat_listings(chat_id: int, principal_id: int) -> tuple[list[dict], int | None]:
    """Every listing attached anywhere in the chat (dedup'd), plus the FOCAL one (the
    most-recently referenced attachment). Free-form chats accrue many listings over time,
    so the responder tracks the latest-referenced one (revisions decision #4)."""
    from chat.models import MessageAttachment

    rows = (
        MessageAttachment.objects.filter(
            message__chat_id=chat_id, kind="listing", listing__isnull=False
        )
        .select_related("listing")
        .order_by("message__created_at", "sort_order", "id")
    )
    seen: dict[int, dict] = {}
    order: list[int] = []
    focal_id: int | None = None
    for a in rows:  # ascending by attachment time
        lid = a.listing_id
        if lid not in seen:
            seen[lid] = _listing_public(a.listing, principal_id)
            order.append(lid)
        focal_id = lid  # last iteration wins → the newest attachment = focal
    return [seen[lid] for lid in order], focal_id


def _chat_transcript(chat_id: int, principal_id: int) -> list[dict]:
    """The PUBLIC transcript (sent messages only), oldest first, tagged with whether each
    was authored by the principal (drives the You/Counterparty rendering in Stage 2)."""
    from chat.models import Message

    return [
        {
            "id": m["id"],
            "kind": m["kind"],
            "sender": m["sender_id"],
            "body": m["body"],
            "is_principal": m["sender_id"] == principal_id,
        }
        for m in Message.objects.filter(chat_id=chat_id, status="sent")
        .order_by("created_at", "id")
        .values("id", "kind", "sender_id", "body")
    ]


def _primary_active_box(user_id: int):
    from catalog.models import BuyBox

    return (
        BuyBox.objects.filter(buyer_id=user_id, is_active=True)
        .order_by("-is_primary", "id")
        .first()
    )


def _listing_mandate(listing_id: int) -> dict | None:
    from catalog.models import Mandate

    m = Mandate.objects.filter(listing_id=listing_id).first()
    return _mandate_dict(m) if m else None


def _box_mandate_dict(box) -> dict | None:
    if box is None:
        return None
    m = box.mandates.first()
    return _mandate_dict(m) if m else None


def _in_play_mandates(principal_id: int, listings: list[dict]) -> list[dict]:
    """Every private mandate the principal holds that could bear on this chat: floors for
    owned listings referenced here + ceilings/must-haves for their active buy-boxes. The
    UNION of their limits is what the output check scans (revisions §disclosure)."""
    from catalog.models import Mandate

    out: list[dict] = []
    owned_ids = [lst["listing_id"] for lst in listings if lst.get("owned_by_principal")]
    if owned_ids:
        out.extend(_mandate_dict(m) for m in Mandate.objects.filter(listing_id__in=owned_ids))
    out.extend(
        _mandate_dict(m)
        for m in Mandate.objects.filter(
            buy_box__buyer_id=principal_id, buy_box__is_active=True
        )
    )
    return out


def _union_limits(mandates: list[dict]) -> list[int]:
    vals: list[int] = []
    for m in mandates:
        for k in ("floor_price", "ceiling_price"):
            v = m.get(k)
            if v is not None:
                vals.append(int(v))
    return sorted(set(vals))


def _missing_must_haves(focal: dict | None, focal_mandate: dict) -> list[str]:
    """Focal must-haves not evidenced in the focal listing's public facts → Stage 1 may
    ask about them (drives the "listing didn't mention repairs → ask about repairs"
    behavior). Deterministic, must-haves stay whitelisted so asking is always allowed."""
    musts = (focal_mandate or {}).get("must_haves") or []
    if not musts or not focal:
        return []
    hay = " ".join(
        str(v).lower()
        for v in (
            focal.get("title"),
            focal.get("address"),
            focal.get("condition"),
            focal.get("year_built"),
        )
        if v is not None
    )
    return [mh for mh in musts if str(mh).lower() not in hay]


def _dominant_strategy_for_user(user_id: int) -> str | None:
    from collections import Counter

    from catalog.models import Sale

    rows = Sale.objects.filter(buyer_id=user_id).values_list("disposition", flat=True)
    counts = Counter(
        _DISPOSITION_TO_STRATEGY.get(d) for d in rows if _DISPOSITION_TO_STRATEGY.get(d)
    )
    return counts.most_common(1)[0][0] if counts else None


def _responder_plan(chat_id: int, inbound_message_id: int) -> dict:
    """Everything Graph 2 needs to run, or {'skip': reason} when no autonomous reply is
    warranted (auto-reply off, terminal chat, own message, …). Presence + the reply cap
    are re-checked by the handler + the commit gate; this resolves the principal, stance,
    and context."""
    from chat.models import Chat, ChatMember, Message
    from users.models import UserProfile

    chat = Chat.objects.filter(id=chat_id).first()
    if chat is None:
        return {"skip": "chat not found"}
    if chat.status in ("escalated", "closed") or chat.terminal is not None:
        return {"skip": f"chat terminal ({chat.status}/{chat.terminal})"}

    inbound = (
        Message.objects.filter(id=inbound_message_id, chat_id=chat_id)
        .values("id", "kind", "sender_id", "body")
        .first()
    )
    if inbound is None:
        return {"skip": "inbound not found"}
    if inbound["sender_id"] is None:
        return {"skip": "inbound has no sender (system message)"}

    member_ids = list(
        ChatMember.objects.filter(chat_id=chat_id).values_list("user_id", flat=True)
    )
    if len(member_ids) != 2 or inbound["sender_id"] not in member_ids:
        return {"skip": "inbound sender is not one of the two chat members"}

    # Principal = the OTHER member (the away human the agent covers for). The inbound
    # sender is the counterparty this turn — including the agent-inbound of the bounded
    # loop, where the sender is the OTHER side's principal.
    principal_id = next(uid for uid in member_ids if uid != inbound["sender_id"])
    counterparty_user_id = inbound["sender_id"]

    prof = (
        UserProfile.objects.filter(user_id=principal_id)
        .values("auto_reply_when_away", "agent_autonomy", "agent_instructions")
        .first()
        or {}
    )
    if not prof.get("auto_reply_when_away", False):
        return {"skip": "auto_reply_when_away disabled for principal"}

    listings, focal_listing_id = _chat_listings(chat_id, principal_id)
    focal = next((lst for lst in listings if lst["listing_id"] == focal_listing_id), None)

    # Stance from OWNERSHIP of the focal listing (deterministic).
    stance = "neutral"
    strategy: str | None = None
    focal_mandate: dict = {}
    if focal is not None:
        if focal["owned_by_principal"]:
            stance = "sell_side"
            focal_mandate = _listing_mandate(focal_listing_id) or {}
        else:
            box = _primary_active_box(principal_id)
            if box is not None:  # a plausible buyer → buy-side; else stay neutral
                stance = "buy_side"
                focal_mandate = _box_mandate_dict(box) or {}
                strategy = box.strategy or _dominant_strategy_for_user(principal_id)

    mandates = _in_play_mandates(principal_id, listings)
    namespace = {"sell_side": "seller", "buy_side": "buyer"}.get(stance, "general")

    return {
        "principal_id": principal_id,
        "counterparty_user_id": counterparty_user_id,
        "chat_id": chat_id,
        "inbound_message_id": inbound_message_id,
        "inbound": inbound,
        "stance": stance,
        "focal_listing_id": focal_listing_id,
        "focal_listing": focal or {},
        "listings": listings,
        "strategy": strategy,
        "autonomy": prof.get("agent_autonomy") or "draft_for_approval",
        "agent_instructions": prof.get("agent_instructions") or "",
        "mandates": mandates,
        "focal_mandate": focal_mandate,
        "private_limits": _union_limits(mandates),
        "missing_must_haves": _missing_must_haves(focal, focal_mandate),
        "memory": _read_memory(principal_id, namespace=namespace, limit=10),
        "transcript": _chat_transcript(chat_id, principal_id),
        "display_name": _display_name(principal_id),
    }


def _responder_assess(listing_id: int, strategy: str | None) -> dict:
    """Deterministic wholesale verdict for the FOCAL listing (buy-side). No ownership
    check — the focal listing is the counterparty's on the buy side."""
    from matching.engine import assess_deal

    return assess_deal(listing_id, strategy=strategy)


def _responder_estimate(listing_id: int) -> dict:
    """Market value + a few comps for the FOCAL listing (sell-side price defense)."""
    from catalog.models import ListingProperty
    from matching.engine import estimate_value, get_comps

    lp = (
        ListingProperty.objects.filter(listing_id=listing_id)
        .select_related("property")
        .order_by("sort_order")
        .first()
    )
    prop = lp.property if lp else None
    if prop is None:
        return {}
    val = estimate_value(prop, arv=False)
    comps = get_comps(prop)
    return {
        "value": val,
        "n_comps": comps.get("n"),
        "comps": comps.get("comps", [])[:5],
    }


responder_plan = sync_to_async(_responder_plan)
responder_assess = sync_to_async(_responder_assess)
responder_estimate = sync_to_async(_responder_estimate)
