#!/usr/bin/env bash
set -euo pipefail

# Compose gates start-up on postgres/redis healthchecks (depends_on:
# service_healthy), so the DB is ready by the time we migrate.

# P0 NOTE: migrations are generated in-container here because Django (GDAL) can't
# run on the host. This keeps `docker compose up` reproducible from a fresh clone.
# P1.1 commits these migrations and reviews them 1:1 against the DDL.
echo "[entrypoint] makemigrations"
python manage.py makemigrations --noinput

echo "[entrypoint] migrate"
python manage.py migrate --noinput

echo "[entrypoint] ensure demo user + P0 spike geo fixtures"
python manage.py ensure_demo_user
python manage.py ensure_spike_fixtures

# P1 demo data (idempotent; re-runs are a no-op). A fresh clone reproduces the
# identical demo: ~20k KC comps + synthetic personas + active listings.
echo "[entrypoint] seed King County demo data"
python manage.py seed_kc || echo "[entrypoint] seed_kc failed (non-fatal); run 'make seed' manually"

# Local staticfiles (admin/DRF UI). Never blocks bring-up.
python manage.py collectstatic --noinput >/dev/null 2>&1 || true

echo "[entrypoint] starting: $*"
exec "$@"
