from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy.orm import Session, joinedload

from ..models.asset import Asset
from ..models.item_identity import Item
from ..models.ops import ItemRequirementSet


def validate_requirement_rules(rules: dict[str, Any]) -> dict[str, Any]:
    if rules is None:
        rules = {}
    elif not isinstance(rules, dict):
        raise ValueError("rules must be an object")
    else:
        rules = dict(rules)
    for key in ("allowed_asset_types", "preferred_asset_types", "excluded_asset_types"):
        value = rules.get(key)
        if value is None:
            continue
        if isinstance(value, str) or not isinstance(value, (list, tuple, set)):
            raise ValueError(f"{key} must be a list, tuple, or set")
    for key in ("required_attributes", "preferred_attributes"):
        value = rules.get(key)
        if value is None:
            continue
        if not isinstance(value, dict):
            raise ValueError(f"{key} must be a dict")
    for key in ("min_parallel_units", "default_parallel_units", "max_parallel_units"):
        value = rules.get(key)
        if value is None:
            continue
        if isinstance(value, bool):
            raise ValueError(f"{key} must be an integer")
        try:
            int(value)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{key} must be an integer") from exc
    normalized = {
        "allowed_asset_types": sorted({str(value) for value in rules.get("allowed_asset_types") or []}),
        "preferred_asset_types": sorted({str(value) for value in rules.get("preferred_asset_types") or []}),
        "excluded_asset_types": sorted({str(value) for value in rules.get("excluded_asset_types") or []}),
        "required_attributes": {
            str(key): value for key, value in dict(rules.get("required_attributes") or {}).items()
        },
        "preferred_attributes": {
            str(key): value for key, value in dict(rules.get("preferred_attributes") or {}).items()
        },
        "min_parallel_units": max(int(rules.get("min_parallel_units") or 0), 0),
        "default_parallel_units": max(int(rules.get("default_parallel_units") or 1), 1),
        "max_parallel_units": max(int(rules.get("max_parallel_units") or 1), 1),
    }
    if normalized["default_parallel_units"] < normalized["min_parallel_units"]:
        normalized["default_parallel_units"] = normalized["min_parallel_units"]
    if normalized["max_parallel_units"] < normalized["default_parallel_units"]:
        normalized["max_parallel_units"] = normalized["default_parallel_units"]
    return normalized


def get_active_item_requirement_set(
    db: Session,
    item_id: uuid.UUID,
    *,
    for_update: bool = False,
) -> ItemRequirementSet | None:
    query = (
        db.query(ItemRequirementSet)
        .filter(ItemRequirementSet.item_id == item_id, ItemRequirementSet.is_active.is_(True))
        .order_by(ItemRequirementSet.version.desc())
    )
    if for_update:
        query = query.with_for_update()
    return query.first()


def replace_item_requirement_set(
    db: Session,
    *,
    item_id: uuid.UUID,
    rules: dict[str, Any],
    notes: str | None,
    created_by_user_id: uuid.UUID | None,
) -> ItemRequirementSet:
    item_row = db.query(Item.id).filter(Item.id == item_id).with_for_update().first()
    if item_row is None:
        raise LookupError(f"Item {item_id} not found")
    active = get_active_item_requirement_set(db, item_id, for_update=True)
    next_version = (int(active.version) + 1) if active is not None else 1
    if active is not None:
        active.is_active = False
    row = ItemRequirementSet(
        item_id=item_id,
        version=next_version,
        is_active=True,
        rules_json=validate_requirement_rules(rules),
        notes=notes,
        created_by_user_id=created_by_user_id,
    )
    db.add(row)
    db.flush()
    return row


def _merged_planning_attributes(asset: Asset) -> dict[str, Any]:
    merged: dict[str, Any] = {}
    type_attrs = getattr(getattr(asset, "asset_type_rel", None), "planning_attributes_json", None) or {}
    asset_attrs = getattr(asset, "planning_attributes_json", None) or {}
    if isinstance(type_attrs, dict):
        merged.update(type_attrs)
    if isinstance(asset_attrs, dict):
        merged.update(asset_attrs)
    return merged


def _attribute_matches(actual: Any, expected: Any) -> bool:
    if isinstance(actual, list) and not isinstance(expected, list):
        return expected in actual
    if isinstance(expected, list):
        if isinstance(actual, list):
            return any(value in actual for value in expected)
        return actual in expected
    return actual == expected


def evaluate_assets_against_requirements(
    db: Session,
    *,
    item_id: uuid.UUID,
    project_id: uuid.UUID | None = None,
    asset_ids: list[uuid.UUID] | None = None,
) -> dict[str, Any]:
    requirement_set = get_active_item_requirement_set(db, item_id)
    rules = validate_requirement_rules(requirement_set.rules_json if requirement_set else {})
    query = db.query(Asset).options(joinedload(Asset.asset_type_rel))
    if asset_ids:
        query = query.filter(Asset.id.in_(asset_ids))
    elif project_id is not None:
        query = query.filter(Asset.project_id == project_id)
    assets = query.all()

    evaluations = []
    for asset in assets:
        actual_type = str(asset.canonical_type or asset.type or "")
        attrs = _merged_planning_attributes(asset)
        failures: list[str] = []
        preferences: list[str] = []
        if rules["allowed_asset_types"] and actual_type not in rules["allowed_asset_types"]:
            failures.append("asset_type_not_allowed")
        if actual_type in rules["excluded_asset_types"]:
            failures.append("asset_type_excluded")
        if rules["preferred_asset_types"] and actual_type in rules["preferred_asset_types"]:
            preferences.append("preferred_asset_type")
        for key, expected in rules["required_attributes"].items():
            if not _attribute_matches(attrs.get(key), expected):
                failures.append(f"missing_required_attribute:{key}")
        for key, expected in rules["preferred_attributes"].items():
            if _attribute_matches(attrs.get(key), expected):
                preferences.append(f"preferred_attribute:{key}")
        evaluations.append(
            {
                "asset_id": asset.id,
                "asset_code": asset.asset_code,
                "asset_name": asset.name,
                "asset_type": actual_type,
                "matches": not failures,
                "failures": failures,
                "preferences": preferences,
                "planning_attributes": attrs,
            }
        )

    return {
        "item_id": item_id,
        "requirements": rules,
        "evaluations": evaluations,
    }
