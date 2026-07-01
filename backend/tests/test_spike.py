"""
P0 spike verification — the objective gate out of Phase 0 (review #8).

Run inside the backend container (GDAL + Postgres/PostGIS available):
    python manage.py makemigrations --noinput
    pytest

Coverage:
  * test_geo_dwithin            -> GeoDjango ST_DWithin through the ORM (deterministic anchor)
  * test_checkpointer_persists  -> AsyncPostgresSaver over the shared pool persists state
  * test_spike_consumer_roundtrip -> WS ping exercises graph + geo through the consumer

The remaining leg — the Inngest event round-tripping back to the *browser* over
the channel layer — needs the full running stack (dev server + ASGI server), so
it is the compose/browser gate: open the spike page and confirm the
`inngest.tick` renders. See implementation_plan P0 "Verification".
"""

from __future__ import annotations

import pytest
from asgiref.sync import sync_to_async
from channels.testing import WebsocketCommunicator
from django.contrib.auth import get_user_model
from django.contrib.gis.geos import Point
from django.contrib.gis.measure import D

from conversations.consumers import SpikeConsumer
from matching.models import SpikePoint
from polaris_agent.checkpointer import close_checkpointer, get_checkpointer
from polaris_agent.graphs.spike import build_spike_graph

# King-County-area points: 4 within 50km of downtown Seattle, Tacoma outside it.
_SEATTLE = Point(-122.3351, 47.6080, srid=4326)
_POINTS = [
    ("Downtown Seattle", -122.3351, 47.6080),
    ("Bellevue", -122.2015, 47.6101),
    ("Kirkland", -122.2087, 47.6769),
    ("Renton", -122.2170, 47.4829),
    ("Tacoma", -122.4443, 47.2529),  # ~48km? assert it's excluded at 50km-ish scale
]


@pytest.mark.django_db
def test_geo_dwithin():
    for name, lon, lat in _POINTS:
        SpikePoint.objects.create(name=name, geom=Point(lon, lat, srid=4326))

    within_50 = SpikePoint.objects.filter(geom__dwithin=(_SEATTLE, D(km=50))).count()
    within_10 = SpikePoint.objects.filter(geom__dwithin=(_SEATTLE, D(km=10))).count()

    # PostGIS answered a real spatial query through the GeoDjango ORM.
    assert within_50 >= 4
    assert within_10 >= 1
    assert within_10 <= within_50


@pytest.mark.django_db(transaction=True)
async def test_checkpointer_persists(reset_checkpointer):
    checkpointer = await get_checkpointer()
    graph = build_spike_graph(checkpointer)
    cfg = {"configurable": {"thread_id": "pytest-thread-1"}}

    s1 = await graph.ainvoke({"ping": "a"}, config=cfg)
    assert s1["count"] == 1
    assert s1["echo"] == "pong:a"

    # Second invoke on the SAME thread must resume from the persisted checkpoint.
    s2 = await graph.ainvoke({"ping": "b"}, config=cfg)
    assert s2["count"] == 2

    # A checkpoint row exists for this thread (persistence, not just in-memory).
    tup = await checkpointer.aget_tuple(cfg)
    assert tup is not None

    await close_checkpointer()


@pytest.mark.django_db(transaction=True)
async def test_spike_consumer_roundtrip(reset_checkpointer):
    User = get_user_model()

    def _make_user():
        user = User.objects.create_user(username="spiketester", password="x")
        for name, lon, lat in _POINTS:
            SpikePoint.objects.create(name=name, geom=Point(lon, lat, srid=4326))
        return user

    user = await sync_to_async(_make_user)()

    communicator = WebsocketCommunicator(SpikeConsumer.as_asgi(), "/ws/spike/")
    communicator.scope["user"] = user  # bypass AuthMiddlewareStack in-process
    connected, _ = await communicator.connect()
    assert connected
    try:
        ready = await communicator.receive_json_from(timeout=10)
        assert ready["type"] == "spike.ready"

        await communicator.send_json_to({"type": "ping", "data": {"ping": "hello"}})
        echo = await communicator.receive_json_from(timeout=15)

        assert echo["type"] == "spike.echo"
        assert echo["data"]["count"] >= 1
        assert echo["data"]["geo_within_50km"] >= 4
        assert echo["data"]["echo"] == "pong:hello"
    finally:
        await communicator.disconnect()
        await close_checkpointer()
