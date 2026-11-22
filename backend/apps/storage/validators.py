"""Валидатор загружаемых фотографий (форматы JPEG, PNG, HEIC, TIFF и размер до 50 МБ)."""

from __future__ import annotations

from io import BytesIO
from pathlib import Path

import pillow_heif
from django.conf import settings
from PIL import Image, UnidentifiedImageError

from apps.core.exceptions import FileValidationError

pillow_heif.register_heif_opener()

ALLOWED_EXTENSIONS: frozenset[str] = frozenset(
    {".jpg", ".jpeg", ".png", ".heic", ".heif", ".tif", ".tiff"}
)
ALLOWED_FORMATS: frozenset[str] = frozenset({"JPEG", "PNG", "HEIC", "TIFF"})
EXTENSION_TO_FORMAT: dict[str, str] = {
    ".jpg": "JPEG",
    ".jpeg": "JPEG",
    ".png": "PNG",
    ".heic": "HEIC",
    ".heif": "HEIC",
    ".tif": "TIFF",
    ".tiff": "TIFF",
}
FORMAT_TO_MIME: dict[str, str] = {
    "JPEG": "image/jpeg",
    "PNG": "image/png",
    "HEIC": "image/heic",
    "TIFF": "image/tiff",
}

_ALLOWED_FORMATS_HINT = "Допустимые форматы: JPEG, PNG, HEIC, TIFF до 50 МБ."


_HEIF_ERROR = getattr(pillow_heif, "HeifError", None)
_IMAGE_DECODE_ERRORS: tuple[type[BaseException], ...] = (
    UnidentifiedImageError,
    OSError,
    SyntaxError,
    ValueError,
    EOFError,
    Image.DecompressionBombError,
    *((_HEIF_ERROR,) if isinstance(_HEIF_ERROR, type) else ()),
)


class FileValidator:
    """Проверяет размер, расширение и целостность сигнатуры изображений."""

    allowed_extensions: set[str]
    allowed_formats: set[str]
    max_size_bytes: int

    def __init__(self, max_size_bytes: int | None = None) -> None:
        self.allowed_extensions = set(ALLOWED_EXTENSIONS)
        self.allowed_formats = set(ALLOWED_FORMATS)
        if max_size_bytes is None:
            max_mb = int(getattr(settings, "MAX_UPLOAD_SIZE_MB", 50))
            self.max_size_bytes = max_mb * 1024 * 1024
        else:
            self.max_size_bytes = int(max_size_bytes)

    @staticmethod
    def _normalize_format(raw_format: str, ext: str) -> str:
        upper = raw_format.upper()
        if upper == "HEIF":
            return "HEIC"
        if upper == "MPO" and ext in {".jpg", ".jpeg"}:
            return "JPEG"
        return upper

    def validate_and_detect(self, file_bytes: bytes, filename: str) -> tuple[str, str]:
        """Валидирует файл и возвращает кортеж (нормализованный формат, MIME-тип)."""
        if not file_bytes:
            raise FileValidationError(f"Загруженный файл пуст. {_ALLOWED_FORMATS_HINT}")

        if len(file_bytes) > self.max_size_bytes:
            raise FileValidationError(
                f"Размер файла превышает допустимый лимит 50 МБ. {_ALLOWED_FORMATS_HINT}"
            )

        ext = Path(str(filename or "").replace("\\", "/")).suffix.lower()
        if not ext or ext not in self.allowed_extensions:
            ext_label = ext or "без расширения"
            raise FileValidationError(
                f"Неподдерживаемый формат файла «{ext_label}». {_ALLOWED_FORMATS_HINT}"
            )

        try:
            with Image.open(BytesIO(file_bytes)) as img:
                raw_format = img.format or ""
                img.verify()
            with Image.open(BytesIO(file_bytes)) as img_load:
                img_load.load()
        except _IMAGE_DECODE_ERRORS as exc:
            raise FileValidationError(
                f"Файл поврежден или не является корректным изображением. {_ALLOWED_FORMATS_HINT}"
            ) from exc

        normalized_format = self._normalize_format(raw_format, ext)
        if (
            normalized_format not in self.allowed_formats
            or EXTENSION_TO_FORMAT.get(ext) != normalized_format
        ):
            raise FileValidationError(
                f"Содержимое файла не соответствует допустимому формату или расширению. "
                f"{_ALLOWED_FORMATS_HINT}"
            )

        return normalized_format, FORMAT_TO_MIME[normalized_format]

    def validate(self, file_bytes: bytes, filename: str) -> str:
        """Проверяет файл и возвращает нормализованный формат (JPEG, PNG, HEIC, TIFF)."""
        file_format, _ = self.validate_and_detect(file_bytes, filename)
        return file_format
