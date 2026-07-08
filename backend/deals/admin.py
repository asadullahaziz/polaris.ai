from django.contrib import admin

from .models import Deal


@admin.register(Deal)
class DealAdmin(admin.ModelAdmin):
    list_display = ("id", "stage", "listing", "buyer", "seller", "chat", "updated_at")
    list_filter = ("stage",)
    raw_id_fields = ("listing", "buyer", "seller", "chat")
