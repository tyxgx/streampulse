"""AppConfig for the gold_data app."""
from django.apps import AppConfig


class GoldDataConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'apps.gold_data'
    label = 'gold_data'
    verbose_name = 'Gold Data'
