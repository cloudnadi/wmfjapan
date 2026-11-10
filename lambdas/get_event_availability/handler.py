"""
Lambda: GET /events/{eventType}/availability  (public — no auth)

One read-only summary for external consumers (e.g. the Kaira AI assistant)
of what is still bookable for the Celebrity Event:

  * celebrityEvent  — registration status + remaining seats
  * dinner          — Celebrity Dinner availability (remaining guest places)
  * seatSelection   — whether the seat map is open + how many seats are free

Aggregate counts only: no names, emails, registration ids or seat owners.
Each section is computed independently, so a failure reading one (reported
as status "unavailable") never hides the others.
"""

import json
import logging
import os
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

import boto3
from botocore.exceptions import ClientError

import celebrity_dinner_logic as dinner
import seat_selection_logic

logger = logging.getLogger()
logger.setLevel(logging.INFO)

dynamodb = boto3.resource("dynamodb", region_name=os.environ.get("REGION"))
REGISTRATIONS_TABLE = os.environ.get("DYNAMODB_TABLE", "")
EVENTS_TABLE = os.environ.get("EVENTS_TABLE", "")

CELEBRITY_OPTIONS = ("event", "combo")
ATTENDEE_KEYS = ("adults", "kids914", "kids68")


def decimal_to_python(obj):
    if isinstance(obj, Decimal):
        return int(obj) if obj == obj.to_integral_value() else float(obj)
    raise TypeError(f"Object of type {type(obj)} is not JSON serializable")


def respond(status: int, body: dict):
    return {
        "statusCode": status,
        "headers": {
            "Access-Control-Allow-Origin": "*",
            "Access-Control-Allow-Headers": "Content-Type,Authorization",
            "Access-Control-Allow-Methods": "OPTIONS,GET",
            "Content-Type": "application/json",
            "Cache-Control": "public, max-age=30",
        },
        "body": json.dumps(body, default=decimal_to_python),
    }


def get_event(event_type: str) -> dict | None:
    if not EVENTS_TABLE:
        return None
    return dynamodb.Table(EVENTS_TABLE).get_item(Key={"eventType": event_type}).get("Item")


def parse_event_info(event_item: dict) -> dict:
    info = event_item.get("eventInfo", {})
    if isinstance(info, str):
        try:
            info = json.loads(info) if info.strip() else {}
        except json.JSONDecodeError:
            info = {}
    return info if isinstance(info, dict) else {}


def count_celebrity_attendees(event_type: str) -> int:
    """Seated attendees across event + combo registrations — the same figure
    the registration capacity check uses (see get_site_config)."""
    table = dynamodb.Table(REGISTRATIONS_TABLE)
    kwargs = {
        "FilterExpression": "eventType = :et AND registrationOption IN (:o0, :o1)",
        "ExpressionAttributeValues": {
            ":et": event_type, ":o0": CELEBRITY_OPTIONS[0], ":o1": CELEBRITY_OPTIONS[1],
        },
    }
    total = 0
    while True:
        response = table.scan(**kwargs)
        for item in response.get("Items", []):
            counts = item.get("attendeeCounts", {}) or {}
            total += sum(int(counts.get(key, 0) or 0) for key in ATTENDEE_KEYS)
        if "LastEvaluatedKey" not in response:
            return total
        kwargs["ExclusiveStartKey"] = response["LastEvaluatedKey"]


def remaining(capacity: int, used: int):
    """None means unlimited (capacity 0 = no cap, same as the admin forms)."""
    return max(capacity - used, 0) if capacity > 0 else None


def celebrity_event_section(event_type: str, registration: dict) -> dict:
    capacity = int(registration.get("celebrityCapacity", 0) or 0)
    closes_on = str(registration.get("closesOn", "") or "").strip()
    manually_closed = registration.get("celebrityRegistrationClosed") is True
    registered = count_celebrity_attendees(event_type)
    left = remaining(capacity, registered)

    deadline_passed = False
    if closes_on:
        try:
            deadline_passed = (
                datetime.now(timezone.utc).date() > datetime.strptime(closes_on, "%Y-%m-%d").date()
            )
        except ValueError:
            pass

    if manually_closed or deadline_passed:
        status = "closed"
    elif left == 0:
        status = "full"
    else:
        status = "open"

    return {
        "status": status,  # open | full | closed
        "registrationOpen": status == "open",
        "closesOn": closes_on,
        "capacity": capacity,
        "registered": registered,
        "remainingSeats": left,
    }


