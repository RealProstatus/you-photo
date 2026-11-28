"""Data transfer objects for the file storage module."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class FileMetadata:
    """Immutable metadata of an uploaded and stored photo file."""

    file_key: str
    original_name: str
    mime_type: str
    file_format: str
    size_bytes: int
