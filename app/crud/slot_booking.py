from sqlalchemy.orm import Session
from typing import Optional, List
from ..models.slot_booking import SlotBooking
from ..schemas.slot_booking import SlotBookingCreate, SlotBookingUpdate
import uuid
import json

def get_slot_booking(db: Session, booking_id: int) -> Optional[SlotBooking]:
    return db.query(SlotBooking).filter(SlotBooking.id == booking_id).first()

def get_slot_bookings_by_project(db: Session, project_id: str) -> List[SlotBooking]:
    return db.query(SlotBooking).filter(SlotBooking.project_id == project_id).all()

def get_all_slot_bookings(db: Session, skip: int = 0, limit: int = 100) -> List[SlotBooking]:
    return db.query(SlotBooking).offset(skip).limit(limit).all()

def create_slot_booking(db: Session, slot_booking: SlotBookingCreate) -> SlotBooking:
    booking_key = str(uuid.uuid4())
    
    # Convert booked_assets list to JSON string for SQLite
    booked_assets_json = json.dumps(slot_booking.booked_assets) if slot_booking.booked_assets else "[]"
    
    db_slot_booking = SlotBooking(
        project_id=slot_booking.project_id,
        booking_title=slot_booking.booking_title,
        booking_for=slot_booking.booking_for,
        booked_assets=booked_assets_json,
        booking_status=slot_booking.booking_status,
        booking_time_dt=slot_booking.booking_time_dt,
        booking_duration_mins=slot_booking.booking_duration_mins,
        booking_description=slot_booking.booking_description,
        booking_notes=slot_booking.booking_notes,
        booking_created_by=slot_booking.booking_created_by,
        booking_key=booking_key
    )
    db.add(db_slot_booking)
    db.commit()
    db.refresh(db_slot_booking)
    return db_slot_booking

def update_slot_booking(db: Session, booking_id: int, slot_booking_update: SlotBookingUpdate) -> Optional[SlotBooking]:
    db_slot_booking = get_slot_booking(db, booking_id)
    if not db_slot_booking:
        return None
    
    update_data = slot_booking_update.dict(exclude_unset=True)
    
    # Handle booked_assets conversion to JSON
    if 'booked_assets' in update_data:
        update_data['booked_assets'] = json.dumps(update_data['booked_assets'])
    
    for field, value in update_data.items():
        setattr(db_slot_booking, field, value)
    
    db.commit()
    db.refresh(db_slot_booking)
    return db_slot_booking

def delete_slot_booking(db: Session, booking_id: int) -> bool:
    db_slot_booking = get_slot_booking(db, booking_id)
    if not db_slot_booking:
        return False
    
    db.delete(db_slot_booking)
    db.commit()
    return True
