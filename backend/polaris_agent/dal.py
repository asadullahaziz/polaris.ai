"""Data-access layer: the only path from polaris_agent to the Django ORM.

A private sync `_fn` does the ORM work; the exported name is its `sync_to_async`
wrapper, safe to call from async graphs/consumers/tools. Django models and
cross-app services are imported lazily inside each function so the package stays
importable outside Django. Every function is user-scoped: it takes the acting
user's id and only touches that user's data.

Reads and writes go through the same service seams as the REST API
(`catalog.services`, `matching.engine`, `ai.outreach_service`, `chat.services`),
so the agent and the API stay in lockstep. The durable `outreach/approved` emit
stays in the caller (the tool / REST view) — Inngest never leaks into `dal`.
"""

from __future__ import annotations

import logging
from datetime import UTC
from decimal import Decimal, InvalidOperation

from asgiref.sync import sync_to_async
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

log = logging.getLogger(__name__)

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


# ---- Copilot chats + transcript (the DB is the system of record) ---------------
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


def _text_content(content) -> str:
    """Coerce a LangChain message `content` (str or list-of-parts) to plain text."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for p in content:
            if isinstance(p, str):
                parts.append(p)
            elif isinstance(p, dict) and p.get("type") == "text":
                parts.append(p.get("text") or "")
        return "".join(parts)
    return str(content or "")


def _repair_tool_pairing(msgs: list) -> list:
    """Drop broken tool pairs defensively: an assistant `tool_calls` entry without its
    matching tool result (or an orphan tool result) is an API 400 on the next turn.
    Keeps only fully-paired calls, in call order; a call-only assistant message with no
    text left disappears entirely."""
    out: list = []
    i = 0
    while i < len(msgs):
        m = msgs[i]
        if isinstance(m, AIMessage) and m.tool_calls:
            j = i + 1
            results: dict[str, ToolMessage] = {}
            while j < len(msgs) and isinstance(msgs[j], ToolMessage):
                results[msgs[j].tool_call_id] = msgs[j]
                j += 1
            kept = [c for c in m.tool_calls if c.get("id") in results]
            if kept:
                out.append(AIMessage(content=m.content, tool_calls=kept))
                out.extend(results[c["id"]] for c in kept)
            elif _text_content(m.content):
                out.append(AIMessage(content=m.content))
            i = j
        elif isinstance(m, ToolMessage):
            i += 1  # orphan result (its call was lost) — never re-feed it
        else:
            out.append(m)
            i += 1
    return out


def _load_transcript(ai_chat_id: int) -> list:
    """Rehydrate the LangChain message list from `ai_message` — the DB, not the
    checkpoint, is the transcript's system of record.

    Block-structured: assistant rows carry their `tool_calls` and role='tool' rows
    the results, so the model remembers what tools returned in past turns (ids,
    figures, rankings) instead of only its own prose. Context policy (settings):
    the last COPILOT_FULL_FIDELITY_TURNS user turns rehydrate at full fidelity;
    older turns collapse to text-only. Oversized tool results truncate.
    Confirm-card rows (kind='confirm_write') stay UI-only — never re-fed."""
    from django.conf import settings

    from ai.models import AiMessage

    rows = list(
        AiMessage.objects.filter(ai_chat_id=ai_chat_id)
        .order_by("created_at", "id")
        .values("role", "content", "tool_calls")
    )
    user_idxs = [i for i, r in enumerate(rows) if r["role"] == "user"]
    n_full = settings.COPILOT_FULL_FIDELITY_TURNS
    full_from = user_idxs[-n_full] if len(user_idxs) > n_full else 0
    max_chars = settings.COPILOT_TOOL_RESULT_MAX_CHARS

    out = []
    for i, m in enumerate(rows):
        role, tc = m["role"], m["tool_calls"]
        if role == "user":
            out.append(HumanMessage(content=m["content"]))
        elif role == "system":
            out.append(SystemMessage(content=m["content"]))
        elif role == "assistant":
            calls = tc if isinstance(tc, list) else []
            if i >= full_from and calls:
                out.append(
                    AIMessage(
                        content=m["content"],
                        tool_calls=[
                            {
                                "id": c.get("id") or "",
                                "name": c.get("name") or "",
                                "args": c.get("args") or {},
                                "type": "tool_call",
                            }
                            for c in calls
                        ],
                    )
                )
            elif m["content"]:
                out.append(AIMessage(content=m["content"]))
        elif role == "tool":
            if not isinstance(tc, dict) or tc.get("kind") != "tool_result":
                continue  # confirm cards + legacy rows are UI-only
            if i < full_from:
                continue  # collapsed window: tool traffic dropped
            body = m["content"]
            if len(body) > max_chars:
                body = body[:max_chars] + "\n…[truncated]"
            out.append(
                ToolMessage(
                    content=body,
                    tool_call_id=tc.get("tool_call_id") or "",
                    name=tc.get("name") or None,
                )
            )
    return _repair_tool_pairing(out)


def _save_ai_message(ai_chat_id: int, *, role: str, content: str) -> int:
    from django.utils import timezone

    from ai.models import AiChat, AiMessage

    msg = AiMessage.objects.create(ai_chat_id=ai_chat_id, role=role, content=content)
    # Bump the chat so the sidebar orders most-recent-first.
    AiChat.objects.filter(id=ai_chat_id).update(updated_at=timezone.now())
    return msg.id


def _save_turn_blocks(ai_chat_id: int, messages: list) -> int | None:
    """Persist one completed copilot turn as block rows: each assistant LLM call is
    its own row (its `tool_calls` in the JSON column), each tool result a role='tool'
    row (`{kind: 'tool_result', tool_call_id, name, label}` + the result as content).
    Atomic — a partially-persisted turn would rehydrate as a broken tool pair.
    Returns the id of the last assistant row that carries text (what `copilot.done`
    reports), or None if the turn produced no assistant text."""
    from django.db import transaction
    from django.utils import timezone

    from ai.models import AiChat, AiMessage
    from polaris_agent.tools.labels import tool_label

    last_text_id: int | None = None
    with transaction.atomic():
        for m in messages:
            if isinstance(m, AIMessage):
                text = _text_content(m.content)
                calls = [
                    {
                        "id": c.get("id") or "",
                        "name": c.get("name") or "",
                        "args": c.get("args") or {},
                    }
                    for c in (m.tool_calls or [])
                ]
                if not text and not calls:
                    continue
                row = AiMessage.objects.create(
                    ai_chat_id=ai_chat_id, role="assistant", content=text, tool_calls=calls
                )
                if text:
                    last_text_id = row.id
            elif isinstance(m, ToolMessage):
                AiMessage.objects.create(
                    ai_chat_id=ai_chat_id,
                    role="tool",
                    content=_text_content(m.content),
                    tool_calls={
                        "kind": "tool_result",
                        "tool_call_id": m.tool_call_id or "",
                        "name": m.name or "",
                        "label": tool_label(m.name),
                    },
                )
        AiChat.objects.filter(id=ai_chat_id).update(updated_at=timezone.now())
    return last_text_id


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
    """Persist a resolved write-confirm so a reopened chat re-renders a greyed
    'Approved/Declined/Expired' card in the timeline. Stored as role='tool', which
    `_load_transcript` skips — visible in the UI, never re-fed to the model. The
    structured card payload + outcome ride in `tool_calls` (the FE reads it back)."""
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
    a cleanup concern, not a correctness one)."""
    from datetime import datetime

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
        created = created.replace(tzinfo=UTC)
    if (timezone.now() - created).total_seconds() < ttl_seconds:
        return False
    _save_confirm_outcome(ai_chat_id, pending.get("value") or {}, "expired")
    AiChat.objects.filter(id=ai_chat_id).update(pending_confirm=None)
    return True


