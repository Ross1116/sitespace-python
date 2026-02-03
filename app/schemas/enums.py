from enum import Enum

class UserRole(str, Enum):
    MANAGER = "manager"
    ADMIN = "admin"
    SUBCONTRACTOR = "subcontractor"

class ProjectStatus(str, Enum):
    ACTIVE = "active"
    PENDING = "pending"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    ON_HOLD = "on_hold"

class AssetStatus(str, Enum):
    AVAILABLE = "available"
    IN_USE = "in_use"
    MAINTENANCE = "maintenance"
    RETIRED = "retired"

class BookingStatus(str, Enum):
    PENDING = "PENDING"
    CONFIRMED = "CONFIRMED"
    IN_PROGRESS = "IN_PROGRESS"
    COMPLETED = "COMPLETED"
    CANCELLED = "CANCELLED"
    DENIED = "DENIED"

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

class BookingAuditAction(str, Enum):
    CREATED = "created"
    APPROVED = "approved"
    DENIED = "denied"
    UPDATED = "updated"
    RESCHEDULED = "rescheduled"
    CANCELLED = "cancelled"
    DELETED = "deleted"