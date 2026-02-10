from typing import Optional, List, Dict, Any
from datetime import datetime
from uuid import UUID

from sqlalchemy.orm import Session
from sqlalchemy import desc

from app.models.booking_audit import BookingAuditLog
from app.models.slot_booking import SlotBooking
from app.models.user import User
from app.models.subcontractor import Subcontractor
from app.schemas.enums import BookingAuditAction, BookingStatus, UserRole
from app.schemas.booking_audit import BookingAuditResponse


def get_actor_name(db: Session, actor_id: UUID, actor_role: UserRole) -> str:
    """
    Get the actor's display name based on role.
    Always fetches from database - no manual override.
    """
    if actor_role == UserRole.SUBCONTRACTOR:
        sub = db.query(Subcontractor).filter(Subcontractor.id == actor_id).first()
        if sub:
            name = f"{sub.first_name} {sub.last_name}"
            if sub.company_name:
                name += f" ({sub.company_name})"
            return name
        return "Unknown Subcontractor"
    else:
        user = db.query(User).filter(User.id == actor_id).first()
        if user:
            return f"{user.first_name} {user.last_name}"
        return "Unknown User"


def log_booking_audit(
    db: Session,
    actor_id: UUID,
    actor_role: UserRole,
    action: BookingAuditAction,
    booking_id: UUID,
    from_status: Optional[BookingStatus] = None,
    to_status: Optional[BookingStatus] = None,
    changes: Optional[Dict[str, Any]] = None,
    comment: Optional[str] = None,  # User-provided, nullable
    auto_commit: bool = False
) -> BookingAuditLog:
    """
    Log a booking audit event.
    
    Args:
        db: Database session
        actor_id: ID of the user/subcontractor performing the action
        actor_role: Role of the actor
        action: The action being performed
        booking_id: ID of the booking being affected
        from_status: Previous status (for status changes)
        to_status: New status (for status changes)
        changes: Dictionary of field changes {field: {old: X, new: Y}}
        comment: User-provided comment (optional - not auto-generated)
        auto_commit: Whether to commit immediately (default False)
    
    Returns:
        The created BookingAuditLog record
    """
    # Always fetch actor name from database
    actor_name = get_actor_name(db, actor_id, actor_role)
    
    audit_log = BookingAuditLog(
        booking_id=booking_id,
        actor_id=actor_id,
        actor_role=actor_role.value if hasattr(actor_role, 'value') else actor_role,
        actor_name=actor_name,
        action=action.value if hasattr(action, 'value') else action,
        from_status=from_status.value if hasattr(from_status, 'value') else from_status,
        to_status=to_status.value if hasattr(to_status, 'value') else to_status,
        changes=changes,
        comment=comment
    )
    
    db.add(audit_log)
    
    if auto_commit:
        db.commit()
        db.refresh(audit_log)
    
    return audit_log


def get_booking_audit_trail(
    db: Session,
    booking_id: UUID,
    skip: int = 0,
    limit: int = 100
) -> List[BookingAuditLog]:
    """Get audit trail for a specific booking."""
    return db.query(BookingAuditLog)\
        .filter(BookingAuditLog.booking_id == booking_id)\
        .order_by(desc(BookingAuditLog.created_at))\
        .offset(skip)\
        .limit(limit)\
        .all()


def get_actor_audit_history(
    db: Session,
    actor_id: UUID,
    skip: int = 0,
    limit: int = 100,
    action_filter: Optional[BookingAuditAction] = None
) -> List[BookingAuditLog]:
    """Get all audit logs for a specific actor."""
    query = db.query(BookingAuditLog)\
        .filter(BookingAuditLog.actor_id == actor_id)
    
    if action_filter:
        filter_val = action_filter.value if hasattr(action_filter, 'value') else action_filter
        query = query.filter(BookingAuditLog.action == filter_val)
    
    return query.order_by(desc(BookingAuditLog.created_at))\
        .offset(skip)\
        .limit(limit)\
        .all()


def get_recent_audit_logs(
    db: Session,
    project_id: Optional[UUID] = None,
    limit: int = 50
) -> List[BookingAuditLog]:
    """Get recent audit logs, optionally filtered by project."""
    query = db.query(BookingAuditLog)
    
    if project_id:
        query = query.join(
            SlotBooking, 
            BookingAuditLog.booking_id == SlotBooking.id
        ).filter(SlotBooking.project_id == project_id)
    
    return query.order_by(desc(BookingAuditLog.created_at))\
        .limit(limit)\
        .all()


def build_changes_dict(
    old_values: Dict[str, Any],
    new_values: Dict[str, Any],
    fields_to_track: Optional[List[str]] = None
) -> Optional[Dict[str, Dict[str, Any]]]:
    """
    Build a changes dictionary comparing old and new values.
    
    Returns: {field_name: {"old": old_value, "new": new_value}} or None if no changes
    """
    changes = {}
    
    if fields_to_track is None:
        fields_to_track = [
            'booking_date', 'start_time', 'end_time', 
            'purpose', 'notes', 'asset_id', 'subcontractor_id'
        ]
    
    for field in fields_to_track:
        old_val = old_values.get(field)
        new_val = new_values.get(field)
        
        # Convert to comparable types
        if hasattr(old_val, 'isoformat'):
            old_val = old_val.isoformat() if old_val else None
        if hasattr(new_val, 'isoformat'):
            new_val = new_val.isoformat() if new_val else None
        
        # Convert UUIDs to strings
        if isinstance(old_val, UUID):
            old_val = str(old_val)
        if isinstance(new_val, UUID):
            new_val = str(new_val)
        
        if old_val != new_val and new_val is not None:
            changes[field] = {"old": old_val, "new": new_val}
    
    return changes if changes else None