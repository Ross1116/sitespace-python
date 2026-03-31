# Sitespace API

Construction site management backend built with FastAPI. Handles asset management, slot booking, project coordination, subcontractor management, programme ingestion, deterministic/AI-assisted programme intelligence, planning-readiness review, lookahead forecasting, and activity-backed booking workflows.

Last updated: 2026-03-27

Architecture reference: [AI_ARCHITECTURE_PLAN.md](AI_ARCHITECTURE_PLAN.md) is the deep-dive architecture source. Section `1A. Historical Status Addendum` is the best current-state overlay for what is implemented today versus what remains planned.

## Quick Start

### Prerequisites

- Python 3.11+
- PostgreSQL 15+

### Setup

```bash
# Install dependencies
pip install -r requirements.txt

# Configure environment
cp env.example .env
# Edit .env with your database URL and secrets

# Run database migrations
alembic upgrade head

# Start the server
uvicorn app.main:app --host 0.0.0.0 --port 8080 --reload
```

### Docker

```bash
docker-compose up -d
```

This starts the FastAPI app on port `8080` and PostgreSQL on port `5432`. The startup script (`start.sh`) waits for the database, runs migrations, then launches Uvicorn.

### API Docs

- Swagger UI: http://localhost:8080/docs
- ReDoc: http://localhost:8080/redoc
- Health check: http://localhost:8080/health

---

## Project Structure

