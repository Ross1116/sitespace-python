"""
Unit tests for the Stage 5 work profile service.

Tests cover (in order):
  - build_compressed_context: phase, spatial_type, area_type, work_type
  - build_context_key: determinism, sensitivity to inputs
  - work_profile_maturity: MANUAL / TRUSTED_BASELINE / CONFIRMED / TENTATIVE + correction override
  - bayesian_update: math correctness
  - _obs_precision: source-based precision values
  - _initial_posterior: first-encounter prior
  - quantize_hours: 0.5-unit rounding
  - finalize_total_hours: precedence order and bounds
  - derive_distribution / derive_normalized_distribution: round-trip
  - validate_stage_b: per-day bucket cap enforcement
  - validate_stage_d: final profile invariants
  - redistribute_capped_distribution: cap redistribution and uniform fallback
  - build_default_profile: 'none' zeros, non-none conservative default
"""

import math
import uuid
import pytest
from unittest.mock import MagicMock, patch, PropertyMock

from app.services.work_profile_service import (
    MATURITY_CONFIRMED,
    MATURITY_MANUAL,
    MATURITY_TENTATIVE,
    MATURITY_TRUSTED_BASELINE,
    ValidationResult,
    bayesian_update,
    build_compressed_context,
    build_context_key,
    build_default_profile,
    derive_distribution,
    derive_normalized_distribution,
    finalize_total_hours,
    quantize_hours,
    redistribute_capped_distribution,
    resolve_work_profile,
    validate_stage_b,
    validate_stage_d,
    work_profile_maturity,
    _base_context_only,
    _initial_posterior,
    _lookup_cache_with_reduced_context,
    _obs_precision,
    _uniform_normalized,
    _find_trusted_baseline,
    _update_cache_on_hit,
    WORK_PROFILE_NORM_DIST_SUM_TOLERANCE,
)
from app.models.work_profile import ItemContextProfile


# ─── Helpers ──────────────────────────────────────────────────────────────────

def _make_profile(
    *,
    source: str = "ai",
    posterior_mean: float | None = None,
    posterior_precision: float | None = None,
    sample_count: int = 1,
    correction_count: int = 0,
    total_hours: float = 10.0,
    confidence: float = 0.8,
    low_confidence_flag: bool = False,
) -> ItemContextProfile:
    p = MagicMock(spec=ItemContextProfile)
    p.source = source
    p.posterior_mean = posterior_mean
    p.posterior_precision = posterior_precision
    p.sample_count = sample_count
    p.correction_count = correction_count
    p.total_hours = total_hours
    p.confidence = confidence
    p.low_confidence_flag = low_confidence_flag
    p.observation_count = 1
    p.evidence_weight = confidence
    return p


# ─── build_compressed_context ─────────────────────────────────────────────────

class TestBuildCompressedContext:

    def test_schema_keys_always_present(self):
        ctx = build_compressed_context("Install crane panels")
        assert set(ctx.keys()) == {"phase", "spatial_type", "area_type", "work_type"}

    def test_phase_detection_structure(self):
        ctx = build_compressed_context("Pour Level 4 slab")
        assert ctx["phase"] == "structure"

    def test_phase_detection_services(self):
        ctx = build_compressed_context("Install HVAC ductwork")
        assert ctx["phase"] == "services"

    def test_phase_detection_prelims(self):
        ctx = build_compressed_context("Site mobilisation and hoarding")
        assert ctx["phase"] == "prelims"

    def test_phase_unknown_fallback(self):
        ctx = build_compressed_context("Generic activity XYZ")
        assert ctx["phase"] == "unknown"

    def test_zone_name_sets_spatial_type_to_zone(self):
        ctx = build_compressed_context("Install reo", zone_name="Zone A")
        assert ctx["spatial_type"] == "zone"

    def test_spatial_type_level_from_level_name(self):
        ctx = build_compressed_context("Pour slab", level_name="Level 4")
        assert ctx["spatial_type"] == "level"

    def test_area_type_basement(self):
        ctx = build_compressed_context("Install waterproofing", level_name="Basement B1")
        assert ctx["area_type"] == "basement"

    def test_area_type_defaults_to_internal(self):
        ctx = build_compressed_context("Fix formwork", level_name="Level 5")
        assert ctx["area_type"] == "internal"

    def test_work_type_column(self):
        ctx = build_compressed_context("Erect precast column grid line A")
        assert ctx["work_type"] == "column"

    def test_work_type_slab(self):
        ctx = build_compressed_context("Concrete pour slab area 3")
        assert ctx["work_type"] == "slab"

    def test_work_type_unknown_fallback(self):
        ctx = build_compressed_context("Generic crane lift")
        assert ctx["work_type"] == "unknown"


