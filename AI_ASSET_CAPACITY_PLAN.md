---
name: Capacity Planning Dashboard
overview: Add a Capacity Planning Dashboard with per-asset max capacity, smart recomputation triggers on asset/PMP changes, and 8-week paginated API. Requires Asset model changes, new dashboard service, and new API endpoints.
todos:
  - id: migration
    content: "DB migration: add max_hours_per_day NUMERIC(4,1) nullable column to assets table"
    status: pending
  - id: asset-model
    content: Add max_hours_per_day to Asset model, AssetCreate, AssetUpdate, AssetResponse, AssetBriefResponse schemas
    status: pending
  - id: asset-crud
    content: Update asset CRUD create/update to persist max_hours_per_day; add helper effective_max_hours(asset, db) that falls back to asset_types.max_hours_per_day
    status: pending
  - id: recompute-triggers
    content: "Add recomputation triggers in asset create/update API: new asset type -> full re-process programme; existing type -> refresh lookahead for that type"
    status: pending
  - id: constants
    content: Add CAPACITY_UTIL_BALANCED_MIN, CAPACITY_UTIL_OVER_PLANNED_MIN, CAPACITY_DASHBOARD_PAGE_SIZE=8 to constants.py
    status: pending
  - id: capacity-compute
    content: Implement _compute_capacity_by_week_asset() in lookahead_engine.py — per-asset max_hours_per_day with type fallback, maintenance window exclusion
    status: pending
  - id: dashboard-fn
    content: Implement compute_capacity_dashboard() — merge demand from lookahead_rows + capacity from asset pool, utilization, status, 8-week pagination
    status: pending
  - id: schemas
    content: Create Pydantic response models in app/schemas/capacity_dashboard.py
    status: pending
  - id: api-endpoint
    content: Add GET /lookahead/{project_id}/capacity-dashboard endpoint with page query param (8 weeks per page)
    status: pending
  - id: edge-cases
    content: "Handle edge cases: none exclusion, other type flagging, zero capacity, partial weeks, maintenance windows, empty uploads"
    status: pending
isProject: false
---

# Capacity Planning Dashboard

## What Exists Today

The lookahead engine ([app/services/lookahead_engine.py](app/services/lookahead_engine.py)) already computes **demand** (from work profiles / activity distributions) and **booked** hours per `(week_start, asset_type)`, storing results in `lookahead_rows` and `lookahead_snapshots.data`. The current API at `GET /lookahead/{project_id}` returns demand vs booked.

**What is missing:** there is no concept of **capacity** — the total hours a project's asset pool can physically supply per week per asset type. Without this, the PM cannot answer "is my schedule feasible given the equipment I have?"

---

## Core Concept (REVISED)

Each individual asset has its own `max_hours_per_day`. Capacity is the **sum** of each available asset's max hours, not a flat count multiplied by a type-level constant.

```
capacity_hours = SUM(asset.max_hours_per_day for each available asset of that type) × work_days_in_week
utilization    = demand_hours / capacity_hours
status         = under_utilised | balanced | over_planned | no_capacity | idle
```

If an asset does not have `max_hours_per_day` set, fall back to `asset_types.max_hours_per_day` for its canonical type.

---

## Data Sources

- **Demand per (week, asset_type):** `lookahead_rows` table — already computed per snapshot
- **Asset pool:** `assets` table — `canonical_type`, `status`, `maintenance_start_date/end_date`, filtered by `project_id`
- **Per-asset max hours:** `assets.max_hours_per_day` (NEW column) with fallback to `asset_types.max_hours_per_day`
- **Work days per week:** `programme_uploads.work_days_per_week` (default 5, ARC Bowden = 6)
- **PMP date range:** `programme_activities.start_date` / `end_date` (min/max across upload)

---

## Identified Gaps (10 total)

### Gap 1: No per-asset max capacity

`max_hours_per_day` exists only on the `asset_types` table (type-level default). Individual assets (e.g. a smaller crane vs a tower crane) may have different capacities.

**Fix:** Add `max_hours_per_day NUMERIC(4,1) nullable` to the `assets` table. Optional — falls back to `asset_types.max_hours_per_day` when null.

**Affected files:**

- [app/models/asset.py](app/models/asset.py) — add column
- [app/schemas/asset.py](app/schemas/asset.py) — add to `AssetCreate`, `AssetUpdate`, `AssetResponse`, `AssetBriefResponse`
- [app/crud/asset.py](app/crud/asset.py) — pass through on create/update
- New Alembic migration

### Gap 2: No recomputation on asset changes

When a new asset is added or an existing one changes type, the dashboard (and potentially classifications) must update.

