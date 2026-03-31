from sqlalchemy import create_engine
from sqlalchemy.orm import declarative_base
from sqlalchemy.orm import sessionmaker
from sqlalchemy.exc import OperationalError, SQLAlchemyError
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
    
    # Log the URL with credentials masked
    try:
        from urllib.parse import urlparse, urlunparse
        p = urlparse(settings.database_url)
        safe_url = urlunparse(p._replace(netloc=f"{p.username}:***@{p.hostname}:{p.port}"))
    except Exception:
        safe_url = "<unparseable url>"
    logger.info("Database URL detected: %s", safe_url)
    
    # We remove the outer try/except block to ensure failure if the database is inaccessible, 
    # preventing silent fallback to an un-migrated SQLite file.
    if settings.database_url.startswith("postgresql"):
        connect_timeout = _get_db_connect_timeout()

        # Pool sizing per service role.
        # Railway hobby Postgres allows ~25 connections total across all services.
        # Split budget: web=5+10, worker=3+5, nightly=2+3
        role = settings.SERVICE_ROLE
        if role == "worker":
            pool_size, max_overflow = 3, 5
            app_name = "sitespace-worker"
        elif role == "nightly":
            pool_size, max_overflow = 2, 3
            app_name = "sitespace-nightly"
        else:
            pool_size, max_overflow = 5, 10
            app_name = "sitespace-api"

        engine = create_engine(
            settings.database_url,
            pool_pre_ping=True,
            # Recycle connections every 5 minutes so Railway's LB (which drops
            # idle TCP connections around that mark) never silently kills them
            # before we can detect the stale state via pool_pre_ping.
            pool_recycle=300,
            pool_timeout=30,
            pool_size=pool_size,
            max_overflow=max_overflow,
            connect_args={
                "connect_timeout": connect_timeout,
                "application_name": app_name,
                # TCP keepalives: OS will probe the connection after 60s idle,
                # retry every 10s, and declare it dead after 5 failures (110s).
                "keepalives": 1,
                "keepalives_idle": 60,
                "keepalives_interval": 10,
                "keepalives_count": 5,
            },
            echo=False  # Set to True for SQL debugging
        )
        logger.info("PostgreSQL engine configured (role=%s, pool=%d+%d).", role, pool_size, max_overflow)
    else:
        # If the URL is not for PostgreSQL, we still use create_engine on it.
        engine = create_engine(settings.database_url, echo=False)
        logger.warning("⚠️ Using non-PostgreSQL engine based on URL.")
            
    # Test the connection. This must succeed for the application to start.
    try:
        assert_database_connection(engine)
        logger.info("✅ Database connection test successful.")
    except SQLAlchemyError:
        logger.exception("❌ Database connectivity check failed at startup")
        raise

    return engine


def assert_database_connection(target_engine=None) -> None:
    """
    Raise on connectivity failure so startup and health checks fail closed.
    """
    engine_to_check = target_engine or engine
    with engine_to_check.connect() as connection:
        connection.exec_driver_sql("SELECT 1")

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
        db.rollback()
        # Log at WARNING, not ERROR — the 503 HTTPException is already captured
        # by Sentry's starlette middleware, so logger.exception would double-report.
        logger.warning("Database operational error during request: %s", db_error)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Database temporarily unavailable. Please retry shortly."
        ) from db_error
    except Exception:
        # Ensure partial state doesn't leak on any other error.
        db.rollback()
        raise
    finally:
        try:
            db.close()
        except OperationalError:
            # Connection already dead (e.g. SSL EOF after a successful commit).
            # SQLAlchemy invalidates and discards the connection automatically —
            # swallow the error so it doesn't surface as a false Sentry alert.
            pass
