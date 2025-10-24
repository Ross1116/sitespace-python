# crud/site_project.py
from sqlalchemy.orm import Session, joinedload
from sqlalchemy import and_, or_, func
from typing import Optional, List, Dict, Any
from uuid import UUID
from datetime import date

from ..models.site_project import SiteProject
from ..models.user import User
from ..models.subcontractor import Subcontractor
from ..schemas.site_project import SiteProjectCreate, SiteProjectUpdate

def create_project(
    db: Session,
    project_data: SiteProjectCreate,
    manager_ids: Optional[List[UUID]] = None,
    subcontractor_ids: Optional[List[UUID]] = None
) -> SiteProject:
    """Create a new project"""
    
    # Extract dict data excluding relationship fields
    project_dict = project_data.dict(exclude={'manager_ids', 'subcontractor_ids'})
    
    # Create the project with basic fields
    project = SiteProject(**project_dict)
    
    db.add(project)
    db.flush()  # Flush to get the ID before adding relationships
    
    # Add managers if provided
    if manager_ids:
        for manager_id in manager_ids:
            manager = db.query(User).filter(User.id == manager_id).first()
            if manager:
                project.managers.append(manager)
    
    # Add subcontractors if provided
    if subcontractor_ids:
        for subcontractor_id in subcontractor_ids:
            subcontractor = db.query(Subcontractor).filter(
                Subcontractor.id == subcontractor_id
            ).first()
            if subcontractor:
                project.subcontractors.append(subcontractor)
    
    db.commit()
    db.refresh(project)
    
    return project

def get_project(db: Session, project_id: UUID) -> Optional[SiteProject]:
    """Get project by ID"""
    return db.query(SiteProject).filter(SiteProject.id == project_id).first()

def get_project_with_details(db: Session, project_id: UUID) -> Optional[SiteProject]:
    """Get project with all relationships loaded"""
    return db.query(SiteProject)\
        .options(
            joinedload(SiteProject.managers),
            joinedload(SiteProject.subcontractors),
            joinedload(SiteProject.assets),
            joinedload(SiteProject.slot_bookings)
        )\
        .filter(SiteProject.id == project_id)\
        .first()

def get_projects(
    db: Session,
    filters: Dict[str, Any] = {},
    skip: int = 0,
    limit: int = 100
) -> List[SiteProject]:
    """Get projects with filters"""
    query = db.query(SiteProject)
    
    # Text search filters
    if 'name' in filters and filters['name']:
        query = query.filter(
            SiteProject.name.ilike(f"%{filters['name']}%")
        )
    
    if 'location' in filters and filters['location']:
        query = query.filter(
            SiteProject.location.ilike(f"%{filters['location']}%")
        )
    
    if 'status' in filters and filters['status']:
        query = query.filter(SiteProject.status == filters['status'])
    
    # Date range filters
    if 'start_date_from' in filters and filters['start_date_from']:
        query = query.filter(SiteProject.start_date >= filters['start_date_from'])
    
    if 'start_date_to' in filters and filters['start_date_to']:
        query = query.filter(SiteProject.start_date <= filters['start_date_to'])
    
    if 'end_date_from' in filters and filters['end_date_from']:
        query = query.filter(SiteProject.end_date >= filters['end_date_from'])
    
    if 'end_date_to' in filters and filters['end_date_to']:
        query = query.filter(SiteProject.end_date <= filters['end_date_to'])
    
    # User access filter - get projects where user is manager or subcontractor
    if 'user_id' in filters and filters['user_id']:
        query = query.filter(
            or_(
                SiteProject.managers.any(User.id == filters['user_id']),
                SiteProject.subcontractors.any(
                    Subcontractor.user_id == filters['user_id']
                )
            )
        )
    
    return query.order_by(SiteProject.created_at.desc()).offset(skip).limit(limit).all()

