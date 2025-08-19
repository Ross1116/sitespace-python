from fastapi import APIRouter, Depends, HTTPException, status, Query
from sqlalchemy.orm import Session
from typing import Optional
from ...core.database import get_db
from ...core.security import get_current_active_user
from ...crud.slot_booking import (
    create_slot_booking, get_slot_bookings_by_project, get_slot_booking,
    update_slot_booking, delete_slot_booking, get_all_slot_bookings
)
from ...models.user import User
from ...schemas.slot_booking import (
    SlotBookingCreate, SlotBookingUpdate, SlotBookingResponse, SlotBookingListResponse
)
from ...schemas.base import MessageResponse
import json

router = APIRouter(prefix="/SlotBooking", tags=["Slot Booking"])

@router.post("/saveSlotBooking", response_model=SlotBookingResponse)
def save_slot_booking(
    slot_booking: SlotBookingCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user)
):
    """
    Create a new slot booking
    """
    try:
        created_booking = create_slot_booking(db, slot_booking)
        
        # Convert the created booking to response format with proper JSON handling
        booking_data = {
            'id': created_booking.id,
            'booking_project': created_booking.booking_project,
            'booking_title': created_booking.booking_title,
            'booking_for': created_booking.booking_for,
            'booking_status': created_booking.booking_status,
            'booking_time_dt': created_booking.booking_time_dt,
            'booking_duration_mins': created_booking.booking_duration_mins,
            'booking_description': created_booking.booking_description,
            'booking_notes': created_booking.booking_notes,
            'booking_created_by': created_booking.booking_created_by,
            'booking_key': created_booking.booking_key,
            'created_at': created_booking.created_at,
            'updated_at': created_booking.updated_at
        }
        
        # Handle booked_assets conversion from JSON string to list
        if created_booking.booked_assets:
            try:
                booking_data['booked_assets'] = json.loads(created_booking.booked_assets)
            except (json.JSONDecodeError, TypeError):
                booking_data['booked_assets'] = []
        else:
            booking_data['booked_assets'] = []
        
        return SlotBookingResponse(**booking_data)
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to save slot booking: {str(e)}"
        )

@router.get("/getSlotBookingList", response_model=SlotBookingListResponse)
def get_slot_booking_list(
    booking_project: Optional[str] = Query(None, description="Filter by project"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user)
):
    """
    Get list of slot bookings, optionally filtered by project
    """
    try:
        if booking_project:
            bookings = get_slot_bookings_by_project(db, booking_project)
        else:
            bookings = get_all_slot_bookings(db)
        
        # Convert database objects to response objects with proper JSON handling
        booking_responses = []
        for booking in bookings:
            # Create dict from database object
            booking_data = {
                'id': booking.id,
                'booking_project': booking.booking_project,
                'booking_title': booking.booking_title,
                'booking_for': booking.booking_for,
                'booking_status': booking.booking_status,
                'booking_time_dt': booking.booking_time_dt,
                'booking_duration_mins': booking.booking_duration_mins,
                'booking_description': booking.booking_description,
                'booking_notes': booking.booking_notes,
                'booking_created_by': booking.booking_created_by,
                'booking_key': booking.booking_key,
                'created_at': booking.created_at,
                'updated_at': booking.updated_at
            }
            
            # Handle booked_assets conversion from JSON string to list
            if booking.booked_assets:
                try:
                    booking_data['booked_assets'] = json.loads(booking.booked_assets)
                except (json.JSONDecodeError, TypeError):
                    booking_data['booked_assets'] = []
            else:
                booking_data['booked_assets'] = []
            
            booking_responses.append(SlotBookingResponse(**booking_data))
        
        return SlotBookingListResponse(
            success=True,
            message="Slot bookings retrieved successfully",
            data=booking_responses
        )
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to retrieve slot bookings: {str(e)}"
        )

@router.put("/updateSlotBooking/{booking_id}", response_model=SlotBookingResponse)
def update_slot_booking_endpoint(
    booking_id: int,
    slot_booking_update: SlotBookingUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user)
):
    """
    Update an existing slot booking
    """
    try:
        updated_booking = update_slot_booking(db, booking_id, slot_booking_update)
        if not updated_booking:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Slot booking not found"
            )
        
        # Convert the updated booking to response format with proper JSON handling
        booking_data = {
            'id': updated_booking.id,
            'booking_project': updated_booking.booking_project,
            'booking_title': updated_booking.booking_title,
            'booking_for': updated_booking.booking_for,
            'booking_status': updated_booking.booking_status,
            'booking_time_dt': updated_booking.booking_time_dt,
            'booking_duration_mins': updated_booking.booking_duration_mins,
            'booking_description': updated_booking.booking_description,
            'booking_notes': updated_booking.booking_notes,
            'booking_created_by': updated_booking.booking_created_by,
            'booking_key': updated_booking.booking_key,
            'created_at': updated_booking.created_at,
            'updated_at': updated_booking.updated_at
        }
        
        # Handle booked_assets conversion from JSON string to list
        if updated_booking.booked_assets:
            try:
                booking_data['booked_assets'] = json.loads(updated_booking.booked_assets)
            except (json.JSONDecodeError, TypeError):
                booking_data['booked_assets'] = []
        else:
            booking_data['booked_assets'] = []
        
        return SlotBookingResponse(**booking_data)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to update slot booking: {str(e)}"
        )

@router.delete("/deleteSlotBooking/{booking_id}", response_model=MessageResponse)
def delete_slot_booking_endpoint(
    booking_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user)
):
    """
    Delete a slot booking
    """
    try:
        success = delete_slot_booking(db, booking_id)
        if not success:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Slot booking not found"
            )
        return MessageResponse(message="Slot booking deleted successfully!")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to delete slot booking: {str(e)}"
        )

@router.get("/getSlotBookingDetails/{booking_id}", response_model=SlotBookingResponse)
def get_slot_booking_details(
    booking_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user)
):
    """
    Get details of a specific slot booking
    """
    try:
        booking = get_slot_booking(db, booking_id)
        if not booking:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Slot booking not found"
            )
        return booking
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to retrieve slot booking details: {str(e)}"
        )
