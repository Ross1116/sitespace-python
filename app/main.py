import os
import logging
import sentry_sdk

sentry_sdk.init(
    dsn=os.getenv("SENTRY_DSN"),
    send_default_pii=True,
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
from .core.middleware import RequestLoggingMiddleware
from .api.v1 import auth, assets, file_upload, slot_booking, site_project, subcontractor, users, booking_audit

# Import all models so SQLAlchemy knows about them
from .models import user, asset, slot_booking as slot_booking_model, site_project as site_project_model, subcontractor as subcontractor_model, file_upload as file_upload_model

# Create database tables (optional for testing) - Non-blocking
def create_tables_if_possible():
    try:
        Base.metadata.create_all(bind=engine)
        print("✅ Database tables created successfully")
        return True
    except Exception as e:
        print(f"⚠️  Database connection failed: {e}")
        print("📝 Application will run without database for testing")
        return False

# Try to create tables but don't block startup
try:
    create_tables_if_possible()
except Exception as e:
    print(f"⚠️  Table creation skipped: {e}")

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    print("Starting Sitespace FastAPI application...")
    yield
    # Shutdown
    print("Shutting down Sitespace FastAPI application...")

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
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Request logging + Sentry user context
app.add_middleware(RequestLoggingMiddleware)

# Global exception handler for HTTPException
@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    return JSONResponse(
        status_code=exc.status_code,
        content={
            "success": False,
            "message": exc.detail,
            "detail": exc.detail
        }
    )

# Global exception handler for all other exceptions
logger = logging.getLogger("sitespace.errors")

@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    logger.exception("Unhandled exception on %s %s", request.method, request.url.path)
    sentry_sdk.capture_exception(exc)

    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={
            "success": False,
            "message": "Internal server error",
            "detail": str(exc) if settings.debug else "An unexpected error occurred"
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
        health_status["db_error"] = str(e)
        print(f"⚠️ Database Check Failed. Type: {type(e).__name__}, Error: {e}")

    return health_status

if __name__ == "__main__":
    uvicorn.run(
        "app.main:app",
        host=settings.host,
        port=settings.port,
        reload=settings.debug
    )