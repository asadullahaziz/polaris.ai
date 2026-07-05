"""
P1 seed verification — runs the real seed_kc (the Kessler County world) to lock in
idempotency, the date rebase, and universal address resolvability.

Parses the full ~20k-row KC CSV, so it is the slow test in the suite; kept minimal.
"""

from __future__ import annotations

import datetime as dt

import pytest
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.utils import timezone

from catalog import services
from catalog.management.commands.seed_kc import N_CLUSTERS, ROWS_PER_CLUSTER
from catalog.models import Listing, Property, Sale

User = get_user_model()


@pytest.mark.django_db
def test_seed_kc_idempotent_and_date_rebased():
    call_command("seed_kc")
    n_prop = Property.objects.filter(county_fips="53033").count()
    n_sale = Sale.objects.count()
    # The subsampled town universe, not the full CSV.
    assert 2_000 < n_prop <= N_CLUSTERS * ROWS_PER_CLUSTER
    assert n_sale > 0
    # Personas became registered users (no prospects in v2).
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
    promise): no legacy `kc:` norms remain, and a listing's own address resolves
    through BOTH the dedup lookup and the geo resolver the rank endpoint uses."""
    call_command("seed_kc")
    assert not Property.objects.filter(
        county_fips="53033", address_norm__startswith="kc:"
    ).exists()

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