# ─── build_context_key ────────────────────────────────────────────────────────

class TestBuildContextKey:

    def test_deterministic(self):
        item_id = uuid.UUID("00000000-0000-0000-0000-000000000001")
        ctx = {"phase": "structure", "spatial_type": "level",
               "area_type": "internal", "work_type": "slab"}
        key1 = build_context_key(item_id, "crane", 5, ctx)
        key2 = build_context_key(item_id, "crane", 5, ctx)
        assert key1 == key2

    def test_length_is_64(self):
        key = build_context_key(uuid.uuid4(), "crane", 3, {})
        assert len(key) == 64

    def test_different_asset_type_gives_different_key(self):
        item_id = uuid.uuid4()
        ctx = {}
        assert build_context_key(item_id, "crane", 5, ctx) != build_context_key(item_id, "hoist", 5, ctx)

    def test_different_duration_gives_different_key(self):
        item_id = uuid.uuid4()
        ctx = {}
        assert build_context_key(item_id, "crane", 5, ctx) != build_context_key(item_id, "crane", 6, ctx)

    def test_different_item_id_gives_different_key(self):
        ctx = {}
        assert build_context_key(uuid.uuid4(), "crane", 5, ctx) != build_context_key(uuid.uuid4(), "crane", 5, ctx)

    def test_context_version_affects_key(self):
        item_id = uuid.uuid4()
        ctx = {}
        assert build_context_key(item_id, "crane", 5, ctx, context_version=1) != \
               build_context_key(item_id, "crane", 5, ctx, context_version=2)

    def test_inference_version_affects_key(self):
        item_id = uuid.uuid4()
        ctx = {}
        assert build_context_key(item_id, "crane", 5, ctx, inference_version=1) != \
               build_context_key(item_id, "crane", 5, ctx, inference_version=2)


# ─── work_profile_maturity ────────────────────────────────────────────────────

