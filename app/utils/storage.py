"""Concrete local file storage used by upload endpoints."""

import os
import uuid

import aiofiles

from ..core.config import settings


class LocalStorage:
    """Local disk storage rooted at EXPORT_FILES_ABSOLUTE_PATH."""

    BACKEND_NAME = "local"

    async def save(self, content: bytes, original_filename: str) -> str:
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


storage = LocalStorage()
