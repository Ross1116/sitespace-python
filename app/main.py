from pydoc import text
from fastapi import FastAPI, HTTPException, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from contextlib import asynccontextmanager
import uvicorn

from .core.config import settings
from .core.database import engine, Base
from .api.v1 import auth, assets, file_upload, slot_booking, site_project, subcontractor, forgot_password, users

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

# Add CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

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
@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    # Log the full error for debugging
    import traceback
    print(f"❌ Unhandled exception: {exc}")
    print(traceback.format_exc())
    
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={
            "success": False,
            "message": "Internal server error",
            "detail": str(exc)
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
app.include_router(forgot_password.router, prefix="/api")

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
        # Basic health check - just return success if app is running
        health_status = {
            "status": "healthy",
            "message": "Sitespace API is healthy",
            "version": "1.0.0"
        }
        
        # Optional: Test database connection (non-blocking)
        try:
            from .core.database import engine
            with engine.connect() as conn:
                conn.execute(text("SELECT 1"))
            health_status["database"] = "connected"
        except Exception as db_error:
            # Don't fail health check if DB is down
            health_status["database"] = "disconnected"
            health_status["db_error"] = str(db_error)
            print(f"⚠️  Database health check failed: {db_error}")
        
        return health_status
        
    except Exception as e:
        # Even if something goes wrong, return a basic response
        print(f"❌ Health check error: {e}")
        return {
            "status": "degraded",
            "message": "API is running but with issues",
            "error": str(e)
        }

if __name__ == "__main__":
    uvicorn.run(
        "app.main:app",
        host=settings.host,
        port=settings.port,
        reload=settings.debug
    )