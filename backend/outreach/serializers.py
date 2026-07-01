"""
Outreach REST serializers (P2.3/P2.7). The shortlist the FE renders for approval,
plus the campaign envelope. Read-only — mutations go through the approve/cancel
actions, which call the invariant-bearing service.
"""

from __future__ import annotations

from rest_framework import serializers

from .models import OutreachCampaign, OutreachRecipient


class OutreachRecipientSerializer(serializers.ModelSerializer):
    name = serializers.SerializerMethodField()
    kind = serializers.SerializerMethodField()
    conversation_id = serializers.IntegerField(read_only=True)

    class Meta:
        model = OutreachRecipient
        fields = [
            "id",
            "name",
            "kind",
            "rank_score",
            "rank_reason",
            "draft_body",
            "status",
            "conversation_id",
        ]
        read_only_fields = fields

    def get_name(self, obj) -> str:
        if obj.recipient_user_id:
            u = obj.recipient_user
            return (u.full_name or u.username) if u else f"Buyer {obj.recipient_user_id}"
        p = obj.recipient_prospect
        if p:
            return p.full_name or p.entity_name or f"Prospect {obj.recipient_prospect_id}"
        return f"Prospect {obj.recipient_prospect_id}"

    def get_kind(self, obj) -> str:
        return "registered" if obj.recipient_user_id else "prospect"


class OutreachCampaignSerializer(serializers.ModelSerializer):
    recipients = OutreachRecipientSerializer(many=True, read_only=True)
    listing_address = serializers.SerializerMethodField()

    class Meta:
        model = OutreachCampaign
        fields = [
            "id",
            "listing",
            "listing_address",
            "copilot_conversation",
            "status",
            "created_at",
            "recipients",
        ]
        read_only_fields = fields

    def get_listing_address(self, obj) -> str | None:
        lp = obj.listing.listingproperty_set.first() if obj.listing_id else None
        return lp.property.address_raw if lp and lp.property else None
