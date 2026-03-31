"""
Internal API endpoints for cross-service communication over Railway private networking.

These endpoints are NOT exposed to the public internet — they are accessed only by
worker/nightly services over Railway's private network (*.railway.internal).
Protected by a shared INTERNAL_API_SECRET header.
"""

import logging
import os

from fastapi import APIRouter, Header, HTTPException, Query
from fastapi.responses import Response

from ..core.config import settings
from ..utils.storage import storage

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/internal", tags=["Internal"])


def _verify_internal_secret(x_internal_secret: str = Header(...)) -> None:
    """Verify the shared secret for internal service-to-service calls."""
    if not settings.INTERNAL_API_SECRET:
        raise HTTPException(status_code=503, detail="Internal API not configured")
    if x_internal_secret != settings.INTERNAL_API_SECRET:
        raise HTTPException(status_code=403, detail="Invalid internal secret")


def _validate_storage_path(path: str) -> str:
    """Resolve and confine path to the upload root to prevent directory traversal."""
    upload_root = os.path.realpath(settings.export_files_absolute_path)
    resolved = os.path.realpath(path)
    if not resolved.startswith(upload_root + os.sep) and resolved != upload_root:
        raise HTTPException(status_code=403, detail="Path is outside the upload root")
    return resolved


@router.get("/files/fetch")
async def fetch_file(
    path: str = Query(..., description="The storage_path of the file to retrieve"),
    x_internal_secret: str = Header(...),
) -> Response:
    """
    Serve a file from local storage to the worker service.

    The worker cannot access the web container's filesystem, so it calls this
    endpoint over Railway private networking to retrieve uploaded file bytes.
    """
    _verify_internal_secret(x_internal_secret)

    safe_path = _validate_storage_path(path)

    try:
        content = storage.read(safe_path)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="File not found at storage path")
    except Exception as exc:
        logger.exception("Internal file fetch failed for path=%s", safe_path)
        raise HTTPException(status_code=500, detail="Failed to read file") from exc

    return Response(
        content=content,
        media_type="application/octet-stream",
        headers={"Content-Length": str(len(content))},
    )
