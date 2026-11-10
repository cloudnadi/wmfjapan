"""
Lambda: /admin/site-config  (Cognito-protected)
  GET  → return current carousel image config for both slots
  POST /admin/site-config/upload-url?slot=onam|anniversary
       → accept base64-encoded image in JSON body, upload directly to S3,
         save URL to DynamoDB, return the live image URL.
         No presigned URL → no cross-origin PUT → no CORS issue.
  PUT  → legacy confirm step, kept for compatibility.
"""

import base64
import json
import re
import os
import time
from datetime import datetime, timezone

import boto3
from botocore.exceptions import ClientError

dynamodb = boto3.resource("dynamodb", region_name=os.environ["REGION"])
s3 = boto3.client("s3", region_name=os.environ["REGION"])

TABLE_NAME = os.environ["SITE_CONFIG_TABLE"]
ASSETS_BUCKET = os.environ["ASSETS_BUCKET"]
ASSETS_BASE_URL = os.environ["ASSETS_BASE_URL"].rstrip("/")

VALID_SLOTS = {"onam", "anniversary"}
ALLOWED_CONTENT_TYPES = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
}
# 3 MB keeps the Lambda event (base64 overhead ~4 MB) under the 6 MB payload limit.
MAX_IMAGE_BYTES = 3 * 1024 * 1024

# key → (allowed values, default)
VALID_SETTINGS = {
    "partners_display_mode": (("categorized", "flat"), "flat"),
    "onam_capacity": (None, "350"),
    "onam_registration_expiry": (None, ""),
    # JSON: {"fee": int}
    "npo_membership_config": (None, ""),
    # Site-wide payment methods. JSON: {"methods": {square|paypay|bank: {enabled, url, instructions}}}
    "payment_methods_config": (None, ""),
}
NPO_PAYMENT_METHODS = ("square", "paypay", "bank")


def cors_headers():
    return {
        "Access-Control-Allow-Origin": "*",
        "Access-Control-Allow-Headers": "Content-Type,Authorization",
        "Access-Control-Allow-Methods": "OPTIONS,GET,POST,PUT",
        "Content-Type": "application/json",
    }


def respond(status: int, body: dict):
    return {
        "statusCode": status,
        "headers": cors_headers(),
        "body": json.dumps(body),
    }


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def get_slot_item(table, slot: str) -> dict | None:
    try:
        resp = table.get_item(Key={"configKey": f"carousel_{slot}"})
        return resp.get("Item")
    except ClientError:
        return None


def save_config(table, slot: str, key: str, image_url: str, updated_at: str) -> None:
    table.put_item(Item={
        "configKey": f"carousel_{slot}",
        "slot": slot,
        "imageKey": key,
        "imageUrl": image_url,
        "updatedAt": updated_at,
    })


def handle_get(_event):
    table = dynamodb.Table(TABLE_NAME)
    config = {}
    for slot in VALID_SLOTS:
        item = get_slot_item(table, slot)
        if item and item.get("imageUrl"):
            config[slot] = {
                "imageUrl": item["imageUrl"],
                "imageKey": item.get("imageKey"),
                "updatedAt": item.get("updatedAt"),
            }
    for key, (_, default) in VALID_SETTINGS.items():
        try:
            resp = table.get_item(Key={"configKey": key})
            item = resp.get("Item")
            config[key] = item.get("value", default) if item else default
        except ClientError:
            config[key] = default
    return respond(200, {"config": config})


def handle_get_upload_url(event):
    params = event.get("queryStringParameters") or {}
    slot = (params.get("slot") or "").lower()
    if slot not in VALID_SLOTS:
        return respond(400, {"error": f"slot must be one of: {', '.join(sorted(VALID_SLOTS))}"})

    body = {}
    try:
        body = json.loads(event.get("body") or "{}")
    except (ValueError, TypeError):
        pass

    content_type = body.get("contentType", "image/jpeg")
    if content_type not in ALLOWED_CONTENT_TYPES:
        allowed = ", ".join(sorted(ALLOWED_CONTENT_TYPES))
        return respond(400, {"error": f"Unsupported content type. Allowed: {allowed}"})

    data_b64 = body.get("data", "")
    if not data_b64:
        return respond(400, {"error": "No image data provided"})

    try:
        image_bytes = base64.b64decode(data_b64)
    except Exception:
        return respond(400, {"error": "Invalid base64 image data"})

    if len(image_bytes) > MAX_IMAGE_BYTES:
        limit_mb = MAX_IMAGE_BYTES // (1024 * 1024)
        return respond(400, {"error": f"Image exceeds the {limit_mb} MB limit"})

    ext = ALLOWED_CONTENT_TYPES[content_type]
    key = f"media/carousel/{slot}_{int(time.time())}{ext}"

    try:
        s3.put_object(
            Bucket=ASSETS_BUCKET,
            Key=key,
            Body=image_bytes,
            ContentType=content_type,
        )
    except ClientError as exc:
        return respond(500, {"error": f"Failed to store image: {exc}"})

    image_url = f"{ASSETS_BASE_URL}/{key}"
    updated_at = now_iso()
    table = dynamodb.Table(TABLE_NAME)

    old_key = None
    old_item = get_slot_item(table, slot)
    if old_item and old_item.get("imageKey") and old_item["imageKey"] != key:
        old_key = old_item["imageKey"]

    try:
        save_config(table, slot, key, image_url, updated_at)
    except ClientError as exc:
        # Roll back the S3 object if DynamoDB write fails.
        try:
            s3.delete_object(Bucket=ASSETS_BUCKET, Key=key)
        except ClientError:
            pass
        return respond(500, {"error": f"Failed to save config: {exc}"})

    if old_key:
        try:
            s3.delete_object(Bucket=ASSETS_BUCKET, Key=old_key)
        except ClientError:
            pass

    return respond(200, {
        "imageUrl": image_url,
        "configKey": f"carousel_{slot}",
        "updatedAt": updated_at,
    })


