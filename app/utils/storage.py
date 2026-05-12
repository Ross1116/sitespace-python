"""Concrete local file storage used by upload endpoints."""

import logging
import os
import uuid
from urllib.parse import urlparse

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
        """Read local bytes first; worker/nightly may fetch remotely if missing."""
        if os.path.exists(storage_path):
            with open(storage_path, "rb") as f:
                return f.read()
        if settings.SERVICE_ROLE != "web" and settings.WEB_INTERNAL_URL:
            return self._read_remote(storage_path)
        with open(storage_path, "rb") as f:
            return f.read()

    def _remote_fetch_url(self) -> str:
        raw_url = settings.WEB_INTERNAL_URL.strip().rstrip("/")
        parsed = urlparse(raw_url)
        if "://" not in raw_url and raw_url and not raw_url.startswith("/"):
            raw_url = f"http://{raw_url}"
            parsed = urlparse(raw_url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError(
                "WEB_INTERNAL_URL must be an http(s) URL for remote file fetches"
            )
        return f"{raw_url}/internal/files/fetch"

    def _read_remote(self, storage_path: str) -> bytes:
        """Fetch file from web service over Railway private networking."""
        url = self._remote_fetch_url()
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
            if exc.response.status_code == 404:
                raise FileNotFoundError(
                    f"File not found on web service: {storage_path}"
                ) from exc
            # Non-404 errors (500, 403, etc.) are transient or config issues —
            # propagate as-is so the worker retry logic can distinguish them.
            raise
        except httpx.RequestError as exc:
            logger.error("Remote file fetch connection error: %s path=%s", exc, storage_path)
            raise ConnectionError(
                f"Cannot reach web service to fetch {storage_path}"
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
