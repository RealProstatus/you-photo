"""Фоновая задача очистки файлов фотографий с истекшим сроком хранения."""

from __future__ import annotations

import logging
from datetime import timedelta

from django.conf import settings
from django.db.models import Q
from django.utils import timezone

from apps.core.enums import OrderStatus
from apps.core.models import PhotoFile
from apps.storage.services import IFileStorage, MinioFileStorage

logger = logging.getLogger(__name__)


class RetentionCleanupJob:
    """Удаляет исходные файлы фотографий через 30 дней после завершения заказа."""

    def __init__(
        self,
        file_storage: IFileStorage | None = None,
        retention_days: int | None = None,
    ) -> None:
        self.file_storage = file_storage or MinioFileStorage()
        if retention_days is None:
            self.retention_days = int(getattr(settings, "FILE_RETENTION_DAYS", 30))
        else:
            self.retention_days = int(retention_days)
        if self.retention_days < 0:
            raise ValueError("Срок хранения файлов не может быть отрицательным.")

    def purge_expired_photos(self) -> int:
        """Удаляет просроченные файлы из хранилища, сохраняя записи заказа и метаданных в БД."""
        now = timezone.now()
        cutoff = now - timedelta(days=self.retention_days)
        expired_photos = (
            PhotoFile.objects.select_related("order")
            .filter(is_deleted_from_storage=False)
            .filter(
                Q(delete_after__isnull=False, delete_after__lte=now)
                | Q(
                    order__status=OrderStatus.COMPLETED,
                    order__completed_at__isnull=False,
                    order__completed_at__lte=cutoff,
                )
            )
        )

        purged_count = 0
        for photo in expired_photos:
            try:
                self.file_storage.delete_photo(photo.file_key)
            except Exception:
                logger.exception(
                    "Не удалось удалить файл %s (PhotoFile %s) из хранилища.",
                    photo.file_key,
                    photo.id,
                )
                continue

            photo.is_deleted_from_storage = True
            photo.save(update_fields=["is_deleted_from_storage"])
            purged_count += 1

        return purged_count
