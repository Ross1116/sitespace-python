from sqlalchemy import create_engine
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
from .config import settings
import logging

logger = logging.getLogger(__name__)

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
        # PostgreSQL connection with connection pooling
        engine = create_engine(
            settings.database_url,
            pool_pre_ping=True,
            pool_recycle=300,
            pool_timeout=30,
            pool_size=5,
            max_overflow=10,
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
        # This block catches any failure (Auth, Host, Driver)
        logger.error(f"❌ FATAL: Database connection failed: {conn_error}")
        # Re-raise the exception to prevent the application from starting
        raise RuntimeError("Database connection failed during startup. Check credentials, host, and port.") from conn_error 

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
    finally:
        db.close()
