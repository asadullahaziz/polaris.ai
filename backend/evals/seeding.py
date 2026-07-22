"""
Ephemeral DB seeding for away-responder eval scenarios.

Generalizes the live-smoke seeders (`tests/test_responder_smoke.py`) into a
spec-driven builder. Each scenario gets its own namespaced users/listing/mandate/
chat/inbound so `dal.responder_plan` resolves the intended stance, then a best-effort
cleanup tears the world down. Because `run_responder` commits real rows across the
sync_to_async threadpool, we cannot roll back — so run evals against a DISPOSABLE DB
(`make down-v` to reset). Cleanup is best-effort and never raises into the run.

The seeder is deterministic and LLM-free; only the graph run that consumes its output
touches a model.
"""

from __future__ import annotations

import datetime as dt
import logging
from dataclasses import dataclass, field
from decimal import Decimal

loga = logging.getLogger(__name__)

# A fixed King County anchor (county_fips 53033) for the synthetic comp cluster —
# same shape as tests/test_matching.py::_mk.
_ANCHOR_LON = -122.330
_ANCHOR_LAT = 47.600


@dataclass
class ResponderSeed:
    chat_id: int
    inbound_id: int
    principal_id: int
    counterparty_id: int
    listing_id: int | None
    _user_ids: list[int] = field(default_factory=list)
    _property_ids: list[int] = field(default_factory=list)

    def cleanup(self) -> None:
        """Best-effort teardown (PROTECT FKs require dependency order). Guarded: a
        failure leaves residue for the disposable DB reset, never breaks the run."""
        from django.contrib.auth import get_user_model

        from catalog.models import BuyBox, Listing, Property
        from chat.models import Chat

        User = get_user_model()
        for step in (
            lambda: _delete_deals(self._user_ids),
            lambda: Chat.objects.filter(id=self.chat_id).delete(),
            lambda: Listing.objects.filter(seller_id__in=self._user_ids).delete(),
            lambda: BuyBox.objects.filter(buyer_id__in=self._user_ids).delete(),
            lambda: Property.objects.filter(id__in=self._property_ids).delete(),
            lambda: _delete_side_effects(self._user_ids),
            lambda: User.objects.filter(id__in=self._user_ids).delete(),
        ):
            try:
                step()
            except Exception as exc:  # noqa: BLE001 - best-effort; residue is acceptable
                loga.warning("eval seed cleanup step failed (continuing): %s", exc)


def _delete_deals(user_ids: list[int]) -> None:
    from django.db.models import Q

    from deals.models import Deal

    Deal.objects.filter(Q(buyer_id__in=user_ids) | Q(seller_id__in=user_ids)).delete()


def _delete_side_effects(user_ids: list[int]) -> None:
    """Rows that may PROTECT/reference the users and block the final user delete."""
    try:
        from notifications.models import Notification

        Notification.objects.filter(user_id__in=user_ids).delete()
    except Exception:  # noqa: BLE001
        pass
    for path in ("polaris_agent.models", "ai.models"):
        try:
            mod = __import__(path, fromlist=["AgentActionLog"])
            log_model = getattr(mod, "AgentActionLog", None)
            if log_model is not None:
                log_model.objects.filter(user_id__in=user_ids).delete()
        except Exception:  # noqa: BLE001
            pass


def _mandate_kwargs(spec: dict | None) -> dict:
    spec = spec or {}
    out: dict = {}
    for k in ("floor_price", "ceiling_price"):
        if spec.get(k) is not None:
            out[k] = Decimal(str(spec[k]))
    out["must_haves"] = list(spec.get("must_haves") or [])
    out["instructions"] = spec.get("instructions") or ""
    if spec.get("availability_window"):
        out["availability_window"] = spec["availability_window"]
    return out


def _make_listing(seller, spec: dict, *, ns: str, prop_ids: list[int]):
    """A focal listing owned by `seller`, with an attached Property (optionally a
    geolocated comp cluster so the matching engine returns real figures)."""
    from django.contrib.gis.geos import Point

    from catalog.models import Listing, ListingProperty, Property

    facts = spec.get("listing") or {}
    with_comps = bool(spec.get("with_comps"))

    prop = Property.objects.create(
        apn=f"eval-{ns}-subj",
        county_fips="53033",
        address_norm=f"eval:{ns}:subject",
        address_raw=f"{ns} Subject St",
        geom=Point(_ANCHOR_LON, _ANCHOR_LAT, srid=4326) if with_comps else None,
        property_type="sfr",
        beds=facts.get("beds", 3),
        baths=Decimal(str(facts.get("baths", 2))),
        sqft=facts.get("sqft", 1600),
        grade=7 if with_comps else None,
        condition=facts.get("condition", 3),
        waterfront=False if with_comps else None,
    )
    prop_ids.append(prop.id)

    listing = Listing.objects.create(
        seller=seller,
        title=f"{ns} Subject St",
        asking_price=Decimal(str(facts.get("asking_price", 500000))),
        bundle_type="single",
        status="active",
    )
    ListingProperty.objects.create(listing=listing, property=prop, sort_order=0)

    if with_comps:
        _seed_comps(ns, prop_ids, base_price=int(facts.get("asking_price", 640000)))
    return listing


