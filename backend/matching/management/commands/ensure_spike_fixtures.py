"""Idempotently seed a few King-County-area SpikePoints so the P0 geo query
returns a deterministic, non-zero count. Called from the entrypoint after
migrate. Removed with SpikePoint in P1.
"""

from __future__ import annotations

from django.contrib.gis.geos import Point
from django.core.management.base import BaseCommand

from matching.models import SpikePoint

# (name, lon, lat) — a few real-ish King County spots.
_POINTS = [
    ("Downtown Seattle", -122.3351, 47.6080),
    ("Bellevue", -122.2015, 47.6101),
    ("Kirkland", -122.2087, 47.6769),
    ("Renton", -122.2170, 47.4829),
    ("Tacoma (outside 50km)", -122.4443, 47.2529),
]


class Command(BaseCommand):
    help = "Ensure P0 SpikePoint geo fixtures exist (idempotent)."

    def handle(self, *args, **options):
        created = 0
        for name, lon, lat in _POINTS:
            _, was_created = SpikePoint.objects.get_or_create(
                name=name,
                defaults={"geom": Point(lon, lat, srid=4326)},
            )
            created += int(was_created)
        self.stdout.write(
            self.style.SUCCESS(
                f"spike fixtures ready: {SpikePoint.objects.count()} points "
                f"({created} created this run)"
            )
        )
