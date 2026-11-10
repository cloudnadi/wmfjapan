"""
Lambda: GET /admin/export
Protected by Cognito JWT authorizer.
Exports all registrations as a CSV file download.
"""

import csv
import io
import json
import os
from datetime import datetime, timezone
from decimal import Decimal

import boto3

dynamodb   = boto3.resource("dynamodb", region_name=os.environ["REGION"])
TABLE_NAME = os.environ["DYNAMODB_TABLE"]
MEMBERS_TABLE = os.environ.get("MEMBERS_TABLE", "")
EVENTS_TABLE = os.environ.get("EVENTS_TABLE", "")


def cors_headers():
    return {
        "Access-Control-Allow-Origin":  "*",
        "Access-Control-Allow-Headers": "Content-Type,Authorization",
        "Access-Control-Allow-Methods": "OPTIONS,GET",
    }


def decimal_to_int(v):
    if isinstance(v, Decimal):
        return int(v)
    return v


def scan_all(table) -> list:
    items = []
    scan_kwargs = {}
    while True:
        response = table.scan(**scan_kwargs)
        items.extend(response.get("Items", []))
        if "LastEvaluatedKey" not in response:
            return items
        scan_kwargs["ExclusiveStartKey"] = response["LastEvaluatedKey"]


def load_member_names() -> dict:
    """email → full name, from the members master list."""
    if not MEMBERS_TABLE:
        return {}
    try:
        members = scan_all(dynamodb.Table(MEMBERS_TABLE))
    except Exception as e:
        print(f"Members scan error: {e}")
        return {}

    names = {}
    for member in members:
        email = str(member.get("email", "")).strip().lower()
        full_name = str(member.get("fullname", "") or member.get("memberName", "")).strip()
        if email and full_name:
            names[email] = full_name
    return names


def split_name(full_name: str) -> tuple:
    parts = str(full_name or "").split()
    if not parts:
        return "", ""
    return parts[0], " ".join(parts[1:])


def load_events() -> dict:
    """eventType → {eventName, eventDate}, from the events table."""
    if not EVENTS_TABLE:
        return {}
    try:
        events = scan_all(dynamodb.Table(EVENTS_TABLE))
    except Exception as e:
        print(f"Events scan error: {e}")
        return {}

    return {
        str(evt.get("eventType", "")).strip(): {
            "eventName": str(evt.get("eventName", "")).strip(),
            "eventDate": str(evt.get("eventDate", "")).strip(),
        }
        for evt in events
        if str(evt.get("eventType", "")).strip()
    }


CSV_COLUMNS = [
    "registrationId",
    "firstName",
    "lastName",
    "email",
    "phone",
    "registrationOption",
    "adults",
    "kids914",
    "kids68",
    "kidsUnder6",
    "totalAttendees",
    "paidAmount",
    "balanceDue",
    "ticketsAmount",
    "discountApplied",
    "totalAmount",
    "paymentMethod",
    "paymentStatus",
    "submittedAt",
]


def flatten_record(item: dict, member_names: dict, events: dict) -> dict:
    # Standard registrations key counts as adult/kids914/kids68/kidsUnder6; Onam
    # registrations use adults/kids914/kids68/kidsUnder6 — support both.
    counts = item.get("attendeeCounts", {})
    adults   = decimal_to_int(counts.get("adults",   counts.get("adults",     0)))
    kids914    = decimal_to_int(counts.get("kids914",    counts.get("kids914",    0)))
    kids68   = decimal_to_int(counts.get("kids68",   counts.get("kids68",     0)))
    kidsUnder6 = decimal_to_int(counts.get("kidsUnder6", counts.get("kidsUnder6", 0)))
    total_attendees = adults + kids914 + kids68 + kidsUnder6

    # Onam records store a single `name`; the members list is the fallback for
    # records that carry neither first/last nor a name.
    email = str(item.get("email", "")).strip().lower()
    first_name = str(item.get("firstName", "")).strip()
    last_name = str(item.get("lastName", "")).strip()
    if not first_name and not last_name:
        first_name, last_name = split_name(
            str(item.get("name", "")).strip() or member_names.get(email, "")
        )

    return {
        "registrationId":       item.get("registrationId", ""),
        "firstName":            first_name,
        "lastName":             last_name,
        "email":                item.get("email", ""),
        "phone":                item.get("phone", ""),
        "registrationOption":   item.get("registrationOption", ""),
        "adults":               adults,
        "kids914":              kids914,
        "kids68":               kids68,
        "kidsUnder6":           kidsUnder6,
        "totalAttendees":       total_attendees,
        "paidAmount":           decimal_to_int(item.get("paidAmount", 0)),
        "balanceDue":           decimal_to_int(item.get("balanceDue", 0)),
        "ticketsAmount":        sum([decimal_to_int(item.get("ticketsAmount", 0)), decimal_to_int(item.get("discountApplied", 0))]),
        "discountApplied":      decimal_to_int(item.get("discountApplied", 0)),
        "totalAmount":          decimal_to_int(item.get("totalAmount", 0)),
        "paymentMethod":        item.get("paymentMethod", ""),
        "paymentStatus":        item.get("paymentStatus", ""),
        "submittedAt":          item.get("submittedAt", ""),
    }


def lambda_handler(event, context):
    if event.get("httpMethod") == "OPTIONS":
        return {
            "statusCode": 200,
            "headers": cors_headers(),
            "body": "",
        }

    # Scan all registrations (paginate internally)
    table = dynamodb.Table(TABLE_NAME)
    items = []
    scan_kwargs = {}

    while True:
        try:
            response = table.scan(**scan_kwargs)
        except Exception as e:
            print(f"DynamoDB scan error: {e}")
            return {
                "statusCode": 500,
                "headers": cors_headers(),
                "body": json.dumps({"error": "Failed to fetch registrations"}),
            }

        items.extend(response.get("Items", []))

        if "LastEvaluatedKey" not in response:
            break
        scan_kwargs["ExclusiveStartKey"] = response["LastEvaluatedKey"]

    # Sort by submittedAt
    items.sort(key=lambda x: x.get("submittedAt", ""))

    member_names = load_member_names()
    events = load_events()

    # Build CSV in memory
    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=CSV_COLUMNS, extrasaction="ignore")
    writer.writeheader()

    for item in items:
        writer.writerow(flatten_record(item, member_names, events))

    csv_content = output.getvalue()
    output.close()

    timestamp    = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    filename     = f"registrations_{timestamp}.csv"

    # The export spans every event, so the header lists the events present
    # rather than naming a single one.
    exported_events = sorted({
        events.get(str(i.get("eventType", "")).strip(), {}).get("eventName", "")
        or str(i.get("eventType", "")).strip()
        for i in items
    } - {""})

    summary = (
        f"# Registration Export\n"
        f"# Events: {', '.join(exported_events) if exported_events else 'N/A'}\n"
        f"# Exported: {datetime.now(timezone.utc).isoformat()}\n"
        f"# Total records: {len(items)}\n"
        f"# Total revenue: ¥{sum(decimal_to_int(i.get('totalAmount', 0)) for i in items):,}\n\n"
    )

    final_csv = summary + csv_content

    return {
        "statusCode": 200,
        "headers": {
            **cors_headers(),
            "Content-Type":        "text/csv; charset=utf-8",
            "Content-Disposition": f'attachment; filename="{filename}"',
        },
        "body":            final_csv,
        "isBase64Encoded": False,
    }
