import runpy
from pathlib import Path

from app.core import constants as constants_module
from app.core.config import settings
from app.core.constants import (
    ALLOWED_ASSET_TYPES,
    ANOMALY_ACTIVITY_DELTA_THRESHOLD,
    ANOMALY_DEMAND_SPIKE_THRESHOLD,
    ANOMALY_MAPPING_CHANGE_THRESHOLD,
    AI_CLASSIFICATION_BATCH_MAX_TOKENS,
    AI_CLASSIFICATION_BATCH_SIZE,
    AI_CLASSIFICATION_MAX_CONCURRENT_BATCHES,
    AI_CLASSIFICATION_PARALLEL_THRESHOLD,
    AI_STANDALONE_TIMEOUT_BUFFER_SECONDS,
    AI_STRUCTURE_DETECTION_MAX_TOKENS,
    AI_STRUCTURE_DETECTION_SAMPLE_SIZE,
    AI_WORK_PROFILE_MAX_CONCURRENT,
    AI_WORK_PROFILE_MAX_TOKENS,
    ASSET_PAGE_DEFAULT,
    ASSET_PAGE_MAX,
    BOOKING_AUDIT_PAGE_DEFAULT,
    BOOKING_AUDIT_PAGE_MAX,
    BOOKING_CALENDAR_MAX_DAYS,
    BOOKING_MIN_SLOT_DURATION_MINUTES,
    BOOKING_PAGE_DEFAULT,
    BOOKING_PAGE_MAX,
    CLASSIFICATION_CONFIRMED_MIN_CONFIRMATIONS,
    CLASSIFICATION_HISTORY_PAGE_DEFAULT,
    CLASSIFICATION_HISTORY_PAGE_MAX,
    CLASSIFICATION_STABLE_MIN_CONFIRMATIONS,
    DEFAULT_FALLBACK_MAX_HOURS,
    DEFAULT_MAX_HOURS_PER_DAY,
    DEMAND_HOURS_PER_DAY,
    DEMAND_LEVEL_HIGH_MAX,
    DEMAND_LEVEL_LOW_MAX,
    DEMAND_LEVEL_MEDIUM_MAX,
    FILE_CACHE_MAX_AGE_SECONDS,
    ITEM_PAGE_DEFAULT,
    ITEM_PAGE_MAX,
    MAX_FILE_UPLOAD_SIZE_BYTES,
    PDF_IMAGE_SCALE,
    PDF_PREVIEW_SCALE,
    PROJECT_PAGE_DEFAULT,
    PROJECT_PAGE_MAX,
    SUBCONTRACTOR_PAGE_DEFAULT,
    SUBCONTRACTOR_PAGE_MAX,
    UPCOMING_BOOKINGS_DEFAULT_DAYS_AHEAD,
    UPCOMING_BOOKINGS_MAX_DAYS_AHEAD,
    UPCOMING_BOOKINGS_PAGE_DEFAULT,
    UPCOMING_BOOKINGS_PAGE_MAX,
    WORK_PROFILE_ACTUAL_ERROR_FRACTION,
    WORK_PROFILE_AI_ERROR_FRACTION,
    WORK_PROFILE_CONTEXT_VERSION,
    WORK_PROFILE_CORRECTION_MIN_SAMPLES,
    WORK_PROFILE_CORRECTION_RATE_THRESHOLD,
    WORK_PROFILE_CV_CONFIRMED,
    WORK_PROFILE_CV_TRUSTED,
    WORK_PROFILE_INFERENCE_VERSION,
    WORK_PROFILE_MAX_NEW_CONTEXTS_PER_UPLOAD,
    WORK_PROFILE_MIN_HOURS,
    WORK_PROFILE_NORM_DIST_SUM_TOLERANCE,
    WORK_PROFILE_OPERATIONAL_UNIT,
    get_active_asset_types,
    get_max_hours_for_type,
)


def _load_config_snapshot() -> dict:
    config_path = Path(__file__).resolve().parents[2] / "app" / "core" / "config.py"
    return runpy.run_path(str(config_path))


