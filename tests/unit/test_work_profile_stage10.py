from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest
from sqlalchemy.exc import IntegrityError

from app.services.work_profile_service import (
    WorkProfilePreflight,
    build_compressed_context,
    build_context_key,
    _seed_local_cache_from_global_knowledge,
    backfill_project_local_context_profiles,
    get_global_knowledge_entry,
    record_actual_hours,
    rebuild_global_knowledge_entry,
    resolve_work_profile,
)


class _QueryStub:
    def __init__(self, result):
        self.result = result

    def filter(self, *args, **kwargs):
        return self

    def join(self, *args, **kwargs):
        return self

    def outerjoin(self, *args, **kwargs):
        return self

    def order_by(self, *args, **kwargs):
        return self

    def one_or_none(self):
        return self.result

    def all(self):
        return self.result


def test_get_global_knowledge_entry_respects_asset_presence_guard(monkeypatch):
    db = MagicMock()

    monkeypatch.setattr(
        "app.services.work_profile_service._project_has_asset_type",
        lambda *args, **kwargs: False,
    )

    result = get_global_knowledge_entry(
        db,
        project_id=uuid4(),
        item_id=uuid4(),
        asset_type="crane",
        duration_days=7,
    )

    assert result is None
    db.query.assert_not_called()


def test_rebuild_global_knowledge_entry_promotes_eligible_local_rows(monkeypatch):
    item_id = uuid4()
    row_project_ids = [uuid4(), uuid4(), uuid4()]
    rows = [
        SimpleNamespace(
            project_id=row_project_ids[idx],
            posterior_mean=10.0 + idx,
            posterior_precision=100.0,
            sample_count=4,
            correction_count=0,
            normalized_distribution_json=[0.2, 0.2, 0.2, 0.1, 0.1, 0.1, 0.1],
        )
        for idx in range(3)
    ]
    db = MagicMock()
    db.query.return_value.filter.return_value.one_or_none.return_value = None

    monkeypatch.setattr(
        "app.services.work_profile_service._eligible_local_profiles_for_global_entry",
        lambda *args, **kwargs: rows,
    )

    result = rebuild_global_knowledge_entry(
        db,
        item_id=item_id,
        asset_type="crane",
        duration_bucket=7,
    )

    assert result is not None
    assert result.item_id == item_id
    assert result.asset_type == "crane"
    assert result.duration_bucket == 7
    assert result.confidence_tier == "high"
    assert result.source_project_count == 3
    assert result.sample_count == 12
    assert len(result.normalized_shape_json) == 7
    db.add.assert_called_once()


def test_rebuild_global_knowledge_entry_downgrades_for_corrected_locals(monkeypatch):
    item_id = uuid4()
    row_project_ids = [uuid4(), uuid4(), uuid4()]
    rows = [
        SimpleNamespace(
            project_id=row_project_ids[idx],
            posterior_mean=10.0 + idx,
            posterior_precision=100.0,
            sample_count=4,
            correction_count=1,
            normalized_distribution_json=[0.2, 0.2, 0.2, 0.1, 0.1, 0.1, 0.1],
            duration_days=7,
            asset_type="crane",
        )
        for idx in range(3)
    ]
    db = MagicMock()
    db.query.return_value.filter.return_value.one_or_none.return_value = None

    monkeypatch.setattr(
        "app.services.work_profile_service._eligible_local_profiles_for_global_entry",
        lambda *args, **kwargs: rows,
    )

    result = rebuild_global_knowledge_entry(
        db,
        item_id=item_id,
        asset_type="crane",
        duration_bucket=7,
    )

    assert result is not None
    assert result.confidence_tier == "medium"
    assert result.correction_count == 3


