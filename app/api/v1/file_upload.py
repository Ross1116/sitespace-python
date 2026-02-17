import os
from fastapi import APIRouter, Depends, UploadFile, File, HTTPException
from sqlalchemy.orm import Session
from ...core.database import get_db
from ...core.security import get_current_active_user
from ...models.user import User
from ...utils.file_upload import save_upload_file
from ...schemas.base import MessageResponse

router = APIRouter(prefix="/uploadfile", tags=["File Upload"])

MAX_FILE_SIZE = 10 * 1024 * 1024  # 10 MB
ALLOWED_EXTENSIONS = {
    ".jpg", ".jpeg", ".png", ".gif", ".webp",  # images
    ".pdf", ".doc", ".docx", ".xls", ".xlsx",  # documents
    ".csv", ".txt", ".json",                    # text/data
}

@router.post("/", response_model=MessageResponse)
async def upload_file(
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user)
):
    """
    Upload a file (max 10 MB, restricted file types)
    """
    try:
        # Validate file
        if not file.filename:
            raise HTTPException(status_code=400, detail="No file provided")

        # Validate file extension
        ext = os.path.splitext(file.filename)[1].lower()
        if ext not in ALLOWED_EXTENSIONS:
            raise HTTPException(
                status_code=400,
                detail=f"File type '{ext}' not allowed. Allowed: {', '.join(sorted(ALLOWED_EXTENSIONS))}"
            )

        # Check file size
        contents = await file.read()
        if len(contents) > MAX_FILE_SIZE:
            raise HTTPException(status_code=400, detail="File too large. Maximum size is 10 MB")
        await file.seek(0)

        # Save file (raises HTTPException on failure)
        result = await save_upload_file(file)

        return MessageResponse(
            success=True,
            message=result["message"],
            data={
                "file_path": result["file_path"],
                "img_path": result["img_path"],
                "filename": result["filename"],
                "original_filename": result["original_filename"],
                "size": result["size"]
            }
        )

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail="Error uploading file") from e
