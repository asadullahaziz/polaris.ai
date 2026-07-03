"""
Email seam — `send_verification_email` / `send_password_reset_email`.

One place builds the SPA link and sends via Django's configured backend
(SendGrid SMTP in dev/prod; locmem under the pytest test runner). Swapping the
transport is a settings change; callers never see it.
"""

from __future__ import annotations

from django.conf import settings
from django.core.mail import send_mail

from .tokens import make_email_verify_token, make_password_reset_token


def _link(path: str, token: str) -> str:
    base = settings.FRONTEND_BASE_URL.rstrip("/")
    return f"{base}{path}?token={token}"


def send_verification_email(user) -> None:
    token = make_email_verify_token(user)
    link = _link("/verify", token)
    send_mail(
        subject="Verify your Polaris AI email",
        message=(
            f"Welcome to Polaris AI.\n\n"
            f"Confirm your email to activate your account:\n{link}\n\n"
            f"This link expires in 3 days. If you didn't sign up, ignore this email."
        ),
        from_email=settings.DEFAULT_FROM_EMAIL,
        recipient_list=[user.email],
        fail_silently=False,
    )


def send_password_reset_email(user) -> None:
    token = make_password_reset_token(user)
    link = _link("/reset", token)
    send_mail(
        subject="Reset your Polaris AI password",
        message=(
            f"We received a request to reset your Polaris AI password.\n\n"
            f"Set a new password:\n{link}\n\n"
            f"This link expires in 1 hour. If you didn't request this, ignore this email — "
            f"your password is unchanged."
        ),
        from_email=settings.DEFAULT_FROM_EMAIL,
        recipient_list=[user.email],
        fail_silently=False,
    )
