from sqlalchemy.orm import Session, joinedload
from sqlalchemy import and_, or_, func, cast, Date
from typing import List, Optional, Tuple, Dict, Any
from uuid import UUID
from datetime import datetime, date, time, timedelta, timezone
from decimal import Decimal

from ..models.asset import Asset, AssetStatus
from ..models.site_project import SiteProject
from ..models.slot_booking import SlotBooking
from ..schemas.enums import BookingStatus
from ..schemas.asset import (
    AssetCreate, AssetUpdate, AssetTransfer,
    AssetDetailResponse, AssetAvailabilityCheck,
    AssetAvailabilityResponse, BookingConflict,
    MaintenanceRecord
)


def resolve_maintenance_status(db: Session, asset: Asset) -> Asset:
    """Check and update asset status based on maintenance date window.

    - If today falls within [start, end], set status to MAINTENANCE.
    - If end date has passed, clear dates and revert to AVAILABLE.
    Only writes to DB when a transition actually occurs.
    Retired assets are never modified.
    """
    if not asset.maintenance_start_date or not asset.maintenance_end_date:
        return asset

    # Never auto-modify retired assets
    if asset.status == AssetStatus.RETIRED:
        return asset

    today = date.today()

    if asset.maintenance_start_date <= today <= asset.maintenance_end_date:
        # Active maintenance window
        if asset.status != AssetStatus.MAINTENANCE:
            asset.status = AssetStatus.MAINTENANCE
            asset.updated_at = datetime.now(timezone.utc)
            db.commit()
            db.refresh(asset)
    elif today > asset.maintenance_end_date:
        # Maintenance window expired — clean up
        asset.maintenance_start_date = None
        asset.maintenance_end_date = None
        if asset.status == AssetStatus.MAINTENANCE:
            asset.status = AssetStatus.AVAILABLE
        asset.updated_at = datetime.now(timezone.utc)
        db.commit()
        db.refresh(asset)

    return asset


def _resolve_maintenance_batch(db: Session, assets: List[Asset]) -> List[Asset]:
    """Batch resolve maintenance status. Commits once if any transitions occurred.
    Retired assets are skipped entirely.
    """
    today = date.today()
    changed = []

    for asset in assets:
        # Never auto-modify retired assets
        if asset.status == AssetStatus.RETIRED:
            continue

        if not asset.maintenance_start_date or not asset.maintenance_end_date:
            continue

        if asset.maintenance_start_date <= today <= asset.maintenance_end_date:
            if asset.status != AssetStatus.MAINTENANCE:
                asset.status = AssetStatus.MAINTENANCE
                asset.updated_at = datetime.now(timezone.utc)
                changed.append(asset)
        elif today > asset.maintenance_end_date:
            asset.maintenance_start_date = None
            asset.maintenance_end_date = None
            if asset.status == AssetStatus.MAINTENANCE:
                asset.status = AssetStatus.AVAILABLE
            asset.updated_at = datetime.now(timezone.utc)
            changed.append(asset)

    if changed:
        db.commit()
        for asset in changed:
            db.refresh(asset)

    return assets


def create_asset(db: Session, asset: AssetCreate, user_id: UUID = None) -> Asset:
    """Create a new asset"""
    # Check if project exists
    project = db.query(SiteProject).filter(SiteProject.id == asset.project_id).first()
    if not project:
        raise ValueError(f"Project with id {asset.project_id} not found")

    if asset.status == AssetStatus.RETIRED and (asset.maintenance_start_date or asset.maintenance_end_date):
        raise ValueError("Cannot set maintenance dates on a retired asset")

    db_asset = Asset(
        project_id=asset.project_id,
        asset_code=asset.asset_code,
        name=asset.name,
        type=asset.type,
        description=asset.description,
        purchase_date=asset.purchase_date,
        purchase_value=asset.purchase_value,
        current_value=asset.current_value or asset.purchase_value,
        status=asset.status or AssetStatus.AVAILABLE,
        maintenance_start_date=asset.maintenance_start_date,
        maintenance_end_date=asset.maintenance_end_date
    )

    db.add(db_asset)
    db.commit()
    db.refresh(db_asset)
    return db_asset


