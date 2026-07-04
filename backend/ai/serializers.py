"""Ai DRF serializers — copilot chats/messages + agent memory + outreach (read models)."""

from __future__ import annotations

from rest_framework import serializers

from .models import AgentMemory, AiChat, AiMessage, OutreachCampaign, OutreachRecipient


class AiMessageSerializer(serializers.ModelSerializer):
    class Meta:
        model = AiMessage
        fields = ["id", "role", "content", "created_at"]


class AiChatSummarySerializer(serializers.ModelSerializer):
    class Meta:
        model = AiChat
        fields = ["id", "title", "status", "created_at", "updated_at"]


class AiChatDetailSerializer(serializers.ModelSerializer):
    messages = AiMessageSerializer(many=True, read_only=True)

    class Meta:
        model = AiChat
        fields = ["id", "title", "status", "created_at", "updated_at", "messages"]


class AgentMemorySerializer(serializers.ModelSerializer):
    class Meta:
        model = AgentMemory
        fields = ["id", "namespace", "content", "created_at", "updated_at"]


# ---- outreach (P5) — the shortlist the FE renders for approval ------------------
class OutreachRecipientSerializer(serializers.ModelSerializer):
    name = serializers.SerializerMethodField()
    chat_id = serializers.IntegerField(read_only=True)

    class Meta:
        model = OutreachRecipient
        fields = [
            "id",
            "recipient_user",
            "name",
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


class OutreachCampaignSerializer(serializers.ModelSerializer):
    recipients = OutreachRecipientSerializer(many=True, read_only=True)
    listing_address = serializers.SerializerMethodField()

    class Meta:
        model = OutreachCampaign
        fields = [
            "id",
            "listing",
            "listing_address",
            "copilot_ai_chat",
            "status",
            "created_at",
            "recipients",
        ]
        read_only_fields = fields

    def get_listing_address(self, obj) -> str | None:
        lp = obj.listing.listingproperty_set.first() if obj.listing_id else None
        return lp.property.address_raw if lp and lp.property else None
