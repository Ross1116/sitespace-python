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

def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Verify a plain password against a hashed password"""
    if not plain_password or not hashed_password:
        return False
    
    try:
        return pwd_context.verify(plain_password, hashed_password)
    except Exception as e:
        logger.error("Password verification error: %s", e)
        return False

def get_password_hash(password: str) -> str:
    """Hash a password"""
    return pwd_context.hash(password)