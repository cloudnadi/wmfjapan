"""
Entry Slot Capacity Management Utilities

This module provides core business logic for entry slot selection and capacity
management, including:
  - Calculating required seats from attendance counts
  - Computing remaining slot capacity
  - Atomically reserving seats in a slot
  - Cancelling reservations and releasing capacity

Critical Business Rule:
  A seat represents one person aged 6 or above.
  - Adult           = 1 seat
  - Kid 9–14        = 1 seat
  - Kid 6–8         = 1 seat
  - Kid below 6     = 0 seats

  Required seats = adults + kids9To14 + kids6To8
"""

import json
import os
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Optional

import boto3
from botocore.exceptions import ClientError
from boto3.dynamodb.conditions import Key, Attr

# ── Environment ──────────────────────────────────────────────────────────────

dynamodb = boto3.resource("dynamodb", region_name=os.environ.get("REGION"))
ENTRY_SLOTS_TABLE = os.environ.get("ENTRY_SLOTS_TABLE", "")
SLOT_RESERVATIONS_TABLE = os.environ.get("SLOT_RESERVATIONS_TABLE", "")
ACTIVE_RESERVATION_STATUSES = {"CONFIRMED", "HELD"}


def is_entry_slots_enabled(event_info) -> bool:
    """Per-event switch: eventInfo.entrySlots.enabled.

    Entry slots existed before they could be switched, so a missing/odd value means ON (existing
    events keep working); only an explicit boolean false turns them off for the event. Accepts the
    dict or the JSON string the events table sometimes stores.
    """
    if isinstance(event_info, str):
        try:
            event_info = json.loads(event_info) if event_info.strip() else {}
        except ValueError:
            return True
    block = event_info.get("entrySlots") if isinstance(event_info, dict) else None
    return not (isinstance(block, dict) and block.get("enabled") is False)


def _is_conditional_check_failed(exc: ClientError) -> bool:
    return exc.response.get("Error", {}).get("Code") == "ConditionalCheckFailedException"


def _reservation_occupies_seats(status: str) -> bool:
    return str(status or "").upper() in ACTIVE_RESERVATION_STATUSES


def _get_slot_occupied_seats_from_reservations(slot_id: str) -> int:
    if not SLOT_RESERVATIONS_TABLE:
        return 0

    table = dynamodb.Table(SLOT_RESERVATIONS_TABLE)
    response = table.query(
            KeyConditionExpression=Key("entrySlotId").eq(slot_id),
        FilterExpression=Attr("status").is_in(list(ACTIVE_RESERVATION_STATUSES)),
        IndexName="SlotOccupancyIndex",
        ProjectionExpression="requiredSeats",
    )

    reserved = 0
    for item in response.get("Items", []):
        reserved += int(item.get("requiredSeats", 0))
    return reserved


def _ensure_reserved_counter(event_type: str, slot_id: str, slot: dict) -> dict:
    if "reservedSeats" in slot:
        return slot

    occupied = _get_slot_occupied_seats_from_reservations(slot_id)
    table = dynamodb.Table(ENTRY_SLOTS_TABLE)
    response = table.update_item(
        Key={"eventType": event_type, "slotId": slot_id},
        UpdateExpression=(
            "SET reservedSeats = if_not_exists(reservedSeats, :occupied), "
            "updatedAt = if_not_exists(updatedAt, :now)"
        ),
        ExpressionAttributeValues={
            ":occupied": occupied,
            ":now": datetime.now(timezone.utc).isoformat(),
        },
        ReturnValues="ALL_NEW",
    )
    return response.get("Attributes", slot)


