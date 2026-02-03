from typing import List, Optional, Union
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status, Query, Path
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.security import get_current_active_user, get_user_role, get_entity_id
from app.models.user import User
from app.models.subcontractor import Subcontractor
from app.schemas.enums import UserRole, BookingAuditAction
from app.schemas.booking_audit import (
    BookingAuditResponse,
    BookingAuditTrailResponse
)
from app.crud import booking_audit as audit_crud
from app.crud import slot_booking as booking_crud
from app.crud import site_project as project_crud

router = APIRouter(prefix="/bookings", tags=["Booking Audit"])


def check_audit_access(
    db: Session,
    booking_id: UUID,
    user_role: UserRole,
    user_id: UUID
) -> None:
    """Verify user has access to view booking audit trail."""
    booking = booking_crud.get_booking(db, booking_id)
    if not booking:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Booking not found"
        )
    
    # Admins can view everything
    if user_role == UserRole.ADMIN:
        return
    
    # Booking owner (manager who created)
    if booking.manager_id == user_id:
        return
    
    # Subcontractor who owns the booking
    if user_role == UserRole.SUBCONTRACTOR and booking.subcontractor_id == user_id:
        return
    
    # Project manager
    if booking.project_id and user_role == UserRole.MANAGER:
        if project_crud.is_project_manager(db, booking.project_id, user_id):
            return
    
    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail="You don't have access to this booking's audit trail"
    )


def audit_log_to_response(log) -> BookingAuditResponse:
    """Convert audit log model to response schema."""
    return BookingAuditResponse(
        id=log.id,
        booking_id=log.booking_id,
        actor_id=log.actor_id,
        actor_role=log.actor_role,
        actor_name=log.actor_name,
        action=log.action,
        from_status=log.from_status,
        to_status=log.to_status,
        changes=log.changes,
        comment=log.comment,
        created_at=log.created_at
    )


@router.get("/{booking_id}/audit", response_model=BookingAuditTrailResponse)
def get_booking_audit_trail(
    booking_id: UUID = Path(..., description="Booking ID"),
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db),
    current_entity: Union[User, Subcontractor] = Depends(get_current_active_user)
) -> BookingAuditTrailResponse:
    """
    Get the complete audit trail for a booking.
    
    Returns chronological history of all actions taken on the booking.
    """
    user_role = get_user_role(current_entity)
    user_id = get_entity_id(current_entity)
    
    check_audit_access(db, booking_id, user_role, user_id)
    
    audit_logs = audit_crud.get_booking_audit_trail(db, booking_id, skip, limit)
    
    return BookingAuditTrailResponse(
        booking_id=booking_id,
        history=[audit_log_to_response(log) for log in audit_logs]
    )


@router.get("/audit/my-activity", response_model=List[BookingAuditResponse])
def get_my_audit_activity(
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    action: Optional[BookingAuditAction] = Query(None, description="Filter by action type"),
    db: Session = Depends(get_db),
    current_entity: Union[User, Subcontractor] = Depends(get_current_active_user)
) -> List[BookingAuditResponse]:
    """
    Get the current user's audit activity history.
    
    Shows all booking actions performed by the current user.
    """
    user_id = get_entity_id(current_entity)
    
    audit_logs = audit_crud.get_actor_audit_history(
        db, 
        actor_id=user_id,
        skip=skip,
        limit=limit,
        action_filter=action
    )
    
    return [audit_log_to_response(log) for log in audit_logs]


@router.get("/audit/project/{project_id}", response_model=List[BookingAuditResponse])
def get_project_audit_logs(
    project_id: UUID = Path(..., description="Project ID"),
    limit: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db),
    current_entity: Union[User, Subcontractor] = Depends(get_current_active_user)
) -> List[BookingAuditResponse]:
    """
    Get recent audit logs for a project.
    
    Admin and project managers only.
    """
    user_role = get_user_role(current_entity)
    user_id = get_entity_id(current_entity)
    
    # Check project access
    if user_role == UserRole.SUBCONTRACTOR:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Subcontractors cannot view project-wide audit logs"
        )
    
    if user_role != UserRole.ADMIN:
        if not project_crud.has_project_access(db, project_id, user_id):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="You don't have access to this project"
            )
    
    audit_logs = audit_crud.get_recent_audit_logs(db, project_id=project_id, limit=limit)
    
    return [audit_log_to_response(log) for log in audit_logs]