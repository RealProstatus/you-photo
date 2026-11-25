"""Management-команда очистки просроченных файлов фотографий из хранилища."""

from __future__ import annotations

from django.core.management.base import BaseCommand, CommandError

from apps.storage.tasks import RetentionCleanupJob


class Command(BaseCommand):
    help = (
        "Удаляет исходные файлы фотографий с истекшим сроком хранения "
        "(по умолчанию 30 дней после завершения заказа)."
    )

    def add_arguments(self, parser) -> None:
        parser.add_argument(
            "--days",
            type=int,
            default=None,
            help="Переопределить срок хранения файлов (в днях).",
        )

    def handle(self, *args, **options) -> None:
        days = options.get("days")
        if days is not None and days < 0:
            raise CommandError("Количество дней хранения не может быть отрицательным.")

        purged_count = RetentionCleanupJob(retention_days=days).purge_expired_photos()
        self.stdout.write(
            self.style.SUCCESS(
                f"Очистка хранилища завершена. Удалено файлов: {purged_count}."
            )
        )