def _increment_slot_reserved_seats(event_type: str, slot_id: str, required_seats: int) -> dict:
    slot = get_slot(event_type, slot_id)
    if not slot:
        raise RuntimeError(f"Slot {slot_id} not found for event {event_type}")

    slot = _ensure_reserved_counter(event_type, slot_id, slot)
    bookable = max(int(slot.get("bookableCapacity", 0) or 0), 0)
    max_reserved_before_increment = bookable - required_seats
    if max_reserved_before_increment < 0:
        raise RuntimeError(
            f"Insufficient capacity in slot {slot_id}: required {required_seats}, available {bookable}"
        )

    try:
        table = dynamodb.Table(ENTRY_SLOTS_TABLE)
        response = table.update_item(
            Key={"eventType": event_type, "slotId": slot_id},
            UpdateExpression=(
                "SET reservedSeats = if_not_exists(reservedSeats, :zero) + :required, "
                "updatedAt = :now"
            ),
            ConditionExpression=(
                Attr("status").eq("ACTIVE")
                & Attr("reservedSeats").lte(max_reserved_before_increment)
            ),
            ExpressionAttributeValues={
                ":zero": 0,
                ":required": required_seats,
                ":now": datetime.now(timezone.utc).isoformat(),
            },
            ReturnValues="ALL_NEW",
        )
        return response.get("Attributes", {})
    except ClientError as e:
        if _is_conditional_check_failed(e):
            current_slot = get_slot(event_type, slot_id) or slot
            reserved = int(current_slot.get("reservedSeats", 0) or 0)
            available = max(bookable - reserved, 0)
            raise RuntimeError(
                f"Insufficient capacity in slot {slot_id}: "
                f"required {required_seats}, available {available}"
            )
        raise RuntimeError(f"Error reserving slot capacity: {e}")


def _decrement_slot_reserved_seats(event_type: str, slot_id: str, seats: int) -> None:
    if seats <= 0 or not ENTRY_SLOTS_TABLE or not slot_id:
        return

    try:
        table = dynamodb.Table(ENTRY_SLOTS_TABLE)
        table.update_item(
            Key={"eventType": event_type, "slotId": slot_id},
            UpdateExpression=(
                "SET reservedSeats = reservedSeats - :seats, updatedAt = :now"
            ),
            ConditionExpression=Attr("reservedSeats").gte(seats),
            ExpressionAttributeValues={
                ":seats": seats,
                ":now": datetime.now(timezone.utc).isoformat(),
            },
        )
    except ClientError as e:
        if _is_conditional_check_failed(e):
            table = dynamodb.Table(ENTRY_SLOTS_TABLE)
            table.update_item(
                Key={"eventType": event_type, "slotId": slot_id},
                UpdateExpression="SET reservedSeats = :zero, updatedAt = :now",
                ExpressionAttributeValues={
                    ":zero": 0,
                    ":now": datetime.now(timezone.utc).isoformat(),
                },
            )
            return
        raise RuntimeError(f"Error releasing slot capacity: {e}")


# ── Seat Calculation ─────────────────────────────────────────────────────────

def calculate_required_seats(
    adults: int,
    kids9_to_14: int,
    kids6_to_8: int,
    kids_below_6: int,
) -> int:
    """
    Calculate the number of entry slot seats required by a registration.

    Args:
        adults: Number of adults (15+ years)
        kids9_to_14: Number of kids aged 9–14
        kids6_to_8: Number of kids aged 6–8
        kids_below_6: Number of kids below 6

    Returns:
        The number of seats required (0 or positive integer).

    Raises:
        ValueError: If any count is negative.
    """
    if adults < 0 or kids9_to_14 < 0 or kids6_to_8 < 0 or kids_below_6 < 0:
        raise ValueError("All attendee counts must be non-negative")

    return adults + kids9_to_14 + kids6_to_8


def calculate_required_seats_from_dict(counts: dict) -> int:
    """
    Calculate required seats from an attendee counts dictionary.

    Dictionary keys (case-insensitive):
      - "adults" or "adult"
      - "kids9To14" or "kids914"
      - "kids6To8" or "kids68"
      - "kidsBelow6" or "kidsUnder6"

    Args:
        counts: Dictionary of attendee counts

    Returns:
        The number of seats required.

    Raises:
        TypeError: If counts are invalid.
    """
    if not isinstance(counts, dict):
        raise TypeError("Counts must be a dictionary")

    # Normalize keys (support multiple naming conventions)
    adults = _get_count(counts, ("adults", "adult"), 0)
    kids914 = _get_count(counts, ("kids9To14", "kids914"), 0)
    kids68 = _get_count(counts, ("kids6To8", "kids68"), 0)
    kids_below_6 = _get_count(counts, ("kidsBelow6", "kidsUnder6"), 0)
    return calculate_required_seats(adults, kids914, kids68, kids_below_6)


def _get_count(counts: dict, keys: tuple, default: int = 0) -> int:
    """Helper to extract a count from a dictionary with multiple key aliases."""
    for key in keys:
        if key in counts:
            try:
                val = int(counts[key])
                return val if val >= 0 else default
            except (TypeError, ValueError):
                continue
    return default


# ── Slot Capacity ───────────────────────────────────────────────────────────

