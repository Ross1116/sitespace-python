"""
Unit tests for the lookahead demand engine.

Covers:
  - _working_days_in_range: counts Mon-Fri / Mon-Sat / all-days within a range.
  - _iter_weekly_activity_hours: splits an activity date range into per-week
    demand buckets at 8h/working-day, respecting work_days_per_week.
  - _compute_anomaly_flags: compares two snapshots and raises threshold flags.
"""

import pytest
from datetime import date, time
from app.services.lookahead_engine import (
    _iter_weekly_activity_hours,
    _working_days_in_range,
    _compute_anomaly_flags,
    _week_start,
    _hours_between,
    _demand_level,
)


# Reference dates -- all in week of Mon 2026-03-16
MON = date(2026, 3, 16)
TUE = date(2026, 3, 17)
WED = date(2026, 3, 18)
THU = date(2026, 3, 19)
FRI = date(2026, 3, 20)
SAT = date(2026, 3, 21)
SUN = date(2026, 3, 22)
MON2 = date(2026, 3, 23)
TUE2 = date(2026, 3, 24)
WED2 = date(2026, 3, 25)
MON3 = date(2026, 3, 30)


# ---------------------------------------------------------------------------
# _working_days_in_range
# ---------------------------------------------------------------------------

class TestWorkingDaysInRange:
    def test_full_week_five_days(self):
        # Mon-Sun: 5 working days (Mon-Fri)
        assert _working_days_in_range(MON, SUN, 5) == 5

    def test_full_week_six_days(self):
        # Mon-Sun: 6 working days (Mon-Sat)
        assert _working_days_in_range(MON, SUN, 6) == 6

    def test_full_week_seven_days(self):
        assert _working_days_in_range(MON, SUN, 7) == 7

    def test_weekend_only_five_days(self):
        assert _working_days_in_range(SAT, SUN, 5) == 0

    def test_saturday_included_in_six_days(self):
        assert _working_days_in_range(SAT, SAT, 6) == 1

    def test_saturday_excluded_in_five_days(self):
        assert _working_days_in_range(SAT, SAT, 5) == 0

    def test_single_monday(self):
        assert _working_days_in_range(MON, MON, 5) == 1

    def test_mon_to_fri(self):
        assert _working_days_in_range(MON, FRI, 5) == 5


# ---------------------------------------------------------------------------
# _iter_weekly_activity_hours
# ---------------------------------------------------------------------------