def count_projects(db: Session, filters: Dict[str, Any] = {}) -> int:
    """Count projects with filters"""
    query = db.query(SiteProject)
    
    # Apply same filters as get_projects
    if 'name' in filters and filters['name']:
        query = query.filter(
            SiteProject.name.ilike(f"%{filters['name']}%")
        )
    
    if 'location' in filters and filters['location']:
        query = query.filter(
            SiteProject.location.ilike(f"%{filters['location']}%")
        )
    
    if 'status' in filters and filters['status']:
        query = query.filter(SiteProject.status == filters['status'])
    
    if 'start_date_from' in filters and filters['start_date_from']:
        query = query.filter(SiteProject.start_date >= filters['start_date_from'])
    
    if 'start_date_to' in filters and filters['start_date_to']:
        query = query.filter(SiteProject.start_date <= filters['start_date_to'])
    
    if 'end_date_from' in filters and filters['end_date_from']:
        query = query.filter(SiteProject.end_date >= filters['end_date_from'])
    
    if 'end_date_to' in filters and filters['end_date_to']:
        query = query.filter(SiteProject.end_date <= filters['end_date_to'])
    
    if 'user_id' in filters and filters['user_id']:
        query = query.filter(
            or_(
                SiteProject.managers.any(User.id == filters['user_id']),
                SiteProject.subcontractors.any(
                    Subcontractor.user_id == filters['user_id']
                )
            )
        )
    
    return query.count()

def update_project(
    db: Session,
    project: SiteProject,
    update_data: SiteProjectUpdate
) -> SiteProject:
    """Update project"""
    
    update_dict = update_data.dict(exclude_unset=True)
    
    # Handle M2M relationships separately
    manager_ids = update_dict.pop('manager_ids', None)
    subcontractor_ids = update_dict.pop('subcontractor_ids', None)
    
    # Update basic fields
    for field, value in update_dict.items():
        setattr(project, field, value)
    
    # Update managers if provided
    if manager_ids is not None:
        # Clear existing and add new
        project.managers.clear()
        for manager_id in manager_ids:
            manager = db.query(User).filter(User.id == manager_id).first()
            if manager:
                project.managers.append(manager)
    
    # Update subcontractors if provided
    if subcontractor_ids is not None:
        # Clear existing and add new
        project.subcontractors.clear()
        for subcontractor_id in subcontractor_ids:
            subcontractor = db.query(Subcontractor).filter(
                Subcontractor.id == subcontractor_id
            ).first()
            if subcontractor:
                project.subcontractors.append(subcontractor)
    
    db.commit()
    db.refresh(project)
    return project

def delete_project(db: Session, project: SiteProject):
    """Delete project and all related data (cascade)"""
    db.delete(project)
    db.commit()

def has_project_access(db: Session, project_id: UUID, user_id: UUID) -> bool:
    """Check if user has access to project (as manager or subcontractor)"""
    project = db.query(SiteProject)\
        .filter(SiteProject.id == project_id)\
        .filter(
            or_(
                SiteProject.managers.any(User.id == user_id)
                # SiteProject.subcontractors.any(
                #     Subcontractor.user_id == user_id
                # )
            )
        ).first()
    
    return project is not None

def is_project_manager(db: Session, project_id: UUID, user_id: UUID) -> bool:
    """Check if user is a project manager"""
    project = db.query(SiteProject)\
        .filter(SiteProject.id == project_id)\
        .filter(SiteProject.managers.any(User.id == user_id))\
        .first()
    
    return project is not None

def is_lead_project_manager(db: Session, project_id: UUID, user_id: UUID) -> bool:
    """Check if user is the lead project manager
    
    For now, we consider the first manager as the lead.
    You can enhance this by adding an association object with is_lead field.
    """
    project = db.query(SiteProject)\
        .options(joinedload(SiteProject.managers))\
        .filter(SiteProject.id == project_id)\
        .first()
    
    if project and project.managers:
        # Consider the first manager as lead
        return project.managers[0].id == user_id
    
    return False

def count_project_managers(db: Session, project_id: UUID) -> int:
    """Count project managers"""
    project = db.query(SiteProject)\
        .options(joinedload(SiteProject.managers))\
        .filter(SiteProject.id == project_id)\
        .first()
    
    if project:
        return len(project.managers)
    return 0

