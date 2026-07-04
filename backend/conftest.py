"""Shared pytest fixtures."""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _clear_caches():
    """Clear the (locmem) cache before each test so DRF scoped throttles don't
    bleed request counts across tests in a single run."""
    from django.core.cache import cache

    cache.clear()
    yield
    cache.clear()


@pytest.fixture
def reset_checkpointer(settings):
    """
    Point the shared checkpointer pool at the database Django is currently using
    (the pytest-django test DB) and clear the module-level cache before/after,
    so each test gets a fresh pool bound to the right DB.
    """
    from django.db import connection

    d = connection.settings_dict
    settings.CHECKPOINTER_DB_URL = (
        f"postgresql://{d['USER']}:{d['PASSWORD']}@{d['HOST']}:{d['PORT']}/{d['NAME']}"
    )

    import polaris_agent.checkpointer as cp

    cp._pool = None
    cp._checkpointer = None
    yield
    cp._pool = None
    cp._checkpointer = None
