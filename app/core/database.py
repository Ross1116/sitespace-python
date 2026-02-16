from sqlalchemy import create_engine
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
from sqlalchemy.exc import OperationalError
from fastapi import HTTPException, status
from .config import settings
import logging
import os

logger = logging.getLogger(__name__)


def _get_db_connect_timeout(default: int = 10) -> int:
    raw = os.getenv("DB_CONNECT_TIMEOUT", str(default))
    try:
        return int(raw)
    except ValueError:
        logger.warning(
            "Invalid DB_CONNECT_TIMEOUT value '%s'; using default timeout=%s",
            raw,
            default,
        )
        return default

# Create SQLAlchemy engine without a SQLite fallback
def create_database_engine():
    """
    Creates the database engine based on the URL in settings. 
    It is configured for PostgreSQL and will raise an error if connection fails.
    """
    
    # Log the URL being used
    logger.info(f"Database URL detected: {settings.database_url}")
    
    # We remove the outer try/except block to ensure failure if the database is inaccessible, 
    # preventing silent fallback to an un-migrated SQLite file.
    if settings.database_url.startswith("postgresql"):
        connect_timeout = _get_db_connect_timeout()
        # PostgreSQL connection with connection pooling
        engine = create_engine(
            settings.database_url,
            pool_pre_ping=True,
            pool_recycle=1800,
            pool_timeout=30,
            pool_size=20,
            max_overflow=40,
            connect_args={
                "connect_timeout": connect_timeout,
                "application_name": "sitespace-api"
            },
            echo=False  # Set to True for SQL debugging
        )
        logger.info("✅ PostgreSQL engine configured.")
    else:
        # If the URL is not for PostgreSQL, we still use create_engine on it.
        engine = create_engine(settings.database_url, echo=False)
        logger.warning("⚠️ Using non-PostgreSQL engine based on URL.")
            
    # Test the connection. This must succeed for the application to start.
    try:
        with engine.connect() as connection:
            logger.info("✅ Database connection test successful.")
    except Exception as conn_error:
        logger.warning("❌ Database connectivity check failed at startup: %s", conn_error)
        logger.warning("Application startup will continue. DB-backed endpoints may return 503 until connectivity is restored.")

    return engine

# Create the engine
engine = create_database_engine()

# Create SessionLocal class
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

# Create Base class
Base = declarative_base()

# Dependency to get database session
def get_db():
    db = SessionLocal()
    try:
        yield db
    except OperationalError as db_error:
        logger.exception("Database operational error during request: %s", db_error)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Database temporarily unavailable. Please retry shortly."
        ) from db_error
    finally:
        db.close()