create_ai_chat = sync_to_async(_create_ai_chat)
list_ai_chats = sync_to_async(_list_ai_chats)
load_transcript = sync_to_async(_load_transcript)
save_ai_message = sync_to_async(_save_ai_message)
save_turn_blocks = sync_to_async(_save_turn_blocks)
set_title_if_empty = sync_to_async(_set_title_if_empty)
owns_ai_chat = sync_to_async(_owns_ai_chat)
needs_title = sync_to_async(_needs_title)
save_pending_confirm = sync_to_async(_save_pending_confirm)
load_pending_confirm = sync_to_async(_load_pending_confirm)
clear_pending_confirm = sync_to_async(_clear_pending_confirm)
save_confirm_outcome = sync_to_async(_save_confirm_outcome)
expire_pending_confirm_if_stale = sync_to_async(_expire_pending_confirm_if_stale)


# ---- Agent memory (per-principal; namespace-scoped + recency-capped) -----------
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
    into the copilot system prompt."""
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


def _visible_listing_q(principal_id: int):
    """Marketplace visibility (same rule as the REST ListingViewSet): the principal's
    own listings in any status + everyone else's ACTIVE ones."""
    from django.db.models import Q

    return Q(seller_id=principal_id) | Q(status="active")


def _browse_listings(principal_id: int, q: str | None = None, limit: int = 20) -> list[dict]:
    from catalog.models import Listing

    qs = (
        Listing.objects.filter(status="active")
        .select_related("seller")
        .order_by("-created_at")
        .prefetch_related("listingproperty_set__property")
    )
    if q:
        from django.db.models import Q

        qs = qs.filter(
            Q(title__icontains=q)
            | Q(description__icontains=q)
            | Q(listingproperty__property__address_raw__icontains=q)
        ).distinct()
    rows = []
    for lst in qs[: max(1, min(int(limit), 50))]:
        row = _listing_summary_row(lst)
        row["seller_id"] = lst.seller_id
        row["seller_name"] = lst.seller.display_name
        row["owned_by_principal"] = lst.seller_id == principal_id
        rows.append(row)
    return rows


