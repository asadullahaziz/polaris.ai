"""
catalog services — the write/query seam shared by the REST API and the copilot's
tools, so the agent can do anything a user can do manually and stays in lockstep
with the API.

Covers: address normalization + fetch-existing property dedup (never mutate a
matched Property — it's the comp basis), multi-property listing create/update, the
per-listing Mandate get/set (deal settings), and buy-box CRUD with inline
deal-settings. Pure ORM, no LLM.
"""

from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation

from django.db import transaction
from django.db.models import Max

from . import storage
from .models import BuyBox, BuyBoxGeo, Listing, ListingMedia, ListingProperty, Mandate, Property


def _to_decimal(v):
    if v in (None, ""):
        return None
    try:
        return Decimal(str(v))
    except (InvalidOperation, ValueError):
        return None


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


def search_properties(q: str, limit: int = 8) -> list[dict]:
    """Closed-world address autocomplete over the known Property universe (no
    geocoder). Matching runs on the normalized address so 'Alder Street' finds
    'Alder St'. Read-only; powers the FE combobox AND the copilot's search tool."""
    norm = normalize_address(q)
    if len(norm) < 2:
        return []
    qs = Property.objects.filter(address_norm__icontains=norm).order_by("address_norm")[
        : max(1, min(limit, 25))
    ]
    return [
        {
            **_property_public(p),
            "last_sale_price": float(p.last_sale_price) if p.last_sale_price is not None else None,
            "last_sale_date": p.last_sale_date.isoformat() if p.last_sale_date else None,
        }
        for p in qs
    ]


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

    * `{"property_id": N}` → return the existing Property unchanged (comp basis).
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


# --- listing media (create-time attach is inline in create_listing; these are
#     the manage-photos-on-an-existing-listing seam) ---------------------------
@transaction.atomic
def add_listing_media(listing: Listing, items: list[dict]) -> list[ListingMedia]:
    """Append media rows to `listing`. Items without a sort_order continue after
    the current max, so newly added photos land after existing ones."""
    current_max = listing.media.aggregate(m=Max("sort_order"))["m"]
    next_order = 0 if current_max is None else current_max + 1
    return [
        ListingMedia.objects.create(
            listing=listing,
            kind=m.get("kind", "photo"),
            url=m["url"],
            sort_order=m.get("sort_order", next_order + i),
        )
        for i, m in enumerate(items)
    ]


def remove_listing_media(listing: Listing, media_id: int) -> bool:
    """Delete one media row (False if it isn't on this listing), then best-effort
    delete the backing object when the URL points into our bucket."""
    row = listing.media.filter(id=media_id).first()
    if row is None:
        return False
    url = row.url
    row.delete()
    storage.delete_object_for_url(url)
    return True


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


# --- buy-box CRUD (the buyer-side deal config; deal-settings inline) -----------
# The `/settings › Buy-boxes` REST and the copilot's buy-box tools both call these,
# so the agent and the API stay in lockstep. The input `fields` dict carries buy-box
# scalars + the inline deal-settings (ceiling_price/must_haves/instructions,
# upserted onto the box's Mandate) + an optional single `geo`.
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
    "baths_min",
    "sqft_min",
    "sqft_max",
    "year_built_min",
    "max_rehab_cost",
    "property_types",
)
_BOX_DECIMAL_FIELDS = {
    "price_min",
    "price_max",
    "arv_min",
    "arv_max",
    "baths_min",
    "max_rehab_cost",
}


def _geo_public(g: BuyBoxGeo) -> dict:
    return {
        "id": g.id,
        "geo_type": g.geo_type,
        "mode": g.mode,
        "state_code": g.state_code,
        "county_fips": g.county_fips,
        "city": g.city,
        "zip": g.zip,
        "radius_mi": float(g.radius_mi) if g.radius_mi is not None else None,
        "center_lat": g.center.y if g.center is not None else None,
        "center_lon": g.center.x if g.center is not None else None,
    }


