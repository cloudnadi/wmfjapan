"""
Lambda: /admin/events/{eventType}/celebrity-dinner
Protected by Cognito JWT authorizer.

Admin config + visibility for the Celebrity Dinner add-on:
- GET   /admin/events/{eventType}/celebrity-dinner                              -> current config
- PUT   /admin/events/{eventType}/celebrity-dinner                              -> create/update config
- GET   /admin/events/{eventType}/celebrity-dinner/registrations                -> list sign-ups
- PATCH /admin/events/{eventType}/celebrity-dinner/registrations/{registrationId} -> update status
                                                                                     (pending/paid/refund_requested/cancelled)

The on/off switch itself (`enabled`) is NOT stored here — it lives on the
event's own eventInfo.celebrityDinner.enabled, set from the Events admin
form (frontend/src/AdminDashboard.jsx), same as the seat-upgrade plugin's own
enable toggle. This config only covers the registration window
(registrationStartsAt/registrationEndsAt), when the dinner itself happens
(dinnerDate), dinner details (venue, price, conditions), payment methods,
and self-service cancellation (cancellationEnabled/refundWindowEndsAt —
see celebrity_dinner_logic.py's cancellation_allowed) for an event that
already has the feature turned on. It never reads or writes the registrations table except via the
PATCH route above, which only ever updates a registration's payment status
(mirrors the seat-upgrade plugin's own admin PATCH .../seat-upgrades/{upgradeId}
— a "paid" or "cancelled" transition sends the registrant a confirmation
email via celebrity_dinner_emailer.py, same as that plugin's own admin PATCH).
"""

import json
from datetime import datetime
from typing import Any

from botocore.exceptions import ClientError

import celebrity_dinner_emailer as dinner_emailer
import celebrity_dinner_logic as dinner


def cors_headers():
    return {
        "Access-Control-Allow-Origin": "*",
        "Access-Control-Allow-Headers": "Content-Type,Authorization",
        "Access-Control-Allow-Methods": "OPTIONS,GET,PUT,PATCH",
        "Content-Type": "application/json",
    }


