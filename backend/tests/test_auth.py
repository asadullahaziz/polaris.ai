"""
P0 auth gate — the email-login lifecycle, all offline (locmem email backend the
pytest test runner installs automatically; `mail.outbox` captures sends).

Coverage:
  * UserManager: create_user normalizes/lowercases email, hashes password,
    creates a profile; create_superuser sets the flags + pre-verifies.
  * register -> verify -> login (token pulled from the sent email).
  * unverified login is gated (403).
  * duplicate email (any case) is rejected at register.
  * resend re-issues a verification email; always 200 (no account leak).
  * password reset -> confirm -> login with the NEW password; the reset token
    is single-use (a second confirm fails).
"""

from __future__ import annotations

import re

import pytest
from django.contrib.auth import get_user_model
from django.core import mail
from rest_framework.test import APIClient

User = get_user_model()

REGISTER = "/api/auth/register/"
VERIFY = "/api/auth/verify/"
RESEND = "/api/auth/resend/"
LOGIN = "/api/auth/login/"
ME = "/api/auth/me/"
RESET = "/api/auth/password/reset/"
RESET_CONFIRM = "/api/auth/password/reset/confirm/"

_TOKEN_RE = re.compile(r"token=([^\s]+)")


def _token_from_last_email() -> str:
    assert mail.outbox, "expected an email to have been sent"
    match = _TOKEN_RE.search(mail.outbox[-1].body)
    assert match, f"no token link in email body:\n{mail.outbox[-1].body}"
    return match.group(1)


@pytest.fixture
def client() -> APIClient:
    return APIClient()


# --- manager ------------------------------------------------------------------
@pytest.mark.django_db
def test_create_user_normalizes_and_hashes():
    user = User.objects.create_user(email="Alice@Example.COM", password="s3cret-pass-99")
    assert user.email == "alice@example.com"  # fully lowercased
    assert user.password != "s3cret-pass-99"  # hashed, not stored raw
    assert user.check_password("s3cret-pass-99")
    assert user.is_email_verified is False
    # The post_save signal created the companion profile.
    assert user.profile is not None
    assert user.profile.agent_autonomy == "auto_send"
    assert user.profile.auto_reply_when_away is True


@pytest.mark.django_db
def test_create_superuser_flags():
    admin = User.objects.create_superuser(email="admin@example.com", password="admin-pass-123")
    assert admin.is_staff and admin.is_superuser
    assert admin.is_email_verified is True


@pytest.mark.django_db
def test_create_user_requires_email():
    with pytest.raises(ValueError):
        User.objects.create_user(email="", password="x")


# --- register -> verify -> login ---------------------------------------------
@pytest.mark.django_db
def test_register_verify_login_flow(client):
    resp = client.post(
        REGISTER,
        {"email": "buyer@example.com", "password": "gr8-passw0rd", "full_name": "Buyer One"},
        format="json",
    )
    assert resp.status_code == 201
    user = User.objects.get(email="buyer@example.com")
    assert user.is_email_verified is False
    assert len(mail.outbox) == 1

    # Login before verifying is gated.
    gated = client.post(
        LOGIN, {"email": "buyer@example.com", "password": "gr8-passw0rd"}, format="json"
    )
    assert gated.status_code == 403
    assert gated.data.get("code") == "email_unverified"

    # Verify with the emailed token.
    token = _token_from_last_email()
    verified = client.post(VERIFY, {"token": token}, format="json")
    assert verified.status_code == 200
    user.refresh_from_db()
    assert user.is_email_verified is True

    # Now login works and sets a session; /me returns the user + nested profile.
    ok = client.post(
        LOGIN, {"email": "buyer@example.com", "password": "gr8-passw0rd"}, format="json"
    )
    assert ok.status_code == 200
    assert ok.data["email"] == "buyer@example.com"

    me = client.get(ME)
    assert me.status_code == 200
    assert me.data["email"] == "buyer@example.com"
    assert "profile" in me.data
    assert me.data["profile"]["agent_autonomy"] == "auto_send"


@pytest.mark.django_db
def test_login_case_insensitive_email(client):
    User.objects.create_user(
        email="mixed@example.com", password="gr8-passw0rd", is_email_verified=True
    )
    resp = client.post(
        LOGIN, {"email": "MIXED@Example.com", "password": "gr8-passw0rd"}, format="json"
    )
    assert resp.status_code == 200


