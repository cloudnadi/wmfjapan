"""
Lambda: GET /registrations/available-slots
Public endpoint (no auth) for registrants to view available entry slots.

Query parameters:
  - eventType (required): The event to fetch slots for
  - numberOfSeats (optional, default 1): Number of seats needed

Response: List of available slots with current occupancy and remaining capacity.
Filters:
  - Status = ACTIVE
  - Booking window open (now between bookingStartsAt and bookingEndsAt)
  - Remaining capacity >= numberOfSeats
"""

import json
import os
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Optional

import boto3
from botocore.exceptions import ClientError
from boto3.dynamodb.conditions import Key

dynamodb = boto3.resource("dynamodb", region_name=os.environ.get("REGION"))
ENTRY_SLOTS_TABLE = os.environ.get("ENTRY_SLOTS_TABLE", "")
EVENTS_TABLE = os.environ.get("EVENTS_TABLE", "")


def decimal_to_python(obj):
    if isinstance(obj, Decimal):
        return int(obj) if obj == obj.to_integral_value() else float(obj)
    raise TypeError(f"Object of type {type(obj)} is not JSON serializable")


def cors_headers():
    return {
        "Access-Control-Allow-Origin": "*",
        "Access-Control-Allow-Headers": "Content-Type,Authorization",
        "Access-Control-Allow-Methods": "OPTIONS,GET",
        "Content-Type": "application/json",
    }


def respond(status: int, body: dict):
    return {
        "statusCode": status,
        "headers": cors_headers(),
        "body": json.dumps(body, default=decimal_to_python),
    }


def entry_slots_enabled(event_type: str) -> bool:
    """eventInfo.entrySlots.enabled for the event. Entry slots predate the switch, so missing means ON;
    only an explicit false turns them off. (Same rule as entry_slots_logic.is_entry_slots_enabled.)"""
    if not EVENTS_TABLE:
        return True
    item = dynamodb.Table(EVENTS_TABLE).get_item(Key={"eventType": event_type}).get("Item") or {}
    info = item.get("eventInfo", {})
    if isinstance(info, str):
        try:
            info = json.loads(info) if info.strip() else {}
        except ValueError:
            return True
    block = info.get("entrySlots") if isinstance(info, dict) else None
    return not (isinstance(block, dict) and block.get("enabled") is False)


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def is_booking_window_open(slot: dict) -> bool:
    """Check if the slot's booking window is currently open."""
    now = datetime.now(timezone.utc)
    
    booking_starts = slot.get("bookingStartsAt")
    booking_ends = slot.get("bookingEndsAt")
    
    # If either timestamp is missing/empty, slot is always available
    if not booking_starts or not booking_ends:
        return True
    
    try:
        start = datetime.fromisoformat(str(booking_starts).replace("Z", "+00:00"))
        end = datetime.fromisoformat(str(booking_ends).replace("Z", "+00:00"))
        return start <= now <= end
    except (ValueError, TypeError):
        # If timestamps are malformed, consider window open
        return True


def normalize_available_slot(item: dict[str, Any]) -> dict[str, Any]:
    """Normalize a slot for the public API response."""
    bookable = int(item.get("bookableCapacity", 0) or 0)
    buffer = int(item.get("bufferCapacity", 0) or 0)
    total = bookable + buffer

    # reservedSeats is maintained atomically on the slot itself; reading it
    # directly avoids read-after-write lag on the reservations table's GSI.
    occupancy = int(item.get("reservedSeats", 0) or 0)
    remaining = max(bookable - occupancy, 0)

    return {
        "slotId": item.get("slotId", ""),
        "name": item.get("name", ""),
        "nickname": item.get("nickname", ""),
        "bookableCapacity": bookable,
        "remainingCapacity": remaining,
        "bookingStartsAt": item.get("bookingStartsAt", ""),
        "bookingEndsAt": item.get("bookingEndsAt", ""),
    }


def lambda_handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    http_method = event.get("httpMethod")

    if http_method == "OPTIONS":
        return respond(200, {})

    if http_method != "GET":
        return respond(405, {"error": "Only GET is allowed"})

    if not ENTRY_SLOTS_TABLE:
        return respond(500, {"error": "Entry slots not configured"})

    # Extract query parameters
    query_params = event.get("queryStringParameters") or {}
    event_type = str(query_params.get("eventType", "")).strip()
    
    try:
        num_seats = int(query_params.get("numberOfSeats", "1") or "1")
        if num_seats < 1:
            num_seats = 1
    except (TypeError, ValueError):
        num_seats = 1

    if not event_type:
        return respond(400, {"error": "eventType query parameter is required"})

    try:
        if not entry_slots_enabled(event_type):
            return respond(200, {"eventType": event_type, "numberOfSeats": num_seats, "availableSlots": [],
                                 "count": 0, "enabled": False})
    except ClientError as exc:
        print(f"Error reading entry slot setting for {event_type}: {exc}")
        return respond(500, {"error": "Failed to fetch available slots"})

    # Fetch all slots for the event
    try:
        table = dynamodb.Table(ENTRY_SLOTS_TABLE)
        response = table.query(
            KeyConditionExpression=Key("eventType").eq(event_type),
        )
        items = response.get("Items", [])
    except ClientError as exc:
        print(f"Error fetching slots for {event_type}: {exc}")
        return respond(500, {"error": "Failed to fetch available slots"})

    # Filter: status ACTIVE, booking window open, enough remaining capacity
    available_slots = []
    for item in items:
        # Check status
        if item.get("status", "INACTIVE") != "ACTIVE":
            continue

        # Check booking window
        if not is_booking_window_open(item):
            continue

        # Check capacity
        bookable = int(item.get("bookableCapacity", 0) or 0)
        occupancy = int(item.get("reservedSeats", 0) or 0)
        remaining = max(bookable - occupancy, 0)

        if remaining < num_seats:
            continue

        available_slots.append(normalize_available_slot(item))

    # Sort by creation date (oldest first)
    available_slots.sort(key=lambda s: item.get("createdAt", ""))

    return respond(
        200,
        {
            "eventType": event_type,
            "numberOfSeats": num_seats,
            "availableSlots": available_slots,
            "count": len(available_slots),
        },
    )
