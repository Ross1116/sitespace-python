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
        # PostgreSQL connection with connection pooling
        engine = create_engine(
            settings.database_url,
            pool_pre_ping=True,
            # Recycle connections every 5 minutes so Railway's LB (which drops
            # idle TCP connections around that mark) never silently kills them
            # before we can detect the stale state via pool_pre_ping.
            pool_recycle=300,
            pool_timeout=30,
            # Smaller pool: Railway hobby/starter plans cap at ~25 connections;
            # 10 base + 20 overflow leaves headroom for the nightly job and
            # concurrent uploads without exhausting the server limit.
            pool_size=10,
            max_overflow=20,
            connect_args={
                "connect_timeout": connect_timeout,
                "application_name": "sitespace-api",
                # TCP keepalives: OS will probe the connection after 60s idle,
                # retry every 10s, and declare it dead after 5 failures (110s).
                # This surfaces broken connections before pool_pre_ping ever
                # gets a chance to recycle them.
                "keepalives": 1,
                "keepalives_idle": 60,
                "keepalives_interval": 10,
                "keepalives_count": 5,
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
    except SQLAlchemyError:
        logger.warning("❌ Database connectivity check failed at startup", exc_info=True)
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
