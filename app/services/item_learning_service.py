from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import uuid

from sqlalchemy import and_, or_
from sqlalchemy.orm import Session

from ..models.item_identity import Item, ItemAlias
from ..models.item_identity import ItemClassification
from ..models.programme import ProgrammeActivity, ProgrammeUpload
from ..models.work_profile import (
    ActivityWorkProfile,
    AssetUsageActual,
    ItemContextProfile,
    ItemKnowledgeBase,
)
from .classification_service import get_active_classification, maturity_tier
from .identity_service import follow_item_redirect
from .work_profile_service import work_profile_maturity


@dataclass
class ItemStatisticsPayload:
    item: Item
    family_item_ids: list[uuid.UUID]
    alias_count: int
    occurrence_count: int
    distinct_project_count: int
    last_seen_at: datetime | None
    actuals_count: int
    actual_hours_total: float
    active_classification: ItemClassification | None
    local_profile_counts_by_source: dict[str, int]
    local_profile_counts_by_maturity: dict[str, int]
    global_knowledge_counts_by_tier: dict[str, int]
    global_knowledge_entries: list[ItemKnowledgeBase]


def _load_related_item_map(
    db: Session,
    seed_item_ids: list[uuid.UUID],
) -> dict[uuid.UUID, Item]:
    item_map: dict[uuid.UUID, Item] = {}
    frontier = {item_id for item_id in seed_item_ids if item_id is not None}
    discovered = set(frontier)

    while frontier:
        rows = (
            db.query(Item)
            .filter(
                or_(
                    Item.id.in_(frontier),
                    Item.merged_into_item_id.in_(frontier),
                )
            )
            .all()
        )
        next_frontier: set[uuid.UUID] = set()
        for row in rows:
            item_map[row.id] = row
            if row.id not in discovered:
                discovered.add(row.id)
                next_frontier.add(row.id)
            merged_into = getattr(row, "merged_into_item_id", None)
            if merged_into is not None and merged_into not in discovered:
                discovered.add(merged_into)
                next_frontier.add(merged_into)
        frontier = next_frontier

    return item_map


def _family_item_ids(
    db: Session,
    canonical_item_id: uuid.UUID,
    *,
    item_map: dict[uuid.UUID, Item] | None = None,
) -> list[uuid.UUID]:
    item_map = item_map or _load_related_item_map(db, [canonical_item_id])

    def _resolve(item: Item) -> uuid.UUID:
        current = item
        seen: set[uuid.UUID] = set()
        while current.identity_status == "merged" and current.merged_into_item_id:
            if current.merged_into_item_id in seen:
                break
            seen.add(current.id)
            next_item = item_map.get(current.merged_into_item_id)
            if next_item is None:
                break
            current = next_item
        return current.id

    family = [item.id for item in item_map.values() if _resolve(item) == canonical_item_id]
    if canonical_item_id not in family:
        family.append(canonical_item_id)
    return sorted(set(family), key=str)


def _item_occurrence_stats(
    db: Session,
    family_item_ids: list[uuid.UUID],
) -> tuple[int, int, datetime | None]:
    activity_rows = (
        db.query(ProgrammeActivity, ProgrammeUpload)
        .join(ProgrammeUpload, ProgrammeUpload.id == ProgrammeActivity.programme_upload_id)
        .filter(ProgrammeActivity.item_id.in_(family_item_ids))
        .all()
    )
    if not activity_rows:
        return 0, 0, None
    occurrence_count = len(activity_rows)
    distinct_project_count = len(
        {
            upload.project_id
            for _activity, upload in activity_rows
            if upload.project_id is not None
        }
    )
    last_seen_at = max((upload.created_at for _activity, upload in activity_rows if upload.created_at is not None), default=None)
    return occurrence_count, distinct_project_count, last_seen_at


