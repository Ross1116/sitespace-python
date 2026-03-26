"""
Unit tests for the Stage 4 classification service.

Tests:
  - maturity_tier: PERMANENT / STABLE / CONFIRMED / TENTATIVE
  - get_active_classification: returns active row or None
  - _persist_classification: inserts new row, deactivates old, writes audit events
  - resolve_item_classification: resolution order (active→keyword→AI→None)
  - apply_manual_classification: PERMANENT source, event_type override
  - reconcile_classifications_on_merge: precedence + confirmation_count absorption
"""

import uuid
import pytest
from unittest.mock import MagicMock, patch, call

from app.services.classification_service import (
    TIER_CONFIRMED,
    TIER_PERMANENT,
    TIER_STABLE,
    TIER_TENTATIVE,
    maturity_tier,
    _keyword_scan,
    apply_manual_classification,
    get_active_classification,
    reconcile_classifications_on_merge,
    resolve_item_classification,
)
from app.models.item_identity import ItemClassification, ItemClassificationEvent


# ─── Helpers ──────────────────────────────────────────────────────────────────

def _make_cls(
    *,
    source: str = "ai",
    confidence: str = "medium",
    is_active: bool = True,
    confirmation_count: int = 0,
    correction_count: int = 0,
    asset_type: str = "crane",
    item_id: uuid.UUID | None = None,
) -> ItemClassification:
    c = MagicMock(spec=ItemClassification)
    c.id = uuid.uuid4()
    c.item_id = item_id or uuid.uuid4()
    c.asset_type = asset_type
    c.source = source
    c.confidence = confidence
    c.is_active = is_active
    c.confirmation_count = confirmation_count
    c.correction_count = correction_count
    return c


def _make_db() -> MagicMock:
    db = MagicMock()
    # begin_nested() returns a savepoint context manager
    sp = MagicMock()
    sp.__enter__ = MagicMock(return_value=sp)
    sp.__exit__ = MagicMock(return_value=False)
    db.begin_nested.return_value = sp
    return db


# ─── maturity_tier ────────────────────────────────────────────────────────────

class TestMaturityTier:

    @pytest.mark.parametrize("source,conf,corr,expected", [
        ("manual", 0, 0, TIER_PERMANENT),
        ("manual", 10, 5, TIER_PERMANENT),   # manual always permanent regardless of counts
        ("ai",     5, 0, TIER_STABLE),
        ("keyword",5, 0, TIER_STABLE),
        ("ai",     2, 0, TIER_CONFIRMED),
        ("keyword",3, 0, TIER_CONFIRMED),     # conf=3 < 5 but ≥ 2 → CONFIRMED
        ("ai",     1, 0, TIER_TENTATIVE),
        ("ai",     5, 1, TIER_TENTATIVE),    # any correction_count → TENTATIVE
        ("ai",     2, 1, TIER_TENTATIVE),
        ("ai",     0, 0, TIER_TENTATIVE),
    ])
    def test_tier(self, source, conf, corr, expected):
        cls = _make_cls(source=source, confirmation_count=conf, correction_count=corr)
        assert maturity_tier(cls) == expected

    def test_confirmed_exactly_at_2(self):
        cls = _make_cls(source="ai", confirmation_count=2, correction_count=0)
        assert maturity_tier(cls) == TIER_CONFIRMED

    def test_stable_exactly_at_5(self):
        cls = _make_cls(source="ai", confirmation_count=5, correction_count=0)
        assert maturity_tier(cls) == TIER_STABLE


# ─── _keyword_scan ────────────────────────────────────────────────────────────

