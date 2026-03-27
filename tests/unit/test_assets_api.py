from types import SimpleNamespace
from uuid import uuid4

from app.api.v1 import assets as assets_api
from app.schemas.enums import AssetStatus


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