class TestWorkProfileMaturity:

    def test_manual_source_always_manual(self):
        p = _make_profile(source="manual")
        assert work_profile_maturity(p) == MATURITY_MANUAL

    def test_no_posterior_is_tentative(self):
        p = _make_profile(source="ai", posterior_mean=None, posterior_precision=None)
        assert work_profile_maturity(p) == MATURITY_TENTATIVE

    def test_zero_posterior_mean_is_tentative(self):
        p = _make_profile(source="ai", posterior_mean=0.0, posterior_precision=1.0)
        assert work_profile_maturity(p) == MATURITY_TENTATIVE

    def test_trusted_baseline_cv_below_010(self):
        # cv = (1/sqrt(pp)) / pm; want cv < 0.10
        # cv = sigma / pm → sigma = pm * 0.05 → pp = 1/(pm*0.05)^2
        pm = 10.0
        sigma = pm * 0.05  # cv = 0.05 < 0.10
        pp = 1.0 / (sigma ** 2)
        p = _make_profile(source="ai", posterior_mean=pm, posterior_precision=pp)
        assert work_profile_maturity(p) == MATURITY_TRUSTED_BASELINE

    def test_confirmed_cv_between_010_and_020(self):
        pm = 10.0
        sigma = pm * 0.15  # cv = 0.15
        pp = 1.0 / (sigma ** 2)
        p = _make_profile(source="ai", posterior_mean=pm, posterior_precision=pp)
        assert work_profile_maturity(p) == MATURITY_CONFIRMED

    def test_tentative_cv_above_020(self):
        pm = 10.0
        sigma = pm * 0.30  # cv = 0.30
        pp = 1.0 / (sigma ** 2)
        p = _make_profile(source="ai", posterior_mean=pm, posterior_precision=pp)
        assert work_profile_maturity(p) == MATURITY_TENTATIVE

    def test_correction_rate_override_downgrades_to_tentative(self):
        # High precision but correction_count/sample_count > 0.20 with sample_count >= 3
        pm = 10.0
        sigma = pm * 0.05  # would be TRUSTED_BASELINE without override
        pp = 1.0 / (sigma ** 2)
        p = _make_profile(
            source="ai", posterior_mean=pm, posterior_precision=pp,
            sample_count=5, correction_count=2,  # 2/5 = 0.40 > 0.20
        )
        assert work_profile_maturity(p) == MATURITY_TENTATIVE

    def test_correction_rate_no_override_below_threshold(self):
        pm = 10.0
        sigma = pm * 0.05
        pp = 1.0 / (sigma ** 2)
        p = _make_profile(
            source="ai", posterior_mean=pm, posterior_precision=pp,
            sample_count=10, correction_count=1,  # 1/10 = 0.10 < 0.20
        )
        assert work_profile_maturity(p) == MATURITY_TRUSTED_BASELINE

    def test_correction_rate_no_override_below_min_samples(self):
        # sample_count < 3 → override does not apply even if rate is high
        pm = 10.0
        sigma = pm * 0.05
        pp = 1.0 / (sigma ** 2)
        p = _make_profile(
            source="ai", posterior_mean=pm, posterior_precision=pp,
            sample_count=2, correction_count=1,  # 1/2 = 0.50 but sample_count < 3
        )
        assert work_profile_maturity(p) == MATURITY_TRUSTED_BASELINE


class TestFindTrustedBaseline:

    def test_requires_at_least_three_rows(self):
        db = MagicMock()
        row1 = _make_profile(source="learned", posterior_mean=10.0, sample_count=3)
        row2 = _make_profile(source="ai", posterior_mean=12.0, sample_count=4)
        db.query.return_value.filter.return_value.all.side_effect = [
            [],
            [row1, row2],
        ]

        baseline = _find_trusted_baseline(db, uuid.uuid4(), "crane", 5)

        assert baseline is None

    def test_uses_median_across_rows(self):
        db = MagicMock()
        row1 = _make_profile(source="learned", posterior_mean=10.0, sample_count=3)
        row2 = _make_profile(source="ai", posterior_mean=14.0, sample_count=4)
        row3 = _make_profile(source="manual", posterior_mean=None, total_hours=12.0, sample_count=5)
        db.query.return_value.filter.return_value.all.return_value = [row1, row2, row3]

        baseline = _find_trusted_baseline(db, uuid.uuid4(), "crane", 5)

        assert baseline == 12.0

    def test_manual_rows_bypass_sample_count_threshold(self):
        db = MagicMock()
        manual_row = _make_profile(source="manual", posterior_mean=None, total_hours=9.5, sample_count=0)
        learned_rows = [_make_profile(source="learned", posterior_mean=10.0, sample_count=3)]
        db.query.return_value.filter.return_value.all.side_effect = [
            [manual_row],
            learned_rows,
        ]

        baseline = _find_trusted_baseline(db, uuid.uuid4(), "crane", 5)

        assert baseline == 9.5


class TestReducedContextFallback:

    def test_base_context_only_drops_work_type_and_keeps_schema_fields(self):
        compressed = {
            "phase": "structure",
            "spatial_type": "level",
            "area_type": "internal",
            "work_type": "slab",
        }

        assert _base_context_only(compressed) == {
            "phase": "structure",
            "spatial_type": "level",
            "area_type": "internal",
        }

    def test_lookup_tries_base_context_after_exact_miss(self):
        compressed = {
            "phase": "structure",
            "spatial_type": "level",
            "area_type": "internal",
            "work_type": "slab",
        }
        item_id = uuid.uuid4()
        cached_profile = _make_profile(source="learned")

        with patch("app.services.work_profile_service.lookup_cache", side_effect=[None, cached_profile]) as lookup:
            cached, matched_hash = _lookup_cache_with_reduced_context(
                MagicMock(),
                item_id,
                "crane",
                5,
                compressed,
            )

        expected_base_hash = build_context_key(item_id, "crane", 5, _base_context_only(compressed))
        assert cached is cached_profile
        assert matched_hash == expected_base_hash
        assert lookup.call_count == 2