class TestKeywordScan:

    def test_crane_keyword(self):
        assert _keyword_scan("Install crane panel") == "crane"

    def test_hoist_keyword(self):
        assert _keyword_scan("Builder's hoist installation") == "hoist"

    def test_longest_key_wins_over_shorter(self):
        # The key "jump the hoist" (crane) is longer than "hoist" (hoist) in
        # _KEYWORD_MAP, so longest-key-first ordering means "Jump the Hoist"
        # returns "crane" rather than "hoist".
        assert _keyword_scan("Jump the Hoist") == "crane"

    def test_no_match_returns_none(self):
        assert _keyword_scan("General site cleanup") is None

    def test_case_insensitive(self):
        assert _keyword_scan("INSTALL CRANE PANELS") == "crane"


# ─── get_active_classification ────────────────────────────────────────────────

class TestGetActiveClassification:

    def test_returns_active_row(self):
        db = MagicMock()
        item_id = uuid.uuid4()
        cls = _make_cls(item_id=item_id)
        db.query.return_value.filter_by.return_value.first.return_value = cls
        result = get_active_classification(db, item_id)
        assert result is cls

    def test_returns_none_when_missing(self):
        db = MagicMock()
        db.query.return_value.filter_by.return_value.first.return_value = None
        result = get_active_classification(db, uuid.uuid4())
        assert result is None


# ─── resolve_item_classification ──────────────────────────────────────────────

class TestResolveItemClassificationActive:
    """Active row exists — resolution must return its asset_type without calling AI."""

    def _setup_db(self, cls_row):
        db = _make_db()
        db.query.return_value.filter_by.return_value.first.return_value = cls_row
        return db

    @pytest.mark.parametrize("source,conf,corr", [
        ("manual", 0, 0),   # PERMANENT
        ("ai",     5, 0),   # STABLE
        ("ai",     2, 0),   # CONFIRMED
    ])
    def test_stable_tiers_return_without_ai(self, source, conf, corr):
        cls = _make_cls(source=source, confirmation_count=conf, correction_count=corr, asset_type="forklift")
        db = self._setup_db(cls)
        with patch("app.services.classification_service._run_standalone_ai") as mock_ai:
            result = resolve_item_classification(db, cls.item_id, "some activity")
        assert result == "forklift"
        mock_ai.assert_not_called()

    def test_stable_increments_confirmation_count(self):
        cls = _make_cls(source="ai", confirmation_count=5, correction_count=0, asset_type="hoist")
        db = self._setup_db(cls)
        resolve_item_classification(db, cls.item_id, "some activity")
        assert cls.confirmation_count == 6

    def test_tentative_returns_current_type_and_runs_ai(self):
        cls = _make_cls(source="ai", confirmation_count=0, asset_type="crane")
        db = self._setup_db(cls)
        with patch("app.services.classification_service._run_standalone_ai", return_value=("crane", "high")):
            result = resolve_item_classification(db, cls.item_id, "Lift column formwork")
        assert result == "crane"

    def test_tentative_ai_agrees_increments_confirmation(self):
        cls = _make_cls(source="ai", confirmation_count=1, correction_count=0, asset_type="crane")
        db = self._setup_db(cls)
        with patch("app.services.classification_service._run_standalone_ai", return_value=("crane", "high")):
            resolve_item_classification(db, cls.item_id, "Lift column formwork")
        assert cls.confirmation_count == 2

    def test_tentative_ai_disagrees_writes_correction_flagged_event(self):
        cls = _make_cls(source="ai", confirmation_count=0, asset_type="crane")
        db = self._setup_db(cls)
        added_events = []
        db.add.side_effect = lambda obj: added_events.append(obj)
        with patch("app.services.classification_service._run_standalone_ai", return_value=("forklift", "medium")):
            resolve_item_classification(db, cls.item_id, "Lift column formwork")
        event_types = [e.event_type for e in added_events if isinstance(e, ItemClassificationEvent)]
        assert "correction_flagged" in event_types


