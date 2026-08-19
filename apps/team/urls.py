"""URL routes for the team app."""
from django.urls import path

from . import views

app_name = 'team'

urlpatterns = [
    path('', views.TeamView.as_view(), name='index'),
]
