from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy.orm import Session

from ..models.programme import ProgrammeUpload

LEGACY_UPLOAD_STATUS_COMPLETED_WITH_WARNINGS = "degraded"
UPLOAD_STATUS_PROCESSING = "processing"
UPLOAD_STATUS_COMMITTED = "committed"
UPLOAD_STATUS_COMPLETED_WITH_WARNINGS = "completed_with_warnings"
UPLOAD_STATUS_FAILED = "failed"

PLANNING_SUCCESSFUL_UPLOAD_STATUSES: tuple[str, ...] = (
    UPLOAD_STATUS_COMMITTED,
    UPLOAD_STATUS_COMPLETED_WITH_WARNINGS,
)
PLANNING_SUCCESSFUL_UPLOAD_STATUSES_WITH_LEGACY: tuple[str, ...] = (
    UPLOAD_STATUS_COMMITTED,
    UPLOAD_STATUS_COMPLETED_WITH_WARNINGS,
    LEGACY_UPLOAD_STATUS_COMPLETED_WITH_WARNINGS,
)


def normalize_upload_status(status: str | None) -> str | None:
    if status == LEGACY_UPLOAD_STATUS_COMPLETED_WITH_WARNINGS:
        return UPLOAD_STATUS_COMPLETED_WITH_WARNINGS
    return status


def get_upload_status(upload: Any) -> str | None:
    return normalize_upload_status(getattr(upload, "status", None))


def is_upload_successful_for_planning(upload: Any) -> bool:
    return get_upload_status(upload) in PLANNING_SUCCESSFUL_UPLOAD_STATUSES


def is_upload_readable(upload: Any) -> bool:
    return is_upload_successful_for_planning(upload)


def is_upload_terminal_success(upload: Any) -> bool:
    return is_upload_successful_for_planning(upload)


def upload_has_warnings(upload: Any) -> bool:
    return get_upload_status(upload) == UPLOAD_STATUS_COMPLETED_WITH_WARNINGS


def is_upload_processing(upload: Any) -> bool:
    return get_upload_status(upload) == UPLOAD_STATUS_PROCESSING


def is_upload_failed(upload: Any) -> bool:
    return get_upload_status(upload) == UPLOAD_STATUS_FAILED


def get_active_programme_upload(project_id: uuid.UUID, db: Session) -> ProgrammeUpload | None:
    return (
        db.query(ProgrammeUpload)
        .filter(
            ProgrammeUpload.project_id == project_id,
            ProgrammeUpload.status.in_(PLANNING_SUCCESSFUL_UPLOAD_STATUSES_WITH_LEGACY),
        )
        .order_by(ProgrammeUpload.version_number.desc())
        .first()
    )


def get_previous_successful_programme_upload(
    project_id: uuid.UUID,
    before_version_number: int,
    db: Session,
) -> ProgrammeUpload | None:
    return (
        db.query(ProgrammeUpload)
        .filter(
            ProgrammeUpload.project_id == project_id,
            ProgrammeUpload.version_number < before_version_number,
            ProgrammeUpload.status.in_(PLANNING_SUCCESSFUL_UPLOAD_STATUSES_WITH_LEGACY),
        )
        .order_by(ProgrammeUpload.version_number.desc())
        .first()
    )
