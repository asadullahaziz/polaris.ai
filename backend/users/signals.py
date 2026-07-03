"""Guarantee every `User` has exactly one `UserProfile` row."""

from __future__ import annotations

from django.db.models.signals import post_save
from django.dispatch import receiver

from .models import User, UserProfile


@receiver(post_save, sender=User)
def ensure_profile(sender, instance: User, created: bool, **kwargs) -> None:
    if created:
        UserProfile.objects.get_or_create(user=instance)
