"""
Lambda: /admin/events
Protected by Cognito JWT authorizer.
Supports listing, viewing, creating, updating, and deleting events.

Events drive the public registration flow: the register Lambda reads this table
by `eventType` and only accepts registrations when `status == "active"`. This
admin CRUD keeps that table maintainable without hand-rolled CLI put-items.
"""

import json
import os
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

import boto3
from botocore.exceptions import ClientError

dynamodb = boto3.resource("dynamodb", region_name=os.environ["REGION"])
TABLE_NAME = os.environ["EVENTS_TABLE"]

VALID_STATUSES = {"active", "inactive", "archived"}


def decimal_to_python(obj):
    if isinstance(obj, Decimal):
        return int(obj) if obj == obj.to_integral_value() else float(obj)
    raise TypeError(f"Object of type {type(obj)} is not JSON serializable")


def cors_headers():
    return {
        "Access-Control-Allow-Origin": "*",
        "Access-Control-Allow-Headers": "Content-Type,Authorization",
        "Access-Control-Allow-Methods": "OPTIONS,GET,POST,PUT,DELETE",
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


def normalize_event_info(raw: Any) -> dict[str, Any]:
    """eventInfo may be stored as a dict or a JSON string; return a dict."""
    if isinstance(raw, str):
        raw = raw.strip()
        if not raw:
            return {}
        try:
            parsed = json.loads(raw)
            return parsed if isinstance(parsed, dict) else {}
        except json.JSONDecodeError:
            return {}
    return raw if isinstance(raw, dict) else {}


def normalize_event(item: dict[str, Any]) -> dict[str, Any]:
    event_info = normalize_event_info(item.get("eventInfo", {}))
    registration = item.get("registration", {})
    if not isinstance(registration, dict):
        registration = {}
    return {
        "eventType": item.get("eventType", ""),
        "eventName": item.get("eventName", ""),
        "eventDate": item.get("eventDate", ""),
        "hostId": item.get("hostId", ""),
        "status": item.get("status", "inactive"),
        "description": item.get("description", ""),
        "venue": item.get("venue", ""),
        "venueAddress": item.get("venueAddress", ""),
        "eventNote": item.get("eventNote", ""),
        "capacity": int(item.get("capacity", 0) or 0),
        "registrationPath": item.get("registrationPath", ""),
        "registration": registration,
        "eventInfo": event_info,
        "createdAt": item.get("createdAt", ""),
        "updatedAt": item.get("updatedAt", ""),
    }


def list_events(table, host_id: str = ""):
    items = []
    scan_kwargs: dict[str, Any] = {}

    while True:
        response = table.scan(**scan_kwargs)
        items.extend(response.get("Items", []))
        if "LastEvaluatedKey" not in response:
            break
        scan_kwargs["ExclusiveStartKey"] = response["LastEvaluatedKey"]

    if host_id:
        items = [item for item in items if str(item.get("hostId", "")).strip() == host_id]

    items.sort(key=lambda item: (str(item.get("eventDate", "")), str(item.get("eventName", "")).lower()))
    return [normalize_event(item) for item in items]


def validate_payload(payload: dict[str, Any]) -> dict[str, str]:
    errors: dict[str, str] = {}

    if not str(payload.get("eventType", "")).strip():
        errors["eventType"] = "Event type (id) is required"

    if not str(payload.get("eventName", "")).strip():
        errors["eventName"] = "Event name is required"

    if not str(payload.get("hostId", "")).strip():
        errors["hostId"] = "Host ID is required"

    status = str(payload.get("status", "inactive")).strip().lower()
    if status not in VALID_STATUSES:
        errors["status"] = "Status must be active, inactive, or archived"

    capacity = payload.get("capacity", 0)
    try:
        if int(capacity or 0) < 0:
            errors["capacity"] = "Capacity must be 0 or greater"
    except (TypeError, ValueError):
        errors["capacity"] = "Capacity must be a whole number"

    registration = payload.get("registration", {})
    if registration is not None and not isinstance(registration, dict):
        errors["registration"] = "Registration controls must be an object"
    elif isinstance(registration, dict):
        closes_on = str(registration.get("closesOn", "")).strip()
        if closes_on:
            try:
                datetime.strptime(closes_on, "%Y-%m-%d")
            except ValueError:
                errors["registration.closesOn"] = "Registration close date must use YYYY-MM-DD format"

        sadhya_slot_start = str(registration.get("sadhyaSlotStartDate", "")).strip()
        sadhya_slot_end = str(registration.get("sadhyaSlotEndDate", "")).strip()
        for key, value in (("sadhyaSlotStartDate", sadhya_slot_start), ("sadhyaSlotEndDate", sadhya_slot_end)):
            if not value:
                continue
            try:
                datetime.strptime(value, "%Y-%m-%d")
            except ValueError:
                errors[f"registration.{key}"] = "Sadhya slot booking date must use YYYY-MM-DD format"
        if (
            sadhya_slot_start
            and sadhya_slot_end
            and "registration.sadhyaSlotStartDate" not in errors
            and "registration.sadhyaSlotEndDate" not in errors
            and sadhya_slot_end < sadhya_slot_start
        ):
            errors["registration.sadhyaSlotEndDate"] = "Sadhya slot booking end date must be on or after the start date"

        for key in ("sadhyaCapacity", "celebrityCapacity"):
            if key not in registration or registration[key] in (None, ""):
                continue
            try:
                if int(registration[key]) < 0:
                    raise ValueError
            except (TypeError, ValueError):
                errors[f"registration.{key}"] = "Capacity must be a whole number greater than or equal to 0"
        if "cancellationEnabled" in registration and not isinstance(registration["cancellationEnabled"], bool):
            errors["registration.cancellationEnabled"] = "Cancellation setting must be true or false"
        for key in ("onamRegistrationClosed", "celebrityRegistrationClosed"):
            if key in registration and not isinstance(registration[key], bool):
                errors[f"registration.{key}"] = "Registration switch must be true or false"

    return errors


def lambda_handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    http_method = event.get("httpMethod")

    if http_method == "OPTIONS":
        return respond(200, {})

    table = dynamodb.Table(TABLE_NAME)
    path_event_type = str((event.get("pathParameters") or {}).get("eventType", "")).strip()
    query_params = event.get("queryStringParameters") or {}
    host_id_filter = str(query_params.get("hostId", "")).strip()

    if http_method == "GET":
        # Single event when a path param is present; otherwise the full list.
        if path_event_type:
            try:
                item = table.get_item(Key={"eventType": path_event_type}).get("Item")
            except ClientError as exc:
                print(f"Event get error: {exc}")
                return respond(500, {"error": "Failed to load event"})
            if not item:
                return respond(404, {"error": "Event not found"})
            return respond(200, {"event": normalize_event(item)})

        try:
            events = list_events(table, host_id_filter)
        except Exception as exc:
            print(f"Event scan error: {exc}")
            return respond(500, {"error": "Failed to load events"})

        return respond(200, {"events": events, "count": len(events)})

    if http_method in {"POST", "PUT"}:
        payload = parse_json_body(event)

        if path_event_type and not str(payload.get("eventType", "")).strip():
            payload["eventType"] = path_event_type

        errors = validate_payload(payload)
        if errors:
            return respond(400, {"error": "Invalid event details", "fields": errors})

        event_type = str(payload["eventType"]).strip()
        now = now_iso()

        try:
            existing = table.get_item(Key={"eventType": event_type}).get("Item")
            record = {
                "eventType": event_type,
                "eventName": str(payload["eventName"]).strip(),
                "eventDate": str(payload.get("eventDate", "")).strip(),
                "hostId": str(payload.get("hostId", "")).strip(),
                "status": str(payload.get("status", "inactive")).strip().lower(),
                "description": str(payload.get("description", "")).strip(),
                "venue": str(payload.get("venue", "")).strip(),
                "venueAddress": str(payload.get("venueAddress", "")).strip(),
                "eventNote": str(payload.get("eventNote", "")).strip(),
                "capacity": int(payload.get("capacity", 0) or 0),
                "registrationPath": str(payload.get("registrationPath", "")).strip(),
                "registration": payload.get("registration", {}) if isinstance(payload.get("registration", {}), dict) else {},
                "eventInfo": normalize_event_info(payload.get("eventInfo", {})),
                "createdAt": existing.get("createdAt", now) if existing else now,
                "updatedAt": now,
            }
            table.put_item(Item=record)
        except ClientError as exc:
            print(f"Event write error: {exc}")
            return respond(500, {"error": "Failed to save event"})

        return respond(200, {"event": normalize_event(record), "created": not existing})

    if http_method == "DELETE":
        if not path_event_type:
            return respond(400, {"error": "Missing eventType"})

        try:
            response = table.delete_item(
                Key={"eventType": path_event_type},
                ReturnValues="ALL_OLD",
            )
        except ClientError as exc:
            print(f"Event delete error: {exc}")
            return respond(500, {"error": "Failed to delete event"})

        if "Attributes" not in response:
            return respond(404, {"error": "Event not found"})

        return respond(200, {"deleted": True, "eventType": path_event_type})

    return respond(405, {"error": "Method not allowed"})
