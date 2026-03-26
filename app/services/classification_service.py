"""
Classification service — Stage 4.

Manages persistent item-level asset-type classification memory.

Resolution order per item (§17.5 of the architecture plan):
  1. Active ItemClassification row  →  return its asset_type
     PERMANENT (source='manual')     — never re-query
     STABLE    (conf≥5, corr=0)      — never re-query
     CONFIRMED (conf≥2, corr=0)      — never re-query
     TENTATIVE (anything else)       — return current type, but AI re-check runs async
  2. Keyword scan on _KEYWORD_MAP    →  persist as source='keyword', confidence='medium'
  3. Standalone AI classification    →  persist as source='ai'
  4. None                            →  no classification written
"""

from __future__ import annotations

import logging
import uuid
from typing import Optional

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..core.constants import (
    CLASSIFICATION_CONFIRMED_MIN_CONFIRMATIONS,
    CLASSIFICATION_STABLE_MIN_CONFIRMATIONS,
)
from ..crud import asset_type as asset_type_crud
from ..models.item_identity import Item, ItemClassification, ItemClassificationEvent

logger = logging.getLogger(__name__)

# Maturity tiers (from least to most stable).
TIER_TENTATIVE = "tentative"
TIER_CONFIRMED = "confirmed"
TIER_STABLE = "stable"
TIER_PERMANENT = "permanent"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def maturity_tier(c: ItemClassification) -> str:
    """Return the maturity tier for a classification row."""
    if c.source == "manual":
        return TIER_PERMANENT
    if c.confirmation_count >= CLASSIFICATION_STABLE_MIN_CONFIRMATIONS and c.correction_count == 0:
        return TIER_STABLE
    if c.confirmation_count >= CLASSIFICATION_CONFIRMED_MIN_CONFIRMATIONS and c.correction_count == 0:
        return TIER_CONFIRMED
    return TIER_TENTATIVE


def _keyword_scan(activity_name: str) -> str | None:
    """Return the best keyword-matched asset type for *activity_name*, or None."""
    # Import here to avoid a circular import at module load time.
    from .ai_service import _KEYWORD_MAP  # type: ignore[attr-defined]

    name_lower = activity_name.lower()
    for keyword, asset_type in sorted(
        _KEYWORD_MAP.items(), key=lambda kv: len(kv[0]), reverse=True
    ):
        if keyword in name_lower:
            return asset_type
    return None


# ---------------------------------------------------------------------------
# Read
# ---------------------------------------------------------------------------

def get_active_classification(
    db: Session,
    item_id: uuid.UUID,
) -> ItemClassification | None:
    """Return the active classification for *item_id*, or None."""
    return (
        db.query(ItemClassification)
        .filter_by(item_id=item_id, is_active=True)
        .first()
    )


# ---------------------------------------------------------------------------
# Write (internal)
# ---------------------------------------------------------------------------

def _persist_classification(
    db: Session,
    item_id: uuid.UUID,
    asset_type: str,
    confidence: str,
    source: str,
    upload_id: Optional[uuid.UUID] = None,
    performed_by_user_id: Optional[uuid.UUID] = None,
) -> ItemClassification:
    """
    Deactivate any existing active classification for *item_id*, then insert a
    new active row.  All writes are inside a savepoint so the outer transaction
    is not poisoned on IntegrityError.

    Returns the newly-inserted ItemClassification.
    """
    sp = db.begin_nested()
    try:
        existing = (
            db.query(ItemClassification)
            .filter_by(item_id=item_id, is_active=True)
            .with_for_update()
            .first()
        )

        old_asset_type: str | None = None
        if existing:
            old_asset_type = existing.asset_type
            existing.is_active = False
            if existing.asset_type != asset_type:
                existing.correction_count += 1
            db.flush()
            db.add(ItemClassificationEvent(
                id=uuid.uuid4(),
                item_id=item_id,
                classification_id=existing.id,
                event_type="deactivated",
                old_asset_type=old_asset_type,
                new_asset_type=asset_type,
                triggered_by_upload_id=upload_id,
                performed_by_user_id=performed_by_user_id,
            ))

        new_row = ItemClassification(
            id=uuid.uuid4(),
            item_id=item_id,
            asset_type=asset_type,
            confidence=confidence,
            source=source,
            is_active=True,
            confirmation_count=0,
            correction_count=0,
            created_by_user_id=performed_by_user_id,
        )
        db.add(new_row)
        db.flush()

        db.add(ItemClassificationEvent(
            id=uuid.uuid4(),
            item_id=item_id,
            classification_id=new_row.id,
            event_type="created",
            old_asset_type=old_asset_type,
            new_asset_type=asset_type,
            triggered_by_upload_id=upload_id,
            performed_by_user_id=performed_by_user_id,
        ))
        db.flush()
        sp.commit()
        return new_row

    except IntegrityError:
        sp.rollback()
        # A concurrent insert won the unique-active-row constraint race.
        # Re-query to return whichever row was committed so the caller
        # receives the actual persisted classification instead of an error.
        winner = (
            db.query(ItemClassification)
            .filter_by(item_id=item_id, is_active=True)
            .first()
        )
        if winner is not None:
            return winner
        raise


