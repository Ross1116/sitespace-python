from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
import re
from typing import Optional
from uuid import UUID

from sqlalchemy.orm import Session, joinedload

from ..models.asset import Asset
from ..models.site_project import SiteProject
from ..models.slot_booking import SlotBooking
from ..models.subcontractor import Subcontractor
from ..schemas.enums import (
    AssetTypeResolutionStatus,
    BookingStatus,
    TradeResolutionStatus,
    TradeSpecialty,
)
from .ai_service import normalize_asset_type


ACTIVE_BOOKING_STATUSES = {
    BookingStatus.PENDING,
    BookingStatus.CONFIRMED,
    BookingStatus.IN_PROGRESS,
    BookingStatus.COMPLETED,
}

ASSET_TYPE_RESOLUTION_READY = {
    AssetTypeResolutionStatus.INFERRED.value,
    AssetTypeResolutionStatus.CONFIRMED.value,
}


@dataclass(frozen=True)
class AssetTypeResolution:
    canonical_type: Optional[str]
    status: str
    source: Optional[str]
    confidence: Optional[float]


@dataclass(frozen=True)
class SubcontractorTradeResolution:
    trade_specialty: Optional[str]
    suggested_trade_specialty: Optional[str]
    status: str
    source: Optional[str]
    confidence: Optional[float]


def _normalize_text(value: Optional[str]) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9]+", " ", (value or "").strip().lower())).strip()


def _extract_email_domain(email: Optional[str]) -> str:
    email = (email or "").strip().lower()
    if "@" not in email:
        return ""
    return email.split("@", 1)[1]


def unknown_asset_type_resolution() -> AssetTypeResolution:
    return AssetTypeResolution(
        canonical_type=None,
        status=AssetTypeResolutionStatus.UNKNOWN.value,
        source=None,
        confidence=None,
    )


def confirmed_asset_type_resolution(canonical_type: Optional[str]) -> AssetTypeResolution:
    canonical = (canonical_type or "").strip().lower() or None
    if canonical is None:
        return unknown_asset_type_resolution()
    return AssetTypeResolution(
        canonical_type=canonical,
        status=AssetTypeResolutionStatus.CONFIRMED.value,
        source="manual",
        confidence=1.0,
    )


def infer_asset_type_resolution(
    *,
    raw_type: Optional[str],
    asset_name: Optional[str],
    asset_code: Optional[str],
) -> AssetTypeResolution:
    candidates: list[tuple[str, str, float]] = []
    for source, value, confidence in (
        ("raw_type", raw_type, 0.97),
        ("asset_name", asset_name, 0.92),
        ("asset_code", asset_code, 0.88),
    ):
        canonical = normalize_asset_type(value or "")
        if canonical:
            candidates.append((canonical, source, confidence))

    if not candidates:
        return unknown_asset_type_resolution()

    unique_matches = {canonical for canonical, _source, _confidence in candidates}
    if len(unique_matches) != 1:
        return unknown_asset_type_resolution()

    canonical, source, confidence = candidates[0]
    return AssetTypeResolution(
        canonical_type=canonical,
        status=AssetTypeResolutionStatus.INFERRED.value,
        source=source,
        confidence=confidence,
    )


TRADE_INFERENCE_KEYWORDS: dict[str, list[str]] = {
    TradeSpecialty.ELECTRICIAN.value: ["electric", "electrical", "sparky"],
    TradeSpecialty.PLUMBER.value: ["plumb", "drain", "hydraulic"],
    TradeSpecialty.CARPENTER.value: ["carpent", "joiner", "joinery", "carpentry"],
    TradeSpecialty.MASON.value: ["mason", "masonry", "brick", "blocklay", "bricklay"],
    TradeSpecialty.PAINTER.value: ["paint", "painting", "decorat"],
    TradeSpecialty.HVAC.value: ["hvac", "mechanical", "aircon", "air con", "ventilation"],
    TradeSpecialty.ROOFER.value: ["roof", "roofing"],
    TradeSpecialty.LANDSCAPER.value: ["landscape", "landscaping", "gardening"],
}


