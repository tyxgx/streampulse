"""AppConfig for the chatbot app."""
import os
import sys
import threading

from django.apps import AppConfig


class ChatbotConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'apps.chatbot'
    label = 'chatbot'
    verbose_name = 'RAG Chatbot'

    def ready(self):
        # Pre-load the SentenceTransformer embedding model at server
        # startup instead of on the first real chat request. Found live:
        # a cold `_get_model()` call (loading all-MiniLM-L6-v2 from disk)
        # added ~20s to whoever's unlucky enough to send the first message
        # after a restart/deploy -- exactly the kind of thing that looks
        # like "the chatbot is broken" in a live demo, when it's actually
        # just the very first request paying a one-time cost that every
        # request after it doesn't.
        #
        # Only run this for an actual running server, not for management
        # commands (migrate, shell, collectstatic, etc.) that also trigger
        # AppConfig.ready() -- and, under `runserver`'s autoreloader, only
        # in the reloaded child process (RUN_MAIN), not the parent that
        # exits immediately after forking it.
        # Substring match, not exact -- gunicorn's own argv[0] is a full
        # path (.../venv/bin/gunicorn), never the bare string "gunicorn".
        is_server_command = any('runserver' in arg or 'gunicorn' in arg for arg in sys.argv)
        is_reloader_parent = any('runserver' in arg for arg in sys.argv) and os.environ.get('RUN_MAIN') != 'true'
        if is_server_command and not is_reloader_parent:
            threading.Thread(target=self._warm_up_embedder, daemon=True).start()

    @staticmethod
    def _warm_up_embedder():
        from . import rag
        rag._get_model()
