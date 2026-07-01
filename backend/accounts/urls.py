from django.urls import path

from .views import CSRFView, LoginView, LogoutView, MeView, PreferencesView

app_name = "accounts"

urlpatterns = [
    path("csrf/", CSRFView.as_view(), name="csrf"),
    path("login/", LoginView.as_view(), name="login"),
    path("logout/", LogoutView.as_view(), name="logout"),
    path("me/", MeView.as_view(), name="me"),
    path("preferences/", PreferencesView.as_view(), name="preferences"),
]
