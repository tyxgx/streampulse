"""Serializers for the chatbot API."""
from rest_framework import serializers


class ChatHistoryTurnSerializer(serializers.Serializer):
    role = serializers.ChoiceField(choices=['user', 'assistant'])
    content = serializers.CharField()


class ChatMessageRequestSerializer(serializers.Serializer):
    message = serializers.CharField(max_length=2000)
    # Client-side conversation memory (see apps/chatbot/static/chatbot/js/
    # chatbot.js) — no server-side session/DB storage, the browser tab
    # sends back whatever history it's tracking. Optional/defaults to
    # empty so existing callers (and the DRF schema) are unaffected.
    history = ChatHistoryTurnSerializer(many=True, required=False, default=list)


class ChatMessageResponseSerializer(serializers.Serializer):
    reply = serializers.CharField()
    sources = serializers.ListField(child=serializers.CharField(), required=False)
