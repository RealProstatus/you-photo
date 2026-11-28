"""URL routes for notifications and Telegram binding APIs."""

from django.urls import path

from apps.notifications.views import TelegramBindLinkView, TelegramWebhookView

app_name = "notifications"

urlpatterns = [
    path("telegram/webhook/", TelegramWebhookView.as_view(), name="telegram-webhook"),
    path("telegram/bind-link/", TelegramBindLinkView.as_view(), name="telegram-bind-link"),
]
