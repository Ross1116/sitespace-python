# SiteSpace Backend - Production Testing Guide

## For Large-Scale Application Deployment

---

## Table of Contents

1. [Load & Stress Testing](#1-load--stress-testing)
2. [Database Performance Testing](#2-database-performance-testing)
3. [Security Testing](#3-security-testing)
4. [API Contract Testing](#4-api-contract-testing)
5. [Staging Environment Testing](#5-staging-environment-testing)
6. [Performance Profiling](#6-performance-profiling)
7. [Monitoring & Observability](#7-monitoring--observability)
8. [Chaos Engineering](#8-chaos-engineering--failure-testing)
9. [Pre-Production Checklist](#9-pre-production-checklist)
10. [Testing Workflow](#10-recommended-testing-workflow)

---

## 1. Load & Stress Testing

Test how your API handles concurrent users and high traffic.

### Recommended Tools

| Tool | Description | Best For |
|------|-------------|----------|
| **Locust** | Python-based, great for FastAPI | Python developers |
| **k6** | Modern, scriptable in JavaScript | CI/CD integration |
| **Apache JMeter** | GUI-based, comprehensive | Complex scenarios |

### Installation

```bash
pip install locust
```

### Sample Locust Configuration

Create a `locustfile.py` in your project root:

```python
from locust import HttpUser, task, between

class SiteSpaceUser(HttpUser):
    wait_time = between(1, 3)

    def on_start(self):
        # Login and get token
        response = self.client.post("/api/auth/login", json={
            "email": "test@example.com",
            "password": "testpass123"
        })
        self.token = response.json().get("data", {}).get("access_token")

    @task(3)
    def get_projects(self):
        self.client.get("/api/site-projects",
            headers={"Authorization": f"Bearer {self.token}"})

    @task(2)
    def get_bookings(self):
        self.client.get("/api/slot-bookings",
            headers={"Authorization": f"Bearer {self.token}"})

    @task(1)
    def get_assets(self):
        self.client.get("/api/assets",
            headers={"Authorization": f"Bearer {self.token}"})

    @task(1)
    def create_booking(self):
        self.client.post("/api/slot-bookings",
            headers={"Authorization": f"Bearer {self.token}"},
            json={
                "project_id": "uuid-here",
                "asset_id": "uuid-here",
                "booking_date": "2024-12-01",
                "start_time": "09:00",
                "end_time": "17:00",
                "purpose": "Load test booking"
            })
```

### Running Load Tests

```bash
# Start Locust web UI
locust -f locustfile.py --host=http://localhost:8080

# Headless mode for CI/CD
locust -f locustfile.py --host=http://localhost:8080 \
    --users 100 --spawn-rate 10 --run-time 5m --headless
```

### Performance Targets

| Metric | Target | Critical Threshold |
|--------|--------|-------------------|
| Response time (p50) | < 200ms | < 500ms |
| Response time (p95) | < 500ms | < 1000ms |
| Response time (p99) | < 1000ms | < 2000ms |
| Throughput | Based on expected users | - |
| Error rate | < 0.1% | < 1% |
| Concurrent users | 100-1000+ | Based on scale |

---

## 2. Database Performance Testing

### PostgreSQL Benchmarking

```bash
# Initialize pgbench (comes with PostgreSQL)
pgbench -i -s 50 your_database

# Run benchmark with 10 clients, 2 threads, 1000 transactions
pgbench -c 10 -j 2 -t 1000 your_database
```

### Connection Pool Validation

Your current settings:
- Pool size: 20
- Max overflow: 40
- Connection timeout: 30 seconds
- Pool recycle: 1800 seconds

Test these under load to ensure they're adequate.

### Query Performance Analysis

```sql
-- Enable query statistics (run once)
CREATE EXTENSION IF NOT EXISTS pg_stat_statements;

-- Check slow queries
SELECT
    query,
    calls,
    mean_time,
    total_time,
    rows
FROM pg_stat_statements
ORDER BY mean_time DESC
LIMIT 20;

-- Check for missing indexes (high seq_scan vs idx_scan)
SELECT
    schemaname,
    relname AS table_name,
    seq_scan,
    seq_tup_read,
    idx_scan,
    idx_tup_fetch
FROM pg_stat_user_tables
WHERE seq_scan > idx_scan
ORDER BY seq_scan DESC;

-- Check index usage
SELECT
    indexrelname AS index_name,
    relname AS table_name,
    idx_scan,
    idx_tup_read,
    idx_tup_fetch
FROM pg_stat_user_indexes
ORDER BY idx_scan DESC;
```

### Critical Queries to Optimize

Run `EXPLAIN ANALYZE` on these frequent queries:

```sql
-- User login lookup
EXPLAIN ANALYZE
SELECT * FROM users WHERE email = 'test@example.com';

-- Subcontractor lookup
EXPLAIN ANALYZE
SELECT * FROM subcontractors WHERE email = 'test@example.com';

-- Project listings with relationships
EXPLAIN ANALYZE
SELECT p.*,
       array_agg(DISTINCT m.user_id) as managers,
       array_agg(DISTINCT s.subcontractor_id) as subcontractors
FROM site_projects p
LEFT JOIN manager_site_project m ON p.id = m.site_project_id
LEFT JOIN subcontractor_site_project s ON p.id = s.site_project_id
GROUP BY p.id;

-- Booking queries by date range
EXPLAIN ANALYZE
SELECT * FROM slot_bookings
WHERE booking_date BETWEEN '2024-01-01' AND '2024-12-31'
AND project_id = 'uuid-here';
```

### Recommended Indexes

```sql
-- Ensure these indexes exist
CREATE INDEX IF NOT EXISTS idx_users_email ON users(email);
CREATE INDEX IF NOT EXISTS idx_subcontractors_email ON subcontractors(email);
CREATE INDEX IF NOT EXISTS idx_slot_bookings_date ON slot_bookings(booking_date);
CREATE INDEX IF NOT EXISTS idx_slot_bookings_project ON slot_bookings(project_id);
CREATE INDEX IF NOT EXISTS idx_assets_project ON assets(project_id);
CREATE INDEX IF NOT EXISTS idx_assets_status ON assets(status);
```

---

## 3. Security Testing

### 3.1 Automated Code Security Scanning

```bash
# Install security tools
pip install bandit safety

# Scan code for security vulnerabilities
bandit -r app/ -ll -f json -o security_report.json

# Check dependencies for known CVEs
safety check -r requirements.txt --json > dependency_report.json

# Run both in CI/CD
bandit -r app/ -ll && safety check -r requirements.txt
```

### 3.2 OWASP Top 10 Testing Checklist

| Vulnerability | Test Method | Priority |
|--------------|-------------|----------|
| **SQL Injection** | Try `' OR '1'='1` in all inputs | CRITICAL |
| **Broken Authentication** | Test token expiry, weak passwords | CRITICAL |
| **Sensitive Data Exposure** | Check logs, responses for secrets | HIGH |
| **XML External Entities** | N/A (JSON API) | LOW |
| **Broken Access Control** | IDOR tests on all endpoints | CRITICAL |
| **Security Misconfiguration** | Check headers, CORS, debug mode | HIGH |
| **XSS** | Test all text inputs | MEDIUM |
| **Insecure Deserialization** | Test JSON payloads | MEDIUM |
| **Using Vulnerable Components** | Run `safety check` | HIGH |
| **Insufficient Logging** | Verify auth failures logged | MEDIUM |

### 3.3 Authentication Security Tests

```python
# tests/test_security.py
import httpx
import pytest

BASE_URL = "http://localhost:8080"

class TestAuthenticationSecurity:

    def test_login_rate_limiting(self):
        """Test that brute force is prevented"""
        for i in range(100):
            response = httpx.post(f"{BASE_URL}/api/auth/login", json={
                "email": "test@example.com",
                "password": f"wrongpass{i}"
            })
        # Should be rate limited after X attempts
        assert response.status_code == 429

    def test_expired_token_rejected(self):
        """Test that expired tokens are rejected"""
        expired_token = "eyJ..."  # Generate an expired token
        response = httpx.get(
            f"{BASE_URL}/api/users/me",
            headers={"Authorization": f"Bearer {expired_token}"}
        )
        assert response.status_code == 401

    def test_tampered_token_rejected(self):
        """Test that modified tokens are rejected"""
        # Get valid token, modify payload
        response = httpx.get(
            f"{BASE_URL}/api/users/me",
            headers={"Authorization": "Bearer tampered.token.here"}
        )
        assert response.status_code == 401

    def test_sql_injection_login(self):
        """Test SQL injection in login"""
        payloads = [
            "' OR '1'='1",
            "'; DROP TABLE users; --",
            "admin'--",
        ]
        for payload in payloads:
            response = httpx.post(f"{BASE_URL}/api/auth/login", json={
                "email": payload,
                "password": payload
            })
            # Should return validation error, not success
            assert response.status_code in [400, 401, 422]
```

### 3.4 IDOR (Insecure Direct Object Reference) Tests

```python
class TestIDOR:

    def test_cannot_access_other_users_projects(self):
        """User A cannot access User B's projects"""
        # Login as User A
        token_a = login("user_a@example.com", "password")

        # Try to access User B's project
        response = httpx.get(
            f"{BASE_URL}/api/site-projects/{user_b_project_id}",
            headers={"Authorization": f"Bearer {token_a}"}
        )
        assert response.status_code == 403

    def test_cannot_modify_other_users_bookings(self):
        """User A cannot modify User B's bookings"""
        token_a = login("user_a@example.com", "password")

        response = httpx.put(
            f"{BASE_URL}/api/slot-bookings/{user_b_booking_id}",
            headers={"Authorization": f"Bearer {token_a}"},
            json={"status": "cancelled"}
        )
        assert response.status_code == 403
```

### 3.5 CRITICAL Security Fixes Required

**IMMEDIATE ACTION REQUIRED:**

```bash
# Generate new secrets
openssl rand -hex 32  # For JWT_SECRET
openssl rand -hex 32  # For SECRET_KEY
```

Update your environment variables:

| Variable | Current (INSECURE) | Action |
|----------|-------------------|--------|
| `JWT_SECRET` | `<your-secret-here>` | **CHANGE IMMEDIATELY** |
| `SECRET_KEY` | `<your-secret-here>` | **CHANGE IMMEDIATELY** |

### 3.6 Security Headers

Add these headers to your FastAPI app:

```python
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.base import BaseHTTPMiddleware

class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["X-XSS-Protection"] = "1; mode=block"
        response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
        response.headers["Content-Security-Policy"] = "default-src 'self'"
        return response

# Add to main.py
app.add_middleware(SecurityHeadersMiddleware)
```

---

## 4. API Contract Testing

### Comprehensive Endpoint Testing

```python
# tests/test_api_contracts.py
import httpx
import pytest
from uuid import uuid4

BASE_URL = "http://localhost:8080"

class TestAPIContracts:

    @pytest.fixture(autouse=True)
    def setup(self):
        # Login and get token
        response = httpx.post(f"{BASE_URL}/api/auth/login", json={
            "email": "admin@example.com",
            "password": "adminpass123"
        })
        self.token = response.json()["data"]["access_token"]
        self.headers = {"Authorization": f"Bearer {self.token}"}

    # ============ AUTH ENDPOINTS ============

    def test_login_success(self):
        response = httpx.post(f"{BASE_URL}/api/auth/login", json={
            "email": "valid@example.com",
            "password": "validpass"
        })
        assert response.status_code == 200
        data = response.json()
        assert "access_token" in data["data"]
        assert "refresh_token" in data["data"]

    def test_login_invalid_email(self):
        response = httpx.post(f"{BASE_URL}/api/auth/login", json={
            "email": "nonexistent@example.com",
            "password": "anypass"
        })
        assert response.status_code == 401

    def test_register_success(self):
        response = httpx.post(f"{BASE_URL}/api/auth/register", json={
            "email": f"newuser_{uuid4()}@example.com",
            "password": "SecurePass123!",
            "first_name": "Test",
            "last_name": "User"
        })
        assert response.status_code in [200, 201]

    def test_register_duplicate_email(self):
        email = f"duplicate_{uuid4()}@example.com"
        # First registration
        httpx.post(f"{BASE_URL}/api/auth/register", json={
            "email": email,
            "password": "SecurePass123!",
            "first_name": "Test",
            "last_name": "User"
        })
        # Duplicate registration
        response = httpx.post(f"{BASE_URL}/api/auth/register", json={
            "email": email,
            "password": "SecurePass123!",
            "first_name": "Test",
            "last_name": "User"
        })
        assert response.status_code == 400

    def test_refresh_token(self):
        # First login
        login_response = httpx.post(f"{BASE_URL}/api/auth/login", json={
            "email": "valid@example.com",
            "password": "validpass"
        })
        refresh_token = login_response.json()["data"]["refresh_token"]

        # Refresh
        response = httpx.post(f"{BASE_URL}/api/auth/refresh", json={
            "refresh_token": refresh_token
        })
        assert response.status_code == 200
        assert "access_token" in response.json()["data"]

    def test_get_current_user(self):
        response = httpx.get(f"{BASE_URL}/api/auth/me", headers=self.headers)
        assert response.status_code == 200
        data = response.json()["data"]
        assert "email" in data
        assert "id" in data

    def test_logout(self):
        response = httpx.post(f"{BASE_URL}/api/auth/logout", headers=self.headers)
        assert response.status_code == 200

    # ============ SITE PROJECTS ============

    def test_list_projects(self):
        response = httpx.get(f"{BASE_URL}/api/site-projects", headers=self.headers)
        assert response.status_code == 200
        assert isinstance(response.json()["data"], list)

    def test_create_project(self):
        response = httpx.post(f"{BASE_URL}/api/site-projects",
            headers=self.headers,
            json={
                "name": f"Test Project {uuid4()}",
                "description": "Test description",
                "location": "Sydney, Australia",
                "start_date": "2024-01-01",
                "end_date": "2024-12-31"
            })
        assert response.status_code in [200, 201]
        assert "id" in response.json()["data"]

    def test_get_project(self):
        # Create project first
        create_response = httpx.post(f"{BASE_URL}/api/site-projects",
            headers=self.headers,
            json={
                "name": f"Test Project {uuid4()}",
                "description": "Test",
                "location": "Sydney"
            })
        project_id = create_response.json()["data"]["id"]

        # Get project
        response = httpx.get(
            f"{BASE_URL}/api/site-projects/{project_id}",
            headers=self.headers
        )
        assert response.status_code == 200

    def test_update_project(self):
        # Create project first
        create_response = httpx.post(f"{BASE_URL}/api/site-projects",
            headers=self.headers,
            json={
                "name": f"Test Project {uuid4()}",
                "description": "Test",
                "location": "Sydney"
            })
        project_id = create_response.json()["data"]["id"]

        # Update project
        response = httpx.put(
            f"{BASE_URL}/api/site-projects/{project_id}",
            headers=self.headers,
            json={"name": "Updated Project Name"}
        )
        assert response.status_code == 200

    def test_delete_project(self):
        # Create project first
        create_response = httpx.post(f"{BASE_URL}/api/site-projects",
            headers=self.headers,
            json={
                "name": f"Test Project {uuid4()}",
                "description": "Test",
                "location": "Sydney"
            })
        project_id = create_response.json()["data"]["id"]

        # Delete project
        response = httpx.delete(
            f"{BASE_URL}/api/site-projects/{project_id}",
            headers=self.headers
        )
        assert response.status_code in [200, 204]

    # ============ SLOT BOOKINGS ============

    def test_list_bookings(self):
        response = httpx.get(f"{BASE_URL}/api/slot-bookings", headers=self.headers)
        assert response.status_code == 200

    def test_create_booking(self):
        # Need valid project_id and asset_id
        response = httpx.post(f"{BASE_URL}/api/slot-bookings",
            headers=self.headers,
            json={
                "project_id": "valid-project-uuid",
                "asset_id": "valid-asset-uuid",
                "booking_date": "2024-06-15",
                "start_time": "09:00",
                "end_time": "17:00",
                "purpose": "Testing"
            })
        # Will fail without valid UUIDs, but tests the endpoint
        assert response.status_code in [200, 201, 400, 404]

    # ============ ASSETS ============

    def test_list_assets(self):
        response = httpx.get(f"{BASE_URL}/api/assets", headers=self.headers)
        assert response.status_code == 200

    # ============ SUBCONTRACTORS ============

    def test_list_subcontractors(self):
        response = httpx.get(f"{BASE_URL}/api/subcontractors", headers=self.headers)
        assert response.status_code == 200

    # ============ ERROR HANDLING ============

    def test_404_not_found(self):
        response = httpx.get(
            f"{BASE_URL}/api/site-projects/{uuid4()}",
            headers=self.headers
        )
        assert response.status_code == 404

    def test_401_unauthorized(self):
        response = httpx.get(f"{BASE_URL}/api/users/me")
        assert response.status_code == 401

    def test_422_validation_error(self):
        response = httpx.post(f"{BASE_URL}/api/auth/login", json={
            "email": "not-an-email",
            "password": ""
        })
        assert response.status_code == 422
```

### Running API Tests

```bash
# Run all API tests
pytest tests/test_api_contracts.py -v

# Run with coverage
pytest tests/test_api_contracts.py -v --cov=app --cov-report=html

# Run specific test class
pytest tests/test_api_contracts.py::TestAPIContracts -v
```

---

## 5. Staging Environment Testing

### Docker Compose for Staging

Create `docker-compose.staging.yml`:

```yaml
version: '3.8'

services:
  app:
    build:
      context: .
      dockerfile: Dockerfile
    ports:
      - "8080:8080"
    environment:
      - DATABASE_URL=postgresql://sitespace:stagingpass@db:5432/sitespace_staging
      - JWT_SECRET=${JWT_SECRET}
      - SECRET_KEY=${SECRET_KEY}
      - IS_PRODUCTION=true
      - DEBUG=false
      - MAILTRAP_USE_SANDBOX=true
      - CORS_ORIGINS=https://staging.sitespace.com.au
      - FRONTEND_URL=https://staging.sitespace.com.au
    depends_on:
      db:
        condition: service_healthy
      redis:
        condition: service_started
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:8080/health"]
      interval: 30s
      timeout: 10s
      retries: 3

  db:
    image: postgres:15
    environment:
      - POSTGRES_USER=sitespace
      - POSTGRES_PASSWORD=stagingpass
      - POSTGRES_DB=sitespace_staging
    volumes:
      - staging_db_data:/var/lib/postgresql/data
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U sitespace -d sitespace_staging"]
      interval: 10s
      timeout: 5s
      retries: 5

  redis:
    image: redis:7-alpine
    ports:
      - "6379:6379"
    healthcheck:
      test: ["CMD", "redis-cli", "ping"]
      interval: 10s
      timeout: 5s
      retries: 5

volumes:
  staging_db_data:
```

### Staging Deployment Commands

```bash
# Start staging environment
docker-compose -f docker-compose.staging.yml up -d

# Run migrations
docker-compose -f docker-compose.staging.yml exec app alembic upgrade head

# Check logs
docker-compose -f docker-compose.staging.yml logs -f app

# Run tests against staging
BASE_URL=http://localhost:8080 pytest tests/ -v

# Tear down
docker-compose -f docker-compose.staging.yml down
```

### Staging Environment Checklist

- [ ] Mirror production infrastructure
- [ ] Use anonymized production data
- [ ] Same environment variable structure
- [ ] Test all migrations: `alembic upgrade head`
- [ ] Verify rollback: `alembic downgrade -1`
- [ ] Test with production-like load
- [ ] Verify external integrations (Mailtrap sandbox)
- [ ] Test SSL/TLS configuration
- [ ] Verify CORS settings

---

## 6. Performance Profiling

### Request Profiling Middleware

Add to `app/middleware/profiling.py`:

```python
import time
import logging
from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware

logger = logging.getLogger(__name__)

class ProfilingMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        start_time = time.perf_counter()

        response = await call_next(request)

        duration = time.perf_counter() - start_time
        duration_ms = duration * 1000

        # Log slow requests
        if duration > 1.0:
            logger.warning(
                f"SLOW REQUEST: {request.method} {request.url.path} "
                f"took {duration_ms:.2f}ms"
            )

        # Add timing header
        response.headers["X-Response-Time-Ms"] = f"{duration_ms:.2f}"

        # Log all requests in debug mode
        logger.debug(
            f"{request.method} {request.url.path} - "
            f"{response.status_code} - {duration_ms:.2f}ms"
        )

        return response
```

### Database Query Profiling

```python
# app/core/database.py
import logging
from sqlalchemy import event
from sqlalchemy.engine import Engine
import time

logger = logging.getLogger(__name__)

@event.listens_for(Engine, "before_cursor_execute")
def before_cursor_execute(conn, cursor, statement, parameters, context, executemany):
    conn.info.setdefault('query_start_time', []).append(time.perf_counter())

@event.listens_for(Engine, "after_cursor_execute")
def after_cursor_execute(conn, cursor, statement, parameters, context, executemany):
    total = time.perf_counter() - conn.info['query_start_time'].pop(-1)
    if total > 0.1:  # Log queries taking > 100ms
        logger.warning(f"SLOW QUERY ({total*1000:.2f}ms): {statement[:200]}")
```

### Memory Profiling

```bash
# Install memory profiler
pip install memory-profiler

# Profile memory usage
python -m memory_profiler run.py

# Or use tracemalloc in code
```

```python
# Add to main.py for debugging
import tracemalloc

@app.on_event("startup")
async def start_memory_tracking():
    tracemalloc.start()

@app.get("/debug/memory")
async def memory_stats():
    snapshot = tracemalloc.take_snapshot()
    top_stats = snapshot.statistics('lineno')[:10]
    return {"top_memory_consumers": [str(stat) for stat in top_stats]}
```

---

## 7. Monitoring & Observability

### 7.1 Prometheus Metrics

```bash
pip install prometheus-fastapi-instrumentator
```

```python
# app/main.py
from prometheus_fastapi_instrumentator import Instrumentator

app = FastAPI(...)

# Add Prometheus metrics
Instrumentator().instrument(app).expose(app, endpoint="/metrics")
```

### 7.2 Structured Logging

```python
# app/core/logging.py
import logging
import json
from datetime import datetime

class JSONFormatter(logging.Formatter):
    def format(self, record):
        log_obj = {
            "timestamp": datetime.utcnow().isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "module": record.module,
            "function": record.funcName,
            "line": record.lineno,
        }

        if record.exc_info:
            log_obj["exception"] = self.formatException(record.exc_info)

        if hasattr(record, 'request_id'):
            log_obj["request_id"] = record.request_id

        return json.dumps(log_obj)

def setup_logging():
    handler = logging.StreamHandler()
    handler.setFormatter(JSONFormatter())

    root_logger = logging.getLogger()
    root_logger.addHandler(handler)
    root_logger.setLevel(logging.INFO)
```

### 7.3 Enhanced Health Check

```python
# app/api/v1/health.py
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from sqlalchemy import text
import httpx

router = APIRouter()

@router.get("/health")
async def health_check(db: Session = Depends(get_db)):
    checks = {
        "status": "healthy",
        "checks": {
            "database": {"status": "unknown", "latency_ms": None},
            "redis": {"status": "unknown", "latency_ms": None},
            "email_service": {"status": "unknown"},
        }
    }

    # Database check
    try:
        start = time.perf_counter()
        db.execute(text("SELECT 1"))
        latency = (time.perf_counter() - start) * 1000
        checks["checks"]["database"] = {
            "status": "healthy",
            "latency_ms": round(latency, 2)
        }
    except Exception as e:
        checks["checks"]["database"] = {
            "status": "unhealthy",
            "error": str(e)
        }
        checks["status"] = "degraded"

    # Redis check (when implemented)
    # try:
    #     start = time.perf_counter()
    #     redis_client.ping()
    #     latency = (time.perf_counter() - start) * 1000
    #     checks["checks"]["redis"] = {
    #         "status": "healthy",
    #         "latency_ms": round(latency, 2)
    #     }
    # except Exception as e:
    #     checks["checks"]["redis"] = {"status": "unhealthy", "error": str(e)}

    return checks

@router.get("/health/ready")
async def readiness_check(db: Session = Depends(get_db)):
    """Kubernetes readiness probe"""
    try:
        db.execute(text("SELECT 1"))
        return {"status": "ready"}
    except:
        raise HTTPException(status_code=503, detail="Not ready")

@router.get("/health/live")
async def liveness_check():
    """Kubernetes liveness probe"""
    return {"status": "alive"}
```

### 7.4 Request Tracing

```python
# app/middleware/tracing.py
import uuid
from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware

class RequestTracingMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        request_id = request.headers.get("X-Request-ID", str(uuid.uuid4()))

        # Add to request state for logging
        request.state.request_id = request_id

        response = await call_next(request)

        # Add to response headers
        response.headers["X-Request-ID"] = request_id

        return response
```

---

## 8. Chaos Engineering / Failure Testing

### Failure Scenarios to Test

| Scenario | How to Simulate | Expected Behavior |
|----------|-----------------|-------------------|
| Database unavailable | `docker-compose stop db` | Graceful error, health check fails |
| Database slow | Add pg_sleep in queries | Timeouts work, requests don't hang |
| High memory usage | Limit container to 256MB | OOM handling, no data corruption |
| Network partition | Use `tc` to drop packets | Retry logic works |
| Disk full | Fill upload directory | Clear error message returned |
| Redis down | Stop Redis container | Falls back gracefully |
| Email service down | Block Mailtrap | Async operations don't block |

### Chaos Testing Scripts

```bash
#!/bin/bash
# chaos_test.sh

echo "=== Chaos Engineering Tests ==="

# Test 1: Database failure
echo "Test 1: Database failure simulation"
docker-compose stop db
sleep 5
curl -s http://localhost:8080/health | jq .
docker-compose start db
sleep 10

# Test 2: High load
echo "Test 2: High concurrent load"
ab -n 1000 -c 100 http://localhost:8080/api/site-projects

# Test 3: Memory pressure
echo "Test 3: Memory pressure"
docker update --memory=256m sitespace-python_app_1
# Run load test and monitor

# Test 4: Slow network
echo "Test 4: Network latency"
docker exec sitespace-python_app_1 tc qdisc add dev eth0 root netem delay 500ms
curl -w "@curl-format.txt" http://localhost:8080/health
docker exec sitespace-python_app_1 tc qdisc del dev eth0 root

echo "=== Chaos Tests Complete ==="
```

### Recovery Testing

```bash
# Test database recovery
docker-compose stop db
# Wait for app to detect failure
docker-compose start db
# Verify app recovers automatically

# Test rolling restart
docker-compose up -d --no-deps --build app
# Verify zero downtime
```

---

## 9. Pre-Production Checklist

### Configuration Security

| Item | Status | Action Required |
|------|--------|-----------------|
| `JWT_SECRET` changed | [ ] | Generate: `openssl rand -hex 32` |
| `SECRET_KEY` changed | [ ] | Generate: `openssl rand -hex 32` |
| `DEBUG=false` | [ ] | Set in production env |
| `IS_PRODUCTION=true` | [ ] | Set in production env |
| Default passwords removed | [ ] | Check all config files |
| `.env` not in repo | [ ] | Verify `.gitignore` |

### Database Readiness

| Item | Status | Notes |
|------|--------|-------|
| All migrations tested | [ ] | `alembic upgrade head` |
| Rollback tested | [ ] | `alembic downgrade -1` |
| Backup strategy implemented | [ ] | Daily backups minimum |
| Connection pool tuned | [ ] | Based on load test results |
| Indexes verified | [ ] | Run EXPLAIN ANALYZE |
| Query performance acceptable | [ ] | p95 < 100ms |

### Security Hardening

| Item | Status | Notes |
|------|--------|-------|
| Rate limiting implemented | [ ] | On auth endpoints |
| HTTPS enforced | [ ] | Redirect HTTP to HTTPS |
| Security headers added | [ ] | HSTS, CSP, X-Frame-Options |
| CORS properly configured | [ ] | Production domains only |
| Token blacklist in Redis | [ ] | Not in-memory |
| Input validation complete | [ ] | All endpoints |
| SQL injection tested | [ ] | Parameterized queries |
| IDOR vulnerabilities checked | [ ] | Access control tests |

### Infrastructure

| Item | Status | Notes |
|------|--------|-------|
| Health checks configured | [ ] | `/health`, `/health/ready` |
| Auto-scaling configured | [ ] | Based on load metrics |
| Logging aggregation | [ ] | JSON structured logs |
| Error alerting | [ ] | PagerDuty/Slack integration |
| Metrics collection | [ ] | Prometheus/Grafana |
| Backup automation | [ ] | Database + file uploads |
| Disaster recovery plan | [ ] | Documented and tested |
| Rollback procedure | [ ] | Documented and tested |

### Performance

| Item | Status | Target |
|------|--------|--------|
| Load test passed | [ ] | 100+ concurrent users |
| Response time p95 | [ ] | < 500ms |
| Error rate | [ ] | < 0.1% |
| Database queries optimized | [ ] | No N+1 queries |
| Memory usage stable | [ ] | No leaks under load |

### Documentation

| Item | Status | Notes |
|------|--------|-------|
| API documentation current | [ ] | Swagger/OpenAPI |
| Deployment guide | [ ] | Step-by-step instructions |
| Runbook for incidents | [ ] | Common issues + fixes |
| Environment variables documented | [ ] | All required vars listed |

---

## 10. Recommended Testing Workflow

```
┌─────────────────────────────────────────────────────────┐
│                    DEVELOPMENT                          │
├─────────────────────────────────────────────────────────┤
│  1. Write code                                          │
│  2. Run unit tests: pytest tests/                       │
│  3. Run linting: black . && flake8 app/                │
│  4. Run security scan: bandit -r app/                  │
└────────────────────────┬────────────────────────────────┘
                         │
                         ▼
┌─────────────────────────────────────────────────────────┐
│                 CONTINUOUS INTEGRATION                  │
├─────────────────────────────────────────────────────────┤
│  1. Run all tests: pytest --cov=app                    │
│  2. Check dependencies: safety check                    │
│  3. Type checking: mypy app/                           │
│  4. Build Docker image                                  │
└────────────────────────┬────────────────────────────────┘
                         │
                         ▼
┌─────────────────────────────────────────────────────────┐
│                  STAGING DEPLOYMENT                     │
├─────────────────────────────────────────────────────────┤
│  1. Deploy to staging environment                       │
│  2. Run database migrations                             │
│  3. Run API contract tests                              │
│  4. Run integration tests                               │
└────────────────────────┬────────────────────────────────┘
                         │
                         ▼
┌─────────────────────────────────────────────────────────┐
│                    LOAD TESTING                         │
├─────────────────────────────────────────────────────────┤
│  1. Run Locust with realistic user patterns            │
│  2. Verify response times meet targets                  │
│  3. Check error rates under load                        │
│  4. Monitor resource utilization                        │
└────────────────────────┬────────────────────────────────┘
                         │
                         ▼
┌─────────────────────────────────────────────────────────┐
│                  SECURITY TESTING                       │
├─────────────────────────────────────────────────────────┤
│  1. Run OWASP ZAP scan                                  │
│  2. Manual penetration testing                          │
│  3. Verify authentication/authorization                 │
│  4. Test rate limiting                                  │
└────────────────────────┬────────────────────────────────┘
                         │
                         ▼
┌─────────────────────────────────────────────────────────┐
│                   CHAOS TESTING                         │
├─────────────────────────────────────────────────────────┤
│  1. Simulate database failure                           │
│  2. Test under memory pressure                          │
│  3. Verify recovery procedures                          │
│  4. Test rollback process                               │
└────────────────────────┬────────────────────────────────┘
                         │
                         ▼
┌─────────────────────────────────────────────────────────┐
│                PRODUCTION DEPLOYMENT                    │
├─────────────────────────────────────────────────────────┤
│  1. Canary deployment (10% traffic)                    │
│  2. Monitor error rates and latency                     │
│  3. Gradual rollout (25% → 50% → 100%)                │
│  4. Keep previous version ready for rollback            │
└─────────────────────────────────────────────────────────┘
```

---

## Quick Reference Commands

```bash
# ==================== TESTING ====================

# Run all tests with coverage
pytest tests/ -v --cov=app --cov-report=html

# Run security scans
bandit -r app/ -ll && safety check -r requirements.txt

# Run linting
black app/ --check && flake8 app/ && mypy app/

# ==================== LOAD TESTING ====================

# Start Locust
locust -f locustfile.py --host=http://localhost:8080

# Headless load test
locust -f locustfile.py --host=http://localhost:8080 \
    --users 100 --spawn-rate 10 --run-time 5m --headless

# ==================== DATABASE ====================

# Run migrations
alembic upgrade head

# Rollback one migration
alembic downgrade -1

# Check migration status
alembic current

# ==================== DOCKER ====================

# Start staging
docker-compose -f docker-compose.staging.yml up -d

# View logs
docker-compose logs -f app

# Rebuild and restart
docker-compose up -d --build

# ==================== MONITORING ====================

# Check health
curl http://localhost:8080/health | jq .

# Check metrics (if Prometheus enabled)
curl http://localhost:8080/metrics

# ==================== SECRETS ====================

# Generate new JWT secret
openssl rand -hex 32

# Generate new secret key
openssl rand -hex 32
```

---

## Appendix: CI/CD Pipeline Example

### GitHub Actions Workflow

```yaml
# .github/workflows/test.yml
name: Test and Deploy

on:
  push:
    branches: [main, develop]
  pull_request:
    branches: [main]

jobs:
  test:
    runs-on: ubuntu-latest

    services:
      postgres:
        image: postgres:15
        env:
          POSTGRES_USER: test
          POSTGRES_PASSWORD: test
          POSTGRES_DB: test_db
        ports:
          - 5432:5432
        options: >-
          --health-cmd pg_isready
          --health-interval 10s
          --health-timeout 5s
          --health-retries 5

    steps:
      - uses: actions/checkout@v3

      - name: Set up Python
        uses: actions/setup-python@v4
        with:
          python-version: '3.11'

      - name: Install dependencies
        run: |
          pip install -r requirements.txt
          pip install -r requirements-test.txt

      - name: Run linting
        run: |
          black app/ --check
          flake8 app/
          mypy app/

      - name: Run security checks
        run: |
          bandit -r app/ -ll
          safety check -r requirements.txt

      - name: Run tests
        env:
          DATABASE_URL: postgresql://test:test@localhost:5432/test_db
        run: |
          alembic upgrade head
          pytest tests/ -v --cov=app --cov-report=xml

      - name: Upload coverage
        uses: codecov/codecov-action@v3

  deploy-staging:
    needs: test
    if: github.ref == 'refs/heads/develop'
    runs-on: ubuntu-latest
    steps:
      - name: Deploy to Railway Staging
        run: |
          # Railway deployment commands
          echo "Deploying to staging..."

  deploy-production:
    needs: test
    if: github.ref == 'refs/heads/main'
    runs-on: ubuntu-latest
    steps:
      - name: Deploy to Railway Production
        run: |
          # Railway deployment commands
          echo "Deploying to production..."
```

---

**Document Version:** 1.0
**Last Updated:** January 2026
**Application:** SiteSpace Backend (FastAPI + PostgreSQL)
