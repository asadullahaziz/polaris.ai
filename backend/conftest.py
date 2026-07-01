"""Shared pytest fixtures for the P0 spike tests."""

from __future__ import annotations

import pytest


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