def get_slot(event_type: str, slot_id: str) -> Optional[dict]:
    """
    Fetch a single entry slot by ID.

    Args:
        event_type: Event type identifier
        slot_id: Slot ID

    Returns:
        Slot item dict or None if not found.
    """
    if not ENTRY_SLOTS_TABLE:
        return None

    try:
        table = dynamodb.Table(ENTRY_SLOTS_TABLE)
        response = table.get_item(Key={"eventType": event_type, "slotId": slot_id})
        return response.get("Item")
    except ClientError as e:
        raise RuntimeError(f"Error fetching slot: {e}")


def get_slot_remaining_capacity(event_type: str, slot_id: str) -> int:
    """
    Calculate the remaining (available) capacity for a slot.

    Remaining = bookableCapacity - (CONFIRMED + HELD seats)

    Args:
        event_type: Event type identifier
        slot_id: Slot ID

    Returns:
        Number of available seats (0 or positive).

    Raises:
        RuntimeError: On DynamoDB error.
    """
    if not ENTRY_SLOTS_TABLE or not SLOT_RESERVATIONS_TABLE:
        return 0

    try:
        # Fetch the slot to get bookableCapacity
        slot = get_slot(event_type, slot_id)
        if not slot:
            return 0

        bookable = int(slot.get("bookableCapacity", 0))
        if bookable < 0:
            bookable = 0

        if "reservedSeats" in slot:
            reserved = int(slot.get("reservedSeats", 0) or 0)
        else:
            reserved = _get_slot_occupied_seats_from_reservations(slot_id)

        remaining = bookable - reserved
        return max(remaining, 0)
    except ClientError as e:
        raise RuntimeError(f"Error calculating remaining capacity: {e}")


def get_all_active_slots_for_event(event_type: str) -> list:
    """
    Get all ACTIVE entry slots for an event, sorted by creation.

    Args:
        event_type: Event type identifier

    Returns:
        List of slot items (empty if no active slots or event not found).
    """
    if not ENTRY_SLOTS_TABLE:
        return []

    try:
        table = dynamodb.Table(ENTRY_SLOTS_TABLE)
        response = table.query(
            KeyConditionExpression=Key("eventType").eq(event_type),
            FilterExpression=Attr("status") == "ACTIVE",
        )
        items = response.get("Items", [])
        # Sort by createdAt ascending
        items.sort(key=lambda x: x.get("createdAt", ""), reverse=False)
        return items
    except ClientError as e:
        raise RuntimeError(f"Error fetching active slots: {e}")


# ── Slot Reservation (Atomic) ─────────────────────────────────────────────────

def reserve_seats_atomic(
    event_type: str,
    slot_id: str,
    registration_id: str,
    required_seats: int,
    hold_duration_minutes: int = 30,
) -> dict:
    """
    Atomically reserve seats in a slot for a registration.

    This function:
    1. Fetches the slot and validates it exists and is ACTIVE.
    2. Calculates remaining capacity at that moment.
    3. Uses DynamoDB conditional writes to ensure atomicity:
       - Validates remaining capacity >= required_seats.
       - Creates a CONFIRMED reservation.

    If the slot does not have enough capacity or is inactive, raises an exception.

    Args:
        event_type: Event type identifier
        slot_id: Entry slot ID
        registration_id: Registration ID (FK to registrations table)
        required_seats: Number of seats to reserve
        hold_duration_minutes: How long to hold HELD reservations before TTL expire

    Returns:
        The created reservation item with fields:
        {
          "reservationId": "uuid",
          "eventType": "...",
          "entrySlotId": "...",
          "registrationId": "...",
          "requiredSeats": N,
          "status": "CONFIRMED",
          "createdAt": "ISO timestamp",
          "updatedAt": "ISO timestamp"
        }

    Raises:
        ValueError: If inputs are invalid (negative seats, etc.)
        RuntimeError: On capacity insufficient, slot not found, or DB error.
    """
    if not ENTRY_SLOTS_TABLE or not SLOT_RESERVATIONS_TABLE:
        raise RuntimeError("Entry slots tables not configured")

    if required_seats <= 0:
        raise ValueError("Required seats must be positive")

    # Validate slot exists and is ACTIVE
    slot = get_slot(event_type, slot_id)
    if not slot:
        raise RuntimeError(f"Slot {slot_id} not found for event {event_type}")

    if slot.get("status") != "ACTIVE":
        raise RuntimeError(f"Slot {slot.get('name')} ({slot.get('nickname')}) is not ACTIVE (status={slot.get('status')})")

    # Check booking time window (optional)
    booking_starts = slot.get("bookingStartsAt")
    booking_ends = slot.get("bookingEndsAt")
    now = datetime.now(timezone.utc).isoformat()

    if booking_starts and now < booking_starts:
        raise RuntimeError(
            f"Booking for slot {slot.get('name')} ({slot.get('nickname')}) has not started yet (starts {booking_starts})"
        )
    if booking_ends and now > booking_ends:
        raise RuntimeError(
            f"Booking for slot {slot.get('name')} ({slot.get('nickname')}) has ended (ended {booking_ends})"
        )

    _increment_slot_reserved_seats(event_type, slot_id, required_seats)

    # Create the reservation
    reservation_id = str(uuid.uuid4())
    now_iso = datetime.now(timezone.utc).isoformat()

    reservation = {
        "eventType": event_type,
        "reservationId": reservation_id,
        "entrySlotId": slot_id,
        "registrationId": registration_id,
        "requiredSeats": required_seats,
        "status": "CONFIRMED",
        "createdAt": now_iso,
        "updatedAt": now_iso,
    }

    try:
        table = dynamodb.Table(SLOT_RESERVATIONS_TABLE)
        table.put_item(Item=reservation)
        return reservation
    except ClientError as e:
        _decrement_slot_reserved_seats(event_type, slot_id, required_seats)
        raise RuntimeError(f"Error creating reservation: {e}")


