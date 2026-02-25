from uuid import UUID
from typing import Optional

from .base import BaseSchema, TimestampSchema
from .stored_file import StoredFileBrief
from .user import UserBriefResponse


class SitePlanCreate(BaseSchema):
    title: str
    file_id: UUID
    project_id: UUID


class SitePlanUpdate(BaseSchema):
    title: Optional[str] = None
    file_id: Optional[UUID] = None  # replaces the file; old file is deleted from disk + DB


class SitePlanResponse(TimestampSchema):
    id: UUID
    title: str
    project_id: UUID
    file: StoredFileBrief
    created_by: UserBriefResponse
