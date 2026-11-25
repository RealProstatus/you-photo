# 02. Гайдлайн модуля: File Storage (`backend/apps/storage/`)

**Основание**: `file_storage_module.puml`, требования `U-C-03`, `U-M-04`, `S-ENV-04`, `S-INT-01`, `S-SEC-04`.

---

## 1. Структура пакета

```text
backend/apps/storage/
├── __init__.py
├── dto.py             # FileMetadata DTO
├── validators.py      # FileValidator (форматы JPEG, PNG, HEIC, TIFF и размер <= 50 МБ)
├── services.py        # IFileStorage, MinioFileStorage
└── tasks.py           # RetentionCleanupJob (удаление файлов через 30 дней после COMPLETED)
```

---

## 2. Классы и контракты (`file_storage_module.puml`)

### 2.1. `FileMetadata` (`dto.py`)
Неизменяемый `dataclass(frozen=True)`:
- `file_key: str`
- `original_name: str`
- `mime_type: str`
- `file_format: str` (`JPEG`, `PNG`, `HEIC`, `TIFF`)
- `size_bytes: int`

### 2.2. `FileValidator` (`validators.py`)
Отвечает за жесткую валидацию загружаемых файлов (`U-C-03`, `S-INT-01`):
- `allowed_extensions = {".jpg", ".jpeg", ".png", ".heic", ".heif", ".tif", ".tiff"}`
- `allowed_formats = {"JPEG", "PNG", "HEIC", "TIFF"}`
- `max_size_bytes = 50 * 1024 * 1024` (ровно **50 МБ включительно**).
- **Логика `validate(file_bytes: bytes, filename: str) -> str`**:
  1. Проверка размера: если `len(file_bytes) > max_size_bytes` (например, 51 МБ) или `len(file_bytes) == 0`, выбрасывает `FileValidationError("Размер файла превышает допустимый лимит 50 МБ. Допустимые форматы: JPEG, PNG, HEIC, TIFF до 50 МБ.")`.
  2. Проверка расширения и сигнатуры (magic bytes / Pillow + `pillow_heif`):
     - Файлы `BMP`, `PDF`, `GIF`, `WEBP`, исполняемые файлы или битые изображения должны **отклоняться** не позднее чем за 5 секунд с понятным сообщением на русском языке (`U-C-03`: указание причины и списка допустимых форматов `JPEG, PNG, HEIC, TIFF до 50 МБ`).
  3. Возвращает нормализованный формат (`JPEG`, `PNG`, `HEIC` или `TIFF`).

### 2.3. `IFileStorage` и `MinioFileStorage` (`services.py`)
Интерфейс `IFileStorage`:
- `upload_photo(file_bytes: bytes, filename: str) -> FileMetadata`
- `get_download_url(file_key: str, ttl_seconds: int = 3600) -> str`
- `get_file_bytes(file_key: str) -> bytes`
- `delete_photo(file_key: str) -> bool`

Реализация `MinioFileStorage`:
- Вызывает `FileValidator.validate(file_bytes, filename)` перед сохранением.
- Генерирует уникальный безопасный ключ `orders/{yyyy}/{mm}/{uuid4}_{clean_filename}`.
- Поддерживает два бэкенда хранения без изменения интерфейса (`S-ENV-04`):
  - **MinIO / S3** (через `boto3` / `django-storages` при `USE_MINIO=True` в Docker/Production);
  - **Локальное изолированное S3-совместимое файловое хранилище** в `MEDIA_ROOT/storage/` (при `USE_MINIO=False` для локальной разработки и быстрого прогона автотестов `PyTest`).
- Метод `get_download_url` возвращает либо presigned URL MinIO, либо защищенный внутренний URL `/api/v1/photos/<uuid:photo_id>/download/`, который проверяет права доступа роли (`U-M-04`: скачать исходник может только менеджер салона-исполнителя/выдачи этого заказа, сам клиент или администратор).

### 2.4. `RetentionCleanupJob` (`tasks.py`)
Фоновая служба очистки (`S-SEC-04`, `US-07`):
- `retention_days: int = 30`
- `purge_expired_photos() -> int`:
  - Находит все записи `PhotoFile`, у которых `is_deleted_from_storage=False` и:
    - либо `delete_after <= timezone.now()`,
    - либо связанный заказ имеет `status == OrderStatus.COMPLETED` и `completed_at <= timezone.now() - timedelta(days=retention_days)`.
  - Вызывает `file_storage.delete_photo(photo.file_key)`.
  - Помечает `photo.is_deleted_from_storage = True` и сохраняет запись.
  - **Важно (`S-SEC-04`)**: Сама карточка заказа (`Order`), позиции (`OrderItem`) и история (`OrderStatusHistory`) **не удаляются** и остаются доступными.
  - Должна быть доступна также как management-команда `python manage.py purge_expired_photos` для запуска из планировщика и проверки на тестовом стенде.