def respond(status: int, body: dict):
    return {
        "statusCode": status,
        "headers": cors_headers(),
        "body": json.dumps(body, default=dinner.decimal_to_python),
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


def validate_config_payload(payload: dict[str, Any]) -> dict[str, str]:
    errors: dict[str, str] = {}

    for field in ("registrationStartsAt", "registrationEndsAt", "refundWindowEndsAt", "dinnerDate"):
        value = payload.get(field)
        if value:
            try:
                datetime.fromisoformat(str(value).replace("Z", "+00:00"))
            except ValueError:
                errors[field] = f"{field} must be a valid ISO timestamp"

    starts = payload.get("registrationStartsAt") or ""
    ends = payload.get("registrationEndsAt") or ""
    if starts and ends and "registrationEndsAt" not in errors and str(starts) > str(ends):
        errors["registrationEndsAt"] = "Registration end must be after the start"

    for field, label in (
        ("dinnerPriceAdults", "Adult dinner price"),
        ("dinnerPriceKids914", "Kid (9-14) dinner price"),
        ("dinnerPriceKids68", "Kid (6-8) dinner price"),
        # No dinnerPriceKidsUnder6 — that category is never priced, only counted.
    ):
        if field in payload:
            try:
                if int(payload[field] or 0) < 0:
                    raise ValueError
            except (TypeError, ValueError):
                errors[field] = f"{label} must be a non-negative whole number"

    if "addOnDiscountPercent" in payload:
        try:
            discount = int(payload["addOnDiscountPercent"])
            if not (0 <= discount <= 100):
                raise ValueError
        except (TypeError, ValueError):
            errors["addOnDiscountPercent"] = "Add-on discount must be a whole number between 0 and 100"

    if "maxTotalGuests" in payload:
        try:
            if int(payload["maxTotalGuests"] or 0) < 0:
                raise ValueError
        except (TypeError, ValueError):
            errors["maxTotalGuests"] = "Maximum total guests must be a non-negative whole number (0 = unlimited)"

    if "paymentMethods" in payload:
        methods = payload["paymentMethods"]
        if not isinstance(methods, list) or not all(isinstance(m, str) for m in methods):
            errors["paymentMethods"] = "Payment methods must be a list of strings"
        elif not [m for m in methods if m in dinner.PAYMENT_PROVIDERS or m == "bank"]:
            errors["paymentMethods"] = "At least one payment method must be selected"

    return errors


def handle_registration_item(http_method: str, event_type: str, registration_id: str, event: dict) -> dict:
    if http_method != "PATCH":
        return respond(405, {"error": "Method not allowed"})
    if not registration_id:
        return respond(400, {"error": "registrationId path parameter is required"})

    payload = parse_json_body(event)
    status = str(payload.get("status", "")).strip().lower()
    if status not in dinner.VALID_DINNER_STATUSES:
        return respond(400, {
            "error": "Validation failed",
            "fields": {"status": f"Status must be one of: {', '.join(sorted(dinner.VALID_DINNER_STATUSES))}"},
        })

    existing = dinner.get_dinner_registration(event_type, registration_id)
    if not existing:
        return respond(404, {"error": f"Celebrity Dinner registration {registration_id} not found"})
    prior_status = str(existing.get("status", "pending")).lower()

    try:
        updated = dinner.update_dinner_registration_status(event_type, registration_id, status)
        # An admin marking a bank-transfer (or otherwise unconfirmed) booking
        # paid by hand still deserves the same registrant confirmation email
        # the payment-provider paths send automatically.
        if status == "paid" and prior_status != "paid":
            dinner_emailer.send_paid_email(updated)
        # Finalizing a cancellation — whether from a still-unpaid booking or
        # from a refund_requested one an admin has confirmed the money
        # actually went back on — is always worth a registrant notice.
        if status == "cancelled" and prior_status != "cancelled":
            dinner_emailer.send_cancelled_email(updated)
        return respond(200, {
            "message": "Celebrity Dinner registration updated",
            "registration": dinner.normalize_dinner_registration(updated),
        })
    except ValueError as exc:
        return respond(400, {"error": str(exc)})
    except RuntimeError as exc:
        print(f"Error updating Celebrity Dinner registration={registration_id} for event={event_type}: {exc}")
        return respond(500, {"error": "Failed to update Celebrity Dinner registration"})


def lambda_handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    http_method = event.get("httpMethod")

    if http_method == "OPTIONS":
        return respond(200, {})

    if not dinner.CONFIG_TABLE:
        return respond(500, {"error": "Celebrity Dinner config table not configured"})

    path_params = event.get("pathParameters") or {}
    event_type = str(path_params.get("eventType", "")).strip()
    resource = event.get("resource", "") or ""

    if not event_type:
        return respond(400, {"error": "eventType path parameter is required"})

    if "/registrations/" in resource:
        registration_id = str(path_params.get("registrationId", "")).strip()
        return handle_registration_item(http_method, event_type, registration_id, event)

    if resource.endswith("/registrations"):
        if http_method != "GET":
            return respond(405, {"error": "Method not allowed"})
        try:
            standalone = dinner.list_dinner_registrations_for_event(event_type)
            for r in standalone:
                r["source"] = "standalone"
            # Guests booked through the seat-upgrade plugin's bundled add-on
            # — same physical dinner, previously invisible on this page (an
            # admin could only see them from the Zone Upgrades tab, and the
            # capacity math below didn't add up without them either — see
            # total_active_dinner_guest_count).
            bundled = dinner.list_bundled_dinner_guests_for_event(event_type)
            registrations = sorted(standalone + bundled, key=lambda r: r.get("submittedAt", ""), reverse=True)
            active_guests = dinner.total_active_dinner_guest_count(event_type)
            max_total_guests = dinner.normalize_config(dinner.get_config(event_type), event_type).get("maxTotalGuests", 0)
        except RuntimeError as exc:
            print(f"Error listing Celebrity Dinner registrations: {exc}")
            return respond(500, {"error": "Failed to load Celebrity Dinner registrations"})
        def _guests(r):
            return (
                r["numberOfAdultsVeg"] + r["numberOfAdultsNonVeg"]
                + r["numberOfKids914Veg"] + r["numberOfKids914NonVeg"]
                + r["numberOfKids68Veg"] + r["numberOfKids68NonVeg"]
                + r["numberOfKidsUnder6Veg"] + r["numberOfKidsUnder6NonVeg"]
            )

        # Active = still holding a seat (pending/paid); everything else —
        # refund_requested (already cancelled by the registrant) and
        # cancelled — is split out so the admin summary can show both.
        active = [r for r in registrations if dinner.is_seat_holding_status(r.get("status"))]
        cancelled = [r for r in registrations if not dinner.is_seat_holding_status(r.get("status"))]
        total_guests = sum(_guests(r) for r in registrations)
        total_amount = sum(r["totalAmount"] for r in registrations)
        return respond(
            200,
            {
                "registrations": registrations,
                "count": len(registrations),
                "activeCount": len(active),
                "cancelledCount": len(cancelled),
                "totalGuests": total_guests,
                "activeGuests": sum(_guests(r) for r in active),
                "cancelledGuests": sum(_guests(r) for r in cancelled),
                "totalAmount": total_amount,
                "maxTotalGuests": max_total_guests,
                "remainingCapacity": max(max_total_guests - active_guests, 0) if max_total_guests > 0 else None,
            },
        )

    if http_method == "GET":
        try:
            item = dinner.get_config(event_type)
            enabled = dinner.is_event_celebrity_dinner_enabled(event_type)
        except RuntimeError as exc:
            print(f"Error fetching Celebrity Dinner config: {exc}")
            return respond(500, {"error": "Failed to load Celebrity Dinner config"})
        return respond(200, {"config": dinner.normalize_config(item, event_type, enabled)})

    if http_method == "PUT":
        payload = parse_json_body(event)
        errors = validate_config_payload(payload)
        if errors:
            return respond(400, {"error": "Validation failed", "fields": errors})

        try:
            existing = dinner.get_config(event_type)
        except RuntimeError as exc:
            print(f"Error fetching Celebrity Dinner config: {exc}")
            return respond(500, {"error": "Failed to load Celebrity Dinner config"})

        now = dinner.now_iso()
        record = {
            "eventType": event_type,
            "registrationStartsAt": str(payload.get("registrationStartsAt", (existing or {}).get("registrationStartsAt", "")) or "").strip(),
            "registrationEndsAt": str(payload.get("registrationEndsAt", (existing or {}).get("registrationEndsAt", "")) or "").strip(),
            # When the dinner itself happens — distinct from the registration
            # window above. See celebrity_dinner_logic.normalize_config.
            "dinnerDate": str(payload.get("dinnerDate", (existing or {}).get("dinnerDate", "")) or "").strip(),
            "venue": str(payload.get("venue", (existing or {}).get("venue", "")) or "").strip(),
            "venueAddress": str(payload.get("venueAddress", (existing or {}).get("venueAddress", "")) or "").strip(),
            "googleMapUrl": str(payload.get("googleMapUrl", (existing or {}).get("googleMapUrl", "")) or "").strip(),
            # Age-tiered pricing — shared identically by the standalone
            # booking (this form) and the seat-upgrade plugin's own bundled
            # add-on. See celebrity_dinner_logic.normalize_config's docstring.
            # Kids under 6 have no price field at all — a headcount only.
            "dinnerPriceAdults": int(payload.get("dinnerPriceAdults", (existing or {}).get("dinnerPriceAdults", 0)) or 0),
            "dinnerPriceKids914": int(payload.get("dinnerPriceKids914", (existing or {}).get("dinnerPriceKids914", 0)) or 0),
            "dinnerPriceKids68": int(payload.get("dinnerPriceKids68", (existing or {}).get("dinnerPriceKids68", 0)) or 0),
            "currency": str(payload.get("currency", (existing or {}).get("currency", "JPY")) or "JPY").strip(),
            "conditions": str(payload.get("conditions", (existing or {}).get("conditions", "")) or "").strip(),
            "notes": str(payload.get("notes", (existing or {}).get("notes", "")) or "").strip(),
            "addOnEnabled": bool(payload.get("addOnEnabled", (existing or {}).get("addOnEnabled", False))),
            # Reserved for a future add-on-specific discount — stored, not
            # currently applied to any computed total. See normalize_config.
            "addOnDiscountPercent": int(payload.get("addOnDiscountPercent", (existing or {}).get("addOnDiscountPercent", 0)) or 0),
            "paymentMethods": list(payload.get("paymentMethods", (existing or {}).get("paymentMethods", dinner.DEFAULT_PAYMENT_METHODS))),
            # See celebrity_dinner_logic.normalize_config/cancellation_allowed.
            "cancellationEnabled": bool(payload.get("cancellationEnabled", (existing or {}).get("cancellationEnabled", True))),
            "refundWindowEndsAt": str(payload.get("refundWindowEndsAt", (existing or {}).get("refundWindowEndsAt", "")) or "").strip(),
            "maxTotalGuests": int(payload.get("maxTotalGuests", (existing or {}).get("maxTotalGuests", 0)) or 0),
            "createdAt": (existing or {}).get("createdAt", now),
            "updatedAt": now,
        }

        try:
            dinner.dynamodb.Table(dinner.CONFIG_TABLE).put_item(Item=record)
            enabled = dinner.is_event_celebrity_dinner_enabled(event_type)
        except ClientError as exc:
            print(f"Error saving Celebrity Dinner config: {exc}")
            return respond(500, {"error": "Failed to save Celebrity Dinner config"})

        return respond(200, {"config": dinner.normalize_config(record, event_type, enabled)})

    return respond(405, {"error": "Method not allowed"})
