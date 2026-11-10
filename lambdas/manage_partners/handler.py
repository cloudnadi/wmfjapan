"""
Lambda: /admin/partners (Cognito-protected) and /partners (public, read-only).

The admin routes support listing, creating, updating, and deleting event
partners. The public GET /partners route is unauthenticated and returns only
active partners so they can be showcased on the public site.
"""

import json
import os
import time
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any
from uuid import uuid4

import boto3
from botocore.exceptions import ClientError

dynamodb = boto3.resource("dynamodb", region_name=os.environ["REGION"])
TABLE_NAME = os.environ["PARTNERS_TABLE"]

# Seconds the browser/CDN may cache the public list, and how long a warm Lambda
# container reuses its in-memory copy before re-scanning DynamoDB. Both are
# short so an admin edit propagates within ~a minute.
PUBLIC_CACHE_TTL = int(os.environ.get("PUBLIC_PARTNERS_CACHE_TTL", "60"))

# Module-scope cache, shared across invocations on a warm container.
_public_cache: dict[str, Any] = {"partners": None, "expires_at": 0.0}


def decimal_to_python(obj):
    if isinstance(obj, Decimal):
        return int(obj) if obj == obj.to_integral_value() else float(obj)
    raise TypeError(f"Object of type {type(obj)} is not JSON serializable")


def cors_headers():
    return {
        "Access-Control-Allow-Origin": "*",
        "Access-Control-Allow-Headers": "Content-Type,Authorization",
        "Access-Control-Allow-Methods": "OPTIONS,GET,POST,DELETE",
        "Content-Type": "application/json",
    }


def respond(status: int, body: dict, extra_headers: dict | None = None):
    headers = cors_headers()
    if extra_headers:
        headers.update(extra_headers)
    return {
        "statusCode": status,
        "headers": headers,
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


def normalize_partner(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "partnerId": item.get("partnerId", ""),
        "name": item.get("name", ""),
        "websiteUrl": item.get("websiteUrl", ""),
        "logoUrl": item.get("logoUrl", ""),
        "description": item.get("description", ""),
        "scope": item.get("scope", "all"),
        "displayOrder": int(item.get("displayOrder", 0) or 0),
        "isActive": bool(item.get("isActive", True)),
        "tier": item.get("tier", "bronze"),
        "createdAt": item.get("createdAt", ""),
        "updatedAt": item.get("updatedAt", ""),
    }


def list_partners(table):
    items = []
    scan_kwargs: dict[str, Any] = {}

    while True:
        response = table.scan(**scan_kwargs)
        items.extend(response.get("Items", []))
        if "LastEvaluatedKey" not in response:
            break
        scan_kwargs["ExclusiveStartKey"] = response["LastEvaluatedKey"]

    items.sort(key=lambda item: (int(item.get("displayOrder", 0) or 0), str(item.get("name", "")).lower()))
    return [normalize_partner(item) for item in items]


def validate_payload(payload: dict[str, Any]) -> dict[str, str]:
    errors: dict[str, str] = {}

    if not str(payload.get("name", "")).strip():
        errors["name"] = "Partner name is required"

    website_url = str(payload.get("websiteUrl", "")).strip()
    if not website_url:
        errors["websiteUrl"] = "Website URL is required"
    elif not website_url.startswith(("http://", "https://")):
        errors["websiteUrl"] = "Website URL must start with http:// or https://"

    display_order = payload.get("displayOrder", 0)
    try:
        if int(display_order) < 0:
            errors["displayOrder"] = "Display order must be 0 or greater"
    except (TypeError, ValueError):
        errors["displayOrder"] = "Display order must be a whole number"

    return errors


def is_public_request(event: dict[str, Any]) -> bool:
    # The public route is mounted at /partners; the admin route at /admin/partners.
    resource_path = str(event.get("resource") or event.get("path") or "")
    return "/admin/" not in resource_path


def get_public_partners(table) -> list[dict[str, Any]]:
    """Active partners for the public list, memoized per warm container."""
    now = time.monotonic()
    if _public_cache["partners"] is not None and now < _public_cache["expires_at"]:
        return _public_cache["partners"]

    active = [p for p in list_partners(table) if p.get("isActive")]
    _public_cache["partners"] = active
    _public_cache["expires_at"] = now + PUBLIC_CACHE_TTL
    return active


def invalidate_public_cache() -> None:
    _public_cache["partners"] = None
    _public_cache["expires_at"] = 0.0


def lambda_handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    http_method = event.get("httpMethod")

    if http_method == "OPTIONS":
        return respond(200, {})

    public = is_public_request(event)

    # The public route is read-only; writes are admin-only.
    if public and http_method != "GET":
        return respond(403, {"error": "Not permitted"})

    table = dynamodb.Table(TABLE_NAME)

    if http_method == "GET":
        try:
            if public:
                partners = get_public_partners(table)
            else:
                partners = list_partners(table)
        except Exception as exc:
            print(f"Partner scan error: {exc}")
            return respond(500, {"error": "Failed to load partners"})

        extra_headers = (
            {"Cache-Control": f"public, max-age={PUBLIC_CACHE_TTL}"} if public else None
        )
        return respond(
            200,
            {"partners": partners, "count": len(partners)},
            extra_headers=extra_headers,
        )

    if http_method == "POST":
        payload = parse_json_body(event)
        errors = validate_payload(payload)
        if errors:
            return respond(400, {"error": "Invalid partner details", "fields": errors})

        partner_id = str(payload.get("partnerId") or uuid4())
        now = now_iso()

        try:
            existing = table.get_item(Key={"partnerId": partner_id}).get("Item")
            record = {
                "partnerId": partner_id,
                "name": str(payload["name"]).strip(),
                "websiteUrl": str(payload["websiteUrl"]).strip(),
                "logoUrl": str(payload.get("logoUrl", "")).strip(),
                "description": str(payload.get("description", "")).strip(),
                "scope": str(payload.get("scope", "all")).strip() or "all",
                "displayOrder": int(payload.get("displayOrder", 0) or 0),
                "isActive": bool(payload.get("isActive", True)),
                "tier": str(payload.get("tier", "bronze")).strip() or "bronze",
                "createdAt": existing.get("createdAt", now) if existing else now,
                "updatedAt": now,
            }
            table.put_item(Item=record)
        except ClientError as exc:
            print(f"Partner write error: {exc}")
            return respond(500, {"error": "Failed to save partner"})

        invalidate_public_cache()
        return respond(200, {"partner": normalize_partner(record)})

    if http_method == "DELETE":
        partner_id = str((event.get("pathParameters") or {}).get("partnerId", "")).strip()
        if not partner_id:
            return respond(400, {"error": "Missing partnerId"})

        try:
            response = table.delete_item(
                Key={"partnerId": partner_id},
                ReturnValues="ALL_OLD",
            )
        except ClientError as exc:
            print(f"Partner delete error: {exc}")
            return respond(500, {"error": "Failed to delete partner"})

        if "Attributes" not in response:
            return respond(404, {"error": "Partner not found"})

        invalidate_public_cache()

        return respond(200, {"deleted": True, "partnerId": partner_id})

    return respond(405, {"error": "Method not allowed"})