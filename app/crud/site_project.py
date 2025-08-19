from sqlalchemy.orm import Session
from typing import Optional, List
from ..models.site_project import SiteProject
from ..schemas.site_project import SiteProjectCreate, SiteProjectUpdate
import uuid
import json

def get_site_project(db: Session, project_id: int) -> Optional[SiteProject]:
    return db.query(SiteProject).filter(SiteProject.id == project_id).first()

def get_site_projects_by_contractor_key(db: Session, contractor_key: str) -> List[SiteProject]:
    return db.query(SiteProject).filter(SiteProject.contractor_key == contractor_key).all()

def get_all_site_projects(db: Session, skip: int = 0, limit: int = 100) -> List[SiteProject]:
    return db.query(SiteProject).offset(skip).limit(limit).all()

def create_site_project(db: Session, site_project: SiteProjectCreate) -> SiteProject:
    if not site_project.contractor_key:
        site_project.contractor_key = str(uuid.uuid4())
    
    # Convert contractor_project list to JSON string for SQLite
    contractor_project_json = json.dumps(site_project.contractor_project) if site_project.contractor_project else "[]"
    
    db_site_project = SiteProject(
        contractor_key=site_project.contractor_key,
        email_id=site_project.email_id,
        contractor_project=contractor_project_json,
        contractor_project_id=site_project.contractor_project_id,
        contractor_name=site_project.contractor_name,
        contractor_company=site_project.contractor_company,
        contractor_trade=site_project.contractor_trade,
        contractor_email=site_project.contractor_email,
        contractor_phone=site_project.contractor_phone,
        created_by=site_project.created_by
    )
    db.add(db_site_project)
    db.commit()
    db.refresh(db_site_project)
    return db_site_project

def update_site_project(db: Session, project_id: int, site_project_update: SiteProjectUpdate) -> Optional[SiteProject]:
    db_site_project = get_site_project(db, project_id)
    if not db_site_project:
        return None
    
    update_data = site_project_update.dict(exclude_unset=True)
    
    # Handle contractor_project conversion to JSON
    if 'contractor_project' in update_data:
        update_data['contractor_project'] = json.dumps(update_data['contractor_project'])
    
    for field, value in update_data.items():
        setattr(db_site_project, field, value)
    
    db.commit()
    db.refresh(db_site_project)
    return db_site_project

def delete_site_project(db: Session, project_id: int) -> bool:
    db_site_project = get_site_project(db, project_id)
    if not db_site_project:
        return False
    
    db.delete(db_site_project)
    db.commit()
    return True
