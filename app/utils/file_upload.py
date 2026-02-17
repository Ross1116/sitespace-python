import os
import aiofiles
from fastapi import UploadFile, HTTPException
from typing import Optional
from ..core.config import settings
import uuid
from datetime import datetime

async def save_upload_file(upload_file: UploadFile, folder: str = "uploads") -> dict:
    """
    Save uploaded file and return file information
    """
    try:
        # Create upload directory if it doesn't exist
        upload_dir = os.path.join(settings.export_files_absolute_path, folder)
        os.makedirs(upload_dir, exist_ok=True)
        
        # Generate unique filename
        file_extension = os.path.splitext(upload_file.filename)[1]
        unique_filename = f"{uuid.uuid4()}{file_extension}"
        file_path = os.path.join(upload_dir, unique_filename)
        
        # Save file
        async with aiofiles.open(file_path, 'wb') as out_file:
            content = await upload_file.read()
            await out_file.write(content)
        
        # Return file information
        return {
            "success": True,
            "file_path": file_path,
            "img_path": f"/{settings.export_files_server_path}/{folder}/{unique_filename}",
            "message": "File uploaded successfully",
            "filename": unique_filename,
            "original_filename": upload_file.filename,
            "size": len(content)
        }
        
    except Exception:
        raise HTTPException(status_code=500, detail="Error uploading file")

def get_file_path(filename: str, folder: str = "uploads") -> str:
    """
    Get file path for serving files
    """
    return os.path.join(settings.export_files_absolute_path, folder, filename)

def delete_file(file_path: str) -> bool:
    """
    Delete file from filesystem
    """
    try:
        if os.path.exists(file_path):
            os.remove(file_path)
            return True
        return False
    except Exception:
        return False
