"""
Unit tests for the classification confidence policy in _write_classifications()
and the source normalizer _normalize_mapping_source().

Confidence policy rules (from the architecture plan and inline docstring):
  high   → ActivityAssetMapping(auto_committed=True,  asset_type=<value>)
  medium → ActivityAssetMapping(auto_committed=True,  asset_type=<value>)
  low    → ActivityAssetMapping(auto_committed=False, asset_type=None)
  invalid/unknown → treated as "low"

Skipped activities (classification.skipped list):
  → ActivityAssetMapping(auto_committed=False, asset_type=None, confidence="low")

AISuggestionLog rows:
  → Created for every classification item AND every skipped activity
  → accepted mirrors auto_committed
  → observability fields (upload_id, pipeline_stage, model_name, fallback_used)
    are passed through from the keyword args
  → model_name forced to None when fallback_used=True

Items with none/empty asset_type:
  → Skipped entirely — no mapping or suggestion row written

_normalize_mapping_source():
  "ai"             → "ai"
  "keyword_boost"  → "keyword"
  "keyword_fallback" → "keyword"
  "keyword"        → "keyword"
  "manual"         → "manual"
  None / ""        → "ai"
  unknown          → "ai"
"""

import uuid
from unittest.mock import MagicMock

import pytest

from app.models.programme import ActivityAssetMapping, AISuggestionLog
from app.services.ai_service import ClassificationItem, ClassificationResult
from app.services.process_programme import _normalize_mapping_source, _write_classifications


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _item(asset_type: str | None, confidence: str, source: str = "ai") -> ClassificationItem:
    return ClassificationItem(
        activity_id=str(uuid.uuid4()),
        asset_type=asset_type,
        confidence=confidence,
        source=source,
    )


def _run(classification: ClassificationResult, **kwargs):
    """
    Run _write_classifications with a mock DB and return
    (mapping_rows, suggestion_rows) as plain lists.
    """
    db = MagicMock()
    captured: list = []
    db.bulk_save_objects.side_effect = lambda objs: captured.extend(objs)
    _write_classifications(classification, db, **kwargs)
    mappings = [o for o in captured if isinstance(o, ActivityAssetMapping)]
    suggestions = [o for o in captured if isinstance(o, AISuggestionLog)]
    return mappings, suggestions


def _result(*items: ClassificationItem, skipped=None) -> ClassificationResult:
    return ClassificationResult(
        classifications=list(items),
        skipped=skipped or [],
    )


# ---------------------------------------------------------------------------
# Confidence policy — auto_committed flag
# ---------------------------------------------------------------------------

class TestAutoCommitPolicy:
    def test_high_confidence_is_auto_committed(self):
        mappings, _ = _run(_result(_item("crane", "high")))
        assert mappings[0].auto_committed is True

    def test_medium_confidence_is_auto_committed(self):
        mappings, _ = _run(_result(_item("crane", "medium")))
        assert mappings[0].auto_committed is True

    def test_low_confidence_is_not_auto_committed(self):
        mappings, _ = _run(_result(_item("crane", "low")))
        assert mappings[0].auto_committed is False

    def test_invalid_confidence_defaults_to_low_not_committed(self):
        mappings, _ = _run(_result(_item("crane", "UNKNOWN")))
        assert mappings[0].auto_committed is False
        assert mappings[0].confidence == "low"

    def test_empty_confidence_defaults_to_low(self):
        mappings, _ = _run(_result(_item("crane", "")))
        assert mappings[0].auto_committed is False

    def test_whitespace_confidence_defaults_to_low(self):
        mappings, _ = _run(_result(_item("crane", "   ")))
        assert mappings[0].auto_committed is False


# ---------------------------------------------------------------------------
# Confidence policy — asset_type on mapping rows
# ---------------------------------------------------------------------------

class TestMappingAssetType:
    def test_high_confidence_preserves_asset_type(self):
        mappings, _ = _run(_result(_item("crane", "high")))
        assert mappings[0].asset_type == "crane"

    def test_medium_confidence_preserves_asset_type(self):
        mappings, _ = _run(_result(_item("forklift", "medium")))
        assert mappings[0].asset_type == "forklift"

    def test_low_confidence_nulls_asset_type(self):
        mappings, _ = _run(_result(_item("crane", "low")))
        assert mappings[0].asset_type is None

    def test_asset_type_lowercased_and_stripped(self):
        # ai_service normalises before calling _write_classifications,
        # but _write_classifications also strips/lowercases defensively.
        mappings, _ = _run(_result(_item("  Crane  ", "high")))
        assert mappings[0].asset_type == "crane"


# ---------------------------------------------------------------------------
# None / empty asset_type items are skipped entirely
# ---------------------------------------------------------------------------

class TestNoneAssetTypeSkipped:
    def test_none_asset_type_produces_no_rows(self):
        mappings, suggestions = _run(_result(_item("", "high")))
        assert mappings == []
        assert suggestions == []

    def test_none_string_asset_type_produces_no_rows(self):
        mappings, suggestions = _run(_result(_item("none", "high")))
        assert mappings == []
        assert suggestions == []

    def test_python_none_asset_type_produces_no_rows(self):
        # ClassificationItem with actual None (not the string "none") is also skipped.
        mappings, suggestions = _run(_result(_item(None, "high")))
        assert mappings == []
        assert suggestions == []

    def test_mixed_valid_and_none_only_valid_written(self):
        mappings, suggestions = _run(_result(
            _item("crane", "high"),
            _item("none", "high"),
            _item("forklift", "medium"),
        ))
        assert len(mappings) == 2
        assert len(suggestions) == 2


# ---------------------------------------------------------------------------
# Skipped activity IDs (low-confidence placeholders)
# ---------------------------------------------------------------------------

