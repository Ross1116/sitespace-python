from pydantic import BaseModel
from typing import Optional, Any, List
from datetime import datetime

class BaseResponse(BaseModel):
    success: bool = True
    message: Optional[str] = None
    data: Optional[Any] = None

class ErrorResponse(BaseModel):
    success: bool = False
    message: str
    error_code: Optional[str] = None

class PaginatedResponse(BaseModel):
    success: bool = True
    data: List[Any]
    total: int
    page: int
    size: int
    pages: int

class MessageResponse(BaseModel):
    message: str