def get_asset(db: Session, asset_id: UUID) -> Optional[Asset]:
    """Get an asset by ID"""
    asset = db.query(Asset).filter(Asset.id == asset_id).first()
    if asset:
        resolve_maintenance_status(db, asset)
    return asset


def get_asset_with_details(db: Session, asset_id: UUID) -> Optional[Asset]:
    """Get asset with all relationships loaded"""
    asset = db.query(Asset)\
        .options(
            joinedload(Asset.project),
            joinedload(Asset.bookings)
        )\
        .filter(Asset.id == asset_id)\
        .first()
    if asset:
        resolve_maintenance_status(db, asset)
    return asset


def get_asset_by_code(db: Session, asset_code: str) -> Optional[Asset]:
    """Get an asset by asset code"""
    asset = db.query(Asset).filter(Asset.asset_code == asset_code).first()
    if asset:
        resolve_maintenance_status(db, asset)
    return asset


def get_assets_paginated(
    db: Session,
    project_id: Optional[UUID] = None,
    status: Optional[AssetStatus] = None,
    asset_type: Optional[str] = None,
    skip: int = 0,
    limit: int = 100
) -> Tuple[List[Asset], int]:
    """Get paginated assets with filters"""
    query = db.query(Asset)

    if project_id:
        query = query.filter(Asset.project_id == project_id)

    if status:
        query = query.filter(Asset.status == status)

    if asset_type:
        query = query.filter(Asset.type == asset_type)

    # Get total count before pagination
    total = query.count()

    # Apply pagination and ordering
    assets = query.order_by(Asset.created_at.desc())\
        .offset(skip).limit(limit).all()
    _resolve_maintenance_batch(db, assets)

    # Post-resolve: batch resolution may have changed statuses (e.g. AVAILABLE
    # → MAINTENANCE). Re-filter so the returned page only contains assets that
    # still match the requested status, and adjust total accordingly.
    if status:
        pre_filter_count = len(assets)
        assets = [a for a in assets if a.status == status]
        total -= (pre_filter_count - len(assets))

    return assets, total


def get_assets_brief(
    db: Session,
    project_id: UUID,
    status: Optional[AssetStatus] = None
) -> List[Asset]:
    """Get brief list of assets for selectors/dropdowns"""
    query = db.query(Asset).filter(Asset.project_id == project_id)

    if status:
        query = query.filter(Asset.status == status)
    else:
        # By default, exclude retired assets for brief lists
        query = query.filter(Asset.status != AssetStatus.RETIRED)

    assets = query.order_by(Asset.name).all()
    _resolve_maintenance_batch(db, assets)

    # Post-resolve: re-filter to ensure status drift from batch resolution
    # doesn't leak assets that no longer match the requested filter.
    if status:
        assets = [a for a in assets if a.status == status]
    else:
        assets = [a for a in assets if a.status != AssetStatus.RETIRED]

    return assets