def test_rebuild_global_knowledge_entry_recovers_from_insert_race(monkeypatch):
    item_id = uuid4()
    existing_row = SimpleNamespace(
        item_id=item_id,
        asset_type="crane",
        duration_bucket=7,
        context_version=1,
        inference_version=1,
        normalized_shape_json=[1 / 7.0] * 7,
        confidence_tier="medium",
        posterior_mean=10.0,
        posterior_precision=5.0,
        source_project_count=1,
        sample_count=4,
        correction_count=0,
    )
    db = MagicMock()
    db.begin_nested.return_value = MagicMock()
    db.flush.side_effect = [
        IntegrityError("insert", {}, Exception("duplicate key")),
        None,
    ]
    query_results = iter([None, existing_row])

    monkeypatch.setattr(
        "app.services.work_profile_service._query_item_knowledge_entry",
        lambda *args, **kwargs: next(query_results),
    )
    monkeypatch.setattr(
        "app.services.work_profile_service._eligible_local_profiles_for_global_entry",
        lambda *args, **kwargs: [
            SimpleNamespace(
                project_id=uuid4(),
                posterior_mean=10.0,
                posterior_precision=100.0,
                sample_count=4,
                correction_count=0,
                normalized_distribution_json=[1 / 7.0] * 7,
            )
        ],
    )

    result = rebuild_global_knowledge_entry(
        db,
        item_id=item_id,
        asset_type="crane",
        duration_bucket=7,
    )

    assert result is existing_row
    assert existing_row.confidence_tier == "medium"


def test_seed_local_cache_from_global_knowledge_initializes_zero_evidence_counts(monkeypatch):
    db = MagicMock()
    item_id = uuid4()
    project_id = uuid4()
    global_row = SimpleNamespace(
        posterior_mean=12.0,
        posterior_precision=4.0,
    )

    monkeypatch.setattr(
        "app.services.work_profile_service.lookup_cache",
        lambda *args, **kwargs: None,
    )

    seeded = _seed_local_cache_from_global_knowledge(
        db,
        project_id=project_id,
        item_id=item_id,
        asset_type="crane",
        duration_days=5,
        context_hash="exact-hash",
        max_hours_per_day=10.0,
        compressed_context={
            "phase": "structure",
            "spatial_type": "level",
            "area_type": "internal",
            "work_type": "slab",
        },
        global_knowledge=global_row,
    )

    assert seeded.project_id == project_id
    assert seeded.item_id == item_id
    assert seeded.source == "learned"
    assert seeded.sample_count == 0
    assert seeded.observation_count == 0
    assert float(seeded.evidence_weight) == 0.0
    assert float(seeded.posterior_mean) == 12.0
    db.add.assert_called_once()
    db.flush.assert_called_once()


def test_seed_local_cache_from_global_knowledge_prefers_learned_global_shape(monkeypatch):
    db = MagicMock()
    item_id = uuid4()
    project_id = uuid4()
    global_row = SimpleNamespace(
        posterior_mean=12.0,
        posterior_precision=4.0,
        normalized_shape_json=[0.6, 0.3, 0.1],
    )

    monkeypatch.setattr(
        "app.services.work_profile_service.lookup_cache",
        lambda *args, **kwargs: None,
    )

    seeded = _seed_local_cache_from_global_knowledge(
        db,
        project_id=project_id,
        item_id=item_id,
        asset_type="crane",
        duration_days=3,
        context_hash="exact-hash",
        max_hours_per_day=10.0,
        compressed_context={
            "phase": "structure",
            "spatial_type": "level",
            "area_type": "internal",
            "work_type": "slab",
        },
        global_knowledge=global_row,
    )

    assert seeded.normalized_distribution_json == [0.6, 0.3, 0.1]
    assert seeded.distribution_json[0] > seeded.distribution_json[1] > seeded.distribution_json[2]


def test_seed_local_cache_from_global_knowledge_recovers_from_insert_race(monkeypatch):
    db = MagicMock()
    db.begin_nested.return_value = MagicMock()
    existing = SimpleNamespace(id=uuid4(), total_hours=12.0)
    db.flush.side_effect = [IntegrityError("insert", {}, Exception("duplicate key"))]

    item_id = uuid4()
    project_id = uuid4()
    monkeypatch.setattr(
        "app.services.work_profile_service.lookup_cache",
        lambda *args, **kwargs: existing,
    )

    seeded = _seed_local_cache_from_global_knowledge(
        db,
        project_id=project_id,
        item_id=item_id,
        asset_type="crane",
        duration_days=5,
        context_hash="exact-hash",
        max_hours_per_day=10.0,
        compressed_context={
            "phase": "structure",
            "spatial_type": "level",
            "area_type": "internal",
            "work_type": "slab",
        },
        global_knowledge=SimpleNamespace(
            posterior_mean=12.0,
            posterior_precision=4.0,
            normalized_shape_json=[],
        ),
    )

    assert seeded is existing


