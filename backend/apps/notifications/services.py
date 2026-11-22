"""Сервисы привязки Telegram-аккаунта и отправки уведомлений через Apprise."""

from __future__ import annotations

import logging
import secrets
from abc import ABC, abstractmethod
from datetime import timedelta
from typing import Any
from uuid import UUID

import apprise
from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import transaction
from django.utils import timezone

from apps.core.enums import OrderStatus
from apps.notifications.dto import NotificationMessage
from apps.notifications.models import TelegramBindingToken

logger = logging.getLogger(__name__)


class TelegramBotBinder:
    """Управляет одноразовыми токенами и привязкой аккаунта клиента к Telegram."""

    def __init__(
        self,
        token_ttl_minutes: int | None = None,
        bot_username: str | None = None,
    ) -> None:
        if token_ttl_minutes is None:
            self.token_ttl_minutes = int(getattr(settings, "TELEGRAM_BIND_TOKEN_TTL_MINUTES", 30))
        else:
            self.token_ttl_minutes = int(token_ttl_minutes)

        raw_username = (
            getattr(settings, "TELEGRAM_BOT_USERNAME", "YouPhotoOrderBot")
            if bot_username is None
            else bot_username
        )
        self.bot_username = str(raw_username or "YouPhotoOrderBot").strip().lstrip("@")

    @staticmethod
    def _resolve_user(user_or_id: Any) -> Any:
        user_model = get_user_model()
        if isinstance(user_or_id, user_model):
            return user_or_id
        if isinstance(user_or_id, (UUID, str)):
            return user_model.objects.get(pk=user_or_id)
        raise ValueError("Ожидался объект пользователя или идентификатор UUID.")

    def create_binding_token(self, user_or_id: Any) -> TelegramBindingToken:
        """Создает и сохраняет в БД одноразовый токен привязки Telegram."""
        user = self._resolve_user(user_or_id)
        now = timezone.now()
        expires_at = now + timedelta(minutes=self.token_ttl_minutes)
        token_value = secrets.token_urlsafe(24)
        return TelegramBindingToken.objects.create(
            user=user,
            token=token_value,
            expires_at=expires_at,
        )

    def create_token(self, user_or_id: Any) -> str:
        """Создает одноразовый токен привязки и возвращает его строковое значение."""
        return self.create_binding_token(user_or_id).token

    def generate_binding_link(self, user_or_id: Any) -> str:
        """Генерирует глубокую ссылку на Telegram-бота с одноразовым токеном."""
        token_value = self.create_token(user_or_id)
        return f"https://t.me/{self.bot_username}?start={token_value}"

    def handle_start_webhook(
        self,
        token: str | TelegramBindingToken,
        chat_id: str | int,
    ) -> bool:
        """Атомарно проверяет токен и привязывает chat_id Telegram к пользователю."""
        token_str = (
            token.token
            if isinstance(token, TelegramBindingToken)
            else str(token or "").strip()
        )
        cleaned_chat_id = str(chat_id if chat_id is not None else "").strip()
        if not token_str or not cleaned_chat_id:
            return False

        with transaction.atomic():
            binding = (
                TelegramBindingToken.objects.select_for_update()
                .select_related("user")
                .filter(token=token_str)
                .first()
            )
            now = timezone.now()
            if binding is None or not binding.is_valid(now=now):
                return False

            binding.user.bind_telegram(cleaned_chat_id)
            binding.is_used = True
            binding.used_at = now
            binding.save(update_fields=["is_used", "used_at"])
            return True


class INotificationService(ABC):
    """Абстрактный интерфейс сервиса уведомлений."""

    @abstractmethod
    def notify_status_changed(
        self,
        chat_id: str,
        order_number: str,
        new_status_label: str,
        comment: str = "",
    ) -> bool:
        """Отправляет уведомление об изменении статуса заказа."""

    @abstractmethod
    def send_order_status_changed(
        self,
        user: Any,
        order: Any,
        old_status: str = "",
        new_status: str = "",
        comment: str = "",
    ) -> bool:
        """Отправляет уведомление об изменении статуса заказа по объектам user и order."""

    @abstractmethod
    def send_pickup_reminder(self, *args: Any, **kwargs: Any) -> bool:
        """Отправляет напоминание о готовности заказа к выдаче."""

    @abstractmethod
    def send_custom(self, message: NotificationMessage) -> bool:
        """Отправляет произвольное уведомление."""

    @abstractmethod
    def generate_binding_link(self, user_or_id: Any) -> str:
        """Генерирует ссылку для привязки Telegram-аккаунта."""