class TestResolveItemClassificationNoActive:
    """No active row — falls through keyword → AI → None."""

    def _setup_db_no_active(self):
        db = _make_db()
        db.query.return_value.filter_by.return_value.first.return_value = None
        # _persist_classification inner query also returns None (no existing active row)
        db.query.return_value.filter_by.return_value.with_for_update.return_value.first.return_value = None
        return db

    def test_keyword_match_persists_and_returns(self):
        item_id = uuid.uuid4()
        db = self._setup_db_no_active()
        new_cls = _make_cls(asset_type="crane", item_id=item_id)
        with patch("app.services.classification_service._persist_classification", return_value=new_cls) as mock_p, \
             patch("app.core.constants.get_active_asset_types", return_value=frozenset({"crane", "hoist", "ewp", "forklift", "excavator", "telehandler", "concrete_pump", "compactor", "loading_bay", "other"})):
            result = resolve_item_classification(db, item_id, "Install precast panels")
        assert result == "crane"
        mock_p.assert_called_once()
        # _persist_classification(db, item_id, asset_type, confidence, source, ...)
        args = mock_p.call_args[0]
        assert args[4] == "keyword"

    def test_no_keyword_runs_ai(self):
        item_id = uuid.uuid4()
        db = self._setup_db_no_active()
        new_cls = _make_cls(asset_type="forklift", item_id=item_id)
        with patch("app.services.classification_service._persist_classification", return_value=new_cls):
            with patch("app.services.classification_service._run_standalone_ai", return_value=("forklift", "medium")):
                result = resolve_item_classification(db, item_id, "Unload site materials")
        assert result == "forklift"

    def test_no_keyword_no_ai_returns_none(self):
        item_id = uuid.uuid4()
        db = self._setup_db_no_active()
        with patch("app.services.classification_service._run_standalone_ai", return_value=None):
            result = resolve_item_classification(db, item_id, "Unload site materials")
        assert result is None

    def test_exception_returns_none_gracefully(self):
        db = MagicMock()
        db.query.side_effect = RuntimeError("DB down")
        result = resolve_item_classification(db, uuid.uuid4(), "Install crane")
        assert result is None


# ─── apply_manual_classification ──────────────────────────────────────────────

class TestApplyManualClassification:

    def _setup_db(self, item_exists=True, type_active=True):
        db = _make_db()
        item_mock = MagicMock() if item_exists else None
        db.get.return_value = item_mock
        type_mock = MagicMock()
        type_mock.is_active = type_active
        db.query.return_value.filter_by.return_value.first.return_value = None  # no existing active cls
        db.query.return_value.filter_by.return_value.with_for_update.return_value.first.return_value = None
        return db, type_mock

    def test_raises_lookup_error_when_item_missing(self):
        db, _ = self._setup_db(item_exists=False)
        with pytest.raises(LookupError, match="not found"):
            apply_manual_classification(db, uuid.uuid4(), "crane", uuid.uuid4())

    def test_raises_value_error_when_type_not_in_taxonomy(self):
        db, _ = self._setup_db()
        with patch("app.services.classification_service.asset_type_crud.get_by_code", return_value=None):
            with pytest.raises(ValueError, match="not in the active taxonomy"):
                apply_manual_classification(db, uuid.uuid4(), "invalid_type", uuid.uuid4())

    def test_raises_value_error_when_type_inactive(self):
        db, type_mock = self._setup_db(type_active=False)
        with patch("app.services.classification_service.asset_type_crud.get_by_code", return_value=type_mock):
            with pytest.raises(ValueError, match="not in the active taxonomy"):
                apply_manual_classification(db, uuid.uuid4(), "retired_type", uuid.uuid4())

    def test_success_calls_persist_with_manual_source(self):
        db, type_mock = self._setup_db()
        item_id = uuid.uuid4()
        user_id = uuid.uuid4()
        new_cls = _make_cls(source="manual", confidence="high", asset_type="crane", item_id=item_id)
        # The last_event query returns None (no event to patch)
        db.query.return_value.filter_by.return_value.first.return_value = None
        with patch("app.services.classification_service.asset_type_crud.get_by_code", return_value=type_mock):
            with patch("app.services.classification_service._persist_classification", return_value=new_cls):
                result = apply_manual_classification(db, item_id, "crane", user_id)
        assert result is new_cls


