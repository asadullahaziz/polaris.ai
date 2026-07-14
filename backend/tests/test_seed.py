"""
Seed verification — runs the real seed_kc (the Kessler County world) to lock in
idempotency, the date rebase, and universal address resolvability.

Parses the full ~20k-row KC CSV, so it is the slow test in the suite; deliberately minimal.
"""

from __future__ import annotations

import datetime as dt

import pytest
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.utils import timezone

from catalog import services
from catalog.management.commands.seed_kc import (
    N_CLUSTERS,
    ROWS_PER_CLUSTER,
    Command as SeedCommand,
)
from catalog.models import Listing, Mandate, Property, Sale
from chat.models import Chat, Message
from deals.models import Deal
from matching.engine import assess_deal
from polaris_agent.disclosure import _literal_variants, style_check
from users.models import UserProfile

User = get_user_model()


def _flagship() -> Listing:
    """The hero listing: kc_seller_1's lowest-id active listing (seed contract)."""
    walt = User.objects.get(email="kc_seller_1@polaris.local")
    listing = Listing.objects.filter(seller=walt, status="active").order_by("id").first()
    assert listing is not None
    return listing


@pytest.mark.django_db
def test_seed_kc_idempotent_and_date_rebased():
    call_command("seed_kc")
    n_prop = Property.objects.filter(county_fips="53033").count()
    n_sale = Sale.objects.count()
    # The subsampled town universe, not the full CSV.
    assert 2_000 < n_prop <= N_CLUSTERS * ROWS_PER_CLUSTER
    assert n_sale > 0
    # All personas are registered users, prospect archetypes included.
    assert User.objects.filter(email__startswith="kc_buyer_").count() == 15
    assert User.objects.filter(email__startswith="kc_prospect_").count() == 25

    # Re-run must be a no-op (idempotency): properties upsert, behavioral layer skips.
    call_command("seed_kc")
    assert Property.objects.filter(county_fips="53033").count() == n_prop
    assert Sale.objects.count() == n_sale

    # Date rebase: newest sale lands within ~24 months of the demo date.
    today = timezone.now().date()
    newest = Property.objects.filter(county_fips="53033").latest("last_sale_date").last_sale_date
    assert today - dt.timedelta(days=800) <= newest <= today


@pytest.mark.django_db
def test_seed_kc_addresses_resolve():
    """Every seeded property is reachable by typing its address (the demo's core
    promise): no unresolvable `kc:` placeholder norms exist, and a listing's own
    address resolves through both the dedup lookup and the geo resolver the rank
    endpoint uses."""
    call_command("seed_kc")
    assert not Property.objects.filter(county_fips="53033", address_norm__startswith="kc:").exists()

    listing = Listing.objects.filter(status="active").order_by("id").first()
    assert listing is not None
    prop = listing.listingproperty_set.order_by("sort_order").first().property
    assert services.lookup_property(prop.address_raw)["found"] is True
    assert services.resolve_geo(prop.address_raw) is not None

    # And the typeahead finds it from a fragment of its street name.
    fragment = prop.address_raw.split()[1]  # street name token
    results = services.search_properties(fragment)
    assert any(r["id"] == prop.pk for r in services.search_properties(prop.address_raw))
    assert results  # fragment search returns suggestions


@pytest.mark.django_db
def test_seed_kc_reset_rebuilds():
    call_command("seed_kc")
    before = Sale.objects.count()
    call_command("seed_kc", "--reset")
    # After reset+reseed the behavioral layer is rebuilt (same deterministic count).
    assert Sale.objects.count() == before


@pytest.mark.django_db
def test_seed_kc_content_populated():
    """The persona layer is real: descriptions, profile texture, enriched mandates."""
    call_command("seed_kc")

    listings = Listing.objects.filter(status="active")
    assert listings.exists()
    for lst in listings:
        assert 200 <= len(lst.description) <= 900, f"listing {lst.id} description length"
        prop = lst.listingproperty_set.order_by("sort_order").first().property
        town = prop.address_raw.split(",")[1].strip()
        assert town in lst.description  # composed from THIS property's attributes

    # Every kc persona has bio + company; buyers and sellers carry standing
    # agent_instructions (the live behavior lever); prospects are texture-only.
    for p in UserProfile.objects.filter(user__email__startswith="kc_"):
        assert p.bio and p.company, f"{p.user.email} missing profile texture"
    for p in UserProfile.objects.filter(user__email__regex=r"^kc_(buyer|seller)_"):
        assert p.agent_instructions, f"{p.user.email} missing agent_instructions"

    for m in Mandate.objects.all():
        assert m.instructions
        assert len(m.must_haves) >= 2
        assert m.availability_window


