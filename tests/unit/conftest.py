"""
Unit test environment setup.

Sets required environment variables BEFORE any app module is imported so that:
  - app.core.config does not raise on missing JWT_SECRET/SECRET_KEY
  - app.core.database creates a SQLite engine instead of connecting to Postgres
  - AI calls are never made (AI_ENABLED=false)

Run unit tests with:
    pytest tests/unit/

Integration tests (tests/test_*.py) require a live server and are run separately
via `python tests/run_tests.py`.
"""

import os

os.environ["DEBUG"] = "true"
os.environ["DATABASE_URL"] = "sqlite:///./test_unit.db"
os.environ["JWT_SECRET"] = "unit-test-secret-not-for-production"
os.environ["SECRET_KEY"] = "unit-test-secret-not-for-production"
os.environ["AI_API_KEY"] = "test-key-unit-tests-never-call-ai"
os.environ["AI_ENABLED"] = "false"