class TestUpdateCacheOnHit:

    def test_only_increments_observation_count(self):
        profile = _make_profile(
            source="ai",
            posterior_mean=10.0,
            posterior_precision=2.0,
            sample_count=4,
            confidence=0.8,
        )
        original_weight = profile.evidence_weight
        original_sample_count = profile.sample_count
        original_mean = profile.posterior_mean
        original_precision = profile.posterior_precision

        _update_cache_on_hit(MagicMock(), profile)

        assert profile.observation_count == 2
        assert profile.evidence_weight == original_weight
        assert profile.sample_count == original_sample_count
        assert profile.posterior_mean == original_mean
        assert profile.posterior_precision == original_precision

    @pytest.mark.parametrize("source", ["default", "manual"])
    def test_default_and_manual_entries_are_not_updated(self, source):
        profile = _make_profile(source=source)
        original_observation_count = profile.observation_count

        _update_cache_on_hit(MagicMock(), profile)

        assert profile.observation_count == original_observation_count


# ─── bayesian_update ──────────────────────────────────────────────────────────

class TestBayesianUpdate:

    def test_basic_update(self):
        # Starting: mean=10, precision=1 (sigma=1)
        # Observation: value=12, precision=4 (sigma=0.5)
        # new_precision = 1 + 4 = 5
        # new_mean = (1*10 + 4*12) / 5 = (10 + 48) / 5 = 11.6
        new_mean, new_prec = bayesian_update(10.0, 1.0, 12.0, 4.0)
        assert abs(new_prec - 5.0) < 1e-9
        assert abs(new_mean - 11.6) < 1e-9

    def test_zero_obs_precision_is_no_op(self):
        new_mean, new_prec = bayesian_update(10.0, 2.0, 15.0, 0.0)
        assert new_prec == 2.0
        assert new_mean == 10.0

    def test_high_obs_precision_dominates(self):
        # obs_precision much larger → new_mean approaches obs_value
        new_mean, new_prec = bayesian_update(10.0, 0.01, 20.0, 1000.0)
        assert abs(new_mean - 20.0) < 0.1

    def test_equal_precisions_averages(self):
        new_mean, new_prec = bayesian_update(10.0, 1.0, 20.0, 1.0)
        assert abs(new_mean - 15.0) < 1e-9
        assert abs(new_prec - 2.0) < 1e-9


# ─── _obs_precision ───────────────────────────────────────────────────────────

class TestObsPrecision:

    def test_ai_source_uses_20pct_sigma(self):
        value = 10.0
        expected = 1.0 / (value * 0.20) ** 2
        assert abs(_obs_precision(value, "ai") - expected) < 1e-9

    def test_actual_source_uses_5pct_sigma(self):
        value = 10.0
        expected = 1.0 / (value * 0.05) ** 2
        assert abs(_obs_precision(value, "actual") - expected) < 1e-9

    def test_actuals_have_16x_more_precision_than_ai(self):
        value = 10.0
        # (0.20 / 0.05)^2 = 16
        assert abs(_obs_precision(value, "actual") / _obs_precision(value, "ai") - 16.0) < 0.01

    def test_zero_value_returns_zero(self):
        assert _obs_precision(0.0, "ai") == 0.0

    def test_negative_value_returns_zero(self):
        assert _obs_precision(-5.0, "ai") == 0.0


# ─── _initial_posterior ───────────────────────────────────────────────────────

