# app/core/security.py
import logging
import threading

from datetime import datetime, timedelta, timezone
from typing import Optional, Union, Dict, Any
from uuid import UUID
from jose import JWTError, jwt
from passlib.context import CryptContext
from fastapi import HTTPException, status, Depends
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from sqlalchemy.orm import Session
import secrets
import hashlib
logger = logging.getLogger(__name__)
from .config import settings
from .database import get_db
from ..models.user import User
from ..models.subcontractor import Subcontractor  # Add this import
from ..crud.user import get_user, get_user_by_email
from .password import verify_password, get_password_hash
from ..schemas.enums import UserRole

# JWT token bearer
security = HTTPBearer()

# Token types
TOKEN_TYPE_ACCESS = "access"
TOKEN_TYPE_REFRESH = "refresh"
TOKEN_TYPE_EMAIL_VERIFY = "email_verify"
TOKEN_TYPE_PASSWORD_RESET = "password_reset"


def normalize_email(email: str) -> str:
    return email.strip().lower()

def create_access_token(data: dict, expires_delta: Optional[timedelta] = None):
    """Create JWT access token"""
    to_encode = data.copy()
    if expires_delta:
        expire = datetime.now(timezone.utc) + expires_delta
    else:
        expire = datetime.now(timezone.utc) + timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    
    to_encode.update({
        "exp": expire,
        "type": TOKEN_TYPE_ACCESS,
        "iat": datetime.now(timezone.utc),
        "jti": secrets.token_urlsafe(32)
    })
    encoded_jwt = jwt.encode(to_encode, settings.jwt_secret, algorithm=settings.jwt_algorithm)
    return encoded_jwt

def create_refresh_token(data: dict, expires_delta: Optional[timedelta] = None):
    """Create JWT refresh token"""
    to_encode = data.copy()
    if expires_delta:
        expire = datetime.now(timezone.utc) + expires_delta
    else:
        expire = datetime.now(timezone.utc) + timedelta(days=settings.REFRESH_TOKEN_EXPIRE_DAYS)
    
    to_encode.update({
        "exp": expire,
        "type": TOKEN_TYPE_REFRESH,
        "iat": datetime.now(timezone.utc),
        "jti": secrets.token_urlsafe(32)  # JWT ID for tracking
    })
    encoded_jwt = jwt.encode(to_encode, settings.jwt_secret, algorithm=settings.jwt_algorithm)
    return encoded_jwt

def create_verification_token(email: str) -> str:
    """Create email verification token"""
    expire = datetime.now(timezone.utc) + timedelta(hours=settings.EMAIL_VERIFICATION_EXPIRE_HOURS)
    to_encode = {
        "email": normalize_email(email),
        "exp": expire,
        "type": TOKEN_TYPE_EMAIL_VERIFY
    }
    encoded_jwt = jwt.encode(to_encode, settings.jwt_secret, algorithm=settings.jwt_algorithm)
    return encoded_jwt

def create_password_reset_token(email: str) -> str:
    """Create password reset token"""
    expire = datetime.now(timezone.utc) + timedelta(hours=settings.PASSWORD_RESET_EXPIRE_HOURS)
    to_encode = {
        "email": normalize_email(email),
        "exp": expire,
        "type": TOKEN_TYPE_PASSWORD_RESET,
        "jti": secrets.token_urlsafe(32)
    }
    encoded_jwt = jwt.encode(to_encode, settings.jwt_secret, algorithm=settings.jwt_algorithm)
    return encoded_jwt

def verify_token(token: str, expected_type: str = TOKEN_TYPE_ACCESS) -> Optional[Dict[str, Any]]:
    """Verify JWT token and return payload"""
    try:
        payload = jwt.decode(token, settings.jwt_secret, algorithms=[settings.jwt_algorithm])
        
        # Check token type
        token_type = payload.get("type")
        if token_type != expected_type:
            return None

        # Enforce token revocation (if token has jti)
        jti = payload.get("jti")
        if jti and token_blacklist.is_blacklisted(jti):
            return None
            
        return payload
    except JWTError:
        return None