```text
app/
|-- api/v1/                 Route handlers
|   |-- auth.py             Authentication & authorization
|   |-- assets.py           Asset CRUD, availability, planning-readiness impacts
|   |-- slot_booking.py     Booking lifecycle, bulk booking, provenance, conflicts
|   |-- site_project.py     Project management, team assignment, planning completeness
|   |-- subcontractor.py    Subcontractor management, projects, bookings, readiness
|   |-- programmes.py       Programme upload, status, activities, booking context, mappings
|   |-- lookahead.py        Snapshots, alerts, history, activity drilldown
|   |-- files.py            Stored file upload/preview/image/delete
|   |-- site_plans.py       Site plan CRUD linked to stored files
|   |-- users.py            User profile management
|   |-- file_upload.py      Legacy upload endpoint (defunct feature)
|   `-- booking_audit.py    Immutable audit trail
|-- core/
|   |-- config.py           Settings & environment variables
|   |-- database.py         SQLAlchemy engine & session
|   |-- security.py         JWT, password hashing, rate limiting
|   `-- email.py            Mailtrap email integration
|-- crud/                   Database access layer
|-- models/                 SQLAlchemy ORM models
|-- schemas/                Pydantic request/response schemas
|-- services/               Programme intelligence, classification, lookahead, work profiles
`-- utils/                  Shared helpers, normalization, completeness-note utilities
alembic/                    Database migrations
tests/                      Test suite
```

---

## API Endpoints

### Authentication (`/api/auth`)

| Method | Path                   | Description                                   |
| ------ | ---------------------- | --------------------------------------------- |
| POST   | `/login`               | Authenticate user or subcontractor (10/min)   |
| POST   | `/register`            | Create new user account (5/min)               |
| POST   | `/refresh`             | Refresh access token (10/min, token rotation) |
| GET    | `/me`                  | Get current user info                         |
| POST   | `/change-password`     | Change password (authenticated, 10/min)       |
| POST   | `/forgot-password`     | Request password reset email (3/min)          |
| POST   | `/reset-password`      | Reset password with token (5/min)             |
| POST   | `/verify-email`        | Verify email address (10/min)                 |
| POST   | `/resend-verification` | Resend verification email (5/min)             |
| POST   | `/logout`              | Logout (20/min, revokes current access token) |

### Assets (`/api/assets`)

| Method | Path                   | Description |
| ------ | ---------------------- | ----------- |
| POST   | `/`                    | Create new asset |
| GET    | `/`                    | List assets (filterable by project, status, type, resolution, planning readiness) |
| GET    | `/brief`               | Lightweight asset list for dropdowns |
| GET    | `/{asset_id}`          | Get asset details |
| GET    | `/code/{asset_code}`   | Get asset by code |
| PUT    | `/{asset_id}`          | Update asset, canonical type, and readiness-affecting fields |
| POST   | `/{asset_id}/status-impact` | Preview booking impact of a status change |
| POST   | `/{asset_id}/transfer` | Transfer asset to another project |
| POST   | `/check-availability`  | Check asset availability for time slot |
| DELETE | `/{asset_id}`          | Delete asset |

Notes:

- Asset responses now include planning-readiness metadata such as canonical type, type resolution status, inference source/confidence, and `planning_ready`.
- Asset updates and transfers trigger lookahead refresh when readiness-affecting fields change.

### Bookings (`/api/bookings`)

| Method | Path                      | Description |
| ------ | ------------------------- | ----------- |
| POST   | `/`                       | Create booking (auto-CONFIRMED for managers, PENDING for subcontractors) |
| POST   | `/bulk`                   | Bulk create bookings across assets/dates |
| GET    | `/`                       | List bookings with role-based filtering |
| GET    | `/calendar`               | Calendar view (max 90-day range) |
| GET    | `/statistics`             | Booking analytics |
| GET    | `/my/upcoming`            | Current user's upcoming bookings |
| GET    | `/{booking_id}`           | Booking details |
| PUT    | `/{booking_id}`           | Update booking with conflict checking |
| PATCH  | `/{booking_id}/status`    | Update status with optional comment |
| DELETE | `/{booking_id}`           | Delete booking |
| POST   | `/check-conflicts`        | Check for scheduling conflicts |
| POST   | `/{booking_id}/duplicate` | Duplicate booking to a new date |

Notes:

- Booking create and bulk create support activity-linked booking via `programme_activity_id` and `selected_week_start`.
- Booking responses now include provenance fields such as `source`, `booking_group_id`, `programme_activity_id`, `programme_activity_name`, `expected_asset_type`, and `is_modified`.
- Assets used for bookings must be planning-ready.

### Projects (`/api/projects`)

| Method | Path                                     | Description |
| ------ | ---------------------------------------- | ----------- |
| POST   | `/`                                      | Create project |
| GET    | `/`                                      | List projects (filterable by status, name, date range) |
| GET    | `/{project_id}`                          | Project details |
| GET    | `/{project_id}/planning-completeness`    | Planning-readiness score, blocking counts, and action tasks |
| PATCH  | `/{project_id}`                          | Update project |
| DELETE | `/{project_id}`                          | Delete project (lead manager or admin) |
| POST   | `/{project_id}/managers`                 | Add manager |
| DELETE | `/{project_id}/managers/{manager_id}`    | Remove manager |
| POST   | `/{project_id}/subcontractors`           | Add subcontractor |
| PATCH  | `/{project_id}/subcontractors/{subcontractor_id}`  | Update subcontractor assignment |
| DELETE | `/{project_id}/subcontractors/{subcontractor_id}`  | Remove subcontractor |
| GET    | `/{project_id}/available-subcontractors` | List unassigned subcontractors |
| GET    | `/{project_id}/statistics`               | Project statistics |

### Programmes (`/api/programmes`)

| Method | Path                                                | Description |
| ------ | --------------------------------------------------- | ----------- |
| POST   | `/upload?project_id={project_id}`                   | Upload CSV/XLSX/XLSM programme file; returns 202 and processes in background |
| GET    | `/{upload_id}/status`                               | Poll processing status with structured diagnostics |
| GET    | `/{project_id}`                                     | List programme versions for project |
| GET    | `/{upload_id}/activities`                           | List imported activities |
| GET    | `/{upload_id}/activities?subcontractor_id={subcontractor_id}` | Subcontractor-scoped activities for assigned subcontractor |
| GET    | `/activities/{activity_id}/booking-context?selected_week_start=YYYY-MM-DD` | Activity-linked booking context for selected week |
| GET    | `/{upload_id}/diff`                                 | Compare against latest earlier planning-successful version |
| GET    | `/{upload_id}/mappings`                             | List activity asset mappings |
| GET    | `/{upload_id}/mappings/unclassified`                | Low-confidence unresolved mappings |
| PATCH  | `/mappings/{mapping_id}`                            | Apply upload-local correction to mapping |
| DELETE | `/{upload_id}`                                      | Delete uploaded programme and cascaded derived data |

Notes:

- Upload processing can degrade safely when AI is unavailable; deterministic fallback continues processing rather than hard-failing the entire upload.
- Stale `processing` uploads older than the configured recovery threshold are failed on startup and before new uploads are accepted, preventing projects from remaining blocked forever after a crash/redeploy.
- `GET /{upload_id}/status` returns stable diagnostics in `completeness_notes`, including AI suppression, unclassified mapping count, excluded booking count, and non-planning-ready asset count.
- Upload metadata write failures clean up orphaned stored blobs.
- Activity parent/child links are preserved during import, including cached-header paths.
- Mapping responses include `item_id`, which is used by admin review flows to promote upload-local fixes into durable item memory.
- Booking context returns booking-ready selected-week suggestions rather than requiring the frontend to reconstruct hour distribution on the client.

### Lookahead (`/api/lookahead`)

| Method | Path                         | Description |
| ------ | ---------------------------- | ----------- |
| GET    | `/{project_id}`              | Latest lookahead snapshot rows (demand/booked/gap) |
| GET    | `/{project_id}/alerts`       | Latest anomaly flags |
| GET    | `/{project_id}/history`      | Snapshot history |
| GET    | `/{project_id}/sub/{sub_id}` | Subcontractor-facing lookahead plus related notifications |
| GET    | `/{project_id}/sub-asset-suggestions` | Asset-type suggestions for subcontractor routing |
| GET    | `/{project_id}/activities?week_start=YYYY-MM-DD&asset_type=...` | Weekly activity drilldown for one asset-type/week cell |

Notes:

- Demand is split by week boundaries across activity spans.
- Overnight bookings are split across day/week boundaries in project timezone.
- Nightly recalculation uses APScheduler with SQLAlchemy jobstore and reuses the app DB engine.
- Duplicate same-day snapshots are prevented by update-on-existing (`project_id` + `snapshot_date`).
- Lookahead rows are persisted in `lookahead_rows` and are also embedded into snapshot JSON history.
- Lookahead now tracks excluded bookings and non-planning-ready asset counts so forecast quality issues are visible.

### Subcontractors (`/api/subcontractors`)

| Method | Path                                 | Description |
| ------ | ------------------------------------ | ----------- |
| POST   | `/`                                  | Create subcontractor |
| GET    | `/`                                  | List all with pagination and filters |
| GET    | `/my-subcontractors`                 | Subcontractors in current manager's projects |
| GET    | `/manager-stats`                     | Manager's subcontractor statistics |
| GET    | `/search`                            | Search by name, company, email, trade |
| GET    | `/available`                         | Available subcontractors for a date/time |
| GET    | `/by-trade/{trade}`                  | Filter by trade specialty |
| PUT    | `/me`                                | Update own profile (subcontractor auth) |
| GET    | `/{subcontractor_id}`                          | Subcontractor details |
| PUT    | `/{subcontractor_id}`                          | Update subcontractor (manager/admin) |
| PUT    | `/{subcontractor_id}/password`                 | Update password |
| DELETE | `/{subcontractor_id}`                          | Deactivate subcontractor |
| POST   | `/{subcontractor_id}/activate`                 | Reactivate subcontractor |
| DELETE | `/{subcontractor_id}/permanent`                | Permanent delete (admin only) |
| POST   | `/{subcontractor_id}/send-welcome-email`       | Send invite/welcome email |
| GET    | `/{subcontractor_id}/projects`                 | Assigned projects |
| GET    | `/{subcontractor_id}/projects/current`         | Active projects only |
| GET    | `/{subcontractor_id}/bookings`                 | Booking history |
| GET    | `/{subcontractor_id}/bookings/upcoming`        | Upcoming bookings |
| GET    | `/{subcontractor_id}/bookings/count-by-status` | Booking counts by status |
| GET    | `/{subcontractor_id}/availability`             | Check availability |
| POST   | `/{subcontractor_id}/projects/{project_id}`    | Assign to project |
| DELETE | `/{subcontractor_id}/projects/{project_id}`    | Remove from project |

Notes:

- Subcontractor responses now include trade-resolution metadata and `planning_ready`.
- List/search endpoints support trade-resolution and planning-readiness-driven workflows.

### Booking Audit (`/api/bookings`)

| Method | Path                          | Description |
| ------ | ----------------------------- | ----------- |
| GET    | `/{booking_id}/audit`         | Full audit trail for a booking |
| GET    | `/audit/my-activity`          | Current user's audit activity |
| GET    | `/audit/project/{project_id}` | Project audit logs (manager/admin) |

### File Upload (`/api/uploadfile`)

| Method | Path | Description |
| ------ | ---- | ----------- |
| POST   | `/`  | Upload file (max 10 MB, restricted types) |

Note: File upload is currently defunct/disabled by product decision and is deferred until explicitly re-enabled.

Allowed types: `.jpg`, `.jpeg`, `.png`, `.gif`, `.webp`, `.pdf`, `.doc`, `.docx`, `.xls`, `.xlsx`, `.csv`, `.txt`, `.json`

### Files (`/api/files`)

| Method | Path                 | Description |
| ------ | -------------------- | ----------- |
| POST   | `/upload`            | Upload file for two-phase site-plan flow (20 MB max) |
| GET    | `/{file_id}`         | Serve raw file |
| GET    | `/{file_id}/preview` | Render preview image (PDF page 1 or image passthrough) |
| GET    | `/{file_id}/image`   | High-scale image render for detail views |
| DELETE | `/{file_id}`         | Delete file (409 if referenced by a site plan) |

### Site Plans (`/api/site-plans`)

| Method | Path         | Description |
| ------ | ------------ | ----------- |
| POST   | `/`          | Create site plan from uploaded file |
| GET    | `/`          | List site plans (optional `project_id` filter) |
| GET    | `/{plan_id}` | Get site plan details |
| PATCH  | `/{plan_id}` | Update title and/or replace linked file |
| DELETE | `/{plan_id}` | Delete site plan (and orphaned linked file) |

### Users (`/api/users`)

| Method | Path         | Description |
| ------ | ------------ | ----------- |
| GET    | `/me`        | Get current user profile |
| PUT    | `/me`        | Update own profile |
| GET    | `/`          | List all users (admin only) |
| PUT    | `/{user_id}` | Update any user (admin only) |

---

## Role-Based Access

### UserRole Enum

```text
UserRole:
  - admin          # Full access
  - manager        # Project management
  - subcontractor  # Limited booking/asset access
  - tv             # Display-only, read-only, project-scoped (case-insensitive)