def _get_listing_detail(listing_id: int, seller_id: int) -> dict:
    from catalog import services
    from catalog.models import Listing

    lst = (
        Listing.objects.filter(_visible_listing_q(seller_id), id=listing_id)
        .select_related("seller")
        .prefetch_related("listingproperty_set__property")
        .first()
    )
    if lst is None:
        return {"error": f"listing {listing_id} not found or not visible to you"}
    props = []
    for lp in lst.listingproperty_set.select_related("property").order_by("sort_order"):
        p = lp.property
        eff = lp.effective_attrs()  # base ⊕ seller's per-listing current-state overrides
        props.append(
            {
                "property_id": p.id,
                "address": p.address_raw,
                "beds": eff["beds"],
                "baths": float(eff["baths"]) if eff["baths"] is not None else None,
                "sqft": eff["sqft"],
                "condition": eff["condition"],
                "year_built": eff["year_built"],
                "asking_price": float(lp.asking_price) if lp.asking_price is not None else None,
                # which current-state attrs the seller restated for this listing:
                "seller_stated_fields": eff["seller_stated_fields"],
            }
        )
    owned = lst.seller_id == seller_id
    detail = {
        "listing_id": lst.id,
        "title": lst.title,
        "description": lst.description,
        "status": lst.status,
        "bundle_type": lst.bundle_type,
        "asking_price": float(lst.asking_price) if lst.asking_price is not None else None,
        "properties": props,
        "seller_id": lst.seller_id,
        "seller_name": lst.seller.display_name,
        "owned_by_principal": owned,
    }
    # The mandate (floor/ceiling/instructions) is seller-private — the airlock rule:
    # another seller's listing gets no mandate slot at all, not an empty one.
    if owned:
        detail["mandate"] = services.get_mandate_for_listing(lst)
    return detail


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


def _get_listing_first_lp(listing_id: int, seller_id: int):
    """First ListingProperty of any listing VISIBLE to the principal (own or active) —
    carries the base property AND the seller's per-listing current-state overrides, so
    valuation/comps value the effective subject. Estimate/comps are market data, not
    seller-private."""
    from django.db.models import Q

    from catalog.models import ListingProperty

    return (
        ListingProperty.objects.filter(
            Q(listing__seller_id=seller_id) | Q(listing__status="active"),
            listing_id=listing_id,
        )
        .select_related("property")
        .order_by("sort_order")
        .first()
    )


def _get_listing_first_property(listing_id: int, seller_id: int):
    """The base Property of the first visible listing-property (back-compat helper)."""
    lp = _get_listing_first_lp(listing_id, seller_id)
    return lp.property if lp else None


