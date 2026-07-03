"""
Session-cookie auth + account lifecycle (v2 auth design).

  GET  /api/auth/csrf              -> primes the `csrftoken` cookie
  POST /api/auth/register          -> create account (unverified) + email a token
  POST /api/auth/verify            -> {token} marks the email verified
  POST /api/auth/resend            -> {email} re-sends verification (rate-limited)
  POST /api/auth/login             -> {email,password} gate on verified, set session
  POST /api/auth/logout            -> clears the session
  GET  /api/auth/me                -> the current user + profile (401 if anon)
  PATCH/api/auth/me                -> update own profile + AI settings
  POST /api/auth/password/change   -> {current_password,new_password} (authenticated)
  POST /api/auth/password/reset    -> {email} always 200; emails a token iff exists
  POST /api/auth/password/reset/confirm -> {token,new_password} -> set_password

DRF `SessionAuthentication` enforces CSRF only for authenticated sessions, so the
anonymous register/login/reset POSTs don't need a token; every unsafe request
thereafter must carry `X-CSRFToken` (the SPA reads the cookie).
"""

from __future__ import annotations

from django.conf import settings
from django.contrib.auth import authenticate
from django.contrib.auth import login as dj_login
from django.contrib.auth import logout as dj_logout
from django.utils.decorators import method_decorator
from django.views.decorators.csrf import ensure_csrf_cookie
from drf_spectacular.utils import extend_schema
from rest_framework import status
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView

from .emails import send_password_reset_email, send_verification_email
from .models import User
from .serializers import (
    EmailSerializer,
    LoginSerializer,
    PasswordChangeSerializer,
    PasswordResetConfirmSerializer,
    ProfileUpdateSerializer,
    RegisterSerializer,
    TokenSerializer,
    UserSerializer,
)
from .tokens import (
    TokenError,
    read_email_verify_token,
    read_password_reset_token,
    reset_token_matches,
)


@method_decorator(ensure_csrf_cookie, name="get")
class CSRFView(APIView):
    permission_classes = [AllowAny]

    @extend_schema(responses={200: None})
    def get(self, request):
        return Response({"detail": "CSRF cookie set"})


class RegisterView(APIView):
    permission_classes = [AllowAny]

    @extend_schema(request=RegisterSerializer, responses={201: None})
    def post(self, request):
        serializer = RegisterSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = serializer.save()
        send_verification_email(user)
        return Response(
            {"detail": "Account created. Check your email to verify."},
            status=status.HTTP_201_CREATED,
        )


class VerifyView(APIView):
    permission_classes = [AllowAny]

    @extend_schema(request=TokenSerializer, responses={200: None})
    def post(self, request):
        serializer = TokenSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            uid = read_email_verify_token(
                serializer.validated_data["token"], settings.EMAIL_VERIFY_MAX_AGE
            )
        except TokenError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)

        user = User.objects.filter(pk=uid).first()
        if user is None:
            return Response({"detail": "Unknown account."}, status=status.HTTP_400_BAD_REQUEST)
        if not user.is_email_verified:
            user.is_email_verified = True
            user.save(update_fields=["is_email_verified", "updated_at"])
        return Response({"detail": "Email verified. You can now log in."})


class ResendView(APIView):
    """Re-send the verification email. Always 200 (never leak whether an account
    exists); a mail is sent only for a real, still-unverified account."""

    permission_classes = [AllowAny]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "auth_resend"

    @extend_schema(request=EmailSerializer, responses={200: None})
    def post(self, request):
        serializer = EmailSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = User.objects.filter(email=serializer.validated_data["email"]).first()
        if user is not None and not user.is_email_verified:
            send_verification_email(user)
        return Response({"detail": "If that account exists and is unverified, a link was sent."})


class LoginView(APIView):
    permission_classes = [AllowAny]

    @extend_schema(request=LoginSerializer, responses={200: UserSerializer})
    def post(self, request):
        serializer = LoginSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = authenticate(
            request,
            username=serializer.validated_data["email"],
            password=serializer.validated_data["password"],
        )
        if user is None:
            return Response({"detail": "Invalid credentials."}, status=status.HTTP_401_UNAUTHORIZED)
        if not user.is_email_verified:
            return Response(
                {
                    "detail": "Please verify your email before logging in.",
                    "code": "email_unverified",
                },
                status=status.HTTP_403_FORBIDDEN,
            )
        dj_login(request, user)
        return Response(UserSerializer(user).data)


class LogoutView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(request=None, responses={204: None})
    def post(self, request):
        dj_logout(request)
        return Response(status=status.HTTP_204_NO_CONTENT)


class MeView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(responses={200: UserSerializer})
    def get(self, request):
        return Response(UserSerializer(request.user).data)

    @extend_schema(request=ProfileUpdateSerializer, responses={200: UserSerializer})
    def patch(self, request):
        serializer = ProfileUpdateSerializer(instance=request.user, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        user = serializer.save()
        user.refresh_from_db()
        return Response(UserSerializer(user).data)


class PasswordChangeView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(request=PasswordChangeSerializer, responses={200: None})
    def post(self, request):
        serializer = PasswordChangeSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = request.user
        if not user.check_password(serializer.validated_data["current_password"]):
            return Response(
                {"detail": "Current password is incorrect."}, status=status.HTTP_400_BAD_REQUEST
            )
        user.set_password(serializer.validated_data["new_password"])
        user.save(update_fields=["password", "updated_at"])
        # Keep the current session valid after the password change.
        dj_login(request, user)
        return Response({"detail": "Password updated."})


class PasswordResetView(APIView):
    """Always 200 (never leak account existence); email a signed token iff the
    account exists."""

    permission_classes = [AllowAny]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "auth_reset"

    @extend_schema(request=EmailSerializer, responses={200: None})
    def post(self, request):
        serializer = EmailSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = User.objects.filter(email=serializer.validated_data["email"]).first()
        if user is not None:
            send_password_reset_email(user)
        return Response({"detail": "If that account exists, a reset link was sent."})


class PasswordResetConfirmView(APIView):
    permission_classes = [AllowAny]

    @extend_schema(request=PasswordResetConfirmSerializer, responses={200: None})
    def post(self, request):
        serializer = PasswordResetConfirmSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            data = read_password_reset_token(
                serializer.validated_data["token"], settings.PASSWORD_RESET_MAX_AGE
            )
        except TokenError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)

        user = User.objects.filter(pk=int(data["uid"])).first()
        # Fingerprint mismatch => the token was already used (password changed).
        if user is None or not reset_token_matches(user, data):
            return Response(
                {"detail": "Invalid or expired reset token."}, status=status.HTTP_400_BAD_REQUEST
            )
        user.set_password(serializer.validated_data["new_password"])
        user.save(update_fields=["password", "updated_at"])
        return Response({"detail": "Password reset. You can now log in."})
