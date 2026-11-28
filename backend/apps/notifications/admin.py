"""Django Admin registration for notification module models."""

from django.contrib import admin

from apps.notifications.models import TelegramBindingToken


@admin.register(TelegramBindingToken)
class TelegramBindingTokenAdmin(admin.ModelAdmin):
    """Admin configuration for ``TelegramBindingToken`` records."""

    list_display = ("token", "user", "is_used", "expires_at", "created_at", "used_at")
    list_filter = ("is_used", "created_at", "expires_at")
    search_fields = ("token", "user__email", "user__phone", "user__name")
    readonly_fields = ("id", "created_at", "used_at")