def add_manager_to_project(
    db: Session, 
    project_id: UUID, 
    manager_id: UUID, 
    is_lead: bool = False
):
    """Add manager to project"""
    project = get_project(db, project_id)
    manager = db.query(User).filter(User.id == manager_id).first()
    
    if project and manager:
        if manager not in project.managers:
            if is_lead:
                # If setting as lead, add at beginning
                project.managers.insert(0, manager)
            else:
                project.managers.append(manager)
            db.commit()
            return True
    return False

def remove_manager_from_project(db: Session, project_id: UUID, manager_id: UUID):
    """Remove manager from project"""
    project = get_project(db, project_id)
    if project:
        project.managers = [m for m in project.managers if m.id != manager_id]
        db.commit()
        return True
    return False

def add_subcontractor_to_project(
    db: Session, 
    project_id: UUID, 
    subcontractor_id: UUID,
    hourly_rate: Optional[float] = None,
    is_active: bool = True  # Add this parameter
):
    """Add subcontractor to project
    
    Note: hourly_rate and is_active are not stored in the current model.
    If needed, create an association object to store project-specific rates and status.
    """
    # Only add if is_active is True (for now, since we can't store the status)
    if not is_active:
        return False
        
    project = get_project(db, project_id)
    subcontractor = db.query(Subcontractor).filter(
        Subcontractor.id == subcontractor_id
    ).first()
    
    if project and subcontractor:
        if subcontractor not in project.subcontractors:
            project.subcontractors.append(subcontractor)
            db.commit()
            return True
    return False

def remove_subcontractor_from_project(
    db: Session, 
    project_id: UUID, 
    subcontractor_id: UUID
):
    """Remove subcontractor from project"""
    project = get_project(db, project_id)
    if project:
        project.subcontractors = [
            s for s in project.subcontractors if s.id != subcontractor_id
        ]
        db.commit()
        return True
    return False

def update_project_subcontractor(
    db: Session,
    project_id: UUID,
    subcontractor_id: UUID,
    update_data: Any
):
    """Update subcontractor details in project
    
    Current model doesn't support project-specific subcontractor data.
    To implement this, you'd need an association object pattern.
    """
    # Placeholder for future implementation
    # Would require changes to the association table
    return True

def get_available_subcontractors(
    db: Session,
    project_id: UUID,
    trade_specialty: Optional[str] = None
) -> List[Subcontractor]:
    """Get list of subcontractors not yet assigned to this project"""
    
    project = get_project(db, project_id)
    if not project:
        return []
    
    # Get IDs of already assigned subcontractors
    assigned_ids = [s.id for s in project.subcontractors]
    
    # Query for unassigned subcontractors
    query = db.query(Subcontractor)
    
    # Exclude already assigned
    if assigned_ids:
        query = query.filter(~Subcontractor.id.in_(assigned_ids))
    
    # Filter by trade specialty if provided
    if trade_specialty:
        query = query.filter(
            Subcontractor.trade_specialty.ilike(f"%{trade_specialty}%")
        )
    
    # Only active subcontractors
    query = query.filter(Subcontractor.is_active == True)
    
    return query.all()

# Additional helper functions

def get_user_projects(
    db: Session,
    user_id: UUID,
    role: Optional[str] = None,
    skip: int = 0,
    limit: int = 100
) -> List[SiteProject]:
    """Get all projects for a specific user"""
    query = db.query(SiteProject)
    
    if role == 'manager':
        query = query.filter(SiteProject.managers.any(User.id == user_id))
    elif role == 'subcontractor':
        query = query.filter(
            SiteProject.subcontractors.any(Subcontractor.user_id == user_id)
        )
    else:
        # Get all projects where user is either manager or subcontractor
        query = query.filter(
            or_(
                SiteProject.managers.any(User.id == user_id),
                SiteProject.subcontractors.any(Subcontractor.user_id == user_id)
            )
        )
    
    return query.order_by(SiteProject.created_at.desc())\
        .offset(skip).limit(limit).all()