def test_resolve_work_profile_high_global_hit_seeds_local_cache_without_ai():
    db = MagicMock()
    project_id = uuid4()
    item_id = uuid4()
    seeded_cache = SimpleNamespace(
        id=uuid4(),
        total_hours=12.0,
        distribution_json=[4.0, 3.0, 2.0, 2.0, 1.0],
        normalized_distribution_json=[1 / 3.0, 0.25, 1 / 6.0, 1 / 6.0, 1 / 12.0],
        confidence=0.9,
        source="learned",
        low_confidence_flag=False,
    )
    written_profile = MagicMock()
    preflight = WorkProfilePreflight(
        compressed_context={
            "phase": "structure",
            "spatial_type": "level",
            "area_type": "internal",
            "work_type": "slab",
        },
        context_hash="exact-hash",
        max_hours_per_day=10.0,
        cached=None,
        tier=None,
        trusted_baseline=None,
        global_knowledge=SimpleNamespace(
            confidence_tier="high",
            posterior_mean=12.0,
            posterior_precision=4.0,
            sample_count=12,
        ),
    )

    with patch("app.services.work_profile_service._seed_local_cache_from_global_knowledge", return_value=seeded_cache) as seed_mock, \
         patch("app.services.work_profile_service._request_validated_ai_proposal_sync") as ai_mock, \
         patch("app.services.work_profile_service._write_activity_profile", return_value=written_profile) as write_mock:
        result = resolve_work_profile(
            db,
            project_id=project_id,
            activity_id=uuid4(),
            item_id=item_id,
            asset_type="crane",
            duration_days=5,
            activity_name="Install tower crane",
            preflight=preflight,
        )

    assert result is written_profile
    seed_mock.assert_called_once()
    ai_mock.assert_not_called()
    assert write_mock.call_args.kwargs["source"] == "cache"
    assert write_mock.call_args.kwargs["context_profile_id"] == seeded_cache.id
    assert write_mock.call_args.kwargs["total_hours"] == seeded_cache.total_hours
    assert write_mock.call_args.kwargs["distribution"] == seeded_cache.distribution_json
    assert write_mock.call_args.kwargs["normalized_distribution"] == seeded_cache.normalized_distribution_json


def test_resolve_work_profile_medium_global_hit_passes_posterior_hint_to_ai():
    db = MagicMock()
    project_id = uuid4()
    item_id = uuid4()
    updated_cache = SimpleNamespace(id=uuid4())
    written_profile = MagicMock()
    preflight = WorkProfilePreflight(
        compressed_context={
            "phase": "structure",
            "spatial_type": "level",
            "area_type": "internal",
            "work_type": "slab",
        },
        context_hash="exact-hash",
        max_hours_per_day=10.0,
        cached=None,
        tier=None,
        trusted_baseline=None,
        global_knowledge=SimpleNamespace(
            confidence_tier="medium",
            posterior_mean=11.0,
            posterior_precision=4.0,
            sample_count=5,
        ),
    )

    ai_payload = {
        "final_hours": 8.0,
        "distribution": [4.0, 4.0],
        "normalized_distribution": [0.5, 0.5],
        "confidence": 0.82,
    }

    with patch("app.services.work_profile_service._request_validated_ai_proposal_sync", return_value=ai_payload) as ai_mock, \
         patch("app.services.work_profile_service._upsert_cache_from_external_observation", return_value=updated_cache), \
         patch("app.services.work_profile_service._write_activity_profile", return_value=written_profile) as write_mock:
        result = resolve_work_profile(
            db,
            project_id=project_id,
            activity_id=uuid4(),
            item_id=item_id,
            asset_type="crane",
            duration_days=2,
            activity_name="Lift facade panels",
            preflight=preflight,
        )

    assert result is written_profile
    assert ai_mock.call_args.kwargs["posterior_hint"]["posterior_mean"] == 11.0
    assert write_mock.call_args.kwargs["source"] == "ai"
    assert write_mock.call_args.kwargs["context_profile_id"] == updated_cache.id


