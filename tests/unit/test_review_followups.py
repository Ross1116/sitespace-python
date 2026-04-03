from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock
from uuid import uuid4

import pytest
from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy.exc import IntegrityError

from app.api.v1 import items as items_api
from app.api.v1 import system as system_api
from app.schemas.item_identity import ItemRequirementEvaluationRequest
from app.services.item_requirements_service import _attribute_matches, validate_requirement_rules
from app.services.work_profile_service import _overwrite_cache_entry, invalidate_context_profile


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
        raise RuntimeError("database down")

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
