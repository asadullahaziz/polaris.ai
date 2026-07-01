from rest_framework import serializers

from .models import AUTONOMY_LEVELS, AgentMemory


class MandateSerializer(serializers.Serializer):
    """Writable mandate fields (target listing/buy-box is set by the route, not the body)."""

    floor_price = serializers.FloatField(required=False, allow_null=True)
    ceiling_price = serializers.FloatField(required=False, allow_null=True)
    instructions = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    autonomy = serializers.ChoiceField(choices=[a[0] for a in AUTONOMY_LEVELS], required=False)
    auto_reply = serializers.BooleanField(required=False)


class MemorySerializer(serializers.ModelSerializer):
    class Meta:
        model = AgentMemory
        fields = ["id", "namespace", "content", "created_at", "updated_at"]
        read_only_fields = ["id", "created_at", "updated_at"]
