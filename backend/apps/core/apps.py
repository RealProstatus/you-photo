"""Django application configuration for ``apps.core``."""

from django.apps import AppConfig


class CoreConfig(AppConfig):
    """Application configuration for the YouPhoto core module."""

    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.core"
    verbose_name = "Ядро системы YouPhoto"