def get_registration_reservation(registration_id: str) -> Optional[dict]:
    """
    Fetch the slot reservation for a registration (if any).

    Args:
        registration_id: Registration ID

    Returns:
        Reservation item or None if not found.
    """
    if not SLOT_RESERVATIONS_TABLE:
        return None

    try:
        table = dynamodb.Table(SLOT_RESERVATIONS_TABLE)
        response = table.query(
            IndexName="RegistrationIdIndex",
            KeyConditionExpression=Key("registrationId").eq(registration_id),
            Limit=1,
        )
        items = response.get("Items", [])
        return items[0] if items else None
    except ClientError as e:
        raise RuntimeError(f"Error fetching registration reservation: {e}")


def cancel_reservation(reservation_id: str, event_type: str) -> None:
    """
    Cancel a slot reservation, releasing its seats.

    Sets status to CANCELLED and clears expiresAt (so TTL doesn't auto-delete).

    Args:
        reservation_id: Reservation ID
        event_type: Event type (required for composite key)

    Raises:
        RuntimeError: On DynamoDB error.
    """
    if not SLOT_RESERVATIONS_TABLE:
        return

    try:
        table = dynamodb.Table(SLOT_RESERVATIONS_TABLE)
        reservation = table.get_item(
            Key={"eventType": event_type, "reservationId": reservation_id}
        ).get("Item")

        table.update_item(
            Key={"eventType": event_type, "reservationId": reservation_id},
            UpdateExpression="SET #status = :cancelled, #updated = :now REMOVE expiresAt",
            ConditionExpression=Attr("status").is_in(list(ACTIVE_RESERVATION_STATUSES)),
            ExpressionAttributeNames={
                "#status": "status",
                "#updated": "updatedAt",
            },
            ExpressionAttributeValues={
                ":cancelled": "CANCELLED",
                ":now": datetime.now(timezone.utc).isoformat(),
            },
        )
    except ClientError as e:
        if _is_conditional_check_failed(e):
            return
        raise RuntimeError(f"Error cancelling reservation: {e}")

    try:
        if reservation and _reservation_occupies_seats(reservation.get("status")):
            _decrement_slot_reserved_seats(
                event_type,
                reservation.get("entrySlotId", ""),
                int(reservation.get("requiredSeats", 0) or 0),
            )
    except ClientError as e:
        raise RuntimeError(f"Error cancelling reservation: {e}")