class TestInitialPosterior:

    def test_mean_equals_total_hours(self):
        pm, pp = _initial_posterior(10.0, 0.8)
        assert pm == 10.0

    def test_precision_uses_confidence(self):
        total_hours = 10.0
        confidence = 0.8
        # σ = 10 * (1 - 0.8) = 2.0  → pp = 1/4 = 0.25
        _, pp = _initial_posterior(total_hours, confidence)
        expected_pp = 1.0 / (total_hours * (1.0 - confidence)) ** 2
        assert abs(pp - expected_pp) < 1e-9

    def test_zero_hours_returns_zero_precision(self):
        pm, pp = _initial_posterior(0.0, 0.8)
        assert pm == 0.0
        assert pp == 0.0

    def test_full_confidence_fallback(self):
        # confidence=1.0 → sigma=0 → fallback to 1% uncertainty
        pm, pp = _initial_posterior(10.0, 1.0)
        assert pm == 10.0
        assert pp > 0


# ─── quantize_hours ───────────────────────────────────────────────────────────

class TestQuantizeHours:

    @pytest.mark.parametrize("input_h,expected", [
        (10.0,   10.0),
        (10.1,   10.0),
        (10.25,  10.5),
        (10.26,  10.5),
        (10.74,  10.5),
        (10.76,  11.0),
        (0.0,    0.0),
        (0.3,    0.5),
        (0.24,   0.0),
    ])
    def test_rounding(self, input_h, expected):
        assert quantize_hours(input_h) == expected


# ─── finalize_total_hours ─────────────────────────────────────────────────────

class TestFinalizeTotalHours:

    def test_none_asset_always_zero(self):
        assert finalize_total_hours(99.0, "none", 5, 0.0) == 0.0

    def test_manual_truth_wins(self):
        result = finalize_total_hours(5.0, "crane", 3, 10.0, manual_truth=7.0)
        assert result == 7.0

    def test_trusted_baseline_over_ai(self):
        result = finalize_total_hours(5.0, "crane", 3, 10.0, trusted_baseline=8.0)
        assert result == 8.0

    def test_manual_beats_baseline(self):
        result = finalize_total_hours(5.0, "crane", 3, 10.0,
                                      manual_truth=7.0, trusted_baseline=8.0)
        assert result == 7.0

    def test_ai_clamped_to_max(self):
        # max_hours_per_day=10, duration=2 → max=20; proposal=25 → clamped to 20
        result = finalize_total_hours(25.0, "crane", 2, 10.0)
        assert result == 20.0

    def test_ai_clamped_to_min(self):
        # proposal=0.1 → clamped to 0.5
        result = finalize_total_hours(0.1, "crane", 2, 10.0)
        assert result == 0.5

    def test_result_is_quantized(self):
        # 7.3 → nearest 0.5 = 7.5
        result = finalize_total_hours(7.3, "crane", 5, 10.0)
        assert result == 7.5

    def test_within_bounds_passed_through(self):
        result = finalize_total_hours(6.0, "crane", 3, 10.0)
        assert result == 6.0


# ─── derive_distribution / normalized ─────────────────────────────────────────

class TestDeriveDistribution:

    def test_basic_derivation(self):
        norm = [0.6, 0.4]
        dist = derive_distribution(norm, 10.0)
        assert len(dist) == 2
        assert abs(dist[0] - 6.0) < 1e-4
        assert abs(dist[1] - 4.0) < 1e-4

    def test_zero_hours_gives_zeros(self):
        dist = derive_distribution([0.5, 0.5], 0.0)
        assert dist == [0.0, 0.0]

    def test_normalize_round_trips(self):
        original = [6.0, 3.0, 1.0]
        norm = derive_normalized_distribution(original)
        assert abs(sum(norm) - 1.0) < 1e-5

    def test_normalize_zero_total_gives_zeros(self):
        norm = derive_normalized_distribution([0.0, 0.0, 0.0])
        assert norm == [0.0, 0.0, 0.0]

    def test_uniform_normalized_sums_to_one(self):
        for n in [1, 2, 3, 5, 6, 7, 9, 10, 12]:
            norm = _uniform_normalized(n)
            assert len(norm) == n
            assert abs(sum(norm) - 1.0) < WORK_PROFILE_NORM_DIST_SUM_TOLERANCE, (
                f"_uniform_normalized({n}) sums to {sum(norm)}, expected 1.0"
            )


# ─── validate_stage_b ─────────────────────────────────────────────────────────