def _canonical_family_maps(
    db: Session,
    *,
    item_ids: list[uuid.UUID] | None = None,
    item_map: dict[uuid.UUID, Item] | None = None,
) -> tuple[dict[uuid.UUID, uuid.UUID], dict[uuid.UUID, list[uuid.UUID]]]:
    if item_map is None:
        if item_ids is None:
            item_map = {item.id: item for item in db.query(Item).all()}
        else:
            item_map = _load_related_item_map(db, item_ids)

    def _resolve(item: Item) -> uuid.UUID:
        current = item
        seen: set[uuid.UUID] = set()
        while current.identity_status == "merged" and current.merged_into_item_id:
            if current.merged_into_item_id in seen:
                break
            seen.add(current.id)
            next_item = item_map.get(current.merged_into_item_id)
            if next_item is None:
                break
            current = next_item
        return current.id

    canonical_by_item_id: dict[uuid.UUID, uuid.UUID] = {}
    family_by_canonical_id: dict[uuid.UUID, list[uuid.UUID]] = {}
    for item in item_map.values():
        canonical_id = _resolve(item)
        canonical_by_item_id[item.id] = canonical_id
        family_by_canonical_id.setdefault(canonical_id, []).append(item.id)

    if item_ids is not None:
        for item_id in item_ids:
            canonical_by_item_id.setdefault(item_id, item_id)
            family_by_canonical_id.setdefault(canonical_by_item_id[item_id], []).append(item_id)

    return (
        canonical_by_item_id,
        {
            canonical_id: sorted(set(member_ids), key=str)
            for canonical_id, member_ids in family_by_canonical_id.items()
        },
    )


def _batched_item_occurrence_stats(
    db: Session,
    *,
    item_ids: list[uuid.UUID],
) -> dict[uuid.UUID, tuple[int, int, datetime | None]]:
    if not item_ids:
        return {}

    canonical_by_item_id, family_by_canonical_id = _canonical_family_maps(db, item_ids=item_ids)
    requested_canonical_ids = {canonical_by_item_id[item_id] for item_id in item_ids}
    relevant_family_ids = {
        family_item_id
        for canonical_id in requested_canonical_ids
        for family_item_id in family_by_canonical_id.get(canonical_id, [canonical_id])
    }
    family_lookup = {
        family_item_id: canonical_id
        for canonical_id in requested_canonical_ids
        for family_item_id in family_by_canonical_id.get(canonical_id, [canonical_id])
    }

    grouped: dict[uuid.UUID, list[tuple[ProgrammeActivity, ProgrammeUpload]]] = {
        canonical_id: []
        for canonical_id in requested_canonical_ids
    }
    activity_rows = (
        db.query(ProgrammeActivity, ProgrammeUpload)
        .join(ProgrammeUpload, ProgrammeUpload.id == ProgrammeActivity.programme_upload_id)
        .filter(ProgrammeActivity.item_id.in_(relevant_family_ids))
        .all()
    )
    for activity, upload in activity_rows:
        canonical_id = family_lookup.get(activity.item_id)
        if canonical_id is not None:
            grouped.setdefault(canonical_id, []).append((activity, upload))

    summary_by_canonical_id: dict[uuid.UUID, tuple[int, int, datetime | None]] = {}
    for canonical_id in requested_canonical_ids:
        rows = grouped.get(canonical_id, [])
        if not rows:
            summary_by_canonical_id[canonical_id] = (0, 0, None)
            continue
        summary_by_canonical_id[canonical_id] = (
            len(rows),
            len({upload.project_id for _activity, upload in rows if upload.project_id is not None}),
            max((upload.created_at for _activity, upload in rows if upload.created_at is not None), default=None),
        )

    return {
        item_id: summary_by_canonical_id.get(canonical_by_item_id[item_id], (0, 0, None))
        for item_id in item_ids
    }


