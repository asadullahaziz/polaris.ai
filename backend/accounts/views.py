"""
Session-cookie auth (implementation_plan §4.1).

  GET  /api/auth/csrf    -> primes the `csrftoken` cookie
  POST /api/auth/login   -> authenticate + login() sets the `sessionid` cookie
  POST /api/auth/logout  -> clears the session
  GET  /api/auth/me      -> the current user (401 if anonymous)

DRF `SessionAuthentication` enforces CSRF only for already-authenticated
sessions, so the anonymous login POST does not require a token; every unsafe
request thereafter must carry `X-CSRFToken` (the SPA reads the cookie).
"""

from __future__ import annotations

from django.contrib.auth import authenticate
from django.contrib.auth import login as dj_login
from django.contrib.auth import logout as dj_logout
from django.utils.decorators import method_decorator
from django.views.decorators.csrf import ensure_csrf_cookie
from drf_spectacular.utils import extend_schema
from rest_framework import status
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from .serializers import LoginSerializer, UserSerializer


@method_decorator(ensure_csrf_cookie, name="get")
class CSRFView(APIView):
    permission_classes = [AllowAny]

    @extend_schema(responses={200: None})
    def get(self, request):
        return Response({"detail": "CSRF cookie set"})


class LoginView(APIView):
    permission_classes = [AllowAny]

    @extend_schema(request=LoginSerializer, responses={200: UserSerializer})
    def post(self, request):
        serializer = LoginSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = authenticate(
            request,
            username=serializer.validated_data["username"],
            password=serializer.validated_data["password"],
        )
        if user is None:
            return Response({"detail": "Invalid credentials."}, status=status.HTTP_401_UNAUTHORIZED)
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


class PreferencesView(APIView):
    """The user half of the shared context store (features §E #22): the UI reads/writes
    the same `app_user.preferences` JSON the agent tools see. PATCH shallow-merges."""

    permission_classes = [IsAuthenticated]

    @extend_schema(responses={200: dict})
    def get(self, request):
        return Response(request.user.preferences or {})

    @extend_schema(request=dict, responses={200: dict})
    def patch(self, request):
        prefs = dict(request.user.preferences or {})
        if not isinstance(request.data, dict):
            return Response(
                {"detail": "expected a JSON object"}, status=status.HTTP_400_BAD_REQUEST
            )
        prefs.update(request.data)
        request.user.preferences = prefs
        request.user.save(update_fields=["preferences", "updated_at"])
        return Response(prefs)