class AppriseNotificationService(INotificationService):
    """Реализация отправки уведомлений в Telegram через библиотеку Apprise."""

    def __init__(
        self,
        bot_token: str | None = None,
        binder: TelegramBotBinder | None = None,
    ) -> None:
        raw_token = (
            getattr(settings, "TELEGRAM_BOT_TOKEN", "")
            if bot_token is None
            else bot_token
        )
        self.bot_token = str(raw_token or "").strip()
        self.binder = binder or TelegramBotBinder()

    @staticmethod
    def _resolve_status_label(status_value: str | OrderStatus) -> str:
        raw_value = str(status_value or "").strip()
        if not raw_value:
            return ""
        try:
            return OrderStatus(raw_value).label_ru
        except ValueError:
            return raw_value

    def _send(self, message: NotificationMessage) -> bool:
        chat_id = str(message.recipient_chat_id or "").strip()
        if not chat_id:
            logger.debug("Уведомление не отправлено: у пользователя не указан Telegram chat_id.")
            return False

        if not self.bot_token:
            logger.warning("Уведомление не отправлено: не задан TELEGRAM_BOT_TOKEN.")
            return False

        target_url = f"tgram://{self.bot_token}/{chat_id}/"
        try:
            ap = apprise.Apprise()
            if not ap.add(target_url):
                logger.warning(
                    "Apprise отклонил конфигурацию Telegram-канала для chat_id=%s.",
                    chat_id,
                )
                return False
            delivered = bool(ap.notify(title=message.title, body=message.body))
            if not delivered:
                logger.warning(
                    "Не удалось доставить Telegram-уведомление для chat_id=%s.",
                    chat_id,
                )
            return delivered
        except Exception:
            logger.exception(
                "Исключение при отправке Telegram-уведомления для chat_id=%s.",
                chat_id,
            )
            return False

    def send_custom(self, message: NotificationMessage) -> bool:
        return self._send(message)

    def notify_status_changed(
        self,
        chat_id: str,
        order_number: str,
        new_status_label: str,
        comment: str = "",
    ) -> bool:
        clean_comment = str(comment or "").strip()
        body = f'Статус вашего заказа №{order_number} изменен на: "{new_status_label}".'
        if clean_comment:
            body = f"{body} {clean_comment}"

        message = NotificationMessage(
            recipient_chat_id=str(chat_id or "").strip(),
            title=f"Обновление статуса заказа №{order_number}",
            body=body,
        )
        return self._send(message)

    def send_order_status_changed(
        self,
        user: Any,
        order: Any,
        old_status: str = "",
        new_status: str = "",
        comment: str = "",
    ) -> bool:
        del old_status
        if user is None:
            return False

        chat_id = (
            user.get_telegram_chat_id()
            if hasattr(user, "get_telegram_chat_id")
            else getattr(user, "telegram_chat_id", "") or ""
        )
        if not str(chat_id).strip():
            return False

        order_number = getattr(order, "order_number", str(order or ""))
        target_status = new_status or getattr(order, "status", "")
        status_label = self._resolve_status_label(target_status)

        return self.notify_status_changed(
            chat_id=str(chat_id),
            order_number=str(order_number),
            new_status_label=status_label,
            comment=comment,
        )

    def send_pickup_reminder(self, *args: Any, **kwargs: Any) -> bool:
        is_domain_call = (
            "user" in kwargs
            or "order" in kwargs
            or (len(args) == 2 and hasattr(args[1], "order_number"))
        )
        if is_domain_call:
            user = kwargs.get("user") if "user" in kwargs else (args[0] if len(args) >= 1 else None)
            order = (
                kwargs.get("order") if "order" in kwargs else (args[1] if len(args) >= 2 else None)
            )
            if user is None or order is None:
                return False
            chat_id = (
                user.get_telegram_chat_id()
                if hasattr(user, "get_telegram_chat_id")
                else getattr(user, "telegram_chat_id", "") or ""
            )
            order_number = str(getattr(order, "order_number", ""))
            pickup_code = str(getattr(order, "pickup_code", ""))
            pickup_salon = getattr(order, "pickup_salon", None)
            salon_address = (
                str(getattr(pickup_salon, "address", "") or "")
                if pickup_salon is not None
                else ""
            )
        else:
            chat_id = kwargs.get("chat_id", args[0] if len(args) >= 1 else "")
            order_number = kwargs.get("order_number", args[1] if len(args) >= 2 else "")
            pickup_code = kwargs.get("pickup_code", args[2] if len(args) >= 3 else "")
            salon_address = kwargs.get("salon_address", args[3] if len(args) >= 4 else "")

        cleaned_chat_id = str(chat_id or "").strip()
        if not cleaned_chat_id:
            return False

        body = (
            f"Напоминаем, что ваш заказ №{order_number} готов к выдаче. "
            f"Код получения: {pickup_code}."
        )
        cleaned_address = str(salon_address or "").strip()
        if cleaned_address:
            body = f"{body} Адрес салона: {cleaned_address}."

        message = NotificationMessage(
            recipient_chat_id=cleaned_chat_id,
            title=f"Заказ №{order_number} готов к выдаче",
            body=body,
        )
        return self._send(message)

    def generate_binding_link(self, user_or_id: Any) -> str:
        return self.binder.generate_binding_link(user_or_id)