class TestConstantsSmoke:
    def test_flat_constant_imports_remain_available(self):
        assert "none" in ALLOWED_ASSET_TYPES
        assert DEFAULT_MAX_HOURS_PER_DAY["crane"] == 10.0
        assert DEFAULT_MAX_HOURS_PER_DAY["loading_bay"] == 12.0
        assert DEFAULT_FALLBACK_MAX_HOURS == 10.0
        assert DEMAND_HOURS_PER_DAY == 8
        assert DEMAND_LEVEL_LOW_MAX == 16
        assert DEMAND_LEVEL_MEDIUM_MAX == 32
        assert DEMAND_LEVEL_HIGH_MAX == 64
        assert ANOMALY_DEMAND_SPIKE_THRESHOLD == 1.5
        assert ANOMALY_MAPPING_CHANGE_THRESHOLD == 0.5
        assert ANOMALY_ACTIVITY_DELTA_THRESHOLD == 0.3
        assert CLASSIFICATION_CONFIRMED_MIN_CONFIRMATIONS == 2
        assert CLASSIFICATION_STABLE_MIN_CONFIRMATIONS == 5
        assert AI_STRUCTURE_DETECTION_SAMPLE_SIZE == 50
        assert AI_STRUCTURE_DETECTION_MAX_TOKENS == 2048
        assert AI_CLASSIFICATION_BATCH_SIZE == 40
        assert AI_CLASSIFICATION_BATCH_MAX_TOKENS == 6144
        assert AI_CLASSIFICATION_PARALLEL_THRESHOLD == 80
        assert AI_CLASSIFICATION_MAX_CONCURRENT_BATCHES == 3
        assert AI_STANDALONE_TIMEOUT_BUFFER_SECONDS == 3
        assert WORK_PROFILE_CONTEXT_VERSION == 1
        assert WORK_PROFILE_INFERENCE_VERSION == 2
        assert WORK_PROFILE_MAX_NEW_CONTEXTS_PER_UPLOAD == 150
        assert AI_WORK_PROFILE_MAX_CONCURRENT == 3
        assert AI_WORK_PROFILE_MAX_TOKENS == 768
        assert WORK_PROFILE_OPERATIONAL_UNIT == 0.5
        assert WORK_PROFILE_MIN_HOURS == 0.5
        assert WORK_PROFILE_CV_CONFIRMED == 0.20
        assert WORK_PROFILE_CV_TRUSTED == 0.10
        assert WORK_PROFILE_CORRECTION_RATE_THRESHOLD == 0.20
        assert WORK_PROFILE_CORRECTION_MIN_SAMPLES == 3
        assert WORK_PROFILE_AI_ERROR_FRACTION == 0.20
        assert WORK_PROFILE_ACTUAL_ERROR_FRACTION == 0.05
        assert WORK_PROFILE_NORM_DIST_SUM_TOLERANCE == 1e-6
        assert BOOKING_MIN_SLOT_DURATION_MINUTES == 30
        assert BOOKING_CALENDAR_MAX_DAYS == 90
        assert UPCOMING_BOOKINGS_DEFAULT_DAYS_AHEAD == 7
        assert UPCOMING_BOOKINGS_MAX_DAYS_AHEAD == 90
        assert MAX_FILE_UPLOAD_SIZE_BYTES == 20 * 1024 * 1024
        assert FILE_CACHE_MAX_AGE_SECONDS == 3600
        assert PDF_PREVIEW_SCALE == 1.5
        assert PDF_IMAGE_SCALE == 3.0
        assert ASSET_PAGE_DEFAULT == 100
        assert ASSET_PAGE_MAX == 100
        assert BOOKING_PAGE_DEFAULT == 100
        assert BOOKING_PAGE_MAX == 1000
        assert BOOKING_AUDIT_PAGE_DEFAULT == 50
        assert BOOKING_AUDIT_PAGE_MAX == 200
        assert ITEM_PAGE_DEFAULT == 50
        assert ITEM_PAGE_MAX == 200
        assert CLASSIFICATION_HISTORY_PAGE_DEFAULT == 50
        assert CLASSIFICATION_HISTORY_PAGE_MAX == 200
        assert PROJECT_PAGE_DEFAULT == 100
        assert PROJECT_PAGE_MAX == 1000
        assert SUBCONTRACTOR_PAGE_DEFAULT == 100
        assert SUBCONTRACTOR_PAGE_MAX == 1000
        assert UPCOMING_BOOKINGS_PAGE_DEFAULT == 10
        assert UPCOMING_BOOKINGS_PAGE_MAX == 100
        assert callable(get_active_asset_types)
        assert callable(get_max_hours_for_type)

    def test_constants_module_layout_stays_flat(self):
        assert hasattr(constants_module, "AI_CLASSIFICATION_BATCH_SIZE")
        assert hasattr(constants_module, "DEMAND_LEVEL_HIGH_MAX")
        assert hasattr(constants_module, "WORK_PROFILE_MAX_NEW_CONTEXTS_PER_UPLOAD")
        assert hasattr(constants_module, "UPCOMING_BOOKINGS_PAGE_DEFAULT")


