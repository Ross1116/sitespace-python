from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock
from uuid import uuid4

import pytest
from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy.exc import IntegrityError
from sqlalchemy.exc import SQLAlchemyError

from app.api.v1 import items as items_api
from app.api.v1 import system as system_api
from app.schemas.item_identity import ItemRequirementEvaluationRequest
from app.services import item_requirements_service
from app.services.item_requirements_service import (
    _attribute_matches,
    replace_item_requirement_set,
    validate_requirement_rules,
)
from app.services.work_profile_service import (
    _copy_context_profile_payload,
    _overwrite_cache_entry,
    active_context_profile_query,
    invalidate_context_profile,
)


def test_item_requirement_evaluation_request_requires_explicit_scope():
    with pytest.raises(ValidationError):
        ItemRequirementEvaluationRequest()

    request_a = ItemRequirementEvaluationRequest(project_id=uuid4())
    request_b = ItemRequirementEvaluationRequest(project_id=uuid4())
    request_a.asset_ids.append(uuid4())

    assert request_b.asset_ids == []


def test_validate_requirement_rules_rejects_malformed_shapes():
    with pytest.raises(ValueError, match="allowed_asset_types"):
        validate_requirement_rules({"allowed_asset_types": "crane"})

    with pytest.raises(ValueError, match="required_attributes"):
        validate_requirement_rules({"required_attributes": ["internal"]})

    with pytest.raises(ValueError, match="default_parallel_units"):
        validate_requirement_rules({"default_parallel_units": "abc"})

    with pytest.raises(ValueError, match="min_parallel_units"):
        validate_requirement_rules({"min_parallel_units": True})

    with pytest.raises(ValueError, match="max_parallel_units"):
        validate_requirement_rules({"max_parallel_units": 1.5})

    with pytest.raises(ValueError, match="default_parallel_units"):
        validate_requirement_rules({"default_parallel_units": "2.5"})


def test_attribute_matches_supports_scalar_and_list_values_both_directions():
    assert _attribute_matches(["roof", "external"], "roof") is True
    assert _attribute_matches(["roof", "external"], "internal") is False
    assert _attribute_matches("roof", ["roof", "podium"]) is True
    assert _attribute_matches(["roof", "external"], ["internal", "external"]) is True


def test_invalidate_context_profile_is_idempotent():
    profile = SimpleNamespace(
        id=uuid4(),
        invalidated_at=None,
        invalidation_reason=None,
        superseded_by_profile_id=None,
    )

    replacement_id = uuid4()
    invalidate_context_profile(profile, reason="first_reason", superseded_by_profile_id=replacement_id)
    first_invalidated_at = profile.invalidated_at

    invalidate_context_profile(profile, reason="second_reason", superseded_by_profile_id=uuid4())

    assert profile.invalidated_at == first_invalidated_at
    assert profile.invalidation_reason == "first_reason"
    assert profile.superseded_by_profile_id == replacement_id


def test_overwrite_cache_entry_rejects_invalidated_profiles():
    profile = SimpleNamespace(id=uuid4(), invalidated_at=datetime.now(timezone.utc))

    with pytest.raises(ValueError, match="invalidated and immutable"):
        _overwrite_cache_entry(
            profile,
            total_hours=4.0,
            distribution=[4.0],
            normalized_distribution=[1.0],
            confidence=0.8,
            source="ai",
            low_confidence_flag=False,
        )


def test_active_context_profile_query_can_include_invalidated_rows():
    query = MagicMock()
    db = MagicMock()
    db.query.return_value = query

    assert active_context_profile_query(db, include_invalidated=True) is query
    query.filter.assert_not_called()


def test_get_item_requirements_404s_when_item_is_missing():
    db = MagicMock()
    db.get.return_value = None

    with pytest.raises(HTTPException) as exc_info:
        items_api.get_item_requirements(
            item_id=uuid4(),
            db=db,
            current_user=SimpleNamespace(id=uuid4(), role="manager"),
        )

    assert exc_info.value.status_code == 404


