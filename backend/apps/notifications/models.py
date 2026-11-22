"""Модели модуля уведомлений."""

from __future__ import annotations

import uuid
from datetime import datetime

from django.conf import settings
from django.db import models
from django.utils import timezone


class TelegramBindingToken(models.Model):
    """Одноразовый криптографический токен привязки пользователя к Telegram-боту."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="telegram_binding_tokens",
        verbose_name="Пользователь",
    )
    token = models.CharField(
        max_length=128,
        unique=True,
        db_index=True,
        verbose_name="Токен привязки",
    )
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="Дата создания")
    expires_at = models.DateTimeField(db_index=True, verbose_name="Действителен до")
    is_used = models.BooleanField(
        default=False,
        db_index=True,
        verbose_name="Использован",
    )
    used_at = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name="Дата использования",
    )

    class Meta:
        verbose_name = "Токен привязки Telegram"
        verbose_name_plural = "Токены привязки Telegram"
        ordering = ("-created_at",)

    def is_valid(self, now: datetime | None = None) -> bool:
        """Проверяет, что токен не использован и срок его действия не истек."""
        current_time = now or timezone.now()
        return not self.is_used and self.used_at is None and self.expires_at > current_time

    def __str__(self) -> str:
        status_label = "использован" if self.is_used else "активен"
        return f"Токен привязки для {self.user} ({status_label})"
