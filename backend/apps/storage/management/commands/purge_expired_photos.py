"""Management command to purge expired photo files from storage."""

from __future__ import annotations

from typing import Any

from django.core.management.base import BaseCommand, CommandError, CommandParser

from apps.storage.tasks import RetentionCleanupJob


class Command(BaseCommand):
    """Django management command that removes expired photo files from storage."""

    help = (
        "Удаляет исходные файлы фотографий с истекшим сроком хранения "
        "(по умолчанию 30 дней после завершения заказа)."
    )

    def add_arguments(self, parser: CommandParser) -> None:
        """Register CLI arguments for the management command.

        Args:
            parser: Django command argument parser.
        """
        parser.add_argument(
            "--days",
            type=int,
            default=None,
            help="Переопределить срок хранения файлов (в днях).",
        )

    def handle(self, *args: Any, **options: Any) -> None:
        """Execute the expired photo cleanup job.

        Args:
            *args: Positional arguments passed by Django's command runner.
            **options: Parsed CLI options, including optional ``days`` override.

        Raises:
            CommandError: If ``days`` is negative.
        """
        del args
        days = options.get("days")
        if days is not None and days < 0:
            raise CommandError("Количество дней хранения не может быть отрицательным.")

        purged_count = RetentionCleanupJob(retention_days=days).purge_expired_photos()
        self.stdout.write(
            self.style.SUCCESS(
                f"Очистка хранилища завершена. Удалено файлов: {purged_count}."
            )
        )
