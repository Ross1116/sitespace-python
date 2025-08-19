from pydantic import BaseModel, EmailStr
from typing import Optional
from datetime import datetime

class UserBase(BaseModel):
    username: str
    email: EmailStr
    user_phone: Optional[str] = None
    profile_pic: Optional[str] = None
    credit_point: Optional[int] = 0
    role: Optional[str] = "user"
    dob: Optional[str] = None

class UserCreate(UserBase):
    password: str
    user_id: Optional[str] = None

class UserUpdate(BaseModel):
    username: Optional[str] = None
    email: Optional[EmailStr] = None
    user_phone: Optional[str] = None
    profile_pic: Optional[str] = None
    credit_point: Optional[int] = None
    role: Optional[str] = None
    dob: Optional[str] = None

class UserResponse(UserBase):
    id: int
    user_id: Optional[str] = None
    is_active: bool
    created_at: datetime
    updated_at: Optional[datetime] = None
    
    class Config:
        from_attributes = True

class UserLogin(BaseModel):
    username: str
    password: str

class UserSignup(BaseModel):
    username: str
    email: EmailStr
    password: str
    user_phone: Optional[str] = None
    role: Optional[str] = "user"

class TokenResponse(BaseModel):
    access_token: str
    user_id: str
    username: str
    email: str
    role: str

class MessageResponse(BaseModel):
    message: str
