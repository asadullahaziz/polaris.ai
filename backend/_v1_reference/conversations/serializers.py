from rest_framework import serializers

from .models import Conversation, Message


class ConversationSerializer(serializers.ModelSerializer):
    class Meta:
        model = Conversation
        fields = ["id", "title", "kind", "status", "created_at", "updated_at"]
        read_only_fields = ["id", "kind", "status", "created_at", "updated_at"]


class MessageSerializer(serializers.ModelSerializer):
    class Meta:
        model = Message
        fields = ["id", "author_type", "author_side", "action", "body", "created_at"]
        read_only_fields = fields
