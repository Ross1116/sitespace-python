from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock
from uuid import uuid4

from app.services import item_learning_service


class _QueryChain:
    def __init__(self, result=None):
        self.result = result

    def filter(self, *args, **kwargs):
        return self

    def join(self, *args, **kwargs):
        return self

    def outerjoin(self, *args, **kwargs):
        return self

    def order_by(self, *args, **kwargs):
        return self

    def all(self):
        return self.result

    def count(self):
        return self.result


def test_get_item_statistics_counts_actuals_for_merged_family_context_profiles(monkeypatch):
    canonical_id = uuid4()
    merged_id = uuid4()
    canonical_item = SimpleNamespace(id=canonical_id, display_name="Tower Crane")
    merged_item = SimpleNamespace(id=merged_id, display_name="Tower Crane Old")
    now = datetime(2026, 3, 31, tzinfo=timezone.utc)

    db = MagicMock()
    db.get.return_value = merged_item
    db.query.side_effect = [
        _QueryChain(2),
        _QueryChain([(SimpleNamespace(item_id=merged_id), SimpleNamespace(project_id=uuid4(), created_at=now))]),
        _QueryChain([]),
        _QueryChain([]),
        _QueryChain(
            [
                (
                    SimpleNamespace(actual_hours_used=6.5),
                    SimpleNamespace(item_id=merged_id),
                    SimpleNamespace(item_id=merged_id),
                )
            ]
        ),
    ]

    monkeypatch.setattr(item_learning_service, "follow_item_redirect", lambda db, item: canonical_item)
    monkeypatch.setattr(item_learning_service, "_family_item_ids", lambda db, item_id: [canonical_id, merged_id])
    monkeypatch.setattr(item_learning_service, "get_active_classification", lambda db, item_id: None)

    stats = item_learning_service.get_item_statistics(db, merged_id)

    assert stats.item.id == canonical_id
    assert stats.alias_count == 2
    assert stats.occurrence_count == 1
    assert stats.actuals_count == 1
    assert stats.actual_hours_total == 6.5
