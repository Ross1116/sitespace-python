from __future__ import annotations

from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Any, List, Optional

from pydantic import Field, field_validator

from .base import BaseSchema


class AssetTypeResponse(BaseSchema):
    """Public representation of an asset type from the taxonomy."""
    code: str
    display_name: str
    parent_code: Optional[str] = None
    is_active: bool = True
    is_user_selectable: bool = True
    max_hours_per_day: Decimal
    planning_attributes_json: Optional[dict[str, Any]] = None
    taxonomy_version: int = 1
    introduced_at: Optional[datetime] = None
    retired_at: Optional[datetime] = None


class AssetTypeCreate(BaseSchema):
    """Schema for adding a new asset type to the taxonomy."""
    code: str = Field(..., min_length=1, max_length=50, pattern=r"^[a-z][a-z0-9_]*$")
    display_name: str = Field(..., min_length=1, max_length=255)
    parent_code: Optional[str] = Field(None, max_length=50)
    is_active: bool = True
    is_user_selectable: bool = True
    max_hours_per_day: Decimal = Field(..., ge=0, le=24)
    planning_attributes_json: Optional[dict[str, Any]] = None
    taxonomy_version: int = Field(1, ge=1)

    @field_validator("max_hours_per_day", mode="before")
    @classmethod
    def round_hours(cls, v: object) -> Decimal:
        try:
            d = Decimal(str(v))
        except (InvalidOperation, TypeError, ValueError) as exc:
            raise ValueError("max_hours_per_day must be a numeric value") from exc
        if not (Decimal(0) <= d <= Decimal(24)):
            raise ValueError("max_hours_per_day must be between 0 and 24")
        return round(d, 1)


class AssetTypeUpdate(BaseSchema):
    """Partial update for an existing asset type."""
    display_name: Optional[str] = Field(None, min_length=1, max_length=255)
    parent_code: Optional[str] = Field(None, max_length=50)
    is_active: Optional[bool] = None
    is_user_selectable: Optional[bool] = None
    max_hours_per_day: Optional[Decimal] = Field(None, ge=0, le=24)
    planning_attributes_json: Optional[dict[str, Any]] = None
    taxonomy_version: Optional[int] = Field(None, ge=1)

    @field_validator("max_hours_per_day", mode="before")
    @classmethod
    def round_hours(cls, v: object) -> Decimal | None:
        if v is not None:
            try:
                d = Decimal(str(v))
            except (InvalidOperation, TypeError, ValueError) as exc:
                raise ValueError("max_hours_per_day must be a numeric value") from exc
            if not (Decimal(0) <= d <= Decimal(24)):
                raise ValueError("max_hours_per_day must be between 0 and 24")
            return round(d, 1)
        return v


class AssetTypeBriefResponse(BaseSchema):
    """Minimal representation for dropdowns and selection lists."""
    code: str
    display_name: str
    max_hours_per_day: Decimal


class AssetTypeListResponse(BaseSchema):
    """List response for asset types."""
    asset_types: List[AssetTypeResponse]
    total: int
