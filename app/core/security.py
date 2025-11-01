# app/core/security.py

from datetime import datetime, timedelta
from typing import Optional, Union, Dict, Any
from uuid import UUID
from jose import JWTError, jwt
from passlib.context import CryptContext
from fastapi import HTTPException, status, Depends
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from sqlalchemy.orm import Session
import secrets
import hashlib
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

def create_access_token(data: dict, expires_delta: Optional[timedelta] = None):
    """Create JWT access token"""
    to_encode = data.copy()
    if expires_delta:
        expire = datetime.utcnow() + expires_delta
    else:
        expire = datetime.utcnow() + timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    
    to_encode.update({
        "exp": expire,
        "type": TOKEN_TYPE_ACCESS,
        "iat": datetime.utcnow()
    })
    encoded_jwt = jwt.encode(to_encode, settings.jwt_secret, algorithm=settings.jwt_algorithm)
    return encoded_jwt

def create_refresh_token(data: dict, expires_delta: Optional[timedelta] = None):
    """Create JWT refresh token"""
    to_encode = data.copy()
    if expires_delta:
        expire = datetime.utcnow() + expires_delta
    else:
        expire = datetime.utcnow() + timedelta(days=settings.REFRESH_TOKEN_EXPIRE_DAYS)
    
    to_encode.update({
        "exp": expire,
        "type": TOKEN_TYPE_REFRESH,
        "iat": datetime.utcnow(),
        "jti": secrets.token_urlsafe(32)  # JWT ID for tracking
    })
    encoded_jwt = jwt.encode(to_encode, settings.jwt_secret, algorithm=settings.jwt_algorithm)
    return encoded_jwt

def create_verification_token(email: str) -> str:
    """Create email verification token"""
    expire = datetime.utcnow() + timedelta(hours=settings.EMAIL_VERIFICATION_EXPIRE_HOURS)
    to_encode = {
        "email": email,
        "exp": expire,
        "type": TOKEN_TYPE_EMAIL_VERIFY
    }
    encoded_jwt = jwt.encode(to_encode, settings.jwt_secret, algorithm=settings.jwt_algorithm)
    return encoded_jwt

def create_password_reset_token(email: str) -> str:
    """Create password reset token"""
    expire = datetime.utcnow() + timedelta(hours=settings.PASSWORD_RESET_EXPIRE_HOURS)
    to_encode = {
        "email": email,
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
            
        return payload
    except JWTError as e:
        return None

def verify_refresh_token(token: str) -> Optional[Dict[str, Any]]:
    """Verify refresh token"""
    return verify_token(token, TOKEN_TYPE_REFRESH)

def verify_email_token(token: str) -> Optional[str]:
    """Verify email verification token and return email"""
    payload = verify_token(token, TOKEN_TYPE_EMAIL_VERIFY)
    if payload:
        return payload.get("email")
    return None

def verify_password_reset_token(token: str) -> Optional[str]:
    """Verify password reset token and return email"""
    payload = verify_token(token, TOKEN_TYPE_PASSWORD_RESET)
    if payload:
        return payload.get("email")
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
    """Dependency to check if user has required role"""
    def role_checker(current_entity: Union[User, Subcontractor] = Depends(get_current_active_user)):
        # Handle subcontractor
        if isinstance(current_entity, Subcontractor):
            if "subcontractor" not in allowed_roles:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="You don't have permission to access this resource"
                )
        # Handle user
        elif isinstance(current_entity, User):
            if current_entity.role not in allowed_roles:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="You don't have permission to access this resource"
                )
        return current_entity
    return role_checker

# Optional: Token blacklist functionality
class TokenBlacklist:
    """Simple in-memory token blacklist (use Redis in production)"""
    def __init__(self):
        self._blacklist = set()
    
    def add(self, jti: str, expires_at: datetime):
        """Add token to blacklist"""
        self._blacklist.add(jti)
        # In production, use Redis with expiration
    
    def is_blacklisted(self, jti: str) -> bool:
        """Check if token is blacklisted"""
        return jti in self._blacklist
    
    def clear_expired(self):
        """Clear expired tokens from blacklist"""
        # In production, Redis handles this automatically
        pass

# Initialize token blacklist
token_blacklist = TokenBlacklist()

# Keep your existing decryption utilities
import base64
import hashlib
from Crypto.Cipher import AES
from Crypto.Util.Padding import pad, unpad

def decrypt_password(encrypted_text: str) -> str:
    """Decrypt password (legacy support)"""
    try:
        # Decode base64
        cipher_data = base64.b64decode(encrypted_text)
        
        # Extract salt (bytes 8-16)
        salt_data = cipher_data[8:16]
        
        # Generate key and IV using MD5
        key_and_iv = generate_key_and_iv(32, 16, 1, salt_data, settings.secret_key.encode('utf-8'))
        key = key_and_iv[0]
        iv = key_and_iv[1]
        
        # Extract encrypted data (bytes 16 onwards)
        encrypted = cipher_data[16:]
        
        # Decrypt
        cipher = AES.new(key, AES.MODE_CBC, iv)
        decrypted_data = cipher.decrypt(encrypted)
        decrypted_text = unpad(decrypted_data, AES.block_size).decode('utf-8')
        
        return decrypted_text.replace('"', '')
    except Exception as e:
        print(f"Decryption error: {e}")
        return encrypted_text

def generate_key_and_iv(key_length: int, iv_length: int, iterations: int, salt: bytes, password: bytes):
    """Generate key and IV for decryption (legacy support)"""
    md5 = hashlib.md5()
    digest_length = md5.digest_size
    required_length = (key_length + iv_length + digest_length - 1) // digest_length * digest_length
    generated_data = bytearray(required_length)
    generated_length = 0
    
    while generated_length < key_length + iv_length:
        if generated_length > 0:
            md5.update(generated_data[generated_length - digest_length:generated_length])
        md5.update(password)
        if salt:
            md5.update(salt[:8])
        
        digest = md5.digest()
        generated_data[generated_length:generated_length + digest_length] = digest
        
        # Additional rounds
        for i in range(1, iterations):
            md5.update(generated_data[generated_length:generated_length + digest_length])
            digest = md5.digest()
            generated_data[generated_length:generated_length + digest_length] = digest
        
        generated_length += digest_length
    
    # Copy key and IV into separate byte arrays
    key = bytes(generated_data[:key_length])
    iv = bytes(generated_data[key_length:key_length + iv_length]) if iv_length > 0 else b''
    
    return [key, iv]

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
        return UserRole(entity.role)
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