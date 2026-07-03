"""Ai DRF serializers — copilot chats/messages + agent memory (read models)."""

from __future__ import annotations

from rest_framework import serializers

from .models import AgentMemory, AiChat, AiMessage


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