def get_asset_detail(db: Session, asset_id: UUID) -> Optional[AssetDetailResponse]:
    """Get detailed asset information with statistics"""
    asset = get_asset_with_details(db, asset_id)

    if not asset:
        return None

    # Calculate statistics
    total_bookings = len(asset.bookings)
    active_bookings = len([b for b in asset.bookings if b.status == BookingStatus.CONFIRMED])
    completed_bookings = len([b for b in asset.bookings if b.status == BookingStatus.COMPLETED])

    # Calculate utilization rate
    utilization_rate = 0.0
    if asset.purchase_date:
        days_owned = (date.today() - asset.purchase_date).days
        if days_owned > 0:
            # Count unique days with bookings
            booked_days = db.query(
                func.count(func.distinct(SlotBooking.booking_date))
            ).filter(
                and_(
                    SlotBooking.asset_id == asset_id,
                    SlotBooking.status.in_([BookingStatus.CONFIRMED, BookingStatus.COMPLETED])
                )
            ).scalar() or 0
            utilization_rate = min((booked_days / days_owned) * 100, 100.0)

    # Calculate depreciation if both values exist
    depreciation_amount = None
    depreciation_percentage = None
    if asset.purchase_value and asset.current_value:
        depreciation_amount = float(asset.purchase_value - asset.current_value)
        if asset.purchase_value > 0:
            depreciation_percentage = (depreciation_amount / float(asset.purchase_value)) * 100

    # Get recent bookings
    recent_bookings = db.query(SlotBooking)\
        .filter(SlotBooking.asset_id == asset_id)\
        .order_by(SlotBooking.booking_date.desc())\
        .limit(10).all()

    # Get maintenance history (placeholder - implement based on your maintenance model)
    maintenance_history = []

    return AssetDetailResponse(
        id=asset.id,
        project_id=asset.project_id,
        asset_code=asset.asset_code,
        name=asset.name,
        type=asset.type,
        description=asset.description,
        purchase_date=asset.purchase_date,
        purchase_value=asset.purchase_value,
        current_value=asset.current_value,
        status=asset.status,
        created_at=asset.created_at,
        updated_at=asset.updated_at,
        project_name=asset.project.name if asset.project else None,
        project_location=asset.project.location if asset.project else None,
        total_bookings=total_bookings,
        active_bookings=active_bookings,
        completed_bookings=completed_bookings,
        utilization_rate=round(utilization_rate, 2),
        depreciation_amount=depreciation_amount,
        depreciation_percentage=round(depreciation_percentage, 2) if depreciation_percentage else None,
        maintenance_history=maintenance_history,
        recent_bookings=[{
            "id": b.id,
            "booking_date": b.booking_date,
            "start_time": b.start_time,
            "end_time": b.end_time,
            "subcontractor_id": b.subcontractor_id,
            "status": b.status
        } for b in recent_bookings]
    )


def update_asset(
    db: Session,
    asset_id: UUID,
    asset_update: AssetUpdate,
    user_id: UUID = None
) -> Optional[Asset]:
    """Update an asset"""
    db_asset = get_asset(db, asset_id)
    if not db_asset:
        return None

    update_data = asset_update.model_dump(exclude_unset=True)

    # If updating asset_code, check uniqueness
    if 'asset_code' in update_data and update_data['asset_code'] != db_asset.asset_code:
        existing = get_asset_by_code(db, update_data['asset_code'])
        if existing:
            raise ValueError(f"Asset code {update_data['asset_code']} already exists")

    # Determine effective status after this update
    effective_status = update_data.get('status', db_asset.status)

    # Block maintenance dates on retired assets; clear existing dates when retiring
    if effective_status == AssetStatus.RETIRED:
        if update_data.get('maintenance_start_date') is not None or update_data.get('maintenance_end_date') is not None:
            raise ValueError("Cannot set maintenance dates on a retired asset")
        if db_asset.maintenance_start_date or db_asset.maintenance_end_date:
            update_data['maintenance_start_date'] = None
            update_data['maintenance_end_date'] = None

    # Manual override: setting status to AVAILABLE always clears maintenance
    # dates — the asset is explicitly marked operational.  Use direct
    # assignment so that any dates provided in the same payload are
    # discarded (setdefault would preserve them).
    if update_data.get('status') == AssetStatus.AVAILABLE:
        update_data['maintenance_start_date'] = None
        update_data['maintenance_end_date'] = None

    for field, value in update_data.items():
        setattr(db_asset, field, value)

    db_asset.updated_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(db_asset)

    # Re-resolve maintenance status so the returned asset reflects the
    # correct effective status (e.g. user sets status=available but also
    # provides maintenance dates that cover today → status should be
    # maintenance in the response, not on the *next* GET).
    resolve_maintenance_status(db, db_asset)

    return db_asset


