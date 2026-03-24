"""
Unit tests for _normalize_for_dedup().

This function is used to collapse activity-name variants into a single
canonical form before the in-batch dedup step runs.  Two activities with
different raw names but the same semantic meaning should produce the same
normalized key so the AI is only called once per unique context.

Normalization rules:
  1. Lowercase the entire string
  2. Strip any leading P6 day-step prefix: "Day N - ", "Day N – ", "Day N-"
     (single or repeated, with hyphen / en-dash / em-dash)
  3. Collapse runs of 2+ internal spaces to a single space
  4. Strip leading/trailing whitespace

Prefixes like "Day 7 - " are added by schedulers when splitting a multi-day
activity into daily sub-tasks.  They carry no semantic content and would
otherwise cause the same work to be classified multiple times.
"""

import pytest
from app.services.ai_service import _normalize_for_dedup


class TestBasicNormalization:
    def test_already_lowercase_unchanged(self):
        assert _normalize_for_dedup("install precast panels") == "install precast panels"

    def test_uppercase_lowercased(self):
        assert _normalize_for_dedup("INSTALL PRECAST PANELS") == "install precast panels"

    def test_mixed_case_lowercased(self):
        assert _normalize_for_dedup("Install Precast Panels") == "install precast panels"

    def test_leading_trailing_whitespace_stripped(self):
        assert _normalize_for_dedup("  pour slab  ") == "pour slab"

    def test_internal_double_space_collapsed(self):
        assert _normalize_for_dedup("pour  slab  level 5") == "pour slab level 5"

    def test_empty_string(self):
        assert _normalize_for_dedup("") == ""

    def test_whitespace_only(self):
        assert _normalize_for_dedup("   ") == ""


class TestDayStepPrefixStripping:
    def test_day_n_hyphen_stripped(self):
        assert _normalize_for_dedup("Day 7 - Slab pour") == "slab pour"

    def test_day_n_endash_stripped(self):
        # en-dash (U+2013) used by some P6 exports
        assert _normalize_for_dedup("Day 7 \u2013 Slab pour") == "slab pour"

    def test_day_n_emdash_stripped(self):
        # em-dash (U+2014)
        assert _normalize_for_dedup("Day 7 \u2014 Slab pour") == "slab pour"

    def test_day_n_no_space_hyphen(self):
        # "Day 7-" without space after day number
        assert _normalize_for_dedup("Day 7-Slab pour") == "slab pour"

    def test_day_double_digit_stripped(self):
        assert _normalize_for_dedup("Day 14 - Commence precast install") == "commence precast install"

    def test_day_triple_digit_stripped(self):
        assert _normalize_for_dedup("Day 100 - Final inspection") == "final inspection"

    def test_repeated_day_prefix_stripped(self):
        # Some P6 exports nest multiple prefixes
        assert _normalize_for_dedup("Day 1 - Day 2 - Pour columns") == "pour columns"

    def test_no_prefix_unchanged(self):
        assert _normalize_for_dedup("Slab pour") == "slab pour"

    def test_day_prefix_case_insensitive(self):
        assert _normalize_for_dedup("DAY 3 - Slab pour") == "slab pour"

    def test_leading_whitespace_before_day_prefix_stripped(self):
        # Input with leading spaces — the Day-N prefix must still be removed.
        assert _normalize_for_dedup("  Day 7 - Slab pour") == "slab pour"

    def test_leading_whitespace_no_prefix(self):
        assert _normalize_for_dedup("  Pour slab  ") == "pour slab"


class TestDedupCollapse:
    """
    Confirm that variant phrasings of the same activity collapse to the same key.
    This is the core purpose of the function: activities from different uploads
    that describe the same work must produce identical normalized keys.
    """

    def test_day_prefixed_and_bare_are_equal(self):
        assert _normalize_for_dedup("Day 7 - Slab pour") == _normalize_for_dedup("Slab pour")

    def test_different_day_numbers_same_work_are_equal(self):
        assert (
            _normalize_for_dedup("Day 3 - Commence precast install")
            == _normalize_for_dedup("Day 5 - Commence precast install")
        )

    def test_case_variants_are_equal(self):
        assert _normalize_for_dedup("Slab Pour") == _normalize_for_dedup("slab pour")

    def test_extra_spaces_collapse_to_same_key(self):
        assert _normalize_for_dedup("pour  slab") == _normalize_for_dedup("pour slab")
