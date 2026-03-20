# app/api/v1/endpoints/users.py
import logging
from fastapi import APIRouter, Depends, HTTPException, status, Query
from sqlalchemy.orm import Session
from typing import List
from uuid import UUID

from ...core.database import get_db
from ...core.security import get_current_active_user
from ...schemas.enums import UserRole
from ...crud import user as user_crud
from ...models.user import User
from ...schemas.user import (
    UserUpdate, 
    UserAdminUpdate, 
    UserResponse, 
    UserDetailResponse, 
    UserListResponse
)

router = APIRouter(prefix="/users", tags=["Users"])
logger = logging.getLogger(__name__)

# ==========================================
# SELF PROFILE ROUTES
# ==========================================

@router.get("/me", response_model=UserDetailResponse)
def read_user_me(
    current_user: User = Depends(get_current_active_user),
    db: Session = Depends(get_db)
):
    """
    Get detailed profile information for the current logged-in user.
    """
    return user_crud.get_user_detail(db, str(current_user.id))

@router.put("/me", response_model=UserResponse)
def update_user_me(
    user_update: UserUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user)
):
    """
    Update current user's profile information.
    Restricted: Cannot update Role or Is_Active status via this endpoint.
    """
    # Check email uniqueness if it is being changed
    if user_update.email and user_update.email != current_user.email:
        if user_crud.get_user_by_email(db, email=user_update.email):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Email already registered"
            )

    updated_user = user_crud.update_user(db, current_user, user_update)
    return updated_user

# ==========================================
# ADMIN ROUTES
# ==========================================

@router.get("/", response_model=UserListResponse)
def read_users(
    skip: int = 0,
    limit: int = 100,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user)
):
    """
    Retrieve all users. (Admin Only)
    """
    if current_user.role != UserRole.ADMIN:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not enough permissions"
        )
    return user_crud.get_users(db, skip=skip, limit=limit)

@router.put("/{user_id}", response_model=UserResponse)
def update_user_by_admin(
    user_id: UUID,
    user_update: UserAdminUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user)
):
    """
    Update any user's information, including Role and Active status.
    (Admin Only)
    """
    if current_user.role != UserRole.ADMIN:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not enough permissions"
        )

    # Get the user to be updated
    user = user_crud.get_user(db, str(user_id))
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found"
        )

    # Check email uniqueness if changing
    if user_update.email and user_update.email != user.email:
        if user_crud.get_user_by_email(db, email=user_update.email):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Email already registered"
            )

    # 1. Update basic fields (Name, Email, Phone)
    # We exclude role/active here to use the standard update function
    base_data = user_update.model_dump(exclude={'role', 'is_active'}, exclude_unset=True)
    if base_data:
        user = user_crud.update_user(db, user, UserUpdate(**base_data))

    # 2. Handle Role Change
    if user_update.role is not None and user_update.role != user.role:
        logger.info(
            "admin_role_change",
            extra={
                "actor_id": str(current_user.id),
                "target_user_id": str(user_id),
                "action": "role_change",
                "from_role": user.role.value if user.role else None,
                "to_role": user_update.role.value,
            },
        )
        user = user_crud.update_user_role(db, user, user_update.role)

    # 3. Handle Activation/Deactivation
    if user_update.is_active is not None and user_update.is_active != user.is_active:
        action = "activate_user" if user_update.is_active else "deactivate_user"
        logger.info(
            "admin_activation_change",
            extra={
                "actor_id": str(current_user.id),
                "target_user_id": str(user_id),
                "action": action,
            },
        )
        if user_update.is_active:
            user = user_crud.activate_user(db, user)
        else:
            user = user_crud.deactivate_user(db, user)

    return user