from sqlalchemy.orm import Session, joinedload
from sqlalchemy import and_, or_, func
from typing import Optional, List, Dict, Any
from uuid import UUID
from datetime import date

from fastapi import HTTPException

from ..models.site_project import SiteProject
from ..models.user import User
from ..models.subcontractor import Subcontractor
from ..schemas.enums import ProjectStatus, UserRole
from ..schemas.site_project import SiteProjectCreate, SiteProjectFilters, SiteProjectUpdate


def _escape_ilike(value: str) -> str:
    """Escape SQL LIKE meta-characters so user input is treated literally."""
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _apply_project_filters(query: Any, filters: Optional[SiteProjectFilters]) -> Any:
    """Apply shared project filters to a query."""
    if not filters:
        return query

    if filters.name:
        query = query.filter(SiteProject.name.ilike(f"%{_escape_ilike(filters.name)}%", escape="\\"))

    if filters.location:
        query = query.filter(SiteProject.location.ilike(f"%{_escape_ilike(filters.location)}%", escape="\\"))

    if filters.status:
        query = query.filter(SiteProject.status == filters.status)

    if filters.start_date_from:
        query = query.filter(SiteProject.start_date >= filters.start_date_from)

    if filters.start_date_to:
        query = query.filter(SiteProject.start_date <= filters.start_date_to)

    if filters.end_date_from:
        query = query.filter(SiteProject.end_date >= filters.end_date_from)

    if filters.end_date_to:
        query = query.filter(SiteProject.end_date <= filters.end_date_to)

    if filters.user_id:
        query = query.filter(SiteProject.managers.any(User.id == filters.user_id))

    return query

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
    filters: Optional[SiteProjectFilters] = None,
    skip: int = 0,
    limit: int = 100
) -> List[SiteProject]:
    """Get projects with filters"""
    query = _apply_project_filters(db.query(SiteProject), filters)
    return query.order_by(SiteProject.created_at.desc()).offset(skip).limit(limit).all()

def count_projects(db: Session, filters: Optional[SiteProjectFilters] = None) -> int:
    """Count projects with filters"""
    query = _apply_project_filters(db.query(SiteProject), filters)
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

def archive_project(db: Session, project: SiteProject) -> SiteProject:
    """Archive a project instead of hard delete.

    Hard-deleting projects can cascade-delete assets/bookings depending on DB
    constraints, which risks destroying booking history. We mark the project as
    cancelled to retain historical records.
    """
    project.status = ProjectStatus.CANCELLED
    db.commit()
    db.refresh(project)

    return project


def has_project_access(db: Session, project_id: UUID, user_id: UUID) -> bool:
    """Check if user has access to project (as manager only)"""
    project = db.query(SiteProject)\
        .filter(SiteProject.id == project_id)\
        .filter(SiteProject.managers.any(User.id == user_id))\
        .first()
    
    return project is not None

def is_project_manager(db: Session, project_id: UUID, user_id: UUID) -> bool:
    """Check if user is a project manager"""
    project = (
        db.query(SiteProject)
        .join(SiteProject.managers)
        .filter(SiteProject.id == project_id)
        .filter(User.id == user_id)
        .filter(User.role.in_([UserRole.MANAGER.value, UserRole.ADMIN.value]))
        .first()
    )

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
        # Consider the first *real* manager/admin as lead (TV members don't count)
        lead_candidates = [
            m for m in project.managers
            if getattr(m, "role", None) in (UserRole.MANAGER.value, UserRole.ADMIN.value)
        ]
        return bool(lead_candidates) and lead_candidates[0].id == user_id
    
    return False

def count_project_managers(db: Session, project_id: UUID) -> int:
    """Count project managers"""
    project = db.query(SiteProject)\
        .options(joinedload(SiteProject.managers))\
        .filter(SiteProject.id == project_id)\
        .first()
    
    if project:
        return len(
            [
                m for m in project.managers
                if getattr(m, "role", None) in (UserRole.MANAGER.value, UserRole.ADMIN.value)
            ]
        )
    return 0

def count_lead_managers(db: Session, project_id: UUID) -> int:
    """Count lead managers (for now, returns 1 if any managers exist)"""
    project = db.query(SiteProject)\
        .options(joinedload(SiteProject.managers))\
        .filter(SiteProject.id == project_id)\
        .first()
    
    if project and project.managers:
        manager_count = len(
            [
                m for m in project.managers
                if getattr(m, "role", None) in (UserRole.MANAGER.value, UserRole.ADMIN.value)
            ]
        )
        return 1 if manager_count > 0 else 0
    return 0

