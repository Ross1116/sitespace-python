"""
Unit tests for Stage 1 row typing and row confidence helpers.

Covers:
  - classify_row_kind: summary / milestone / task detection
  - score_row_confidence: high / medium / low scoring
"""

import pytest
from app.services.ai_service import classify_row_kind, score_row_confidence


# ---------------------------------------------------------------------------
# classify_row_kind
# ---------------------------------------------------------------------------

class TestClassifyRowKind:

    @pytest.mark.parametrize("is_summary,start,finish,expected", [
        # is_summary flag wins regardless of dates
        (True,  "2025-01-01", "2025-01-10", "summary"),
        (True,  None,         None,         "summary"),
        (True,  "2025-01-01", "2025-01-01", "summary"),  # would be milestone if not summary

        # milestone: non-summary + start == finish (zero-duration)
        (False, "2025-01-15", "2025-01-15", "milestone"),

        # task: non-summary + start != finish
        (False, "2025-01-01", "2025-01-10", "task"),

        # task: non-summary + one or both dates missing
        (False, "2025-01-01", None,          "task"),
        (False, None,         "2025-01-10",  "task"),
        (False, None,         None,          "task"),
    ])
    def test_kind(self, is_summary, start, finish, expected):
        assert classify_row_kind(is_summary=is_summary, start=start, finish=finish) == expected


# ---------------------------------------------------------------------------
# score_row_confidence
# ---------------------------------------------------------------------------

class TestScoreRowConfidence:

    @pytest.mark.parametrize("name,start,finish,kind,expected", [
        # high: name + both dates
        ("Install formwork L3", "2025-02-01", "2025-02-05", "task",    "high"),
        ("STRUCTURE COMPLETE",  "2025-06-01", "2025-06-01", "milestone", "high"),

        # medium: name + only one date (task)
        ("Pour slab",           "2025-02-01", None,          "task",    "medium"),
        ("Strip formwork",      None,          "2025-02-10", "task",    "medium"),

        # milestones: high when at least one date present (start==finish in practice)
        ("Project start",       "2025-01-06", "2025-01-06", "milestone", "high"),
        # milestone with no dates at all → medium
        ("Handover",            None,          None,         "milestone", "medium"),

        # low: blank name
        ("",                    "2025-01-01", "2025-01-10", "task",    "low"),
        ("   ",                 "2025-01-01", "2025-01-10", "task",    "low"),

        # low: task with no dates at all
        ("Pour slab",           None,          None,         "task",    "low"),
        ("Pour slab",           None,          None,         "summary", "low"),
    ])
    def test_confidence(self, name, start, finish, kind, expected):
        assert score_row_confidence(name=name, start=start, finish=finish, activity_kind=kind) == expected
