"""DRF-представления для webhook-а и генерации ссылки привязки Telegram."""

from __future__ import annotations

from typing import Any

from rest_framework import status
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.notifications.services import TelegramBotBinder


class TelegramWebhookView(APIView):
    """Публичный webhook для обработки команды `/start <token>` от Telegram-бота."""

    authentication_classes = ()
    permission_classes = (AllowAny,)

    def post(self, request: Request, *args: Any, **kwargs: Any) -> Response:
        del args, kwargs
        payload = request.data if isinstance(request.data, dict) else {}
        message = payload.get("message") or payload.get("edited_message")

        if isinstance(message, dict):
            chat = message.get("chat") or {}
            chat_id = str(chat.get("id", "")).strip() if isinstance(chat, dict) else ""
            text = str(message.get("text") or "").strip()
            if text.startswith("/start"):
                parts = text.split(maxsplit=1)
                command = parts[0]
                if command == "/start" or command.startswith("/start@"):
                    token = parts[1].strip() if len(parts) > 1 else ""
                    if not token or not chat_id:
                        return Response(
                            {
                                "ok": False,
                                "bound": False,
                                "detail": (
                                    "Токен привязки не указан или некорректен. "
                                    "Повторите привязку из профиля."
                                ),
                            },
                            status=status.HTTP_400_BAD_REQUEST,
                        )
                    bound = TelegramBotBinder().handle_start_webhook(token, chat_id)
                    if bound:
                        return Response(
                            {
                                "ok": True,
                                "bound": True,
                                "detail": "Telegram-аккаунт успешно привязан.",
                            },
                            status=status.HTTP_200_OK,
                        )
                    return Response(
                        {
                            "ok": False,
                            "bound": False,
                            "detail": (
                                "Токен привязки недействителен или истек. "
                                "Повторите привязку из профиля."
                            ),
                        },
                        status=status.HTTP_400_BAD_REQUEST,
                    )
            return Response(
                {
                    "ok": True,
                    "bound": False,
                    "detail": "Команда не требует привязки аккаунта.",
                },
                status=status.HTTP_200_OK,
            )

        token = str(payload.get("token") or "").strip()
        raw_chat_id = payload.get("chat_id")
        chat_id = str(raw_chat_id if raw_chat_id is not None else "").strip()
        if not token or not chat_id:
            return Response(
                {
                    "ok": False,
                    "bound": False,
                    "detail": "Укажите токен привязки и идентификатор чата Telegram.",
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        bound = TelegramBotBinder().handle_start_webhook(token, chat_id)
        if bound:
            return Response(
                {
                    "ok": True,
                    "bound": True,
                    "detail": "Telegram-аккаунт успешно привязан.",
                },
                status=status.HTTP_200_OK,
            )

        return Response(
            {
                "ok": False,
                "bound": False,
                "detail": (
                    "Токен привязки недействителен или истек. "
                    "Повторите привязку из профиля."
                ),
            },
            status=status.HTTP_400_BAD_REQUEST,
        )


class TelegramBindLinkView(APIView):
    """Генерирует одноразовую ссылку для привязки Telegram текущему пользователю."""

    permission_classes = (IsAuthenticated,)

    def _issue_link(self, request: Request) -> Response:
        if getattr(request.user, "is_blocked", False):
            return Response(
                {"detail": "Учетная запись заблокирована."},
                status=status.HTTP_403_FORBIDDEN,
            )
        binder = TelegramBotBinder()
        binding_token = binder.create_binding_token(request.user)
        link = f"https://t.me/{binder.bot_username}?start={binding_token.token}"
        return Response(
            {
                "binding_link": link,
                "link": link,
                "token": binding_token.token,
            },
            status=status.HTTP_200_OK,
        )

    def get(self, request: Request, *args: Any, **kwargs: Any) -> Response:
        del args, kwargs
        return self._issue_link(request)

    def post(self, request: Request, *args: Any, **kwargs: Any) -> Response:
        del args, kwargs
        return self._issue_link(request)