def _estimate_for_listing(listing_id: int, seller_id: int, arv: bool) -> dict:
    from matching.engine import estimate_current_value, estimate_value

    lp = _get_listing_first_lp(listing_id, seller_id)
    if lp is None or lp.property is None:
        return {"error": f"listing {listing_id} not found or not visible to you"}
    eff = lp.effective_attrs()
    result = estimate_value(eff, arv=arv)
    # Condition-aware current value (as-is): the number that moves with a renovation.
    result["current_value"] = estimate_current_value(eff)
    result["subject"] = {
        "address": lp.property.address_raw,
        "beds": eff["beds"],
        "sqft": eff["sqft"],
    }
    return result


def _comps_for_listing(listing_id: int, seller_id: int) -> dict:
    from matching.engine import get_comps

    lp = _get_listing_first_lp(listing_id, seller_id)
    if lp is None or lp.property is None:
        return {"error": f"listing {listing_id} not found or not visible to you"}
    return get_comps(lp.effective_attrs())


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


def _update_listing_property(
    listing_id: int, seller_id: int, property_id: int, fields: dict
) -> dict:
    """Set the seller's per-listing current-state overrides for one property on their
    listing (post-reno condition, a correction, an addition). Never mutates the shared
    Property — the override lives on the ListingProperty through-row. Owner-scoped."""
    from catalog import services
    from catalog.models import Listing

    listing = Listing.objects.filter(id=listing_id, seller_id=seller_id).first()
    if listing is None:
        return {"error": f"listing {listing_id} not found or not yours"}
    data = dict(fields)
    if "condition" in data:  # accept an int (1–5) or a label (full_gut/cosmetic/turnkey)
        data["condition"] = _condition_to_int(data["condition"])
    return services.update_listing_property(listing, property_id, data)


property_lookup = sync_to_async(_property_lookup)
search_properties = sync_to_async(_search_properties)
list_seller_listings = sync_to_async(_list_seller_listings)
browse_listings = sync_to_async(_browse_listings)
get_listing_detail = sync_to_async(_get_listing_detail)
create_listing = sync_to_async(_create_listing)
update_listing = sync_to_async(_update_listing)
estimate_for_listing = sync_to_async(_estimate_for_listing)
comps_for_listing = sync_to_async(_comps_for_listing)
get_mandate_for_listing = sync_to_async(_get_mandate_for_listing)
set_mandate_for_listing = sync_to_async(_set_mandate_for_listing)
update_listing_property = sync_to_async(_update_listing_property)


# ---- Buy-box CRUD (delegates to catalog.services — the seam shared with REST) -----
# The `/settings › Buy-boxes` REST views and these copilot tools share one seam
# (agent == API); dal stays the user-scoped async wrapper. The copilot's flat `fields`
# contract (scalars + inline ceiling/must_haves/instructions + a single `geo`) is the
# services input shape.
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


def _rank_buyers_for_listings(
    seller_id: int, listing_ids: list[int], limit_per_listing: int = 10
) -> dict:
    """The multi-listing 'who matches what' read: per-buyer merged rankings over the
    seller's listings (each match keeps its per-listing score + reason), plus listing
    meta so the caller can narrate addresses. Ownership-checked per listing."""
    from catalog.models import Listing, ListingProperty
    from matching.engine import rank_buyers_multi

    ids = list(dict.fromkeys(int(x) for x in (listing_ids or [])))
    if not ids:
        return {"error": "no listing_ids given"}
    owned = set(
        Listing.objects.filter(id__in=ids, seller_id=seller_id).values_list("id", flat=True)
    )
    errors = [
        {"listing_id": lid, "error": "not found or not yours"} for lid in ids if lid not in owned
    ]
    ok_ids = [lid for lid in ids if lid in owned]
    if not ok_ids:
        return {"buyers": [], "listings": [], "errors": errors}

    res = rank_buyers_multi(ok_ids, limit_per_listing=limit_per_listing)
    meta: dict[int, dict] = {}
    for lp in (
        ListingProperty.objects.filter(listing_id__in=ok_ids)
        .select_related("property", "listing")
        .order_by("listing_id", "sort_order")
    ):
        meta.setdefault(
            lp.listing_id,
            {
                "listing_id": lp.listing_id,
                "address": lp.property.address_raw,
                "asking_price": (
                    float(lp.listing.asking_price) if lp.listing.asking_price is not None else None
                ),
            },
        )
    out = {
        "listings": [meta.get(lid, {"listing_id": lid, "address": None}) for lid in ok_ids],
        "buyers": res["buyers"],
    }
    if errors:
        out["errors"] = errors
    return out


