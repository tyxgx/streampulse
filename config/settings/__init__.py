"""
Settings entry point.

Selects the concrete settings module based on the DJANGO_ENV environment
variable so `DJANGO_SETTINGS_MODULE=config.settings` works unchanged across
dev, prod, and any future environment (staging, ci, ...) — only the value
of DJANGO_ENV needs to differ.
"""
import os

_env = os.environ.get('DJANGO_ENV', 'dev')

if _env == 'prod':
    from .prod import *  # noqa: F401,F403
else:
    from .dev import *  # noqa: F401,F403
