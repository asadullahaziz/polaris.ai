"""
Hand-rolled signed tokens for email verification + password reset.

Both use `django.core.signing` (HMAC over SECRET_KEY) with distinct salts so a
verification token can never be replayed as a reset token. The reset token is
bound to a fingerprint of the current password hash, so it is single-use: once
`set_password` runs the fingerprint changes and the old token no longer verifies.
"""

from __future__ import annotations

import hashlib

from django.core import signing

_VERIFY_SALT = "users.email-verify"
_RESET_SALT = "users.password-reset"


class TokenError(Exception):
    """Raised for any invalid/expired/tampered token (callers map to 400)."""


def _password_fingerprint(user) -> str:
    """Short, stable fingerprint of the current password hash (single-use reset)."""
    return hashlib.sha256((user.password or "").encode()).hexdigest()[:16]


# --- email verification -------------------------------------------------------
def make_email_verify_token(user) -> str:
    return signing.dumps({"uid": user.pk}, salt=_VERIFY_SALT)


def read_email_verify_token(token: str, max_age: int) -> int:
    try:
        data = signing.loads(token, salt=_VERIFY_SALT, max_age=max_age)
        return int(data["uid"])
    except (signing.BadSignature, signing.SignatureExpired, KeyError, ValueError, TypeError) as exc:
        raise TokenError("Invalid or expired verification token.") from exc


# --- password reset (single-use via password-hash fingerprint) ----------------
def make_password_reset_token(user) -> str:
    return signing.dumps({"uid": user.pk, "fp": _password_fingerprint(user)}, salt=_RESET_SALT)


def read_password_reset_token(token: str, max_age: int) -> dict:
    try:
        data = signing.loads(token, salt=_RESET_SALT, max_age=max_age)
        int(data["uid"])  # shape check
        str(data["fp"])
        return data
    except (signing.BadSignature, signing.SignatureExpired, KeyError, ValueError, TypeError) as exc:
        raise TokenError("Invalid or expired reset token.") from exc


def reset_token_matches(user, data: dict) -> bool:
    """True iff the reset token was issued for the user's CURRENT password hash."""
    return data.get("fp") == _password_fingerprint(user)
