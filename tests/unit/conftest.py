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

# Eagerly import every ORM model so that SQLAlchemy's mapper registry is fully
# populated before any test triggers mapper configuration.  Without this,
# models that reference each other via string-name relationships (e.g.
# SiteProject → "SitePlan") fail with InvalidRequestError when the referenced
# class hasn't been imported yet in the unit-test process.
import app.models.site_plan  # noqa: F401, E402
import app.models.stored_file  # noqa: F401, E402
import app.models.asset  # noqa: F401, E402
import app.models.lookahead  # noqa: F401, E402
import app.models.programme  # noqa: F401, E402
import app.models.user  # noqa: F401, E402
import app.models.site_project  # noqa: F401, E402
import app.models.subcontractor  # noqa: F401, E402
import app.models.slot_booking  # noqa: F401, E402
import app.models.booking_audit  # noqa: F401, E402
import app.models.file_upload  # noqa: F401, E402
import app.models.item_identity  # noqa: F401, E402
import app.models.asset_type  # noqa: F401, E402
