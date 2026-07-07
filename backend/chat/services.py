"""
chat services — the write/query seam for human 1:1 messaging (the same layer the REST
views and the ChatConsumer both call, so live-WS and REST stay in lockstep).

Covers: **find-or-create the one chat per user-pair** (`pair_key`), posting a human
message (with optional listing attachments + client-dedup), the inbox list, a chat's
transcript, and read-state. Pure ORM, no LLM. The autonomous-reply commit gate lives
separately in `responder_service.py` (the invariant core).
"""

from __future__ import annotations

import logging

from django.db import IntegrityError, transaction
from django.utils import timezone

from .models import Chat, ChatMember, Message, MessageAttachment, make_pair_key

log = logging.getLogger(__name__)


def pair_key(a_id: int, b_id: int) -> str:
    return make_pair_key(a_id, b_id)


# ---- find-or-create the single pair chat ---------------------------------------
@transaction.atomic
def get_or_create_chat(a_id: int, b_id: int) -> tuple[Chat, bool]:
    """Return (chat, created) for the ONE chat between users a and b. Idempotent from
    either direction (canonical `pair_key`); creates exactly two `ChatMember` rows."""
    if a_id == b_id:
        raise ValueError("a chat needs two distinct users")
    key = make_pair_key(a_id, b_id)
    chat, created = Chat.objects.get_or_create(pair_key=key)
    if created:
        ChatMember.objects.bulk_create(
            [ChatMember(chat=chat, user_id=a_id), ChatMember(chat=chat, user_id=b_id)]
        )
    return chat, created


def chat_membership(chat_id: int, user_id: int) -> dict | None:
    """The acting user's membership view of a chat, or None if they're not a member.
    Returns the counterparty's id (2-party ⇒ unambiguous)."""
    if not ChatMember.objects.filter(chat_id=chat_id, user_id=user_id).exists():
        return None
    other = (
        ChatMember.objects.filter(chat_id=chat_id)
        .exclude(user_id=user_id)
        .values_list("user_id", flat=True)
        .first()
    )
    return {"chat_id": chat_id, "counterparty_user_id": other}


# ---- serialization -------------------------------------------------------------
def _listing_brief(listing) -> dict:
    """A light inline card for an attached listing (click → /listings/[id])."""
    lp = listing.listingproperty_set.select_related("property").order_by("sort_order").first()
    prop = lp.property if lp else None
    return {
        "listing_id": listing.id,
        "title": listing.title,
        "status": listing.status,
        "asking_price": float(listing.asking_price) if listing.asking_price is not None else None,
        "address": prop.address_raw if prop else None,
    }


def serialize_message(m: Message) -> dict:
    """Wire shape for one message (REST history + WS live). Keyed off `sender`/`kind`,
    never a side. Includes inline listing attachments."""
    attachments = []
    for a in m.attachments.all():
        attachments.append(
            {
                "id": a.id,
                "kind": a.kind,
                "listing_id": a.listing_id,
                "listing": _listing_brief(a.listing) if a.listing_id else None,
                "sort_order": a.sort_order,
            }
        )
    return {
        "id": m.id,
        "chat_id": m.chat_id,
        "kind": m.kind,
        "sender": m.sender_id,
        "action": m.action,
        "body": m.body,
        "status": m.status,
        "reply_to": m.reply_to_id,
        "created_at": m.created_at.isoformat(),
        "attachments": attachments,
    }


# ---- post a human message (used by both the consumer and REST) -----------------
def _attach_listings(message: Message, listing_ids) -> None:
    from catalog.models import Listing

    valid = list(
        Listing.objects.filter(id__in=[int(x) for x in listing_ids]).values_list("id", flat=True)
    )
    MessageAttachment.objects.bulk_create(
        [
            MessageAttachment(message=message, kind="listing", listing_id=lid, sort_order=i)
            for i, lid in enumerate(valid)
        ]
    )


def post_human_message(
    chat_id: int,
    sender_id: int,
    body: str,
    *,
    attachment_listing_ids=None,
    client_dedup_uuid: str | None = None,
) -> dict:
    """Persist a human message (system of record) + optional listing attachments.
    A repeated `client_dedup_uuid` (double-tap / retry) is a silent no-op → {duplicate}.
    Returns the serialized message dict (with `duplicate: bool`)."""
    body = (body or "").strip()
    key = f"human:{chat_id}:{sender_id}:{client_dedup_uuid}" if client_dedup_uuid else None
    try:
        with transaction.atomic():
            msg = Message.objects.create(
                chat_id=chat_id,
                kind="human",
                sender_id=sender_id,
                body=body,
                status="sent",
                sent_at=timezone.now(),
                dedup_key=key,
            )
            if attachment_listing_ids:
                _attach_listings(msg, attachment_listing_ids)
    except IntegrityError:
        return {"duplicate": True}
    Chat.objects.filter(id=chat_id).update(updated_at=timezone.now())
    return {**serialize_message(msg), "duplicate": False}


