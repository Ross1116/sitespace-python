"""
Stage 2 — Identity layer service.

Responsibilities:
  - normalize_activity_name: deterministic text normalizer (version 1)
  - resolve_or_create_item: alias lookup → item; creates new item+alias when unseen
  - follow_item_redirect: resolve merged-item chains to the canonical active item
  - merge_items: execute a manual merge, record audit event

Normalizer version is stored on every alias so that rule changes don't silently
rewrite history. Bump NORMALIZER_VERSION when the normalization rules change.
"""

import logging
import re
import uuid
from typing import Optional

from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError

from ..models.item_identity import Item, ItemAlias, ItemIdentityEvent

logger = logging.getLogger(__name__)

# ─── Normalizer ──────────────────────────────────────────────────────────────

NORMALIZER_VERSION: int = 1

# Strip leading "Day N - " or "Day N: " style prefixes (P6 / MS Project artefacts).
_DAY_PREFIX_RE = re.compile(r"^day\s+\d+\s*[-:–—]\s*", re.IGNORECASE)

# Collapse any run of non-alphanumeric, non-space characters to a single space.
# This strips punctuation while preserving numbers (important: "pour 1" ≠ "pour 2").
_PUNCT_RE = re.compile(r"[^\w\s]")


def normalize_activity_name(name: str) -> str:
    """
    Return a deterministic normalised form of an activity name for identity lookup.

    Rules (normalizer_version=1):
      1. Strip leading/trailing whitespace
      2. Lowercase
      3. Strip leading "Day N - / Day N: " prefixes
      4. Replace punctuation with a space
      5. Collapse internal whitespace to a single space
      6. Strip again

    Numeric suffixes are preserved intentionally: "pour 1" and "pour 2" are
    different activities.
    """
    s = name.strip().lower()
    s = _DAY_PREFIX_RE.sub("", s)
    s = _PUNCT_RE.sub(" ", s)
    s = re.sub(r"\s{2,}", " ", s).strip()
    return s


# ─── Resolution ──────────────────────────────────────────────────────────────

def follow_item_redirect(db: Session, item: Item) -> Item:
    """
    Follow merged_into_item_id chains until we reach an active item.
    Guards against cycles by limiting traversal depth.
    """
    seen: set[uuid.UUID] = set()
    current = item
    while current.identity_status == "merged" and current.merged_into_item_id:
        if current.merged_into_item_id in seen:
            logger.warning(
                "Cycle detected in item merge chain starting at item_id=%s; stopping redirect",
                item.id,
            )
            break
        seen.add(current.id)
        next_item = db.get(Item, current.merged_into_item_id)
        if next_item is None:
            logger.warning(
                "Item %s merged_into_item_id=%s not found; returning current item",
                current.id,
                current.merged_into_item_id,
            )
            break
        current = next_item
    return current


def resolve_or_create_item(
    db: Session,
    raw_name: str,
    normalizer_version: int = NORMALIZER_VERSION,
) -> Optional[uuid.UUID]:
    """
    Resolve a raw activity name to an active Item.id, creating one if unseen.

    Returns None when raw_name normalises to an empty string (e.g. whitespace-only).

    Algorithm:
      1. Normalise the name
      2. Look up alias by (normalised_name, normalizer_version)
      3. If found: resolve merge redirects, return active item id
      4. If not found: create Item + ItemAlias, return new item id

    On race-condition duplicate-alias IntegrityError (two concurrent inserts),
    falls back to a second lookup instead of raising.
    """
    normalised = normalize_activity_name(raw_name)
    if not normalised:
        return None

    alias = (
        db.query(ItemAlias)
        .filter_by(alias_normalised_name=normalised, normalizer_version=normalizer_version)
        .first()
    )

    if alias:
        active = follow_item_redirect(db, alias.item)
        return active.id

    # Not found — create item + seed alias inside a savepoint so that an
    # IntegrityError (race condition: another worker created the same alias
    # between our lookup and insert) only rolls back this sub-transaction,
    # leaving the outer pipeline transaction intact.
    sp = db.begin_nested()
    try:
        item = Item(display_name=raw_name.strip(), identity_status="active")
        db.add(item)
        db.flush()

        alias = ItemAlias(
            item_id=item.id,
            alias_normalised_name=normalised,
            normalizer_version=normalizer_version,
            alias_type="exact",
            confidence="high",
            source="parser",
        )
        db.add(alias)
        db.flush()
        sp.commit()

        logger.debug("Created new item id=%s for name=%r", item.id, raw_name)
        return item.id

    except IntegrityError:
        # Race condition: roll back only the savepoint, then retry the lookup.
        sp.rollback()
        alias = (
            db.query(ItemAlias)
            .filter_by(alias_normalised_name=normalised, normalizer_version=normalizer_version)
            .first()
        )
        if alias:
            active = follow_item_redirect(db, alias.item)
            return active.id
        raise  # unexpected — re-raise if still not found after rollback