# ---------------------------------------------------------------------------
# Resolve (main entry point for the pipeline)
# ---------------------------------------------------------------------------

def resolve_item_classification(
    db: Session,
    item_id: uuid.UUID,
    activity_name: str,
    upload_id: Optional[uuid.UUID] = None,
) -> str | None:
    """
    Return the resolved asset_type for *item_id*, persisting a new
    classification row if none exists yet.

    Resolution order: active DB row → keyword scan → standalone AI → None.
    Never raises; logs and returns None on unexpected errors.
    """
    try:
        # Step 0 — reject merged items to avoid creating classifications on
        # non-canonical IDs.  Callers should canonicalize via follow_item_redirect
        # before reaching here; we guard defensively without importing from
        # identity_service (which imports us) to prevent circular imports.
        _item = db.get(Item, item_id)
        if _item is not None and _item.identity_status == "merged":
            logger.debug("Item %s has been merged — skipping classification", item_id)
            return None

        # Step 1 — existing active classification
        existing = get_active_classification(db, item_id)
        if existing is not None:
            tier = maturity_tier(existing)

            if tier in (TIER_PERMANENT, TIER_STABLE, TIER_CONFIRMED):
                # Stable — increment confirmation count and return.
                # Re-read with a row lock so concurrent increments are serialised.
                sp = db.begin_nested()
                try:
                    locked = (
                        db.query(ItemClassification)
                        .filter_by(item_id=item_id, is_active=True)
                        .with_for_update()
                        .first()
                    )
                    if locked is not None and locked.id == existing.id:
                        locked.confirmation_count += 1
                        db.add(ItemClassificationEvent(
                            id=uuid.uuid4(),
                            item_id=item_id,
                            classification_id=locked.id,
                            event_type="confirmed",
                            old_asset_type=None,
                            new_asset_type=locked.asset_type,
                            triggered_by_upload_id=upload_id,
                        ))
                        db.flush()
                        sp.commit()
                    else:
                        # Row was deactivated or replaced concurrently — skip
                        # the increment to avoid mutating an inactive row.
                        sp.rollback()
                except Exception:
                    sp.rollback()
                    logger.warning("Failed to record confirmation for item %s — returning type anyway", item_id)
                return existing.asset_type

            # TENTATIVE — return current type for this run; AI re-check below.
            tentative_type = existing.asset_type
            logger.debug(
                "Item %s classification TENTATIVE (%s conf=%d corr=%d) — running AI re-check",
                item_id, existing.asset_type, existing.confirmation_count, existing.correction_count,
            )

            # Run AI re-check (import inside function to avoid circular imports).
            ai_result = _run_standalone_ai(activity_name, db)
            if ai_result is None:
                # AI unavailable/timed out — leave tentative as-is, no count change.
                return tentative_type

            ai_type, ai_confidence = ai_result
            sp = db.begin_nested()
            try:
                if ai_type != tentative_type:
                    # AI disagrees — flag for human review, do NOT auto-promote.
                    # Revalidate under lock: existing.id must still be the active
                    # row, otherwise the event would reference an inactive row.
                    revalidated = (
                        db.query(ItemClassification)
                        .filter_by(item_id=item_id, is_active=True)
                        .with_for_update()
                        .first()
                    )
                    if revalidated is not None and revalidated.id == existing.id:
                        db.add(ItemClassificationEvent(
                            id=uuid.uuid4(),
                            item_id=item_id,
                            classification_id=revalidated.id,
                            event_type="correction_flagged",
                            old_asset_type=tentative_type,
                            new_asset_type=ai_type,
                            triggered_by_upload_id=upload_id,
                            details_json={
                                "ai_suggestion": ai_type,
                                "ai_confidence": ai_confidence,
                                "reason": "ai_disagrees_tentative",
                            },
                        ))
                else:
                    # AI agrees — increment confirmation with a row lock so
                    # concurrent increments are serialised.
                    locked = (
                        db.query(ItemClassification)
                        .filter_by(item_id=item_id, is_active=True)
                        .with_for_update()
                        .first()
                    )
                    if locked is not None and locked.id == existing.id:
                        locked.confirmation_count += 1
                        db.add(ItemClassificationEvent(
                            id=uuid.uuid4(),
                            item_id=item_id,
                            classification_id=locked.id,
                            event_type="confirmed",
                            old_asset_type=None,
                            new_asset_type=tentative_type,
                            triggered_by_upload_id=upload_id,
                        ))
                db.flush()
                sp.commit()
            except Exception:
                sp.rollback()
                logger.warning("Failed to record TENTATIVE re-check event for item %s", item_id)

            return tentative_type

        # Step 2 — keyword scan (validate against active taxonomy before persisting)
        keyword_type = _keyword_scan(activity_name)
        if keyword_type:
            from ..core.constants import get_active_asset_types
            if keyword_type in get_active_asset_types(db):
                new_cls = _persist_classification(
                    db, item_id, keyword_type, "medium", "keyword", upload_id=upload_id,
                )
                logger.debug("Item %s classified via keyword → %s", item_id, keyword_type)
                return new_cls.asset_type
            else:
                logger.debug("Item %s keyword match '%s' is not an active asset type — skipping", item_id, keyword_type)

        # Step 3 — standalone AI
        ai_result = _run_standalone_ai(activity_name, db)
        if ai_result:
            ai_type, ai_confidence = ai_result
            new_cls = _persist_classification(
                db, item_id, ai_type, ai_confidence, "ai", upload_id=upload_id,
            )
            logger.debug("Item %s classified via standalone AI → %s (%s)", item_id, ai_type, ai_confidence)
            return new_cls.asset_type

        logger.debug("Item %s: no classification resolved for '%s'", item_id, activity_name)
        return None

    except Exception:
        logger.exception(
            "resolve_item_classification failed for item=%s activity='%s'",
            item_id, activity_name,
        )
        return None


