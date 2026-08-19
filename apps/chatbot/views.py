"""
Views for the chatbot app.

Renders the RAG chatbot UI shell. The actual chat request handling lives
behind a single stub endpoint (see urls.py / views.py send_message once
added in the chatbot UI milestone) so a real LLM/RAG backend can be wired
in later without touching the frontend markup.
"""
from django.views.generic import TemplateView


class ChatbotView(TemplateView):
    template_name = 'chatbot/chatbot.html'
