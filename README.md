# Sitespace API

Construction site management backend built with FastAPI. Handles asset management, slot booking, project coordination, and subcontractor management.

## Quick Start

### Prerequisites

- Python 3.11+
- PostgreSQL 15+

### Setup

````bash
# Install dependencies
pip install -r requirements.txt

# Configure environment
cp env.example .env
# Edit .env with your database URL and secrets

# Run database migrations
alembic upgrade head

# Start the server
uvicorn app.main:app --host 0.0.0.0 --port 8080 --reload
```text

### Docker

```bash
docker-compose up -d
```text

This starts the FastAPI app on port `8080` and PostgreSQL on port `5432`. The startup script (`start.sh`) waits for the database, runs migrations, then launches Uvicorn.

### API Docs

- Swagger UI: http://localhost:8080/docs
- ReDoc: http://localhost:8080/redoc
- Health check: http://localhost:8080/health

---

## Project Structure

````

app/
api/v1/ Route handlers
auth.py Authentication & authorization
assets.py Asset CRUD & availability
slot_booking.py Booking lifecycle & scheduling
site_project.py Project management & team assignments
subcontractor.py Subcontractor management & availability
users.py User profile management
file_upload.py File upload with validation
booking_audit.py Immutable audit trail
core/
config.py Settings & environment variables
database.py SQLAlchemy engine & session
security.py JWT, password hashing, rate limiting
email.py Mailtrap email integration
crud/ Database access layer
models/ SQLAlchemy ORM models
schemas/ Pydantic request/response schemas
utils/ File upload & password utilities
alembic/ Database migrations
tests/ Test suite

```

---

## API Endpoints

### Authentication (`/api/auth`)

| Method | Path                   | Description                                 |
| ------ | ---------------------- | ------------------------------------------- |
| POST   | `/login`               | Authenticate user or subcontractor (10/min) |
| POST   | `/register`            | Create new user account                     |
| POST   | `/refresh`             | Refresh access token                        |
| GET    | `/me`                  | Get current user info                       |
| POST   | `/change-password`     | Change password (authenticated)             |
| POST   | `/forgot-password`     | Request password reset email (3/min)        |
| POST   | `/reset-password`      | Reset password with token                   |
| POST   | `/verify-email`        | Verify email address                        |
| POST   | `/resend-verification` | Resend verification email                   |
| POST   | `/logout`              | Logout                                      |

### Assets (`/api/assets`)

| Method | Path                   | Description                                       |
| ------ | ---------------------- | ------------------------------------------------- |
| POST   | `/`                    | Create new asset                                  |
| GET    | `/`                    | List assets (filterable by project, status, type) |
| GET    | `/brief`               | Lightweight asset list for dropdowns              |
| GET    | `/{asset_id}`          | Get asset details                                 |
| GET    | `/code/{asset_code}`   | Get asset by code                                 |
| PUT    | `/{asset_id}`          | Update asset                                      |
| POST   | `/{asset_id}/transfer` | Transfer asset to another project                 |
| POST   | `/check-availability`  | Check asset availability for time slot            |
| DELETE | `/{asset_id}`          | Delete asset                                      |

### Bookings (`/api/bookings`)

| Method | Path                      | Description                                                              |
| ------ | ------------------------- | ------------------------------------------------------------------------ |
| POST   | `/`                       | Create booking (auto-CONFIRMED for managers, PENDING for subcontractors) |
| POST   | `/bulk`                   | Bulk create bookings across assets/dates                                 |
| GET    | `/`                       | List bookings with role-based filtering                                  |
| GET    | `/calendar`               | Calendar view (max 90-day range)                                         |
| GET    | `/statistics`             | Booking analytics                                                        |
| GET    | `/my/upcoming`            | Current user's upcoming bookings                                         |
| GET    | `/{booking_id}`           | Booking details                                                          |
| PUT    | `/{booking_id}`           | Update booking with conflict checking                                    |
| PATCH  | `/{booking_id}/status`    | Update status with optional comment                                      |
| DELETE | `/{booking_id}`           | Delete booking                                                           |
| POST   | `/check-conflicts`        | Check for scheduling conflicts                                           |
| POST   | `/{booking_id}/duplicate` | Duplicate booking to a new date                                          |

### Projects (`/api/projects`)

