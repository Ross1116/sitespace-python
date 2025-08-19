from sqlalchemy import Column, Integer, String, Boolean, DateTime
from sqlalchemy.sql import func
from ..core.database import Base

class FileUpload(Base):
    __tablename__ = "file_uploads"
    
    id = Column(Integer, primary_key=True, index=True)
    success = Column(Boolean, default=False)
    img_path = Column(String)
    file_path = Column(String)
    message = Column(String)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    
    def __repr__(self):
        return f"<FileUpload(id={self.id}, success={self.success}, file_path='{self.file_path}')>"
