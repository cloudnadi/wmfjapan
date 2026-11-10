"""
Lambda: /admin/agms and /admin/agms/{agmId}   (Cognito-protected)

  GET  /admin/agms            -> every AGM (newest first) with registration counts
  POST /admin/agms            -> create an AGM (id generated from its date)
  GET  /admin/agms/{agmId}    -> one AGM plus all of its registrations and totals
  PUT  /admin/agms/{agmId}    -> update an AGM — only while it is still active
                                 (its date is today or later); past AGMs are a
                                 read-only record.

AGM definitions live in the agms table; registrations (kept for every AGM,
past and present) live in the separate agm_registrations table.
"""

import json
import os
import re
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import boto3
from boto3.dynamodb.conditions import Key
from botocore.exceptions import ClientError

import agm_logic

dynamodb = boto3.resource("dynamodb", region_name=os.environ["REGION"])
AGMS_TABLE = os.environ["AGMS_TABLE"]
AGM_TABLE = os.environ["AGM_TABLE"]
MEMBERS_TABLE = os.environ.get("MEMBERS_TABLE", "")
MEMBERSHIP_TABLE = os.environ.get("MEMBERSHIP_TABLE", "")

JST = timezone(timedelta(hours=9))
TEXT_FIELDS = {
    "title": 120, "venue": 200, "venueJa": 200, "access": 300, "mapUrl": 500,
    "remoteNote": 300, "refreshmentsNote": 300, "notes": 1000,
}


def _default(obj):
    if isinstance(obj, Decimal):
        return int(obj) if obj == obj.to_integral_value() else float(obj)
    raise TypeError(type(obj))


def respond(status, body):
    return {
        "statusCode": status,
        "headers": {
            "Access-Control-Allow-Origin": "*",
            "Access-Control-Allow-Headers": "Content-Type,Authorization",
            "Access-Control-Allow-Methods": "OPTIONS,GET,POST,PUT",
            "Content-Type": "application/json",
        },
        "body": json.dumps(body, default=_default),
    }


def today_jst():
    return datetime.now(JST).date().isoformat()


def is_active(agm):
    """An AGM can be edited until its date has passed (no date = still being planned)."""
    date = agm.get("date") or ""
    return not date or date >= today_jst()


def with_status(agm):
    return {**agm, "active": is_active(agm)}


def validate(raw, *, require_date):
    """Return (clean fields, error)."""
    clean = {"enabled": raw.get("enabled") is True}
    for key, limit in TEXT_FIELDS.items():
        clean[key] = str(raw.get(key, "")).strip()[:limit]
    clean["date"] = str(raw.get("date", "")).strip()
    for key in ("startTime", "endTime"):
        clean[key] = str(raw.get(key, "")).strip()
        if clean[key] and not re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", clean[key]):
            return None, "Times must use HH:MM format"
    if not clean["title"]:
        return None, "Give the AGM a title"
    if require_date and not clean["date"]:
        return None, "Choose the AGM date"
    if clean["date"]:
        try:
            datetime.strptime(clean["date"], "%Y-%m-%d")
        except ValueError:
            return None, "Date must use YYYY-MM-DD format"
    if clean["mapUrl"] and not clean["mapUrl"].startswith(("http://", "https://")):
        return None, "Map link must start with http:// or https://"
    return clean, ""


def scan_all(table):
    items, kwargs = [], {}
    while True:
        resp = table.scan(**kwargs)
        items.extend(resp.get("Items", []))
        if "LastEvaluatedKey" not in resp:
            return items
        kwargs["ExclusiveStartKey"] = resp["LastEvaluatedKey"]


def registrations_for(agm_id):
    table = dynamodb.Table(AGM_TABLE)
    items, kwargs = [], {"KeyConditionExpression": Key("agmId").eq(agm_id)}
    while True:
        resp = table.query(**kwargs)
        items.extend(resp.get("Items", []))
        if "LastEvaluatedKey" not in resp:
            break
        kwargs["ExclusiveStartKey"] = resp["LastEvaluatedKey"]
    items.sort(key=lambda i: i.get("registeredAt", ""))
    return items


def totals_for(items):
    groups = ("adults", *(key for key, _ in agm_logic.YOUNG_GROUPS))
    totals = {k: sum(int(i.get(k, 0) or 0) for i in items) for k in groups}
    attending = lambda i: int(i.get("totalAttendees", 0) or 0)  # noqa: E731
    totals.update(
        registrations=len(items),
        inPerson=sum(attending(i) for i in items if i.get("attendance") != "remote"),
        remote=sum(attending(i) for i in items if i.get("attendance") == "remote"),
        people=sum(attending(i) for i in items),
    )
    return totals