def _preview_outreach(seller_id: int, recipients: list[dict]) -> dict:
    """Validate an outreach selection + resolve names/addresses/ledger flags for the
    confirm card. Read-only (safe to re-run on an interrupt resume). Builds the same
    templated fallback body the commit would use, so the card never lies."""
    from django.contrib.auth import get_user_model

    from ai.outreach_service import _ledger_already_sent, build_opener
    from catalog.models import Listing, ListingProperty

    recipients = [r for r in (recipients or []) if r.get("listing_ids")]
    if not recipients:
        return {"error": "no recipients (each needs a user_id and at least one listing_id)"}

    all_lids = sorted({int(lid) for r in recipients for lid in r["listing_ids"]})
    owned = {lst.id: lst for lst in Listing.objects.filter(id__in=all_lids, seller_id=seller_id)}
    missing = [lid for lid in all_lids if lid not in owned]
    if missing:
        return {"error": f"listing(s) {missing} not found or not yours — check list_my_listings"}

    props: dict[int, object] = {}
    for lp in (
        ListingProperty.objects.filter(listing_id__in=all_lids)
        .select_related("property")
        .order_by("listing_id", "sort_order")
    ):
        props.setdefault(lp.listing_id, lp.property)

    User = get_user_model()
    uids = [int(r["user_id"]) for r in recipients]
    users = {u.id: u for u in User.objects.filter(id__in=uids)}
    bad = sorted({uid for uid in uids if uid not in users or uid == seller_id})
    if bad:
        return {
            "error": f"recipient user(s) {bad} not found (or the seller themself) — "
            "resolve buyers with rank_buyers_for_listings or find_buyers"
        }

    out = []
    for r in recipients:
        uid = int(r["user_id"])
        name = users[uid].display_name
        lids = list(dict.fromkeys(int(x) for x in r["listing_ids"]))
        listings = []
        fresh = []
        for lid in lids:
            already = _ledger_already_sent(lid, uid)
            prop = props.get(lid)
            listings.append(
                {
                    "listing_id": lid,
                    "address": prop.address_raw if prop else None,
                    "already_contacted": already,
                }
            )
            if not already:
                fresh.append(lid)
        body = (r.get("body") or "").strip() or build_opener(
            [(props.get(lid), owned[lid].asking_price) for lid in (fresh or lids)], name
        )
        out.append({"user_id": uid, "name": name, "listings": listings, "body": body})
    return {"recipients": out}


def _launch_outreach(seller_id: int, recipients: list[dict], ai_chat_id=None) -> dict:
    """Persist an `awaiting_approval` campaign from explicit selections (the pure ledger
    core). The caller approves + emits `outreach/approved` separately (Inngest stays out
    of dal)."""
    from ai.outreach_service import launch_outreach

    return launch_outreach(seller_id, recipients, copilot_ai_chat_id=ai_chat_id)


def _approve_campaign(seller_id: int, campaign_id: int) -> dict:
    """Flip an `awaiting_approval` campaign to `sending` (the batch send gate)."""
    from ai.outreach_service import approve_campaign

    return approve_campaign(seller_id, campaign_id)


rank_buyers = sync_to_async(_rank_buyers)
rank_buyers_for_listings = sync_to_async(_rank_buyers_for_listings)
find_buyers = sync_to_async(_find_buyers)
assess_deal_for_listing = sync_to_async(_assess_deal_for_listing)
preview_outreach = sync_to_async(_preview_outreach)
launch_outreach = sync_to_async(_launch_outreach)
approve_campaign = sync_to_async(_approve_campaign)