def post_agent_message(
    chat_id: int,
    sender_id: int,
    body: str,
    *,
    attachment_listing_ids=None,
    dedup_key: str | None = None,
    action: str = "inform",
) -> dict:
    """Persist an agent message — Polaris speaking FOR `sender_id` (kind='agent',
    sender=the principal), e.g. a copilot follow-up or an outreach opener. The caller
    supplies a namespaced `dedup_key` (`copilot:…` / `outreach:…`) so a replayed commit
    is a silent no-op → {duplicate}. Returns the serialized message dict."""
    body = (body or "").strip()
    try:
        with transaction.atomic():
            msg = Message.objects.create(
                chat_id=chat_id,
                kind="agent",
                sender_id=sender_id,
                action=action,
                body=body,
                status="sent",
                sent_at=timezone.now(),
                dedup_key=dedup_key,
            )
            if attachment_listing_ids:
                _attach_listings(msg, attachment_listing_ids)
    except IntegrityError:
        return {"duplicate": True}
    Chat.objects.filter(id=chat_id).update(updated_at=timezone.now())
    return {**serialize_message(msg), "duplicate": False}


# ---- inbox + transcript + read state -------------------------------------------
def _counterparty(chat: Chat, user_id: int):
    for member in chat.members.all():
        if member.user_id != user_id:
            return member.user
    return None


def _has_unread(chat: Chat, user_id: int, member: ChatMember) -> bool:
    """A sent message from the OTHER party newer than our last_read_at (or chat start)."""
    since = member.last_read_at or chat.created_at
    return (
        Message.objects.filter(chat_id=chat.id, status="sent", created_at__gt=since)
        .exclude(sender_id=user_id)
        .exists()
    )


def _serialize_chat_row(chat: Chat, user_id: int) -> dict:
    member = next((m for m in chat.members.all() if m.user_id == user_id), None)
    other = _counterparty(chat, user_id)
    last = (
        Message.objects.filter(chat_id=chat.id, status="sent")
        .order_by("-created_at", "-id")
        .values("body", "kind", "sender_id", "action", "created_at")
        .first()
    )
    return {
        "id": chat.id,
        "counterparty": (
            None
            if other is None
            else {
                "id": other.id,
                "name": other.display_name,
                "avatar_url": getattr(getattr(other, "profile", None), "avatar_url", "") or "",
            }
        ),
        "status": chat.status,
        "terminal": chat.terminal,
        "updated_at": chat.updated_at.isoformat(),
        "unread": bool(member and _has_unread(chat, user_id, member)),
        "last_message": (
            None
            if not last
            else {
                "body": last["body"],
                "kind": last["kind"],
                "sender": last["sender_id"],
                "action": last["action"],
                "created_at": last["created_at"].isoformat(),
            }
        ),
    }


def list_inbox(user_id: int) -> list[dict]:
    chat_ids = ChatMember.objects.filter(user_id=user_id).values_list("chat_id", flat=True)
    chats = (
        Chat.objects.filter(id__in=list(chat_ids))
        .order_by("-updated_at")
        .prefetch_related("members__user__profile")
    )
    return [_serialize_chat_row(c, user_id) for c in chats]


def chat_header(chat_id: int, user_id: int) -> dict | None:
    chat = (
        Chat.objects.filter(id=chat_id)
        .prefetch_related("members__user__profile")
        .first()
    )
    if chat is None or not any(m.user_id == user_id for m in chat.members.all()):
        return None
    return _serialize_chat_row(chat, user_id)


def list_messages(chat_id: int, user_id: int) -> list[dict] | None:
    """Transcript for a member: all `sent` messages + this user's own `draft`s (a draft
    is visible ONLY to its owner — the away-responder's proposed reply awaiting approval)."""
    if not ChatMember.objects.filter(chat_id=chat_id, user_id=user_id).exists():
        return None
    from django.db.models import Q

    qs = (
        Message.objects.filter(chat_id=chat_id)
        .filter(Q(status="sent") | Q(status="draft", sender_id=user_id))
        .order_by("created_at", "id")
        .prefetch_related("attachments__listing__listingproperty_set__property")
    )
    return [serialize_message(m) for m in qs]


def mark_read(chat_id: int, user_id: int) -> bool:
    updated = ChatMember.objects.filter(chat_id=chat_id, user_id=user_id).update(
        last_read_at=timezone.now()
    )
    return bool(updated)
