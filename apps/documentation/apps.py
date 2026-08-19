"""AppConfig for the documentation app."""
from django.apps import AppConfig


class DocumentationConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'apps.documentation'
    label = 'documentation'
    verbose_name = 'Documentation'
