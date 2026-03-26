from __future__ import annotations

import uuid

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..models.lookahead import ProjectAlertPolicy


def ensure_project_alert_policy(db: Session, project_id: uuid.UUID) -> ProjectAlertPolicy:
    policy = (
        db.query(ProjectAlertPolicy)
        .filter(ProjectAlertPolicy.project_id == project_id)
        .one_or_none()
    )
    if policy is None:
        savepoint = db.begin_nested()
        try:
            policy = ProjectAlertPolicy(project_id=project_id)
            db.add(policy)
            db.flush()
            savepoint.commit()
        except IntegrityError:
            savepoint.rollback()
            policy = (
                db.query(ProjectAlertPolicy)
                .filter(ProjectAlertPolicy.project_id == project_id)
                .one_or_none()
            )
            if policy is None:
                raise
    return policy
