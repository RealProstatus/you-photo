"""Сервис объектного/локального хранения фотографий (IFileStorage и MinioFileStorage)."""

from __future__ import annotations

import re
import uuid
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any
from uuid import UUID

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError
from django.apps import apps
from django.conf import settings
from django.core.exceptions import AppRegistryNotReady, ImproperlyConfigured
from django.db import DatabaseError
from django.utils import timezone

from apps.storage.dto import FileMetadata
from apps.storage.validators import FileValidator

_SAFE_STEM_RE = re.compile(r"[^A-Za-z0-9_-]+")


class IFileStorage(ABC):
    """Абстрактный интерфейс хранилища файлов фотографий."""

    @abstractmethod
    def upload_photo(self, file_bytes: bytes, filename: str) -> FileMetadata:
        """Валидирует и сохраняет фотографию в хранилище."""

    @abstractmethod
    def get_download_url(
        self,
        file_key: str,
        ttl_seconds: int = 3600,
        photo_id: str | UUID | None = None,
    ) -> str:
        """Возвращает URL для скачивания файла."""

    @abstractmethod
    def get_file_bytes(self, file_key: str) -> bytes:
        """Возвращает бинарное содержимое файла из хранилища."""

    @abstractmethod
    def delete_photo(self, file_key: str) -> bool:
        """Удаляет файл из хранилища. Возвращает True при удалении, False если не найден."""

    @abstractmethod
    def exists(self, file_key: str) -> bool:
        """Проверяет наличие файла в хранилище."""

    def save_file(
        self,
        file_bytes: bytes,
        filename: str,
        content_type: str = "",
    ) -> FileMetadata:
        """Синоним upload_photo для совместимости с контрактом core <-> storage."""
        del content_type
        return self.upload_photo(file_bytes, filename)

    def read_file(self, file_key: str) -> bytes:
        """Синоним get_file_bytes для совместимости с контрактом core <-> storage."""
        return self.get_file_bytes(file_key)

    def delete_file(self, file_key: str) -> bool:
        """Синоним delete_photo для совместимости с контрактом core <-> storage."""
        return self.delete_photo(file_key)