class TestConfigDefaults:
    def test_settings_exposes_new_ai_and_scheduler_fields(self):
        assert hasattr(settings, "AI_PROVIDER")
        assert hasattr(settings, "AI_MODEL")
        assert hasattr(settings, "AI_TIMEOUT_STRUCTURE")
        assert hasattr(settings, "AI_TIMEOUT_CLASSIFY")
        assert hasattr(settings, "AI_TIMEOUT_WORK_PROFILE")
        assert hasattr(settings, "AI_UPLOAD_COST_BUDGET_USD")
        assert hasattr(settings, "AI_INPUT_COST_PER_MILLION_USD")
        assert hasattr(settings, "AI_OUTPUT_COST_PER_MILLION_USD")
        assert hasattr(settings, "NIGHTLY_LOOKAHEAD_HOUR")
        assert hasattr(settings, "NIGHTLY_LOOKAHEAD_MINUTE")
        assert hasattr(settings, "NIGHTLY_LOOKAHEAD_TIMEZONE")
        assert settings.AI_ENABLED is False  # unit tests disable live AI calls

    def test_config_module_defaults_are_env_backed(self, monkeypatch):
        monkeypatch.setenv("DEBUG", "true")
        monkeypatch.setenv("JWT_SECRET", "unit-test-secret-not-for-production")
        monkeypatch.setenv("SECRET_KEY", "unit-test-secret-not-for-production")
        monkeypatch.setenv("AI_ENABLED", "true")
        monkeypatch.delenv("AI_TIMEOUT_STRUCTURE", raising=False)
        monkeypatch.delenv("AI_TIMEOUT_CLASSIFY", raising=False)
        monkeypatch.delenv("AI_TIMEOUT_WORK_PROFILE", raising=False)
        monkeypatch.delenv("AI_UPLOAD_COST_BUDGET_USD", raising=False)
        monkeypatch.delenv("AI_INPUT_COST_PER_MILLION_USD", raising=False)
        monkeypatch.delenv("AI_OUTPUT_COST_PER_MILLION_USD", raising=False)
        monkeypatch.delenv("NIGHTLY_LOOKAHEAD_HOUR", raising=False)
        monkeypatch.delenv("NIGHTLY_LOOKAHEAD_MINUTE", raising=False)
        monkeypatch.delenv("NIGHTLY_LOOKAHEAD_TIMEZONE", raising=False)

        snapshot = _load_config_snapshot()
        loaded_settings = snapshot["settings"]

        assert loaded_settings.AI_TIMEOUT_STRUCTURE == 20
        assert loaded_settings.AI_TIMEOUT_CLASSIFY == 30
        assert loaded_settings.AI_TIMEOUT_WORK_PROFILE == 25
        assert loaded_settings.AI_UPLOAD_COST_BUDGET_USD == 5.0
        assert loaded_settings.AI_INPUT_COST_PER_MILLION_USD is None
        assert loaded_settings.AI_OUTPUT_COST_PER_MILLION_USD is None
        assert loaded_settings.NIGHTLY_LOOKAHEAD_HOUR == 18
        assert loaded_settings.NIGHTLY_LOOKAHEAD_MINUTE == 30
        assert loaded_settings.NIGHTLY_LOOKAHEAD_TIMEZONE == "Australia/Adelaide"
