"""DTO для модуля файлового хранилища."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class FileMetadata:
    """Метаданные загруженного и сохраненного файла фотографии."""

    file_key: str
    original_name: str
    mime_type: str
    file_format: str
    size_bytes: int
