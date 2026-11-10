"""
Lambda: GET /admin/registrations
Protected by Cognito JWT authorizer.
Supports: pagination, search by email, filter by status/reg_type.
"""

import json
import os
from decimal import Decimal
from datetime import datetime
from typing import Any, Optional

import boto3
from boto3.dynamodb.conditions import Attr, ConditionBase

dynamodb = boto3.resource("dynamodb", region_name=os.environ["REGION"])
TABLE_NAME = os.environ["DYNAMODB_TABLE"]
EVENTS_TABLE = os.environ.get("EVENTS_TABLE", "")


def decimal_to_python(obj):
    """DynamoDB returns Decimals — convert for JSON serialisation."""
    if isinstance(obj, Decimal):
        return int(obj) if obj == obj.to_integral_value() else float(obj)
    raise TypeError(f"Object of type {type(obj)} is not JSON serializable")


def cors_headers():
    return {
        "Access-Control-Allow-Origin":  "*",
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


def normalize_event_info(event_record: dict[str, Any]) -> dict[str, Any]:
    event_info = event_record.get("eventInfo", {})

    if isinstance(event_info, str):
        event_info = event_info.strip()
        if not event_info:
            return {}
        try:
            parsed = json.loads(event_info)
            return parsed if isinstance(parsed, dict) else {}
        except json.JSONDecodeError:
            return {}

    return event_info if isinstance(event_info, dict) else {}


def normalize_event_date(raw_event_date: Any) -> str:
    if raw_event_date is None:
        return ""

    value = str(raw_event_date).strip()
    if not value:
        return ""

    for fmt in (
        "%Y-%m-%d",
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%dT%H:%M:%S.%f",
        "%Y-%m-%dT%H:%M:%S%z",
        "%Y-%m-%dT%H:%M:%S.%f%z",
        "%B %d, %Y",
        "%b %d, %Y",
        "%d %B %Y",
        "%d %b %Y",
        "%Y/%m/%d",
    ):
        try:
            return datetime.strptime(value, fmt).date().isoformat()
        except ValueError:
            continue

    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).date().isoformat()
    except ValueError:
        return value


def resolve_event_date(event_record: dict[str, Any]) -> str:
    event_info = normalize_event_info(event_record)
    raw_event_date = (
        event_record.get("eventDate")
        or event_info.get("date")
        or event_record.get("date")
    )
    return normalize_event_date(raw_event_date)


def load_event_dates() -> dict[str, str]:
    if not EVENTS_TABLE:
        return {}

    table = dynamodb.Table(EVENTS_TABLE)
    scan_kwargs: dict[str, Any] = {}
    event_dates: dict[str, str] = {}

    while True:
        response = table.scan(**scan_kwargs)
        for item in response.get("Items", []):
            event_type = item.get("eventType")
            if event_type:
                event_dates[event_type] = resolve_event_date(item)

        if "LastEvaluatedKey" not in response:
            break
        scan_kwargs["ExclusiveStartKey"] = response["LastEvaluatedKey"]

    return event_dates


def lambda_handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    if event.get("httpMethod") == "OPTIONS":
        return respond(200, {})

    params = event.get("queryStringParameters") or {}
    limit        = min(int(params.get("limit", "50")), 200)
    search_email = params.get("email", "").strip().lower()
    filter_type  = params.get("type", "").strip()    # "event" | "sadhya"
    filter_status = params.get("status", "").strip() # "pending_payment" | "paid" | "confirmed"
    last_key_raw  = params.get("lastEvaluatedKey", "")

    table = dynamodb.Table(TABLE_NAME)

    # Build filter expression
    filter_exp: Optional[ConditionBase] = None

    if search_email:
        filter_exp = Attr("email").contains(search_email)

    if filter_type in ("event", "sadhya"):
        cond = Attr("registrationType").eq(filter_type)
        filter_exp = cond if filter_exp is None else filter_exp & cond

    if filter_status:
        cond = Attr("status").eq(filter_status)
        filter_exp = cond if filter_exp is None else filter_exp & cond

    scan_kwargs: dict[str, Any] = {"Limit": limit}
    if filter_exp is not None:
        scan_kwargs["FilterExpression"] = filter_exp

    # Resume pagination
    if last_key_raw:
        try:
            scan_kwargs["ExclusiveStartKey"] = json.loads(last_key_raw)
        except (json.JSONDecodeError, ValueError):
            return respond(400, {"error": "Invalid lastEvaluatedKey"})

    try:
        response = table.scan(**scan_kwargs)
    except Exception as e:
        print(f"DynamoDB scan error: {e}")
        return respond(500, {"error": "Failed to fetch registrations"})

    items = response.get("Items", [])

    try:
        event_dates_by_type = load_event_dates()
    except Exception as e:
        print(f"Event metadata lookup error: {e}")
        event_dates_by_type = {}

    for item in items:
        if item.get("eventDate"):
            item["eventDate"] = normalize_event_date(item.get("eventDate"))
            continue

        event_type = item.get("eventType", "")
        if event_type in event_dates_by_type:
            item["eventDate"] = event_dates_by_type[event_type]

    # Sort by submittedAt descending (most recent first)
    items.sort(key=lambda x: x.get("submittedAt", ""), reverse=True)

    # Summary stats
    total_adults   = sum(int(i.get("attendeeCounts", {}).get("adult", 0))   for i in items)
    total_teens    = sum(int(i.get("attendeeCounts", {}).get("teen", 0))    for i in items)
    total_children = sum(int(i.get("attendeeCounts", {}).get("child", 0))   for i in items)
    total_toddlers = sum(int(i.get("attendeeCounts", {}).get("toddler", 0)) for i in items)

    result = {
        "registrations": items,
        "count":         len(items),
        "stats": {
            "totalRegistrations": len(items),
            "totalAttendees": {
                "adult":   total_adults,
                "teen":    total_teens,
                "child":   total_children,
                "toddler": total_toddlers,
                "total":   total_adults + total_teens + total_children + total_toddlers,
            }
        },
    }

    # Pagination token
    if "LastEvaluatedKey" in response:
        result["lastEvaluatedKey"] = json.dumps(response["LastEvaluatedKey"])

    return respond(200, result)
