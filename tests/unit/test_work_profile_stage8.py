from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock
from uuid import uuid4

from app.services.work_profile_service import (
    _merge_context_profile_counters,
    build_context_key,
    prepare_manual_work_profile,
    reconcile_context_profiles_on_merge,
    upsert_manual_context_profile,
)


def _make_context_profile(
    *,
    project_id=None,
    item_id,
    asset_type: str,
    source: str,
    total_hours: float,
    compressed_context: dict | None = None,
    context_hash: str | None = None,
    confidence: float = 0.6,
    observation_count: int = 1,
    evidence_weight: float = 1.0,
    sample_count: int = 1,
):
    compressed_context = compressed_context or {
        "phase": "structure",
        "spatial_type": "level",
        "area_type": "internal",
        "work_type": "unknown",
    }
    context_hash = context_hash or build_context_key(
        item_id,
        asset_type,
        2,
        compressed_context,
        context_version=1,
        inference_version=2,
    )
    return SimpleNamespace(
        id=uuid4(),
        project_id=project_id or uuid4(),
        item_id=item_id,
        asset_type=asset_type,
        duration_days=2,
        context_version=1,
        inference_version=2,
        context_hash=context_hash,
        compressed_context=compressed_context,
        total_hours=total_hours,
        distribution_json=[total_hours / 2.0, total_hours / 2.0],
        normalized_distribution_json=[0.5, 0.5],
        confidence=confidence,
        source=source,
        low_confidence_flag=False,
        observation_count=observation_count,
        evidence_weight=evidence_weight,
        posterior_mean=total_hours,
        posterior_precision=1.0,
        sample_count=sample_count,
        correction_count=0,
        actuals_count=0,
        actuals_median=None,
        created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        updated_at=datetime(2026, 1, 2, tzinfo=timezone.utc),
    )


def test_prepare_manual_work_profile_auto_clamps_to_asset_cap():
    prepared = prepare_manual_work_profile(
        asset_type="crane",
        duration_days=2,
        max_hours_per_day=8.0,
        manual_total_hours=20.0,
        manual_normalized_distribution=[0.75, 0.25],
    )

    assert prepared.total_hours == 16.0
    assert prepared.distribution == [8.0, 8.0]
    assert prepared.normalized_distribution == [0.75, 0.25]


def test_prepare_manual_work_profile_uses_manual_total_with_existing_distribution():
    prepared = prepare_manual_work_profile(
        asset_type="crane",
        duration_days=2,
        max_hours_per_day=8.0,
        manual_total_hours=12.0,
        manual_normalized_distribution=None,
        existing_total_hours=10.0,
        existing_distribution=[4.0, 6.0],
        existing_normalized_distribution=[0.4, 0.6],
    )

    assert prepared.total_hours == 12.0
    assert prepared.normalized_distribution == [0.4, 0.6]
    assert prepared.distribution == [5.0, 7.0]


def test_prepare_manual_work_profile_uses_manual_distribution_with_existing_total():
    prepared = prepare_manual_work_profile(
        asset_type="crane",
        duration_days=2,
        max_hours_per_day=8.0,
        manual_total_hours=None,
        manual_normalized_distribution=[0.75, 0.25],
        existing_total_hours=12.0,
        existing_distribution=[6.0, 6.0],
        existing_normalized_distribution=[0.5, 0.5],
    )

    assert prepared.total_hours == 12.0
    assert prepared.normalized_distribution == [0.75, 0.25]
    assert prepared.distribution == [8.0, 4.0]


def test_prepare_manual_work_profile_uses_uniform_distribution_for_manual_total_only():
    prepared = prepare_manual_work_profile(
        asset_type="crane",
        duration_days=2,
        max_hours_per_day=8.0,
        manual_total_hours=12.0,
        manual_normalized_distribution=None,
        existing_total_hours=None,
        existing_distribution=None,
        existing_normalized_distribution=None,
    )

    assert prepared.total_hours == 12.0
    assert prepared.normalized_distribution == [0.5, 0.5]
    assert prepared.distribution == [6.0, 6.0]