def live_adult_status(email, agm_date, cache):
    """Each adult's NPO status for the AGM date *now* — a pending payment may have
    been confirmed (or never paid) since they registered."""
    if email in cache:
        return cache[email]
    status = agm_logic.NOT_NK_MEMBER
    if MEMBERS_TABLE and MEMBERSHIP_TABLE:
        try:
            member = dynamodb.Table(MEMBERS_TABLE).get_item(Key={"email": email}).get("Item")
            memberid = str((member or {}).get("memberid", ""))
            txns = (
                dynamodb.Table(MEMBERSHIP_TABLE).query(KeyConditionExpression=Key("memberid").eq(memberid)).get("Items", [])
                if memberid else []
            )
            status = agm_logic.status_from(member, txns, agm_date)
        except ClientError:
            status = ""  # unknown: shown without a status rather than guessing
    cache[email] = status
    return status


def handle_list():
    agms = scan_all(dynamodb.Table(AGMS_TABLE))
    reg_table = dynamodb.Table(AGM_TABLE)
    result = []
    for agm in agms:
        regs = scan_registration_summary(reg_table, agm["agmId"])
        result.append({**with_status(agm), "registrationCount": regs["registrations"], "peopleCount": regs["people"]})
    result.sort(key=lambda a: (a.get("date") or "9999", a.get("createdAt", "")), reverse=True)
    return respond(200, {"agms": result})


def scan_registration_summary(reg_table, agm_id):
    resp = reg_table.query(KeyConditionExpression=Key("agmId").eq(agm_id))
    items = resp.get("Items", [])
    while "LastEvaluatedKey" in resp:
        resp = reg_table.query(KeyConditionExpression=Key("agmId").eq(agm_id), ExclusiveStartKey=resp["LastEvaluatedKey"])
        items.extend(resp.get("Items", []))
    return totals_for(items)


def handle_get(agm_id):
    agm = dynamodb.Table(AGMS_TABLE).get_item(Key={"agmId": agm_id}).get("Item")
    if not agm:
        return respond(404, {"error": "AGM not found"})
    regs = registrations_for(agm_id)
    cache = {}
    for reg in regs:
        for adult in reg.get("adultAttendees") or []:
            adult["registeredStatus"] = adult.get("status", "")
            adult["status"] = live_adult_status(adult.get("email", ""), agm.get("date", ""), cache)
    needs_attention = sum(
        1 for reg in regs for a in reg.get("adultAttendees") or [] if a.get("status") not in agm_logic.ALLOWED
        and a.get("status") != ""
    )
    return respond(200, {"agm": with_status(agm), "registrations": regs, "totals": totals_for(regs),
                         "needsAttention": needs_attention})


def handle_create(body):
    clean, error = validate(body, require_date=True)
    if error:
        return respond(400, {"error": error})
    if clean["date"] < today_jst():
        return respond(400, {"error": "A new AGM can't be dated in the past"})
    table = dynamodb.Table(AGMS_TABLE)
    base = f"agm-{clean['date'][:4]}"
    now = datetime.now(timezone.utc).isoformat()
    for n in range(1, 20):
        agm_id = base if n == 1 else f"{base}-{n}"
        try:
            table.put_item(
                Item={"agmId": agm_id, **clean, "createdAt": now, "updatedAt": now},
                ConditionExpression="attribute_not_exists(agmId)",
            )
            return respond(200, {"agm": with_status({"agmId": agm_id, **clean, "createdAt": now, "updatedAt": now})})
        except ClientError as exc:
            if exc.response.get("Error", {}).get("Code") != "ConditionalCheckFailedException":
                return respond(500, {"error": "Could not save the AGM"})
    return respond(409, {"error": "Too many AGMs for that year"})


def handle_update(agm_id, body):
    table = dynamodb.Table(AGMS_TABLE)
    existing = table.get_item(Key={"agmId": agm_id}).get("Item")
    if not existing:
        return respond(404, {"error": "AGM not found"})
    if not is_active(existing):
        return respond(409, {"error": "This AGM is over, so it can no longer be edited"})
    clean, error = validate(body, require_date=True)
    if error:
        return respond(400, {"error": error})
    if clean["date"] < today_jst():
        return respond(400, {"error": "The AGM date can't be moved into the past"})
    item = {**existing, **clean, "updatedAt": datetime.now(timezone.utc).isoformat()}
    table.put_item(Item=item)
    return respond(200, {"agm": with_status(item)})


def lambda_handler(event, _context):
    method = (event.get("httpMethod") or "GET").upper()
    if method == "OPTIONS":
        return respond(200, {})
    agm_id = ((event.get("pathParameters") or {}).get("agmId") or "").strip()
    try:
        body = json.loads(event.get("body") or "{}")
    except (TypeError, ValueError):
        return respond(400, {"error": "Invalid JSON body"})
    if not isinstance(body, dict):
        body = {}

    if not agm_id:
        if method == "GET":
            return handle_list()
        if method == "POST":
            return handle_create(body)
    else:
        if method == "GET":
            return handle_get(agm_id)
        if method == "PUT":
            return handle_update(agm_id, body)
    return respond(405, {"error": "Method not allowed"})