def update_reservation_status(
    event_type: str, reservation_id: str, new_status: str
) -> None:
    """
    Update the status of a reservation (e.g., HELD → CONFIRMED).

    Args:
        event_type: Event type
        reservation_id: Reservation ID
        new_status: New status (HELD, CONFIRMED, CANCELLED, EXPIRED)

    Raises:
        RuntimeError: On DynamoDB error.
    """
    if not SLOT_RESERVATIONS_TABLE:
        return

    try:
        table = dynamodb.Table(SLOT_RESERVATIONS_TABLE)
        reservation = table.get_item(
            Key={"eventType": event_type, "reservationId": reservation_id}
        ).get("Item")
        old_status = reservation.get("status") if reservation else ""
        seats = int((reservation or {}).get("requiredSeats", 0) or 0)
        slot_id = (reservation or {}).get("entrySlotId", "")
        old_occupies = _reservation_occupies_seats(old_status)
        new_occupies = _reservation_occupies_seats(new_status)

        if not old_occupies and new_occupies:
            _increment_slot_reserved_seats(event_type, slot_id, seats)

        table.update_item(
            Key={"eventType": event_type, "reservationId": reservation_id},
            UpdateExpression="SET #status = :new_status, #updated = :now",
            ExpressionAttributeNames={
                "#status": "status",
                "#updated": "updatedAt",
            },
            ExpressionAttributeValues={
                ":new_status": new_status,
                ":now": datetime.now(timezone.utc).isoformat(),
            },
        )
        if old_occupies and not new_occupies:
            _decrement_slot_reserved_seats(event_type, slot_id, seats)
    except ClientError as e:
        raise RuntimeError(f"Error updating reservation status: {e}")


# ── Slot Management ──────────────────────────────────────────────────────────

def create_entry_slot(
    event_type: str,
    name: str,
    nickname: str,
    bookable_capacity: int,
    buffer_capacity: int,
    status: str = "ACTIVE",
    booking_starts_at: Optional[str] = None,
    booking_ends_at: Optional[str] = None,
) -> dict:
    """
    Create a new entry slot.

    Args:
        event_type: Event type identifier
        name: Slot name (e.g., "Slot 1")
        nickname: Slot nickname (e.g., "ABC")
        bookable_capacity: Capacity available to regular users
        buffer_capacity: Protected capacity for admin use
        status: ACTIVE or INACTIVE
        booking_starts_at: ISO timestamp when bookings open (optional)
        booking_ends_at: ISO timestamp when bookings close (optional)

    Returns:
        The created slot item.

    Raises:
        ValueError: If inputs are invalid.
        RuntimeError: On DynamoDB error.
    """
    if not ENTRY_SLOTS_TABLE:
        raise RuntimeError("Entry slots table not configured")

    if bookable_capacity < 0 or buffer_capacity < 0:
        raise ValueError("Capacities must be non-negative")

    slot_id = str(uuid.uuid4())
    now_iso = datetime.now(timezone.utc).isoformat()

    slot = {
        "eventType": event_type,
        "slotId": slot_id,
        "name": name,
        "nickname": nickname,
        "bookableCapacity": bookable_capacity,
        "bufferCapacity": buffer_capacity,
        "reservedSeats": 0,
        "status": status.upper(),
        "createdAt": now_iso,
        "updatedAt": now_iso,
    }

    if booking_starts_at:
        slot["bookingStartsAt"] = booking_starts_at
    if booking_ends_at:
        slot["bookingEndsAt"] = booking_ends_at

    try:
        table = dynamodb.Table(ENTRY_SLOTS_TABLE)
        table.put_item(Item=slot)
        return slot
    except ClientError as e:
        raise RuntimeError(f"Error creating slot: {e}")


