from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock
from uuid import uuid4

import pytest
from fastapi import HTTPException

from app.api.v1 import items as items_api


def test_get_item_statistics_endpoint_serializes_learning_payload(monkeypatch):
    item_id = uuid4()
    now = datetime(2026, 3, 31, tzinfo=timezone.utc)
    stats = SimpleNamespace(
        item=SimpleNamespace(id=item_id, display_name="Tower Crane"),
        alias_count=2,
        occurrence_count=6,
        distinct_project_count=3,
        actuals_count=1,
        actual_hours_total=14.5,
        last_seen_at=now,
        active_classification=SimpleNamespace(
            asset_type="crane",
            source="manual",
            confidence="high",
        ),
        local_profile_counts_by_source={"manual": 1, "learned": 2, "ai": 3, "default": 0},
        local_profile_counts_by_maturity={"manual": 1, "trusted_baseline": 2, "confirmed": 1, "tentative": 2},
        global_knowledge_counts_by_tier={"medium": 1, "high": 1},
        global_knowledge_entries=[
            SimpleNamespace(
                asset_type="crane",
                duration_bucket=7,
                confidence_tier="high",
                source_project_count=3,
                sample_count=12,
                correction_count=0,
                posterior_mean=11.5,
                last_updated_at=now,
            )
        ],
    )

    monkeypatch.setattr(items_api, "get_item_statistics", lambda db, item_id: stats)
    monkeypatch.setattr(items_api, "maturity_tier", lambda cls: "manual")

    response = items_api.get_item_statistics_endpoint(
        item_id=item_id,
        db=MagicMock(),
        current_user=SimpleNamespace(id=uuid4(), role="admin"),
    )

    assert response.item_id == item_id
    assert response.display_name == "Tower Crane"
    assert response.actual_hours_total == 14.5
    assert response.active_classification.asset_type == "crane"
    assert response.global_knowledge_entries[0].confidence_tier == "high"


def test_get_item_statistics_endpoint_maps_lookup_error_to_404(monkeypatch):
    item_id = uuid4()

    monkeypatch.setattr(
        items_api,
        "get_item_statistics",
        lambda db, item_id: (_ for _ in ()).throw(LookupError("missing")),
    )

    with pytest.raises(HTTPException) as exc_info:
        items_api.get_item_statistics_endpoint(
            item_id=item_id,
            db=MagicMock(),
            current_user=SimpleNamespace(id=uuid4(), role="manager"),
        )

    assert exc_info.value.status_code == 404
    assert exc_info.value.detail == "missing"


def test_review_other_items_endpoint_serializes_rows(monkeypatch):
    item_id = uuid4()
    now = datetime(2026, 3, 31, tzinfo=timezone.utc)
    rows = [
        {
            "item_id": item_id,
            "display_name": "Generic Support Item",
            "occurrence_count": 5,
            "distinct_project_count": 2,
            "last_seen_at": now,
            "classification_source": "ai",
            "classification_confidence": "medium",
            "classification_maturity_tier": "tentative",
        }
    ]

    monkeypatch.setattr(items_api, "list_other_review_items", lambda db, limit, offset: rows)

    response = items_api.review_other_items(
        limit=25,
        offset=0,
        db=MagicMock(),
        current_user=SimpleNamespace(id=uuid4(), role="manager"),
    )

    assert len(response) == 1
    assert response[0].item_id == item_id
    assert response[0].classification_maturity_tier == "tentative"