# ---- Human 1:1 chats (copilot resolver + confirm-gated follow-ups) ---------------
def _list_chats(
    principal_id: int,
    *,
    counterparty: str | None = None,
    involves_listing_id: int | None = None,
    awaiting_reply_only: bool = False,
    limit: int = 20,
) -> list[dict]:
    """The principal's 1:1 chats, filterable — the copilot's people→chat_id resolver.
    `awaiting_reply` = the last sent message came from our side (human or agent), i.e.
    the counterparty hasn't answered yet."""
    from chat.services import list_inbox

    rows = list_inbox(principal_id)
    out: list[dict] = []
    for r in rows:
        last = r.get("last_message")
        awaiting = bool(last and last.get("sender") == principal_id)
        if awaiting_reply_only and not awaiting:
            continue
        cp = r.get("counterparty") or {}
        if counterparty and counterparty.lower() not in (cp.get("name") or "").lower():
            continue
        out.append(
            {
                "chat_id": r["id"],
                "counterparty": {"user_id": cp.get("id"), "name": cp.get("name")},
                "unread": r["unread"],
                "awaiting_reply": awaiting,
                "updated_at": r["updated_at"],
                "last_message": (
                    None
                    if not last
                    else {
                        "body": (last.get("body") or "")[:200],
                        "kind": last.get("kind"),
                        "from_me": last.get("sender") == principal_id,
                        "created_at": last.get("created_at"),
                    }
                ),
            }
        )
    if involves_listing_id is not None:
        from chat.models import MessageAttachment

        with_listing = set(
            MessageAttachment.objects.filter(
                listing_id=involves_listing_id,
                message__chat_id__in=[o["chat_id"] for o in out],
            ).values_list("message__chat_id", flat=True)
        )
        out = [o for o in out if o["chat_id"] in with_listing]
    return out[:limit]


def _preview_message_sends(principal_id: int, user_ids: list[int], listing_ids: list[int]) -> dict:
    """Recipient + attachment validation for the confirm card. Read-only (safe to re-run
    on an interrupt resume). Resolves each valid recipient's display name and whether the
    send opens a new chat (no pair chat yet); a user_id missing from `recipients` is
    invalid (unknown, or the principal themself). Attachments follow marketplace
    visibility — ids the principal can't see come back in `invalid_listing_ids`."""
    from django.contrib.auth import get_user_model

    from catalog.models import Listing
    from chat.models import Chat, make_pair_key

    ids = {int(u) for u in user_ids} - {principal_id}
    users = list(get_user_model().objects.filter(id__in=ids))
    existing = set(
        Chat.objects.filter(
            pair_key__in=[make_pair_key(principal_id, u.id) for u in users]
        ).values_list("pair_key", flat=True)
    )
    recipients = {
        u.id: {
            "name": u.display_name,
            "new_chat": make_pair_key(principal_id, u.id) not in existing,
        }
        for u in users
    }
    wanted = {int(x) for x in listing_ids}
    visible = set(
        Listing.objects.filter(_visible_listing_q(principal_id), id__in=wanted).values_list(
            "id", flat=True
        )
    )
    return {"recipients": recipients, "invalid_listing_ids": sorted(wanted - visible)}


def _send_messages(principal_id: int, sends: list[dict], dedup_prefix: str) -> dict:
    """Commit a confirmed batch: one kind='agent' message per recipient, sent as Polaris
    for the principal. The pair chat is found-or-created per recipient (the same move as
    the web client's POST /api/chats/), so first contact and follow-up are one operation.
    Each insert is idempotent under `{dedup_prefix}:{chat_id}:{body-hash}` so a resume
    replay can't double-send (sends stay repeatable across turns — unlike the outreach
    ledger, there is deliberately no once-ever rule here)."""
    import hashlib

    from django.contrib.auth import get_user_model

    from chat.services import get_or_create_chat, post_agent_message

    results: list[dict] = []
    for s in sends:
        recipient_id = int(s["recipient_user_id"])
        body = (s.get("body") or "").strip()
        if (
            recipient_id == principal_id
            or not get_user_model().objects.filter(id=recipient_id).exists()
        ):
            results.append(
                {
                    "recipient_user_id": recipient_id,
                    "status": "error",
                    "error": "unknown recipient",
                }
            )
            continue
        if not body:
            results.append(
                {"recipient_user_id": recipient_id, "status": "error", "error": "empty body"}
            )
            continue
        chat, _created = get_or_create_chat(principal_id, recipient_id)
        digest = hashlib.sha1(body.encode()).hexdigest()[:8]
        saved = post_agent_message(
            chat.id,
            principal_id,
            body,
            attachment_listing_ids=s.get("listing_ids") or None,
            dedup_key=f"{dedup_prefix}:{chat.id}:{digest}",
        )
        if saved.get("duplicate"):
            results.append(
                {"recipient_user_id": recipient_id, "chat_id": chat.id, "status": "duplicate"}
            )
            continue
        results.append(
            {
                "recipient_user_id": recipient_id,
                "chat_id": chat.id,
                "status": "sent",
                "message_id": saved["id"],
                # The full wire-shape message, for the caller's live WS broadcast only —
                # popped before the result is narrated back to the model.
                "message": {k: v for k, v in saved.items() if k != "duplicate"},
            }
        )
    return {"sent": sum(1 for r in results if r["status"] == "sent"), "results": results}


