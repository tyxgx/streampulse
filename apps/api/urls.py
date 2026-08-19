"""
URL routes for the API app.

This module only handles versioning at the top level (/api/v1/, and a
future /api/v2/ if a breaking change is ever needed). Each version's
routes live in their own package so old and new API contracts can
coexist during a migration.
"""
from django.urls import include, path

app_name = 'api'

urlpatterns = [
    path('v1/', include('apps.api.v1.urls')),
]
