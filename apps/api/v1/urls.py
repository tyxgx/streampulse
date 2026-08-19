"""
v1 API routes.

A single DRF DefaultRouter is shared across all domain apps so the full
API surface is visible in one place. Each feature app owns its own
serializers/views in its own module and registers them here — domain
logic stays in the domain app, this file just wires it together.

/api/v1/schema/, /docs/, /redoc/ are wired up now (via drf-spectacular)
so interactive API docs are available from day one.
"""
from django.urls import path
from drf_spectacular.views import (
    SpectacularAPIView,
    SpectacularRedocView,
    SpectacularSwaggerView,
)
from rest_framework.routers import DefaultRouter

from apps.chatbot.api import ChatMessageView

app_name = 'v1'

router = DefaultRouter()
# ViewSet-backed resources register here, e.g.:
#   router.register('chatbot/conversations', ConversationViewSet, basename='chatbot-conversations')

urlpatterns = router.urls + [
    path('chatbot/messages/', ChatMessageView.as_view(), name='chatbot-messages'),
    path('schema/', SpectacularAPIView.as_view(), name='schema'),
    path('docs/', SpectacularSwaggerView.as_view(url_name='api:v1:schema'), name='docs'),
    path('redoc/', SpectacularRedocView.as_view(url_name='api:v1:schema'), name='redoc'),
]