```

### Permissions Table

| Role          | JWT Value       | Write Access   | Project Assignment | Notes |
| ------------- | --------------- | -------------- | ------------------ | ----- |
| admin         | "admin"         | Yes            | Any                | Full access |
| manager       | "manager"       | Yes            | By assignment      | Project CRUD, booking/asset management |
| subcontractor | "subcontractor" | Limited (self) | By assignment      | Bookings for assigned projects |
| tv            | "tv"            | No (read-only) | By assignment      | Only GET, only for assigned projects |

---

---

## Authentication

JWT-based with dual entity support (Users and Subcontractors).

- Logout revokes the current access token via blacklist.
- Refresh token flow uses rotation (the used refresh token is revoked).

---

## TV Role (Display-Only)

The `tv` role is intended for wall displays / read-only project viewing.

- Role input is case-insensitive (`tv`, `TV`, `Tv`, etc.).
- `/api/auth/me` returns `role: "tv"` for these users.
- JWT access tokens include a `role` claim, normalized to lowercase.

### Rules (RBAC)

- TV users can only read bookings, calendar, assets, projects, and lookahead for projects they are assigned to.
- TV users are blocked from all write operations (`POST`, `PUT`, `PATCH`, `DELETE`) with `403 Forbidden`.

### Create a TV user

Use the standard registration endpoint (example):

```bash
curl -sS -X POST "http://localhost:8080/api/auth/register" \
  -H "Content-Type: application/json" \
  -d '{
    "email": "tv1@example.com",
    "first_name": "TV",
    "last_name": "Display",
    "phone": null,
    "password": "ChangeMe123!",
    "confirm_password": "ChangeMe123!",
    "role": "TV"
  }'