list_chats = sync_to_async(_list_chats)
preview_message_sends = sync_to_async(_preview_message_sends)
send_messages = sync_to_async(_send_messages)


# ---- Away-responder: plan resolution + deterministic deal math -------------------
# The away-assistant covers one human's chats while they're away. It reads the whole
# free-form chat: the full transcript, every listing ever attached, and the principal's
# in-play mandates. No fixed role, no bound listing.
_DISPOSITION_TO_STRATEGY = {"flip": "fix_flip", "hold": "buy_hold", "brrrr": "brrrr"}


def _mandate_dict(m) -> dict:
    """A mandate row → the private dict the responder reasons from. Governance knobs
    (auto_reply/autonomy) are user-level on UserProfile, not here."""
    return {
        "floor_price": int(m.floor_price) if m.floor_price is not None else None,
        "ceiling_price": int(m.ceiling_price) if m.ceiling_price is not None else None,
        "must_haves": list(m.must_haves or []),
        "availability_window": m.availability_window,
        "instructions": m.instructions or "",
    }


def _listing_public(listing, principal_id: int) -> dict:
    """Public listing facts (visible to both parties) + an ownership flag. `owned_by_
    principal` is what derives stance — the away-assistant defends a listing it owns and
    evaluates one it doesn't."""
    lp = listing.listingproperty_set.select_related("property").order_by("sort_order").first()
    prop = lp.property if lp else None
    # Effective current-state = base ⊕ the seller's per-listing overrides. The buyer must
    # see the same figures the ARV was computed on, and `seller_stated_fields` tells the
    # disclosure layer which of these are seller-restated (to caveat, never suppress).
    eff = lp.effective_attrs() if lp else None
    ask = (
        listing.asking_price
        if listing.asking_price is not None
        else (lp.asking_price if lp else None)
    )
    return {
        "listing_id": listing.id,
        "title": listing.title,
        "address": prop.address_raw if prop else None,
        "beds": eff["beds"] if eff else None,
        "baths": float(eff["baths"]) if eff and eff["baths"] is not None else None,
        "sqft": eff["sqft"] if eff else None,
        "condition": eff["condition"] if eff else None,
        "year_built": eff["year_built"] if eff else None,
        "asking_price": float(ask) if ask is not None else None,
        "owned_by_principal": listing.seller_id == principal_id,
        "seller_stated_fields": eff["seller_stated_fields"] if eff else [],
    }