def test_upsert_manual_context_profile_overwrites_existing_non_manual(monkeypatch):
    item_id = uuid4()
    project_id = uuid4()
    existing = _make_context_profile(
        project_id=project_id,
        item_id=item_id,
        asset_type="forklift",
        source="ai",
        total_hours=10.0,
    )
    db = MagicMock()

    monkeypatch.setattr(
        "app.services.work_profile_service.lookup_cache",
        lambda *args, **kwargs: existing,
    )

    result = upsert_manual_context_profile(
        db,
        project_id=project_id,
        item_id=item_id,
        asset_type="forklift",
        duration_days=2,
        compressed_context={"phase": "structure", "spatial_type": "level", "area_type": "internal", "work_type": "unknown"},
        total_hours=14.0,
        distribution=[7.0, 7.0],
        normalized_distribution=[0.5, 0.5],
    )

    assert result is existing
    assert existing.source == "manual"
    assert existing.confidence == 1.0
    assert existing.total_hours == 14.0
    assert existing.observation_count == 2
    db.flush.assert_called()


def test_reconcile_context_profiles_on_merge_moves_non_conflicting_rows():
    source_item_id = uuid4()
    target_item_id = uuid4()
    project_id = uuid4()
    source_profile = _make_context_profile(
        project_id=project_id,
        item_id=source_item_id,
        asset_type="crane",
        source="ai",
        total_hours=12.0,
    )
    db = MagicMock()
    active_query = db.query.return_value.filter.return_value
    active_query.filter.return_value.with_for_update.return_value.all.side_effect = [
        [source_profile],
        [],
    ]

    reconcile_context_profiles_on_merge(db, source_item_id, target_item_id)

    assert source_profile.item_id == target_item_id
    assert source_profile.context_hash == build_context_key(
        target_item_id,
        "crane",
        2,
        source_profile.compressed_context,
        context_version=1,
        inference_version=2,
    )
    db.flush.assert_called_once()


def test_reconcile_context_profiles_on_merge_prefers_manual_profile_for_conflict():
    source_item_id = uuid4()
    target_item_id = uuid4()
    project_id = uuid4()
    source_profile = _make_context_profile(
        project_id=project_id,
        item_id=source_item_id,
        asset_type="forklift",
        source="manual",
        total_hours=18.0,
        compressed_context={
            "phase": "structure",
            "spatial_type": "level",
            "area_type": "internal",
            "work_type": "lift",
        },
        confidence=1.0,
        observation_count=3,
        evidence_weight=3.0,
        sample_count=3,
    )
    target_profile = _make_context_profile(
        project_id=project_id,
        item_id=target_item_id,
        asset_type="forklift",
        source="ai",
        total_hours=10.0,
        compressed_context={
            "phase": "structure",
            "spatial_type": "level",
            "area_type": "internal",
            "work_type": "lift",
        },
        confidence=0.6,
        observation_count=2,
        evidence_weight=1.5,
        sample_count=2,
    )
    db = MagicMock()
    active_query = db.query.return_value.filter.return_value
    active_query.filter.return_value.with_for_update.return_value.all.side_effect = [
        [source_profile],
        [target_profile],
    ]

    reconcile_context_profiles_on_merge(db, source_item_id, target_item_id)

    assert target_profile.source == "manual"
    assert target_profile.total_hours == 18.0
    assert target_profile.observation_count == 5
    assert target_profile.evidence_weight == 4.5
    assert target_profile.sample_count == 5
    assert target_profile.context_hash == build_context_key(
        target_item_id,
        "forklift",
        2,
        target_profile.compressed_context,
        context_version=1,
        inference_version=2,
    )


def test_merge_context_profile_counters_invalidates_posterior_and_uses_original_actuals_count():
    item_id = uuid4()
    winner = _make_context_profile(
        item_id=item_id,
        asset_type="forklift",
        source="manual",
        total_hours=10.0,
        observation_count=2,
        evidence_weight=2.0,
        sample_count=2,
    )
    winner.posterior_mean = 10.0
    winner.posterior_precision = 2.5
    winner.actuals_count = 1
    winner.actuals_median = 10.0

    loser = _make_context_profile(
        item_id=item_id,
        asset_type="forklift",
        source="ai",
        total_hours=14.0,
        observation_count=3,
        evidence_weight=1.5,
        sample_count=3,
    )
    loser.actuals_count = 2
    loser.actuals_median = 14.0

    merged = _merge_context_profile_counters(winner, loser)

    assert merged.observation_count == 5
    assert merged.evidence_weight == 3.5
    assert merged.sample_count == 5
    assert merged.posterior_mean is None
    assert merged.posterior_precision is None
    assert merged.actuals_count == 3
    assert merged.actuals_median == 14.0