def _parse_object(raw: str, what: str) -> dict:
    try:
        data = json.loads(raw)
    except (TypeError, ValueError):
        raise ValueError(f"{what} must be valid JSON")
    if not isinstance(data, dict):
        raise ValueError(f"{what} must be an object")
    return data


def normalize_npo_config(raw: str) -> dict:
    """Validate the NPO membership fee JSON and return a clean copy."""
    data = _parse_object(raw, "NPO membership settings")
    try:
        fee = int(data.get("fee"))
        if not 0 <= fee <= 1000000:
            raise ValueError
    except (TypeError, ValueError):
        raise ValueError("Membership fee must be a whole number of yen between 0 and 1,000,000")
    return {"fee": fee, "whatsappLinks": normalize_whatsapp_links(data.get("whatsappLinks"))}


def normalize_whatsapp_links(raw) -> dict:
    """{membership year → WhatsApp group invite link}; blank entries are dropped."""
    if raw in (None, ""):
        return {}
    if not isinstance(raw, dict):
        raise ValueError("WhatsApp group links must be a list of year → link")
    links = {}
    for year, url in raw.items():
        year, url = str(year).strip(), str(url or "").strip()
        if not url:
            continue
        if not re.fullmatch(r"\d{4}-\d{2}", year):
            raise ValueError(f"'{year}' isn't a membership year — use the form 2026-27")
        if not url.startswith("https://") or len(url) > 300 or any(c.isspace() for c in url):
            raise ValueError(f"The WhatsApp link for {year} must be a single https:// link (e.g. https://chat.whatsapp.com/…)")
        links[year] = url
    return links


def normalize_payment_methods_config(raw: str) -> dict:
    """Validate the site-wide payment methods JSON and return a clean copy."""
    data = _parse_object(raw, "Payment method settings")
    methods_in = data.get("methods") if isinstance(data.get("methods"), dict) else {}
    methods = {}
    for key in NPO_PAYMENT_METHODS:
        entry = methods_in.get(key) if isinstance(methods_in.get(key), dict) else {}
        url = str(entry.get("url", "")).strip()
        if url and not url.startswith(("http://", "https://")):
            raise ValueError(f"{key} payment link must start with http:// or https://")
        methods[key] = {
            "enabled": entry.get("enabled") is True,
            "url": url,
            "instructions": str(entry.get("instructions", "")).strip()[:2000],
        }
    return {"methods": methods}


def handle_confirm(event):
    """General site settings update (PUT /admin/site-config)."""
    try:
        body = json.loads(event.get("body") or "{}")
    except (ValueError, TypeError):
        return respond(400, {"error": "Invalid JSON body"})

    setting = (body.get("setting") or "").strip()
    value = str(body.get("value") or "").strip()

    if setting not in VALID_SETTINGS:
        return respond(400, {"error": f"Unknown setting. Valid settings: {', '.join(VALID_SETTINGS)}"})

    allowed_values, _ = VALID_SETTINGS[setting]
    if allowed_values is not None and value not in allowed_values:
        return respond(400, {"error": f"Invalid value for '{setting}'. Allowed: {', '.join(allowed_values)}"})

    if setting == "onam_capacity":
        try:
            if not 1 <= int(value) <= 100000:
                raise ValueError
        except ValueError:
            return respond(400, {"error": "Onam capacity must be a whole number between 1 and 100000"})
    if setting == "onam_registration_expiry" and value:
        try:
            datetime.strptime(value, "%Y-%m-%d")
        except ValueError:
            return respond(400, {"error": "Registration expiry must use YYYY-MM-DD format"})

    if setting == "npo_membership_config":
        try:
            value = json.dumps(normalize_npo_config(value))
        except ValueError as exc:
            return respond(400, {"error": str(exc)})

    if setting == "payment_methods_config":
        try:
            value = json.dumps(normalize_payment_methods_config(value))
        except ValueError as exc:
            return respond(400, {"error": str(exc)})

    table = dynamodb.Table(TABLE_NAME)
    try:
        table.put_item(Item={"configKey": setting, "value": value})
    except ClientError as exc:
        return respond(500, {"error": f"Failed to save setting: {exc}"})

    return respond(200, {"configKey": setting, "value": value})


def lambda_handler(event, _context):
    method = (event.get("httpMethod") or "GET").upper()
    path = event.get("path") or ""

    if method == "GET":
        return handle_get(event)
    if method == "POST" and path.endswith("/upload-url"):
        return handle_get_upload_url(event)
    if method == "PUT":
        return handle_confirm(event)
