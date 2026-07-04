"""Idempotently ensure a verified demo login exists (dev/demo only).

Called from the container entrypoint after migrate. Email-login (v2), so the
credential is DEMO_EMAIL / DEMO_PASSWORD.
"""

from __future__ import annotations

import os

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Ensure a verified demo user exists (idempotent; dev/demo only)."

    def handle(self, *args, **options):
        User = get_user_model()
        email = os.environ.get("DEMO_EMAIL", "demo@polaris.local").strip().lower()
        password = os.environ.get("DEMO_PASSWORD", "demo12345")

        user, created = User.objects.get_or_create(email=email, defaults={"full_name": "Demo User"})
        user.set_password(password)
        user.is_email_verified = True
        # Superuser so /admin is usable in the demo; harmless in dev.
        user.is_staff = True
        user.is_superuser = True
        user.save()
        self.stdout.write(self.style.SUCCESS(f"demo user '{email}' ready (created={created})"))
