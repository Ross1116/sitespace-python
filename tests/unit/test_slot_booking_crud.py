from types import SimpleNamespace
from uuid import uuid4
from unittest.mock import MagicMock

from app.crud import slot_booking as booking_crud
from app.schemas.enums import UserRole


def test_resolve_booking_actor_uses_preloaded_project_members_for_manager():
    manager_id = uuid4()
    subcontractor_id = uuid4()
    project_id = uuid4()
    db = MagicMock()
    project = SimpleNamespace(
        id=project_id,
        managers=[SimpleNamespace(id=manager_id)],
        subcontractors=[SimpleNamespace(id=subcontractor_id)],
    )

    resolved_manager_id, resolved_subcontractor_id, booking_status = booking_crud._resolve_booking_actor(
        db=db,
        actor_id=manager_id,
        actor_role=UserRole.MANAGER,
        provided_manager_id=None,
        provided_subcontractor_id=subcontractor_id,
        project_id=project_id,
        project=project,
    )

    assert resolved_manager_id == manager_id
    assert resolved_subcontractor_id == subcontractor_id
    assert booking_status.value == "confirmed"
    db.query.assert_not_called()


def test_check_booking_conflicts_reuses_preloaded_asset_capacity():
    asset_id = uuid4()
    query_confirmed = MagicMock()
    query_confirmed.filter.return_value = query_confirmed
    query_confirmed.all.return_value = []

    query_pending = MagicMock()
    query_pending.filter.return_value = query_pending
    query_pending.scalar.return_value = 1

    db = MagicMock()
    db.query.side_effect = [query_confirmed, query_pending]

    response = booking_crud.check_booking_conflicts(
        db,
        conflict_check=SimpleNamespace(
            asset_id=asset_id,
            booking_date=SimpleNamespace(),
            start_time=SimpleNamespace(),
            end_time=SimpleNamespace(),
            exclude_booking_id=None,
        ),
        asset=SimpleNamespace(id=asset_id, pending_booking_capacity=3),
    )

    assert response.pending_capacity == 3
    assert db.query.call_count == 2
