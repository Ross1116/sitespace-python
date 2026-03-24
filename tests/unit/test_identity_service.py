"""
Unit tests for the Stage 2 identity service.

Tests:
  - normalize_activity_name: day-prefix stripping, punctuation, whitespace, case
  - resolve_or_create_item: creates item+alias on first call, reuses on second
  - resolve_or_create_item: follows merge redirects to active item
  - merge_items: marks source as merged, logs audit event
  - merge_items: raises MergeError for invalid inputs
  - follow_item_redirect: cycle guard
"""

import uuid
import pytest
from unittest.mock import MagicMock, patch, PropertyMock
from sqlalchemy.orm import Session

from app.services.identity_service import (
    NORMALIZER_VERSION,
    MergeError,
    follow_item_redirect,
    merge_items,
    normalize_activity_name,
    resolve_or_create_item,
)
from app.models.item_identity import Item, ItemAlias, ItemIdentityEvent


# ─── normalize_activity_name ─────────────────────────────────────────────────

class TestNormalizeActivityName:

    @pytest.mark.parametrize("raw, expected", [
        ("Install Formwork Level 3",         "install formwork level 3"),
        ("  STRIP   formwork  ",             "strip formwork"),
        ("Day 1 - Install formwork",         "install formwork"),
        ("Day 12: Pour concrete slab",       "pour concrete slab"),
        ("day 3 – Fix reinforcement",        "fix reinforcement"),
        ("Install formwork (Level 3)",       "install formwork level 3"),
        ("Pour concrete, Level 2",           "pour concrete level 2"),
        ("pour 1",                           "pour 1"),   # numeric suffix preserved
        ("pour 2",                           "pour 2"),   # differs from pour 1
        ("",                                 ""),
        ("   ",                              ""),
    ])
    def test_normalise(self, raw, expected):
        assert normalize_activity_name(raw) == expected

    def test_different_numerics_not_equal(self):
        assert normalize_activity_name("pour 1") != normalize_activity_name("pour 2")

    def test_variant_spellings_still_differ(self):
        # Variants are NOT merged by the normalizer (manual merge only in v1)
        a = normalize_activity_name("pour concrete columns")
        b = normalize_activity_name("concrete column pour")
        assert a != b


# ─── Helpers for DB mocking ───────────────────────────────────────────────────

def _make_item(item_id=None, status="active", merged_into=None, name="Test Activity"):
    item = MagicMock(spec=Item)
    item.id = item_id or uuid.uuid4()
    item.identity_status = status
    item.merged_into_item_id = merged_into
    item.display_name = name
    return item


def _make_alias(item):
    alias = MagicMock(spec=ItemAlias)
    alias.item = item
    return alias


def _make_db():
    db = MagicMock(spec=Session)
    db.add = MagicMock()
    db.flush = MagicMock()
    db.rollback = MagicMock()
    return db


# ─── resolve_or_create_item ───────────────────────────────────────────────────

class TestResolveOrCreateItem:

    def test_creates_new_item_when_unseen(self):
        db = _make_db()
        db.query.return_value.filter_by.return_value.first.return_value = None

        with patch("app.services.identity_service.Item") as MockItem, \
             patch("app.services.identity_service.ItemAlias") as MockAlias:
            fake_item = _make_item()
            MockItem.return_value = fake_item

            result = resolve_or_create_item(db, "Install formwork")

        assert result == fake_item.id
        db.add.assert_called()
        db.flush.assert_called()

    def test_reuses_existing_alias(self):
        db = _make_db()
        existing_item = _make_item(status="active")
        alias = _make_alias(existing_item)
        db.query.return_value.filter_by.return_value.first.return_value = alias

        result = resolve_or_create_item(db, "Install formwork")

        assert result == existing_item.id
        db.add.assert_not_called()

    def test_follows_merge_redirect(self):
        db = _make_db()
        survivor = _make_item(status="active", name="Survivor")
        merged = _make_item(status="merged", merged_into=survivor.id, name="Merged")

        alias = _make_alias(merged)
        db.query.return_value.filter_by.return_value.first.return_value = alias
        db.get.return_value = survivor

        result = resolve_or_create_item(db, "Install formwork")

        assert result == survivor.id

    def test_empty_name_returns_none(self):
        db = _make_db()
        result = resolve_or_create_item(db, "   ")
        assert result is None
        db.query.assert_not_called()