class TestValidateStageB:

    def test_valid_profile_passes(self):
        dist = [8.0, 7.0, 5.0]
        result = validate_stage_b(dist, "crane", 10.0, 3)
        assert result.valid is True

    def test_bucket_exceeds_cap_fails(self):
        dist = [11.0, 5.0]  # 11 > 10
        result = validate_stage_b(dist, "crane", 10.0, 2)
        assert result.valid is False
        assert any("distribution[0]" in e for e in result.errors)

    def test_wrong_length_fails(self):
        dist = [5.0, 5.0]
        result = validate_stage_b(dist, "crane", 10.0, 3)  # expects length 3
        assert result.valid is False

    def test_none_asset_all_zero_passes(self):
        result = validate_stage_b([0.0, 0.0], "none", 0.0, 2)
        assert result.valid is True

    def test_none_asset_nonzero_fails(self):
        result = validate_stage_b([0.0, 1.0], "none", 0.0, 2)
        assert result.valid is False

    def test_negative_bucket_fails(self):
        result = validate_stage_b([5.0, -1.0], "crane", 10.0, 2)
        assert result.valid is False


# ─── validate_stage_d ─────────────────────────────────────────────────────────

class TestValidateStageD:

    def _valid_args(self):
        total_hours = 10.0
        norm = [0.6, 0.4]
        dist = derive_distribution(norm, total_hours)
        return total_hours, dist, norm, "crane", 2, 10.0

    def test_valid_profile_passes(self):
        result = validate_stage_d(*self._valid_args())
        assert result.valid is True

    def test_none_asset_zero_hours_passes(self):
        result = validate_stage_d(0.0, [0.0, 0.0], [0.0, 0.0], "none", 2, 0.0)
        assert result.valid is True

    def test_none_asset_nonzero_hours_fails(self):
        result = validate_stage_d(5.0, [5.0, 0.0], [1.0, 0.0], "none", 2, 0.0)
        assert result.valid is False

    def test_total_hours_exceeds_max_fails(self):
        # max = 10 * 2 = 20; total = 25
        norm = [0.5, 0.5]
        dist = derive_distribution(norm, 25.0)
        result = validate_stage_d(25.0, dist, norm, "crane", 2, 10.0)
        assert result.valid is False

    def test_not_quantized_fails(self):
        # 10.1 is not a multiple of 0.5
        norm = [0.6, 0.4]
        dist = [6.06, 4.04]
        result = validate_stage_d(10.1, dist, norm, "crane", 2, 10.0)
        assert result.valid is False

    def test_distribution_sum_mismatch_fails(self):
        norm = [0.6, 0.4]
        dist = [6.0, 3.0]  # sum = 9 ≠ total_hours = 10
        result = validate_stage_d(10.0, dist, norm, "crane", 2, 10.0)
        assert result.valid is False

    def test_normalized_sum_not_one_fails(self):
        norm = [0.6, 0.5]  # sum = 1.1 > 1
        dist = derive_distribution([0.6, 0.4], 10.0)
        result = validate_stage_d(10.0, dist, norm, "crane", 2, 10.0)
        assert result.valid is False

    def test_normalized_length_mismatch_fails(self):
        norm = [1.0]
        dist = [6.0, 4.0]
        result = validate_stage_d(10.0, dist, norm, "crane", 2, 10.0)
        assert result.valid is False
        assert any("normalized_distribution length" in e for e in result.errors)

    def test_distribution_bucket_above_cap_fails(self):
        norm = [0.7, 0.3]
        dist = [11.0, 4.0]
        result = validate_stage_d(15.0, dist, norm, "crane", 2, 10.0)
        assert result.valid is False
        assert any("max_hours_per_day" in e for e in result.errors)


# ─── redistribute_capped_distribution ─────────────────────────────────────────