def _match_trade_keywords(raw_value: Optional[str]) -> set[str]:
    normalized = _normalize_text(raw_value)
    if not normalized:
        return set()
    return {
        trade
        for trade, keywords in TRADE_INFERENCE_KEYWORDS.items()
        if any(keyword in normalized for keyword in keywords)
    }


def unknown_subcontractor_trade_resolution() -> SubcontractorTradeResolution:
    return SubcontractorTradeResolution(
        trade_specialty=None,
        suggested_trade_specialty=None,
        status=TradeResolutionStatus.UNKNOWN.value,
        source=None,
        confidence=None,
    )


def confirmed_subcontractor_trade_resolution(trade_specialty: Optional[str]) -> SubcontractorTradeResolution:
    trade = (trade_specialty or "").strip().lower() or None
    if trade is None:
        return unknown_subcontractor_trade_resolution()
    return SubcontractorTradeResolution(
        trade_specialty=trade,
        suggested_trade_specialty=None,
        status=TradeResolutionStatus.CONFIRMED.value,
        source="manual",
        confidence=1.0,
    )


def infer_subcontractor_trade_resolution(
    *,
    company_name: Optional[str],
    email: Optional[str],
) -> SubcontractorTradeResolution:
    matches: list[tuple[str, str, float]] = []
    for source, value, confidence in (
        ("company_name", company_name, 0.9),
        ("email_domain", _extract_email_domain(email), 0.72),
    ):
        matched = _match_trade_keywords(value)
        if len(matched) > 1:
            return unknown_subcontractor_trade_resolution()
        if len(matched) == 1:
            matches.append((next(iter(matched)), source, confidence))

    if not matches:
        return unknown_subcontractor_trade_resolution()

    unique_trades = {trade for trade, _source, _confidence in matches}
    if len(unique_trades) != 1:
        return unknown_subcontractor_trade_resolution()

    trade, source, confidence = matches[0]
    return SubcontractorTradeResolution(
        trade_specialty=None,
        suggested_trade_specialty=trade,
        status=TradeResolutionStatus.SUGGESTED.value,
        source=source,
        confidence=confidence,
    )


def asset_is_planning_ready(asset: Asset) -> bool:
    return (
        (asset.type_resolution_status or AssetTypeResolutionStatus.UNKNOWN.value) in ASSET_TYPE_RESOLUTION_READY
        and bool(asset.canonical_type)
    )


def subcontractor_is_planning_ready(subcontractor: Subcontractor) -> bool:
    return (
        (subcontractor.trade_resolution_status or TradeResolutionStatus.UNKNOWN.value)
        == TradeResolutionStatus.CONFIRMED.value
        and bool(subcontractor.trade_specialty)
    )


def _compute_score(
    *,
    unknown_assets: int,
    blocking_unknown_assets: int,
    suggested_trades: int,
    unknown_trades: int,
    blocking_unknown_trades: int,
) -> int:
    score = 100
    score -= unknown_assets * 18
    score -= blocking_unknown_assets * 12
    score -= suggested_trades * 8
    score -= unknown_trades * 14
    score -= blocking_unknown_trades * 10
    return max(0, min(100, score))


