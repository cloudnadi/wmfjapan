"""
Lambda: /admin/events/{eventType}/entry-slots
Protected by Cognito JWT authorizer.

Supports admin CRUD for entry slot management:
- GET    /admin/events/{eventType}/entry-slots          → list all slots for event
- POST   /admin/events/{eventType}/entry-slots          → create new slot
- GET    /admin/events/{eventType}/entry-slots/{slotId} → get slot details
- PATCH  /admin/events/{eventType}/entry-slots/{slotId} → update slot
- DELETE /admin/events/{eventType}/entry-slots/{slotId} → delete slot

Slots define entry capacity, booking windows, and buffer seats.
"""

import json
import os
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

import boto3
from botocore.exceptions import ClientError
from boto3.dynamodb.conditions import Key, Attr

import entry_slots_logic

dynamodb = boto3.resource("dynamodb", region_name=os.environ.get("REGION"))
TABLE_NAME = os.environ.get("ENTRY_SLOTS_TABLE", "")
SLOT_RESERVATIONS_TABLE = os.environ.get("SLOT_RESERVATIONS_TABLE", "")

VALID_STATUSES = {"ACTIVE", "INACTIVE"}


def decimal_to_python(obj):
    if isinstance(obj, Decimal):
        return int(obj) if obj == obj.to_integral_value() else float(obj)
    raise TypeError(f"Object of type {type(obj)} is not JSON serializable")


def cors_headers():
    return {
        "Access-Control-Allow-Origin": "*",
        "Access-Control-Allow-Headers": "Content-Type,Authorization",
        "Access-Control-Allow-Methods": "OPTIONS,GET,POST,PATCH,DELETE",
        "Content-Type": "application/json",
    }


def respond(status: int, body: dict):
    return {
        "statusCode": status,
        "headers": cors_headers(),
        "body": json.dumps(body, default=decimal_to_python),
    }


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def parse_json_body(event: dict[str, Any]) -> dict[str, Any]:
    if event.get("isBase64Encoded"):
        return {}

    raw_body = event.get("body") or "{}"
    try:
        parsed = json.loads(raw_body)
        return parsed if isinstance(parsed, dict) else {}
    except json.JSONDecodeError:
        return {}


def normalize_slot(item: dict[str, Any]) -> dict[str, Any]:
    """Normalize a slot item and include computed capacity values."""
    bookable = int(item.get("bookableCapacity", 0) or 0)
    buffer = int(item.get("bufferCapacity", 0) or 0)
    total = bookable + buffer

    # reservedSeats is maintained atomically on the slot itself (see
    # entry_slots_logic.reserve_seats_atomic); reading it directly avoids the
    # read-after-write lag of querying the reservations table's GSI.
    occupancy = int(item.get("reservedSeats", 0) or 0)

    return {
        "eventType": item.get("eventType", ""),
        "slotId": item.get("slotId", ""),
        "name": item.get("name", ""),
        "nickname": item.get("nickname", ""),
        "bookableCapacity": bookable,
        "bufferCapacity": buffer,
        "totalCapacity": total,
        "occupiedSeats": occupancy,
        "remainingCapacity": max(bookable - occupancy, 0),
        "status": item.get("status", "INACTIVE"),
        "bookingStartsAt": item.get("bookingStartsAt", ""),
        "bookingEndsAt": item.get("bookingEndsAt", ""),
        "createdAt": item.get("createdAt", ""),
        "updatedAt": item.get("updatedAt", ""),
    }


def list_slots_for_event(event_type: str) -> list:
    """List all slots for an event, sorted by creation date."""
    if not TABLE_NAME:
        return []

    try:
        table = dynamodb.Table(TABLE_NAME)
        response = table.query(
            KeyConditionExpression=Key("eventType").eq(event_type),
        )

        items = response.get("Items", [])
        # Sort by createdAt ascending (oldest first)
        items.sort(key=lambda x: x.get("createdAt", ""))
        return [normalize_slot(item) for item in items]
    except ClientError as e:
        raise RuntimeError(f"Error listing slots: {e}")


def validate_payload(payload: dict[str, Any]) -> dict[str, str]:
    """Validate slot creation/update payload."""
    errors: dict[str, str] = {}

    # Name is required for creation; optional for updates
    name = str(payload.get("name", "")).strip()
    if "name" in payload and not name:
        errors["name"] = "Slot name is required"

    # Nickname is required for creation; optional for updates
    nickname = str(payload.get("nickname", "")).strip()
    if "nickname" in payload and not nickname:
        errors["nickname"] = "Slot nickname is required"

    # Bookable capacity
    if "bookableCapacity" in payload:
        try:
            cap = int(payload["bookableCapacity"] or 0)
            if cap < 0:
                raise ValueError
        except (TypeError, ValueError):
            errors["bookableCapacity"] = "Bookable capacity must be a non-negative integer"

    # Buffer capacity
    if "bufferCapacity" in payload:
        try:
            cap = int(payload["bufferCapacity"] or 0)
            if cap < 0:
                raise ValueError
        except (TypeError, ValueError):
            errors["bufferCapacity"] = "Buffer capacity must be a non-negative integer"

    # Status
    if "status" in payload:
        status = str(payload.get("status", "")).strip().upper()
        if status not in VALID_STATUSES:
            errors["status"] = f"Status must be one of: {', '.join(VALID_STATUSES)}"

    # Booking times (optional ISO timestamps)
    for field in ("bookingStartsAt", "bookingEndsAt"):
        if field in payload and payload[field]:
            try:
                datetime.fromisoformat(str(payload[field]).replace("Z", "+00:00"))
            except ValueError:
                errors[field] = f"{field} must be a valid ISO timestamp"

    return errors


