import mimetypes
import os
from uuid import UUID

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile, status
from fastapi.responses import Response
from sqlalchemy.orm import Session

from ...core.database import get_db
from ...core.security import get_current_active_user, require_role
from ...models.stored_file import StoredFile
from ...models.site_plan import SitePlan
from ...models.user import User
from ...schemas.enums import UserRole
from ...schemas.stored_file import FileUploadResponse
from ...utils.pdf_utils import extract_suggested_title, render_pdf_to_png
from ...utils.storage import storage

router = APIRouter(prefix="/files", tags=["Files"])

MAX_FILE_SIZE = 20 * 1024 * 1024  # 20 MB
ALLOWED_EXTENSIONS = {".pdf", ".jpg", ".jpeg", ".png", ".gif", ".webp"}


def _file_urls(file_id: UUID) -> tuple[str, str, str]:
    """Return (preview_url, image_url, raw_url) for a given file id."""
    base = f"/api/files/{file_id}"
    return f"{base}/preview", f"{base}/image", base


# ---------------------------------------------------------------------------
# Upload
# ---------------------------------------------------------------------------

@router.post("/upload", response_model=FileUploadResponse, status_code=status.HTTP_201_CREATED)
async def upload_file(
    request: Request,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role([UserRole.MANAGER, UserRole.ADMIN])),
):
    """
    Upload a file (PDF or image). Returns a suggested title derived from the
    filename (or PDF metadata) plus the file_id needed for the second phase.

    Phase 1 of the two-phase site-plan upload UX:
      1. POST /api/files/upload  →  { file_id, suggested_title, preview_url }
      2. POST /api/site-plans/   →  { title, file_id, project_id }
    """
    if not file.filename:
        raise HTTPException(status_code=400, detail="No filename provided")

    ext = os.path.splitext(file.filename)[1].lower()
    if ext not in ALLOWED_EXTENSIONS:
        raise HTTPException(
            status_code=400,
            detail=f"File type '{ext}' not allowed. Allowed: {', '.join(sorted(ALLOWED_EXTENSIONS))}",
        )

    # Fast-fail on Content-Length before reading the body
    content_length = request.headers.get("content-length")
    if content_length and int(content_length) > MAX_FILE_SIZE:
        raise HTTPException(status_code=413, detail="File too large. Maximum size is 20 MB")

    content = await file.read()
    # Verify actual size (Content-Length can be spoofed)
    if len(content) > MAX_FILE_SIZE:
        raise HTTPException(status_code=413, detail="File too large. Maximum size is 20 MB")

    content_type = (
        file.content_type
        or mimetypes.guess_type(file.filename)[0]
        or "application/octet-stream"
    )
    suggested_title = extract_suggested_title(content, file.filename)

    storage_path = await storage.save(content, file.filename)

    uploader_id = current_user.id if isinstance(current_user, User) else None
    record = StoredFile(
        original_filename=file.filename,
        storage_backend=storage.BACKEND_NAME,
        storage_path=storage_path,
        content_type=content_type,
        file_size=len(content),
        uploaded_by_id=uploader_id,
    )
    db.add(record)
    db.commit()
    db.refresh(record)

    preview_url, _, _ = _file_urls(record.id)
    return FileUploadResponse(
        file_id=record.id,
        suggested_title=suggested_title,
        original_filename=file.filename,
        content_type=content_type,
        file_size=len(content),
        preview_url=preview_url,
    )


# ---------------------------------------------------------------------------
# Serve helpers
# ---------------------------------------------------------------------------

def _get_stored_file(file_id: UUID, db: Session) -> StoredFile:
    record = db.query(StoredFile).filter(StoredFile.id == file_id).first()
    if not record:
        raise HTTPException(status_code=404, detail="File not found")
    if not storage.exists(record.storage_path):
        raise HTTPException(status_code=404, detail="File data not found on storage")
    return record


def _cache_headers(file_id: UUID, suffix: str) -> dict[str, str]:
    """Shared cache headers for rendered outputs. ETag scoped by file_id + render variant."""
    return {
        "Cache-Control": "public, max-age=3600",
        "ETag": f'"{file_id}-{suffix}"',
    }


def _serve_as_image(record: StoredFile, scale: float, cache_suffix: str) -> Response:
    content = storage.read(record.storage_path)
    ct = (record.content_type or "").lower()
    headers = _cache_headers(record.id, cache_suffix)
    if ct == "application/pdf" or record.original_filename.lower().endswith(".pdf"):
        png_bytes = render_pdf_to_png(content, scale=scale)
        return Response(content=png_bytes, media_type="image/png", headers=headers)
    # Already an image — serve the original with cache headers
    return Response(content=content, media_type=ct or "image/jpeg", headers=headers)


# ---------------------------------------------------------------------------
# Serve endpoints (all authenticated roles)
# ---------------------------------------------------------------------------

@router.get("/{file_id}")
def serve_file_raw(
    file_id: UUID,
    db: Session = Depends(get_db),
    _: User = Depends(get_current_active_user),
):
    """Serve the raw file with its original content-type."""
    record = _get_stored_file(file_id, db)
    content = storage.read(record.storage_path)
    return Response(
        content=content,
        media_type=record.content_type or "application/octet-stream",
        headers={"Content-Disposition": f'inline; filename="{record.original_filename}"'},
    )


@router.get("/{file_id}/preview")
def serve_file_preview(
    file_id: UUID,
    db: Session = Depends(get_db),
    _: User = Depends(get_current_active_user),
):
    """
    Serve the file as a PNG at thumbnail scale (1.5×).
    For PDFs, renders the first page. For images, serves the original.
    Response is cached for 1 hour (ETag + Cache-Control).
    """
    record = _get_stored_file(file_id, db)
    return _serve_as_image(record, scale=1.5, cache_suffix="preview")


@router.get("/{file_id}/image")
def serve_file_image(
    file_id: UUID,
    db: Session = Depends(get_db),
    _: User = Depends(get_current_active_user),
):
    """
    Serve the file as a high-quality PNG (3×).
    Intended for standalone display in popups or detail views.
    Response is cached for 1 hour (ETag + Cache-Control).
    """
    record = _get_stored_file(file_id, db)
    return _serve_as_image(record, scale=3.0, cache_suffix="image")


# ---------------------------------------------------------------------------
# Delete (manager/admin only)
# ---------------------------------------------------------------------------

@router.delete("/{file_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_file(
    file_id: UUID,
    db: Session = Depends(get_db),
    _: User = Depends(require_role([UserRole.MANAGER, UserRole.ADMIN])),
):
    """
    Delete a stored file. Returns 409 if any site plan still references it.
    DB commit happens before storage deletion so a failed disk remove doesn't
    leave the DB in an inconsistent state.
    """
    record = _get_stored_file(file_id, db)

    referenced = db.query(SitePlan).filter(SitePlan.file_id == file_id).first()
    if referenced:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Cannot delete file: it is referenced by one or more site plans. Delete the site plan first.",
        )

    storage_path = record.storage_path
    db.delete(record)
    db.commit()  # DB consistent first

    storage.delete(storage_path)  # then disk
