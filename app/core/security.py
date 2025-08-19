from datetime import datetime, timedelta
from typing import Optional, Union
from jose import JWTError, jwt
from passlib.context import CryptContext
from fastapi import HTTPException, status, Depends
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from sqlalchemy.orm import Session
from .config import settings
from .database import get_db
from ..models.user import User
from ..crud.user import get_user_by_username
from ..utils.password import verify_password, get_password_hash

# JWT token bearer
security = HTTPBearer()

def create_access_token(data: dict, expires_delta: Optional[timedelta] = None):
    to_encode = data.copy()
    if expires_delta:
        expire = datetime.utcnow() + expires_delta
    else:
        expire = datetime.utcnow() + timedelta(milliseconds=settings.jwt_expiration_ms)
    to_encode.update({"exp": expire})
    encoded_jwt = jwt.encode(to_encode, settings.jwt_secret, algorithm=settings.jwt_algorithm)
    return encoded_jwt

def verify_token(token: str) -> Optional[str]:
    try:
        payload = jwt.decode(token, settings.jwt_secret, algorithms=[settings.jwt_algorithm])
        username: str = payload.get("sub")
        if username is None:
            return None
        return username
    except JWTError:
        return None

def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(security),
    db: Session = Depends(get_db)
) -> User:
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    
    token = credentials.credentials
    username = verify_token(token)
    if username is None:
        raise credentials_exception
    
    user = get_user_by_username(db, username=username)
    if user is None:
        raise credentials_exception
    return user

def get_current_active_user(current_user: User = Depends(get_current_user)) -> User:
    if not current_user.is_active:
        raise HTTPException(status_code=400, detail="Inactive user")
    return current_user

# Encryption/Decryption utilities (converted from Java)
import base64
import hashlib
from Crypto.Cipher import AES
from Crypto.Util.Padding import pad, unpad

def decrypt_password(encrypted_text: str) -> str:
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
