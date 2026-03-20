from __future__ import annotations

from typing import Any
from uuid import UUID

from .base import BaseSchema


class LookaheadResponse(BaseSchema):
    """Manager lookahead view for a project."""

    project_id: UUID
    snapshot_id: UUID | None = None
    snapshot_date: str | None = None
    timezone: str | None = None
    rows: list[Any] = []
    message: str | None = None


class LookaheadAlertsResponse(BaseSchema):
    """Anomaly alerts for a project lookahead snapshot."""

    project_id: UUID
    snapshot_id: UUID | None = None
    snapshot_date: str | None = None
    alerts: dict[str, Any] = {}


class SubNotification(BaseSchema):
    """A single subcontractor notification entry."""

    id: UUID
    activity_id: UUID
    asset_type: str | None = None
    trigger_type: str | None = None
    status: str | None = None
    sent_at: str | None = None
    acted_at: str | None = None
    booking_id: UUID | None = None
    created_at: str | None = None


class SubcontractorLookaheadResponse(BaseSchema):
    """Subcontractor-scoped lookahead view with notifications."""

    project_id: UUID
    sub_id: UUID
    snapshot_date: str | None = None
    timezone: str | None = None
    rows: list[Any] = []
    notifications: list[SubNotification] = []


class SubAssetSuggestionsResponse(BaseSchema):
    """Per-subcontractor asset demand suggestions."""

    project_id: UUID
    snapshot_date: str | None = None
    suggestions: list[Any] = []


class SnapshotHistoryItem(BaseSchema):
    """Summary of a single lookahead snapshot in the history list."""

    snapshot_id: UUID
    snapshot_date: str
    created_at: str | None = None
    anomaly_flags: dict[str, Any] = {}


class LookaheadHistoryResponse(BaseSchema):
    """History of lookahead snapshots for a project."""

    project_id: UUID
    history: list[SnapshotHistoryItem] = []
