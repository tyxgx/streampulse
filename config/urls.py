"""
Root URL configuration for the project.

Each domain app owns its own urls.py (with its own app_name namespace);
this file only maps URL prefixes to those app-level URLconfs. The `api/`
prefix is kept separate and versioned so the REST layer can evolve
independently of the page routes.
"""
from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import include, path

urlpatterns = [
    path('admin/', admin.site.urls),
    path('api/', include('apps.api.urls')),
    path('chatbot/', include('apps.chatbot.urls')),
    path('documentation/', include('apps.documentation.urls')),
    path('team/', include('apps.team.urls')),
    path('contact/', include('apps.contact.urls')),
    # core is mounted at the root so Home/About/Architecture/Pipeline
    # Overview resolve to '/', '/about/', etc.
    path('', include('apps.core.urls')),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
