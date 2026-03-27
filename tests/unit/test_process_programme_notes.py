from app.services.process_programme import _normalize_completeness_notes


def test_process_normalize_completeness_notes_parses_missing_fields_and_booleans():
    notes = _normalize_completeness_notes(
        {
            "missing_fields": '["start_date", "finish_date"]',
            "notes": None,
            "ai_quota_exhausted": "yes",
            "classification_ai_suppressed": "0",
            "work_profile_ai_suppressed": "false",
            "unclassified_mapping_count": "7",
        }
    )

    assert notes["missing_fields"] == ["start_date", "finish_date"]
    assert notes["notes"] == ""
    assert notes["ai_quota_exhausted"] is True
    assert notes["classification_ai_suppressed"] is False
    assert notes["work_profile_ai_suppressed"] is False
    assert notes["unclassified_mapping_count"] == 7
