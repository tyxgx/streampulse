"""URL routes for the chatbot app."""
from django.urls import path

from . import views

app_name = 'chatbot'

urlpatterns = [
    path('', views.ChatbotView.as_view(), name='index'),
]
