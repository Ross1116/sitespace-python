from __future__ import annotations

from datetime import date, datetime
from typing import Optional
from uuid import UUID

from pydantic import Field

from .base import BaseSchema


class CapacityCell(BaseSchema):
    """Capacity metrics for a single (week, asset_type) cell."""

    demand_hours: float = 0.0
    booked_hours: float = 0.0
    capacity_hours: float = 0.0
    remaining_capacity_hours: float = 0.0
    uncovered_demand_hours: float = 0.0
    demand_utilization_pct: float = 0.0
    booked_utilization_pct: float = 0.0
    available_assets: int = 0
    status: str  # idle | under_utilised | balanced | tight | over_capacity | no_capacity | review_needed
    is_anomalous: bool = False


class CapacityWeekSummary(BaseSchema):
    """Aggregated capacity metrics for a single week across all asset types."""

    total_demand_hours: float = 0.0
    total_booked_hours: float = 0.0
    total_capacity_hours: float = 0.0
    overall_demand_utilization_pct: float = 0.0
    overall_booked_utilization_pct: float = 0.0
    worst_status: str = "idle"


class CapacityAssetTypeSummary(BaseSchema):
    """Aggregated capacity metrics for a single asset type across all weeks."""

    total_demand_hours: float = 0.0
    total_booked_hours: float = 0.0
    total_capacity_hours: float = 0.0
    peak_week: Optional[date] = None
    peak_demand_utilization_pct: float = 0.0
    weeks_over_capacity: int = 0
    weeks_tight: int = 0


class CapacityDashboardDiagnostics(BaseSchema):
    """Diagnostic metadata attached to a capacity dashboard response."""

    unresolved_asset_count: int = 0
    other_demand_hours_total: float = 0.0
    excluded_asset_types: list[str] = Field(default_factory=list)
    snapshot_id: Optional[UUID] = None
    snapshot_date: Optional[date] = None
    snapshot_refreshed_at: Optional[datetime] = None
    total_assets_evaluated: int = 0
    excluded_not_planning_ready: int = 0
    excluded_retired: int = 0
    capacity_computed_at: datetime
    assumptions: list[str] = Field(default_factory=list)


class CapacityDashboardResponse(BaseSchema):
    """Full capacity dashboard response."""

    project_id: UUID
    upload_id: Optional[UUID] = None
    start_week: date
    weeks: list[date] = Field(default_factory=list)
    work_days_per_week: int = 5
    asset_types: list[str] = Field(default_factory=list)
    rows: dict[str, dict[str, CapacityCell]] = Field(default_factory=dict)
    summary_by_week: dict[str, CapacityWeekSummary] = Field(default_factory=dict)
    summary_by_asset_type: dict[str, CapacityAssetTypeSummary] = Field(default_factory=dict)
    diagnostics: Optional[CapacityDashboardDiagnostics] = None
    message: Optional[str] = None
