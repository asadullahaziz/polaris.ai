#!/usr/bin/env bash
set -euo pipefail

# Compose gates start-up on postgres/redis healthchecks (depends_on:
# service_healthy), so the DB is ready by the time we migrate.

# Migrations are generated in-container because Django (GDAL) can't run on the
# host. This keeps `docker compose up` reproducible from a fresh clone.
echo "[entrypoint] makemigrations"
python manage.py makemigrations --noinput

echo "[entrypoint] migrate"
python manage.py migrate --noinput

echo "[entrypoint] ensure demo user"
python manage.py ensure_demo_user

# Demo data (idempotent; re-runs are a no-op): the Kessler County world — ~3.2k
# comps with resolvable addresses + ~40 archetype personas + ~15 active listings.
# Non-fatal so a hiccup never blocks bring-up.
echo "[entrypoint] seed Kessler County demo data"
python manage.py seed_kc || echo "[entrypoint] seed_kc failed (non-fatal); run 'make seed' manually"

# Local staticfiles (admin/DRF UI). Never blocks bring-up.
python manage.py collectstatic --noinput >/dev/null 2>&1 || true

echo "[entrypoint] starting: $*"
exec "$@"