def test_replace_requirements_rolls_back_on_version_conflict(monkeypatch):
    item_id = uuid4()
    db = MagicMock()
    db.get.return_value = SimpleNamespace(id=item_id)

    def _raise_conflict(*_args, **_kwargs):
        raise IntegrityError("insert", {}, Exception("boom"))

    monkeypatch.setattr(items_api, "replace_item_requirement_set", _raise_conflict)

    with pytest.raises(HTTPException) as exc_info:
        items_api.replace_requirements(
            item_id=item_id,
            body=SimpleNamespace(rules_json={}, notes=None),
            db=db,
            current_user=SimpleNamespace(id=uuid4(), role="admin"),
        )

    assert exc_info.value.status_code == 409
    db.rollback.assert_called_once()
    db.commit.assert_not_called()


def test_system_health_short_circuits_when_database_is_unavailable(monkeypatch):
    def _raise_db_error(_engine):
        raise SQLAlchemyError("database down")

    monkeypatch.setattr(system_api, "assert_database_connection", _raise_db_error)
    monkeypatch.setattr(
        system_api,
        "build_system_health_payload",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("should not query ORM when DB is down")),
    )

    response = system_api.get_system_health(
        db=MagicMock(),
        current_user=SimpleNamespace(id=uuid4(), role="manager"),
    )

    assert response.database_connected is False
    assert response.state == "degraded"
    assert response.reason_codes == ["database_unavailable"]
    assert response.queue_backlog.queued == 0


def test_system_health_short_circuits_when_payload_build_fails(monkeypatch):
    monkeypatch.setattr(system_api, "assert_database_connection", lambda _engine: None)
    monkeypatch.setattr(
        system_api,
        "build_system_health_payload",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("payload failed")),
    )

    response = system_api.get_system_health(
        db=MagicMock(),
        current_user=SimpleNamespace(id=uuid4(), role="manager"),
    )

    assert response.database_connected is False
    assert response.reason_codes == ["database_unavailable"]


def test_system_health_short_circuits_when_response_mapping_fails(monkeypatch):
    monkeypatch.setattr(system_api, "assert_database_connection", lambda _engine: None)
    monkeypatch.setattr(
        system_api,
        "build_system_health_payload",
        lambda *_args, **_kwargs: {
            "state": "healthy",
            "reason_codes": [],
            "clean_upload_streak": 1,
            "last_transition_at": None,
            "last_trigger_upload_id": None,
            "queue_backlog": object(),
            "last_nightly_run": None,
            "last_feature_learning_run": None,
        },
    )

    response = system_api.get_system_health(
        db=MagicMock(),
        current_user=SimpleNamespace(id=uuid4(), role="manager"),
    )

    assert response.database_connected is False
    assert response.reason_codes == ["database_unavailable"]


def test_ai_readiness_returns_fallback_when_payload_build_fails(monkeypatch):
    monkeypatch.setattr(
        system_api,
        "build_ai_readiness_payload",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("readiness failed")),
    )

    response = system_api.get_ai_readiness(
        db=MagicMock(),
        current_user=SimpleNamespace(id=uuid4(), role="manager"),
    )

    assert response.ready_for_future_ml is False
    assert response.metrics == []
    assert "unavailable" in response.summary.lower()


def test_feature_effects_404_when_item_missing():
    db = MagicMock()
    db.get.return_value = None

    with pytest.raises(HTTPException) as exc_info:
        items_api.get_item_feature_effects(
            item_id=uuid4(),
            asset_type=None,
            duration_bucket=None,
            db=db,
            current_user=SimpleNamespace(id=uuid4(), role="manager"),
        )

    assert exc_info.value.status_code == 404


def test_expansion_signals_404_when_item_missing():
    db = MagicMock()
    db.get.return_value = None

    with pytest.raises(HTTPException) as exc_info:
        items_api.get_item_expansion_signals(
            item_id=uuid4(),
            asset_type=None,
            db=db,
            current_user=SimpleNamespace(id=uuid4(), role="manager"),
        )

    assert exc_info.value.status_code == 404