```

### Assign TV user to a project

TV visibility is scoped by project assignment. Assign the TV user to a project using the existing project membership endpoint (requires manager/admin auth):

```bash
curl -sS -X POST "http://localhost:8080/api/projects/{project_id}/managers" \
  -H "Authorization: Bearer $MANAGER_OR_ADMIN_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "manager_id": "{tv_user_id}",
    "is_lead_manager": false
  }'
```

Note: the path uses `/managers` for historical reasons, but TV users are treated as project members (not project managers).

### Endpoints used by the frontend in TV mode (GET-only)

- `GET /api/projects/?my_projects=true&limit=...&skip=...`
- `GET /api/assets/?project_id=...&skip=...&limit=...`
- `GET /api/bookings/?project_id=...&limit=...&skip=...`
- `GET /api/bookings/calendar?date_from=YYYY-MM-DD&date_to=YYYY-MM-DD&project_id=...`
- `GET /api/bookings/{bookingId}`
- `GET /api/bookings/{bookingId}/audit`
- `GET /api/lookahead/{project_id}`
- `GET /api/lookahead/{project_id}/alerts`

All of the above enforce project membership for TV users; guessing IDs from other projects will return `403`.

| Token              | Expiry   | Purpose |
| ------------------ | -------- | ------- |
| Access token       | 30 min   | API access |
| Refresh token      | 7 days   | Get new access tokens |
| Email verification | 24 hours | Confirm email |
| Password reset     | 1 hour   | Reset password |

Algorithm: HS512

### Role-Based Access

| Role              | Access |
| ----------------- | ------ |
| **Admin**         | Full access to all resources |
| **Manager**       | Project-scoped access, approve subcontractor bookings |
| **Subcontractor** | Own bookings, assigned projects |

### Rate Limits

| Endpoint                    | Limit |
| --------------------------- | ----- |
| `/auth/login`               | 10 requests/min |
| `/auth/register`            | 5 requests/min |
| `/auth/refresh`             | 10 requests/min |
| `/auth/forgot-password`     | 3 requests/min |
| `/auth/reset-password`      | 5 requests/min |
| `/auth/change-password`     | 10 requests/min |
| `/auth/verify-email`        | 10 requests/min |
| `/auth/resend-verification` | 5 requests/min |
| `/auth/logout`              | 20 requests/min |

---

## Database

PostgreSQL with SQLAlchemy ORM. All primary keys are UUIDs.

### Models

| Table | Key Relationships / Purpose |
| ----- | --------------------------- |
| `users` | M2M with projects (via `manager_site_project`) |
| `subcontractors` | M2M with projects (via `subcontractor_site_project`) |
| `site_projects` | Has many assets, bookings, managers, subcontractors |
| `assets` | Belongs to project; includes canonical type and planning-readiness fields |
| `slot_bookings` | Belongs to project, manager, subcontractor, asset, optional booking group |
| `activity_booking_groups` | Groups activity-linked bookings under one programme activity |
| `programme_uploads` | Uploaded programme versions and processing diagnostics |
| `programme_activities` | Parsed activity rows and hierarchy |
| `activity_asset_mappings` | Activity-to-asset-type classification and review state |
| `items` / `item_aliases` | Durable activity identity memory |
| `item_classifications` | Stable asset-type memory per item |
| `activity_work_profiles` | Work profile hours/distribution intelligence |
| `lookahead_snapshots` | Snapshot history |
| `lookahead_rows` | Persisted operational demand/booked/gap rows |
| `notifications` | Lookahead-driven notifications and acted status |
| `project_alert_policies` | Per-project alert control settings |
| `subcontractor_asset_type_assignments` | Routed subcontractor demand coverage assignments |
| `booking_audit_logs` | Immutable booking audit trail |

### Enums

| Enum | Values |
| ---- | ------ |
| `BookingStatus` | `PENDING`, `CONFIRMED`, `IN_PROGRESS`, `COMPLETED`, `CANCELLED`, `DENIED` |
| `AssetStatus` | `available`, `maintenance`, `retired` |
| `ProjectStatus` | `active`, `pending`, `completed`, `cancelled`, `on_hold` |
| `UserRole` | `manager`, `admin`, `subcontractor`, `tv` |
| `TradeSpecialty` | `electrician`, `plumber`, `carpenter`, `mason`, `painter`, `hvac`, `roofer`, `landscaper`, `general`, `other` |
| `AssetTypeResolutionStatus` | `unknown`, `inferred`, `confirmed`, `manual` |
| `TradeResolutionStatus` | `unknown`, `inferred`, `confirmed`, `manual` |

### Connection Pool

```text
pool_size: 20, max_overflow: 40, pool_recycle: 1800s, pool_pre_ping: true
```

### Migrations

```bash
alembic upgrade head                              # Apply all migrations
alembic revision --autogenerate -m "description" # Generate migration
alembic downgrade -1                              # Revert last migration
alembic history                                   # Show migration history
```

---

## Environment Variables

```bash
# Database
DATABASE_URL=postgresql://user:password@localhost:5432/sitespace
DB_CONNECT_TIMEOUT=10