def transfer_asset(
    db: Session,
    asset_id: UUID,
    transfer: AssetTransfer,
    user_id: UUID = None
) -> Optional[Asset]:
    """Transfer asset to another project"""
    db_asset = get_asset(db, asset_id)
    if not db_asset:
        return None

    # Check if new project exists
    new_project = db.query(SiteProject).filter(
        SiteProject.id == transfer.new_project_id
    ).first()
    if not new_project:
        raise ValueError("Target project not found")

    # Check for active bookings that might be affected
    active_bookings = db.query(SlotBooking).filter(
        and_(
            SlotBooking.asset_id == asset_id,
            SlotBooking.booking_date >= date.today(),
            SlotBooking.status == BookingStatus.CONFIRMED
        )
    ).count()

    if active_bookings > 0 and not transfer.force_transfer:
        raise ValueError(f"Asset has {active_bookings} active future bookings. Use force_transfer=true to proceed.")

    # Store old project for logging
    old_project_id = db_asset.project_id

    # Update asset project
    db_asset.project_id = transfer.new_project_id
    if transfer.update_status:
        db_asset.status = transfer.update_status
    db_asset.updated_at = datetime.now(timezone.utc)

    # Update related bookings if needed
    if transfer.update_bookings:
        db.query(SlotBooking).filter(
            and_(
                SlotBooking.asset_id == asset_id,
                SlotBooking.project_id == old_project_id
            )
        ).update({"project_id": transfer.new_project_id})

    db.commit()
    db.refresh(db_asset)
    return db_asset


def check_asset_availability(
    db: Session,
    check: AssetAvailabilityCheck
) -> AssetAvailabilityResponse:
    """Check if asset is available for booking"""

    # Get the asset first to check its status
    asset = get_asset(db, check.asset_id)
    if not asset:
        return AssetAvailabilityResponse(
            is_available=False,
            conflicts=[],
            reason="Asset not found"
        )

    # Block permanently unavailable statuses
    if asset.status in (AssetStatus.MAINTENANCE, AssetStatus.RETIRED):
        return AssetAvailabilityResponse(
            is_available=False,
            conflicts=[],
            reason=f"Asset is {asset.status.value}"
        )

    # Block bookings during scheduled maintenance windows
    if asset.maintenance_start_date and asset.maintenance_end_date:
        if asset.maintenance_start_date <= check.date <= asset.maintenance_end_date:
            return AssetAvailabilityResponse(
                is_available=False,
                conflicts=[],
                reason=f"Asset is under scheduled maintenance from {asset.maintenance_start_date} to {asset.maintenance_end_date}"
            )

    # Parse time strings to time objects
    start_hour, start_min = map(int, check.start_time.split(':'))
    end_hour, end_min = map(int, check.end_time.split(':'))

    check_start_time = time(start_hour, start_min)
    check_end_time = time(end_hour, end_min)

    # Find conflicting bookings
    conflicts = db.query(SlotBooking).filter(
        and_(
            SlotBooking.asset_id == check.asset_id,
            SlotBooking.booking_date == check.date,
            SlotBooking.status.in_([BookingStatus.CONFIRMED, BookingStatus.PENDING]),
            or_(
                # New booking starts during existing booking
                and_(
                    SlotBooking.start_time <= check_start_time,
                    SlotBooking.end_time > check_start_time
                ),
                # New booking ends during existing booking
                and_(
                    SlotBooking.start_time < check_end_time,
                    SlotBooking.end_time >= check_end_time
                ),
                # New booking completely covers existing booking
                and_(
                    SlotBooking.start_time >= check_start_time,
                    SlotBooking.end_time <= check_end_time
                )
            )
        )
    ).all()

    booking_conflicts = [
        BookingConflict(
            booking_id=booking.id,
            start_time=booking.start_time.strftime('%H:%M') if booking.start_time else '',
            end_time=booking.end_time.strftime('%H:%M') if booking.end_time else '',
            booked_by=str(booking.subcontractor_id) if booking.subcontractor_id else 'Unknown',
            status=booking.status
        )
        for booking in conflicts
    ]

    return AssetAvailabilityResponse(
        is_available=len(conflicts) == 0,
        conflicts=booking_conflicts,
        asset_status=asset.status.value
    )


def delete_asset(db: Session, asset_id: UUID, user_id: UUID = None) -> bool:
    """Delete or retire an asset"""
    db_asset = get_asset(db, asset_id)
    if not db_asset:
        return False

    # Check if asset has any bookings
    has_bookings = db.query(SlotBooking).filter(
        SlotBooking.asset_id == asset_id
    ).first() is not None

    if has_bookings:
        # Soft delete - change status to retired
        db_asset.status = AssetStatus.RETIRED
        db_asset.updated_at = datetime.now(timezone.utc)
        db.commit()
    else:
        # Hard delete if no bookings
        db.delete(db_asset)
        db.commit()

    return True