class TestWeeklyBuckets:
    def test_single_day_activity(self):
        result = _iter_weekly_activity_hours(MON, MON)
        assert result == [(MON, 8.0)]

    def test_single_day_midweek(self):
        result = _iter_weekly_activity_hours(WED, WED)
        assert result == [(MON, 8.0)]

    def test_five_day_mon_to_fri(self):
        result = _iter_weekly_activity_hours(MON, FRI)
        assert result == [(MON, 40.0)]

    def test_full_calendar_week_five_day(self):
        # Mon-Sun with 5d/week: only Mon-Fri = 40h, weekend skipped
        result = _iter_weekly_activity_hours(MON, SUN, work_days_per_week=5)
        assert result == [(MON, 40.0)]

    def test_full_calendar_week_six_day(self):
        # Mon-Sun with 6d/week: Mon-Sat = 48h
        result = _iter_weekly_activity_hours(MON, SUN, work_days_per_week=6)
        assert result == [(MON, 48.0)]

    def test_full_calendar_week_seven_day(self):
        # Mon-Sun with 7d/week: all 7 days = 56h
        result = _iter_weekly_activity_hours(MON, SUN, work_days_per_week=7)
        assert result == [(MON, 56.0)]

    def test_activity_spans_two_weeks_five_day(self):
        # MON to WED2 (MON + full week + Mon/Tue/Wed)
        result = _iter_weekly_activity_hours(MON, WED2)
        assert len(result) == 2
        assert result[0] == (MON, 40.0)   # Mon-Fri = 5 working days
        assert result[1] == (MON2, 24.0)  # Mon-Wed = 3 working days

    def test_activity_mid_week_start_spans_two_partial_weeks(self):
        # FRI to WED2 with 5d/week
        result = _iter_weekly_activity_hours(FRI, WED2)
        assert len(result) == 2
        assert result[0] == (MON, 8.0)    # only Friday is a working day in week 1
        assert result[1] == (MON2, 24.0)  # Mon-Wed = 3 working days

    def test_cross_week_start_saturday_five_day(self):
        # SAT to MON2 with 5d/week: Sat+Sun are not working, only MON2 counts
        result = _iter_weekly_activity_hours(SAT, MON2)
        assert len(result) == 1
        assert result[0] == (MON2, 8.0)

    def test_cross_week_start_saturday_six_day(self):
        # SAT to MON2 with 6d/week: Sat counts, Sun doesn't, MON2 counts
        result = _iter_weekly_activity_hours(SAT, MON2, work_days_per_week=6)
        assert len(result) == 2
        assert result[0] == (MON, 8.0)    # Sat = 1 working day in week 1
        assert result[1] == (MON2, 8.0)

    def test_weekend_only_span_produces_no_buckets(self):
        # Sat-Sun with 5d/week: no working days -> empty result
        result = _iter_weekly_activity_hours(SAT, SUN, work_days_per_week=5)
        assert result == []

    def test_reversed_dates_handled(self):
        forward = _iter_weekly_activity_hours(MON, FRI)
        reversed_ = _iter_weekly_activity_hours(FRI, MON)
        assert forward == reversed_

    def test_total_hours_five_day_week(self):
        # MON to WED2 = 5 + 3 working days = 64h total
        buckets = _iter_weekly_activity_hours(MON, WED2)
        assert sum(h for _, h in buckets) == 64.0

    def test_week_starts_are_mondays(self):
        buckets = _iter_weekly_activity_hours(SAT, WED2, work_days_per_week=6)
        for week_start, _ in buckets:
            assert week_start.weekday() == 0, f"{week_start} is not a Monday"

    def test_weekday_produces_at_least_one_bucket(self):
        # A Tuesday always gives at least one bucket with default 5d/week
        result = _iter_weekly_activity_hours(TUE, TUE)
        assert len(result) >= 1

    def test_long_activity_ten_weeks_working_hours(self):
        # MON 2026-03-16 to MON 2026-05-25 = 11 weeks
        end = date(2026, 5, 25)
        buckets = _iter_weekly_activity_hours(MON, end)
        assert len(buckets) == 11
        # Each full week has 5 working days; last week has 1 day (Mon only)
        total = sum(h for _, h in buckets)
        assert total == (10 * 5 + 1) * 8.0  # 51 working days * 8h


# ---------------------------------------------------------------------------
# _compute_anomaly_flags
# ---------------------------------------------------------------------------

