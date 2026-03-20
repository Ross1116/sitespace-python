from sqlalchemy.orm import Session
from sqlalchemy import and_, or_, func
from sqlalchemy.exc import IntegrityError
from typing import Optional, List, Dict, Any
from uuid import UUID
from datetime import datetime, timezone
from ..models.slot_booking import SlotBooking
from ..models.subcontractor import Subcontractor
from ..models.site_project import SiteProject

from ..models.user import User
from ..schemas.enums import BookingStatus, ProjectStatus, UserRole
from ..schemas.user import (
    UserCreate,
    UserUpdate,
    UserDetailResponse,
    UserListResponse,
    UserResponse,
    UserBriefResponse
)
from ..schemas.site_project import SiteProjectBriefResponse
from ..core.password import get_password_hash, verify_password
from ..core.security import normalize_email as _normalize_email


def get_user(db: Session, user_id: str) -> Optional[User]:
    """Get user by ID"""
    return db.query(User).filter(User.id == user_id).first()


def get_user_by_email(db: Session, email: str) -> Optional[User]:
    """Get user by email (case-insensitive)"""
    normalized_email = _normalize_email(email)
    return db.query(User).filter(
        func.lower(User.email) == normalized_email
    ).first()


def get_users(
    db: Session,
    skip: int = 0,
    limit: int = 100,
    role: Optional[UserRole] = None,
    is_active: Optional[bool] = None,
    email_verified: Optional[bool] = None,
    search: Optional[str] = None
) -> UserListResponse:
    """
    Get paginated list of users with optional filters
    """
    query = db.query(User)

    # Apply filters
    if role:
        query = query.filter(User.role == role.value)

    if is_active is not None:
        query = query.filter(User.is_active == is_active)

    if email_verified is not None:
        query = query.filter(User.email_verified == email_verified)

    if search:
        search_filter = f"%{search}%"
        query = query.filter(
            or_(
                User.email.ilike(search_filter),
                User.first_name.ilike(search_filter),
                User.last_name.ilike(search_filter),
                User.phone.ilike(search_filter)
            )
        )

    # Get total count
    total = query.count()

    # Get paginated results
    users = query.offset(skip).limit(limit).all()

    # Check if there are more results
    has_more = (skip + limit) < total

    return UserListResponse(
        users=[UserResponse.model_validate(user) for user in users],
        total=total,
        skip=skip,
        limit=limit,
        has_more=has_more
    )


def get_user_detail(db: Session, user_id: str) -> Optional[UserDetailResponse]:
    """Get detailed user information with relationships"""
    user = db.query(User).filter(User.id == user_id).first()

    if not user:
        return None

    # Get counts and active projects based on role
    if user.role == UserRole.MANAGER.value:
        managed_projects_count = len(user.managed_projects)
        active_projects = [
            SiteProjectBriefResponse.model_validate(project)
            for project in user.managed_projects
            if project.status == ProjectStatus.ACTIVE.value
        ][:5]  # Limit to 5 most recent active projects
        subcontractors_count = 0
    else:
        managed_projects_count = 0
        active_projects = [
            SiteProjectBriefResponse.model_validate(project)
            for project in user.assigned_projects
            if project.status == ProjectStatus.ACTIVE.value
        ][:5]
        # Count actual subcontractors if admin
        if user.role == UserRole.ADMIN.value:
            subcontractors_count = db.query(Subcontractor).count()
        else:
            subcontractors_count = 0

    return UserDetailResponse(
        id=user.id,
        email=user.email,
        first_name=user.first_name,
        last_name=user.last_name,
        phone=user.phone,
        role=UserRole(user.role) if user.role in UserRole._value2member_map_ else None,
        is_active=user.is_active,
        created_at=user.created_at,
        updated_at=user.updated_at,
        managed_projects_count=managed_projects_count,
        active_projects=active_projects,
        subcontractors_count=subcontractors_count
    )


