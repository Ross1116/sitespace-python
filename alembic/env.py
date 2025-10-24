from dotenv import load_dotenv
load_dotenv()

from logging.config import fileConfig

from sqlalchemy import engine_from_config
from sqlalchemy import pool
from app.core.database import Base

from alembic import context

import os
import sys
from pathlib import Path

# Fix: Ensure python paths are correct if running from project root
sys.path.append(str(Path(__file__).resolve().parents[1]))

# --- START: FIX FOR EMPTY MIGRATION FILE & DEPENDENCY ERRORS ---
# Import ALL your models here so Base.metadata is fully populated
# and SQLAlchemy can resolve foreign key dependencies.
try:
    import app.models.user
    import app.models.asset
    import app.models.file_upload
    import app.models.project
    import app.models.site_project
    import app.models.slot_booking
    import app.models.subcontractor
    
    print("Alembic: Successfully imported all models.")
except ImportError as e:
    print(f"Alembic Import Error: Could not import all models. Check your paths. Error: {e}")
# --- END: FIX FOR EMPTY MIGRATION FILE & DEPENDENCY ERRORS ---


# this is the Alembic Config object, which provides
# access to the values within the .ini file in use.
config = context.config

# Interpret the config file for Python logging.
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# add your model's MetaData object here
target_metadata = Base.metadata

# Helper to get URL, prioritizing the OS environment variable
def get_db_url():
    """Fetches the DB URL, prioritizing the OS environment variable."""
    db_url = os.environ.get("DATABASE_URL")
    
    # We must explicitly use the "postgres" database for connection stability
    # before Alembic attempts to check the 'sitespace' schema.
    if db_url and db_url != "${DATABASE_URL}":
        print("Alembic: Using DATABASE_URL from OS Environment.")
        return db_url
        
    config_url = config.get_main_option("sqlalchemy.url")
    if config_url and config_url != "${DATABASE_URL}":
        print("Alembic: Using URL from alembic.ini config.")
        return config_url
        
    raise Exception("DATABASE_URL is not set or improperly parsed.")

def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode."""
    url = get_db_url()
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode."""
    # Get the config section dictionary
    alembic_config = config.get_section(config.config_ini_section, {})
    
    # CRITICAL FIX STEP 2: Override the URL in the configuration dictionary
    alembic_config['sqlalchemy.url'] = get_db_url()

    connectable = engine_from_config(
        alembic_config,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection, target_metadata=target_metadata
        )

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
