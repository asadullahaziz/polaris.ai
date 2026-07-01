"""
Thin data-access layer (P0.8 rationale / §2): the agent package reaches Django
models only through here, wrapping sync ORM calls with `sync_to_async` so they
are safe to call from async graphs/consumers. Keeps `polaris_agent`
import-isolated from views and keeps the ORM boundary in one place.

P0 exposes just the spike geo query; P1+ adds the real read/write helpers.
"""

from __future__ import annotations

from asgiref.sync import sync_to_async


def _count_points_within_km(lon: float, lat: float, km: float) -> int:
    # Imported lazily so this module stays importable before Django apps load.
    from django.contrib.gis.geos import Point
    from django.contrib.gis.measure import D

    from matching.models import SpikePoint

    center = Point(lon, lat, srid=4326)
    return SpikePoint.objects.filter(geom__dwithin=(center, D(km=km))).count()


#: Async geo query used by the spike consumer — proves ORM ST_DWithin works.
count_points_within_km = sync_to_async(_count_points_within_km)