| Method | Path                                     | Description                                            |
| ------ | ---------------------------------------- | ------------------------------------------------------ |
| POST   | `/`                                      | Create project                                         |
| GET    | `/`                                      | List projects (filterable by status, name, date range) |
| GET    | `/{project_id}`                          | Project details                                        |
| PATCH  | `/{project_id}`                          | Update project                                         |
| DELETE | `/{project_id}`                          | Delete project (lead manager or admin)                 |
| POST   | `/{project_id}/managers`                 | Add manager                                            |
| DELETE | `/{project_id}/managers/{manager_id}`    | Remove manager                                         |
| POST   | `/{project_id}/subcontractors`           | Add subcontractor                                      |
| PATCH  | `/{project_id}/subcontractors/{sub_id}`  | Update subcontractor assignment                        |
| DELETE | `/{project_id}/subcontractors/{sub_id}`  | Remove subcontractor                                   |
| GET    | `/{project_id}/available-subcontractors` | List unassigned subcontractors                         |
| GET    | `/{project_id}/statistics`               | Project statistics                                     |

### Subcontractors (`/api/subcontractors`)

| Method | Path                                 | Description                                  |
| ------ | ------------------------------------ | -------------------------------------------- |
| POST   | `/`                                  | Create subcontractor                         |
| GET    | `/`                                  | List all with pagination and filters         |
| GET    | `/my-subcontractors`                 | Subcontractors in current manager's projects |
| GET    | `/manager-stats`                     | Manager's subcontractor statistics           |
| GET    | `/search`                            | Search by name, company, email, trade        |
| GET    | `/available`                         | Available subcontractors for a date/time     |
| GET    | `/by-trade/{trade}`                  | Filter by trade specialty                    |
| PUT    | `/me`                                | Update own profile (subcontractor auth)      |
| GET    | `/{sub_id}`                          | Subcontractor details                        |
| PUT    | `/{sub_id}`                          | Update subcontractor (manager/admin)         |
| PUT    | `/{sub_id}/password`                 | Update password                              |
| DELETE | `/{sub_id}`                          | Deactivate subcontractor                     |
| POST   | `/{sub_id}/activate`                 | Reactivate subcontractor                     |
| DELETE | `/{sub_id}/permanent`                | Permanent delete (admin only)                |
| POST   | `/{sub_id}/send-welcome-email`       | Send invite/welcome email                    |
| GET    | `/{sub_id}/projects`                 | Assigned projects                            |
| GET    | `/{sub_id}/projects/current`         | Active projects only                         |
| GET    | `/{sub_id}/bookings`                 | Booking history                              |
| GET    | `/{sub_id}/bookings/upcoming`        | Upcoming bookings                            |
| GET    | `/{sub_id}/bookings/count-by-status` | Booking counts by status                     |
| GET    | `/{sub_id}/availability`             | Check availability                           |
| POST   | `/{sub_id}/projects/{project_id}`    | Assign to project                            |
| DELETE | `/{sub_id}/projects/{project_id}`    | Remove from project                          |

### Booking Audit (`/api/bookings`)

| Method | Path                          | Description                        |
| ------ | ----------------------------- | ---------------------------------- |
| GET    | `/{booking_id}/audit`         | Full audit trail for a booking     |
| GET    | `/audit/my-activity`          | Current user's audit activity      |
| GET    | `/audit/project/{project_id}` | Project audit logs (manager/admin) |

### File Upload (`/api/uploadfile`)

| Method | Path | Description                               |
| ------ | ---- | ----------------------------------------- |
| POST   | `/`  | Upload file (max 10 MB, restricted types) |

Allowed types: `.jpg`, `.jpeg`, `.png`, `.gif`, `.webp`, `.pdf`, `.doc`, `.docx`, `.xls`, `.xlsx`, `.csv`, `.txt`, `.json`

### Users (`/api/users`)

| Method | Path         | Description                  |
| ------ | ------------ | ---------------------------- |
| GET    | `/me`        | Get current user profile     |
| PUT    | `/me`        | Update own profile           |
| GET    | `/`          | List all users (admin only)  |
| PUT    | `/{user_id}` | Update any user (admin only) |

---

## Authentication

JWT-based with dual entity support (Users and Subcontractors).

| Token              | Expiry   | Purpose               |
| ------------------ | -------- | --------------------- |
| Access token       | 30 min   | API access            |
| Refresh token      | 7 days   | Get new access tokens |
| Email verification | 24 hours | Confirm email         |
| Password reset     | 1 hour   | Reset password        |