def add_manager_to_project(
    db: Session,
    project_id: UUID,
    manager_id: UUID,
    is_lead: bool = False,
) -> bool:
    """Add manager to project. Returns True if the manager was added, False otherwise."""
    project = get_project(db, project_id)
    manager = db.query(User).filter(User.id == manager_id).first()

    if not project or not manager:
        return False

    if manager not in project.managers:
        if is_lead:
            # If setting as lead, add at beginning
            project.managers.insert(0, manager)
        else:
            project.managers.append(manager)
        db.commit()
        return True

    return False

def remove_manager_from_project(db: Session, project_id: UUID, manager_id: UUID) -> bool:
    """Remove manager from project"""
    project = get_project(db, project_id)
    if project:
        original = [m for m in project.managers if m.id == manager_id]
        if not original:
            return False
        project.managers = [m for m in project.managers if m.id != manager_id]
        db.commit()
        return True
    return False

def add_subcontractor_to_project(
    db: Session,
    project_id: UUID,
    subcontractor_id: UUID,
    is_active: bool = True,
) -> bool:
    """Add subcontractor to project"""
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
    subcontractor_id: UUID,
) -> bool:
    """Remove subcontractor from project"""
    project = get_project(db, project_id)
    if project:
        original = [s for s in project.subcontractors if s.id == subcontractor_id]
        if not original:
            return False
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
    update_data: Any,
) -> bool:
    """Update subcontractor details in project"""
    # Placeholder for future implementation
    return False

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
            Subcontractor.trade_specialty.ilike(f"%{_escape_ilike(trade_specialty)}%", escape="\\")
        )
    
    # Only active subcontractors
    query = query.filter(Subcontractor.is_active.is_(True))
    
    return query.all()

def get_user_projects(
    db: Session,
    user_id: UUID,
    role: Optional[str] = None,
    skip: int = 0,
    limit: int = 100
) -> List[SiteProject]:
    """Get all projects for a specific user (managers only)"""
    query = db.query(SiteProject)
    
    # Only filter by managers (subcontractors are separate entities)
    query = query.filter(SiteProject.managers.any(User.id == user_id))
    
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
    escaped = _escape_ilike(search_term)
    search_filter = or_(
        SiteProject.name.ilike(f"%{escaped}%", escape="\\"),
        SiteProject.location.ilike(f"%{escaped}%", escape="\\") if SiteProject.location else False,
        SiteProject.description.ilike(f"%{escaped}%", escape="\\") if SiteProject.description else False,
    )
    query = query.filter(search_filter)
    
    # If user_id provided, filter to user's projects only
    if user_id:
        query = query.filter(SiteProject.managers.any(User.id == user_id))
    
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
            SiteProject.status == ProjectStatus.ACTIVE.value,
            SiteProject.status == None
        )
    )
    
    if user_id:
        query = query.filter(SiteProject.managers.any(User.id == user_id))
    
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
        query = query.filter(SiteProject.managers.any(User.id == user_id))
    
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
    from datetime import datetime, timedelta, timezone

    today = datetime.now(timezone.utc).date()
    future_date = today + timedelta(days=days_ahead)
    
    query = db.query(SiteProject).filter(
        and_(
            SiteProject.start_date >= today,
            SiteProject.start_date <= future_date
        )
    )
    
    if user_id:
        query = query.filter(SiteProject.managers.any(User.id == user_id))
    
    return query.order_by(SiteProject.start_date.asc())\
        .offset(skip).limit(limit).all()

def is_subcontractor_assigned(
    db: Session,
    project_id: UUID,
    subcontractor_id: UUID
) -> bool:
    """Check if a subcontractor is already assigned to a project"""
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

def get_project_statistics(db: Session, project_id: UUID) -> Optional[Dict[str, Any]]:
    """Get project statistics"""
    project = get_project_with_details(db, project_id)

    if not project:
        return None

    return {
        "project_id": str(project.id),
        "project_name": project.name,
        "total_managers": len(project.managers),
        "total_subcontractors": len(project.subcontractors),
        "total_assets": len(project.assets) if hasattr(project, 'assets') else 0,
        "total_bookings": len(project.slot_bookings) if hasattr(project, 'slot_bookings') else 0,
        "status": project.status
    }


def check_sub_project_access(
    db: Session,
    subcontractor: "Subcontractor",
    project: "SiteProject",
) -> bool:
    """Return True if ``subcontractor`` is assigned to ``project``, False otherwise.

    The project object must have its ``subcontractors`` relationship loaded
    before calling this (e.g. via a prior ``db.query`` or joinedload).
    Callers in the API layer are responsible for translating False into HTTP 403.
    """
    return any(str(sub.id) == str(subcontractor.id) for sub in project.subcontractors)