def get_available_assets(
    db: Session,
    project_id: UUID,
    check_date: date,
    start_time: str,
    end_time: str,
    asset_type: Optional[str] = None
) -> List[Asset]:
    """Get all available assets for a specific time slot"""
    # Get all assets for the project (exclude maintenance and retired)
    query = db.query(Asset).filter(
        and_(
            Asset.project_id == project_id,
            Asset.status.notin_([AssetStatus.MAINTENANCE, AssetStatus.RETIRED])
        )
    )

    if asset_type:
        query = query.filter(Asset.type == asset_type)

    assets = query.all()
    _resolve_maintenance_batch(db, assets)
    assets = [a for a in assets if a.status not in (AssetStatus.MAINTENANCE, AssetStatus.RETIRED)]

    available_assets = []
    for asset in assets:
        check = AssetAvailabilityCheck(
            asset_id=asset.id,
            date=check_date,
            start_time=start_time,
            end_time=end_time
        )
        availability = check_asset_availability(db, check)
        if availability.is_available:
            available_assets.append(asset)

    return available_assets


def update_asset_value(
    db: Session,
    asset_id: UUID,
    new_value: Decimal,
    user_id: UUID = None
) -> Optional[Asset]:
    """Update asset current value (for depreciation)"""
    db_asset = get_asset(db, asset_id)
    if not db_asset:
        return None

    db_asset.current_value = new_value
    db_asset.updated_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(db_asset)
    return db_asset


def update_asset_status(
    db: Session,
    asset_id: UUID,
    new_status: AssetStatus,
    user_id: UUID = None
) -> Optional[Asset]:
    """Update asset status"""
    db_asset = get_asset(db, asset_id)
    if not db_asset:
        return None

    # Check if status transition is valid
    if db_asset.status == AssetStatus.RETIRED and new_status != AssetStatus.RETIRED:
        # Check if asset has no future bookings
        future_bookings = db.query(SlotBooking).filter(
            and_(
                SlotBooking.asset_id == asset_id,
                SlotBooking.booking_date >= date.today()
            )
        ).count()

        if future_bookings > 0:
            raise ValueError("Cannot reactivate asset with future bookings")

    db_asset.status = new_status
    db_asset.updated_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(db_asset)
    return db_asset


# Additional helper functions

def get_assets_by_type(
    db: Session,
    project_id: UUID,
    asset_type: str,
    include_unavailable: bool = False
) -> List[Asset]:
    """Get all assets of a specific type in a project"""
    query = db.query(Asset).filter(
        and_(
            Asset.project_id == project_id,
            Asset.type == asset_type
        )
    )

    if not include_unavailable:
        query = query.filter(Asset.status.notin_([AssetStatus.MAINTENANCE, AssetStatus.RETIRED]))

    assets = query.order_by(Asset.name).all()
    _resolve_maintenance_batch(db, assets)

    # Post-resolve: status may have drifted (e.g. AVAILABLE → MAINTENANCE).
    # Re-filter so callers that requested only available assets don't get
    # maintenance or retired assets in the result set.
    if not include_unavailable:
        assets = [a for a in assets if a.status not in (AssetStatus.MAINTENANCE, AssetStatus.RETIRED)]

    return assets


