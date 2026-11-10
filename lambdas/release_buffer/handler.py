"""
Lambda: /admin/events/{eventType}/entry-slots/{slotId}/release-buffer
Protected by Cognito JWT authorizer.

Allows admins to release buffer seats, increasing the bookable capacity.
This is a destructive operation and should be logged for audit purposes.
"""

import json
import os
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

import boto3
from botocore.exceptions import ClientError

import entry_slots_logic

dynamodb = boto3.resource("dynamodb", region_name=os.environ.get("REGION"))
ENTRY_SLOTS_TABLE = os.environ.get("ENTRY_SLOTS_TABLE", "")


def decimal_to_python(obj):
    if isinstance(obj, Decimal):
        return int(obj) if obj == obj.to_integral_value() else float(obj)
    raise TypeError(f"Object of type {type(obj)} is not JSON serializable")


def cors_headers():
    return {
        "Access-Control-Allow-Origin": "*",
        "Access-Control-Allow-Headers": "Content-Type,Authorization",
        "Access-Control-Allow-Methods": "OPTIONS,POST",
        "Content-Type": "application/json",
    }


def respond(status: int, body: dict):
    return {
        "statusCode": status,
        "headers": cors_headers(),
        "body": json.dumps(body, default=decimal_to_python),
    }


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
    """Normalize a slot item for response."""
    bookable = int(item.get("bookableCapacity", 0) or 0)
    buffer = int(item.get("bufferCapacity", 0) or 0)
    total = bookable + buffer

    return {
        "eventType": item.get("eventType", ""),
        "slotId": item.get("slotId", ""),
        "name": item.get("name", ""),
        "nickname": item.get("nickname", ""),
        "bookableCapacity": bookable,
        "bufferCapacity": buffer,
        "totalCapacity": total,
        "status": item.get("status", "INACTIVE"),
        "bookingStartsAt": item.get("bookingStartsAt", ""),
        "bookingEndsAt": item.get("bookingEndsAt", ""),
        "createdAt": item.get("createdAt", ""),
        "updatedAt": item.get("updatedAt", ""),
    }


def lambda_handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    http_method = event.get("httpMethod")

    if http_method == "OPTIONS":
        return respond(200, {})

    if http_method != "POST":
        return respond(405, {"error": "Only POST is allowed"})

    if not ENTRY_SLOTS_TABLE:
        return respond(500, {"error": "Entry slots table not configured"})

    # Extract path parameters
    path_params = event.get("pathParameters") or {}
    event_type = str(path_params.get("eventType", "")).strip()
    slot_id = str(path_params.get("slotId", "")).strip()

    if not event_type or not slot_id:
        return respond(
            400,
            {"error": "eventType and slotId path parameters are required"},
        )

    payload = parse_json_body(event)

    # Validate the request
    try:
        num_seats = int(payload.get("numberOfSeats", 0) or 0)
    except (TypeError, ValueError):
        return respond(400, {"error": "numberOfSeats must be a positive integer"})

    if num_seats <= 0:
        return respond(400, {"error": "numberOfSeats must be greater than 0"})

    try:
        # Use the utility function to release buffer seats
        updated_slot = entry_slots_logic.release_buffer_seats(
            event_type=event_type,
            slot_id=slot_id,
            num_seats=num_seats,
        )

        return respond(
            200,
            {
                "message": f"Released {num_seats} buffer seat(s)",
                "slot": normalize_slot(updated_slot),
            },
        )

    except ValueError as exc:
        return respond(400, {"error": str(exc)})
    except RuntimeError as exc:
        print(f"Error releasing buffer: {exc}")
        return respond(404 if "not found" in str(exc).lower() else 500, {"error": str(exc)})
    except Exception as exc:
        print(f"Error releasing buffer: {exc}")
        return respond(500, {"error": "Failed to release buffer seats"})
