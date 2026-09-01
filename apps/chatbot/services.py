"""
Chat response service.

get_bot_reply() calls the real RAG pipeline (apps.chatbot.rag) — retrieves
grounded context from gold_chunks and generates an answer via Groq/Gemini/
Ollama (see rag._call_llm()'s fallback chain). Falls back to the canned demo
reply only if every provider in that chain is genuinely unavailable, so the
chat UI stays interactive even with all LLM backends down.
"""
import logging
import random

from . import rag

logger = logging.getLogger(__name__)

_CANNED_REPLIES = [
    "That's a great question! Once the RAG pipeline is connected, I'll answer using real data from the Gold layer.",
    "I'm running in demo mode right now — my answers will be grounded in real pipeline data once the LLM backend is wired in.",
    "Thanks for trying the chatbot! This response is a placeholder until retrieval-augmented generation is connected.",
]


def get_bot_reply(user_message, history=None):
    """Returns {'reply': str, 'sources': list[str]}. Falls back to the
    canned demo reply (no sources) if the RAG pipeline is down.
    history (optional) is client-side conversation memory — see
    apps.chatbot.rag.get_rag_reply()'s history param.

    The canned-reply fallback used to swallow every exception silently --
    found live, right before a presentation: the real failure (a Gemini
    503 that should have fallen through to Groq, see rag._call_llm()) was
    completely invisible, indistinguishable from "everything is genuinely
    unset up." Any real failure that reaches here now gets logged with a
    full traceback first, so the actual cause shows up in the server logs
    (journalctl/gunicorn log in prod) even though the user still sees a
    graceful reply instead of a 500.
    """
    try:
        return rag.get_rag_reply(user_message, history=history)
    except Exception:
        logger.exception(
            "get_bot_reply: RAG pipeline failed, falling back to canned demo reply (message=%r)",
            user_message,
        )
        return {'reply': random.choice(_CANNED_REPLIES), 'sources': []}
