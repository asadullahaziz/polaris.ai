#!/usr/bin/env bash
set -euo pipefail

# Compose gates start-up on postgres/redis healthchecks (depends_on:
# service_healthy), so the DB is ready by the time we migrate.

# v2 NOTE: migrations are generated in-container here because Django (GDAL) can't
# run on the host. This keeps `docker compose up` reproducible from a fresh clone.
# Each phase commits its migrations and reviews them against the schema.
echo "[entrypoint] makemigrations"
python manage.py makemigrations --noinput

echo "[entrypoint] migrate"
python manage.py migrate --noinput

echo "[entrypoint] ensure demo user"
python manage.py ensure_demo_user

# P1+ seeds (King County demo data) land once the catalog app exists.

# Local staticfiles (admin/DRF UI). Never blocks bring-up.
python manage.py collectstatic --noinput >/dev/null 2>&1 || true

echo "[entrypoint] starting: $*"
exec "$@"