def test_promote_expansion_signal_rolls_back_on_unexpected_error(monkeypatch):
    db = MagicMock()

    def _raise_runtime(*_args, **_kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(items_api, "set_context_expansion_signal_promoted", _raise_runtime)

    with pytest.raises(HTTPException) as exc_info:
        items_api.promote_expansion_signal(
            signal_id=uuid4(),
            body=SimpleNamespace(promoted=True),
            db=db,
            current_user=SimpleNamespace(id=uuid4(), role="admin"),
        )

    assert exc_info.value.status_code == 500
    db.rollback.assert_called_once()


def test_evaluate_item_requirements_maps_value_error_to_400(monkeypatch):
    item_id = uuid4()
    db = MagicMock()
    db.get.return_value = SimpleNamespace(id=item_id)

    def _raise_value_error(*_args, **_kwargs):
        raise ValueError("bad scope")

    monkeypatch.setattr(items_api, "evaluate_assets_against_requirements", _raise_value_error)

    with pytest.raises(HTTPException) as exc_info:
        items_api.evaluate_item_requirements(
            item_id=item_id,
            body=SimpleNamespace(project_id=uuid4(), asset_ids=[]),
            db=db,
            current_user=SimpleNamespace(id=uuid4(), role="manager"),
        )

    assert exc_info.value.status_code == 400
    assert exc_info.value.detail == "bad scope"


def test_evaluate_assets_against_requirements_short_circuits_on_explicit_empty_asset_ids():
    db = MagicMock()

    with pytest.MonkeyPatch.context() as monkeypatch:
        monkeypatch.setattr(item_requirements_service, "get_active_item_requirement_set", lambda *_args, **_kwargs: None)
        payload = item_requirements_service.evaluate_assets_against_requirements(
            db,
            item_id=uuid4(),
            project_id=uuid4(),
            asset_ids=[],
        )

    assert payload["evaluations"] == []
    db.query.assert_not_called()


def test_evaluate_assets_against_requirements_requires_scope():
    db = MagicMock()

    with pytest.MonkeyPatch.context() as monkeypatch:
        monkeypatch.setattr(item_requirements_service, "get_active_item_requirement_set", lambda *_args, **_kwargs: None)
        with pytest.raises(ValueError, match="either asset_ids or project_id must be provided"):
            item_requirements_service.evaluate_assets_against_requirements(
                db,
                item_id=uuid4(),
                project_id=None,
                asset_ids=None,
            )

    db.query.assert_not_called()


def test_replace_item_requirement_set_does_not_deactivate_active_row_when_rules_invalid(monkeypatch):
    item_id = uuid4()
    active = SimpleNamespace(version=2, is_active=True)
    db = MagicMock()
    db.query.return_value.filter.return_value.with_for_update.return_value.first.return_value = (item_id,)
    monkeypatch.setattr(item_requirements_service, "get_active_item_requirement_set", lambda *_args, **_kwargs: active)

    with pytest.raises(ValueError, match="must be an integer"):
        replace_item_requirement_set(
            db,
            item_id=item_id,
            rules={"default_parallel_units": "2.5"},
            notes=None,
            created_by_user_id=uuid4(),
        )

    assert active.is_active is True
    db.add.assert_not_called()


def test_copy_context_profile_payload_does_not_preemptively_overwrite_actuals_median():
    target = SimpleNamespace(
        invalidated_at=None,
        asset_type="crane",
        duration_days=5,
        context_version=1,
        inference_version=1,
        total_hours=10.0,
        distribution_json=[2.0] * 5,
        normalized_distribution_json=[0.2] * 5,
        confidence=0.8,
        source="ai",
        low_confidence_flag=False,
        posterior_mean=10.0,
        posterior_precision=5.0,
        actuals_median=7.5,
    )
    source = SimpleNamespace(
        asset_type="hoist",
        duration_days=3,
        context_version=2,
        inference_version=3,
        total_hours=6.0,
        distribution_json=[2.0, 2.0, 2.0],
        normalized_distribution_json=[1 / 3, 1 / 3, 1 / 3],
        confidence=0.9,
        source="manual",
        low_confidence_flag=True,
        posterior_mean=6.0,
        posterior_precision=8.0,
        actuals_median=12.0,
    )

    result = _copy_context_profile_payload(target, source)

    assert result is target
    assert target.actuals_median == 7.5
