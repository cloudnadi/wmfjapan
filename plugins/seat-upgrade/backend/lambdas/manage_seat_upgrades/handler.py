"""
Lambda: admin seat-upgrade management for the Celebrity Event add-on.
Protected by Cognito JWT authorizer.

Routes (API Gateway proxy):
  GET    /admin/events/{eventType}/seat-upgrade-tiers            -> list tiers
  POST   /admin/events/{eventType}/seat-upgrade-tiers            -> create tier
  GET    /admin/events/{eventType}/seat-upgrade-tiers/{tierId}   -> get tier
  PATCH  /admin/events/{eventType}/seat-upgrade-tiers/{tierId}   -> update tier
  DELETE /admin/events/{eventType}/seat-upgrade-tiers/{tierId}   -> delete tier

  POST   /admin/events/{eventType}/seat-upgrade-seat-map          -> upload seat map image, returns {imageUrl}

  POST   /admin/events/{eventType}/seat-upgrade-announcement     -> email every paid event/combo registrant
                                                                       that premium seating is available;
                                                                       re-triggerable, no "already sent" tracking

  GET    /admin/events/{eventType}/seat-upgrade-email-templates                    -> list all 4 templates
                                                                                        (default + admin override merged)
  PATCH  /admin/events/{eventType}/seat-upgrade-email-templates/{templateKey}      -> save an override
  DELETE /admin/events/{eventType}/seat-upgrade-email-templates/{templateKey}      -> reset to default
  POST   /admin/events/{eventType}/seat-upgrade-email-templates/{templateKey}/preview
                                                                                    -> render draft fields against
                                                                                       sample data, nothing persisted

  GET    /admin/events/{eventType}/seat-upgrades                 -> list purchase requests
  PATCH  /admin/events/{eventType}/seat-upgrades/{upgradeId}     -> update status
                                                                     (pending/paid/refund_requested/cancelled)

Email templates are global to the plugin, not per-event, despite living
under this {eventType}-scoped route — see handle_email_templates' docstring.

Seat upgrades are a paid add-on layered on top of a registration — the
registration itself is never touched; requests only ever reference it by
registrationId (same pattern as entry_allocations).

Marking a request paid (e.g. confirming a bank transfer by hand) or
finalizing a refund_requested one to cancelled both send the registrant a
confirmation email — see seat_upgrade_emailer.py and
docs/SEAT_UPGRADE_FEATURE.md's "Refunds" / "Email notifications" sections.
"""

import base64
import json
import logging
import os
import time
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Optional

import boto3
from boto3.dynamodb.conditions import Key
from botocore.exceptions import ClientError

import seat_upgrade_emailer
import seat_upgrade_logic

logger = logging.getLogger(__name__)
logger.setLevel(os.environ.get("LOG_LEVEL", "INFO").upper())

s3 = boto3.client("s3", region_name=os.environ.get("REGION"))
ASSETS_BUCKET = os.environ.get("ASSETS_BUCKET", "")
ASSETS_BASE_URL = os.environ.get("ASSETS_BASE_URL", "").rstrip("/")

# For the seat-upgrade-announcement route only — every other route in this
# file goes entirely through seat_upgrade_logic, which is deliberately
# independent of the registrations/events tables (see its docstring). The
# announcement is the one admin action that needs to read both directly:
# who to email (registrations) and the booking window / seat map to put in
# the email (eventInfo.seatUpgrade, same as seat_upgrade/handler.py's
# get_seat_upgrade_config — duplicated in slimmed form below rather than
# shared, same convention as _event_name below and seat_upgrade_emailer.py's
# own copy of it).
dynamodb = boto3.resource("dynamodb", region_name=os.environ.get("REGION"))
REGISTRATIONS_TABLE = os.environ.get("DYNAMODB_TABLE", "")
EVENTS_TABLE = os.environ.get("EVENTS_TABLE", "")
ELIGIBLE_OPTIONS = {"event", "combo"}