def create_user(db: Session, user_data: UserCreate) -> User:
    """Create new user"""
    # Remove confirm_password from data
    user_dict = user_data.model_dump()
    user_dict.pop('confirm_password', None)
    hashed_password = get_password_hash(user_data.password)

    db_user = User(
        email=_normalize_email(user_data.email),
        password=hashed_password,
        first_name=user_data.first_name,
        last_name=user_data.last_name,
        phone=user_data.phone,
        role=user_data.role,
        is_active=True,
        email_verified=False
    )

    db.add(db_user)
    db.commit()
    db.refresh(db_user)

    return db_user


def update_user(
    db: Session,
    user: User,
    user_update: UserUpdate
) -> User:
    """Update user information"""
    update_data = user_update.model_dump(exclude_unset=True)

    for field, value in update_data.items():
        if field == "email" and value:
            setattr(user, field, _normalize_email(value))
        else:
            setattr(user, field, value)

    user.updated_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(user)

    return user


def update_password(db: Session, user: User, new_password: str) -> User:
    """Update user password"""
    user.password = get_password_hash(new_password)
    user.updated_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(user)
    return user


def verify_email(db: Session, user: User) -> User:
    """Mark user email as verified"""
    user.email_verified = True
    user.updated_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(user)
    return user


def update_user_role(db: Session, user: User, new_role: UserRole) -> User:
    """Update user role"""
    user.role = new_role.value
    user.updated_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(user)
    return user


def toggle_user_status(db: Session, user: User) -> User:
    """Toggle user active status"""
    user.is_active = not user.is_active
    user.updated_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(user)
    return user


def deactivate_user(db: Session, user: User) -> User:
    """Deactivate user account"""
    user.is_active = False
    user.updated_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(user)
    return user


def activate_user(db: Session, user: User) -> User:
    """Activate user account"""
    user.is_active = True
    user.updated_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(user)
    return user


def delete_user(db: Session, user: User) -> bool:
    """
    Delete a user if safe; otherwise deactivate to preserve booking history.

    Returns:
        True: the user record was permanently deleted.
        False: the user had bookings and was deactivated instead.
    """
    active_statuses = (
        BookingStatus.PENDING,
        BookingStatus.CONFIRMED,
        BookingStatus.IN_PROGRESS,
    )

    booking_count = (
        db.query(func.count(SlotBooking.id))
        .filter(SlotBooking.manager_id == user.id)
        .filter(SlotBooking.status.in_(active_statuses))
        .scalar()
        or 0
    )

    if booking_count > 0:
        # Preserve booking history: deactivate instead of deleting.
        user.is_active = False
        user.updated_at = datetime.now(timezone.utc)
        db.commit()
        db.refresh(user)
        return False

    db.delete(user)
    try:
        db.commit()
        return True
    except IntegrityError:
        # Preserve booking history (and handle FK-RESTRICT races): deactivate instead.
        db.rollback()
        existing_user = db.query(User).filter(User.id == user.id).first()
        if existing_user is None:
            return True

        existing_user.is_active = False
        existing_user.updated_at = datetime.now(timezone.utc)
        db.commit()
        db.refresh(existing_user)
        return False


def get_user_projects(db: Session, user: User) -> List[SiteProject]:
    """Get all projects associated with a user"""
    if user.role == UserRole.MANAGER.value or user.role == UserRole.ADMIN.value:
        return user.managed_projects
    return []


def get_user_bookings(db: Session, user: User) -> List[SlotBooking]:
    """Get all bookings for a user"""
    return user.bookings


def count_users(
    db: Session,
    role: Optional[UserRole] = None,
    is_active: Optional[bool] = None,
    email_verified: Optional[bool] = None
) -> int:
    """Count users with optional filters"""
    query = db.query(User)

    if role:
        query = query.filter(User.role == role.value)

    if is_active is not None:
        query = query.filter(User.is_active == is_active)

    if email_verified is not None:
        query = query.filter(User.email_verified == email_verified)

    return query.count()


def get_users_by_role(db: Session, role: UserRole) -> List[User]:
    """Get all active users with specific role"""
    return db.query(User).filter(
        User.role == role.value,
        User.is_active == True,
        User.email_verified == True
    ).all()


