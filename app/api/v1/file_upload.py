from fastapi import APIRouter, Depends, UploadFile, File, HTTPException
from sqlalchemy.orm import Session
from ...core.database import get_db
from ...core.security import get_current_active_user
from ...models.user import User
from ...utils.file_upload import save_upload_file
from ...schemas.base import MessageResponse

router = APIRouter(prefix="/uploadfile", tags=["File Upload"])

@router.post("/", response_model=MessageResponse)
async def upload_file(
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user)
):
    """
    Upload a file
    """
    try:
        # Validate file
        if not file.filename:
            raise HTTPException(status_code=400, detail="No file provided")
        
        # Check file size (optional - you can add size limits)
        # if file.size > MAX_FILE_SIZE:
        #     raise HTTPException(status_code=400, detail="File too large")
        
        # Save file
        result = await save_upload_file(file)
        
        if result["success"]:
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
        else:
            return MessageResponse(
                success=False,
                message=result["message"]
            )
            
    except HTTPException:
        raise
    except Exception as e:
        return MessageResponse(
            success=False,
            message=f"Error uploading file: {str(e)}"
        )
