from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from difflib import SequenceMatcher
import uuid

from sqlalchemy import and_, func, or_
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
    canonical_by_item_id, family_by_canonical_id = _canonical_family_maps(
        db,
        item_ids=[canonical_item_id],
        item_map=item_map,
    )
    canonical_id = canonical_by_item_id.get(canonical_item_id, canonical_item_id)
    family = family_by_canonical_id.get(canonical_id, [canonical_item_id])
    return sorted(set(family), key=str)


def _item_occurrence_stats(
    db: Session,
    family_item_ids: list[uuid.UUID],
) -> tuple[int, int, datetime | None]:
    occurrence_count, distinct_project_count, last_seen_at = (
        db.query(
            func.count(ProgrammeActivity.id),
            func.count(func.distinct(ProgrammeUpload.project_id)),
            func.max(ProgrammeUpload.created_at),
        )
        .join(ProgrammeUpload, ProgrammeUpload.id == ProgrammeActivity.programme_upload_id)
        .filter(ProgrammeActivity.item_id.in_(family_item_ids))
        .one()
    )
    return int(occurrence_count or 0), int(distinct_project_count or 0), last_seen_at


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

    adjacency: dict[uuid.UUID, set[uuid.UUID]] = {}
    for item_id in item_map:
        adjacency.setdefault(item_id, set())
    for item in item_map.values():
        merged_into = getattr(item, "merged_into_item_id", None)
        if merged_into is None:
            continue
        adjacency.setdefault(item.id, set()).add(merged_into)
        adjacency.setdefault(merged_into, set()).add(item.id)

    seen: set[uuid.UUID] = set()
    canonical_by_item_id: dict[uuid.UUID, uuid.UUID] = {}
    family_by_canonical_id: dict[uuid.UUID, list[uuid.UUID]] = {}
    for item_id in sorted(adjacency, key=str):
        if item_id in seen:
            continue
        stack = [item_id]
        component: set[uuid.UUID] = set()
        while stack:
            current = stack.pop()
            if current in seen:
                continue
            seen.add(current)
            component.add(current)
            stack.extend(neighbor for neighbor in adjacency.get(current, set()) if neighbor not in seen)

        canonical_id = min(component, key=str)
        members = sorted(component, key=str)
        family_by_canonical_id[canonical_id] = members
        for member_id in members:
            canonical_by_item_id[member_id] = canonical_id

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
            ItemContextProfile.invalidated_at.is_(None),
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

    actuals_count, actual_hours_total = (
        db.query(
            func.count(AssetUsageActual.id),
            func.coalesce(func.sum(AssetUsageActual.actual_hours_used), 0),
        )
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
        .one()
    )

    active_classification = get_active_classification(db, canonical_item.id)
    return ItemStatisticsPayload(
        item=canonical_item,
        family_item_ids=family_item_ids,
        alias_count=alias_count,
        occurrence_count=occurrence_count,
        distinct_project_count=distinct_project_count,
        last_seen_at=last_seen_at,
        actuals_count=int(actuals_count or 0),
        actual_hours_total=round(float(actual_hours_total or 0), 4),
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


def summarize_other_review_items(db: Session) -> dict[str, object]:
    rows = list_other_review_items(db, limit=25, offset=0)
    total_items = (
        db.query(func.count(Item.id))
        .join(ItemClassification, ItemClassification.item_id == Item.id)
        .filter(
            Item.identity_status == "active",
            ItemClassification.is_active.is_(True),
            ItemClassification.asset_type == "other",
        )
        .scalar()
        or 0
    )
    total_occurrences, distinct_project_count = (
        db.query(
            func.count(ProgrammeActivity.id),
            func.count(func.distinct(ProgrammeUpload.project_id)),
        )
        .join(ProgrammeUpload, ProgrammeUpload.id == ProgrammeActivity.programme_upload_id)
        .join(ItemClassification, ItemClassification.item_id == ProgrammeActivity.item_id)
        .filter(
            ItemClassification.is_active.is_(True),
            ItemClassification.asset_type == "other",
        )
        .one()
    )
    recent_cutoff = datetime.now(timezone.utc) - timedelta(days=14)
    recent_upload_occurrences = (
        db.query(func.count(ProgrammeActivity.id))
        .join(ProgrammeUpload, ProgrammeUpload.id == ProgrammeActivity.programme_upload_id)
        .join(ItemClassification, ItemClassification.item_id == ProgrammeActivity.item_id)
        .filter(
            ItemClassification.is_active.is_(True),
            ItemClassification.asset_type == "other",
            ProgrammeUpload.created_at >= recent_cutoff,
        )
        .scalar()
        or 0
    )
    return {
        "total_items": int(total_items),
        "total_occurrences": int(total_occurrences or 0),
        "distinct_project_count": int(distinct_project_count or 0),
        "recent_upload_occurrences": int(recent_upload_occurrences),
        "top_items": rows,
    }


def _normalized_tokens(value: str) -> set[str]:
    return {token for token in (value or "").lower().replace("-", " ").replace("_", " ").split() if token}


def suggest_merge_candidates(
    db: Session,
    item_id: uuid.UUID,
    *,
    limit: int = 10,
) -> list[dict[str, object]]:
    item = db.get(Item, item_id)
    if item is None:
        raise LookupError(f"Item {item_id} not found")
    canonical_item = follow_item_redirect(db, item)
    family_ids = set(_family_item_ids(db, canonical_item.id))

    item_tokens = _normalized_tokens(canonical_item.display_name)
    alias_tokens = {
        token
        for row in db.query(ItemAlias).filter(ItemAlias.item_id.in_(family_ids)).all()
        for token in _normalized_tokens(row.alias_normalised_name)
    }
    source_tokens = item_tokens | alias_tokens
    source_classification = get_active_classification(db, canonical_item.id)

    candidates = (
        db.query(Item)
        .filter(Item.identity_status == "active", Item.id.notin_(sorted(family_ids, key=str)))
        .all()
    )
    stats_by_item = _batched_item_occurrence_stats(db, item_ids=[candidate.id for candidate in candidates])

    ranked = []
    for candidate in candidates:
        candidate_family = set(_family_item_ids(db, candidate.id))
        if family_ids & candidate_family:
            continue
        candidate_alias_rows = db.query(ItemAlias).filter(ItemAlias.item_id.in_(candidate_family)).all()
        candidate_tokens = _normalized_tokens(candidate.display_name)
        candidate_tokens |= {
            token
            for row in candidate_alias_rows
            for token in _normalized_tokens(row.alias_normalised_name)
        }
        overlap = sorted(source_tokens & candidate_tokens)
        name_similarity = SequenceMatcher(
            None,
            canonical_item.display_name.lower(),
            candidate.display_name.lower(),
        ).ratio()
        token_similarity = 0.0
        union = source_tokens | candidate_tokens
        if union:
            token_similarity = len(source_tokens & candidate_tokens) / len(union)
        classification = get_active_classification(db, candidate.id)
        classification_boost = 0.0
        if (
            source_classification is not None
            and classification is not None
            and source_classification.asset_type == classification.asset_type
        ):
            classification_boost = 0.15
        score = round((name_similarity * 0.55) + (token_similarity * 0.30) + classification_boost, 4)
        if score < 0.25:
            continue
        occurrence_count, distinct_project_count, last_seen_at = stats_by_item.get(candidate.id, (0, 0, None))
        ranked.append(
            {
                "item_id": candidate.id,
                "display_name": candidate.display_name,
                "score": score,
                "overlapping_tokens": overlap,
                "candidate_asset_type": classification.asset_type if classification is not None else None,
                "occurrence_count": occurrence_count,
                "distinct_project_count": distinct_project_count,
                "last_seen_at": last_seen_at,
            }
        )
    ranked.sort(key=lambda row: (-float(row["score"]), row["display_name"], str(row["item_id"])))
    return ranked[:limit]
