"""Ai DRF serializers — copilot chats/messages + agent memory + outreach (read models)."""

from __future__ import annotations

from rest_framework import serializers

from .models import AgentMemory, AiChat, AiMessage, OutreachCampaign, OutreachRecipient


class AiMessageSerializer(serializers.ModelSerializer):
    class Meta:
        model = AiMessage
        # `tool_calls` carries the structured payload for a resolved/expired confirm row
        # (role='tool') so the FE can re-render a greyed Approved/Declined/Expired card.
        fields = ["id", "role", "content", "tool_calls", "created_at"]


class AiChatSummarySerializer(serializers.ModelSerializer):
    class Meta:
        model = AiChat
        fields = ["id", "title", "status", "created_at", "updated_at"]


class AiChatDetailSerializer(serializers.ModelSerializer):
    messages = AiMessageSerializer(many=True, read_only=True)
    # Only the confirm-card render payload — never the internal cfg/thread_id/buf.
    pending_confirm = serializers.SerializerMethodField()

    class Meta:
        model = AiChat
        fields = [
            "id",
            "title",
            "status",
            "created_at",
            "updated_at",
            "messages",
            "pending_confirm",
        ]

    def get_pending_confirm(self, obj) -> dict | None:
        """The parked write-confirm (`{kind, action, summary, proposal}`) so a reopened
        session rebuilds an actionable card; NULL when nothing is awaiting approval."""
        return (obj.pending_confirm or {}).get("value")


class AgentMemorySerializer(serializers.ModelSerializer):
    class Meta:
        model = AgentMemory
        fields = ["id", "namespace", "content", "created_at", "updated_at"]


# ---- outreach — the shortlist the FE renders for approval -----------------------
def _listing_address(listing_id) -> str | None:
    from catalog.models import ListingProperty

    lp = (
        ListingProperty.objects.filter(listing_id=listing_id)
        .select_related("property")
        .order_by("sort_order")
        .first()
    )
    return lp.property.address_raw if lp and lp.property else None


class OutreachRecipientSerializer(serializers.ModelSerializer):
    name = serializers.SerializerMethodField()
    chat_id = serializers.IntegerField(read_only=True)
    listing_address = serializers.SerializerMethodField()

    class Meta:
        model = OutreachRecipient
        fields = [
            "id",
            "recipient_user",
            "name",
            "listing",
            "listing_address",
            "rank_score",
            "rank_reason",
            "draft_body",
            "status",
            "chat_id",
        ]
        read_only_fields = fields

    def get_name(self, obj) -> str:
        u = obj.recipient_user
        return u.display_name if u else f"Buyer {obj.recipient_user_id}"

    def get_listing_address(self, obj) -> str | None:
        return _listing_address(obj.listing_id)


class OutreachCampaignSerializer(serializers.ModelSerializer):
    recipients = OutreachRecipientSerializer(many=True, read_only=True)
    listing_address = serializers.SerializerMethodField()
    listing_addresses = serializers.SerializerMethodField()

    class Meta:
        model = OutreachCampaign
        fields = [
            "id",
            "listing",
            "listing_address",
            "listing_addresses",
            "copilot_ai_chat",
            "status",
            "created_at",
            "recipients",
        ]
        read_only_fields = fields

    def get_listing_address(self, obj) -> str | None:
        """The single-listing display address; NULL for a multi-listing campaign."""
        return _listing_address(obj.listing_id) if obj.listing_id else None

    def get_listing_addresses(self, obj) -> list[str]:
        """Distinct addresses across the campaign's recipient rows (multi-listing)."""
        seen: dict[int, None] = {}
        for r in obj.recipients.all():
            seen.setdefault(r.listing_id)
        return [a for a in (_listing_address(lid) for lid in seen) if a]
