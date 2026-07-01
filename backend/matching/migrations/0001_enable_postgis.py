"""
P0.5 — enable the PostGIS extension via a migration (not left implicit to the
image), so `migrate` is the single source of truth for DB setup.

`CreateExtension` issues `CREATE EXTENSION IF NOT EXISTS postgis`, so it is a
no-op when the postgis/postgis image has already created it. This migration is
the dependency anchor every geometry/geography table (P1+) declares.
"""

from django.contrib.postgres.operations import CreateExtension
from django.db import migrations


class Migration(migrations.Migration):
    initial = True

    dependencies: list = []

    operations = [
        CreateExtension("postgis"),
    ]