def _seed_comps(ns: str, prop_ids: list[int], *, base_price: int, n: int = 6) -> None:
    """A tight synthetic comp cluster near the anchor so estimate_value / get_comps
    (n_comps >= 5) return a real point value. Faithful to tests/test_matching.py::_mk."""
    from django.contrib.gis.geos import Point
    from django.utils import timezone

    from catalog.models import Property

    for i in range(n):
        p = Property.objects.create(
            apn=f"eval-{ns}-comp-{i}",
            county_fips="53033",
            address_norm=f"eval:{ns}:comp:{i}",
            address_raw=f"{ns} comp {i}",
            geom=Point(_ANCHOR_LON + 0.001 * (i + 1), _ANCHOR_LAT + 0.001 * (i + 1), srid=4326),
            property_type="sfr",
            beds=3,
            baths=Decimal("2"),
            sqft=1600,
            grade=7,
            condition=3,
            waterfront=False,
            last_sale_price=Decimal(base_price + i * 2500),
            last_sale_date=timezone.now().date() - dt.timedelta(days=30 + i),
        )
        prop_ids.append(p.id)


def build_responder_scenario(spec: dict, *, ns: str) -> ResponderSeed:
    """Reconstruct a chat that will resolve to the intended stance, and return the
    (chat_id, inbound_id) `dal.responder_plan` consumes. `ns` must be unique per run+item."""
    from django.contrib.auth import get_user_model

    from catalog.models import BuyBox, Mandate
    from chat import services
    from users.models import UserProfile

    User = get_user_model()
    stance = spec.get("stance", "neutral")
    p_spec = spec.get("principal") or {}
    prop_ids: list[int] = []

    principal = User.objects.create_user(
        email=f"eval-{ns}-principal@polaris.eval",
        password="pw-evaluation-01",
        full_name=p_spec.get("name", "Pat Principal"),
    )
    counterparty = User.objects.create_user(
        email=f"eval-{ns}-counter@polaris.eval",
        password="pw-evaluation-01",
        full_name=spec.get("counterparty_name", "Casey Counterparty"),
    )
    UserProfile.objects.filter(user=principal).update(
        auto_reply_when_away=True,
        agent_autonomy=p_spec.get("autonomy", "auto_send"),
        agent_instructions=p_spec.get("agent_instructions", ""),
    )

    listing = None
    if stance == "sell_side":
        listing = _make_listing(principal, spec, ns=ns, prop_ids=prop_ids)
        Mandate.objects.create(listing=listing, **_mandate_kwargs(spec.get("mandate")))
    elif stance == "buy_side":
        listing = _make_listing(counterparty, spec, ns=ns, prop_ids=prop_ids)
        if spec.get("counterparty_mandate"):
            Mandate.objects.create(listing=listing, **_mandate_kwargs(spec["counterparty_mandate"]))
        box = BuyBox.objects.create(
            buyer=principal,
            name=f"{ns} eval box",
            is_active=True,
            is_primary=True,
            strategy=spec.get("strategy", "buy_hold"),
        )
        Mandate.objects.create(buy_box=box, **_mandate_kwargs(spec.get("mandate")))
    # neutral: no listing, no attachment -> focal is None -> stance neutral.

    attach = bool(listing) if spec.get("attach_listing") is None else spec.get("attach_listing")
    attach_ids = [listing.id] if (listing and attach) else []

    chat, _ = services.get_or_create_chat(principal.id, counterparty.id)
    inbound = services.post_human_message(
        chat.id, counterparty.id, spec["inbound"], attachment_listing_ids=attach_ids
    )

    return ResponderSeed(
        chat_id=chat.id,
        inbound_id=inbound["id"],
        principal_id=principal.id,
        counterparty_id=counterparty.id,
        listing_id=listing.id if listing else None,
        _user_ids=[principal.id, counterparty.id],
        _property_ids=prop_ids,
    )
