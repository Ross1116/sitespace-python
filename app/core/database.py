from sqlalchemy import create_engine
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
from .config import settings
import logging

logger = logging.getLogger(__name__)

# Create SQLAlchemy engine with better error handling
def create_database_engine():
    try:
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
            logger.info("✅ PostgreSQL engine created successfully")
        else:
            # SQLite fallback
            engine = create_engine(settings.database_url, echo=False)
            logger.info("✅ SQLite engine created successfully")
            
        # Test the connection (but don't block startup if it fails)
        try:
            with engine.connect() as connection:
                logger.info("✅ Database connection test successful")
        except Exception as conn_error:
            logger.warning(f"⚠️  Database connection test failed: {conn_error}")
            # Don't fail here, let the app start anyway
            
        return engine
        
    except Exception as e:
        logger.error(f"❌ Database engine creation failed: {e}")
        # Create a fallback SQLite engine
        fallback_engine = create_engine("sqlite:///./fallback.db", echo=False)
        logger.warning("🔄 Using fallback SQLite database")
        return fallback_engine

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