def test_record_actual_hours_updates_non_manual_profile_and_rebuilds_global():
    project_id = uuid4()
    item_id = uuid4()
    profile = SimpleNamespace(
        id=uuid4(),
        context_profile_id=uuid4(),
    )
    context_profile = SimpleNamespace(
        id=profile.context_profile_id,
        project_id=project_id,
        item_id=item_id,
        asset_type="crane",
        duration_days=5,
        context_version=1,
        inference_version=1,
        source="ai",
        actuals_count=0,
        actuals_median=None,
        sample_count=3,
        posterior_mean=10.0,
        posterior_precision=4.0,
        evidence_weight=2.0,
    )
    db = MagicMock()
    db.query.side_effect = [
        _QueryStub(profile),
        _QueryStub(None),
        _QueryStub(context_profile),
        _QueryStub([]),
    ]

    with patch("app.services.work_profile_service.rebuild_global_knowledge_entry") as rebuild_mock, \
         patch("app.services.work_profile_service._resolve_context_profile_compressed_context", return_value=None):
        actual = record_actual_hours(
            db,
            activity_work_profile_id=profile.id,
            actual_hours_used=12.0,
            source="manual",
            recorded_by_user_id=uuid4(),
        )

    assert float(actual.actual_hours_used) == 12.0
    assert context_profile.actuals_count == 1
    assert float(context_profile.actuals_median) == 12.0
    assert context_profile.sample_count == 4
    assert float(context_profile.evidence_weight) == 3.0
    rebuild_mock.assert_called_once()
    db.add.assert_called_once()


def test_record_actual_hours_preserves_manual_profile_authority():
    profile = SimpleNamespace(
        id=uuid4(),
        context_profile_id=uuid4(),
    )
    context_profile = SimpleNamespace(
        id=profile.context_profile_id,
        project_id=uuid4(),
        item_id=uuid4(),
        asset_type="crane",
        duration_days=5,
        context_version=1,
        inference_version=1,
        source="manual",
        actuals_count=2,
        actuals_median=10.0,
        sample_count=4,
        posterior_mean=11.0,
        posterior_precision=6.0,
        evidence_weight=3.0,
    )
    db = MagicMock()
    db.query.side_effect = [
        _QueryStub(profile),
        _QueryStub(None),
        _QueryStub(context_profile),
        _QueryStub([(9.0,), (10.0,)]),
    ]

    with patch("app.services.work_profile_service.rebuild_global_knowledge_entry") as rebuild_mock, \
         patch("app.services.work_profile_service._resolve_context_profile_compressed_context", return_value=None):
        record_actual_hours(
            db,
            activity_work_profile_id=profile.id,
            actual_hours_used=14.0,
        )

    assert context_profile.actuals_count == 3
    assert context_profile.sample_count == 4
    assert float(context_profile.posterior_mean) == 11.0
    assert float(context_profile.posterior_precision) == 6.0
    rebuild_mock.assert_not_called()


def test_record_actual_hours_updating_existing_row_triggers_learning_refresh():
    profile = SimpleNamespace(
        id=uuid4(),
        context_profile_id=uuid4(),
    )
    existing_actual = SimpleNamespace(
        activity_work_profile_id=profile.id,
        actual_hours_used=10.0,
        source="system",
        recorded_by_user_id=None,
        booking_group_id=None,
        booking_id=None,
    )
    context_profile = SimpleNamespace(
        id=profile.context_profile_id,
        project_id=uuid4(),
        item_id=uuid4(),
        asset_type="crane",
        duration_days=5,
        context_version=1,
        inference_version=1,
        source="ai",
        actuals_count=3,
        actuals_median=11.0,
        sample_count=6,
        posterior_mean=12.0,
        posterior_precision=7.0,
        evidence_weight=4.0,
    )
    db = MagicMock()
    db.query.side_effect = [
        _QueryStub(profile),
        _QueryStub(existing_actual),
        _QueryStub(context_profile),
        _QueryStub([(10.0,), (11.0,), (12.0,)]),
    ]

    with patch("app.services.work_profile_service.rebuild_global_knowledge_entry") as rebuild_mock, \
         patch("app.services.work_profile_service._resolve_context_profile_compressed_context", return_value=None):
        actual = record_actual_hours(
            db,
            activity_work_profile_id=profile.id,
            actual_hours_used=14.0,
            source="import",
        )

    assert actual is existing_actual
    assert float(existing_actual.actual_hours_used) == 14.0
    assert context_profile.actuals_count == 3
    assert context_profile.sample_count == 6
    assert float(context_profile.evidence_weight) == 4.0
    rebuild_mock.assert_called_once()


