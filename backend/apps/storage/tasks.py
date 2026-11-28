"""Background cleanup job for expired photo files."""

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
    """Deletes original photo files 30 days after order completion."""

    def __init__(
        self,
        file_storage: IFileStorage | None = None,
        retention_days: int | None = None,
    ) -> None:
        """Initialize the retention cleanup job.

        Args:
            file_storage: Storage backend used to delete expired files.
            retention_days: Number of days to retain files after completion.
                Defaults to ``settings.FILE_RETENTION_DAYS`` (30).

        Raises:
            ValueError: If ``retention_days`` is negative.
        """
        self.file_storage = file_storage or MinioFileStorage()
        if retention_days is None:
            self.retention_days = int(getattr(settings, "FILE_RETENTION_DAYS", 30))
        else:
            self.retention_days = int(retention_days)
        if self.retention_days < 0:
            raise ValueError("Срок хранения файлов не может быть отрицательным.")

    def purge_expired_photos(self) -> int:
        """Delete expired photo files from storage while keeping DB order records.

        Returns:
            Number of photo files deleted during this run.
        """
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
