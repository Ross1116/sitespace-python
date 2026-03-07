import os
import logging
import importlib
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

from sqlalchemy import text as sql_text
from fastapi import FastAPI, HTTPException, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from contextlib import asynccontextmanager
import uvicorn
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded

from .core.config import settings
from .core.database import engine, Base
from .core.middleware import RequestLoggingMiddleware, TvReadOnlyMiddleware
from .api.v1 import auth, assets, file_upload, lookahead, slot_booking, site_project, subcontractor, users, booking_audit, files, site_plans, programmes
from .services.lookahead_engine import nightly_lookahead_job

# Import all models so SQLAlchemy knows about them
from .models import user, asset, slot_booking as slot_booking_model, site_project as site_project_model, subcontractor as subcontractor_model, file_upload as file_upload_model, stored_file as stored_file_model, site_plan as site_plan_model, programme, lookahead as lookahead_models

try:
    _aps_scheduler_module = importlib.import_module("apscheduler.schedulers.asyncio")
    _aps_jobstore_module = importlib.import_module("apscheduler.jobstores.sqlalchemy")
    AsyncIOScheduler = _aps_scheduler_module.AsyncIOScheduler
    SQLAlchemyJobStore = _aps_jobstore_module.SQLAlchemyJobStore
    scheduler = AsyncIOScheduler(
        jobstores={"default": SQLAlchemyJobStore(url=settings.database_url)}
    )
except Exception as exc:
    scheduler = None
    logger.warning("APScheduler unavailable; nightly lookahead job disabled. Error: %s", exc)

# Create database tables (optional for testing) - Non-blocking
def create_tables_if_possible():
    try:
        Base.metadata.create_all(bind=engine)
        logger.info("Database tables created successfully")
        return True
    except Exception as e:
        logger.warning("Database connection failed: %s", e)
        logger.info("Application will run without database for testing")
        return False

# Try to create tables but don't block startup
try:
    create_tables_if_possible()
except Exception as e:
    logger.warning("Table creation skipped: %s", e)

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    logger.info("Starting Sitespace FastAPI application...")
    if scheduler is not None:
        scheduler.add_job(
            nightly_lookahead_job,
            trigger="cron",
            hour=17,
            minute=0,
            id="nightly_lookahead_job",
            replace_existing=True,
        )
        scheduler.start()
    yield
    # Shutdown
    if scheduler is not None and scheduler.running:
        scheduler.shutdown()
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
app.include_router(file_upload.router, prefix="/api")
app.include_router(slot_booking.router, prefix="/api")
app.include_router(site_project.router, prefix="/api")
app.include_router(subcontractor.router, prefix="/api")
app.include_router(users.router, prefix="/api") 
app.include_router(booking_audit.router, prefix="/api")
app.include_router(files.router, prefix="/api")
app.include_router(site_plans.router, prefix="/api")
app.include_router(programmes.router, prefix="/api")
app.include_router(lookahead.router, prefix="/api")

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
    health_status = {
        "status": "healthy",
        "message": "Sitespace API is healthy",
        "version": "1.0.0"
    }
    
    try:
        from .core.database import engine
        
        with engine.connect() as conn:
            conn.execute(sql_text("SELECT 1"))
            
        health_status["database"] = "connected"
        
    except Exception as e:
        health_status["database"] = "disconnected"
        logger.warning("Database check failed. Type: %s, Error: %s", type(e).__name__, e)

    return health_status

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
