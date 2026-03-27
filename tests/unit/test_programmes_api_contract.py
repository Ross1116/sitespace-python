from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import uuid4

from app.api.v1.programmes import _normalize_completeness_notes, _serialize_mapping


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
