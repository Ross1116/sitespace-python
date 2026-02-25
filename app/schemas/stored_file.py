from uuid import UUID
from datetime import datetime
from typing import Optional

from .base import BaseSchema


class FileUploadResponse(BaseSchema):
    """Returned immediately after a successful file upload (phase 1 of two-phase UX)."""

    file_id: UUID
    suggested_title: str
    original_filename: str
    content_type: str
    file_size: int
    preview_url: str  # /api/files/{file_id}/preview


class StoredFileBrief(BaseSchema):
    """Embedded file info returned inside SitePlanResponse."""

    id: UUID
    original_filename: str
    content_type: Optional[str] = None
    file_size: Optional[int] = None
    preview_url: str   # /api/files/{id}/preview  (thumbnail)
    image_url: str     # /api/files/{id}/image    (full quality)
    raw_url: str       # /api/files/{id}           (raw download)
    created_at: Optional[datetime] = None
