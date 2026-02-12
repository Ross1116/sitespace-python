# app/core/password.py
import logging
from passlib.context import CryptContext
from passlib.exc import UnknownHashError, PasswordValueError, InternalBackendError

logger = logging.getLogger(__name__)

# Update the password context to include argon2
pwd_context = CryptContext(
    schemes=["bcrypt", "argon2"], 
    deprecated="auto",
    
    bcrypt__rounds=12
)

def _truncate_for_bcrypt(password: str) -> str:
    """Truncate password to 72 bytes (bcrypt's hard limit)."""
    encoded = password.encode("utf-8")
    if len(encoded) > 72:
        encoded = encoded[:72]
    return encoded.decode("utf-8", errors="ignore")


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Verify a plain password against a hashed password"""
    if not plain_password or not hashed_password:
        return False

    try:
        scheme = pwd_context.identify(hashed_password)
        secret = _truncate_for_bcrypt(plain_password) if scheme == "bcrypt" else plain_password
        return pwd_context.verify(secret, hashed_password)
    except (UnknownHashError, PasswordValueError, InternalBackendError):
        logger.exception("Password verification error")
        return False

def get_password_hash(password: str) -> str:
    """Hash a password"""
    secret = _truncate_for_bcrypt(password) if pwd_context.default_scheme() == "bcrypt" else password
    return pwd_context.hash(secret)