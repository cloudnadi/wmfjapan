"""
Lambda: POST /visits
Records SPA page views for the site in DynamoDB.
"""

import json
import os
import uuid
from datetime import datetime, timezone

import boto3
from botocore.exceptions import ClientError

VISITS_TABLE = os.environ["VISITS_TABLE"]
REGION = os.environ["REGION"]

dynamodb = boto3.resource("dynamodb", region_name=REGION)


def cors_headers():
    return {
        "Access-Control-Allow-Origin": "*",
        "Access-Control-Allow-Headers": "Content-Type,Authorization",
        "Access-Control-Allow-Methods": "OPTIONS,POST",
        "Content-Type": "application/json",
    }


def respond(status_code, body):
    return {
        "statusCode": status_code,
        "headers": cors_headers(),
        "body": json.dumps(body, default=str),
    }


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def lambda_handler(event, _context):
    method = (event.get("httpMethod") or "POST").upper()
    if method == "OPTIONS":
        return respond(200, {"ok": True})
    if method != "POST":
        return respond(405, {"error": "Method not allowed"})

    try:
        body = json.loads(event.get("body") or "{}")
    except (ValueError, TypeError):
        body = {}

    visit_id = str(uuid.uuid4())
    visited_at = now_utc().isoformat()
    visit_date = visited_at[:10]
    visited_at_key = f"{visited_at}#{visit_id}"

    screen = body.get("viewport") or {}
    item = {
        "visitDate": visit_date,
        "visitedAtKey": visited_at_key,
        "visitId": visit_id,
        "visitedAt": visited_at,
        "path": (body.get("path") or "/").strip() or "/",
        "referrer": (body.get("referrer") or "").strip(),
        "title": (body.get("title") or "").strip(),
        "visitorId": (body.get("visitorId") or "").strip(),
        "visitorType": (body.get("visitorType") or "anonymous").strip(),
        "principalEmail": (body.get("principalEmail") or "").strip(),
        "userAgent": (body.get("userAgent") or "").strip(),
        "screenWidth": screen.get("width"),
        "screenHeight": screen.get("height"),
    }

    try:
        dynamodb.Table(VISITS_TABLE).put_item(Item=item)
    except ClientError as exc:
        return respond(500, {"error": f"Could not save visit log: {exc}"})

    return respond(202, {"ok": True, "visitId": visit_id, "visitedAt": visited_at})
