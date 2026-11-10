"""
Lambda: GET /site-config  (public — no auth)
Returns current carousel image URLs from DynamoDB.
Response is cacheable for 60 seconds at the client/CDN level.
"""

import json
import os
from datetime import datetime, timedelta, timezone

import boto3
from botocore.exceptions import ClientError

dynamodb = boto3.resource("dynamodb", region_name=os.environ["REGION"])
TABLE_NAME = os.environ["SITE_CONFIG_TABLE"]
REGISTRATIONS_TABLE = os.environ.get("DYNAMODB_TABLE", "")
EVENTS_TABLE = os.environ.get("EVENTS_TABLE", "")
ONAM_EVENT_TYPE = os.environ.get("EVENT_TYPE_ONAM", "onam_2026")
ONAM_CAPACITY = int(os.environ.get("ONAM_CAPACITY", "350"))
AGMS_TABLE = os.environ.get("AGMS_TABLE", "")
AGM_PUBLIC_FIELDS = (
    "agmId", "title", "date", "startTime", "endTime", "venue", "venueJa",
    "access", "mapUrl", "remoteNote", "refreshmentsNote", "notes",
)

VALID_SLOTS = ("onam", "anniversary")
SETTINGS_WITH_DEFAULTS = {
    "partners_display_mode": "flat",
}


def get_capacity_attendee_total(event_type: str, options: tuple[str, ...]) -> int:
    """Sum seated attendees for one event's registration capacity pool."""
    if not REGISTRATIONS_TABLE:
        return 0

    table = dynamodb.Table(REGISTRATIONS_TABLE)
    option_values = {f":o{index}": option for index, option in enumerate(options)}
    scan_kwargs = {
        "FilterExpression": "eventType = :et AND registrationOption IN (" + ", ".join(option_values) + ")",
        "ExpressionAttributeValues": {
            ":et": event_type, **option_values,
        },
    }
    total = 0
    while True:
        try:
            response = table.scan(**scan_kwargs)
        except ClientError:
            break
        for item in response.get("Items", []):
            counts = item.get("attendeeCounts", {}) or {}
            total += sum(
                int(counts.get(key, 0) or 0)
                for key in ("adults", "kids914", "kids68")
            )
        if "LastEvaluatedKey" not in response:
            break
        scan_kwargs["ExclusiveStartKey"] = response["LastEvaluatedKey"]
    return total


def get_event_registration_config(event_type: str) -> dict:
    if not EVENTS_TABLE or not event_type:
        return {}
    try:
        item = dynamodb.Table(EVENTS_TABLE).get_item(
            Key={"eventType": event_type}
        ).get("Item")
        registration = (item or {}).get("registration", {})
        return registration if isinstance(registration, dict) else {}
    except ClientError:
        return {}


def get_open_agm() -> dict | None:
    """Public details of the soonest upcoming AGM that has registration open (Japan time)."""
    if not AGMS_TABLE:
        return None
    try:
        table = dynamodb.Table(AGMS_TABLE)
        items, kwargs = [], {}
        while True:
            resp = table.scan(**kwargs)
            items.extend(resp.get("Items", []))
            if "LastEvaluatedKey" not in resp:
                break
            kwargs["ExclusiveStartKey"] = resp["LastEvaluatedKey"]
    except ClientError:
        return None
    today = datetime.now(timezone(timedelta(hours=9))).date().isoformat()
    open_now = [a for a in items if a.get("enabled") is True and (a.get("date") or "9999-12-31") >= today]
    if not open_now:
        return None
    open_now.sort(key=lambda a: a.get("date") or "9999-12-31")
    return {key: open_now[0].get(key, "") for key in AGM_PUBLIC_FIELDS}


def lambda_handler(event, _context):
    table = dynamodb.Table(TABLE_NAME)
    config = {}

    for slot in VALID_SLOTS:
        try:
            resp = table.get_item(Key={"configKey": f"carousel_{slot}"})
            item = resp.get("Item")
            if item and item.get("imageUrl"):
                config[slot] = {
                    "imageUrl": item["imageUrl"],
                    "updatedAt": item.get("updatedAt"),
                }
        except ClientError:
            pass

    for key, default in SETTINGS_WITH_DEFAULTS.items():
        try:
            resp = table.get_item(Key={"configKey": key})
            item = resp.get("Item")
            config[key] = item.get("value", default) if item else default
        except ClientError:
            config[key] = default

    event_type = str((event.get("queryStringParameters") or {}).get("eventType", "")).strip()
    registration = get_event_registration_config(event_type)
    if event_type:
        sadhya_capacity = registration.get("sadhyaCapacity")
        celebrity_capacity = registration.get("celebrityCapacity")
        if sadhya_capacity is None and event_type == ONAM_EVENT_TYPE:
            try:
                legacy_capacity = table.get_item(Key={"configKey": "onam_capacity"}).get("Item")
                sadhya_capacity = legacy_capacity.get("value", ONAM_CAPACITY) if legacy_capacity else ONAM_CAPACITY
            except ClientError:
                sadhya_capacity = ONAM_CAPACITY
        config["registration"] = {
            "closesOn": registration.get("closesOn", ""),
            "cancellationEnabled": registration.get("cancellationEnabled") is not False,
            "sadhyaCapacity": int(sadhya_capacity or 0),
            "sadhyaAttendeeTotal": get_capacity_attendee_total(event_type, ("onam", "combo")),
            "celebrityCapacity": int(celebrity_capacity or 0),
            "celebrityAttendeeTotal": get_capacity_attendee_total(event_type, ("event", "combo")),
            # Manual per-section switches, independent of the seat capacities.
            "onamRegistrationClosed": registration.get("onamRegistrationClosed") is True,
            "celebrityRegistrationClosed": registration.get("celebrityRegistrationClosed") is True,
        }

    try:
        capacity_item = table.get_item(Key={"configKey": "onam_capacity"}).get("Item")
        expiry_item = table.get_item(Key={"configKey": "onam_registration_expiry"}).get("Item")
        config["onamCapacity"] = int(capacity_item.get("value", ONAM_CAPACITY)) if capacity_item else ONAM_CAPACITY
        config["onamRegistrationExpiry"] = expiry_item.get("value", "") if expiry_item else ""
    except (ClientError, TypeError, ValueError):
        config["onamCapacity"] = ONAM_CAPACITY
        config["onamRegistrationExpiry"] = ""
    config["onamAttendeeTotal"] = get_capacity_attendee_total(ONAM_EVENT_TYPE, ("onam", "combo"))

    agm = get_open_agm()
    if agm:
        config["agm"] = agm

    return {
        "statusCode": 200,
        "headers": {
            "Access-Control-Allow-Origin": "*",
            "Content-Type": "application/json",
            "Cache-Control": "public, max-age=60",  # 1-minute browser/CDN cache
        },
        "body": json.dumps({"config": config}),
    }
