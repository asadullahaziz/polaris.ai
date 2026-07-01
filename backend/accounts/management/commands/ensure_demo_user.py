"""Idempotently ensure a demo login exists so the P0 spike page can authenticate.

Dev/demo convenience only — called from the container entrypoint after migrate.
Credentials come from env (DEMO_USER / DEMO_PASSWORD / DEMO_EMAIL).
"""

from __future__ import annotations

import os

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Ensure a demo user exists (idempotent; dev/demo only)."

    def handle(self, *args, **options):
        User = get_user_model()
        username = os.environ.get("DEMO_USER", "demo")
        password = os.environ.get("DEMO_PASSWORD", "demo12345")
        email = os.environ.get("DEMO_EMAIL", "demo@polaris.local")

        user, created = User.objects.get_or_create(username=username, defaults={"email": email})
        user.email = email
        user.set_password(password)
        # Superuser so /admin is usable in the demo; harmless in dev.
        user.is_staff = True
        user.is_superuser = True
        user.save()
        self.stdout.write(self.style.SUCCESS(f"demo user '{username}' ready (created={created})"))
