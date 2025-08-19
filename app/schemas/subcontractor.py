from pydantic import BaseModel, field_validator
from typing import Optional, List
from datetime import datetime
import json

class SubcontractorBase(BaseModel):
    name: Optional[str] = None
    email_id: Optional[str] = None
    contractor_project: Optional[List[str]] = None
    contractor_project_id: Optional[str] = None
    contractor_name: Optional[str] = None
    contractor_company: Optional[str] = None
    contractor_trade: Optional[str] = None
    contractor_email: Optional[str] = None
    contractor_phone: Optional[str] = None
    contractor_pass: Optional[str] = None
    created_by: Optional[str] = None

class SubcontractorCreate(SubcontractorBase):
    pass

class SubcontractorUpdate(BaseModel):
    name: Optional[str] = None
    email_id: Optional[str] = None
    contractor_project: Optional[List[str]] = None
    contractor_project_id: Optional[str] = None
    contractor_name: Optional[str] = None
    contractor_company: Optional[str] = None
    contractor_trade: Optional[str] = None
    contractor_email: Optional[str] = None
    contractor_phone: Optional[str] = None
    contractor_pass: Optional[str] = None
    created_by: Optional[str] = None

class SubcontractorResponse(SubcontractorBase):
    id: int
    created_at: datetime
    updated_at: Optional[datetime] = None
    
    @field_validator('contractor_project', mode='before')
    @classmethod
    def parse_contractor_project(cls, v):
        if isinstance(v, str):
            try:
                return json.loads(v)
            except (json.JSONDecodeError, TypeError):
                return []
        return v or []
    
    class Config:
        from_attributes = True

class SubcontractorListResponse(BaseModel):
    success: bool
    message: str
    data: List[SubcontractorResponse]