def get_asset_statistics(
    db: Session,
    asset_id: UUID,
    start_date: Optional[date] = None,
    end_date: Optional[date] = None
) -> Dict[str, Any]:
    """Get comprehensive statistics for an asset"""
    asset = get_asset(db, asset_id)
    if not asset:
        return {}

    # Build base query for bookings
    bookings_query = db.query(SlotBooking).filter(
        SlotBooking.asset_id == asset_id
    )

    if start_date:
        bookings_query = bookings_query.filter(SlotBooking.booking_date >= start_date)
    if end_date:
        bookings_query = bookings_query.filter(SlotBooking.booking_date <= end_date)

    bookings = bookings_query.all()

    # Calculate statistics
    total_bookings = len(bookings)
    confirmed_bookings = sum(1 for b in bookings if b.status == BookingStatus.CONFIRMED)
    completed_bookings = sum(1 for b in bookings if b.status == BookingStatus.COMPLETED)
    cancelled_bookings = sum(1 for b in bookings if b.status == BookingStatus.CANCELLED)

    # Calculate total hours booked
    total_hours = 0
    for booking in bookings:
        if booking.start_time and booking.end_time and booking.status in [BookingStatus.CONFIRMED, BookingStatus.COMPLETED]:
            start_dt = datetime.combine(booking.booking_date, booking.start_time)
            end_dt = datetime.combine(booking.booking_date, booking.end_time)
            hours = (end_dt - start_dt).total_seconds() / 3600
            total_hours += hours

    # Calculate value depreciation
    depreciation_info = {}
    if asset.purchase_value and asset.current_value:
        depreciation_amount = float(asset.purchase_value - asset.current_value)
        depreciation_percentage = (depreciation_amount / float(asset.purchase_value)) * 100
        depreciation_info = {
            "original_value": float(asset.purchase_value),
            "current_value": float(asset.current_value),
            "depreciation_amount": depreciation_amount,
            "depreciation_percentage": round(depreciation_percentage, 2)
        }

    # Calculate utilization rate for the period
    if start_date and end_date:
        total_days = (end_date - start_date).days + 1
        booked_days = len(set(b.booking_date for b in bookings if b.status in [BookingStatus.CONFIRMED, BookingStatus.COMPLETED]))
        utilization_rate = (booked_days / total_days) * 100 if total_days > 0 else 0
    else:
        utilization_rate = 0

    return {
        "asset_id": asset_id,
        "asset_name": asset.name,
        "asset_code": asset.asset_code,
        "asset_type": asset.type,
        "status": asset.status.value,
        "bookings": {
            "total": total_bookings,
            "confirmed": confirmed_bookings,
            "completed": completed_bookings,
            "cancelled": cancelled_bookings,
            "total_hours": round(total_hours, 2)
        },
        "utilization_rate": round(utilization_rate, 2),
        "depreciation": depreciation_info,
        "period": {
            "start_date": start_date,
            "end_date": end_date
        }
    }


def search_assets(
    db: Session,
    search_term: str,
    project_id: Optional[UUID] = None,
    skip: int = 0,
    limit: int = 100
) -> Tuple[List[Asset], int]:
    """Search assets by name, code, or type"""
    query = db.query(Asset)

    # Apply search filter
    search_filter = or_(
        Asset.name.ilike(f"%{search_term}%"),
        Asset.asset_code.ilike(f"%{search_term}%"),
        Asset.type.ilike(f"%{search_term}%"),
        Asset.description.ilike(f"%{search_term}%") if Asset.description else False
    )
    query = query.filter(search_filter)

    # Filter by project if specified
    if project_id:
        query = query.filter(Asset.project_id == project_id)

    # Exclude retired assets from search by default
    query = query.filter(Asset.status != AssetStatus.RETIRED)

    total = query.count()
    assets = query.order_by(Asset.name).offset(skip).limit(limit).all()
    _resolve_maintenance_batch(db, assets)

    # Post-resolve: ensure no status drift leaked retired assets into results
    pre_filter_count = len(assets)
    assets = [a for a in assets if a.status != AssetStatus.RETIRED]
    total -= (pre_filter_count - len(assets))

    return assets, total


def get_assets_requiring_maintenance(
    db: Session,
    project_id: Optional[UUID] = None,
    days_threshold: int = 90
) -> List[Asset]:
    """Get assets that might require maintenance based on usage"""
    query = db.query(Asset).filter(
        Asset.status == AssetStatus.AVAILABLE
    )

    if project_id:
        query = query.filter(Asset.project_id == project_id)

    threshold_date = date.today() - timedelta(days=days_threshold)

    # Single query with subquery instead of N+1 loop
    from ..schemas.enums import BookingStatus
    booking_count_subq = db.query(
        SlotBooking.asset_id,
        func.count(SlotBooking.id).label("booking_count")
    ).filter(
        SlotBooking.booking_date >= threshold_date,
        SlotBooking.status == BookingStatus.COMPLETED
    ).group_by(SlotBooking.asset_id).subquery()

    results = query.join(
        booking_count_subq,
        Asset.id == booking_count_subq.c.asset_id
    ).filter(
        booking_count_subq.c.booking_count > 20
    ).all()

    return results