# ─── follow_item_redirect ─────────────────────────────────────────────────────

class TestFollowItemRedirect:

    def test_active_item_returns_itself(self):
        db = _make_db()
        item = _make_item(status="active")
        result = follow_item_redirect(db, item)
        assert result is item

    def test_single_redirect(self):
        db = _make_db()
        target_id = uuid.uuid4()
        source = _make_item(status="merged", merged_into=target_id)
        target = _make_item(item_id=target_id, status="active")
        db.get.return_value = target

        result = follow_item_redirect(db, source)
        assert result is target

    def test_cycle_guard_returns_last_valid(self):
        db = _make_db()
        id_a = uuid.uuid4()
        id_b = uuid.uuid4()

        item_a = _make_item(item_id=id_a, status="merged", merged_into=id_b)
        item_b = _make_item(item_id=id_b, status="merged", merged_into=id_a)

        db.get.return_value = item_b

        # Should not loop infinitely — cycle guard breaks it
        result = follow_item_redirect(db, item_a)
        assert result is not None


# ─── merge_items ─────────────────────────────────────────────────────────────

class TestMergeItems:

    def _setup_db(self, source, target):
        db = _make_db()
        db.get.side_effect = lambda model, item_id: (
            source if item_id == source.id else target
        )
        return db

    def test_successful_merge(self):
        source_id = uuid.uuid4()
        target_id = uuid.uuid4()
        source = _make_item(item_id=source_id, status="active", name="Source")
        target = _make_item(item_id=target_id, status="active", name="Target")
        db = self._setup_db(source, target)

        with patch("app.services.identity_service.ItemIdentityEvent"):
            result = merge_items(db, source_id, target_id)

        assert result is target
        assert source.identity_status == "merged"
        assert source.merged_into_item_id == target_id

    def test_raises_on_same_item(self):
        db = _make_db()
        item_id = uuid.uuid4()
        with pytest.raises(MergeError, match="itself"):
            merge_items(db, item_id, item_id)

    def test_raises_on_source_not_found(self):
        db = _make_db()
        db.get.return_value = None
        with pytest.raises(MergeError, match="not found"):
            merge_items(db, uuid.uuid4(), uuid.uuid4())

    def test_raises_if_source_already_merged(self):
        source_id = uuid.uuid4()
        target_id = uuid.uuid4()
        source = _make_item(item_id=source_id, status="merged")
        target = _make_item(item_id=target_id, status="active")
        db = self._setup_db(source, target)

        with pytest.raises(MergeError, match="already merged"):
            merge_items(db, source_id, target_id)

    def test_raises_if_target_already_merged(self):
        source_id = uuid.uuid4()
        target_id = uuid.uuid4()
        source = _make_item(item_id=source_id, status="active")
        target = _make_item(item_id=target_id, status="merged")
        db = self._setup_db(source, target)

        with pytest.raises(MergeError, match="non-active item"):
            merge_items(db, source_id, target_id)

    def test_audit_event_recorded(self):
        source_id = uuid.uuid4()
        target_id = uuid.uuid4()
        source = _make_item(item_id=source_id, status="active", name="Source")
        target = _make_item(item_id=target_id, status="active", name="Target")
        db = self._setup_db(source, target)

        with patch("app.services.identity_service.ItemIdentityEvent") as MockEvent:
            merge_items(db, source_id, target_id)

        MockEvent.assert_called_once()
        call_kwargs = MockEvent.call_args.kwargs
        assert call_kwargs["event_type"] == "merge"
        assert call_kwargs["source_item_id"] == source_id
        assert call_kwargs["target_item_id"] == target_id