ALLOWED_SEAT_MAP_CONTENT_TYPES = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
}
# 5 MB is generous for a seat map diagram; keeps the base64-encoded Lambda
# event (~4/3 overhead) comfortably under API Gateway's 10 MB payload limit.
MAX_SEAT_MAP_IMAGE_BYTES = 5 * 1024 * 1024

VALID_TIER_STATUSES = {"ACTIVE", "INACTIVE"}
# "refund_requested" is normally reached via the registrant's own self-service
# cancel (see seat_upgrade/handler.py), but an admin can also land a request
# there directly, or finalize one out of it — see handle_upgrades' PATCH.
VALID_UPGRADE_STATUSES = {"pending", "paid", "cancelled", "refund_requested"}


def decimal_to_python(obj):
    if isinstance(obj, Decimal):
        return int(obj) if obj == obj.to_integral_value() else float(obj)
    raise TypeError(f"Object of type {type(obj)} is not JSON serializable")


def cors_headers():
    return {
        "Access-Control-Allow-Origin": "*",
        "Access-Control-Allow-Headers": "Content-Type,Authorization",
        "Access-Control-Allow-Methods": "OPTIONS,GET,POST,PATCH,DELETE",
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


def normalize_tier(item: dict[str, Any]) -> dict[str, Any]:
    capacity = int(item.get("capacity", 0) or 0)
    sold = int(item.get("upgradedSeats", 0) or 0)
    return {
        "eventType": item.get("eventType", ""),
        "tierId": item.get("tierId", ""),
        "name": item.get("name", ""),
        "description": item.get("description", ""),
        "upgradePrice": int(item.get("upgradePrice", 0) or 0),
        "capacity": capacity,
        "upgradedSeats": sold,
        "remainingCapacity": max(capacity - sold, 0),
        "status": item.get("status", "INACTIVE"),
        "displayOrder": int(item.get("displayOrder", 0) or 0),
        "mapRegion": item.get("mapRegion"),
        "seatMapCategory": item.get("seatMapCategory", ""),
        "createdAt": item.get("createdAt", ""),
        "updatedAt": item.get("updatedAt", ""),
    }


def normalize_upgrade(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "upgradeId": item.get("upgradeId", ""),
        "eventType": item.get("eventType", ""),
        "tierName": item.get("tierName", ""),
        "zoneBreakdown": [{
            "tierId": z.get("tierId", ""),
            "tierName": z.get("tierName", ""),
            "count": int(z.get("count", 0) or 0),
            "regularUnitPrice": int(z.get("regularUnitPrice", 0) or 0),
            "subtotal": int(z.get("subtotal", 0) or 0),
            "seatMapCategory": z.get("seatMapCategory", ""),
        } for z in item.get("zoneBreakdown", [])],
        "seatCount": int(item.get("seatCount", 0) or 0),
        "registrationId": item.get("registrationId", ""),
        "email": item.get("email", ""),
        "name": item.get("name", ""),
        "upgradeAmount": int(item.get("upgradeAmount", 0) or 0),
        "totalAmount": int(item.get("totalAmount", 0) or 0),
        "status": item.get("status", "pending"),
        "paymentMethod": item.get("paymentMethod", ""),
        "paymentProvider": item.get("paymentProvider", ""),
        # providerPaymentId is the checkout-time reference (PayPay
        # merchantPaymentId / Square Order id); paymentReference is the
        # provider's own transaction/payment id, stamped once paid — the
        # admin Purchase Requests table shows whichever is set as "Transaction ID".
        "providerPaymentId": item.get("providerPaymentId", ""),
        "paymentReference": item.get("paymentReference", ""),
        "refundStatus": item.get("refundStatus", ""),
        "refundReference": item.get("refundReference", ""),
        "refundError": item.get("refundError", ""),
        "refundRequestedAt": item.get("refundRequestedAt", ""),
        **{field: int(item.get(field, 0) or 0) for field in seat_upgrade_logic.DINNER_GUEST_FIELDS},
        "dinnerPriceAdults": int(item.get("dinnerPriceAdults", 0) or 0),
        "dinnerPriceKids914": int(item.get("dinnerPriceKids914", 0) or 0),
        "dinnerPriceKids68": int(item.get("dinnerPriceKids68", 0) or 0),
        "dinnerAmount": int(item.get("dinnerAmount", 0) or 0),
        "requestedAt": item.get("requestedAt", ""),
        "updatedAt": item.get("updatedAt", ""),
    }


def validate_tier_payload(payload: dict[str, Any], *, partial: bool) -> dict[str, str]:
    errors: dict[str, str] = {}

    if not partial or "name" in payload:
        if not str(payload.get("name", "")).strip():
            errors["name"] = "Tier name is required"

    if not partial or "upgradePrice" in payload:
        try:
            if int(payload.get("upgradePrice", -1)) < 0:
                raise ValueError
        except (TypeError, ValueError):
            errors["upgradePrice"] = "Upgrade price must be a non-negative whole number of yen"

    if not partial or "capacity" in payload:
        try:
            if int(payload.get("capacity", -1)) < 0:
                raise ValueError
        except (TypeError, ValueError):
            errors["capacity"] = "Capacity must be a non-negative whole number"

    if "status" in payload:
        status = str(payload.get("status", "")).strip().upper()
        if status not in VALID_TIER_STATUSES:
            errors["status"] = f"Status must be one of: {', '.join(VALID_TIER_STATUSES)}"

    # None (present or defaulted) means "no hotspot" and is always valid —
    # reuses seat_upgrade_logic's own validation so there's one definition
    # of a valid region, not two that can drift apart.
    if "mapRegion" in payload and payload.get("mapRegion") is not None:
        try:
            seat_upgrade_logic._normalize_region(payload["mapRegion"])
        except ValueError as exc:
            errors["mapRegion"] = str(exc)

    if "seatMapCategory" in payload:
        category = str(payload.get("seatMapCategory", "") or "").strip()
        if category and category not in seat_upgrade_logic.SEAT_MAP_CATEGORIES:
            errors["seatMapCategory"] = (
                f"Seat map zone must be one of: {', '.join(seat_upgrade_logic.SEAT_MAP_CATEGORIES)}"
            )

    return errors


def handle_tiers(http_method: str, event_type: str, tier_id: str, event: dict) -> dict:
    if http_method == "GET":
        try:
            if tier_id:
                tier = seat_upgrade_logic.get_tier(event_type, tier_id)
                if not tier:
                    return respond(404, {"error": f"Tier {tier_id} not found"})
                return respond(200, {"tier": normalize_tier(tier)})
            tiers = seat_upgrade_logic.list_tiers_for_event(event_type)
            return respond(200, {"tiers": [normalize_tier(t) for t in tiers], "count": len(tiers)})
        except Exception as exc:
            logger.exception("Error fetching seat upgrade tier(s) for event=%s tier=%s", event_type, tier_id)
            return respond(500, {"error": "Failed to fetch zone upgrade tier(s)", "detail": str(exc)})

    if http_method == "POST":
        if tier_id:
            return respond(400, {"error": "Cannot POST with tierId in path; use POST without tierId"})
        payload = parse_json_body(event)
        errors = validate_tier_payload(payload, partial=False)
        if errors:
            return respond(400, {"error": "Validation failed", "fields": errors})
        try:
            tier = seat_upgrade_logic.create_tier(
                event_type=event_type,
                name=str(payload.get("name", "")).strip(),
                description=str(payload.get("description", "")).strip(),
                upgrade_price=int(payload.get("upgradePrice", 0)),
                capacity=int(payload.get("capacity", 0)),
                status=str(payload.get("status", "ACTIVE")).upper(),
                display_order=int(payload.get("displayOrder", 0) or 0),
                map_region=payload.get("mapRegion"),
                seat_map_category=str(payload.get("seatMapCategory", "") or "").strip(),
            )
            return respond(201, {"message": "Zone upgrade tier created", "tier": normalize_tier(tier)})
        except ValueError as exc:
            return respond(400, {"error": str(exc)})
        except Exception as exc:
            logger.exception("Error creating seat upgrade tier for event=%s payload=%s", event_type, payload)
            return respond(500, {"error": "Failed to create zone upgrade tier", "detail": str(exc)})

    if http_method == "PATCH":
        if not tier_id:
            return respond(400, {"error": "tierId path parameter is required for PATCH"})
        payload = parse_json_body(event)
        errors = validate_tier_payload(payload, partial=True)
        if errors:
            return respond(400, {"error": "Validation failed", "fields": errors})
        if not seat_upgrade_logic.get_tier(event_type, tier_id):
            return respond(404, {"error": f"Tier {tier_id} not found"})
        try:
            tier = seat_upgrade_logic.update_tier(
                event_type=event_type,
                tier_id=tier_id,
                name=payload.get("name"),
                description=payload.get("description"),
                upgrade_price=int(payload["upgradePrice"]) if "upgradePrice" in payload else None,
                capacity=int(payload["capacity"]) if "capacity" in payload else None,
                status=payload.get("status"),
                display_order=int(payload["displayOrder"]) if "displayOrder" in payload else None,
                map_region=payload["mapRegion"] if "mapRegion" in payload else seat_upgrade_logic.UNSET,
                seat_map_category=str(payload["seatMapCategory"] or "").strip() if "seatMapCategory" in payload else None,
            )
            return respond(200, {"message": "Zone upgrade tier updated", "tier": normalize_tier(tier)})
        except ValueError as exc:
            return respond(400, {"error": str(exc)})
        except Exception as exc:
            logger.exception("Error updating seat upgrade tier=%s for event=%s payload=%s", tier_id, event_type, payload)
            return respond(500, {"error": "Failed to update zone upgrade tier", "detail": str(exc)})

    if http_method == "DELETE":
        if not tier_id:
            return respond(400, {"error": "tierId path parameter is required for DELETE"})
        tier = seat_upgrade_logic.get_tier(event_type, tier_id)
        if not tier:
            return respond(404, {"error": f"Tier {tier_id} not found"})
        if int(tier.get("upgradedSeats", 0) or 0) > 0:
            return respond(400, {
                "error": "Cannot delete a tier with existing upgrade purchases. "
                         "Set it to INACTIVE instead."
            })
        try:
            seat_upgrade_logic.delete_tier(event_type, tier_id)
            return respond(200, {"message": "Zone upgrade tier deleted"})
        except Exception as exc:
            logger.exception("Error deleting seat upgrade tier=%s for event=%s", tier_id, event_type)
            return respond(500, {"error": "Failed to delete zone upgrade tier", "detail": str(exc)})

    return respond(405, {"error": "Method not allowed"})


def handle_upgrades(http_method: str, event_type: str, upgrade_id: str, event: dict) -> dict:
    if http_method == "GET":
        if upgrade_id:
            item = seat_upgrade_logic.get_upgrade(upgrade_id)
            if not item or item.get("eventType") != event_type:
                return respond(404, {"error": f"Zone upgrade {upgrade_id} not found"})
            return respond(200, {"upgrade": normalize_upgrade(item)})
        try:
            items = seat_upgrade_logic.list_upgrades_for_event(event_type)
            return respond(200, {"upgrades": [normalize_upgrade(i) for i in items], "count": len(items)})
        except Exception as exc:
            logger.exception("Error listing seat upgrades for event=%s", event_type)
            return respond(500, {"error": "Failed to list zone upgrades", "detail": str(exc)})

    if http_method == "PATCH":
        if not upgrade_id:
            return respond(400, {"error": "upgradeId path parameter is required for PATCH"})
        payload = parse_json_body(event)
        status = str(payload.get("status", "")).strip().lower()
        if status not in VALID_UPGRADE_STATUSES:
            return respond(400, {
                "error": "Validation failed",
                "fields": {"status": f"Status must be one of: {', '.join(VALID_UPGRADE_STATUSES)}"},
            })
        existing = seat_upgrade_logic.get_upgrade(upgrade_id)
        if not existing or existing.get("eventType") != event_type:
            return respond(404, {"error": f"Zone upgrade {upgrade_id} not found"})
        prior_status = existing.get("status")
        try:
            updated = seat_upgrade_logic.update_upgrade_status(upgrade_id, status)
            # An admin marking a bank-transfer (or otherwise unconfirmed)
            # request paid by hand still deserves the same registrant
            # confirmation email the payment-provider paths send automatically.
            if status == "paid" and prior_status != "paid":
                seat_upgrade_emailer.send_paid_email(updated)
            # Finalizing a cancellation — whether from a still-unpaid request
            # or from a refund_requested one an admin has confirmed the money
            # actually went back on — is always worth a registrant notice.
            if status == "cancelled" and prior_status != "cancelled":
                seat_upgrade_emailer.send_cancelled_email(updated)
            return respond(200, {"message": "Zone upgrade updated", "upgrade": normalize_upgrade(updated)})
        except ValueError as exc:
            return respond(400, {"error": str(exc)})
        except Exception as exc:
            logger.exception("Error updating seat upgrade=%s for event=%s to status=%s", upgrade_id, event_type, status)
            return respond(500, {"error": "Failed to update zone upgrade", "detail": str(exc)})

    return respond(405, {"error": "Method not allowed"})


def handle_seat_map_upload(event_type: str, event: dict) -> dict:
    """POST /admin/events/{eventType}/seat-upgrade-seat-map

    Uploads a base64-encoded image to the shared assets S3 bucket (same
    bucket/pattern as manage_site_config's carousel upload — see its
    docstring for why base64-in-JSON rather than a presigned URL) and
    returns its public URL. Doesn't persist anything itself: the caller
    (AdminDashboard.jsx's Events form) stores the returned URL in
    eventInfo.seatUpgrade.seatMapImageUrl as part of the normal event save.

    Unlike the carousel upload, this doesn't track or delete a prior
    image when replaced — there's no per-event config record here to read
    the old key back from. Orphaned objects under media/ self-expire via
    the 90-day lifecycle rule in backend/assets.tf.
    """
    if not ASSETS_BUCKET or not ASSETS_BASE_URL:
        return respond(500, {"error": "Asset storage is not configured"})

    payload = parse_json_body(event)
    content_type = payload.get("contentType", "image/jpeg")
    if content_type not in ALLOWED_SEAT_MAP_CONTENT_TYPES:
        allowed = ", ".join(sorted(ALLOWED_SEAT_MAP_CONTENT_TYPES))
        return respond(400, {"error": f"Unsupported content type. Allowed: {allowed}"})

    data_b64 = payload.get("data", "")
    if not data_b64:
        return respond(400, {"error": "No image data provided"})

    try:
        image_bytes = base64.b64decode(data_b64)
    except Exception:
        return respond(400, {"error": "Invalid base64 image data"})

    if len(image_bytes) > MAX_SEAT_MAP_IMAGE_BYTES:
        limit_mb = MAX_SEAT_MAP_IMAGE_BYTES // (1024 * 1024)
        return respond(400, {"error": f"Image exceeds the {limit_mb} MB limit"})

    ext = ALLOWED_SEAT_MAP_CONTENT_TYPES[content_type]
    key = f"media/seat-upgrade/{event_type}_{int(time.time())}{ext}"

    try:
        s3.put_object(Bucket=ASSETS_BUCKET, Key=key, Body=image_bytes, ContentType=content_type)
    except ClientError as exc:
        logger.exception("Error uploading seat map image for event=%s", event_type)
        return respond(500, {"error": "Failed to store image", "detail": str(exc)})

    return respond(200, {"imageUrl": f"{ASSETS_BASE_URL}/{key}"})


def normalize_email_template(template_key: str, override: dict[str, Any] | None) -> dict[str, Any]:
    """The built-in default merged with whatever an admin has customized —
    reuses seat_upgrade_emailer.DEFAULT_TEMPLATES directly so the admin
    editor and the actual sender can never drift apart."""
    base = dict(seat_upgrade_emailer.DEFAULT_TEMPLATES[template_key])
    customized = []
    for field in seat_upgrade_logic.EMAIL_TEMPLATE_FIELDS:
        value = (override or {}).get(field)
        if isinstance(value, str) and value.strip():
            base[field] = value
            customized.append(field)
    return {
        "templateKey": template_key,
        **base,
        "isCustomized": bool(customized),
        "updatedAt": (override or {}).get("updatedAt", ""),
    }


def handle_email_templates(http_method: str, template_key: str, is_preview: bool, event: dict) -> dict:
    """Admin CRUD + live preview for the plugin's registrant-facing email
    copy. Global to the plugin, not per-event — mounted under
    /admin/events/{eventType}/... anyway (see api_gateway.tf) purely to
    reuse that mount point without new API Gateway wiring; {eventType}
    itself is ignored here.
    """
    if http_method == "GET":
        if template_key:
            return respond(400, {"error": "GET is only supported on the collection, not a single template"})
        try:
            overrides = seat_upgrade_logic.list_email_template_overrides()
            templates = [
                normalize_email_template(key, overrides.get(key))
                for key in sorted(seat_upgrade_logic.EMAIL_TEMPLATE_KEYS)
            ]
            return respond(200, {"templates": templates})
        except Exception as exc:
            logger.exception("Error listing seat upgrade email templates")
            return respond(500, {"error": "Failed to list email templates", "detail": str(exc)})

    if not template_key:
        return respond(400, {"error": "templateKey path parameter is required"})
    if template_key not in seat_upgrade_logic.EMAIL_TEMPLATE_KEYS:
        return respond(404, {"error": f"Unknown template: {template_key}"})

    payload = parse_json_body(event)

    if is_preview:
        if http_method != "POST":
            return respond(405, {"error": "Method not allowed"})
        fields = {
            k: v for k, v in payload.items()
            if k in seat_upgrade_logic.EMAIL_TEMPLATE_FIELDS and isinstance(v, str)
        }
        try:
            return respond(200, seat_upgrade_emailer.render_preview(template_key, fields))
        except ValueError as exc:
            return respond(400, {"error": str(exc)})
        except Exception as exc:
            logger.exception("Error rendering email template preview for %s", template_key)
            return respond(500, {"error": "Failed to render preview", "detail": str(exc)})

    if http_method == "PATCH":
        if "tone" in payload and payload["tone"] not in seat_upgrade_logic.VALID_EMAIL_TEMPLATE_TONES:
            return respond(400, {
                "error": "Validation failed",
                "fields": {
                    "tone": f"Tone must be one of: {', '.join(sorted(seat_upgrade_logic.VALID_EMAIL_TEMPLATE_TONES))}"
                },
            })
        fields = {k: str(v) for k, v in payload.items() if k in seat_upgrade_logic.EMAIL_TEMPLATE_FIELDS}
        try:
            override = seat_upgrade_logic.save_email_template_override(template_key, fields)
            return respond(200, {"template": normalize_email_template(template_key, override)})
        except ValueError as exc:
            return respond(400, {"error": str(exc)})
        except Exception as exc:
            logger.exception("Error saving seat upgrade email template=%s", template_key)
            return respond(500, {"error": "Failed to save email template", "detail": str(exc)})

    if http_method == "DELETE":
        try:
            seat_upgrade_logic.reset_email_template_override(template_key)
            return respond(200, {"template": normalize_email_template(template_key, None)})
        except Exception as exc:
            logger.exception("Error resetting seat upgrade email template=%s", template_key)
            return respond(500, {"error": "Failed to reset email template", "detail": str(exc)})

    return respond(405, {"error": "Method not allowed"})


def _event_name(event_type: str) -> str:
    """Best-effort friendly event name; falls back to the raw eventType."""
    if not EVENTS_TABLE or not event_type:
        return event_type or "your event"
    try:
        item = dynamodb.Table(EVENTS_TABLE).get_item(Key={"eventType": event_type}).get("Item")
        return (item or {}).get("eventName", event_type)
    except ClientError:
        return event_type


def _get_seat_upgrade_settings(event_type: str) -> dict:
    """enabled/startDate/endDate/seatMapImageUrl from eventInfo.seatUpgrade
    — everything the announcement email needs. Payment methods aren't read
    here; that's the registrant-facing Lambda's concern."""
    empty = {"enabled": False, "startDate": "", "endDate": "", "seatMapImageUrl": ""}
    if not EVENTS_TABLE or not event_type:
        return empty
    try:
        item = dynamodb.Table(EVENTS_TABLE).get_item(Key={"eventType": event_type}).get("Item") or {}
    except ClientError:
        logger.exception("Could not read seat upgrade settings for event=%s", event_type)
        return empty

    event_info = item.get("eventInfo", {})
    if isinstance(event_info, str):
        try:
            event_info = json.loads(event_info) if event_info.strip() else {}
        except json.JSONDecodeError:
            event_info = {}
    if not isinstance(event_info, dict):
        event_info = {}

    raw = event_info.get("seatUpgrade")
    if not isinstance(raw, dict):
        return empty
    return {
        "enabled": raw.get("enabled") is True,
        "startDate": raw.get("startDate") if isinstance(raw.get("startDate"), str) else "",
        "endDate": raw.get("endDate") if isinstance(raw.get("endDate"), str) else "",
        "seatMapImageUrl": raw.get("seatMapImageUrl") if isinstance(raw.get("seatMapImageUrl"), str) else "",
    }


def _window_status(settings: dict) -> str:
    """"not_started" | "open" | "ended" — mirrors seat_upgrade/handler.py's
    window_status(), just for the announcement email's own copy (e.g.
    "booking opens {date}"); this route doesn't enforce the window, an
    admin may want to announce a soon-to-open zone ahead of time."""
    today = datetime.now(timezone.utc).date().isoformat()
    start = settings.get("startDate") or ""
    end = settings.get("endDate") or ""
    if start and today < start:
        return "not_started"
    if end and today > end:
        return "ended"
    return "open"


def _eligible_registrations_for_event(event_type: str) -> list:
    """Every paid event/combo registration for this event — who the
    announcement goes to. Mirrors seat_upgrade/handler.py's
    eligible_registrations(), keyed by eventType (EventTypeIndex) instead
    of by email, since this is a broadcast rather than one registrant's own
    lookup."""
    if not REGISTRATIONS_TABLE:
        return []
    try:
        resp = dynamodb.Table(REGISTRATIONS_TABLE).query(
            IndexName="EventTypeIndex",
            KeyConditionExpression=Key("eventType").eq(event_type),
        )
        items = resp.get("Items", [])
    except ClientError:
        logger.exception("Could not list registrations for event=%s", event_type)
        return []
    return [
        reg for reg in items
        if reg.get("registrationOption") in ELIGIBLE_OPTIONS
        and str(reg.get("paymentStatus", "")).lower() == "paid"
    ]


def _normalize_announcement_celebrity_dinner(raw: Any) -> Optional[dict]:
    """The announcement endpoint knows nothing about the Celebrity Dinner
    feature or its tables (this plugin's Lambda has no IAM access to
    them) — the caller (CelebrityDinnerManagement.jsx, which already has
    the event's Celebrity Dinner config loaded) supplies whatever it wants
    mentioned, in the same shape celebrity_dinner_logic.normalize_config
    uses. None/not-a-dict means "don't mention Celebrity Dinner"."""
    if not isinstance(raw, dict):
        return None
    return {
        "venue": str(raw.get("venue", "") or "").strip(),
        "dinnerDate": str(raw.get("dinnerDate", "") or "").strip(),
        "currency": str(raw.get("currency", "JPY") or "JPY").strip(),
        "dinnerPriceAdults": int(raw.get("dinnerPriceAdults", 0) or 0),
        "dinnerPriceKids914": int(raw.get("dinnerPriceKids914", 0) or 0),
        "dinnerPriceKids68": int(raw.get("dinnerPriceKids68", 0) or 0),
        "conditions": str(raw.get("conditions", "") or "").strip(),
    }


def handle_announcement(http_method: str, event_type: str, event: dict) -> dict:
    """POST /admin/events/{eventType}/seat-upgrade-announcement

    Admin-triggered broadcast: emails every paid event/combo registrant for
    this event that premium seating is available. Re-triggerable — every
    call re-sends to the full current list of eligible registrants (no
    "already sent" tracking), so it doubles as a reminder blast once new
    people have registered since the last send.

    Optional JSON body: {"celebrityDinner": {...}} — when present, the same
    broadcast also mentions the Celebrity Dinner, so admins have one button
    for "premium seating + dinner are open" instead of two separate emails.
    See _normalize_announcement_celebrity_dinner.
    """
    if http_method != "POST":
        return respond(405, {"error": "Method not allowed"})

    celebrity_dinner = _normalize_announcement_celebrity_dinner(parse_json_body(event).get("celebrityDinner"))

    settings = _get_seat_upgrade_settings(event_type)
    if not settings["enabled"]:
        return respond(400, {"error": "Zone upgrades are not enabled for this event"})

    tiers = seat_upgrade_logic.list_active_tiers_for_event(event_type)
    if not tiers:
        return respond(400, {"error": "Add at least one active seating zone before sending the announcement"})

    event_name = _event_name(event_type)
    registrations = _eligible_registrations_for_event(event_type)
    if not registrations:
        return respond(200, {
            "sent": 0,
            "failed": 0,
            "total": 0,
            "eventName": event_name,
            "message": "No paid event/combo registrants to notify yet.",
        })

    window = {
        "status": _window_status(settings),
        "startDate": settings["startDate"],
        "endDate": settings["endDate"],
    }

    sent = 0
    failed = 0
    for reg in registrations:
        try:
            ok = seat_upgrade_emailer.send_announcement_email(
                reg, event_name, tiers, settings["seatMapImageUrl"], window, celebrity_dinner=celebrity_dinner
            )
        except Exception:
            logger.exception("Error sending seat upgrade announcement to %s", reg.get("email"))
            ok = False
        if ok:
            sent += 1
        else:
            failed += 1

    return respond(200, {"sent": sent, "failed": failed, "total": len(registrations), "eventName": event_name})


def lambda_handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    http_method = event.get("httpMethod")

    if http_method == "OPTIONS":
        return respond(200, {})

    path_params = event.get("pathParameters") or {}
    event_type = str(path_params.get("eventType", "")).strip()
    if not event_type:
        return respond(400, {"error": "eventType path parameter is required"})

    resource_path = str(event.get("resource") or event.get("path") or "")

    if resource_path.endswith("/seat-upgrade-seat-map"):
        if http_method != "POST":
            return respond(405, {"error": "Method not allowed"})
        return handle_seat_map_upload(event_type, event)

    if resource_path.endswith("/seat-upgrade-announcement"):
        return handle_announcement(http_method, event_type, event)

    if "/seat-upgrade-email-templates" in resource_path:
        template_key = str(path_params.get("templateKey", "")).strip()
        is_preview = resource_path.endswith("/preview")
        return handle_email_templates(http_method, template_key, is_preview, event)

    if "/seat-upgrade-tiers" in resource_path:
        tier_id = str(path_params.get("tierId", "")).strip()
        return handle_tiers(http_method, event_type, tier_id, event)

    if "/seat-upgrades" in resource_path:
        upgrade_id = str(path_params.get("upgradeId", "")).strip()
        return handle_upgrades(http_method, event_type, upgrade_id, event)

    return respond(404, {"error": "Unknown route"})
