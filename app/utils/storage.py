"""Concrete local file storage used by upload endpoints."""

import logging
import os
import uuid

import aiofiles
import httpx

from ..core.config import settings

logger = logging.getLogger(__name__)


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
        """Read file bytes — locally if on web, remotely if on worker/nightly."""
        if settings.SERVICE_ROLE != "web" and settings.WEB_INTERNAL_URL:
            return self._read_remote(storage_path)
        with open(storage_path, "rb") as f:
            return f.read()

    def _read_remote(self, storage_path: str) -> bytes:
        """Fetch file from web service over Railway private networking."""
        url = f"{settings.WEB_INTERNAL_URL.rstrip('/')}/internal/files/fetch"
        try:
            resp = httpx.get(
                url,
                params={"path": storage_path},
                headers={"X-Internal-Secret": settings.INTERNAL_API_SECRET},
                timeout=60.0,
            )
            resp.raise_for_status()
            return resp.content
        except httpx.HTTPStatusError as exc:
            logger.error(
                "Remote file fetch failed: status=%d path=%s",
                exc.response.status_code, storage_path,
            )
            raise FileNotFoundError(
                f"Remote file fetch returned {exc.response.status_code} for {storage_path}"
            ) from exc
        except httpx.RequestError as exc:
            logger.error("Remote file fetch connection error: %s path=%s", exc, storage_path)
            raise FileNotFoundError(
                f"Remote file fetch connection failed for {storage_path}"
            ) from exc

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