def get_item_statistics(db: Session, item_id: uuid.UUID) -> ItemStatisticsPayload:
    item = db.get(Item, item_id)
    if item is None:
        raise LookupError(f"Item {item_id} not found")
    canonical_item = follow_item_redirect(db, item)
    family_item_ids = _family_item_ids(db, canonical_item.id)

    alias_count = (
        db.query(ItemAlias)
        .filter(ItemAlias.item_id.in_(family_item_ids))
        .count()
    )
    occurrence_count, distinct_project_count, last_seen_at = _item_occurrence_stats(db, family_item_ids)

    local_profiles = (
        db.query(ItemContextProfile)
        .filter(
            ItemContextProfile.item_id.in_(family_item_ids),
            ItemContextProfile.project_id.isnot(None),
        )
        .all()
    )
    local_profile_counts_by_source = {
        "manual": 0,
        "learned": 0,
        "ai": 0,
        "default": 0,
    }
    local_profile_counts_by_maturity = {
        "manual": 0,
        "trusted_baseline": 0,
        "confirmed": 0,
        "tentative": 0,
    }
    for profile in local_profiles:
        local_profile_counts_by_source[str(profile.source)] = local_profile_counts_by_source.get(str(profile.source), 0) + 1
        local_profile_counts_by_maturity[work_profile_maturity(profile)] = (
            local_profile_counts_by_maturity.get(work_profile_maturity(profile), 0) + 1
        )

    global_rows = (
        db.query(ItemKnowledgeBase)
        .filter(ItemKnowledgeBase.item_id == canonical_item.id)
        .order_by(ItemKnowledgeBase.asset_type, ItemKnowledgeBase.duration_bucket)
        .all()
    )
    global_knowledge_counts_by_tier = {"medium": 0, "high": 0}
    for row in global_rows:
        global_knowledge_counts_by_tier[str(row.confidence_tier)] = (
            global_knowledge_counts_by_tier.get(str(row.confidence_tier), 0) + 1
        )

    actual_rows = (
        db.query(AssetUsageActual, ActivityWorkProfile, ItemContextProfile)
        .join(ActivityWorkProfile, ActivityWorkProfile.id == AssetUsageActual.activity_work_profile_id)
        .outerjoin(ItemContextProfile, ItemContextProfile.id == ActivityWorkProfile.context_profile_id)
        .filter(
            or_(
                ItemContextProfile.item_id.in_(family_item_ids),
                and_(
                    ActivityWorkProfile.item_id.in_(family_item_ids),
                    ItemContextProfile.id.is_(None),
                ),
            )
        )
        .all()
    )
    actuals_count = len(actual_rows)
    actual_hours_total = round(
        sum(float(actual.actual_hours_used or 0) for actual, _awp, _profile in actual_rows),
        4,
    )

    active_classification = get_active_classification(db, canonical_item.id)
    return ItemStatisticsPayload(
        item=canonical_item,
        family_item_ids=family_item_ids,
        alias_count=alias_count,
        occurrence_count=occurrence_count,
        distinct_project_count=distinct_project_count,
        last_seen_at=last_seen_at,
        actuals_count=actuals_count,
        actual_hours_total=actual_hours_total,
        active_classification=active_classification,
        local_profile_counts_by_source=local_profile_counts_by_source,
        local_profile_counts_by_maturity=local_profile_counts_by_maturity,
        global_knowledge_counts_by_tier=global_knowledge_counts_by_tier,
        global_knowledge_entries=global_rows,
    )


def list_other_review_items(
    db: Session,
    *,
    limit: int,
    offset: int,
) -> list[dict[str, object]]:
    rows = (
        db.query(Item, ItemClassification)
        .join(
            ItemClassification,
            ItemClassification.item_id == Item.id,
        )
        .filter(
            Item.identity_status == "active",
            ItemClassification.is_active.is_(True),
            ItemClassification.asset_type == "other",
        )
        .order_by(Item.display_name, Item.id)
        .offset(offset)
        .limit(limit)
        .all()
    )
    payload: list[dict[str, object]] = []
    stats_by_item_id = _batched_item_occurrence_stats(
        db,
        item_ids=[item.id for item, _classification in rows],
    )
    for item, classification in rows:
        occurrence_count, distinct_project_count, last_seen_at = stats_by_item_id.get(item.id, (0, 0, None))
        payload.append(
            {
                "item_id": item.id,
                "display_name": item.display_name,
                "occurrence_count": occurrence_count,
                "distinct_project_count": distinct_project_count,
                "last_seen_at": last_seen_at,
                "classification_source": classification.source,
                "classification_confidence": classification.confidence,
                "classification_maturity_tier": maturity_tier(classification),
            }
        )
    return payload
