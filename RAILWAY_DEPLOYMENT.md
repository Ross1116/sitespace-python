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

### 3. Configure environment variables

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

### 4. Run migrations on deploy

The startup flow runs Alembic migrations via `start.sh`. Ensure the deployed command/path still executes:

```bash
alembic upgrade head
```

### 5. Verify deployment

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

- APScheduler is initialized when available and uses `SQLAlchemyJobStore(engine=engine)`.
- Nightly lookahead scheduling is controlled by `NIGHTLY_LOOKAHEAD_HOUR` and `NIGHTLY_LOOKAHEAD_MINUTE`.
- CORS uses `settings.effective_cors_origins`; localhost origins are added only when `DEBUG=True`.
- Production should keep `DEBUG=False` and set strong secrets.

## Troubleshooting

1. App boots but requests fail: verify `DATABASE_URL` and migration state.
2. 500 on startup: verify `JWT_SECRET` and `SECRET_KEY` are non-empty when `DEBUG=False`.
3. Lookahead job missing: check APScheduler dependency installation and startup logs.
4. CORS failures: validate exact frontend origin values in `CORS_ORIGINS`.

## Production Checklist

- [ ] Rotate and store secrets securely in Railway variables
- [ ] Confirm `DEBUG=False` and `IS_PRODUCTION=True`
- [ ] Confirm `alembic upgrade head` runs on deploy
- [ ] Validate `/health` and `/docs` post-deploy
- [ ] Verify auth + one protected route end-to-end
- [ ] Confirm logs and alerts are configured
