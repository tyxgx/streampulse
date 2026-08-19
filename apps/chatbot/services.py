"""
Chat response service.

get_bot_reply() calls the real RAG pipeline (apps.chatbot.rag) — retrieves
grounded context from gold_chunks and generates an answer via a local
Ollama model. Falls back to the canned demo reply if Ollama isn't running,
so the chat UI stays interactive even without the local LLM up.
"""
import random

from . import rag

_CANNED_REPLIES = [
    "That's a great question! Once the RAG pipeline is connected, I'll answer using real data from the Gold layer.",
    "I'm running in demo mode right now — my answers will be grounded in real pipeline data once the LLM backend is wired in.",
    "Thanks for trying the chatbot! This response is a placeholder until retrieval-augmented generation is connected.",
]


def get_bot_reply(user_message, history=None):
    """Returns {'reply': str, 'sources': list[str]}. Falls back to the
    canned demo reply (no sources) if the RAG pipeline / Ollama is down.
    history (optional) is client-side conversation memory — see
    apps.chatbot.rag.get_rag_reply()'s history param."""
    try:
        return rag.get_rag_reply(user_message, history=history)
    except Exception:
        return {'reply': random.choice(_CANNED_REPLIES), 'sources': []}