def _run_standalone_ai(activity_name: str, db: Session) -> tuple[str, str] | None:
    """Run standalone AI classification; return (asset_type, confidence) or None."""
    try:
        from .ai_service import classify_item_standalone  # type: ignore[attr-defined]
        from ..core.constants import get_active_asset_types

        valid_types = get_active_asset_types(db)
        return classify_item_standalone(activity_name, valid_types)
    except Exception as exc:
        logger.warning("Standalone AI classification failed: %s", exc)
        return None


# ---------------------------------------------------------------------------
# Manual override (API-driven)
# ---------------------------------------------------------------------------

def apply_manual_classification(
    db: Session,
    item_id: uuid.UUID,
    asset_type: str,
    performed_by_user_id: uuid.UUID,
) -> ItemClassification:
    """
    Apply a human-driven classification.  Result is PERMANENT (source='manual').

    Raises ValueError if asset_type is not in the active taxonomy.
    Raises LookupError if item_id does not exist.
    """
    item = db.get(Item, item_id)
    if item is None:
        raise LookupError(f"Item {item_id} not found")
    if item.identity_status == "merged":
        raise LookupError(f"Item {item_id} has been merged into another item")

    db_type = asset_type_crud.get_by_code(db, asset_type)
    if db_type is None or not db_type.is_active:
        raise ValueError(f"Asset type '{asset_type}' is not in the active taxonomy")

    new_cls = _persist_classification(
        db,
        item_id,
        asset_type,
        confidence="high",
        source="manual",
        performed_by_user_id=performed_by_user_id,
    )
    # Replace the 'created' event with a 'manual_override' event for clarity.
    # The deactivation event for the previous row was already written by
    # _persist_classification; we just need to fix the event_type of the new row's event.
    last_event = (
        db.query(ItemClassificationEvent)
        .filter_by(classification_id=new_cls.id, event_type="created")
        .first()
    )
    if last_event:
        last_event.event_type = "manual_override"
        db.flush()

    return new_cls


