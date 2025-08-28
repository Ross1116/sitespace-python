from sqlalchemy import create_engine
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
from .config import settings
import logging

logger = logging.getLogger(__name__)

# Create SQLAlchemy engine with better error handling
try:
    if settings.database_url.startswith("postgresql"):
        # PostgreSQL connection with connection pooling
        engine = create_engine(
            settings.database_url,
            pool_pre_ping=True,
            pool_recycle=300,
            echo=False  # Set to True for SQL debugging
        )
        logger.info("✅ PostgreSQL engine created successfully")
    else:
        # SQLite fallback
        engine = create_engine(settings.database_url, echo=False)
        logger.info("✅ SQLite engine created successfully")
        
    # Test the connection
    with engine.connect() as connection:
        logger.info("✅ Database connection test successful")
        
except Exception as e:
    logger.error(f"❌ Database engine creation failed: {e}")
    # Create a fallback SQLite engine
    engine = create_engine("sqlite:///./fallback.db", echo=False)
    logger.warning("🔄 Using fallback SQLite database")

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