def _chat_listings(chat_id: int, principal_id: int) -> tuple[list[dict], int | None]:
    """Every listing attached anywhere in the chat (dedup'd), plus the focal one (the
    most-recently referenced attachment). Free-form chats accrue many listings over
    time, so the responder tracks the latest-referenced one."""
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
    """The public transcript (sent messages only), oldest first, tagged with whether each
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
    union of their limits is what the output check scans."""
    from catalog.models import Mandate

    out: list[dict] = []
    owned_ids = [lst["listing_id"] for lst in listings if lst.get("owned_by_principal")]
    if owned_ids:
        out.extend(_mandate_dict(m) for m in Mandate.objects.filter(listing_id__in=owned_ids))
    out.extend(
        _mandate_dict(m)
        for m in Mandate.objects.filter(buy_box__buyer_id=principal_id, buy_box__is_active=True)
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

    member_ids = list(ChatMember.objects.filter(chat_id=chat_id).values_list("user_id", flat=True))
    if len(member_ids) != 2 or inbound["sender_id"] not in member_ids:
        return {"skip": "inbound sender is not one of the two chat members"}

    # Principal = the other member (the away human the agent covers for). The inbound
    # sender is the counterparty this turn — including an agent-authored inbound, where
    # the sender is the other side's principal.
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

    # Stance from ownership of the focal listing (deterministic).
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

    # Mini CRM: the focal deal's stage + standing agent-disclosed offers + the
    # honest-urgency count. Private context; defensive so a deals hiccup never
    # blocks the turn.
    deal_ctx = {"deal": None, "negotiation": None, "other_active_deals": 0}
    try:
        from deals import service as deal_svc

        deal_ctx = deal_svc.responder_context(chat_id, focal_listing_id, principal_id)
    except Exception:  # noqa: BLE001
        log.warning("deals.responder_context failed; continuing without deal state")

    return {
        **deal_ctx,
        "principal_id": principal_id,
        "counterparty_user_id": counterparty_user_id,
        "counterparty_name": _display_name(counterparty_user_id),
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
    """Deterministic wholesale verdict for the focal listing (buy-side). No ownership
    check — the focal listing is the counterparty's on the buy side. Attaches the
    grounded `max_offer` (the price at which margin == threshold): the private anchor
    Stage 1 negotiates from — never disclosed, never in share_lines."""
    from matching.engine import assess_deal

    res = assess_deal(listing_id, strategy=strategy)
    try:
        arv = float(res["arv"])
        rehab = float(res["est_rehab"])
        fee = float(res["wholesale_fee"])
        threshold = float(res["threshold"])
        res["max_offer"] = int(arv - rehab - fee - threshold * arv)
    except (KeyError, TypeError, ValueError):
        pass  # thin comps / missing inputs → no grounded anchor
    return res


def _responder_estimate(listing_id: int) -> dict:
    """Market value + a few comps for the focal listing (sell-side price defense)."""
    from catalog.models import ListingProperty
    from matching.engine import estimate_value, get_comps

    lp = (
        ListingProperty.objects.filter(listing_id=listing_id)
        .select_related("property")
        .order_by("sort_order")
        .first()
    )
    if lp is None or lp.property is None:
        return {}
    eff = lp.effective_attrs()  # value the effective subject (base ⊕ seller overrides)
    # Both figures: as-is market value defends the asking price; ARV answers the
    # wholesale buyer's "ARV supported by comps?" (share flags gate what crosses).
    val = estimate_value(eff, arv=False)
    arv_val = estimate_value(eff, arv=True)
    comps = get_comps(eff)
    return {
        "value": val,
        "arv": arv_val,
        "n_comps": comps.get("n"),
        "comps": comps.get("comps", [])[:5],
        # provenance: True when the shared value/ARV derive from seller-restated attrs, so
        # render_shared_lines caveats them before they cross to the counterparty.
        "seller_stated": bool(eff["seller_stated_fields"]),
        "seller_stated_fields": eff["seller_stated_fields"],
    }


def _list_deals(
    principal_id: int,
    *,
    side: str | None = None,
    stage: str | None = None,
    listing_id: int | None = None,
    limit: int = 50,
) -> list[dict]:
    """The user's deal pipeline (mini CRM), same row shape as /api/deals/."""
    from deals.views import _base_queryset, serialize_deal

    qs = _base_queryset(principal_id)
    if side == "selling":
        qs = qs.filter(seller_id=principal_id)
    elif side == "buying":
        qs = qs.filter(buyer_id=principal_id)
    if stage:
        qs = qs.filter(stage=stage)
    if listing_id is not None:
        qs = qs.filter(listing_id=listing_id)
    return [serialize_deal(d, principal_id) for d in qs[: max(1, min(limit, 200))]]


def _update_deal_stage(principal_id: int, deal_id: int, stage: str) -> dict:
    """Manual stage override, ownership-checked (buyer or seller)."""
    from django.db.models import Q

    from deals import service as deal_svc
    from deals.models import Deal
    from deals.views import serialize_deal

    deal = Deal.objects.filter(
        Q(buyer_id=principal_id) | Q(seller_id=principal_id), id=deal_id
    ).first()
    if deal is None:
        return {"error": "deal not found"}
    if stage not in deal_svc.ALL_STAGES:
        return {"error": f"stage must be one of {list(deal_svc.ALL_STAGES)}"}
    deal_svc.set_stage_manual(deal, stage)
    return serialize_deal(deal, principal_id)


responder_plan = sync_to_async(_responder_plan)
responder_assess = sync_to_async(_responder_assess)
responder_estimate = sync_to_async(_responder_estimate)
list_deals = sync_to_async(_list_deals)
update_deal_stage = sync_to_async(_update_deal_stage)