@pytest.mark.django_db
def test_duplicate_email_rejected_any_case(client):
    User.objects.create_user(email="dup@example.com", password="gr8-passw0rd")
    resp = client.post(
        REGISTER, {"email": "DUP@example.com", "password": "another-pass-1"}, format="json"
    )
    assert resp.status_code == 400
    assert "email" in resp.data


@pytest.mark.django_db
def test_bad_token_rejected(client):
    resp = client.post(VERIFY, {"token": "not-a-real-token"}, format="json")
    assert resp.status_code == 400


# --- resend -------------------------------------------------------------------
@pytest.mark.django_db
def test_resend_always_200_and_resends_for_unverified(client):
    User.objects.create_user(email="pending@example.com", password="gr8-passw0rd")
    mail.outbox.clear()

    resp = client.post(RESEND, {"email": "pending@example.com"}, format="json")
    assert resp.status_code == 200
    assert len(mail.outbox) == 1

    # Unknown account: still 200, but no email leaks its (non)existence.
    mail.outbox.clear()
    resp = client.post(RESEND, {"email": "nobody@example.com"}, format="json")
    assert resp.status_code == 200
    assert len(mail.outbox) == 0


# --- password reset -----------------------------------------------------------
@pytest.mark.django_db
def test_password_reset_confirm_and_single_use(client):
    User.objects.create_user(
        email="reset@example.com", password="old-passw0rd-1", is_email_verified=True
    )
    mail.outbox.clear()

    # Request reset — always 200; email carries the token.
    resp = client.post(RESET, {"email": "reset@example.com"}, format="json")
    assert resp.status_code == 200
    assert len(mail.outbox) == 1
    token = _token_from_last_email()

    # Confirm sets the new password.
    confirm = client.post(
        RESET_CONFIRM, {"token": token, "new_password": "brand-new-pass-2"}, format="json"
    )
    assert confirm.status_code == 200

    # Old password no longer works; new one does.
    assert (
        client.post(
            LOGIN, {"email": "reset@example.com", "password": "old-passw0rd-1"}, format="json"
        ).status_code
        == 401
    )
    assert (
        client.post(
            LOGIN, {"email": "reset@example.com", "password": "brand-new-pass-2"}, format="json"
        ).status_code
        == 200
    )

    # The same token cannot be replayed (fingerprint changed after set_password).
    replay = client.post(
        RESET_CONFIRM, {"token": token, "new_password": "yet-another-3"}, format="json"
    )
    assert replay.status_code == 400


@pytest.mark.django_db
def test_password_reset_unknown_email_is_silent(client):
    mail.outbox.clear()
    resp = client.post(RESET, {"email": "ghost@example.com"}, format="json")
    assert resp.status_code == 200
    assert len(mail.outbox) == 0


# --- profile + AI settings (settings page; read by the copilot/responder) ------
@pytest.mark.django_db
def test_patch_me_updates_profile_and_ai_settings(client):
    user = User.objects.create_user(
        email="owner@example.com", password="gr8-passw0rd", is_email_verified=True
    )
    client.force_authenticate(user=user)

    resp = client.patch(
        ME,
        {
            "full_name": "Owner Renamed",
            "auto_reply_when_away": True,
            "agent_autonomy": "auto_send",
            "agent_instructions": "Be concise; never go below asking.",
            "company": "Acme Holdings",
        },
        format="json",
    )
    assert resp.status_code == 200
    assert resp.data["full_name"] == "Owner Renamed"
    assert resp.data["profile"]["auto_reply_when_away"] is True
    assert resp.data["profile"]["agent_autonomy"] == "auto_send"
    assert resp.data["profile"]["agent_instructions"] == "Be concise; never go below asking."
    assert resp.data["profile"]["company"] == "Acme Holdings"

    user.refresh_from_db()
    assert user.full_name == "Owner Renamed"
    assert user.profile.auto_reply_when_away is True


@pytest.mark.django_db
def test_password_change_requires_current(client):
    user = User.objects.create_user(
        email="changer@example.com", password="old-passw0rd-1", is_email_verified=True
    )
    client.force_authenticate(user=user)
    CHANGE = "/api/auth/password/change/"

    # Wrong current password is rejected.
    bad = client.post(
        CHANGE, {"current_password": "wrong", "new_password": "new-passw0rd-2"}, format="json"
    )
    assert bad.status_code == 400

    # Correct current password rotates it.
    ok = client.post(
        CHANGE,
        {"current_password": "old-passw0rd-1", "new_password": "new-passw0rd-2"},
        format="json",
    )
    assert ok.status_code == 200
    user.refresh_from_db()
    assert user.check_password("new-passw0rd-2")