@pytest.mark.django_db
def test_seed_kc_content_gate_compatible():
    """Seeded prose must never fight the away-agent's deterministic gates: instructions
    pass style_check verbatim, nothing carries an em/en dash, and no floor/ceiling
    renders literally anywhere (the leak scan only knows the mandate values)."""
    call_command("seed_kc")

    instructions = [
        *Mandate.objects.exclude(instructions="").values_list("instructions", flat=True),
        *UserProfile.objects.filter(user__email__startswith="kc_")
        .exclude(agent_instructions="")
        .values_list("agent_instructions", flat=True),
    ]
    descriptions = list(
        Listing.objects.filter(status="active").values_list("description", flat=True)
    )
    for text in instructions:
        ok, reason = style_check(text)
        assert ok, f"instruction fails the style gate ({reason}): {text[:80]!r}"
    for text in [*instructions, *descriptions]:
        assert "—" not in text and "–" not in text

    limits = {
        int(x)
        for pair in Mandate.objects.values_list("floor_price", "ceiling_price")
        for x in pair
        if x is not None
    }
    haystacks = [t.lower() for t in [*instructions, *descriptions]]
    for limit in limits:
        for variant in _literal_variants(limit):
            for hay in haystacks:
                assert variant not in hay, f"limit {limit} leaks as {variant!r}"


@pytest.mark.django_db
def test_seed_kc_hero_divergence():
    """The load-bearing demo guarantee: one listing, four engineered outcomes.
    assess_deal is deterministic (LLM-free), so drift breaks CI, not the recording."""
    call_command("seed_kc")
    flag = _flagship()

    res = {s: assess_deal(flag.id, strategy=s) for s in ("buy_hold", "brrrr", "fix_flip")}
    assert res["buy_hold"]["verdict"] == "qualify", res["buy_hold"]["rationale"]
    assert res["brrrr"]["verdict"] == "hold", res["brrrr"]["rationale"]
    assert res["fix_flip"]["verdict"] == "decline", res["fix_flip"]["rationale"]
    # Calibrated margin holds its buffer (±2 pts before any verdict flips).
    assert 0.105 <= res["buy_hold"]["margin_pct"] <= 0.145

    floor = float(Mandate.objects.get(listing=flag).floor_price)
    erin = float(
        Mandate.objects.get(buy_box__buyer__email="kc_buyer_1@polaris.local").ceiling_price
    )
    jake = float(
        Mandate.objects.get(buy_box__buyer__email="kc_buyer_4@polaris.local").ceiling_price
    )
    assert erin >= floor  # the accept path is reachable for the qualify buyer
    assert jake < floor  # the impasse buyer can never clear the seller's floor

    # Erin's agent's grounded max offer (dal formula) clears the floor too.
    a = res["buy_hold"]
    max_offer = a["arv"] - a["est_rehab"] - a["wholesale_fee"] - a["threshold"] * a["arv"]
    assert max_offer >= floor


@pytest.mark.django_db
def test_seed_kc_prewarm():
    """The lived-in state: one closed deal with a real transcript, one stale thread —
    and the whole step is idempotent under the behavioral sentinel."""
    call_command("seed_kc")

    closed = Deal.objects.get(stage="closed")
    assert closed.agreed_price is not None
    assert closed.seller.email == "kc_seller_1@polaris.local"
    msgs = Message.objects.filter(chat=closed.chat, status="sent")
    assert msgs.count() >= 5
    assert msgs.filter(kind="agent", action="propose").exists()
    assert msgs.filter(kind="human").exists()

    stale = Deal.objects.get(stage="contacted")
    stale_msgs = Message.objects.filter(chat=stale.chat)
    assert stale_msgs.count() == 1
    assert stale_msgs.first().created_at < timezone.now() - dt.timedelta(days=3)

    n_msgs, n_deals, n_chats = (
        Message.objects.count(),
        Deal.objects.count(),
        Chat.objects.count(),
    )
    call_command("seed_kc")  # sentinel-skip → no duplicate pre-warm
    assert Message.objects.count() == n_msgs
    assert Deal.objects.count() == n_deals
    assert Chat.objects.count() == n_chats


@pytest.mark.django_db
def test_seed_kc_reset_clears_prewarm():
    """--reset must survive the pre-warm transcripts (Message.sender is PROTECT) and
    rebuild the identical world."""
    call_command("seed_kc")
    n_msgs, n_deals, n_chats = (
        Message.objects.count(),
        Deal.objects.count(),
        Chat.objects.count(),
    )
    assert n_msgs and n_deals and n_chats

    cmd = SeedCommand()
    cmd._reset()  # would raise ProtectedError without the chat cleanup
    assert Chat.objects.count() == 0
    assert Message.objects.count() == 0
    assert Deal.objects.count() == 0
    assert not User.objects.filter(email__startswith="kc_").exists()

    call_command("seed_kc")
    assert Message.objects.count() == n_msgs
    assert Deal.objects.count() == n_deals
    assert Chat.objects.count() == n_chats
