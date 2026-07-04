from django.urls import path

from .views import (
    CSRFView,
    LoginView,
    LogoutView,
    MeView,
    PasswordChangeView,
    PasswordResetConfirmView,
    PasswordResetView,
    RegisterView,
    ResendView,
    VerifyView,
)

app_name = "users"

urlpatterns = [
    path("csrf/", CSRFView.as_view(), name="csrf"),
    path("register/", RegisterView.as_view(), name="register"),
    path("verify/", VerifyView.as_view(), name="verify"),
    path("resend/", ResendView.as_view(), name="resend"),
    path("login/", LoginView.as_view(), name="login"),
    path("logout/", LogoutView.as_view(), name="logout"),
    path("me/", MeView.as_view(), name="me"),
    path("password/change/", PasswordChangeView.as_view(), name="password-change"),
    path("password/reset/", PasswordResetView.as_view(), name="password-reset"),
    path(
        "password/reset/confirm/",
        PasswordResetConfirmView.as_view(),
        name="password-reset-confirm",
    ),
]