def _normalize_exp_to_datetime(exp_value: Any) -> Optional[datetime]:
    """Normalize JWT exp claim into a timezone-aware datetime."""
    if exp_value is None:
        return None

    if isinstance(exp_value, datetime):
        return exp_value if exp_value.tzinfo else exp_value.replace(tzinfo=timezone.utc)

    if isinstance(exp_value, (int, float)):
        return datetime.fromtimestamp(exp_value, tz=timezone.utc)

    if isinstance(exp_value, str):
        try:
            return datetime.fromtimestamp(int(exp_value), tz=timezone.utc)
        except (ValueError, TypeError):
            return None

    return None


def revoke_token_payload(payload: Dict[str, Any]) -> bool:
    """Revoke a JWT payload by adding its jti to the blacklist until expiry."""
    jti = payload.get("jti")
    if not jti:
        return False

    expires_at = _normalize_exp_to_datetime(payload.get("exp"))
    if expires_at is None:
        return False

    token_blacklist.add(jti, expires_at)
    return True

def verify_refresh_token(token: str) -> Optional[Dict[str, Any]]:
    """Verify refresh token"""
    return verify_token(token, TOKEN_TYPE_REFRESH)

def verify_email_token(token: str) -> Optional[str]:
    """Verify email verification token and return email"""
    payload = verify_token(token, TOKEN_TYPE_EMAIL_VERIFY)
    if payload:
        email = payload.get("email")
        return normalize_email(email) if email else None
    return None

def verify_password_reset_token(token: str) -> Optional[str]:
    """Verify password reset token and return email"""
    payload = verify_token(token, TOKEN_TYPE_PASSWORD_RESET)
    if payload:
        email = payload.get("email")
        return normalize_email(email) if email else None
    return None

def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(security),
    db: Session = Depends(get_db)
) -> Union[User, Subcontractor]:
    """Get current user or subcontractor from JWT token"""
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    
    token = credentials.credentials
    payload = verify_token(token, TOKEN_TYPE_ACCESS)
    
    if payload is None:
        raise credentials_exception
    
    # Get user type from token (defaults to "user" for backward compatibility)
    user_type = payload.get("user_type", "user")
    user_id = payload.get("sub")
    email = payload.get("email")
    
    entity = None
    
    if user_type == "subcontractor":
        # Get subcontractor
        from ..crud.subcontractor import get_subcontractor, get_subcontractor_by_email
        
        if user_id:
            entity = get_subcontractor(db, subcontractor_id=user_id)
        elif email:
            entity = get_subcontractor_by_email(db, email=email)
    else:
        # Get user
        if user_id:
            from ..crud.user import get_user
            entity = get_user(db, user_id=user_id)
        elif email:
            entity = get_user_by_email(db, email=email)
    
    if entity is None:
        raise credentials_exception
        
    return entity

def get_current_active_user(
    current_entity: Union[User, Subcontractor] = Depends(get_current_user)
) -> Union[User, Subcontractor]:
    """Get current active user or subcontractor"""
    if not current_entity.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Inactive account"
        )
    return current_entity

def get_current_verified_user(
    current_entity: Union[User, Subcontractor] = Depends(get_current_active_user)
) -> User:
    """Get current user with verified email (only for User model)"""
    # This only applies to User model, not Subcontractor
    if not isinstance(current_entity, User):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="This endpoint is only for verified users"
        )
    
    if not current_entity.email_verified:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Please verify your email first"
        )
    return current_entity

