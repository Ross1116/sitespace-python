"""
Unit tests for the lookahead demand engine.

Covers:
  - _iter_weekly_activity_hours: splits an activity date range into per-week
    demand buckets at 8 hours per calendar day.
  - _compute_anomaly_flags: compares two snapshots and raises threshold flags.

NOTE -- known limitation in _iter_weekly_activity_hours:
  The current implementation counts ALL calendar days (including Saturday and
  Sunday) at 8 h/day.  A 5-day Mon-Fri activity produces 40 h, which is correct,
  but a 7-day Mon-Sun activity produces 56 h rather than 40 h.

  Stage 1 will add `work_days_per_week` to projects/uploads so the demand
  engine can exclude non-working days.  Tests that document this behaviour are
  marked with a comment: "documents current (pre-Stage-1) behaviour".
"""

import pytest
from datetime import date, time
from app.services.lookahead_engine import (
    _iter_weekly_activity_hours,
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

    def test_full_calendar_week_mon_to_sun(self):
        # Documents current (pre-Stage-1) behaviour:
        # weekends are counted at 8h/day.  After Stage 1 adds
        # work_days_per_week, a 5d/week project would return 40h here.
        result = _iter_weekly_activity_hours(MON, SUN)
        assert result == [(MON, 56.0)]

    def test_activity_spans_two_weeks(self):
        # Mon-Wed of next week: 7 days in week 1, 3 days in week 2
        result = _iter_weekly_activity_hours(MON, WED2)
        assert len(result) == 2
        assert result[0] == (MON, 56.0)   # Mon-Sun = 7 days
        assert result[1] == (MON2, 24.0)  # Mon-Wed = 3 days

    def test_activity_mid_week_start_spans_two_partial_weeks(self):
        # FRI -> WED2 (6 calendar days): 3 tail days in week 1, 3 head days in week 2
        result = _iter_weekly_activity_hours(FRI, WED2)
        assert len(result) == 2
        # Week 1: Fri + Sat + Sun = 3 days
        assert result[0] == (MON, 24.0)
        # Week 2: Mon + Tue + Wed = 3 days
        assert result[1] == (MON2, 24.0)

    def test_cross_week_start_saturday(self):
        # Activity starting Sat -- falls in week starting Mon before it
        result = _iter_weekly_activity_hours(SAT, MON2)
        assert len(result) == 2
        assert result[0][0] == MON        # week containing Sat
        assert result[0][1] == 16.0       # Sat + Sun = 2 days
        assert result[1][0] == MON2
        assert result[1][1] == 8.0        # just Mon

    def test_reversed_dates_handled(self):
        # The function normalises min/max, so reversed dates work identically
        forward = _iter_weekly_activity_hours(MON, FRI)
        reversed_ = _iter_weekly_activity_hours(FRI, MON)
        assert forward == reversed_

    def test_total_hours_preserved_across_weeks(self):
        # Sum of all bucket hours must equal total_days * 8
        start, end = MON, WED2   # 10 calendar days
        buckets = _iter_weekly_activity_hours(start, end)
        total = sum(h for _, h in buckets)
        days = (end - start).days + 1
        assert total == days * 8.0

    def test_week_starts_are_mondays(self):
        # All returned week-start dates must be Mondays (weekday() == 0)
        buckets = _iter_weekly_activity_hours(SAT, WED2)
        for week_start, _ in buckets:
            assert week_start.weekday() == 0, f"{week_start} is not a Monday"

    def test_empty_result_impossible(self):
        # Any valid date range produces at least one bucket
        result = _iter_weekly_activity_hours(TUE, TUE)
        assert len(result) >= 1

    def test_long_activity_ten_weeks(self):
        end = date(2026, 5, 25)  # ~10 weeks after MON
        buckets = _iter_weekly_activity_hours(MON, end)
        assert len(buckets) == 11
        total = sum(h for _, h in buckets)
        days = (end - MON).days + 1
        assert total == days * 8.0


# ---------------------------------------------------------------------------
# _compute_anomaly_flags
# ---------------------------------------------------------------------------

class TestAnomalyFlags:
    """
    Thresholds from the architecture plan:
      demand_spike_over_100pct       : pct_change > 1.0  (strictly greater)
      mapping_changes_over_40pct     : ratio >= 0.4       (greater-or-equal)
      activity_count_delta_over_30pct: delta_ratio > 0.3  (strictly greater)
    """

    def _make_row(self, asset_type: str, week_start: str, demand_hours: float) -> dict:
        return {"asset_type": asset_type, "week_start": week_start, "demand_hours": demand_hours}

    def _make_mapping_set(self, mappings: list[tuple[str, str]]) -> set[tuple[str, str]]:
        return set(mappings)

    # ---- demand spike ----

    def test_demand_spike_triggers_above_100pct(self):
        prev = [self._make_row("crane", "2026-03-16", 100.0)]
        curr = [self._make_row("crane", "2026-03-16", 201.0)]
        flags = _compute_anomaly_flags(prev, curr, 10, 10, set(), set())
        assert flags["demand_spike_over_100pct"] is True

    def test_demand_spike_exact_100pct_does_not_trigger(self):
        # pct_change = 1.0, test is > 1.0 -> should NOT fire
        prev = [self._make_row("crane", "2026-03-16", 100.0)]
        curr = [self._make_row("crane", "2026-03-16", 200.0)]
        flags = _compute_anomaly_flags(prev, curr, 10, 10, set(), set())
        assert flags["demand_spike_over_100pct"] is False

    def test_demand_spike_below_100pct_no_flag(self):
        prev = [self._make_row("crane", "2026-03-16", 100.0)]
        curr = [self._make_row("crane", "2026-03-16", 150.0)]
        flags = _compute_anomaly_flags(prev, curr, 10, 10, set(), set())
        assert flags["demand_spike_over_100pct"] is False

    def test_demand_spike_no_previous_data_no_flag(self):
        curr = [self._make_row("crane", "2026-03-16", 200.0)]
        flags = _compute_anomaly_flags([], curr, 0, 10, set(), set())
        assert flags["demand_spike_over_100pct"] is False

    def test_demand_spike_decrease_does_not_trigger(self):
        prev = [self._make_row("crane", "2026-03-16", 200.0)]
        curr = [self._make_row("crane", "2026-03-16", 50.0)]
        flags = _compute_anomaly_flags(prev, curr, 10, 10, set(), set())
        # pct_change = |50-200|/200 = 0.75, not > 1.0
        assert flags["demand_spike_over_100pct"] is False

    def test_demand_spike_multiple_asset_types_one_triggers(self):
        prev = [
            self._make_row("crane", "2026-03-16", 100.0),
            self._make_row("forklift", "2026-03-16", 20.0),
        ]
        curr = [
            self._make_row("crane", "2026-03-16", 100.0),   # no change
            self._make_row("forklift", "2026-03-16", 50.0),  # 150% spike
        ]
        flags = _compute_anomaly_flags(prev, curr, 10, 10, set(), set())
        assert flags["demand_spike_over_100pct"] is True

    # ---- mapping changes ----

    def test_mapping_changes_exact_40pct_triggers(self):
        # 4 activities changed out of 10 total = 0.4 >= 0.4 -> True
        prev_set = {(f"act{i}", "crane") for i in range(10)}
        curr_set = {(f"act{i}", "forklift" if i < 4 else "crane") for i in range(10)}
        flags = _compute_anomaly_flags([], [], 10, 10, prev_set, curr_set)
        assert flags["mapping_changes_over_40pct"] is True

    def test_mapping_changes_below_40pct_no_flag(self):
        # 3 of 10 changed = 0.3 < 0.4 -> False
        prev_set = {(f"act{i}", "crane") for i in range(10)}
        curr_set = {(f"act{i}", "forklift" if i < 3 else "crane") for i in range(10)}
        flags = _compute_anomaly_flags([], [], 10, 10, prev_set, curr_set)
        assert flags["mapping_changes_over_40pct"] is False

    def test_mapping_changes_zero_previous_no_flag(self):
        flags = _compute_anomaly_flags([], [], 10, 10, set(), set())
        assert flags["mapping_changes_over_40pct"] is False

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
        assert flags["demand_spike_over_100pct"] is False
        assert flags["mapping_changes_over_40pct"] is False
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

    def test_below_8_is_low(self):
        assert _demand_level(7.9) == "low"

    def test_exactly_8_is_medium(self):
        assert _demand_level(8.0) == "medium"

    def test_between_8_and_20_is_medium(self):
        assert _demand_level(16.0) == "medium"

    def test_below_20_is_medium(self):
        assert _demand_level(19.9) == "medium"

    def test_exactly_20_is_high(self):
        assert _demand_level(20.0) == "high"

    def test_between_20_and_40_is_high(self):
        assert _demand_level(30.0) == "high"

    def test_below_40_is_high(self):
        assert _demand_level(39.9) == "high"

    def test_exactly_40_is_critical(self):
        assert _demand_level(40.0) == "critical"

    def test_above_40_is_critical(self):
        assert _demand_level(80.0) == "critical"

    def test_boundary_coverage(self):
        # Confirm the four tiers are the only possible values
        valid = {"low", "medium", "high", "critical"}
        for hours in [0, 4, 8, 12, 20, 32, 40, 56, 100]:
            assert _demand_level(float(hours)) in valid
