"""Django application configuration for apps.notifications."""

from django.apps import AppConfig


class NotificationsConfig(AppConfig):
    """Configuration for the notifications Django application."""

    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.notifications"
    verbose_name = "Сервис уведомлений"