# JWT (required - no defaults)
JWT_SECRET=your-secret-key
JWT_ALGORITHM=HS512
JWT_EXPIRATION_MS=86400000
ACCESS_TOKEN_EXPIRE_MINUTES=30
REFRESH_TOKEN_EXPIRE_DAYS=7
EMAIL_VERIFICATION_EXPIRE_HOURS=24
PASSWORD_RESET_EXPIRE_HOURS=1

# App (required - no defaults)
SECRET_KEY=your-app-secret
EXPORT_FILES_ABSOLUTE_PATH=/app/uploads/
EXPORT_FILES_SERVER_PATH=getFile
APP_NAME=Sitespace

# Server
HOST=127.0.0.1
PORT=8080
DEBUG=False

# CORS
CORS_ORIGINS=http://localhost:3000,https://your-frontend.com

# Sentry
SENTRY_DSN=

# Email (Mailtrap)
MAILTRAP_USE_SANDBOX=True
MAILTRAP_TOKEN=your-token
MAILTRAP_INBOX_ID=your-inbox-id
FROM_EMAIL=noreply@sitespace.com
FROM_NAME=Sitespace Team

# Frontend
FRONTEND_URL=http://localhost:3000
COOKIE_DOMAIN=
IS_PRODUCTION=False

# AI provider and budget controls
AI_ENABLED=true
AI_PROVIDER=anthropic
AI_API_KEY=...
AI_MODEL=claude-haiku-4-5-20251001
AI_TIMEOUT_STRUCTURE=20
AI_TIMEOUT_CLASSIFY=30
AI_TIMEOUT_WORK_PROFILE=25
AI_UPLOAD_COST_BUDGET_USD=5.0
AI_INPUT_COST_PER_MILLION_USD=
AI_OUTPUT_COST_PER_MILLION_USD=

