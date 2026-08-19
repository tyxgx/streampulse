"""
Base settings shared by every environment (dev, prod, ...).

Environment-specific settings files (dev.py, prod.py) import * from this
module and override only what differs. This keeps environment differences
explicit and auditable instead of scattered behind if/else blocks.
"""
from pathlib import Path

import environ

# Build paths inside the project like this: BASE_DIR / 'subdir'.
# BASE_DIR points at the repository root (three levels up from this file:
# config/settings/base.py -> config/settings -> config -> repo root).
BASE_DIR = Path(__file__).resolve().parent.parent.parent

env = environ.Env()
# Read the .env file from the repo root if it exists. In production the
# same variables are expected to be provided by the hosting platform
# instead of a committed file.
environ.Env.read_env(BASE_DIR / '.env')

SECRET_KEY = env('DJANGO_SECRET_KEY', default='django-insecure-change-me-in-env-file')

# Applications are grouped so it is obvious, at a glance, which apps are
# framework/third-party vs. which are this project's own domain apps.
DJANGO_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
]

THIRD_PARTY_APPS = [
    'rest_framework',
    'corsheaders',
    'drf_spectacular',
]

# Each of these is a self-contained domain app living under apps/.
# One app per major feature/page group so pages, data access, and future
# real-API integrations stay isolated from one another.
LOCAL_APPS = [
    'apps.core.apps.CoreConfig',
    'apps.chatbot.apps.ChatbotConfig',
    'apps.gold_data.apps.GoldDataConfig',
    'apps.documentation.apps.DocumentationConfig',
    'apps.team.apps.TeamConfig',
    'apps.contact.apps.ContactConfig',
    'apps.api.apps.ApiConfig',
]

INSTALLED_APPS = DJANGO_APPS + THIRD_PARTY_APPS + LOCAL_APPS

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'corsheaders.middleware.CorsMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

ROOT_URLCONF = 'config.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        # Project-level templates dir holds base.html and shared partials
        # (navbar, footer, cards). Each app additionally ships its own
        # templates/<app_name>/ directory for page-specific markup.
        'DIRS': [BASE_DIR / 'templates'],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.debug',
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
            ],
        },
    },
]

WSGI_APPLICATION = 'config.wsgi.application'
ASGI_APPLICATION = 'config.asgi.application'

DATABASES = {
    'default': env.db('DATABASE_URL', default=f'sqlite:///{BASE_DIR / "db.sqlite3"}'),
    # Read-only Gold-layer mart. apps.gold_data models are managed=False —
    # schema.sql and scripts/load_gold_to_postgres.py own the schema/data,
    # Django only queries it via .objects.using('gold').
    'gold': env.db('GOLD_DATABASE_URL', default='postgres://localhost:5432/gold'),
}

AUTH_PASSWORD_VALIDATORS = [
    {'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator'},
    {'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator'},
    {'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator'},
    {'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator'},
]

LANGUAGE_CODE = 'en-us'
TIME_ZONE = 'Asia/Kolkata'
USE_I18N = True
USE_TZ = True

STATIC_URL = 'static/'
# Project-wide static assets (shared CSS/JS/img). Individual apps may also
# ship static/<app_name>/ directories that get collected alongside these.
STATICFILES_DIRS = [BASE_DIR / 'static']
STATIC_ROOT = BASE_DIR / 'staticfiles'

MEDIA_URL = 'media/'
MEDIA_ROOT = BASE_DIR / 'media'

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

# --- Django REST Framework -------------------------------------------------
# Dummy data today is still served through DRF serializers/viewsets so the
# request/response contract (pagination, auth, versioning) is already in
# place when real pipeline data replaces the dummy service layer.
REST_FRAMEWORK = {
    'DEFAULT_SCHEMA_CLASS': 'drf_spectacular.openapi.AutoSchema',
    'DEFAULT_PAGINATION_CLASS': 'rest_framework.pagination.PageNumberPagination',
    'PAGE_SIZE': 20,
}

SPECTACULAR_SETTINGS = {
    'TITLE': 'StreamPulse API',
    'DESCRIPTION': (
        'API layer for the CDAC Big Data Engineering project. Endpoints '
        'currently return demo/placeholder data and are designed to be '
        'swapped for live pipeline data without changing the contract.'
    ),
    'VERSION': '1.0.0',
    'SERVE_INCLUDE_SCHEMA': False,
}

# --- CORS -------------------------------------------------------------------
# Locked down by default; dev.py opens this up for local frontend work.
CORS_ALLOWED_ORIGINS = env.list('CORS_ALLOWED_ORIGINS', default=[])
