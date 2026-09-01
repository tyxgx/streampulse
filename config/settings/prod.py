"""
Production settings.

Kept intentionally strict: nothing here should silently fall back to an
insecure default. Missing required environment variables raise on startup
rather than deploying with weak security settings.
"""
from .base import *  # noqa: F401,F403
from .base import env

DEBUG = False

ALLOWED_HOSTS = env.list('DJANGO_ALLOWED_HOSTS')

# IP-only deployments (no domain/TLS termination in front of Nginx) need to
# opt out of the TLS-forcing settings below, or every request loops
# redirecting to an https:// URL that doesn't exist. Defaults to True (the
# secure behavior) so nothing changes for anyone who does have TLS -- only
# set DJANGO_FORCE_TLS=false explicitly for a known IP-only deployment.
_force_tls = env.bool('DJANGO_FORCE_TLS', default=True)

SECURE_SSL_REDIRECT = _force_tls
SESSION_COOKIE_SECURE = _force_tls
CSRF_COOKIE_SECURE = _force_tls
SECURE_HSTS_SECONDS = 31536000 if _force_tls else 0
SECURE_HSTS_INCLUDE_SUBDOMAINS = _force_tls
SECURE_HSTS_PRELOAD = _force_tls

CORS_ALLOW_ALL_ORIGINS = False
