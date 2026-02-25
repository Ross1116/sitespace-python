from sqlalchemy import Column, String, Integer, DateTime, ForeignKey
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
import uuid

from app.core.database import Base


class StoredFile(Base):
    __tablename__ = "stored_files"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    original_filename = Column(String(255), nullable=False)
    # "local" for now; future values: "s3", "gcs", "azure"
    storage_backend = Column(String(50), nullable=False, default="local", server_default="local")
    # Internal path — never returned to clients; serve via /api/files/{id}
    storage_path = Column(String(1024), nullable=False)
    content_type = Column(String(100))
    file_size = Column(Integer)
    uploaded_by_id = Column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    # Relationships
    uploaded_by = relationship("User", foreign_keys=[uploaded_by_id])
    site_plans = relationship("SitePlan", back_populates="file")

    def __repr__(self) -> str:
        return f"<StoredFile(id={self.id}, filename='{self.original_filename}')>"
