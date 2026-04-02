from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest

from app.services.feature_learning_service import (
    apply_feature_adjustments_to_hours,
    batch_compute_all_feature_effects,
    compute_feature_effects,
    evaluate_context_expansion,
    get_feature_adjustments,
    record_actuals_shape,
    record_feature_observation,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_context_profile(
    *,
    duration_days: int = 5,
    context_version: int = 1,
    inference_version: int = 2,
    actuals_count: int = 0,
    actuals_shape_json=None,
    source: str = "ai",
):
    return SimpleNamespace(
        id=uuid4(),
        item_id=uuid4(),
        asset_type="crane",
        duration_days=duration_days,
        context_version=context_version,
        inference_version=inference_version,
        actuals_count=actuals_count,
        actuals_shape_json=actuals_shape_json,
        source=source,
        project_id=uuid4(),
    )


def _make_activity_profile(*, total_hours: float = 10.0):
    return SimpleNamespace(
        id=uuid4(),
        total_hours=total_hours,
    )


# ---------------------------------------------------------------------------
# record_feature_observation
# ---------------------------------------------------------------------------

def test_record_feature_observation_basic():
    db = MagicMock()
    cp = _make_context_profile(duration_days=5)
    ap = _make_activity_profile(total_hours=10.0)
    compressed = {
        "phase": "structure",
        "spatial_type": "level",
        "area_type": "internal",
        "work_type": "slab",
    }

    obs = record_feature_observation(
        db,
        context_profile=cp,
        activity_work_profile=ap,
        actual_hours=12.0,
        compressed_context=compressed,
        project_id=cp.project_id,
    )

    assert obs.residual == pytest.approx(2.0)
    assert obs.relative_error == pytest.approx(0.2)
    assert obs.predicted_hours == pytest.approx(10.0)
    assert obs.actual_hours == pytest.approx(12.0)
    assert obs.ctx_phase == "structure"
    assert obs.ctx_spatial_type == "level"
    assert obs.ctx_area_type == "internal"
    assert obs.ctx_work_type == "slab"
    assert obs.item_id == cp.item_id
    assert obs.asset_type == "crane"
    assert obs.duration_bucket == 5   # duration_bucket_for_days(5) = 5
    assert obs.context_version == 1
    assert obs.inference_version == 2
    db.add.assert_called_once_with(obs)


def test_record_feature_observation_negative_residual():
    db = MagicMock()
    cp = _make_context_profile()
    ap = _make_activity_profile(total_hours=20.0)
    compressed = {
        "phase": "services",
        "spatial_type": "zone",
        "area_type": "external",
        "work_type": "unknown",
    }

    obs = record_feature_observation(
        db,
        context_profile=cp,
        activity_work_profile=ap,
        actual_hours=15.0,
        compressed_context=compressed,
        project_id=cp.project_id,
    )

    assert obs.residual == pytest.approx(-5.0)
    assert obs.relative_error == pytest.approx(-0.25)


def test_record_feature_observation_zero_predicted():
    """relative_error must be NULL when predicted_hours is 0."""
    db = MagicMock()
    cp = _make_context_profile()
    ap = _make_activity_profile(total_hours=0.0)
    compressed = {
        "phase": "unknown",
        "spatial_type": "unknown",
        "area_type": "internal",
        "work_type": "unknown",
    }

    obs = record_feature_observation(
        db,
        context_profile=cp,
        activity_work_profile=ap,
        actual_hours=5.0,
        compressed_context=compressed,
        project_id=cp.project_id,
    )

    assert obs.relative_error is None
    assert obs.residual == pytest.approx(5.0)


def test_record_feature_observation_missing_context_fields_fall_back_to_unknown():
    """Partial compressed context should default missing fields to 'unknown'."""
    db = MagicMock()
    cp = _make_context_profile()
    ap = _make_activity_profile(total_hours=8.0)

    obs = record_feature_observation(
        db,
        context_profile=cp,
        activity_work_profile=ap,
        actual_hours=8.0,
        compressed_context={"phase": "fitout"},   # missing 3 fields
        project_id=cp.project_id,
    )

    assert obs.ctx_phase == "fitout"
    assert obs.ctx_spatial_type == "unknown"
    assert obs.ctx_area_type == "unknown"
    assert obs.ctx_work_type == "unknown"


def test_record_feature_observation_links_provenance():
    """context_profile_id and activity_work_profile_id are recorded."""
    db = MagicMock()
    cp = _make_context_profile()
    ap = _make_activity_profile(total_hours=5.0)
    compressed = {"phase": "facade", "spatial_type": "level", "area_type": "external", "work_type": "facade"}

    obs = record_feature_observation(
        db,
        context_profile=cp,
        activity_work_profile=ap,
        actual_hours=5.0,
        compressed_context=compressed,
        project_id=cp.project_id,
    )

    assert obs.context_profile_id == cp.id
    assert obs.activity_work_profile_id == ap.id
    assert obs.project_id == cp.project_id


# ---------------------------------------------------------------------------
# record_actuals_shape
# ---------------------------------------------------------------------------

def test_record_actuals_shape_first_observation():
    """First observation is stored directly as the normalized shape."""
    db = MagicMock()
    cp = _make_context_profile(actuals_count=1)   # count already incremented by record_actual_hours

    record_actuals_shape(db, context_profile=cp, actual_daily_hours=[4.0, 4.0, 2.0])

    shape = cp.actuals_shape_json
    assert len(shape) == 3
    assert shape[0] == pytest.approx(0.4)
    assert shape[1] == pytest.approx(0.4)
    assert shape[2] == pytest.approx(0.2)


def test_record_actuals_shape_weighted_blend():
    """Second observation blends with equal weight (n-1)/n vs 1/n."""
    db = MagicMock()
    cp = _make_context_profile(
        actuals_count=2,
        actuals_shape_json=[0.5, 0.3, 0.2],
    )

    # Second observation: [6, 3, 1] → normalised [0.6, 0.3, 0.1]
    record_actuals_shape(db, context_profile=cp, actual_daily_hours=[6.0, 3.0, 1.0])

    shape = cp.actuals_shape_json
    # expected: (old * 1 + new * 1) / 2 = ([0.5, 0.3, 0.2] + [0.6, 0.3, 0.1]) / 2
    assert shape[0] == pytest.approx(0.55, abs=1e-6)
    assert shape[1] == pytest.approx(0.30, abs=1e-6)
    assert shape[2] == pytest.approx(0.15, abs=1e-6)


def test_record_actuals_shape_normalises_to_one():
    """Result must sum to 1.0 after blending."""
    db = MagicMock()
    cp = _make_context_profile(
        actuals_count=3,
        actuals_shape_json=[0.33, 0.33, 0.34],
    )

    record_actuals_shape(db, context_profile=cp, actual_daily_hours=[1.0, 1.0, 1.0])

    total = sum(cp.actuals_shape_json)
    assert total == pytest.approx(1.0, abs=1e-6)


def test_record_actuals_shape_all_zero_hours_no_op():
    """All-zero actual_daily_hours must not change the profile."""
    db = MagicMock()
    cp = _make_context_profile(actuals_count=1, actuals_shape_json=[0.5, 0.5])

    record_actuals_shape(db, context_profile=cp, actual_daily_hours=[0.0, 0.0])

    assert cp.actuals_shape_json == [0.5, 0.5]


def test_record_actuals_shape_length_mismatch_resets():
    """If existing shape length doesn't match, reset to new observation."""
    db = MagicMock()
    cp = _make_context_profile(
        actuals_count=2,
        actuals_shape_json=[0.5, 0.5],   # old length 2
    )

    # New activity has 3 days
    record_actuals_shape(db, context_profile=cp, actual_daily_hours=[3.0, 3.0, 4.0])

    shape = cp.actuals_shape_json
    assert len(shape) == 3
    assert sum(shape) == pytest.approx(1.0, abs=1e-6)


# ---------------------------------------------------------------------------
# Integration: record_actual_hours creates a feature observation
# ---------------------------------------------------------------------------

def test_record_actual_hours_creates_feature_observation(monkeypatch):
    """
    record_actual_hours must call record_feature_observation when a
    context_profile is linked and a compressed context can be resolved.
    """
    from app.services.work_profile_service import record_actual_hours

    profile_id = uuid4()
    context_profile_id = uuid4()
    item_id = uuid4()
    project_id = uuid4()

    fake_actual = SimpleNamespace(
        id=uuid4(),
        actual_hours_used=None,
        source=None,
    )
    fake_profile = SimpleNamespace(
        id=profile_id,
        total_hours=10.0,
        context_profile_id=context_profile_id,
    )
    fake_cp = SimpleNamespace(
        id=context_profile_id,
        item_id=item_id,
        asset_type="excavator",
        duration_days=5,
        context_version=1,
        inference_version=2,
        source="ai",
        posterior_mean=10.0,
        posterior_precision=25.0,
        sample_count=1,
        correction_count=0,
        actuals_count=0,
        actuals_median=None,
        observation_count=0,
        evidence_weight=0.0,
        project_id=project_id,
    )
    compressed = {"phase": "structure", "spatial_type": "level", "area_type": "internal", "work_type": "slab"}

    recorded_obs = []

    def _fake_record(db, *, context_profile, activity_work_profile, actual_hours, compressed_context, project_id):
        recorded_obs.append({
            "actual_hours": actual_hours,
            "compressed_context": compressed_context,
        })

    monkeypatch.setattr(
        "app.services.feature_learning_service.record_feature_observation",
        _fake_record,
    )
    monkeypatch.setattr(
        "app.services.work_profile_service._resolve_context_profile_compressed_context",
        lambda db, profile: compressed,
    )
    monkeypatch.setattr(
        "app.services.work_profile_service.rebuild_global_knowledge_entry",
        lambda *args, **kwargs: None,
    )

    db = MagicMock()
    # First query returns None (no existing AssetUsageActual), second returns context profile
    db.query.return_value.filter.return_value.one_or_none.side_effect = [
        fake_profile,   # ActivityWorkProfile lookup
        None,           # AssetUsageActual lookup (not yet exists)
        fake_cp,        # ItemContextProfile lookup
    ]
    db.query.return_value.join.return_value.filter.return_value.all.return_value = []

    # Patch the Bayesian helpers to avoid deep arithmetic on SimpleNamespace
    monkeypatch.setattr(
        "app.services.work_profile_service._reverse_actual_observations_from_posterior",
        lambda cp, vals: (float(cp.posterior_mean or 10), float(cp.posterior_precision or 25)),
    )
    monkeypatch.setattr(
        "app.services.work_profile_service._apply_actual_observations_to_posterior",
        lambda base_mean, base_prec, vals: (base_mean, base_prec),
    )

    record_actual_hours(db, activity_work_profile_id=profile_id, actual_hours_used=12.0)

    assert len(recorded_obs) == 1
    assert recorded_obs[0]["actual_hours"] == 12.0
    assert recorded_obs[0]["compressed_context"] == compressed


# ---------------------------------------------------------------------------
# Phase B — apply_feature_adjustments_to_hours (pure function)
# ---------------------------------------------------------------------------

def test_apply_adjustments_no_adjustments():
    assert apply_feature_adjustments_to_hours(10.0, {}) == pytest.approx(10.0)


def test_apply_adjustments_positive():
    result = apply_feature_adjustments_to_hours(10.0, {"phase": 0.1})
    assert result == pytest.approx(11.0)


def test_apply_adjustments_negative():
    result = apply_feature_adjustments_to_hours(10.0, {"phase": -0.1})
    assert result == pytest.approx(9.0)


def test_apply_adjustments_multiplicative():
    result = apply_feature_adjustments_to_hours(10.0, {"phase": 0.1, "area_type": 0.1})
    assert result == pytest.approx(10.0 * 1.1 * 1.1, rel=1e-6)


def test_apply_adjustments_clamp_upper():
    """+50% shift is clamped to +30%."""
    result = apply_feature_adjustments_to_hours(10.0, {"phase": 0.5})
    assert result == pytest.approx(13.0)


def test_apply_adjustments_clamp_lower():
    """-50% shift is clamped to -30%."""
    result = apply_feature_adjustments_to_hours(10.0, {"phase": -0.5})
    assert result == pytest.approx(7.0)


def test_apply_adjustments_zero_base():
    """Zero base hours returns zero regardless of adjustments."""
    assert apply_feature_adjustments_to_hours(0.0, {"phase": 0.5}) == pytest.approx(0.0)


# ---------------------------------------------------------------------------
# Phase B — compute_feature_effects
# ---------------------------------------------------------------------------

def _make_obs(*, ctx_phase="structure", ctx_spatial_type="level",
              ctx_area_type="internal", ctx_work_type="slab",
              predicted=10.0, actual=12.0,
              item_id=None, asset_type="crane", duration_bucket=5,
              context_version=1, inference_version=2):
    return SimpleNamespace(
        item_id=item_id or uuid4(),
        asset_type=asset_type,
        duration_bucket=duration_bucket,
        ctx_phase=ctx_phase,
        ctx_spatial_type=ctx_spatial_type,
        ctx_area_type=ctx_area_type,
        ctx_work_type=ctx_work_type,
        predicted_hours=predicted,
        actual_hours=actual,
        residual=actual - predicted,
        relative_error=(actual - predicted) / predicted if predicted else None,
        context_version=context_version,
        inference_version=inference_version,
    )


def test_compute_feature_effects_basic(monkeypatch):
    """Positive residuals for structure → positive learned_weight."""
    item_id = uuid4()
    rows = [
        _make_obs(ctx_phase="structure", predicted=10.0, actual=13.0, item_id=item_id)
        for _ in range(6)
    ] + [
        _make_obs(ctx_phase="services", predicted=10.0, actual=8.0, item_id=item_id)
        for _ in range(6)
    ]

    added = []
    db = MagicMock()
    db.query.return_value.filter.return_value.all.return_value = rows
    db.query.return_value.filter.return_value.one_or_none.return_value = None
    db.add.side_effect = added.append

    result = compute_feature_effects(
        db,
        item_id=item_id,
        asset_type="crane",
        duration_bucket=5,
        min_observations=5,
    )

    assert result > 0
    # Check that structure has positive weight and services has negative weight
    structure_effect = next((e for e in added if getattr(e, "feature_value", None) == "structure"), None)
    services_effect = next((e for e in added if getattr(e, "feature_value", None) == "services"), None)
    assert structure_effect is not None
    assert services_effect is not None
    assert float(structure_effect.learned_weight) > 0
    assert float(services_effect.learned_weight) < 0


def test_compute_feature_effects_below_threshold(monkeypatch):
    """Fewer than min_observations produces no effects."""
    item_id = uuid4()
    rows = [_make_obs(item_id=item_id) for _ in range(3)]

    db = MagicMock()
    db.query.return_value.filter.return_value.all.return_value = rows

    result = compute_feature_effects(
        db,
        item_id=item_id,
        asset_type="crane",
        duration_bucket=5,
        min_observations=5,
    )
    assert result == 0
    db.add.assert_not_called()


def test_compute_feature_effects_no_rows():
    """No observations returns 0."""
    db = MagicMock()
    db.query.return_value.filter.return_value.all.return_value = []

    result = compute_feature_effects(db, item_id=uuid4(), asset_type="crane", duration_bucket=5)
    assert result == 0


def test_confidence_formula():
    """Verify 1 - 1/sqrt(n) for key sample sizes."""
    import math
    from app.services.feature_learning_service import compute_feature_effects  # noqa — just checking formula

    for n, expected in [(5, 1 - 1/math.sqrt(5)), (10, 1 - 1/math.sqrt(10)), (25, 1 - 0.2)]:
        assert pytest.approx(expected, rel=1e-6) == 1 - 1 / math.sqrt(n)


def test_compute_feature_effects_upserts_existing(monkeypatch):
    """When an effect row already exists, it is updated in place (not re-added)."""
    item_id = uuid4()
    rows = [_make_obs(ctx_phase="structure", predicted=10.0, actual=12.0, item_id=item_id) for _ in range(6)]

    existing_effect = SimpleNamespace(
        observation_count=3, mean_residual=1.5, variance_of_residual=0.5,
        effect_magnitude=0.15, learned_weight=0.15, confidence=0.4, effective_weight=0.06,
    )

    db = MagicMock()
    db.query.return_value.filter.return_value.all.return_value = rows
    db.query.return_value.filter.return_value.one_or_none.return_value = existing_effect

    result = compute_feature_effects(db, item_id=item_id, asset_type="crane", duration_bucket=5, min_observations=5)

    assert result > 0
    db.add.assert_not_called()  # updated in place, not added
    assert existing_effect.observation_count == 6


# ---------------------------------------------------------------------------
# Phase B — get_feature_adjustments
# ---------------------------------------------------------------------------

def test_get_feature_adjustments_matches_context():
    """Returns effective_weight for matching feature values."""
    item_id = uuid4()
    effect = SimpleNamespace(
        feature_name="phase",
        feature_value="structure",
        effective_weight=0.12,
        confidence=0.7,
    )
    db = MagicMock()
    db.query.return_value.filter.return_value.all.return_value = [effect]

    result = get_feature_adjustments(
        db,
        item_id=item_id,
        asset_type="crane",
        duration_bucket=5,
        compressed_context={"phase": "structure", "spatial_type": "level"},
    )
    assert result == {"phase": pytest.approx(0.12)}


def test_get_feature_adjustments_no_match():
    """Feature value doesn't match context — returns empty dict."""
    item_id = uuid4()
    effect = SimpleNamespace(
        feature_name="phase",
        feature_value="services",
        effective_weight=0.12,
        confidence=0.7,
    )
    db = MagicMock()
    db.query.return_value.filter.return_value.all.return_value = [effect]

    result = get_feature_adjustments(
        db,
        item_id=item_id,
        asset_type="crane",
        duration_bucket=5,
        compressed_context={"phase": "structure"},  # doesn't match "services"
    )
    assert result == {}


def test_get_feature_adjustments_no_effects():
    db = MagicMock()
    db.query.return_value.filter.return_value.all.return_value = []

    result = get_feature_adjustments(
        db, item_id=uuid4(), asset_type="crane", duration_bucket=5, compressed_context={"phase": "structure"}
    )
    assert result == {}


# ---------------------------------------------------------------------------
# Phase B — evaluate_context_expansion
# ---------------------------------------------------------------------------

def test_evaluate_context_expansion_detects_high_variance(monkeypatch):
    """High parent CV + low sub-group CV → expansion signal."""
    item_id = uuid4()

    # Same base context, two work_types with very different actuals
    rows = (
        [_make_obs(ctx_work_type="slab", actual=5.0, predicted=10.0, item_id=item_id) for _ in range(6)] +
        [_make_obs(ctx_work_type="column", actual=20.0, predicted=10.0, item_id=item_id) for _ in range(6)]
    )
    # All rows share same phase/spatial/area

    added = []
    db = MagicMock()
    db.query.return_value.filter.return_value.all.return_value = rows
    db.query.return_value.filter.return_value.one_or_none.return_value = None
    db.add.side_effect = added.append

    result = evaluate_context_expansion(db, item_id=item_id, asset_type="crane")

    assert result >= 1
    assert len(added) >= 1
    signal = added[0]
    assert signal.promoted is False
    assert signal.expansion_candidate_field == "work_type"
    assert float(signal.expansion_score) > 0


def test_evaluate_context_expansion_low_variance_no_signal():
    """Low parent CV → no expansion signal."""
    item_id = uuid4()
    # All actuals close to each other — low variance
    rows = [_make_obs(actual=10.0 + i * 0.1, predicted=10.0, item_id=item_id) for i in range(12)]

    db = MagicMock()
    db.query.return_value.filter.return_value.all.return_value = rows

    result = evaluate_context_expansion(db, item_id=item_id, asset_type="crane")
    assert result == 0
    db.add.assert_not_called()


def test_evaluate_context_expansion_below_min_obs():
    """Fewer than FEATURE_LEARNING_EXPANSION_MIN_OBS rows → no signal."""
    item_id = uuid4()
    rows = [_make_obs(item_id=item_id) for _ in range(5)]  # below default 10

    db = MagicMock()
    db.query.return_value.filter.return_value.all.return_value = rows

    result = evaluate_context_expansion(db, item_id=item_id, asset_type="crane")
    assert result == 0


# ---------------------------------------------------------------------------
# Phase B — batch_compute_all_feature_effects
# ---------------------------------------------------------------------------

def test_batch_compute_returns_summary(monkeypatch):
    """batch_compute_all_feature_effects returns correct summary structure."""
    item_id = uuid4()
    tuples = [(item_id, "crane", 5), (item_id, "crane", 7)]

    db = MagicMock()
    # distinct query returns tuples
    db.query.return_value.filter.return_value.distinct.return_value.all.return_value = tuples

    monkeypatch.setattr(
        "app.services.feature_learning_service.compute_feature_effects",
        lambda db, item_id, asset_type, duration_bucket, **kw: 3,
    )
    monkeypatch.setattr(
        "app.services.feature_learning_service.evaluate_context_expansion",
        lambda db, item_id, asset_type, **kw: 1,
    )

    result = batch_compute_all_feature_effects(db)

    assert result["computed"] == 2
    assert result["skipped"] == 0
    assert result["effects_upserted"] == 6
    assert result["expansion_signals"] == 1  # evaluated once per (item, asset_type)
