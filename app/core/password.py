# app/core/password.py
import logging
from passlib.context import CryptContext

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
        return pwd_context.verify(_truncate_for_bcrypt(plain_password), hashed_password)
    except Exception as e:
        logger.error("Password verification error: %s", e)
        return False

def get_password_hash(password: str) -> str:
    """Hash a password"""
    return pwd_context.hash(_truncate_for_bcrypt(password))