def test_record_actual_hours_persists_actual_when_context_profile_is_invalidated():
    profile = SimpleNamespace(
        id=uuid4(),
        context_profile_id=uuid4(),
    )
    context_profile = SimpleNamespace(
        id=profile.context_profile_id,
        invalidated_at=datetime.now(timezone.utc),
        project_id=uuid4(),
        item_id=uuid4(),
        asset_type="crane",
        duration_days=5,
        context_version=1,
        inference_version=1,
        source="ai",
        actuals_count=0,
        actuals_median=None,
        sample_count=3,
        posterior_mean=10.0,
        posterior_precision=4.0,
        evidence_weight=2.0,
    )
    db = MagicMock()
    db.query.side_effect = [
        _QueryStub(profile),
        _QueryStub(None),
        _QueryStub(context_profile),
        _QueryStub([]),
    ]

    with patch("app.services.work_profile_service.rebuild_global_knowledge_entry") as rebuild_mock, \
         patch("app.services.work_profile_service._resolve_context_profile_compressed_context", return_value=None):
        actual = record_actual_hours(
            db,
            activity_work_profile_id=profile.id,
            actual_hours_used=12.0,
            source="manual",
            recorded_by_user_id=uuid4(),
        )

    assert float(actual.actual_hours_used) == 12.0
    db.add.assert_called_once()
    db.flush.assert_called_once()
    rebuild_mock.assert_not_called()
    assert context_profile.actuals_count == 0


def test_backfill_project_local_context_profiles_repoints_existing_profile(monkeypatch):
    project_id = uuid4()
    item_id = uuid4()
    local_profile = SimpleNamespace(
        id=uuid4(),
        source="learned",
        observation_count=0,
    )
    activity_profile = SimpleNamespace(
        id=uuid4(),
        activity_id=uuid4(),
        item_id=item_id,
        asset_type="crane",
        duration_days=5,
        context_version=1,
        inference_version=1,
        total_hours=10.0,
        distribution_json=[5.0, 5.0, 0.0, 0.0, 0.0],
        normalized_distribution_json=[0.5, 0.5, 0.0, 0.0, 0.0],
        confidence=0.8,
        source="cache",
        context_hash="exact-hash",
        low_confidence_flag=False,
        context_profile_id=None,
        created_at=datetime(2026, 3, 1, tzinfo=timezone.utc),
    )
    activity = SimpleNamespace(
        id=activity_profile.activity_id,
        programme_upload_id=uuid4(),
        item_id=item_id,
        name="Lift panels",
        level_name="Level 1",
        zone_name="Zone A",
    )
    upload = SimpleNamespace(id=activity.programme_upload_id, project_id=project_id)
    db = MagicMock()
    db.query.return_value.join.return_value.join.return_value.outerjoin.return_value.order_by.return_value.all.return_value = [
        (activity_profile, activity, upload, None),
    ]

    monkeypatch.setattr(
        "app.services.work_profile_service.lookup_cache",
        lambda *args, **kwargs: local_profile,
    )
    rebuild_calls: list[object] = []
    monkeypatch.setattr(
        "app.services.work_profile_service.rebuild_all_global_knowledge",
        lambda _db: rebuild_calls.append(_db),
    )

    result = backfill_project_local_context_profiles(db)

    assert activity_profile.context_profile_id == local_profile.id
    assert result["processed_activity_profiles"] == 1
    assert result["repointed_activity_profiles"] == 1
    assert rebuild_calls == [db]


def test_backfill_project_local_context_profiles_fails_closed_on_missing_project():
    activity_profile = SimpleNamespace(
        id=uuid4(),
        activity_id=uuid4(),
        item_id=uuid4(),
        asset_type="crane",
        duration_days=5,
        context_version=1,
        inference_version=1,
        total_hours=10.0,
        distribution_json=[5.0, 5.0, 0.0, 0.0, 0.0],
        normalized_distribution_json=[0.5, 0.5, 0.0, 0.0, 0.0],
        confidence=0.8,
        source="cache",
        context_hash="exact-hash",
        low_confidence_flag=False,
        context_profile_id=None,
        created_at=datetime(2026, 3, 1, tzinfo=timezone.utc),
    )
    activity = SimpleNamespace(
        id=activity_profile.activity_id,
        programme_upload_id=uuid4(),
        item_id=uuid4(),
        name="Lift panels",
        level_name="Level 1",
        zone_name="Zone A",
    )
    upload = SimpleNamespace(id=activity.programme_upload_id, project_id=None)
    db = MagicMock()
    db.query.return_value.join.return_value.join.return_value.outerjoin.return_value.order_by.return_value.all.return_value = [
        (activity_profile, activity, upload, None),
    ]

    with pytest.raises(RuntimeError, match="unresolved provenance"):
        backfill_project_local_context_profiles(db)


