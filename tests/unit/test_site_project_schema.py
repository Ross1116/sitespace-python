from datetime import time

import pytest
from pydantic import ValidationError

from app.schemas.site_project import SiteProjectCreate, SiteProjectUpdate


def test_project_create_rejects_inverted_default_work_hours():
    with pytest.raises(ValidationError, match="default_work_end_time must be later"):
        SiteProjectCreate(
            name="Bad hours",
            default_work_start_time=time(16, 0),
            default_work_end_time=time(8, 0),
        )


def test_project_update_rejects_inverted_default_work_hours_when_both_supplied():
    with pytest.raises(ValidationError, match="default_work_end_time must be later"):
        SiteProjectUpdate(
            default_work_start_time=time(16, 0),
            default_work_end_time=time(16, 0),
        )