def _num(v):
    return float(v) if v is not None else None


def serialize_buy_box(box: BuyBox) -> dict:
    """The public buy-box view (read shape shared by REST + copilot): `buy_box_id`,
    nested `mandate`, `n_geos`, plus the full scalar set + `geos` so the settings UI
    round-trips."""
    m = box.mandates.first()
    return {
        "buy_box_id": box.id,
        "name": box.name,
        "strategy": box.strategy,
        "is_primary": box.is_primary,
        "is_active": box.is_active,
        "price_min": _num(box.price_min),
        "price_max": _num(box.price_max),
        "arv_min": _num(box.arv_min),
        "arv_max": _num(box.arv_max),
        "beds_min": box.beds_min,
        "baths_min": _num(box.baths_min),
        "sqft_min": box.sqft_min,
        "sqft_max": box.sqft_max,
        "year_built_min": box.year_built_min,
        "max_rehab_cost": _num(box.max_rehab_cost),
        "property_types": list(box.property_types or []),
        "geos": [_geo_public(g) for g in box.geos.all()],
        "n_geos": box.geos.count(),
        "mandate": (
            None
            if m is None
            else {
                "ceiling_price": _num(m.ceiling_price),
                "must_haves": list(m.must_haves or []),
                "instructions": m.instructions,
            }
        ),
    }


def _apply_box_scalars(box: BuyBox, fields: dict) -> None:
    for k in _BOX_SCALAR_FIELDS:
        if fields.get(k) is not None:
            setattr(box, k, _to_decimal(fields[k]) if k in _BOX_DECIMAL_FIELDS else fields[k])


def _apply_box_geo(box: BuyBox, geo: dict) -> None:
    """Create ONE BuyBoxGeo from a simple spec (named place or radius). Best-effort — the
    ranker's candidate pool uses radius geos + nearby sales, so radius matters most."""
    from django.contrib.gis.geos import Point

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


def _upsert_box_mandate(box: BuyBox, fields: dict) -> None:
    data = {}
    if fields.get("ceiling_price") is not None:
        data["ceiling_price"] = _to_decimal(fields["ceiling_price"])
    for k in ("must_haves", "instructions"):
        if fields.get(k) is not None:
            data[k] = fields[k]
    if data:
        Mandate.objects.update_or_create(buy_box=box, defaults=data)


def list_buy_boxes(user_id: int) -> list[dict]:
    return [
        serialize_buy_box(b)
        for b in BuyBox.objects.filter(buyer_id=user_id)
        .order_by("-is_primary", "name")
        .prefetch_related("geos", "mandates")
    ]


def get_buy_box(user_id: int, box_id: int) -> dict:
    box = (
        BuyBox.objects.filter(id=box_id, buyer_id=user_id)
        .prefetch_related("geos", "mandates")
        .first()
    )
    if box is None:
        return {"error": f"buy-box {box_id} not found or not yours"}
    return serialize_buy_box(box)


def create_buy_box(user_id: int, fields: dict) -> dict:
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
    return get_buy_box(user_id, box.id)


def update_buy_box(user_id: int, box_id: int, fields: dict) -> dict:
    box = BuyBox.objects.filter(id=box_id, buyer_id=user_id).first()
    if box is None:
        return {"error": f"buy-box {box_id} not found or not yours"}
    with transaction.atomic():
        _apply_box_scalars(box, fields)
        box.save()
        if fields.get("geo"):
            _apply_box_geo(box, fields["geo"])
        _upsert_box_mandate(box, fields)
    return get_buy_box(user_id, box_id)


def delete_buy_box(user_id: int, box_id: int) -> dict:
    box = BuyBox.objects.filter(id=box_id, buyer_id=user_id).first()
    if box is None:
        return {"error": f"buy-box {box_id} not found or not yours"}
    box.delete()  # cascades geos + its mandate
    return {"deleted": True, "buy_box_id": box_id}
