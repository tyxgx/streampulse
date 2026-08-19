"""AppConfig for the team app."""
from django.apps import AppConfig


class TeamConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'apps.team'
    label = 'team'
    verbose_name = 'Team'