def get_managers(db: Session, active_only: bool = True) -> List[User]:
    """Get all managers"""
    query = db.query(User).filter(User.role == UserRole.MANAGER.value)

    if active_only:
        query = query.filter(
            User.is_active == True,
            User.email_verified == True
        )

    return query.all()


def get_admins(db: Session, active_only: bool = True) -> List[User]:
    """Get all admins"""
    query = db.query(User).filter(User.role == UserRole.ADMIN.value)

    if active_only:
        query = query.filter(
            User.is_active == True,
            User.email_verified == True
        )

    return query.all()


def get_brief_users(
    db: Session,
    user_ids: List[str]
) -> List[UserBriefResponse]:
    """Get brief user information for multiple users"""
    users = db.query(User).filter(User.id.in_(user_ids)).all()
    return [
        UserBriefResponse(
            id=user.id,
            email=user.email,
            first_name=user.first_name,
            last_name=user.last_name,
            role=UserRole(user.role) if user.role in UserRole._value2member_map_ else None
        )
        for user in users
    ]


def bulk_update_users(
    db: Session,
    user_ids: List[str],
    update_data: Dict[str, Any]
) -> int:
    """
    Bulk update multiple users

    Returns:
        Number of users updated
    """
    # Ensure we don't update passwords in bulk (work on a copy to avoid mutating caller's dict)
    update_data = {k: v for k, v in update_data.items() if k != 'password'}

    count = db.query(User).filter(
        User.id.in_(user_ids)
    ).update(
        {**update_data, "updated_at": datetime.now(timezone.utc)},
        synchronize_session=False
    )
    db.commit()
    return count


def get_user_stats(db: Session) -> Dict[str, Any]:
    """Get comprehensive user statistics"""
    total_users = db.query(User).count()
    active_users = db.query(User).filter(User.is_active == True).count()
    verified_users = db.query(User).filter(User.email_verified == True).count()

    # Count by role
    managers = db.query(User).filter(User.role == UserRole.MANAGER.value).count()
    admins = db.query(User).filter(User.role == UserRole.ADMIN.value).count()

    # Active and verified by role
    active_managers = db.query(User).filter(
        User.role == UserRole.MANAGER.value,
        User.is_active == True,
        User.email_verified == True
    ).count()

    active_admins = db.query(User).filter(
        User.role == UserRole.ADMIN.value,
        User.is_active == True,
        User.email_verified == True
    ).count()

    return {
        "total_users": total_users,
        "active_users": active_users,
        "verified_users": verified_users,
        "inactive_users": total_users - active_users,
        "unverified_users": total_users - verified_users,
        "users_by_role": {
            "managers": managers,
            "admins": admins
        },
        "active_by_role": {
            "managers": active_managers,
            "admins": active_admins
        }
    }


def user_exists(
    db: Session,
    email: Optional[str] = None,
    user_id: Optional[str] = None
) -> bool:
    """Check if user exists by email or ID"""
    query = db.query(User)

    if email:
        query = query.filter(func.lower(User.email) == func.lower(email))
    elif user_id:
        query = query.filter(User.id == user_id)
    else:
        return False

    return query.first() is not None


def search_users(
    db: Session,
    query: str,
    limit: int = 10
) -> List[UserBriefResponse]:
    """Search users by name or email"""
    search_filter = f"%{query}%"

    users = db.query(User).filter(
        or_(
            User.email.ilike(search_filter),
            User.first_name.ilike(search_filter),
            User.last_name.ilike(search_filter),
            func.concat(User.first_name, ' ', User.last_name).ilike(search_filter)
        ),
        User.is_active == True,
        User.email_verified == True
    ).limit(limit).all()

    return [
        UserBriefResponse(
            id=user.id,
            email=user.email,
            first_name=user.first_name,
            last_name=user.last_name,
            role=UserRole(user.role) if user.role in UserRole._value2member_map_ else None
        )
        for user in users
    ]