class TestRedistributeCappedDistribution:

    def test_no_cap_violation_unchanged(self):
        dist = [5.0, 3.0, 2.0]
        adjusted, fallback = redistribute_capped_distribution(dist, 10.0, 10.0)
        assert not fallback
        assert all(v <= 10.0 for v in adjusted)

    def test_excess_redistributed_to_uncapped(self):
        # bucket[0]=12 > cap=10; bucket[1]=5 can absorb 2 extra
        dist = [12.0, 5.0]
        adjusted, fallback = redistribute_capped_distribution(dist, 10.0, 17.0)
        assert not fallback
        assert all(v <= 10.0 for v in adjusted)

    def test_all_at_cap_triggers_uniform_fallback(self):
        # Both buckets exceed cap and total > sum-of-caps, so no uncapped neighbours
        # can absorb the remaining hours → uniform fallback.
        # cap=10, total=25: after capping each to 10, sum=20 < 25, remaining=5
        # but all buckets are at cap → no uncapped_indices → fallback
        dist = [15.0, 15.0]
        adjusted, fallback = redistribute_capped_distribution(dist, 10.0, 25.0)
        assert fallback is True
        assert len(adjusted) == 2
    def test_uncapped_bucket_overflow_triggers_fallback(self):
        dist = [15.0, 5.0]
        adjusted, fallback = redistribute_capped_distribution(dist, 10.0, 35.0)
        assert fallback is True
        assert all(v <= 10.0 for v in adjusted)

    def test_redistribution_sum_is_preserved(self):
        dist = [12.0, 5.0]
        adjusted, fallback = redistribute_capped_distribution(dist, 10.0, 17.0)
        assert not fallback
        assert abs(sum(adjusted) - 17.0) < 0.01

    def test_uniform_fallback_respects_per_bucket_cap(self):
        dist = [20.0, 20.0]
        adjusted, fallback = redistribute_capped_distribution(dist, 10.0, 50.0)
        assert fallback is True
        assert all(v <= 10.0 for v in adjusted)


# ─── build_default_profile ────────────────────────────────────────────────────

class TestBuildDefaultProfile:

    def test_none_asset_all_zeros(self):
        total, dist, norm = build_default_profile("none", 3, 0.0)
        assert total == 0.0
        assert all(v == 0.0 for v in dist)
        assert all(v == 0.0 for v in norm)

    def test_non_none_has_positive_hours(self):
        total, dist, norm = build_default_profile("crane", 3, 10.0)
        assert total > 0

    def test_distribution_sums_to_total(self):
        total, dist, norm = build_default_profile("crane", 5, 10.0)
        assert abs(sum(dist) - total) < 0.01

    def test_normalized_sums_to_one(self):
        total, dist, norm = build_default_profile("crane", 5, 10.0)
        assert abs(sum(norm) - 1.0) < 1e-4

    def test_distribution_length_matches_duration(self):
        for dur in [1, 2, 3, 5, 10]:
            total, dist, norm = build_default_profile("crane", dur, 10.0)
            assert len(dist) == dur
            assert len(norm) == dur

    def test_respects_half_utilisation_convention(self):
        # Default is 0.5 × max_hours_per_day × duration_days, quantized
        total, _, _ = build_default_profile("crane", 4, 10.0)
        expected = quantize_hours(0.5 * 10.0 * 4)
        assert total == expected


class TestResolveWorkProfile:

    def test_trusted_baseline_writes_activity_profile_with_cache_source(self):
        db = MagicMock()
        activity_id = uuid.uuid4()
        item_id = uuid.uuid4()
        cache_row = MagicMock()
        cache_row.id = uuid.uuid4()
        written_profile = MagicMock()

        with patch("app.services.work_profile_service._lookup_cache_with_reduced_context", return_value=(None, "exact-hash")), \
             patch("app.services.work_profile_service._find_trusted_baseline", return_value=8.0), \
             patch("app.services.work_profile_service._write_cache_entry", return_value=cache_row), \
             patch("app.services.work_profile_service._write_activity_profile", return_value=written_profile) as write_activity, \
             patch("app.core.constants.get_max_hours_for_type", return_value=10.0):
            result = resolve_work_profile(
                db,
                activity_id=activity_id,
                item_id=item_id,
                asset_type="crane",
                duration_days=2,
                activity_name="Pour slab",
            )

        assert result is written_profile
        assert write_activity.call_args.kwargs["source"] == "cache"
        assert write_activity.call_args.kwargs["context_profile_id"] == cache_row.id
