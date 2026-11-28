"""Django application configuration for apps.storage."""

from django.apps import AppConfig


class StorageConfig(AppConfig):
    """Configuration for the file storage Django application."""

    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.storage"
    verbose_name = "Файловое хранилище"
