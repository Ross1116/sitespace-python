from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock
from uuid import uuid4

import pytest
from fastapi import HTTPException

from app.api.v1 import programmes as programmes_api
from app.schemas.programme import MappingCorrectionRequest
from app.services import correction_service
from app.services.correction_service import MappingCorrectionContext


def test_mapping_correction_request_accepts_existing_asset_only_payload():
    payload = MappingCorrectionRequest(asset_type=" Crane ")

    assert payload.asset_type == "crane"
    assert payload.manual_total_hours is None
    assert payload.manual_normalized_distribution is None


def test_mapping_correction_request_requires_paired_manual_fields():
    with pytest.raises(ValueError, match="must be supplied together"):
        MappingCorrectionRequest(manual_total_hours=8.0)


def test_mapping_correction_request_requires_zero_hours_distribution_to_be_all_zero():
    with pytest.raises(ValueError, match="must be all zeros when manual_total_hours is zero"):
        MappingCorrectionRequest(
            manual_total_hours=0.0,
            manual_normalized_distribution=[1.0, 0.0],
        )


def test_apply_mapping_correction_propagates_manual_truth(monkeypatch):
    mapping_id = uuid4()
    activity_id = uuid4()
    upload_id = uuid4()
    item_id = uuid4()
    user_id = uuid4()
    context_profile_id = uuid4()
    activity_profile_id = uuid4()

    mapping = SimpleNamespace(
        id=mapping_id,
        programme_activity_id=activity_id,
        asset_type="crane",
        confidence="medium",
        source="ai",
        manually_corrected=False,
        corrected_by=None,
        corrected_at=None,
        auto_committed=True,
    )
    activity = SimpleNamespace(
        id=activity_id,
        programme_upload_id=upload_id,
        item_id=item_id,
        name="Install tower crane",
        level_name="Level 1",
        zone_name="Zone A",
        duration_days=2,
    )
    upload = SimpleNamespace(id=upload_id, project_id=uuid4())
    existing_profile = SimpleNamespace(
        id=uuid4(),
        item_id=item_id,
        total_hours=12.0,
        distribution_json=[6.0, 6.0],
        normalized_distribution_json=[0.5, 0.5],
    )
    suggestion_log = SimpleNamespace(
        id=uuid4(),
        accepted=True,
        correction=None,
        upload_id=upload_id,
        suggested_asset_type="crane",
    )
    context = MappingCorrectionContext(
        mapping=mapping,
        activity=activity,
        upload=upload,
        activity_profile=existing_profile,
        suggestion_log=suggestion_log,
        canonical_item=SimpleNamespace(id=item_id, identity_status="active"),
    )

    db = MagicMock()
    classification_calls = {}
    cache_calls = {}
    activity_profile_calls = {}

    monkeypatch.setattr(correction_service, "get_max_hours_for_type", lambda _db, asset_type: 8.0)

    def fake_apply_manual_classification(db, item_id_arg, asset_type_arg, performed_by_user_id_arg):
        classification_calls["args"] = (item_id_arg, asset_type_arg, performed_by_user_id_arg)
        return SimpleNamespace(id=uuid4(), item_id=item_id_arg, asset_type=asset_type_arg, source="manual")

    monkeypatch.setattr(correction_service, "apply_manual_classification", fake_apply_manual_classification)

    def fake_upsert_manual_context_profile(db, **kwargs):
        cache_calls.update(kwargs)
        return SimpleNamespace(id=context_profile_id)

    monkeypatch.setattr(correction_service, "upsert_manual_context_profile", fake_upsert_manual_context_profile)

    def fake_write_manual_activity_profile(db, **kwargs):
        activity_profile_calls.update(kwargs)
        return SimpleNamespace(id=activity_profile_id, source="manual", **kwargs)

    monkeypatch.setattr(correction_service, "write_manual_activity_profile", fake_write_manual_activity_profile)

    result = correction_service.apply_mapping_correction(
        db,
        context=context,
        corrected_by_user_id=user_id,
        asset_type="forklift",
        manual_total_hours=20.0,
        manual_normalized_distribution=[0.75, 0.25],
    )

    assert mapping.asset_type == "forklift"
    assert mapping.source == "manual"
    assert mapping.manually_corrected is True
    assert mapping.corrected_by == user_id
    assert isinstance(mapping.corrected_at, datetime)
    assert mapping.corrected_at.tzinfo == timezone.utc
    assert mapping.auto_committed is False

    assert suggestion_log.accepted is False
    assert suggestion_log.correction == "forklift"

    assert classification_calls["args"] == (item_id, "forklift", user_id)
    assert cache_calls["item_id"] == item_id
    assert cache_calls["asset_type"] == "forklift"
    assert cache_calls["total_hours"] == 16.0
    assert cache_calls["distribution"] == [8.0, 8.0]
    assert cache_calls["normalized_distribution"] == [0.75, 0.25]
    assert activity_profile_calls["context_profile_id"] == context_profile_id
    assert activity_profile_calls["total_hours"] == 16.0
    assert result.context_profile.id == context_profile_id
    assert result.activity_profile.id == activity_profile_id


def test_apply_mapping_correction_without_item_skips_memory_updates():
    mapping = SimpleNamespace(
        id=uuid4(),
        programme_activity_id=uuid4(),
        asset_type="crane",
        confidence="medium",
        source="ai",
        manually_corrected=False,
        corrected_by=None,
        corrected_at=None,
        auto_committed=True,
    )
    activity = SimpleNamespace(
        id=uuid4(),
        programme_upload_id=uuid4(),
        item_id=None,
        name="Install tower crane",
        level_name=None,
        zone_name=None,
        duration_days=3,
    )
    upload = SimpleNamespace(id=uuid4(), project_id=uuid4())
    db = MagicMock()
    context = MappingCorrectionContext(
        mapping=mapping,
        activity=activity,
        upload=upload,
        activity_profile=None,
        suggestion_log=None,
        canonical_item=None,
    )

    result = correction_service.apply_mapping_correction(
        db,
        context=context,
        corrected_by_user_id=uuid4(),
        asset_type="telehandler",
    )

    assert result.classification is None
    assert result.context_profile is None
    assert result.activity_profile is None
    added_log = result.suggestion_log
    assert added_log.correction == "telehandler"
    assert added_log.accepted is False
    db.add.assert_called_once()


def test_correct_activity_mapping_rolls_back_on_stage8_failure(monkeypatch):
    mapping_id = uuid4()
    current_user = SimpleNamespace(id=uuid4(), role="manager")
    db = MagicMock()
    context = SimpleNamespace(
        upload=SimpleNamespace(project_id=uuid4()),
    )

    monkeypatch.setattr(programmes_api, "load_mapping_correction_context", lambda db, mapping_id: context)
    monkeypatch.setattr(programmes_api, "_check_project_access", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        programmes_api,
        "apply_mapping_correction",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("boom")),
    )

    with pytest.raises(HTTPException) as exc_info:
        programmes_api.correct_activity_mapping(
            mapping_id=mapping_id,
            payload=MappingCorrectionRequest(asset_type="forklift"),
            db=db,
            current_user=current_user,
        )

    assert exc_info.value.status_code == 500
    db.rollback.assert_called_once()
    db.commit.assert_not_called()