def get_current_user_or_subcontractor(
    current_entity: Union[User, Subcontractor] = Depends(get_current_user)
) -> Dict[str, Any]:
    """Get current entity info as a dictionary"""
    if isinstance(current_entity, User):
        return {
            "id": str(current_entity.id),
            "email": current_entity.email,
            "first_name": current_entity.first_name,
            "last_name": current_entity.last_name,
            "role": current_entity.role,
            "entity_type": "user",
            "is_active": current_entity.is_active
        }
    else:
        return {
            "id": str(current_entity.id),
            "email": current_entity.email,
            "first_name": current_entity.first_name,
            "last_name": current_entity.last_name,
            "role": "subcontractor",
            "entity_type": "subcontractor",
            "is_active": current_entity.is_active,
            "company_name": current_entity.company_name,
            "trade_specialty": current_entity.trade_specialty
        }

def require_role(allowed_roles: list):
    """Dependency to check if user has required role (case-insensitive, robust to enums/strings)."""
    # Normalize allowed_roles to a set of lowercase strings
    def _normalize_role(r):
        if hasattr(r, "value"):
            return str(r.value).strip().lower()
        if hasattr(r, "name"):
            return str(r.name).strip().lower()
        if isinstance(r, str):
            return r.strip().lower()
        return str(r).strip().lower()

    allowed_set = set(_normalize_role(r) for r in allowed_roles)

    def role_checker(current_entity: Union[User, Subcontractor] = Depends(get_current_active_user)):
        # Handle subcontractor
        if isinstance(current_entity, Subcontractor):
            if "subcontractor" not in allowed_set:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="You don't have permission to access this resource"
                )
        # Handle user
        elif isinstance(current_entity, User):
            role_norm = _normalize_role(current_entity.role)
            if role_norm not in allowed_set:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="You don't have permission to access this resource"
                )
        return current_entity
    return role_checker

# Token blacklist with expiry cleanup (swap to Redis for multi-instance deployments)
class TokenBlacklist:
    def __init__(self):
        self._blacklist: dict[str, datetime] = {}  # jti -> expires_at
        self._lock = threading.Lock()

    def add(self, jti: str, expires_at: datetime):
        """Add token to blacklist with its expiry time"""
        with self._lock:
            self._blacklist[jti] = expires_at
        self._cleanup_if_needed()

    def is_blacklisted(self, jti: str) -> bool:
        """Check if token is blacklisted (ignores expired entries)"""
        with self._lock:
            expires_at = self._blacklist.get(jti)
            if expires_at is None:
                return False
            if datetime.now(timezone.utc) > expires_at:
                del self._blacklist[jti]
                return False
            return True

    def clear_expired(self):
        """Remove all expired tokens from blacklist"""
        with self._lock:
            self._clear_expired_locked()

    def _cleanup_if_needed(self):
        """Auto-cleanup when blacklist grows large"""
        with self._lock:
            if len(self._blacklist) > 1000:
                self._clear_expired_locked()

    def _clear_expired_locked(self):
        now = datetime.now(timezone.utc)
        self._blacklist = {
            jti: exp for jti, exp in self._blacklist.items() if exp > now
        }

# Initialize token blacklist
token_blacklist = TokenBlacklist()

def get_user_role(entity: Union[User, Subcontractor]) -> UserRole:
    """
    Get UserRole from User or Subcontractor entity.
    
    Args:
        entity: User or Subcontractor instance
        
    Returns:
        UserRole enum value
    """
    if isinstance(entity, Subcontractor):
        return UserRole.SUBCONTRACTOR
    elif isinstance(entity, User):
        raw_role = entity.role.strip().lower() if isinstance(entity.role, str) else entity.role
        return UserRole(raw_role)
    else:
        raise ValueError(f"Invalid entity type: {type(entity)}")

def get_entity_id(entity: Union[User, Subcontractor]) -> UUID:
    """
    Get ID from User or Subcontractor entity.
    
    Args:
        entity: User or Subcontractor instance
        
    Returns:
        UUID of the entity
    """
    return entity.id

def is_subcontractor(entity: Union[User, Subcontractor]) -> bool:
    """Check if entity is a Subcontractor"""
    return isinstance(entity, Subcontractor)

def is_user(entity: Union[User, Subcontractor]) -> bool:
    """Check if entity is a User"""
    return isinstance(entity, User)