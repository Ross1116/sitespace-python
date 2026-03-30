from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest
from fastapi import HTTPException

from app.api.v1 import items as items_api
from app.services.identity_service import AliasConflictError, add_manual_alias, merge_items


def _make_item(*, item_id=None, status="active", merged_into=None, display_name="Item"):
    return SimpleNamespace(
        id=item_id or uuid4(),
        identity_status=status,
        merged_into_item_id=merged_into,
        display_name=display_name,
    )


def test_add_manual_alias_is_idempotent_for_same_canonical_item():
    item = _make_item()
    existing_alias = SimpleNamespace(item=item)
    db = MagicMock()
    db.get.return_value = item
    db.query.return_value.filter_by.return_value.first.return_value = existing_alias

    result = add_manual_alias(db, item.id, "Tower Crane Lift")

    assert result is existing_alias
    db.add.assert_not_called()


def test_add_manual_alias_rejects_other_active_item():
    item = _make_item()
    other_item = _make_item()
    existing_alias = SimpleNamespace(item=other_item)
    db = MagicMock()
    db.get.return_value = item
    db.query.return_value.filter_by.return_value.first.return_value = existing_alias

    with pytest.raises(AliasConflictError):
        add_manual_alias(db, item.id, "Tower Crane Lift")


def test_add_manual_alias_creates_alias_and_event():
    item = _make_item()
    db = MagicMock()
    db.get.return_value = item
    db.query.return_value.filter_by.return_value.first.return_value = None
    db.begin_nested.return_value = MagicMock()
    added = []
    db.add.side_effect = lambda obj: added.append(obj)

    alias = add_manual_alias(db, item.id, " Tower Crane Lift ", performed_by_user_id=uuid4())

    assert alias.item_id == item.id
    assert alias.alias_normalised_name == "tower crane lift"
    assert alias.alias_type == "manual"
    assert alias.source == "manual"
    assert any(getattr(obj, "event_type", None) == "alias_add" for obj in added)


def test_merge_items_invokes_context_profile_reconciliation():
    source_id = uuid4()
    target_id = uuid4()
    source = _make_item(item_id=source_id, display_name="Source")
    target = _make_item(item_id=target_id, display_name="Target")
    db = MagicMock()
    db.query.return_value.filter.return_value.order_by.return_value.with_for_update.return_value.all.return_value = [
        source,
        target,
    ]

    with patch("app.services.classification_service.reconcile_classifications_on_merge") as classification_mock, patch(
        "app.services.work_profile_service.reconcile_context_profiles_on_merge"
    ) as context_mock:
        merge_items(db, source_id, target_id)

    classification_mock.assert_called_once_with(db, source_id, target_id)
    context_mock.assert_called_once_with(db, source_id, target_id)


def test_merge_items_raises_when_context_profile_reconciliation_fails():
    source_id = uuid4()
    target_id = uuid4()
    source = _make_item(item_id=source_id, display_name="Source")
    target = _make_item(item_id=target_id, display_name="Target")
    db = MagicMock()
    savepoint = MagicMock()
    db.begin_nested.return_value = savepoint
    db.query.return_value.filter.return_value.order_by.return_value.with_for_update.return_value.all.return_value = [
        source,
        target,
    ]

    with patch("app.services.classification_service.reconcile_classifications_on_merge"), patch(
        "app.services.work_profile_service.reconcile_context_profiles_on_merge",
        side_effect=RuntimeError("context merge boom"),
    ):
        with pytest.raises(RuntimeError, match="context merge boom"):
            merge_items(db, source_id, target_id)

    savepoint.rollback.assert_called_once()


def test_add_item_alias_route_returns_created_alias(monkeypatch):
    item_id = uuid4()
    user_id = uuid4()
    alias = SimpleNamespace(
        id=uuid4(),
        item_id=item_id,
        alias_normalised_name="tower crane lift",
        normalizer_version=1,
        alias_type="manual",
        confidence="high",
        source="manual",
        created_at=datetime(2026, 3, 30, tzinfo=timezone.utc),
        updated_at=datetime(2026, 3, 30, tzinfo=timezone.utc),
    )
    db = MagicMock()
    current_user = SimpleNamespace(id=user_id, role="admin")

    monkeypatch.setattr(items_api, "add_manual_alias", lambda **kwargs: alias)

    response = items_api.add_item_alias(
        item_id=item_id,
        body=items_api.ItemAliasCreateRequest(alias="Tower Crane Lift"),
        db=db,
        current_user=current_user,
    )

    assert response.item_id == item_id
    assert response.alias_normalised_name == "tower crane lift"
    db.commit.assert_called_once()


def test_add_item_alias_route_maps_conflicts_to_409(monkeypatch):
    item_id = uuid4()
    db = MagicMock()
    current_user = SimpleNamespace(id=uuid4(), role="admin")

    monkeypatch.setattr(
        items_api,
        "add_manual_alias",
        lambda **kwargs: (_ for _ in ()).throw(AliasConflictError("already belongs elsewhere")),
    )

    with pytest.raises(HTTPException) as exc_info:
        items_api.add_item_alias(
            item_id=item_id,
            body=items_api.ItemAliasCreateRequest(alias="Tower Crane Lift"),
            db=db,
            current_user=current_user,
        )

    assert exc_info.value.status_code == 409
    db.rollback.assert_called_once()
