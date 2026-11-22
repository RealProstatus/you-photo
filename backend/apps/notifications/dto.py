"""DTO для модуля уведомлений."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class NotificationMessage:
    """Неизменяемое сообщение уведомления для отправки в Telegram."""

    recipient_chat_id: str
    title: str
    body: str