# ─── Merge ───────────────────────────────────────────────────────────────────

class MergeError(ValueError):
    """Raised when a merge operation is invalid."""


def merge_items(
    db: Session,
    source_item_id: uuid.UUID,
    target_item_id: uuid.UUID,
    performed_by_user_id: Optional[uuid.UUID] = None,
) -> Item:
    """
    Merge source into target: source becomes 'merged', target stays active.

    Rules from architecture plan:
      - Source marked identity_status='merged', merged_into_item_id=target.id
      - Existing historical rows are NOT repointed (runtime redirect handles this)
      - An ItemIdentityEvent audit record is created
      - Returns the active (target) item

    Raises MergeError if:
      - Either item not found
      - source == target
      - source is already merged
      - target is already merged (can't merge into a non-active item)
    """
    if source_item_id == target_item_id:
        raise MergeError("Cannot merge an item into itself")

    # Lock both rows in a single query so concurrent merges cannot race.
    rows = (
        db.query(Item)
        .filter(Item.id.in_([source_item_id, target_item_id]))
        .order_by(Item.id)
        .with_for_update()
        .all()
    )
    row_map = {r.id: r for r in rows}
    source = row_map.get(source_item_id)
    target = row_map.get(target_item_id)

    if source is None:
        raise MergeError(f"Source item {source_item_id} not found")
    if target is None:
        raise MergeError(f"Target item {target_item_id} not found")

    if source.identity_status == "merged":
        raise MergeError(f"Source item {source_item_id} is already merged")

    if target.identity_status == "merged":
        raise MergeError(f"Target item {target_item_id} is already merged — cannot merge into a non-active item")

    source.identity_status = "merged"
    source.merged_into_item_id = target_item_id

    event = ItemIdentityEvent(
        event_type="merge",
        source_item_id=source_item_id,
        target_item_id=target_item_id,
        details_json={
            "source_display_name": source.display_name,
            "target_display_name": target.display_name,
        },
        created_by_user_id=performed_by_user_id,
    )
    db.add(event)
    db.flush()

    # Reconcile classification memory — runs inside a savepoint so a partial
    # failure rolls back only the classification work, not the merge itself.
    # Import here, not at module top, to avoid a circular dependency:
    #   identity_service → classification_service → identity_service (Item model)
    try:
        from .classification_service import reconcile_classifications_on_merge
        sp = db.begin_nested()
        try:
            reconcile_classifications_on_merge(db, source_item_id, target_item_id)
            sp.commit()
        except Exception as exc:
            sp.rollback()
            logger.warning(
                "Classification reconciliation failed for merge %s->%s: %s — merge itself succeeded",
                source_item_id, target_item_id, exc,
            )
    except Exception as exc:
        logger.warning(
            "Classification reconciliation import/setup failed for merge %s->%s: %s",
            source_item_id, target_item_id, exc,
        )

    logger.info(
        "Merged item %s ('%s') into %s ('%s') by user %s",
        source_item_id, source.display_name,
        target_item_id, target.display_name,
        performed_by_user_id,
    )
    return target
