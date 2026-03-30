from datetime import date, datetime, time, timezone
from types import SimpleNamespace
from uuid import uuid4

from app.api.v1.programmes import (
    _build_suggested_booking_dates,
    _normalize_completeness_notes,
    _serialize_programme_upload,
    _serialize_mapping,
)


def test_normalize_completeness_notes_includes_stable_defaults():
    notes = _normalize_completeness_notes({"notes": "degraded"})

    assert notes["notes"] == "degraded"
    assert notes["ai_quota_exhausted"] is False
    assert notes["classification_ai_suppressed"] is False
    assert notes["work_profile_ai_suppressed"] is False
    assert notes["unclassified_mapping_count"] == 0
    assert notes["non_planning_ready_asset_count"] == 0
    assert notes["excluded_booking_count"] == 0


def test_normalize_completeness_notes_parses_strings_safely():
    notes = _normalize_completeness_notes(
        {
            "missing_fields": "start_date, end_date",
            "notes": None,
            "ai_quota_exhausted": "false",
            "classification_ai_suppressed": "1",
            "work_profile_ai_suppressed": "no",
        }
    )

    assert notes["missing_fields"] == ["start_date", "end_date"]
    assert notes["notes"] == ""
    assert notes["ai_quota_exhausted"] is False
    assert notes["classification_ai_suppressed"] is True
    assert notes["work_profile_ai_suppressed"] is False


def test_serialize_mapping_includes_item_id():
    mapping = SimpleNamespace(
        id=uuid4(),
        programme_activity_id=uuid4(),
        asset_type="crane",
        confidence="medium",
        source="keyword",
        auto_committed=False,
        manually_corrected=False,
        corrected_by=None,
        corrected_at=None,
        subcontractor_id=None,
        created_at=datetime.now(timezone.utc),
    )
    item_id = uuid4()

    response = _serialize_mapping(mapping, "Install precast wall panels", item_id)

    assert response.item_id == item_id
    assert response.activity_name == "Install precast wall panels"
    assert response.asset_type == "crane"


def test_build_suggested_booking_dates_includes_daily_gap_and_ignores_cancelled_bookings():
    week_start = date(2026, 3, 30)
    linked_bookings = [
        SimpleNamespace(
            booking_date=date(2026, 4, 1),
            start_time=time(8, 0),
            end_time=time(12, 0),
            status="confirmed",
        ),
        SimpleNamespace(
            booking_date=date(2026, 4, 1),
            start_time=time(13, 0),
            end_time=time(15, 0),
            status="cancelled",
        ),
    ]

    suggestions = _build_suggested_booking_dates(
        effective_week_start=week_start,
        distribution_result={
            "work_dates": [
                date(2026, 4, 1),
                date(2026, 4, 2),
            ],
            "distribution": [8.0, 2.5],
        },
        linked_bookings=linked_bookings,
        default_start_time="08:00",
        default_end_time="16:00",
    )

    assert [entry.date for entry in suggestions] == ["2026-04-01", "2026-04-02"]
    assert suggestions[0].demand_hours == 8.0
    assert suggestions[0].booked_hours == 4.0
    assert suggestions[0].gap_hours == 4.0
    assert suggestions[0].hours == 4.0
    assert suggestions[1].demand_hours == 2.5
    assert suggestions[1].booked_hours == 0.0
    assert suggestions[1].gap_hours == 2.5


def test_serialize_programme_upload_normalizes_legacy_status_and_flags():
    upload_id = uuid4()
    upload = SimpleNamespace(
        id=upload_id,
        status="degraded",
        processing_outcome="completed_with_warnings",
        completeness_score=0.75,
        completeness_notes={"notes": "warning"},
        version_number=3,
        file_name="programme.pdf",
        ai_tokens_used=123,
        ai_cost_usd=4.56,
        created_at=datetime(2026, 3, 30, tzinfo=timezone.utc),
    )

    payload = _serialize_programme_upload(upload, active_upload_id=upload_id, include_notes=True)

    assert payload["status"] == "completed_with_warnings"
    assert payload["processing_outcome"] == "completed_with_warnings"
    assert payload["is_active_version"] is True
    assert payload["is_terminal_success"] is True
    assert payload["has_warnings"] is True
    assert payload["completeness_notes"]["notes"] == "warning"