def lambda_handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    http_method = event.get("httpMethod")

    if http_method == "OPTIONS":
        return respond(200, {})

    if not TABLE_NAME:
        return respond(500, {"error": "Entry slots table not configured"})

    # Extract path parameters
    path_params = event.get("pathParameters") or {}
    event_type = str(path_params.get("eventType", "")).strip()
    slot_id = str(path_params.get("slotId", "")).strip()

    if not event_type:
        return respond(400, {"error": "eventType path parameter is required"})

    table = dynamodb.Table(TABLE_NAME)

    # ── GET: List or retrieve a single slot ──────────────────────────────────

    if http_method == "GET":
        try:
            if slot_id:
                # GET /admin/events/{eventType}/entry-slots/{slotId}
                response = table.get_item(
                    Key={"eventType": event_type, "slotId": slot_id}
                )
                item = response.get("Item")
                if not item:
                    return respond(404, {"error": f"Slot {slot_id} not found"})
                return respond(200, {"slot": normalize_slot(item)})
            else:
                # GET /admin/events/{eventType}/entry-slots
                slots = list_slots_for_event(event_type)
                return respond(
                    200,
                    {
                        "slots": slots,
                        "count": len(slots),
                    },
                )
        except Exception as exc:
            print(f"Error fetching slot(s): {exc}")
            return respond(500, {"error": "Failed to fetch slot(s)"})

    # ── POST: Create a new slot ───────────────────────────────────────────────

    if http_method == "POST":
        if slot_id:
            # POST with slotId in path is invalid
            return respond(400, {"error": "Cannot POST with slotId in path; use POST without slotId"})

        payload = parse_json_body(event)

        # Validate required fields for creation
        errors = validate_payload(payload)
        if not str(payload.get("name", "")).strip():
            errors["name"] = "Slot name is required"
        if not str(payload.get("nickname", "")).strip():
            errors["nickname"] = "Slot nickname is required"
        if "bookableCapacity" not in payload:
            errors["bookableCapacity"] = "Bookable capacity is required"
        if "bufferCapacity" not in payload:
            errors["bufferCapacity"] = "Buffer capacity is required"

        if errors:
            return respond(400, {"error": "Validation failed", "fields": errors})

        try:
            new_slot = entry_slots_logic.create_entry_slot(
                event_type=event_type,
                name=str(payload.get("name", "")).strip(),
                nickname=str(payload.get("nickname", "")).strip(),
                bookable_capacity=int(payload.get("bookableCapacity", 0)),
                buffer_capacity=int(payload.get("bufferCapacity", 0)),
                status=str(payload.get("status", "ACTIVE")).upper(),
                booking_starts_at=payload.get("bookingStartsAt"),
                booking_ends_at=payload.get("bookingEndsAt"),
            )

            return respond(
                201,
                {
                    "message": "Slot created successfully",
                    "slot": normalize_slot(new_slot),
                },
            )
        except ValueError as exc:
            return respond(400, {"error": str(exc)})
        except Exception as exc:
            print(f"Error creating slot: {exc}")
            return respond(500, {"error": "Failed to create slot"})

    # ── PATCH: Update a slot ──────────────────────────────────────────────────

    if http_method == "PATCH":
        if not slot_id:
            return respond(400, {"error": "slotId path parameter is required for PATCH"})

        payload = parse_json_body(event)
        errors = validate_payload(payload)

        if errors:
            return respond(400, {"error": "Validation failed", "fields": errors})

        try:
            # Fetch current slot to verify it exists
            response = table.get_item(Key={"eventType": event_type, "slotId": slot_id})
            if not response.get("Item"):
                return respond(404, {"error": f"Slot {slot_id} not found"})

            # Use the utility function to update
            updated_slot = entry_slots_logic.update_entry_slot(
                event_type=event_type,
                slot_id=slot_id,
                name=payload.get("name"),
                nickname=payload.get("nickname"),
                bookable_capacity=payload.get("bookableCapacity"),
                buffer_capacity=payload.get("bufferCapacity"),
                status=payload.get("status"),
                booking_starts_at=payload.get("bookingStartsAt"),
                booking_ends_at=payload.get("bookingEndsAt"),
            )

            return respond(
                200,
                {
                    "message": "Slot updated successfully",
                    "slot": normalize_slot(updated_slot),
                },
            )
        except ValueError as exc:
            return respond(400, {"error": str(exc)})
        except Exception as exc:
            print(f"Error updating slot: {exc}")
            return respond(500, {"error": "Failed to update slot"})

    # ── DELETE: Delete a slot (only if no reservations) ──────────────────────

    if http_method == "DELETE":
        if not slot_id:
            return respond(400, {"error": "slotId path parameter is required for DELETE"})

        try:
            # Check if any reservations exist for this slot
            if SLOT_RESERVATIONS_TABLE:
                res_table = dynamodb.Table(SLOT_RESERVATIONS_TABLE)
                response = res_table.query(
                    IndexName="SlotOccupancyIndex",
                    KeyConditionExpression=Key("entrySlotId").eq(slot_id),
                    Limit=1,
                )
                if response.get("Items"):
                    return respond(
                        400,
                        {
                            "error": "Cannot delete slot with existing reservations. "
                            "Please cancel or reassign all registrations first."
                        },
                    )

            # Delete the slot
            table.delete_item(Key={"eventType": event_type, "slotId": slot_id})
            return respond(200, {"message": "Slot deleted successfully"})

        except Exception as exc:
            print(f"Error deleting slot: {exc}")
            return respond(500, {"error": "Failed to delete slot"})

    return respond(405, {"error": "Method not allowed"})