class TestAnomalyFlags:
    """
    Thresholds (sourced from core.constants):
      demand_spike_over_150pct       : pct_change > 1.5  (strictly greater - 2.5x demand)
      mapping_changes_over_50pct     : ratio >= 0.5       (greater-or-equal - half rewired)
      activity_count_delta_over_30pct: delta_ratio > 0.3  (strictly greater)
    """

    def _make_row(self, asset_type: str, week_start: str, demand_hours: float) -> dict:
        return {"asset_type": asset_type, "week_start": week_start, "demand_hours": demand_hours}

    def _make_mapping_set(self, mappings: list[tuple[str, str]]) -> set[tuple[str, str]]:
        return set(mappings)

    # ---- demand spike ----

    def test_demand_spike_triggers_above_150pct(self):
        # pct_change = (251 - 100) / 100 = 1.51 > 1.5 -> True
        prev = [self._make_row("crane", "2026-03-16", 100.0)]
        curr = [self._make_row("crane", "2026-03-16", 251.0)]
        flags = _compute_anomaly_flags(prev, curr, 10, 10, set(), set())
        assert flags["demand_spike_over_150pct"] is True

    def test_demand_spike_exact_150pct_does_not_trigger(self):
        # pct_change = 1.5, threshold is > 1.5 -> should NOT fire
        prev = [self._make_row("crane", "2026-03-16", 100.0)]
        curr = [self._make_row("crane", "2026-03-16", 250.0)]
        flags = _compute_anomaly_flags(prev, curr, 10, 10, set(), set())
        assert flags["demand_spike_over_150pct"] is False

    def test_demand_spike_below_150pct_no_flag(self):
        # pct_change = 0.5 -> well below threshold
        prev = [self._make_row("crane", "2026-03-16", 100.0)]
        curr = [self._make_row("crane", "2026-03-16", 150.0)]
        flags = _compute_anomaly_flags(prev, curr, 10, 10, set(), set())
        assert flags["demand_spike_over_150pct"] is False

    def test_demand_spike_no_previous_data_no_flag(self):
        curr = [self._make_row("crane", "2026-03-16", 200.0)]
        flags = _compute_anomaly_flags([], curr, 0, 10, set(), set())
        assert flags["demand_spike_over_150pct"] is False

    def test_demand_spike_decrease_does_not_trigger(self):
        prev = [self._make_row("crane", "2026-03-16", 200.0)]
        curr = [self._make_row("crane", "2026-03-16", 50.0)]
        flags = _compute_anomaly_flags(prev, curr, 10, 10, set(), set())
        # pct_change = |50-200|/200 = 0.75, not > 1.5
        assert flags["demand_spike_over_150pct"] is False

    def test_demand_spike_multiple_asset_types_one_triggers(self):
        prev = [
            self._make_row("crane", "2026-03-16", 100.0),
            self._make_row("forklift", "2026-03-16", 20.0),
        ]
        curr = [
            self._make_row("crane", "2026-03-16", 100.0),   # no change
            self._make_row("forklift", "2026-03-16", 51.0),  # 155% spike > 150% threshold
        ]
        flags = _compute_anomaly_flags(prev, curr, 10, 10, set(), set())
        assert flags["demand_spike_over_150pct"] is True

    # ---- mapping changes ----

    def test_mapping_changes_exact_50pct_triggers(self):
        # 5 activities changed out of 10 total = 0.5 >= 0.5 -> True
        prev_set = {(f"act{i}", "crane") for i in range(10)}
        curr_set = {(f"act{i}", "forklift" if i < 5 else "crane") for i in range(10)}
        flags = _compute_anomaly_flags([], [], 10, 10, prev_set, curr_set)
        assert flags["mapping_changes_over_50pct"] is True

    def test_mapping_changes_below_50pct_no_flag(self):
        # 4 of 10 changed = 0.4 < 0.5 -> False
        prev_set = {(f"act{i}", "crane") for i in range(10)}
        curr_set = {(f"act{i}", "forklift" if i < 4 else "crane") for i in range(10)}
        flags = _compute_anomaly_flags([], [], 10, 10, prev_set, curr_set)
        assert flags["mapping_changes_over_50pct"] is False

    def test_mapping_changes_zero_previous_no_flag(self):
        flags = _compute_anomaly_flags([], [], 10, 10, set(), set())
        assert flags["mapping_changes_over_50pct"] is False

    def test_mapping_change_ratio_stored(self):
        prev_set = {(f"act{i}", "crane") for i in range(10)}
        curr_set = {(f"act{i}", "forklift" if i < 5 else "crane") for i in range(10)}
        flags = _compute_anomaly_flags([], [], 10, 10, prev_set, curr_set)
        assert "mapping_change_ratio" in flags
        assert abs(flags["mapping_change_ratio"] - 0.5) < 0.001

    # ---- activity count delta ----

    def test_activity_count_delta_triggers_above_30pct(self):
        # 131 vs 100 = 31% > 30% -> True
        flags = _compute_anomaly_flags([], [], 100, 131, set(), set())
        assert flags["activity_count_delta_over_30pct"] is True

    def test_activity_count_delta_exact_30pct_no_flag(self):
        # 130 vs 100 = 30%, NOT > 30% -> False
        flags = _compute_anomaly_flags([], [], 100, 130, set(), set())
        assert flags["activity_count_delta_over_30pct"] is False

    def test_activity_count_delta_below_30pct_no_flag(self):
        flags = _compute_anomaly_flags([], [], 100, 120, set(), set())
        assert flags["activity_count_delta_over_30pct"] is False

    def test_activity_count_delta_zero_previous_no_flag(self):
        flags = _compute_anomaly_flags([], [], 0, 100, set(), set())
        assert flags["activity_count_delta_over_30pct"] is False

    def test_activity_count_delta_decrease_triggers(self):
        # |70 - 100| / 100 = 0.3, NOT > 0.3 -> False
        flags = _compute_anomaly_flags([], [], 100, 70, set(), set())
        assert flags["activity_count_delta_over_30pct"] is False

    def test_activity_count_delta_large_decrease_triggers(self):
        # |60 - 100| / 100 = 0.4 > 0.3 -> True
        flags = _compute_anomaly_flags([], [], 100, 60, set(), set())
        assert flags["activity_count_delta_over_30pct"] is True

    def test_all_flags_false_by_default(self):
        flags = _compute_anomaly_flags([], [], 50, 50, set(), set())
        assert flags["demand_spike_over_150pct"] is False
        assert flags["mapping_changes_over_50pct"] is False
        assert flags["activity_count_delta_over_30pct"] is False


