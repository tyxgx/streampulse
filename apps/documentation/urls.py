"""URL routes for the documentation app."""
from django.urls import path

from . import views

app_name = 'documentation'

urlpatterns = [
    path('', views.DocumentationView.as_view(), name='index'),
]