def dinner_section(event_type: str, event_info: dict) -> dict:
    cfg = (event_info.get("celebrityDinner") or {}) if isinstance(event_info.get("celebrityDinner"), dict) else {}
    if cfg.get("enabled") is not True:
        return {"status": "not_offered", "registrationOpen": False}

    config = dinner.normalize_config(dinner.get_config(event_type), event_type, enabled=True)
    window = dinner.registration_window_status(config)  # not_open | open | closed
    cap = config["maxTotalGuests"]
    booked = dinner.total_active_dinner_guest_count(event_type)
    left = remaining(cap, booked)

    if window != "open":
        status = window
    elif left == 0:
        status = "full"
    else:
        status = "open"

    return {
        "status": status,  # open | full | not_open | closed | not_offered
        "registrationOpen": status == "open",
        "registrationStartsAt": config["registrationStartsAt"],
        "registrationEndsAt": config["registrationEndsAt"],
        "dinnerDate": config["dinnerDate"],
        "venue": config["venue"],
        "currency": config["currency"],
        "pricePerAdult": config["dinnerPriceAdults"],
        "pricePerKids914": config["dinnerPriceKids914"],
        "pricePerKids68": config["dinnerPriceKids68"],
        "maxTotalGuests": cap,
        "guestsBooked": booked,
        "remainingSeats": left,
    }


def seat_selection_section(event_type: str, event_info: dict) -> dict:
    config = seat_selection_logic.parse_seat_selection_config(event_info)
    if not config["enabled"]:
        return {"status": "not_offered", "selectionOpen": False}

    window = seat_selection_logic.seat_selection_window_status(config)  # not_started | open | ended
    overrides = seat_selection_logic.get_category_overrides(event_type)
    occupied = seat_selection_logic.get_occupied_seats(event_type)
    layout = seat_selection_logic.generate_seat_layout(overrides)

    zone_total = seat_selection_logic.get_zone_totals(overrides)
    zone_free = dict(zone_total)
    for row in layout:
        for seat in row["seats"]:
            if seat["seatId"] in occupied:
                zone_free[seat["category"]] -= 1
    free = sum(zone_free.values())

    if window != "open":
        status = "not_open" if window == "not_started" else "closed"
    elif free == 0:
        status = "sold_out"
    else:
        status = "open"

    return {
        "status": status,  # open | sold_out | not_open | closed | not_offered
        "selectionOpen": status == "open",
        "startDate": config["startDate"],
        "endDate": config["endDate"],
        "totalSeats": sum(zone_total.values()),
        "availableSeats": free,
        "unavailableSeats": sum(zone_total.values()) - free,
        "zones": {
            zone: {"total": zone_total[zone], "available": zone_free[zone]}
            for zone in zone_total
        },
        "pricing": seat_selection_logic.get_zone_pricing(event_type) if config["pricingEnabled"] else None,
    }


def safe(name: str, fn, *args) -> dict:
    try:
        return fn(*args)
    except (ClientError, RuntimeError):
        logger.exception("availability: %s section failed", name)
        return {"status": "unavailable"}


def lambda_handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    method = event.get("httpMethod")
    if method == "OPTIONS":
        return respond(200, {})
    if method != "GET":
        return respond(405, {"error": "Only GET is allowed"})

    event_type = str((event.get("pathParameters") or {}).get("eventType", "")).strip()
    if not event_type:
        return respond(400, {"error": "eventType path parameter is required"})

    try:
        event_item = get_event(event_type)
    except ClientError:
        logger.exception("availability: could not read event %s", event_type)
        return respond(500, {"error": "Failed to load event"})
    if not event_item:
        return respond(404, {"error": "Event not found"})

    event_info = parse_event_info(event_item)
    registration = event_item.get("registration", {})
    registration = registration if isinstance(registration, dict) else {}

    return respond(200, {
        "eventType": event_type,
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "celebrityEvent": safe("celebrityEvent", celebrity_event_section, event_type, registration),
        "dinner": safe("dinner", dinner_section, event_type, event_info),
        "seatSelection": safe("seatSelection", seat_selection_section, event_type, event_info),
    })