def test_backfill_project_local_context_profiles_skips_when_already_repointed(monkeypatch):
    project_id = uuid4()
    item_id = uuid4()
    local_profile = SimpleNamespace(
        id=uuid4(),
        source="learned",
        observation_count=0,
    )
    activity_profile = SimpleNamespace(
        id=uuid4(),
        activity_id=uuid4(),
        item_id=item_id,
        asset_type="crane",
        duration_days=5,
        context_version=1,
        inference_version=1,
        total_hours=10.0,
        distribution_json=[5.0, 5.0, 0.0, 0.0, 0.0],
        normalized_distribution_json=[0.5, 0.5, 0.0, 0.0, 0.0],
        confidence=0.8,
        source="cache",
        context_hash="exact-hash",
        low_confidence_flag=False,
        context_profile_id=local_profile.id,
        created_at=datetime(2026, 3, 1, tzinfo=timezone.utc),
    )
    activity = SimpleNamespace(
        id=activity_profile.activity_id,
        programme_upload_id=uuid4(),
        item_id=item_id,
        name="Lift panels",
        level_name="Level 1",
        zone_name="Zone A",
    )
    upload = SimpleNamespace(id=activity.programme_upload_id, project_id=project_id)
    db = MagicMock()
    db.query.return_value.join.return_value.join.return_value.outerjoin.return_value.order_by.return_value.all.return_value = [
        (activity_profile, activity, upload, None),
    ]

    monkeypatch.setattr(
        "app.services.work_profile_service.lookup_cache",
        lambda *args, **kwargs: local_profile,
    )
    monkeypatch.setattr(
        "app.services.work_profile_service.rebuild_all_global_knowledge",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("should not rebuild global knowledge")),
    )
    monkeypatch.setattr(
        "app.services.work_profile_service._update_cache_on_hit",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("should not update cache hit")),
    )

    result = backfill_project_local_context_profiles(db)

    assert result["processed_activity_profiles"] == 1
    assert result["repointed_activity_profiles"] == 0
    assert result["reused_local_hits"] == 0
    assert result["materialized_local_profiles"] == 0


def test_backfill_project_local_context_profiles_normalizes_duration_before_hash(monkeypatch):
    project_id = uuid4()
    item_id = uuid4()
    activity_profile = SimpleNamespace(
        id=uuid4(),
        activity_id=uuid4(),
        item_id=item_id,
        asset_type="crane",
        duration_days=0,
        context_version=1,
        inference_version=1,
        total_hours=10.0,
        distribution_json=[10.0],
        normalized_distribution_json=[1.0],
        confidence=0.8,
        source="cache",
        context_hash=None,
        low_confidence_flag=False,
        context_profile_id=None,
        created_at=datetime(2026, 3, 1, tzinfo=timezone.utc),
    )
    activity = SimpleNamespace(
        id=activity_profile.activity_id,
        programme_upload_id=uuid4(),
        item_id=item_id,
        name="Lift panels",
        level_name="Level 1",
        zone_name="Zone A",
    )
    upload = SimpleNamespace(id=activity.programme_upload_id, project_id=project_id)
    db = MagicMock()
    db.query.return_value.join.return_value.join.return_value.outerjoin.return_value.order_by.return_value.all.return_value = [
        (activity_profile, activity, upload, None),
    ]

    captured: dict[str, object] = {}

    def _lookup_cache(
        _db,
        _project_id,
        _item_id,
        _asset_type,
        duration_days,
        context_hash,
        _context_version,
        _inference_version,
    ):
        captured["duration_days"] = duration_days
        captured["context_hash"] = context_hash
        return SimpleNamespace(id=uuid4(), source="learned", observation_count=0)

    monkeypatch.setattr("app.services.work_profile_service.lookup_cache", _lookup_cache)
    monkeypatch.setattr(
        "app.services.work_profile_service.rebuild_all_global_knowledge",
        lambda _db: None,
    )

    backfill_project_local_context_profiles(db)

    compressed_context = build_compressed_context(
        activity.name,
        level_name=activity.level_name,
        zone_name=activity.zone_name,
    )
    expected_hash = build_context_key(
        item_id,
        "crane",
        1,
        compressed_context,
        context_version=1,
        inference_version=1,
    )

    assert captured["duration_days"] == 1
    assert captured["context_hash"] == expected_hash
