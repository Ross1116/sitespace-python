from enum import Enum

class UserRole(str, Enum):
    MANAGER = "manager"
    ADMIN = "admin"
    SUBCONTRACTOR = "subcontractor"
    TV = "tv"

class ProjectStatus(str, Enum):
    ACTIVE = "active"
    PENDING = "pending"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    ON_HOLD = "on_hold"

class AssetStatus(str, Enum):
    AVAILABLE = "available"
    MAINTENANCE = "maintenance"
    RETIRED = "retired"

class BookingStatus(str, Enum):
    PENDING = "pending"
    CONFIRMED = "confirmed"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    DENIED = "denied"

class TradeSpecialty(str, Enum):
    ELECTRICIAN = "electrician"
    PLUMBER = "plumber"
    CARPENTER = "carpenter"
    MASON = "mason"
    PAINTER = "painter"
    HVAC = "hvac"
    ROOFER = "roofer"
    LANDSCAPER = "landscaper"
    GENERAL = "general"
    OTHER = "other"

class AssetTypeResolutionStatus(str, Enum):
    UNKNOWN = "unknown"
    INFERRED = "inferred"
    CONFIRMED = "confirmed"

class TradeResolutionStatus(str, Enum):
    UNKNOWN = "unknown"
    SUGGESTED = "suggested"
    CONFIRMED = "confirmed"


ASSET_TYPE_RESOLUTION_READY: frozenset[str] = frozenset(
    {
        AssetTypeResolutionStatus.INFERRED.value,
        AssetTypeResolutionStatus.CONFIRMED.value,
    }
)

class BookingAuditAction(str, Enum):
    CREATED = "created"
    APPROVED = "approved"
    DENIED = "denied"
    UPDATED = "updated"
    RESCHEDULED = "rescheduled"
    CANCELLED = "cancelled"
    DELETED = "deleted"