Algorithm: HS512

### Role-Based Access

| Role              | Access                                                |
| ----------------- | ----------------------------------------------------- |
| **Admin**         | Full access to all resources                          |
| **Manager**       | Project-scoped access, approve subcontractor bookings |
| **Subcontractor** | Own bookings, assigned projects                       |

### Rate Limits

| Endpoint                | Limit           |
| ----------------------- | --------------- |
| `/auth/login`           | 10 requests/min |
| `/auth/forgot-password` | 3 requests/min  |

---

## Database

PostgreSQL with SQLAlchemy ORM. All primary keys are UUIDs.

### Models

| Table                | Key Relationships                                    |
| -------------------- | ---------------------------------------------------- |
| `users`              | M2M with projects (via `manager_site_project`)       |
| `subcontractors`     | M2M with projects (via `subcontractor_site_project`) |
| `site_projects`      | Has many assets, bookings, managers, subcontractors  |
| `assets`             | Belongs to project, has many bookings                |
| `slot_bookings`      | Belongs to project, manager, subcontractor, asset    |
| `booking_audit_logs` | Belongs to booking, immutable                        |

### Enums

| Enum             | Values                                                                                                        |
| ---------------- | ------------------------------------------------------------------------------------------------------------- |
| `BookingStatus`  | `PENDING`, `CONFIRMED`, `IN_PROGRESS`, `COMPLETED`, `CANCELLED`, `DENIED`                                     |
| `AssetStatus`    | `available`, `in_use`, `maintenance`, `retired`                                                               |
| `ProjectStatus`  | `active`, `pending`, `completed`, `cancelled`, `on_hold`                                                      |
| `UserRole`       | `manager`, `admin`, `subcontractor`                                                                           |
| `TradeSpecialty` | `electrician`, `plumber`, `carpenter`, `mason`, `painter`, `hvac`, `roofer`, `landscaper`, `general`, `other` |

### Connection Pool

```

pool_size: 20, max_overflow: 40, pool_recycle: 1800s, pool_pre_ping: true

````

### Migrations

```bash
alembic upgrade head              # Apply all migrations
alembic revision --autogenerate -m "description"  # Generate migration
alembic downgrade -1              # Revert last migration
alembic history                   # Show migration history
````

---

## Environment Variables

```bash
# Database
DATABASE_URL=postgresql://user:password@localhost:5432/sitespace

# JWT (required - no defaults)
JWT_SECRET=your-secret-key
JWT_ALGORITHM=HS512
JWT_EXPIRATION_MS=86400000

# App (required - no defaults)
SECRET_KEY=your-app-secret

# Server
HOST=0.0.0.0
PORT=8080
DEBUG=False

# CORS
CORS_ORIGINS=http://localhost:3000,https://your-frontend.com

# Email (Mailtrap)
MAILTRAP_USE_SANDBOX=True
MAILTRAP_TOKEN=your-token
MAILTRAP_INBOX_ID=your-inbox-id
FROM_EMAIL=noreply@sitespace.com
FROM_NAME=Sitespace Team

# Frontend
FRONTEND_URL=http://localhost:3000
COOKIE_DOMAIN=                    # Optional, e.g. ".example.com"
IS_PRODUCTION=False
```

---

## Testing

```bash
# Run all tests
python tests/run_tests.py

# Run specific test module
pytest tests/test_auth.py -v

# Load testing with Locust
locust -f locustfile.py --host=http://localhost:8080
```

Test modules: `test_auth`, `test_assets`, `test_slot_booking`, `test_site_project`, `test_subcontractor`, `test_file_upload`, `test_forgot_password`

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

| Component        | Technology               |
| ---------------- | ------------------------ |
| Framework        | FastAPI 0.104.1          |
| ORM              | SQLAlchemy 2.0.23        |
| Database         | PostgreSQL 15            |
| Migrations       | Alembic 1.12.1           |
| Auth             | JWT (python-jose, HS512) |
| Password hashing | bcrypt + argon2          |
| Validation       | Pydantic 2.5.0           |
| Rate limiting    | slowapi 0.1.9            |
| Email            | Mailtrap API             |
| Server           | Uvicorn 0.24.0           |
| Container        | Docker + Docker Compose  |
