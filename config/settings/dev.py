"""Local development settings. Never used in production."""
from .base import *  # noqa: F401,F403
from .base import env

DEBUG = True

ALLOWED_HOSTS = env.list('DJANGO_ALLOWED_HOSTS', default=['localhost', '127.0.0.1'])

CORS_ALLOW_ALL_ORIGINS = True
