"""
catalog services — the write/query seam BOTH the REST API and (P2) the copilot's
`dal` tools call, so the agent can do anything a user can do manually and stays in
lockstep with the API.

Covers: address normalization + **fetch-existing property dedup** (never mutate a
matched Property — it's the comp basis), multi-property listing create/update, and
the per-listing Mandate get/set (deal settings). Pure ORM, no LLM.
"""

from __future__ import annotations

import re

from django.db import transaction

from .models import Listing, ListingMedia, ListingProperty, Mandate, Property

# Street-suffix canonicalization so "1 Maple Ave" and "1 maple avenue" normalize equal.
_SUFFIXES = {
    "avenue": "ave",
    "street": "st",
    "road": "rd",
    "boulevard": "blvd",
    "drive": "dr",
    "lane": "ln",
    "court": "ct",
    "place": "pl",
    "terrace": "ter",
    "circle": "cir",
    "parkway": "pkwy",
    "highway": "hwy",
    "apartment": "apt",
    "suite": "ste",
    "north": "n",
    "south": "s",
    "east": "e",
    "west": "w",
}


def normalize_address(raw: str) -> str:
    """Deterministic address key for the dedup unique constraint (`Property.address_norm`).
    Lowercase, strip punctuation, canonicalize common suffixes, collapse whitespace."""
    s = (raw or "").lower()
    s = re.sub(r"[^\w\s]", " ", s)  # punctuation → space
    tokens = [_SUFFIXES.get(t, t) for t in s.split()]
    return " ".join(tokens).strip()


def _property_public(p: Property) -> dict:
    """Read-only view of a Property (matched properties are shown, never edited)."""
    return {
        "id": p.pk,
        "address_raw": p.address_raw,
        "address_norm": p.address_norm,
        "property_type": p.property_type,
        "beds": p.beds,
        "baths": float(p.baths) if p.baths is not None else None,
        "sqft": p.sqft,
        "lot_size_sqft": p.lot_size_sqft,
        "year_built": p.year_built,
        "condition": p.condition,
        "grade": p.grade,
        "waterfront": p.waterfront,
    }


def lookup_property(address: str) -> dict:
    """Fetch-existing dedup: normalize `address` and return the existing Property
    (read-only) or {found: False}. Never creates or mutates."""
    norm = normalize_address(address)
    if not norm:
        return {"found": False, "normalized": norm}
    p = Property.objects.filter(address_norm=norm).first()
    if p is None:
        return {"found": False, "normalized": norm}
    return {"found": True, "normalized": norm, "property": _property_public(p)}


def resolve_geo(address: str):
    """Best-effort address→point using the existing Property universe (no geocoder).
    Returns a geography Point or None (ranking degrades to non-geo signals)."""
    norm = normalize_address(address)
    if not norm:
        return None
    p = Property.objects.filter(address_norm=norm, geom__isnull=False).first()
    return p.geom if p else None


# --- property attach/create (fetch-existing dedup) ----------------------------
_NEW_PROPERTY_FIELDS = (
    "property_type",
    "beds",
    "baths",
    "sqft",
    "lot_size_sqft",
    "year_built",
    "condition",
    "grade",
    "waterfront",
)


def attach_or_create_property(item: dict) -> Property:
    """Resolve one listing-property item to a Property row.

    * `{"property_id": N}` → return the existing Property **unchanged** (comp basis).
    * else `{"address": ..., <attrs>}` → find by normalized address (attach existing,
      unchanged) or create a new Property.
    """
    if item.get("property_id"):
        return Property.objects.get(pk=item["property_id"])

    address = (item.get("address") or "").strip()
    norm = normalize_address(address)
    if norm:
        existing = Property.objects.filter(address_norm=norm).first()
        if existing is not None:
            return existing  # attach existing, never mutate

    fields = {k: item.get(k) for k in _NEW_PROPERTY_FIELDS if item.get(k) is not None}
    return Property.objects.create(
        address_raw=address or "(no address)",
        address_norm=norm or f"new:{address or ''}",
        **fields,
    )


# --- listing create / update --------------------------------------------------
_LISTING_FIELDS = ("title", "description", "asking_price", "bundle_type", "status")


@transaction.atomic
def create_listing(user, data: dict) -> Listing:
    """Create a listing owned by `user` with N properties (+ optional media + mandate)."""
    listing = Listing.objects.create(
        seller=user,
        **{k: data[k] for k in _LISTING_FIELDS if k in data and data[k] is not None},
    )

    for i, item in enumerate(data.get("properties", []) or []):
        prop = attach_or_create_property(item)
        ListingProperty.objects.create(
            listing=listing,
            property=prop,
            asking_price=item.get("asking_price"),
            sort_order=item.get("sort_order", i),
        )

    for i, m in enumerate(data.get("media", []) or []):
        ListingMedia.objects.create(
            listing=listing,
            kind=m.get("kind", "photo"),
            url=m["url"],
            sort_order=m.get("sort_order", i),
        )

    mandate = data.get("mandate")
    if mandate:
        set_mandate_for_listing(listing, mandate)

    return listing


@transaction.atomic
def update_listing(listing: Listing, data: dict) -> Listing:
    """Update listing scalar fields + (optionally) its mandate. Property membership
    edits go through dedicated attach/detach flows, not this scalar update."""
    dirty = []
    for k in _LISTING_FIELDS:
        if k in data and data[k] is not None:
            setattr(listing, k, data[k])
            dirty.append(k)
    if dirty:
        listing.save(update_fields=[*dirty, "updated_at"])
    if data.get("mandate") is not None:
        set_mandate_for_listing(listing, data["mandate"])
    return listing


# --- mandate (deal settings, set in the listing UI) ---------------------------
_MANDATE_FIELDS = (
    "floor_price",
    "ceiling_price",
    "must_haves",
    "availability_window",
    "instructions",
)


def get_mandate_for_listing(listing: Listing) -> dict:
    m = Mandate.objects.filter(listing=listing).first()
    if m is None:
        return {"exists": False}
    return {
        "exists": True,
        "floor_price": float(m.floor_price) if m.floor_price is not None else None,
        "ceiling_price": float(m.ceiling_price) if m.ceiling_price is not None else None,
        "must_haves": m.must_haves,
        "availability_window": m.availability_window,
        "instructions": m.instructions,
    }


def set_mandate_for_listing(listing: Listing, data: dict) -> dict:
    fields = {k: data[k] for k in _MANDATE_FIELDS if k in data}
    Mandate.objects.update_or_create(listing=listing, defaults=fields)
    return get_mandate_for_listing(listing)