**Fix:** Two recomputation triggers in the asset create/update API ([app/api/v1/assets.py](app/api/v1/assets.py)):

- **New asset with a NEW canonical_type** (not previously present in this project's asset pool): trigger **full programme re-process** — re-classify all activities so AI can now assign work to the new type. Uses existing `process_programme()` infrastructure.
- **New asset with an EXISTING canonical_type** (or capacity/maintenance changes): trigger **lookahead refresh only** — demand stays the same, but capacity changes. Uses existing `refresh_lookahead_after_project_change()`.
- **New PMP uploaded**: already triggers full recompute via existing upload pipeline.

Detection logic: before creating the asset, query `SELECT DISTINCT canonical_type FROM assets WHERE project_id = X AND canonical_type IS NOT NULL` and check if the new type is already in that set.

### Gap 3: No capacity computation exists anywhere

The system computes demand and booked but never asks "how many hours can the asset pool supply?"

**Fix:** New function `_compute_capacity_by_week_asset()` in `lookahead_engine.py`.

### Gap 4: Asset maintenance windows not used in capacity

`Asset` has `maintenance_start_date` and `maintenance_end_date`, but the lookahead engine ignores them. An asset in maintenance should NOT contribute capacity during those weeks.

**Fix:** When computing per-week capacity, check each asset's maintenance window. Asset is available for week W if:

- `status` is not RETIRED/DECOMMISSIONED
- No maintenance overlap: `maintenance_start_date is null OR maintenance_end_date < week_start OR maintenance_start_date > week_end`

### Gap 5: Unresolved assets create a blind spot

Assets with `canonical_type = NULL` or `type_resolution_status` not in `ASSET_TYPE_RESOLUTION_READY` cannot contribute capacity but PM needs to know they exist.

**Fix:** Include `diagnostics.unresolved_asset_count` in the response. The existing `planning_ready` property handles this.

### Gap 6: `other` type demand with no `other` assets

Activities classified as `other` create demand, but projects rarely have `other`-typed assets.

**Fix:**

- Show `other` rows only when demand > 0
- Mark with status `review_needed`
- Include `diagnostics.other_demand_hours_total`

### Gap 7: No utilization threshold configuration

**Fix:** Start with constants in `constants.py` for v1:

```python
CAPACITY_UTIL_BALANCED_MIN = 0.70
CAPACITY_UTIL_OVER_PLANNED_MIN = 0.90
CAPACITY_DASHBOARD_PAGE_SIZE = 8   # weeks per page
```

### Gap 8: Dashboard needs full PMP timeline, not just demand weeks

**Fix:** Derive week range from `programme_activities` min(start_date) to max(end_date), paginated in 8-week chunks.

### Gap 9: No per-week cross-asset summary

**Fix:** Include `summary_by_week` with aggregated capacity, demand, worst-case status.

### Gap 10: `none` type must be excluded

**Fix:** Filter `asset_type = 'none'` from both demand and capacity in the dashboard.

---

## Implementation Plan

### Phase A: Asset Backend Changes

#### A1. DB Migration

New Alembic migration:

```python
op.add_column('assets', sa.Column('max_hours_per_day', sa.Numeric(4, 1), nullable=True))
```

#### A2. Asset Model

In [app/models/asset.py](app/models/asset.py), add:

```python
max_hours_per_day = Column(NUMERIC(4, 1), nullable=True)
```

#### A3. Asset Schemas

In [app/schemas/asset.py](app/schemas/asset.py):

- `AssetBase`: add `max_hours_per_day: Optional[Decimal] = Field(None, gt=0, le=24)`
- `AssetResponse`: add `max_hours_per_day: Optional[Decimal] = None`
- `AssetBriefResponse`: add `max_hours_per_day: Optional[Decimal] = None`

Since `AssetCreate` and `AssetUpdate` inherit from or share fields with `AssetBase`, they get it automatically.

#### A4. Effective Max Hours Helper

In [app/crud/asset.py](app/crud/asset.py) or a utility module, add:

```python
def effective_max_hours_per_day(asset: Asset, type_max_hours: dict[str, float]) -> float:
    """Return the asset's own max_hours_per_day, or fall back to the type-level default."""
    if asset.max_hours_per_day is not None:
        return float(asset.max_hours_per_day)
    return type_max_hours.get(asset.canonical_type, 8.0)
```

### Phase B: Recomputation Triggers

#### B1. New-Asset-Type Detection

In `create_asset` endpoint ([app/api/v1/assets.py](app/api/v1/assets.py)), after `asset_crud.create_asset()`:

```python
if db_asset.canonical_type:
    existing_types = {
        row[0] for row in
        db.query(Asset.canonical_type)
        .filter(Asset.project_id == db_asset.project_id, Asset.canonical_type.isnot(None), Asset.id != db_asset.id)
        .distinct()
        .all()
    }
    if db_asset.canonical_type not in existing_types:
        # NEW type — full re-process: re-classify activities so AI can use this type
        _trigger_full_reprocess(db, db_asset.project_id)
    else:
        # EXISTING type — capacity changed, refresh lookahead only
        refresh_lookahead_after_project_change(db_asset.project_id)
```

`_trigger_full_reprocess` enqueues a re-process of the latest committed upload via the existing `ProgrammeUploadJob` infrastructure (or calls `process_programme` for the active upload).

#### B2. Update Asset Triggers

Already partially handled in `update_asset` — it calls `refresh_lookahead_after_project_change` when `canonical_type` or `status` changes. Extend: if `canonical_type` changed to a type not previously in the project, trigger full re-process instead.

#### B3. New PMP Upload

Already handled — `process_programme()` runs full pipeline including `calculate_lookahead_for_project()`.

### Phase C: Dashboard Service

#### C1. Constants

In [app/core/constants.py](app/core/constants.py):

```python
CAPACITY_UTIL_BALANCED_MIN: float = 0.70
CAPACITY_UTIL_OVER_PLANNED_MIN: float = 0.90
CAPACITY_DASHBOARD_PAGE_SIZE: int = 8
```

#### C2. Capacity Computation

In [app/services/lookahead_engine.py](app/services/lookahead_engine.py):

```python
def _compute_capacity_by_week_asset(
    db: Session,
    project_id: uuid.UUID,
    week_starts: list[date],
    work_days_per_week: int,
) -> dict[tuple[date, str], dict]:
```

Logic:

1. Query planning-ready assets for the project: `WHERE project_id = X AND canonical_type IS NOT NULL AND planning_ready`
2. Load type-level `max_hours_per_day` via existing `_load_max_hours_by_type()`
3. For each week, for each asset:

- Check maintenance window overlap
- If available: add `effective_max_hours_per_day(asset, type_max_hours)` to that week+type bucket

1. Multiply summed daily hours by `min(work_days_per_week, actual_work_days_in_week)` for partial weeks
2. Return `{(week_start, asset_type): {"capacity_hours": X, "available_assets": N}}`

#### C3. Dashboard Function

```python
def compute_capacity_dashboard(
    project_id: uuid.UUID,
    db: Session,
    page: int = 1,
    page_size: int = CAPACITY_DASHBOARD_PAGE_SIZE,
) -> dict | None:
```

Steps:

1. Load project, active upload, `work_days_per_week`
2. Ensure fresh snapshot exists (reuse `_get_fresh_snapshot` pattern)
3. Load `lookahead_rows` for latest snapshot (demand per week+type), exclude `none`
4. Derive full week list from `programme_activities` min(start_date) to max(end_date)
5. Apply pagination: `weeks[start:start+page_size]` where `start = (page-1) * page_size`
6. Compute capacity for paginated weeks via `_compute_capacity_by_week_asset()`
7. Merge into grid: for each (week, asset_type), build cell with capacity, demand, utilization, status
8. Build `summary_by_asset_type` (across ALL weeks, not just page) and `summary_by_week` (page only)
9. Return structured response

Status derivation:

```python
if capacity == 0 and demand > 0:
    status = "no_capacity"
elif capacity == 0 and demand == 0:
    status = "idle"
elif utilization < CAPACITY_UTIL_BALANCED_MIN:
    status = "under_utilised"
elif utilization < CAPACITY_UTIL_OVER_PLANNED_MIN:
    status = "balanced"
else:
    status = "over_planned"
```

### Phase D: API and Schemas

#### D1. Response Schemas

New file [app/schemas/capacity_dashboard.py](app/schemas/capacity_dashboard.py):

```python
class CapacityCell(BaseSchema):
    capacity_hours: float
    demand_hours: float
    utilization_pct: float      # 0-100+
    status: str                 # under_utilised | balanced | over_planned | no_capacity | idle | review_needed
    available_assets: int
    is_anomalous: bool = False

class AssetTypeSummary(BaseSchema):
    total_capacity_hours: float
    total_demand_hours: float
    avg_utilization_pct: float
    peak_week: Optional[date] = None
    peak_utilization_pct: float = 0.0
    weeks_over_planned: int = 0
    weeks_balanced: int = 0
    weeks_under_utilised: int = 0
    weeks_idle: int = 0

class WeekSummary(BaseSchema):
    total_capacity_hours: float
    total_demand_hours: float
    overall_utilization_pct: float
    worst_status: str
    asset_type_count: int

class CapacityThresholds(BaseSchema):
    balanced_min_pct: float
    over_planned_min_pct: float

class CapacityDashboardDiagnostics(BaseSchema):
    unresolved_asset_count: int
    other_demand_hours_total: float
    total_weeks: int
    total_asset_types: int
    snapshot_date: date

class PaginationMeta(BaseSchema):
    page: int
    page_size: int
    total_weeks: int
    total_pages: int
    has_next: bool
    has_previous: bool

class CapacityDashboardResponse(BaseSchema):
    project_id: UUID
    upload_id: UUID
    work_days_per_week: int
    thresholds: CapacityThresholds
    pagination: PaginationMeta
    weeks: list[date]                                        # paginated weeks
    asset_types: list[str]
    grid: dict[str, dict[str, CapacityCell]]                 # asset_type -> week_iso -> cell
    summary_by_asset_type: dict[str, AssetTypeSummary]       # across ALL weeks
    summary_by_week: dict[str, WeekSummary]                  # paginated weeks only
    diagnostics: CapacityDashboardDiagnostics
```

#### D2. API Endpoint

In [app/api/v1/lookahead.py](app/api/v1/lookahead.py):

```
GET /lookahead/{project_id}/capacity-dashboard?page=1
```

Roles: MANAGER, ADMIN. Query params: `page` (default 1, 8 weeks per page).

---

## Architecture Decisions

### Compute capacity on-the-fly (do NOT store)

- Asset pool changes (new asset, maintenance) should immediately reflect without re-running the full lookahead pipeline
- The computation is lightweight: one SQL query for assets + simple arithmetic
- `lookahead_rows` stores PMP demand truth; capacity is a property of the asset pool
- No migration needed for the lookahead tables

### Per-asset capacity with type fallback

- `asset.max_hours_per_day` takes priority (different cranes have different capacities)
- Falls back to `asset_types.max_hours_per_day` when not set on the individual asset
- This means capacity formula is: `SUM(effective_max_hours(asset)) × work_days` not `COUNT(assets) × type_max_hours × work_days`

### Full re-process on new asset type

When a new canonical type appears in the project's asset pool, AI classification must re-run because activities previously classified as `other` (or with borderline confidence) might now correctly classify to the new type. This uses the existing `process_programme` pipeline.

---

## Files to Change

- **New Alembic migration** — `assets.max_hours_per_day` column
- **[app/models/asset.py](app/models/asset.py)** — add `max_hours_per_day` column
- **[app/schemas/asset.py](app/schemas/asset.py)** — add to `AssetBase`, `AssetResponse`, `AssetBriefResponse`
- **[app/crud/asset.py](app/crud/asset.py)** — add `effective_max_hours_per_day()` helper
- **[app/api/v1/assets.py](app/api/v1/assets.py)** — add recomputation triggers on create/update
- **[app/core/constants.py](app/core/constants.py)** — add capacity utilization constants + page size
- **[app/services/lookahead_engine.py](app/services/lookahead_engine.py)** — add `_compute_capacity_by_week_asset()`, `compute_capacity_dashboard()`
- **New: [app/schemas/capacity_dashboard.py](app/schemas/capacity_dashboard.py)** — Pydantic response models
- **[app/api/v1/lookahead.py](app/api/v1/lookahead.py)** — add `GET /{project_id}/capacity-dashboard` endpoint

---

## fRecomputation Trigger Summary

```
Asset created (NEW type for project)     → full programme re-process (re-classify all activities)
Asset created (EXISTING type)            → refresh_lookahead_after_project_change()
Asset updated (type/status/capacity)     → refresh_lookahead_after_project_change()
Asset updated (type changed to NEW type) → full programme re-process
New PMP uploaded                         → already handled by process_programme pipeline
```

---

## Edge Cases

- **Zero assets for a type but demand > 0** — status = `no_capacity`
- **Assets exist but zero demand** — status = `idle`
- **Partial first/last weeks** — if PMP starts Wednesday, that week has fewer work days; capacity reflects actual available days
- **Overlapping maintenance** — multiple assets of same type, some in maintenance; count only available ones per week
- `**other` type — show with `review_needed` flag when demand > 0
- `**none` type — excluded entirely from the dashboard
- **No active upload** — return 404 with message
- **Asset with no canonical_type** — cannot contribute capacity; counted in `diagnostics.unresolved_asset_count`
- **Per-asset max_hours_per_day not set** — fall back to `asset_types.max_hours_per_day`
- **Pagination boundary** — `summary_by_asset_type` spans ALL weeks (full picture); `grid` and `summary_by_week` are paginated
