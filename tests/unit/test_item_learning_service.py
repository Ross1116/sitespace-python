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
        if isinstance(self.result, int):
            return self.result
        if self.result is None:
            return 0
        return len(self.result)


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


def test_item_occurrence_stats_ignores_none_project_ids():
    now = datetime(2026, 3, 31, tzinfo=timezone.utc)
    db = MagicMock()
    db.query.return_value.join.return_value.filter.return_value.all.return_value = [
        (SimpleNamespace(item_id=uuid4()), SimpleNamespace(project_id=None, created_at=now)),
        (SimpleNamespace(item_id=uuid4()), SimpleNamespace(project_id=uuid4(), created_at=now)),
    ]

    occurrence_count, distinct_project_count, last_seen_at = item_learning_service._item_occurrence_stats(db, [uuid4()])

    assert occurrence_count == 2
    assert distinct_project_count == 1
    assert last_seen_at == now


def test_list_other_review_items_uses_batched_stats(monkeypatch):
    item_id = uuid4()
    rows = [
        (
            SimpleNamespace(id=item_id, display_name="Generic Support Item", identity_status="active"),
            SimpleNamespace(source="ai", confidence="medium"),
        )
    ]
    db = MagicMock()
    db.query.return_value.join.return_value.filter.return_value.order_by.return_value.offset.return_value.limit.return_value.all.return_value = rows

    monkeypatch.setattr(
        item_learning_service,
        "_batched_item_occurrence_stats",
        lambda db, item_ids: {item_id: (5, 2, None)},
    )
    monkeypatch.setattr(
        item_learning_service,
        "get_item_statistics",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("should not call get_item_statistics")),
    )
    monkeypatch.setattr(item_learning_service, "maturity_tier", lambda classification: "tentative")

    payload = item_learning_service.list_other_review_items(db, limit=10, offset=0)

    assert payload == [
        {
            "item_id": item_id,
            "display_name": "Generic Support Item",
            "occurrence_count": 5,
            "distinct_project_count": 2,
            "last_seen_at": None,
            "classification_source": "ai",
            "classification_confidence": "medium",
            "classification_maturity_tier": "tentative",
        }
    ]
