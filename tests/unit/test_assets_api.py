from types import SimpleNamespace
from uuid import uuid4

from app.api.v1 import assets as assets_api
from app.schemas.enums import AssetStatus, UserRole


def test_update_asset_refreshes_lookahead_when_canonical_type_changes(monkeypatch):
    asset_id = uuid4()
    project_id = uuid4()
    current_user = SimpleNamespace(id=uuid4(), role="manager")
    existing_asset = SimpleNamespace(
        id=asset_id,
        project_id=project_id,
        status=AssetStatus.AVAILABLE,
        canonical_type="crane",
    )
    updated_asset = SimpleNamespace(
        id=asset_id,
        project_id=project_id,
        status=AssetStatus.AVAILABLE,
        canonical_type="excavator",
    )

    monkeypatch.setattr(assets_api.asset_crud, "get_asset", lambda db, asset_id: existing_asset)
    monkeypatch.setattr(assets_api, "check_asset_view_access", lambda db, project_id, current_user: True)
    monkeypatch.setattr(
        assets_api.asset_crud,
        "update_asset",
        lambda db, asset_id, asset_update, user_id, actor_role, confirm_booking_denials: updated_asset,
    )
    refresh_mock = []
    monkeypatch.setattr(
        assets_api,
        "refresh_lookahead_after_project_change",
        lambda project_id: refresh_mock.append(project_id),
    )
    monkeypatch.setattr(assets_api.AssetResponse, "model_validate", staticmethod(lambda obj: obj))

    response = assets_api.update_asset(
        asset_id=asset_id,
        asset_update=SimpleNamespace(),
        confirm_booking_denials=False,
        db=SimpleNamespace(),
        current_user=current_user,
    )

    assert response is updated_asset
    assert refresh_mock == [project_id]


def test_transfer_asset_refreshes_old_and_new_projects_even_without_booking_move(monkeypatch):
    asset_id = uuid4()
    old_project_id = uuid4()
    new_project_id = uuid4()
    current_user = SimpleNamespace(id=uuid4(), role="manager")
    transfer = SimpleNamespace(update_bookings=False)

    monkeypatch.setattr(
        assets_api.asset_crud,
        "get_asset",
        lambda db, asset_id: SimpleNamespace(id=asset_id, project_id=old_project_id),
    )
    monkeypatch.setattr(
        assets_api.asset_crud,
        "transfer_asset",
        lambda db, asset_id, transfer, user_id: SimpleNamespace(id=asset_id, project_id=new_project_id),
    )
    refreshed_projects = []
    monkeypatch.setattr(
        assets_api,
        "refresh_lookahead_after_project_change",
        lambda project_id: refreshed_projects.append(project_id),
    )
    monkeypatch.setattr(assets_api.AssetResponse, "model_validate", staticmethod(lambda obj: obj))

    response = assets_api.transfer_asset(
        asset_id=asset_id,
        transfer=transfer,
        db=SimpleNamespace(),
        current_user=current_user,
    )

    assert response.project_id == new_project_id
    assert set(refreshed_projects) == {old_project_id, new_project_id}


def test_check_asset_view_access_uses_preloaded_project_members_for_manager(monkeypatch):
    manager_id = uuid4()
    project_id = uuid4()
    current_entity = SimpleNamespace(id=manager_id)
    project = SimpleNamespace(
        managers=[SimpleNamespace(id=manager_id)],
        subcontractors=[],
    )

    monkeypatch.setattr(assets_api, "get_user_role", lambda entity: UserRole.MANAGER)
    monkeypatch.setattr(assets_api, "get_entity_id", lambda entity: str(manager_id))
    monkeypatch.setattr(
        assets_api.project_crud,
        "has_project_access",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("should use preloaded project managers before querying")
        ),
    )

    assert (
        assets_api.check_asset_view_access(
            db=SimpleNamespace(),
            project_id=project_id,
            entity=current_entity,
            project=project,
        )
        is True
    )


def test_check_asset_view_access_uses_preloaded_project_members_for_subcontractor(monkeypatch):
    subcontractor_id = uuid4()
    project_id = uuid4()
    current_entity = SimpleNamespace(id=subcontractor_id)
    project = SimpleNamespace(
        managers=[],
        subcontractors=[SimpleNamespace(id=subcontractor_id)],
    )

    monkeypatch.setattr(assets_api, "get_user_role", lambda entity: UserRole.SUBCONTRACTOR)
    monkeypatch.setattr(assets_api, "get_entity_id", lambda entity: str(subcontractor_id))
    monkeypatch.setattr(
        assets_api.project_crud,
        "is_subcontractor_assigned",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("should use preloaded project subcontractors before querying")
        ),
    )

    assert (
        assets_api.check_asset_view_access(
            db=SimpleNamespace(),
            project_id=project_id,
            entity=current_entity,
            project=project,
        )
        is True
    )


def test_get_asset_detail_reuses_preloaded_asset_for_access_and_response(monkeypatch):
    asset_id = uuid4()
    project_id = uuid4()
    current_entity = SimpleNamespace(id=uuid4())
    project = SimpleNamespace(managers=[], subcontractors=[])
    preloaded_asset = SimpleNamespace(
        id=asset_id,
        project_id=project_id,
        project=project,
    )
    expected_response = SimpleNamespace(id=asset_id)

    monkeypatch.setattr(
        assets_api.asset_crud,
        "get_asset",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("route should not call get_asset before get_asset_with_details")
        ),
    )
    monkeypatch.setattr(
        assets_api.asset_crud,
        "get_asset_with_details",
        lambda db, asset_id: preloaded_asset,
    )

    access_calls = {}

    def fake_check_asset_view_access(db, project_id, entity, project=None):
        access_calls["project_id"] = project_id
        access_calls["project"] = project
        access_calls["entity"] = entity
        return True

    monkeypatch.setattr(assets_api, "check_asset_view_access", fake_check_asset_view_access)

    detail_calls = {}

    def fake_get_asset_detail(db, asset_id, asset=None):
        detail_calls["asset_id"] = asset_id
        detail_calls["asset"] = asset
        return expected_response

    monkeypatch.setattr(assets_api.asset_crud, "get_asset_detail", fake_get_asset_detail)

    response = assets_api.get_asset_detail(
        asset_id=asset_id,
        db=SimpleNamespace(),
        current_entity=current_entity,
    )

    assert response is expected_response
    assert access_calls == {
        "project_id": project_id,
        "project": project,
        "entity": current_entity,
    }
    assert detail_calls == {
        "asset_id": asset_id,
        "asset": preloaded_asset,
    }
