from sqlalchemy.orm import Session
from typing import Optional, List
from ..models.subcontractor import Subcontractor
from ..schemas.subcontractor import SubcontractorCreate, SubcontractorUpdate
from ..utils.password import get_password_hash
import uuid
import json

def get_subcontractor(db: Session, subcontractor_id: int) -> Optional[Subcontractor]:
    return db.query(Subcontractor).filter(Subcontractor.id == subcontractor_id).first()

def get_subcontractor_by_email(db: Session, email_id: str) -> Optional[Subcontractor]:
    return db.query(Subcontractor).filter(Subcontractor.email_id == email_id).first()

def get_all_subcontractors(db: Session, skip: int = 0, limit: int = 100) -> List[Subcontractor]:
    return db.query(Subcontractor).offset(skip).limit(limit).all()

def create_subcontractor(db: Session, subcontractor: SubcontractorCreate) -> Subcontractor:
    # Hash password if provided
    hashed_password = None
    if subcontractor.contractor_pass:
        hashed_password = get_password_hash(subcontractor.contractor_pass)
    
    # Convert contractor_project list to JSON string for SQLite
    contractor_project_json = json.dumps(subcontractor.contractor_project) if subcontractor.contractor_project else "[]"
    
    db_subcontractor = Subcontractor(
        name=subcontractor.name,
        email_id=subcontractor.email_id,
        contractor_project=contractor_project_json,
        contractor_project_id=subcontractor.contractor_project_id,
        contractor_name=subcontractor.contractor_name,
        contractor_company=subcontractor.contractor_company,
        contractor_trade=subcontractor.contractor_trade,
        contractor_email=subcontractor.contractor_email,
        contractor_phone=subcontractor.contractor_phone,
        contractor_pass=hashed_password,
        created_by=subcontractor.created_by
    )
    db.add(db_subcontractor)
    db.commit()
    db.refresh(db_subcontractor)
    return db_subcontractor

def update_subcontractor(db: Session, subcontractor_id: int, subcontractor_update: SubcontractorUpdate) -> Optional[Subcontractor]:
    db_subcontractor = get_subcontractor(db, subcontractor_id)
    if not db_subcontractor:
        return None
    
    update_data = subcontractor_update.dict(exclude_unset=True)
    
    # Hash password if it's being updated
    if 'contractor_pass' in update_data and update_data['contractor_pass']:
        update_data['contractor_pass'] = get_password_hash(update_data['contractor_pass'])
    
    # Handle contractor_project conversion to JSON
    if 'contractor_project' in update_data:
        update_data['contractor_project'] = json.dumps(update_data['contractor_project'])
    
    for field, value in update_data.items():
        setattr(db_subcontractor, field, value)
    
    db.commit()
    db.refresh(db_subcontractor)
    return db_subcontractor

def delete_subcontractor(db: Session, subcontractor_id: int) -> bool:
    db_subcontractor = get_subcontractor(db, subcontractor_id)
    if not db_subcontractor:
        return False
    
    db.delete(db_subcontractor)
    db.commit()
    return True