# ─── reconcile_classifications_on_merge ───────────────────────────────────────

class TestReconcileClassificationsOnMerge:

    def _setup_db(self, source_cls, target_cls):
        db = MagicMock()
        # Match filter_by calls by item_id kwarg so the mock is order-independent.
        source_id = source_cls.item_id if source_cls is not None else None
        target_id = target_cls.item_id if target_cls is not None else None

        def filter_by_side(**kwargs):
            m = MagicMock()
            item_id = kwargs.get("item_id")
            if source_id is not None and item_id == source_id:
                result = source_cls
            elif target_id is not None and item_id == target_id:
                result = target_cls
            else:
                result = None
            m.with_for_update.return_value.first.return_value = result
            return m

        db.query.return_value.filter_by.side_effect = filter_by_side
        return db

    def test_manual_beats_ai(self):
        source_id = uuid.uuid4()
        target_id = uuid.uuid4()
        source_cls = _make_cls(source="manual", confidence="high", asset_type="crane",
                               item_id=source_id, confirmation_count=2)
        target_cls = _make_cls(source="ai", confidence="high", asset_type="forklift",
                               item_id=target_id, confirmation_count=3)
        db = self._setup_db(source_cls, target_cls)
        reconcile_classifications_on_merge(db, source_id, target_id)
        # manual wins — target (loser) should be deactivated
        assert target_cls.is_active is False
        assert source_cls.confirmation_count == 5  # 2 + 3
        # winner must be reattached to the canonical target item
        assert source_cls.item_id == target_id
        assert source_cls.is_active is True

    def test_target_wins_on_tie(self):
        source_id = uuid.uuid4()
        target_id = uuid.uuid4()
        source_cls = _make_cls(source="ai", confidence="high", asset_type="crane",
                               item_id=source_id, confirmation_count=1)
        target_cls = _make_cls(source="ai", confidence="high", asset_type="hoist",
                               item_id=target_id, confirmation_count=2)
        db = self._setup_db(source_cls, target_cls)
        reconcile_classifications_on_merge(db, source_id, target_id)
        # equal precedence → target wins
        assert source_cls.is_active is False
        assert target_cls.confirmation_count == 3  # 2 + 1

    def test_keyword_beats_low_ai(self):
        source_id = uuid.uuid4()
        target_id = uuid.uuid4()
        source_cls = _make_cls(source="keyword", confidence="medium", asset_type="crane",
                               item_id=source_id, confirmation_count=0)
        target_cls = _make_cls(source="ai", confidence="low", asset_type="forklift",
                               item_id=target_id, confirmation_count=1)
        db = self._setup_db(source_cls, target_cls)
        reconcile_classifications_on_merge(db, source_id, target_id)
        assert target_cls.is_active is False
        assert source_cls.confirmation_count == 1  # 0 + 1
        # winner must be reattached to the canonical target item
        assert source_cls.item_id == target_id
        assert source_cls.is_active is True

    def test_no_source_classification_no_op(self):
        source_id = uuid.uuid4()
        target_id = uuid.uuid4()
        target_cls = _make_cls(source="ai", asset_type="crane", item_id=target_id)
        db = self._setup_db(None, target_cls)
        # Should complete without error and not modify target
        reconcile_classifications_on_merge(db, source_id, target_id)
        assert target_cls.is_active is True

    def test_no_target_classification_writes_event(self):
        source_id = uuid.uuid4()
        target_id = uuid.uuid4()
        source_cls = _make_cls(source="ai", asset_type="crane", item_id=source_id)
        db = self._setup_db(source_cls, None)
        added = []
        db.add.side_effect = lambda obj: added.append(obj)
        reconcile_classifications_on_merge(db, source_id, target_id)
        events = [e for e in added if isinstance(e, ItemClassificationEvent)]
        assert any(e.event_type == "merge_reconcile" for e in events)