def update_entry_slot(
    event_type: str,
    slot_id: str,
    name: Optional[str] = None,
    nickname: Optional[str] = None,
    bookable_capacity: Optional[int] = None,
    buffer_capacity: Optional[int] = None,
    status: Optional[str] = None,
    booking_starts_at: Optional[str] = None,
    booking_ends_at: Optional[str] = None,
) -> dict:
    """
    Update an entry slot. Only provided fields are updated.

    Args:
        event_type: Event type
        slot_id: Slot ID
        name: New name (optional)
        nickname: New nickname (optional)
        bookable_capacity: New bookable capacity (optional, must be >= 0)
        buffer_capacity: New buffer capacity (optional, must be >= 0)
        status: New status (optional)
        booking_starts_at: New booking start time (optional)
        booking_ends_at: New booking end time (optional)

    Returns:
        The updated slot item.

    Raises:
        ValueError: If inputs are invalid.
        RuntimeError: On DynamoDB error.
    """
    if not ENTRY_SLOTS_TABLE:
        raise RuntimeError("Entry slots table not configured")

    if bookable_capacity is not None and bookable_capacity < 0:
        raise ValueError("Bookable capacity must be non-negative")
    if buffer_capacity is not None and buffer_capacity < 0:
        raise ValueError("Buffer capacity must be non-negative")

    updates = []
    values = {}

    if name is not None:
        updates.append("#name = :name")
        values[":name"] = name
    if nickname is not None:
        updates.append("nickname = :nickname")
        values[":nickname"] = nickname
    if bookable_capacity is not None:
        updates.append("bookableCapacity = :bookable")
        values[":bookable"] = bookable_capacity
    if buffer_capacity is not None:
        updates.append("bufferCapacity = :buffer")
        values[":buffer"] = buffer_capacity
    if status is not None:
        updates.append("#status = :status")
        values[":status"] = status.upper()
    if booking_starts_at is not None:
        updates.append("bookingStartsAt = :starts")
        values[":starts"] = booking_starts_at
    if booking_ends_at is not None:
        updates.append("bookingEndsAt = :ends")
        values[":ends"] = booking_ends_at

    if not updates:
        # No updates provided; return the current slot
        return get_slot(event_type, slot_id) or {}

    updates.append("updatedAt = :now")
    values[":now"] = datetime.now(timezone.utc).isoformat()

    update_expression = "SET " + ", ".join(updates)
    attr_names = {"#name": "name", "#status": "status"}
    condition_expression = None
    if bookable_capacity is not None:
        condition_expression = (
            Attr("reservedSeats").not_exists()
            | Attr("reservedSeats").lte(bookable_capacity)
        )

    try:
        table = dynamodb.Table(ENTRY_SLOTS_TABLE)
        update_kwargs = {
            "Key": {"eventType": event_type, "slotId": slot_id},
            "UpdateExpression": update_expression,
            "ExpressionAttributeNames": attr_names,
            "ExpressionAttributeValues": values,
            "ReturnValues": "ALL_NEW",
        }
        if condition_expression is not None:
            update_kwargs["ConditionExpression"] = condition_expression
        response = table.update_item(
            **update_kwargs,
        )
        return response.get("Attributes", {})
    except ClientError as e:
        if _is_conditional_check_failed(e):
            current = get_slot(event_type, slot_id) or {}
            reserved = int(current.get("reservedSeats", 0) or 0)
            raise ValueError(
                f"Bookable capacity cannot be below reserved seats ({reserved})"
            )
        raise RuntimeError(f"Error updating slot: {e}")


def release_buffer_seats(
    event_type: str, slot_id: str, num_seats: int
) -> dict:
    """
    Release buffer seats by increasing bookableCapacity.

    Args:
        event_type: Event type
        slot_id: Slot ID
        num_seats: Number of buffer seats to release (must be > 0)

    Returns:
        The updated slot item.

    Raises:
        ValueError: If num_seats is invalid.
        RuntimeError: On error.
    """
    if not ENTRY_SLOTS_TABLE:
        raise RuntimeError("Entry slots table not configured")

    if num_seats <= 0:
        raise ValueError("Number of seats to release must be positive")

    slot = get_slot(event_type, slot_id)
    if not slot:
        raise RuntimeError(f"Slot {slot_id} not found")

    current_buffer = int(slot.get("bufferCapacity", 0))
    if num_seats > current_buffer:
        raise ValueError(
            f"Cannot release {num_seats} seats: only {current_buffer} buffer seats available"
        )

    try:
        table = dynamodb.Table(ENTRY_SLOTS_TABLE)
        response = table.update_item(
            Key={"eventType": event_type, "slotId": slot_id},
            UpdateExpression=(
                "SET bookableCapacity = bookableCapacity + :seats, "
                "bufferCapacity = bufferCapacity - :seats, updatedAt = :now"
            ),
            ConditionExpression=Attr("bufferCapacity").gte(num_seats),
            ExpressionAttributeValues={
                ":seats": num_seats,
                ":now": datetime.now(timezone.utc).isoformat(),
            },
            ReturnValues="ALL_NEW",
        )
        return response.get("Attributes", {})
    except ClientError as e:
        if _is_conditional_check_failed(e):
            latest = get_slot(event_type, slot_id) or slot
            available = int(latest.get("bufferCapacity", 0) or 0)
            raise ValueError(
                f"Cannot release {num_seats} seats: only {available} buffer seats available"
            )
        raise RuntimeError(f"Error releasing buffer seats: {e}")