# ---------------------------------------------------------------------------
# _week_start
# ---------------------------------------------------------------------------

class TestWeekStart:
    def test_monday_returns_self(self):
        assert _week_start(MON) == MON

    def test_wednesday_returns_monday(self):
        assert _week_start(WED) == MON

    def test_friday_returns_monday(self):
        assert _week_start(FRI) == MON

    def test_saturday_returns_monday(self):
        assert _week_start(SAT) == MON

    def test_sunday_returns_monday(self):
        assert _week_start(SUN) == MON

    def test_next_monday_returns_itself(self):
        assert _week_start(MON2) == MON2

    def test_result_is_always_monday(self):
        for d in [MON, TUE, WED, THU, FRI, SAT, SUN, MON2, TUE2, WED2]:
            assert _week_start(d).weekday() == 0


# ---------------------------------------------------------------------------
# _hours_between
# ---------------------------------------------------------------------------

class TestHoursBetween:
    def test_same_time_returns_24h(self):
        # end <= start -> wraps to next day
        assert _hours_between(time(8, 0), time(8, 0)) == 24.0

    def test_eight_to_five(self):
        assert _hours_between(time(8, 0), time(17, 0)) == 9.0

    def test_eight_to_four(self):
        assert _hours_between(time(8, 0), time(16, 0)) == 8.0

    def test_midnight_to_midnight_wraps(self):
        # end == start, wraps to full day
        assert _hours_between(time(0, 0), time(0, 0)) == 24.0

    def test_overnight_shift(self):
        # 22:00 -> 06:00 = 8 hours overnight
        assert _hours_between(time(22, 0), time(6, 0)) == 8.0

    def test_partial_hours(self):
        assert _hours_between(time(8, 0), time(8, 30)) == 0.5

    def test_end_before_start_wraps(self):
        # 17:00 -> 08:00 = 15 hours (overnight)
        assert _hours_between(time(17, 0), time(8, 0)) == 15.0


# ---------------------------------------------------------------------------
# _demand_level
# ---------------------------------------------------------------------------

class TestDemandLevel:
    def test_zero_hours_is_low(self):
        assert _demand_level(0.0) == "low"

    def test_below_16_is_low(self):
        assert _demand_level(15.9) == "low"

    def test_exactly_16_is_medium(self):
        # low/medium boundary: < 16 h = low, ≥ 16 h = medium
        assert _demand_level(16.0) == "medium"

    def test_between_16_and_32_is_medium(self):
        assert _demand_level(24.0) == "medium"

    def test_below_32_is_medium(self):
        assert _demand_level(31.9) == "medium"

    def test_exactly_32_is_high(self):
        # medium/high boundary: < 32 h = medium, ≥ 32 h = high
        assert _demand_level(32.0) == "high"

    def test_between_32_and_64_is_high(self):
        assert _demand_level(40.0) == "high"

    def test_below_64_is_high(self):
        assert _demand_level(63.9) == "high"

    def test_exactly_64_is_critical(self):
        # high/critical boundary: < 64 h = high, ≥ 64 h = critical
        # 64 h = one full single-asset week even on longer work-week calendars
        assert _demand_level(64.0) == "critical"

    def test_above_64_is_critical(self):
        assert _demand_level(80.0) == "critical"

    def test_boundary_coverage(self):
        # Confirm the four tiers are the only possible values
        valid = {"low", "medium", "high", "critical"}
        for hours in [0, 8, 16, 24, 32, 40, 64, 72, 100]:
            assert _demand_level(float(hours)) in valid
