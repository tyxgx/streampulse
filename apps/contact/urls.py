"""URL routes for the contact app."""
from django.urls import path

from . import views

app_name = 'contact'

urlpatterns = [
    path('', views.ContactView.as_view(), name='index'),
]