class TestSkippedActivities:
    def test_skipped_produces_mapping_row(self):
        act_id = str(uuid.uuid4())
        mappings, _ = _run(ClassificationResult(classifications=[], skipped=[act_id]))
        assert len(mappings) == 1
        assert mappings[0].programme_activity_id == act_id

    def test_skipped_mapping_not_auto_committed(self):
        act_id = str(uuid.uuid4())
        mappings, _ = _run(ClassificationResult(classifications=[], skipped=[act_id]))
        assert mappings[0].auto_committed is False

    def test_skipped_mapping_asset_type_is_none(self):
        act_id = str(uuid.uuid4())
        mappings, _ = _run(ClassificationResult(classifications=[], skipped=[act_id]))
        assert mappings[0].asset_type is None

    def test_skipped_mapping_confidence_is_low(self):
        act_id = str(uuid.uuid4())
        mappings, _ = _run(ClassificationResult(classifications=[], skipped=[act_id]))
        assert mappings[0].confidence == "low"

    def test_skipped_produces_suggestion_row(self):
        act_id = str(uuid.uuid4())
        _, suggestions = _run(ClassificationResult(classifications=[], skipped=[act_id]))
        assert len(suggestions) == 1
        assert suggestions[0].suggested_asset_type is None

    def test_skipped_suggestion_not_accepted(self):
        act_id = str(uuid.uuid4())
        _, suggestions = _run(ClassificationResult(classifications=[], skipped=[act_id]))
        assert suggestions[0].accepted is False


# ---------------------------------------------------------------------------
# AISuggestionLog — always written for every item
# ---------------------------------------------------------------------------

class TestSuggestionRowsAlwaysWritten:
    def test_one_suggestion_per_classification(self):
        items = [_item("crane", "high"), _item("forklift", "medium"), _item("hoist", "low")]
        _, suggestions = _run(_result(*items))
        assert len(suggestions) == 3

    def test_high_suggestion_accepted(self):
        _, suggestions = _run(_result(_item("crane", "high")))
        assert suggestions[0].accepted is True

    def test_medium_suggestion_accepted(self):
        _, suggestions = _run(_result(_item("crane", "medium")))
        assert suggestions[0].accepted is True

    def test_low_suggestion_not_accepted(self):
        _, suggestions = _run(_result(_item("crane", "low")))
        assert suggestions[0].accepted is False

    def test_suggestion_asset_type_always_set(self):
        # Even for low confidence, the suggestion row records what the AI returned.
        _, suggestions = _run(_result(_item("crane", "low")))
        assert suggestions[0].suggested_asset_type == "crane"


# ---------------------------------------------------------------------------
# Observability fields on AISuggestionLog
# ---------------------------------------------------------------------------

class TestObservabilityFields:
    def test_upload_id_written_to_suggestion(self):
        uid = str(uuid.uuid4())
        _, suggestions = _run(_result(_item("crane", "high")), upload_id=uid)
        assert suggestions[0].upload_id == uid

    def test_pipeline_stage_is_classify_assets(self):
        _, suggestions = _run(_result(_item("crane", "high")))
        assert suggestions[0].pipeline_stage == "classify_assets"

    def test_model_name_written_when_not_fallback(self):
        _, suggestions = _run(
            _result(_item("crane", "high")),
            model_name="claude-haiku-4-5-20251001",
            fallback_used=False,
        )
        assert suggestions[0].model_name == "claude-haiku-4-5-20251001"

    def test_model_name_none_when_fallback_used(self):
        _, suggestions = _run(
            _result(_item("crane", "high")),
            model_name="claude-haiku-4-5-20251001",
            fallback_used=True,
        )
        assert suggestions[0].model_name is None

    def test_fallback_used_written_to_suggestion(self):
        _, suggestions = _run(
            _result(_item("crane", "high")),
            fallback_used=True,
        )
        assert suggestions[0].fallback_used is True

    def test_skipped_suggestion_has_pipeline_stage(self):
        act_id = str(uuid.uuid4())
        _, suggestions = _run(
            ClassificationResult(classifications=[], skipped=[act_id]),
            model_name="claude-haiku-4-5-20251001",
            fallback_used=False,
        )
        assert suggestions[0].pipeline_stage == "classify_assets"
        assert suggestions[0].model_name == "claude-haiku-4-5-20251001"


# ---------------------------------------------------------------------------
# _normalize_mapping_source
# ---------------------------------------------------------------------------

class TestNormalizeMappingSource:
    def test_ai_returns_ai(self):
        assert _normalize_mapping_source("ai") == "ai"

    def test_manual_returns_manual(self):
        assert _normalize_mapping_source("manual") == "manual"

    def test_keyword_returns_keyword(self):
        assert _normalize_mapping_source("keyword") == "keyword"

    def test_keyword_boost_maps_to_keyword(self):
        assert _normalize_mapping_source("keyword_boost") == "keyword"

    def test_keyword_fallback_maps_to_keyword(self):
        assert _normalize_mapping_source("keyword_fallback") == "keyword"

    def test_none_defaults_to_ai(self):
        assert _normalize_mapping_source(None) == "ai"

    def test_empty_string_defaults_to_ai(self):
        assert _normalize_mapping_source("") == "ai"

    def test_unknown_value_defaults_to_ai(self):
        assert _normalize_mapping_source("gpt") == "ai"

    def test_case_insensitive(self):
        assert _normalize_mapping_source("KEYWORD_BOOST") == "keyword"
        assert _normalize_mapping_source("Manual") == "manual"

    def test_whitespace_stripped(self):
        assert _normalize_mapping_source("  ai  ") == "ai"