class MinioFileStorage(IFileStorage):
    """Реализация хранилища с поддержкой MinIO/S3 и локального fallback в MEDIA_ROOT/storage/."""

    def __init__(
        self,
        *,
        use_minio: bool | None = None,
        endpoint_url: str | None = None,
        access_key: str | None = None,
        secret_key: str | None = None,
        bucket_name: str | None = None,
        use_ssl: bool | None = None,
        media_root: str | Path | None = None,
        media_url: str | None = None,
        validator: FileValidator | None = None,
        s3_client: Any = None,
    ) -> None:
        self.use_minio = (
            bool(getattr(settings, "USE_MINIO", False))
            if use_minio is None
            else bool(use_minio)
        )
        self.endpoint_url = (
            endpoint_url
            if endpoint_url is not None
            else str(getattr(settings, "MINIO_ENDPOINT_URL", "http://localhost:9000"))
        )
        self.access_key = (
            access_key
            if access_key is not None
            else str(getattr(settings, "MINIO_ACCESS_KEY", "minioadmin"))
        )
        self.secret_key = (
            secret_key
            if secret_key is not None
            else str(getattr(settings, "MINIO_SECRET_KEY", "minioadmin123"))
        )
        self.bucket_name = (
            bucket_name
            if bucket_name is not None
            else str(getattr(settings, "MINIO_BUCKET_NAME", "youphoto-orders"))
        )
        self.use_ssl = (
            bool(getattr(settings, "MINIO_USE_SSL", False))
            if use_ssl is None
            else bool(use_ssl)
        )
        resolved_media_root = (
            media_root
            if media_root is not None
            else getattr(settings, "MEDIA_ROOT", "media")
        )
        self.storage_root = Path(resolved_media_root) / "storage"
        self.media_url = (
            media_url
            if media_url is not None
            else str(getattr(settings, "MEDIA_URL", "/media/"))
        )
        self.validator = validator or FileValidator()
        self._s3_client = s3_client
        self._bucket_verified = False

    def _get_s3_client(self) -> Any:
        if self._s3_client is None:
            self._s3_client = boto3.client(
                "s3",
                endpoint_url=self.endpoint_url,
                aws_access_key_id=self.access_key,
                aws_secret_access_key=self.secret_key,
                use_ssl=self.use_ssl,
                config=Config(signature_version="s3v4"),
            )
        return self._s3_client

    def _ensure_bucket(self) -> None:
        if self._bucket_verified:
            return
        client = self._get_s3_client()
        try:
            client.head_bucket(Bucket=self.bucket_name)
        except ClientError as exc:
            error_code = str(exc.response.get("Error", {}).get("Code", ""))
            if error_code in {"404", "NoSuchBucket", "NotFound"}:
                client.create_bucket(Bucket=self.bucket_name)
            else:
                raise
        self._bucket_verified = True

    @staticmethod
    def _build_file_key(filename: str) -> tuple[str, str]:
        raw_name = str(filename or "").replace("\\", "/").split("/")[-1].strip()
        path_obj = Path(raw_name)
        ext = path_obj.suffix.lower()
        clean_stem = _SAFE_STEM_RE.sub("_", path_obj.stem).strip("._-")
        if not clean_stem:
            clean_stem = "photo"
        clean_stem = clean_stem[:80]
        clean_filename = f"{clean_stem}{ext}"
        now = timezone.now()
        file_key = f"orders/{now:%Y}/{now:%m}/{uuid.uuid4().hex}_{clean_filename}"
        original_name = raw_name[:255] if raw_name else clean_filename
        return file_key, original_name

    def _resolve_local_path(self, file_key: str) -> Path:
        raw_key = str(file_key or "")
        if not raw_key or raw_key.startswith(("/", "\\")):
            raise ValueError("Недопустимый ключ файла.")
        normalized = raw_key.replace("\\", "/")
        first_segment = normalized.split("/", 1)[0]
        if ":" in first_segment:
            raise ValueError("Недопустимый ключ файла.")
        parts = [part for part in normalized.split("/") if part and part != "."]
        if not parts or any(part == ".." for part in parts):
            raise ValueError("Попытка выхода за пределы директории хранилища.")
        root = self.storage_root.resolve()
        target = (root / Path(*parts)).resolve()
        if not target.is_relative_to(root):
            raise ValueError("Попытка выхода за пределы директории хранилища.")
        return target

    @staticmethod
    def _resolve_photo_id(file_key: str) -> str | None:
        if not apps.ready:
            return None
        try:
            from apps.core.models import PhotoFile

            photo_id = (
                PhotoFile.objects.filter(file_key=file_key)
                .values_list("id", flat=True)
                .first()
            )
            return str(photo_id) if photo_id is not None else None
        except (AppRegistryNotReady, ImproperlyConfigured, DatabaseError):
            return None

    def upload_photo(self, file_bytes: bytes, filename: str) -> FileMetadata:
        file_format, mime_type = self.validator.validate_and_detect(file_bytes, filename)
        file_key, original_name = self._build_file_key(filename)

        if self.use_minio:
            self._ensure_bucket()
            self._get_s3_client().put_object(
                Bucket=self.bucket_name,
                Key=file_key,
                Body=file_bytes,
                ContentType=mime_type,
            )
        else:
            target = self._resolve_local_path(file_key)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(file_bytes)

        return FileMetadata(
            file_key=file_key,
            original_name=original_name,
            mime_type=mime_type,
            file_format=file_format,
            size_bytes=len(file_bytes),
        )

    def get_download_url(
        self,
        file_key: str,
        ttl_seconds: int = 3600,
        photo_id: str | UUID | None = None,
    ) -> str:
        if self.use_minio:
            self._ensure_bucket()
            return str(
                self._get_s3_client().generate_presigned_url(
                    "get_object",
                    Params={"Bucket": self.bucket_name, "Key": file_key},
                    ExpiresIn=int(ttl_seconds),
                )
            )

        resolved_id = str(photo_id) if photo_id is not None else self._resolve_photo_id(file_key)
        if resolved_id:
            return f"/api/v1/photos/{resolved_id}/download/"

        clean_key = str(file_key or "").replace("\\", "/").lstrip("/")
        return f"{self.media_url.rstrip('/')}/storage/{clean_key}"

    def get_file_bytes(self, file_key: str) -> bytes:
        if self.use_minio:
            try:
                response = self._get_s3_client().get_object(
                    Bucket=self.bucket_name,
                    Key=file_key,
                )
            except ClientError as exc:
                error_code = str(exc.response.get("Error", {}).get("Code", ""))
                if error_code in {"404", "NoSuchKey", "NotFound"}:
                    raise FileNotFoundError(f"Файл не найден в хранилище: {file_key}") from exc
                raise
            return bytes(response["Body"].read())

        target = self._resolve_local_path(file_key)
        if not target.is_file():
            raise FileNotFoundError(f"Файл не найден в хранилище: {file_key}")
        return target.read_bytes()

    def delete_photo(self, file_key: str) -> bool:
        if self.use_minio:
            if not self.exists(file_key):
                return False
            self._get_s3_client().delete_object(Bucket=self.bucket_name, Key=file_key)
            return True

        target = self._resolve_local_path(file_key)
        if not target.is_file():
            return False
        target.unlink()
        return True

    def exists(self, file_key: str) -> bool:
        if self.use_minio:
            try:
                self._get_s3_client().head_object(Bucket=self.bucket_name, Key=file_key)
                return True
            except ClientError as exc:
                error_code = str(exc.response.get("Error", {}).get("Code", ""))
                if error_code in {"404", "NoSuchKey", "NoSuchBucket", "NotFound"}:
                    return False
                raise

        target = self._resolve_local_path(file_key)
        return target.is_file()