def get_project_planning_completeness(project_id: UUID, db: Session) -> dict:
    from .lookahead_engine import calculate_lookahead_for_project, get_latest_snapshot

    window_start = date.today()
    window_end = window_start + timedelta(weeks=6)

    project = (
        db.query(SiteProject)
        .options(
            joinedload(SiteProject.assets).joinedload(Asset.bookings),
            joinedload(SiteProject.subcontractors).joinedload(Subcontractor.bookings),
        )
        .filter(SiteProject.id == project_id)
        .first()
    )
    if project is None:
        raise ValueError("Project not found")

    snapshot = get_latest_snapshot(project_id, db)
    if snapshot is None:
        snapshot = calculate_lookahead_for_project(project_id, db)

    snapshot_rows = (snapshot.data or {}).get("rows", []) if snapshot else []
    has_window_demand = any(
        row.get("demand_hours", 0) and window_start <= date.fromisoformat(row["week_start"]) <= window_end
        for row in snapshot_rows
        if row.get("week_start")
    )

    unknown_assets = inferred_assets = confirmed_assets = 0
    unknown_trades = suggested_trades = confirmed_trades = 0
    blocking_unknown_assets = blocking_unknown_trades = 0
    tasks: list[dict] = []

    for asset in project.assets:
        status = asset.type_resolution_status or AssetTypeResolutionStatus.UNKNOWN.value
        if status == AssetTypeResolutionStatus.CONFIRMED.value:
            confirmed_assets += 1
        elif status == AssetTypeResolutionStatus.INFERRED.value:
            inferred_assets += 1
        else:
            unknown_assets += 1
            affects_window = any(
                booking.project_id == project_id
                and booking.status in ACTIVE_BOOKING_STATUSES
                and booking.booking_date is not None
                and window_start <= booking.booking_date <= window_end
                for booking in asset.bookings
            )
            if affects_window:
                blocking_unknown_assets += 1
            tasks.append(
                {
                    "kind": "confirm_asset_type",
                    "severity": "blocking" if affects_window else "warning",
                    "entity_type": "asset",
                    "entity_id": asset.id,
                    "title": f"Confirm asset type for {asset.name}",
                    "suggested_value": asset.canonical_type,
                    "blocking": affects_window,
                    "affects_next_6_weeks": affects_window,
                }
            )

    for subcontractor in project.subcontractors:
        status = subcontractor.trade_resolution_status or TradeResolutionStatus.UNKNOWN.value
        affects_window = has_window_demand or any(
            booking.project_id == project_id
            and booking.status in ACTIVE_BOOKING_STATUSES
            and booking.booking_date is not None
            and window_start <= booking.booking_date <= window_end
            for booking in subcontractor.bookings
        )
        if status == TradeResolutionStatus.CONFIRMED.value:
            confirmed_trades += 1
        elif status == TradeResolutionStatus.SUGGESTED.value:
            suggested_trades += 1
            tasks.append(
                {
                    "kind": "confirm_subcontractor_trade",
                    "severity": "warning",
                    "entity_type": "subcontractor",
                    "entity_id": subcontractor.id,
                    "title": f"Confirm trade for {subcontractor.company_name or subcontractor.email}",
                    "suggested_value": subcontractor.suggested_trade_specialty,
                    "blocking": False,
                    "affects_next_6_weeks": affects_window,
                }
            )
        else:
            unknown_trades += 1
            if affects_window:
                blocking_unknown_trades += 1
            tasks.append(
                {
                    "kind": "confirm_subcontractor_trade",
                    "severity": "blocking" if affects_window else "warning",
                    "entity_type": "subcontractor",
                    "entity_id": subcontractor.id,
                    "title": f"Confirm trade for {subcontractor.company_name or subcontractor.email}",
                    "suggested_value": subcontractor.suggested_trade_specialty,
                    "blocking": affects_window,
                    "affects_next_6_weeks": affects_window,
                }
            )

    score = _compute_score(
        unknown_assets=unknown_assets,
        blocking_unknown_assets=blocking_unknown_assets,
        suggested_trades=suggested_trades,
        unknown_trades=unknown_trades,
        blocking_unknown_trades=blocking_unknown_trades,
    )

    if blocking_unknown_assets or blocking_unknown_trades:
        status = "blocked"
    elif unknown_assets or unknown_trades or suggested_trades:
        status = "attention"
    else:
        status = "ready"

    tasks.sort(
        key=lambda row: (
            not bool(row["blocking"]),
            row["severity"] != "blocking",
            row["title"].lower(),
        )
    )

    return {
        "project_id": project_id,
        "score": score,
        "status": status,
        "window_start": window_start,
        "window_end": window_end,
        "counts": {
            "unknown_assets": unknown_assets,
            "inferred_assets": inferred_assets,
            "confirmed_assets": confirmed_assets,
            "unknown_trades": unknown_trades,
            "suggested_trades": suggested_trades,
            "confirmed_trades": confirmed_trades,
            "blocking_unknown_assets": blocking_unknown_assets,
            "blocking_unknown_trades": blocking_unknown_trades,
        },
        "tasks": tasks,
    }