# ---------------------------------------------------------------------------
# Merge reconciliation
# ---------------------------------------------------------------------------

# Flat merge-precedence scores implementing:
#   manual > high-confidence AI > keyword > low-confidence AI
_MERGE_SCORE: dict[tuple[str, str], int] = {
    ("manual",  "high"):   100,
    ("manual",  "medium"): 100,
    ("manual",  "low"):    100,
    ("ai",      "high"):    30,
    ("ai",      "medium"):  20,
    ("keyword", "high"):    15,
    ("keyword", "medium"):  15,
    ("keyword", "low"):     10,
    ("ai",      "low"):      5,
}


def reconcile_classifications_on_merge(
    db: Session,
    source_item_id: uuid.UUID,
    target_item_id: uuid.UUID,
) -> None:
    """
    After merging source into target, reconcile their active classifications.

    Precedence: manual > high-confidence AI > keyword > low-confidence AI.
    The survivor's confirmation_count absorbs the loser's count.
    Called from identity_service.merge_items() inside the same transaction.
    """
    source_cls = (
        db.query(ItemClassification)
        .filter_by(item_id=source_item_id, is_active=True)
        .with_for_update()
        .first()
    )
    target_cls = (
        db.query(ItemClassification)
        .filter_by(item_id=target_item_id, is_active=True)
        .with_for_update()
        .first()
    )

    if source_cls is None and target_cls is None:
        return  # Nothing to reconcile.

    if source_cls is None:
        # Target wins by default — nothing to do.
        return

    if target_cls is None:
        # Source has a classification; target doesn't — move it to the canonical
        # target item so get_active_classification(db, target_item_id) works.
        source_cls.item_id = target_item_id
        db.add(ItemClassificationEvent(
            id=uuid.uuid4(),
            item_id=target_item_id,
            classification_id=source_cls.id,
            event_type="merge_reconcile",
            old_asset_type=source_cls.asset_type,
            new_asset_type=source_cls.asset_type,
            details_json={"note": "source_only_moved_to_target", "original_source_item_id": str(source_item_id)},
        ))
        db.flush()
        return

    # Both have active classifications — determine winner by precedence.
    def _score(c: ItemClassification) -> int:
        return _MERGE_SCORE.get((c.source, c.confidence), 0)

    source_score = _score(source_cls)
    target_score = _score(target_cls)

    if source_score > target_score:
        winner, loser = source_cls, target_cls
    else:
        # target wins on tie (target is the canonical survivor)
        winner, loser = target_cls, source_cls

    # Reassign winner to the canonical target item so get_active_classification
    # on target_item_id returns the winner after the merge.
    winner.item_id = target_item_id

    # Absorb confirmation count from loser into winner.
    combined_confirmations = winner.confirmation_count + loser.confirmation_count
    winner.confirmation_count = combined_confirmations

    # Deactivate loser.
    loser.is_active = False

    db.add(ItemClassificationEvent(
        id=uuid.uuid4(),
        item_id=target_item_id,
        classification_id=winner.id,
        event_type="merge_reconcile",
        old_asset_type=loser.asset_type,
        new_asset_type=winner.asset_type,
        details_json={
            "source_item_id": str(source_item_id),
            "winner_source": winner.source,
            "loser_source": loser.source,
            "combined_confirmation_count": combined_confirmations,
        },
    ))
    db.flush()
