"""
Storage backend abstraction.

Current implementation: local disk.
Future: swap LocalStorageBackend for S3Backend, GCSBackend, etc.
Only this module and the files API need to change when scaling storage.
"""

import os
import uuid
from abc import ABC, abstractmethod

import aiofiles

from ..core.config import settings


class StorageBackend(ABC):
    BACKEND_NAME: str

    @abstractmethod
    async def save(self, content: bytes, original_filename: str) -> str:
        """Persist content and return an opaque storage_path string."""

    @abstractmethod
    def read(self, storage_path: str) -> bytes:
        """Return raw file bytes given a storage_path."""

    @abstractmethod
    def delete(self, storage_path: str) -> bool:
        """Delete the file. Returns True if deleted, False if not found."""

    @abstractmethod
    def exists(self, storage_path: str) -> bool:
        """Return True if the file exists in storage."""


class LocalStorageBackend(StorageBackend):
    """Stores files on the local filesystem under EXPORT_FILES_ABSOLUTE_PATH/uploads/."""

    BACKEND_NAME = "local"

    async def save(self, content: bytes, original_filename: str) -> str:
        # Store directly under EXPORT_FILES_ABSOLUTE_PATH (the /app/uploads mounted volume)
        ext = os.path.splitext(original_filename)[1].lower()
        unique_name = f"{uuid.uuid4()}{ext}"
        upload_dir = settings.export_files_absolute_path.rstrip("/").rstrip("\\")
        os.makedirs(upload_dir, exist_ok=True)
        file_path = os.path.join(upload_dir, unique_name)
        async with aiofiles.open(file_path, "wb") as f:
            await f.write(content)
        return file_path

    def read(self, storage_path: str) -> bytes:
        with open(storage_path, "rb") as f:
            return f.read()

    def delete(self, storage_path: str) -> bool:
        try:
            if os.path.exists(storage_path):
                os.remove(storage_path)
                return True
            return False
        except OSError:
            return False

    def exists(self, storage_path: str) -> bool:
        return os.path.exists(storage_path)


# Singleton — import this in endpoints and services
storage: StorageBackend = LocalStorageBackend()