# Nightly lookahead scheduler
NIGHTLY_LOOKAHEAD_HOUR=18
NIGHTLY_LOOKAHEAD_MINUTE=30
NIGHTLY_LOOKAHEAD_TIMEZONE=Australia/Adelaide

# Upload recovery
# Positive integer minutes; uploads stuck in `processing` longer than this
# threshold are marked failed on startup and before new uploads are accepted.
# Default: 30
PROGRAMME_PROCESSING_STALE_MINUTES=30
```

`PROGRAMME_PROCESSING_STALE_MINUTES` is read from the environment and must be a
positive integer representing minutes. It controls the stale-processing
recovery threshold used by the upload safeguards described above: deterministic
fallback still allows degraded completion when AI is unavailable, but uploads
that remain in `processing` past this threshold are failed and annotated in
`completeness_notes` so operators can retry cleanly.

---

## Testing

```bash
# Run all tests
python tests/run_tests.py

# Run a specific API test module
pytest tests/test_auth.py -v

# Run focused unit/service coverage
pytest tests/unit/test_lookahead_api.py tests/unit/test_programmes_api_contract.py -v

# Load testing with Locust
locust -f locustfile.py --host=http://localhost:8080
```

Test coverage includes legacy API suites under `tests/` plus newer focused unit/service suites under `tests/unit/` for parser, classification, work-profile, lookahead, readiness, and booking integration behavior.

---

## Deployment

### Docker Compose

```bash
docker-compose up -d
```

Services:

- **app** (port 8080) - FastAPI with Uvicorn
- **db** (port 5432) - PostgreSQL 15

The [start.sh](start.sh) script handles:

1. Waiting for PostgreSQL readiness (retries with backoff)
2. Running Alembic migrations
3. Validating Python imports
4. Starting Uvicorn

### Manual

```bash
pip install -r requirements.txt
alembic upgrade head
uvicorn app.main:app --host 0.0.0.0 --port 8080
```

---

## Tech Stack

| Component | Technology |
| --------- | ---------- |
| Framework | FastAPI 0.104.1 |
| ORM | SQLAlchemy 2.0.23 |
| Database | PostgreSQL 15 |
| Migrations | Alembic 1.12.1 |
| Scheduling | APScheduler 3.11.x |
| Auth | JWT (python-jose, HS512) |
| Password hashing | bcrypt + argon2 |
| Validation | Pydantic 2.5.0 |
| Rate limiting | slowapi 0.1.9 |
| Email | Mailtrap API |
| Server | Uvicorn 0.24.0 |
| Container | Docker + Docker Compose |
