import os
import logging
import sentry_sdk

send_default_pii = os.getenv("SENTRY_SEND_PII", "false").strip().lower() in (
    "true",
    "1",
    "yes",
    "on",
)

sentry_sdk.init(
    dsn=os.getenv("SENTRY_DSN"),
    send_default_pii=send_default_pii,
    enable_logs=True,
    traces_sample_rate=0.4,
    profile_session_sample_rate=0.2,
    profile_lifecycle="trace",
)

# Structured logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)

from fastapi import FastAPI, HTTPException, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from contextlib import asynccontextmanager
import uvicorn
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded

from .core.config import settings
from .core.database import assert_database_connection, engine  # noqa: F401 — engine used at import for model registration
from .core.middleware import RequestLoggingMiddleware, TvReadOnlyMiddleware
from .api.v1 import auth, assets, asset_types, items, lookahead, slot_booking, site_project, subcontractor, users, booking_audit, files, site_plans, programmes
from .api import internal as internal_api

# Import all models so SQLAlchemy knows about them
from .models import user, asset, asset_type as asset_type_model, slot_booking as slot_booking_model, site_project as site_project_model, subcontractor as subcontractor_model, stored_file as stored_file_model, site_plan as site_plan_model, programme, lookahead as lookahead_models, item_identity
from .models import job_queue as job_queue_model  # noqa: F401 — register tables

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup — web service only needs to verify schema readiness
    logger.info("Starting Sitespace FastAPI application (web)...")
    yield
    logger.info("Shutting down Sitespace FastAPI application...")

# Create FastAPI app
app = FastAPI(
    title="Sitespace API",
    description="Construction site management API converted from Spring Boot",
    version="1.0.0",
    lifespan=lifespan
)

# Rate limiter
limiter = Limiter(key_func=get_remote_address)
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

# Add CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.effective_cors_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type", "Accept", "X-Request-ID"],
)

# Request logging + Sentry user context
# Enforce TV read-only mode (must wrap API so blocked writes are still logged)
app.add_middleware(TvReadOnlyMiddleware)

# Request logging + Sentry user context
app.add_middleware(RequestLoggingMiddleware)

# Global exception handler for HTTPException
@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    is_server_error = exc.status_code >= 500
    if is_server_error:
        safe_detail = "Internal server error"
    else:
        safe_detail = exc.detail

    return JSONResponse(
        status_code=exc.status_code,
        content={
            "success": False,
            "message": safe_detail,
            "detail": safe_detail
        }
    )

# Global exception handler for all other exceptions
error_logger = logging.getLogger("sitespace.errors")

@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    error_logger.exception("Unhandled exception on %s %s", request.method, request.url.path)
    sentry_sdk.capture_exception(exc)

    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={
            "success": False,
            "message": "Internal server error",
            "detail": "An unexpected error occurred"
        }
    )

# Include routers
app.include_router(auth.router, prefix="/api")
app.include_router(assets.router, prefix="/api")
app.include_router(asset_types.router, prefix="/api")
app.include_router(slot_booking.router, prefix="/api")
app.include_router(site_project.router, prefix="/api")
app.include_router(subcontractor.router, prefix="/api")
app.include_router(users.router, prefix="/api") 
app.include_router(booking_audit.router, prefix="/api")
app.include_router(files.router, prefix="/api")
app.include_router(site_plans.router, prefix="/api")
app.include_router(programmes.router, prefix="/api")
app.include_router(items.router, prefix="/api")
app.include_router(lookahead.router, prefix="/api")
# Internal endpoints only on the web service (serves files to worker over private network)
if settings.SERVICE_ROLE == "web" and settings.INTERNAL_API_SECRET:
    app.include_router(internal_api.router)

# Root endpoint
@app.get("/")
async def root():
    return {
        "message": "Sitespace API is running",
        "version": "1.0.0",
        "docs": "/docs"
    }

# Health check endpoint
@app.get("/health")
async def health_check():
    try:
        assert_database_connection(engine)
    except Exception as e:
        logger.warning("Database check failed. Type: %s, Error: %s", type(e).__name__, e)
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content={
                "status": "unhealthy",
                "message": "Sitespace API cannot reach the database",
                "version": "1.0.0",
                "database": "disconnected",
            },
        )

    return {
        "status": "healthy",
        "message": "Sitespace API is healthy",
        "version": "1.0.0",
        "database": "connected",
    }

if __name__ == "__main__":
    uvicorn.run(
        "app.main:app",
        host=settings.host,
        port=settings.port,
        reload=settings.debug,
        proxy_headers=True,
        forwarded_allow_ips=os.getenv(
            "FORWARDED_ALLOW_IPS",
            "127.0.0.1,10.0.0.0/8",
        ),
    )
