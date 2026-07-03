"""
P1 seed verification — runs the real seed_kc to lock in idempotency + the date rebase.

Loads the full ~20k-row KC CSV, so it is the slow test in the suite; kept minimal.
"""

from __future__ import annotations

import datetime as dt

import pytest
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.utils import timezone

from catalog.models import Property, Sale

User = get_user_model()


@pytest.mark.django_db
def test_seed_kc_idempotent_and_date_rebased():
    call_command("seed_kc")
    n_prop = Property.objects.filter(county_fips="53033").count()
    n_sale = Sale.objects.count()
    assert n_prop > 20_000
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
def test_seed_kc_reset_rebuilds():
    call_command("seed_kc")
    before = Sale.objects.count()
    call_command("seed_kc", "--reset")
    # After reset+reseed the behavioral layer is rebuilt (same deterministic count).
    assert Sale.objects.count() == before
