"""
DRF view for the chatbot's message endpoint.

This is the single, clearly marked integration point for the real
RAG/LLM backend — see apps.chatbot.services.get_bot_reply(). The frontend
(static/chatbot/js/chatbot.js) posts here and expects {"reply": "..."}
back; that request/response contract won't change when the backend does.
"""
from drf_spectacular.utils import extend_schema
from rest_framework.response import Response
from rest_framework.views import APIView

from . import services
from .serializers import ChatMessageRequestSerializer, ChatMessageResponseSerializer


class ChatMessageView(APIView):
    """POST /api/v1/chatbot/messages/ — send a user message, get a bot reply."""

    @extend_schema(request=ChatMessageRequestSerializer, responses=ChatMessageResponseSerializer)
    def post(self, request):
        request_serializer = ChatMessageRequestSerializer(data=request.data)
        request_serializer.is_valid(raise_exception=True)

        result = services.get_bot_reply(
            request_serializer.validated_data['message'],
            history=request_serializer.validated_data['history'],
        )

        response_serializer = ChatMessageResponseSerializer(result)
        return Response(response_serializer.data)
