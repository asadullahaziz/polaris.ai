"""DRF serializers for the users/auth surface."""

from __future__ import annotations

from django.contrib.auth.password_validation import validate_password
from rest_framework import serializers

from .models import User, UserProfile


class ProfileSerializer(serializers.ModelSerializer):
    class Meta:
        model = UserProfile
        fields = [
            "preferences",
            "bio",
            "company",
            "avatar_url",
            "auto_reply_when_away",
            "agent_autonomy",
            "agent_instructions",
        ]


class UserSerializer(serializers.ModelSerializer):
    """The current user + nested profile (what /api/auth/me returns)."""

    profile = ProfileSerializer(read_only=True)

    class Meta:
        model = User
        fields = [
            "id",
            "email",
            "full_name",
            "phone",
            "preferred_channel",
            "is_email_verified",
            "is_staff",
            "date_joined",
            "profile",
        ]
        read_only_fields = fields


class RegisterSerializer(serializers.Serializer):
    email = serializers.EmailField()
    password = serializers.CharField(
        style={"input_type": "password"}, trim_whitespace=False, write_only=True
    )
    full_name = serializers.CharField(required=False, allow_blank=True, default="")

    def validate_email(self, value: str) -> str:
        value = value.strip().lower()
        if User.objects.filter(email=value).exists():
            raise serializers.ValidationError("An account with this email already exists.")
        return value

    def validate_password(self, value: str) -> str:
        validate_password(value)
        return value

    def create(self, validated_data):
        return User.objects.create_user(
            email=validated_data["email"],
            password=validated_data["password"],
            full_name=validated_data.get("full_name", ""),
            is_email_verified=False,
        )


class LoginSerializer(serializers.Serializer):
    email = serializers.EmailField()
    password = serializers.CharField(
        style={"input_type": "password"}, trim_whitespace=False, write_only=True
    )

    def validate_email(self, value: str) -> str:
        return value.strip().lower()


class TokenSerializer(serializers.Serializer):
    token = serializers.CharField()


class EmailSerializer(serializers.Serializer):
    email = serializers.EmailField()

    def validate_email(self, value: str) -> str:
        return value.strip().lower()


class PasswordResetConfirmSerializer(serializers.Serializer):
    token = serializers.CharField()
    new_password = serializers.CharField(
        style={"input_type": "password"}, trim_whitespace=False, write_only=True
    )

    def validate_new_password(self, value: str) -> str:
        validate_password(value)
        return value


class PasswordChangeSerializer(serializers.Serializer):
    current_password = serializers.CharField(
        style={"input_type": "password"}, trim_whitespace=False, write_only=True
    )
    new_password = serializers.CharField(
        style={"input_type": "password"}, trim_whitespace=False, write_only=True
    )

    def validate_new_password(self, value: str) -> str:
        validate_password(value)
        return value


class ProfileUpdateSerializer(serializers.Serializer):
    """PATCH the current user's own profile + AI settings (settings page)."""

    # User row fields
    full_name = serializers.CharField(required=False, allow_blank=True)
    phone = serializers.CharField(required=False, allow_blank=True)
    preferred_channel = serializers.ChoiceField(
        choices=[c[0] for c in User._meta.get_field("preferred_channel").choices],
        required=False,
    )
    # Profile fields
    bio = serializers.CharField(required=False, allow_blank=True)
    company = serializers.CharField(required=False, allow_blank=True)
    avatar_url = serializers.CharField(required=False, allow_blank=True)
    preferences = serializers.JSONField(required=False)
    auto_reply_when_away = serializers.BooleanField(required=False)
    agent_autonomy = serializers.ChoiceField(
        choices=[c[0] for c in UserProfile._meta.get_field("agent_autonomy").choices],
        required=False,
    )
    agent_instructions = serializers.CharField(required=False, allow_blank=True)

    _USER_FIELDS = {"full_name", "phone", "preferred_channel"}

    def update(self, instance: User, validated_data):
        profile, _ = UserProfile.objects.get_or_create(user=instance)
        user_dirty, profile_dirty = [], []
        for field, value in validated_data.items():
            if field in self._USER_FIELDS:
                setattr(instance, field, value)
                user_dirty.append(field)
            else:
                setattr(profile, field, value)
                profile_dirty.append(field)
        if user_dirty:
            instance.save(update_fields=[*user_dirty, "updated_at"])
        if profile_dirty:
            profile.save(update_fields=[*profile_dirty, "updated_at"])
        return instance
