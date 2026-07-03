from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin

from .models import User, UserProfile


class ProfileInline(admin.StackedInline):
    model = UserProfile
    can_delete = False


@admin.register(User)
class UserAdmin(BaseUserAdmin):
    """Email-login admin (no username field)."""

    inlines = [ProfileInline]
    ordering = ("email",)
    list_display = ("email", "full_name", "is_email_verified", "is_staff", "date_joined")
    list_filter = ("is_staff", "is_superuser", "is_email_verified", "is_active")
    search_fields = ("email", "full_name")
    readonly_fields = ("date_joined", "last_login", "updated_at")

    fieldsets = (
        (None, {"fields": ("email", "password")}),
        ("Profile", {"fields": ("full_name", "phone", "preferred_channel")}),
        (
            "Permissions",
            {
                "fields": (
                    "is_active",
                    "is_email_verified",
                    "is_staff",
                    "is_superuser",
                    "groups",
                    "user_permissions",
                )
            },
        ),
        ("Dates", {"fields": ("last_login", "date_joined", "updated_at")}),
    )
    add_fieldsets = (
        (
            None,
            {
                "classes": ("wide",),
                "fields": ("email", "password1", "password2", "is_staff", "is_superuser"),
            },
        ),
    )
