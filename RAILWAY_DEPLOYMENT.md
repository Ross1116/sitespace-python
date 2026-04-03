# Railway Deployment Guide for Sitespace FastAPI

Last updated: 2026-03-08

This guide reflects the current app wiring in `app/main.py` and the active API routers.

## Prerequisites

1. Railway account and project
2. Repository connected to Railway (Dockerfile-based deploy)
3. PostgreSQL service attached to the Railway project

## Required Repository Files

- `Dockerfile`
- `railway.toml`
- `requirements.txt`
- `start.sh`
- `alembic.ini`

## Deployment Steps

### 1. Create and connect project

1. Create a new Railway project.
2. Deploy from the GitHub repository.
3. Confirm Railway detects and builds from `Dockerfile`.

### 2. Add PostgreSQL service

1. Add a PostgreSQL service in Railway.
2. Ensure `DATABASE_URL` is injected into the app service.

### 3. Provision Railway services

Create three services from the same repository:

1. `web`
   - `SERVICE_ROLE=web`
   - exposes HTTP
   - configure Railway healthcheck to `GET /health`
2. `worker`
   - `SERVICE_ROLE=worker`
   - processes upload jobs only
   - no public HTTP exposure required
3. `nightly`
   - `SERVICE_ROLE=nightly`
   - runs the one-shot nightly tick only
   - configure Railway cron to invoke this service on the desired cadence

Example per-service env blocks:

```bash
# web
SERVICE_ROLE=web
HOST=0.0.0.0
PORT=8080
```

```bash
# worker
SERVICE_ROLE=worker
UPLOAD_WORKER_POLL_SECONDS=2
UPLOAD_WORKER_HEARTBEAT_SECONDS=15
UPLOAD_WORKER_CLAIM_TTL_SECONDS=90
UPLOAD_WORKER_MAX_ATTEMPTS=3
```

```bash
# nightly
SERVICE_ROLE=nightly
NIGHTLY_LOOKAHEAD_TIMEZONE=America/Los_Angeles
NIGHTLY_LOOKAHEAD_HOUR=17
NIGHTLY_LOOKAHEAD_MINUTE=0
```

Railway cron should target the `nightly` service itself so uploads and nightly recalculation are not left running in `web`.

### 4. Configure environment variables

Minimum required:

```bash
JWT_SECRET=<strong-random-secret>
SECRET_KEY=<strong-random-secret>
DATABASE_URL=<railway-postgres-url>
DEBUG=False
IS_PRODUCTION=True
```

Recommended core settings:

```bash
HOST=0.0.0.0
PORT=8080
JWT_ALGORITHM=HS512
JWT_EXPIRATION_MS=86400000
CORS_ORIGINS=https://sitespace.vercel.app,https://sitespace.com.au,https://www.sitespace.com.au
FORWARDED_ALLOW_IPS=127.0.0.1,10.0.0.0/8
```

AI/lookahead settings (if used):

```bash
AI_PROVIDER=anthropic
AI_API_KEY=<provider-key>
AI_MODEL=claude-haiku-4-5-20251001
AI_ENABLED=True
AI_TIMEOUT_STRUCTURE=8
AI_TIMEOUT_CLASSIFY=3
NIGHTLY_LOOKAHEAD_HOUR=17
NIGHTLY_LOOKAHEAD_MINUTE=0
NIGHTLY_LOOKAHEAD_TIMEZONE=America/Los_Angeles
```

Mailtrap settings (if notifications/email are enabled):

```bash
MAILTRAP_USE_SANDBOX=True
MAILTRAP_TOKEN=<token>
MAILTRAP_INBOX_ID=<inbox-id>
FROM_EMAIL=noreply@sitespace.com
FROM_NAME=Sitespace Team
FRONTEND_URL=https://<your-frontend-domain>
```

### 5. Run migrations on deploy

The startup flow runs Alembic migrations via `start.sh`. Ensure the deployed command/path still executes:

```bash
alembic upgrade head
```

### 5.1 Optional Stage 10 backfill rollout hook

`start.sh` also supports a temporary, opt-in Stage 10 rollout hook. This does **not** run by default.

Enable these Railway variables only for the rollout where you want startup to execute the Stage 10 learning replay:

```bash
RUN_STAGE10_BACKFILL_ON_STARTUP=true
STAGE10_BACKFILL_DELETE_LEGACY=false
```

If you want startup to prune unreferenced legacy null-project `item_context_profiles` after repointing, set:

```bash
STAGE10_BACKFILL_DELETE_LEGACY=true
```

This hook now performs the Stage 10 rollout in the required order:

```bash
alembic upgrade l2m3n4o5p6q7
python -m scripts.backfill_stage10_learning
alembic upgrade head
```

or, when legacy cleanup is enabled:

```bash
alembic upgrade l2m3n4o5p6q7
python -m scripts.backfill_stage10_learning --delete-unreferenced-legacy
alembic upgrade head
```

If the database is already at Stage 10 revision B (`m3n4o5p6q7r8`), startup skips the pre-backfill staged migration and only reruns the idempotent backfill before continuing.

After the Stage 10 rollout is complete, set both variables back to `false` (or remove them).

### 6. Verify deployment

Check these endpoints:

- `GET /health`
- `GET /docs`
- `POST /api/auth/login`
- `GET /api/projects`
- `GET /api/lookahead/{project_id}` (with valid auth/project)

## Current API Route Groups

All API routers are mounted under `/api`:

- `/auth`
- `/assets`
- `/bookings`
- `/projects`
- `/subcontractors`
- `/users`
- `/programmes`
- `/lookahead`
- `/files`
- `/site-plans`
- `/uploadfile` (legacy/defunct upload path)

## Operational Notes

- The web service is `SERVICE_ROLE=web`.
- The upload worker is `SERVICE_ROLE=worker`.
- The nightly tick service is `SERVICE_ROLE=nightly` and should be invoked by Railway cron (for example every 5 minutes).
- Nightly lookahead scheduling semantics are controlled by `NIGHTLY_LOOKAHEAD_HOUR`, `NIGHTLY_LOOKAHEAD_MINUTE`, and `NIGHTLY_LOOKAHEAD_TIMEZONE`.
- CORS uses `settings.effective_cors_origins`; localhost origins are added only when `DEBUG=True`.
- Production should keep `DEBUG=False` and set strong secrets.

## Troubleshooting

1. App boots but requests fail: verify `DATABASE_URL` and migration state.
2. 500 on startup: verify `JWT_SECRET` and `SECRET_KEY` are non-empty when `DEBUG=False`.
3. Lookahead job missing: check the Railway scheduled job service logs and the `scheduled_job_runs` table.
4. CORS failures: validate exact frontend origin values in `CORS_ORIGINS`.

## Production Checklist

- [ ] Rotate and store secrets securely in Railway variables
- [ ] Confirm `DEBUG=False` and `IS_PRODUCTION=True`
- [ ] Confirm `alembic upgrade head` runs on deploy
- [ ] Validate `/health` and `/docs` post-deploy
- [ ] Verify auth + one protected route end-to-end
- [ ] Confirm logs and alerts are configured