def search_projects(
    db: Session,
    search_term: str,
    user_id: Optional[UUID] = None,
    skip: int = 0,
    limit: int = 100
) -> List[SiteProject]:
    """Search projects by name, location, or description"""
    query = db.query(SiteProject)
    
    # Search in multiple fields
    search_filter = or_(
        SiteProject.name.ilike(f"%{search_term}%"),
        SiteProject.location.ilike(f"%{search_term}%") if SiteProject.location else False,
        SiteProject.description.ilike(f"%{search_term}%") if SiteProject.description else False
    )
    query = query.filter(search_filter)
    
    # If user_id provided, filter to user's projects only
    if user_id:
        query = query.filter(
            or_(
                SiteProject.managers.any(User.id == user_id),
                SiteProject.subcontractors.any(Subcontractor.user_id == user_id)
            )
        )
    
    return query.order_by(SiteProject.created_at.desc())\
        .offset(skip).limit(limit).all()

def get_active_projects(
    db: Session,
    user_id: Optional[UUID] = None,
    skip: int = 0,
    limit: int = 100
) -> List[SiteProject]:
    """Get active projects"""
    query = db.query(SiteProject).filter(
        or_(
            SiteProject.status == 'active',
            SiteProject.status == None  # Default is considered active
        )
    )
    
    if user_id:
        query = query.filter(
            or_(
                SiteProject.managers.any(User.id == user_id),
                SiteProject.subcontractors.any(Subcontractor.user_id == user_id)
            )
        )
    
    return query.order_by(SiteProject.created_at.desc())\
        .offset(skip).limit(limit).all()

def get_projects_by_status(
    db: Session,
    status: str,
    user_id: Optional[UUID] = None,
    skip: int = 0,
    limit: int = 100
) -> List[SiteProject]:
    """Get projects by status"""
    query = db.query(SiteProject).filter(SiteProject.status == status)
    
    if user_id:
        query = query.filter(
            or_(
                SiteProject.managers.any(User.id == user_id),
                SiteProject.subcontractors.any(Subcontractor.user_id == user_id)
            )
        )
    
    return query.order_by(SiteProject.created_at.desc())\
        .offset(skip).limit(limit).all()

def get_upcoming_projects(
    db: Session,
    user_id: Optional[UUID] = None,
    days_ahead: int = 30,
    skip: int = 0,
    limit: int = 100
) -> List[SiteProject]:
    """Get projects starting in the next N days"""
    from datetime import datetime, timedelta
    
    today = datetime.now().date()
    future_date = today + timedelta(days=days_ahead)
    
    query = db.query(SiteProject).filter(
        and_(
            SiteProject.start_date >= today,
            SiteProject.start_date <= future_date
        )
    )
    
    if user_id:
        query = query.filter(
            or_(
                SiteProject.managers.any(User.id == user_id),
                SiteProject.subcontractors.any(Subcontractor.user_id == user_id)
            )
        )
    
    return query.order_by(SiteProject.start_date.asc())\
        .offset(skip).limit(limit).all()

    
def is_subcontractor_assigned(
    db: Session,
    project_id: UUID,
    subcontractor_id: UUID
) -> bool:
    """
    Check if a subcontractor is already assigned to a project.
    
    Args:
        db: Database session
        project_id: The project ID to check
        subcontractor_id: The subcontractor ID to check
    
    Returns:
        True if the subcontractor is assigned to the project, False otherwise
    """
    from ..models.site_project import SiteProject
    from ..models.subcontractor import Subcontractor
    
    # Query to check if the relationship exists
    project = db.query(SiteProject).filter(
        SiteProject.id == project_id
    ).first()
    
    if not project:
        return False
    
    # Check if subcontractor is in the project's subcontractors list
    subcontractor_assigned = db.query(Subcontractor).filter(
        Subcontractor.id == subcontractor_id
    ).join(
        Subcontractor.assigned_projects
    ).filter(
        SiteProject.id == project_id
    ).first()
    
    return subcontractor_assigned is not None