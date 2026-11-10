"""
Seat Selection Plugin — Lambda: /admin/events/{eventType}/seats[...]
Protected by Cognito JWT authorizer.

Admin visibility and light maintenance for the seat map:
  GET    /admin/events/{eventType}/seats           -> full layout + occupancy
  POST   /admin/events/{eventType}/seats            -> body {seatId, reason?, category?}
                                                        category in zone1/zone2/zone3/regular
                                                          -> cosmetic reassignment, stays bookable
                                                        category in camera/projector
                                                          -> blocks the seat with that icon tag
                                                        no category -> plain generic block
                                                          (broken chair, AV blind spot, ...)
  DELETE /admin/events/{eventType}/seats/{seatId}   -> reset a seat to its hall
                                                        default (unblock + clear any override)

Seats actually booked by a registration are not touched here — release those
through the host app's own delete/cancel flow for that registration (calling
seat_selection_logic.release_seats directly), which keeps the registrationId
link consistent instead of orphaning it.

Also handles per-zone pricing, dispatched by path (same Lambda, a sibling
API Gateway resource — see the module docstring in seat_selection_logic.py):
  GET    /admin/events/{eventType}/seat-pricing                -> all priced zones
  PUT    /admin/events/{eventType}/seat-pricing/{category}      -> body
                                                                    {displayName, pricePerFamily,
                                                                     pricePerPerson, maxFamilySize?, status?}
  DELETE /admin/events/{eventType}/seat-pricing/{category}      -> unprice (not for sale again)

And purchase request management (mark bank-transfer paid, confirm a refund
done — same admin-confirms pattern seat-upgrade uses):
  GET   /admin/events/{eventType}/seat-purchases[/{purchaseId}]    -> list/get
  PATCH /admin/events/{eventType}/seat-purchases/{purchaseId}       -> body {status}
                                                                        pending|paid|cancelled|refund_requested

And the door check-in scan (not event-scoped — a scanned QR carries only
its own token, see api_gateway.tf):
  POST  /admin/seat-check-in    -> body {token, action: "resolve"|"check_in"}
"""

import json
from decimal import Decimal
from typing import Any

import seat_selection_logic
import seat_purchase_logic
import seat_purchase_emailer


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


def respond(status: int, body: dict):
    return {
        "statusCode": status,
        "headers": cors_headers(),
        "body": json.dumps(body, default=decimal_to_python),
    }


def parse_json_body(event: dict[str, Any]) -> dict[str, Any]:
    if event.get("isBase64Encoded"):
        return {}
    try:
        parsed = json.loads(event.get("body") or "{}")
        return parsed if isinstance(parsed, dict) else {}
    except json.JSONDecodeError:
        return {}


def normalize_seat(seat: dict, item: dict | None) -> dict:
    if not item:
        return {
            "seatId": seat["seatId"],
            "category": seat["category"],
            "block": seat["block"],
            "status": "AVAILABLE",
        }
    return {
        "seatId": seat["seatId"],
        "category": seat["category"],
        "block": seat["block"],
        "status": item.get("status", "BOOKED"),
        "registrationId": item.get("registrationId", ""),
        "holderName": item.get("holderName", ""),
        "reason": item.get("reason", ""),
        "blockCategory": item.get("blockCategory", ""),
        "bookedAt": item.get("bookedAt", ""),
        "updatedAt": item.get("updatedAt", ""),
    }


def list_seats(event_type: str):
    total_seats = seat_selection_logic.TOTAL_SEATS
    overrides = seat_selection_logic.get_category_overrides(event_type)
    layout = seat_selection_logic.generate_seat_layout(overrides)
    occupied = seat_selection_logic.get_occupied_seats(event_type)

    booked = sum(1 for item in occupied.values() if item.get("status") == "BOOKED")
    blocked = sum(1 for item in occupied.values() if item.get("status") == "BLOCKED")

    rows = [
        {
            "row": row["row"],
            "blankEdges": row["blankEdges"],
            "seats": [normalize_seat(seat, occupied.get(seat["seatId"])) for seat in row["seats"]],
        }
        for row in layout
    ]

    return respond(
        200,
        {
            "eventType": event_type,
            "totalSeats": total_seats,
            "bookedSeats": booked,
            "blockedSeats": blocked,
            "availableSeats": max(total_seats - booked - blocked, 0),
            "walkwayAfterRow": seat_selection_logic.WALKWAY_AFTER_ROW,
            "rows": rows,
            "zonePricing": seat_selection_logic.get_zone_pricing(event_type),
        },
    )


def list_zone_pricing(event_type: str):
    return respond(200, {"eventType": event_type, "pricing": seat_selection_logic.get_zone_pricing(event_type)})


def upsert_zone_price(event_type: str, category: str, payload: dict):
    if category not in seat_selection_logic.PRICEABLE_CATEGORIES:
        return respond(
            400,
            {"error": f"Unknown category '{category}'. Valid: {', '.join(seat_selection_logic.PRICEABLE_CATEGORIES)}"},
        )
    display_name = str(payload.get("displayName", "")).strip()
    status = str(payload.get("status", "ACTIVE")).strip().upper()
    try:
        price_per_family = int(payload.get("pricePerFamily", 0))
        price_per_person = int(payload.get("pricePerPerson", 0))
        max_family_size = int(payload.get("maxFamilySize", 0))
    except (TypeError, ValueError):
        return respond(400, {"error": "pricePerFamily/pricePerPerson/maxFamilySize must be numbers"})

    try:
        item = seat_selection_logic.set_zone_price(
            event_type, category, display_name, price_per_family, price_per_person, max_family_size, status
        )
    except (ValueError, RuntimeError) as exc:
        return respond(400, {"error": str(exc)})
    return respond(200, {"message": "Zone price saved", "price": item})


def delete_zone_price(event_type: str, category: str):
    seat_selection_logic.delete_zone_price(event_type, category)
    return respond(200, {"message": "Zone unpriced"})


def normalize_purchase(item: dict) -> dict:
    return {
        "purchaseId": item.get("purchaseId", ""),
        "registrationId": item.get("registrationId", ""),
        "eventType": item.get("eventType", ""),
        "email": item.get("email", ""),
        "name": item.get("name", ""),
        "seatIds": item.get("seatIds", []),
        "pricingMode": item.get("pricingMode", "person"),
        "breakdown": item.get("breakdown", []),
        "totalAmount": item.get("totalAmount", 0),
        "pricingEnabled": item.get("pricingEnabled", True),
        "status": item.get("status", "pending"),
        "paymentMethod": item.get("paymentMethod", ""),
        "paymentProvider": item.get("paymentProvider", ""),
        "paymentReference": item.get("paymentReference", ""),
        "refundStatus": item.get("refundStatus", ""),
        "refundReference": item.get("refundReference", ""),
        "requestedAt": item.get("requestedAt", ""),
        "updatedAt": item.get("updatedAt", ""),
        "checkedIn": item.get("checkedIn", False),
        "checkedInAt": item.get("checkedInAt", ""),
        "checkedInBy": item.get("checkedInBy", ""),
    }


def list_seat_purchases(event_type: str, purchase_id: str):
    if purchase_id:
        item = seat_purchase_logic.get_purchase(purchase_id)
        if not item or item.get("eventType") != event_type:
            return respond(404, {"error": f"Seat purchase {purchase_id} not found"})
        return respond(200, {"purchase": normalize_purchase(item)})
    try:
        items = seat_purchase_logic.list_purchases_for_event(event_type)
        return respond(200, {"purchases": [normalize_purchase(i) for i in items], "count": len(items)})
    except RuntimeError as exc:
        return respond(500, {"error": "Failed to list seat purchases", "detail": str(exc)})


def update_seat_purchase_status(event_type: str, purchase_id: str, payload: dict):
    status = str(payload.get("status", "")).strip().lower()
    if status not in seat_purchase_logic.VALID_STATUSES:
        return respond(400, {"error": f"status must be one of: {', '.join(seat_purchase_logic.VALID_STATUSES)}"})
    existing = seat_purchase_logic.get_purchase(purchase_id)
    if not existing or existing.get("eventType") != event_type:
        return respond(404, {"error": f"Seat purchase {purchase_id} not found"})
    prior_status = existing.get("status")
    try:
        updated = seat_purchase_logic.update_purchase_status(purchase_id, status)
    except (ValueError, RuntimeError) as exc:
        return respond(400, {"error": str(exc)})

    # An admin marking a bank-transfer (or otherwise unconfirmed) request
    # paid by hand still deserves the same registrant confirmation email
    # the payment-provider paths send automatically.
    if status == "paid" and prior_status != "paid":
        seat_purchase_emailer.send_paid_email(updated)
    # Finalizing a cancellation — whether from a still-unpaid request or
    # from a refund_requested one an admin has confirmed the money actually
    # went back on — is always worth a registrant notice.
    if status == "cancelled" and prior_status != "cancelled":
        seat_purchase_emailer.send_cancelled_email(updated)

    return respond(200, {"message": "Seat purchase updated", "purchase": normalize_purchase(updated)})


def handle_seat_check_in(payload: dict, event: dict):
    """POST /admin/seat-check-in — body {token, action}.

    action="resolve" just looks the ticket up (for the door screen to show
    before anyone taps a button); action="check_in" atomically consumes it.
    Mirrors this host's own /admin/check-in (entry-pass) route's
    resolve/check_in split, but against seat_purchase_logic's own
    checkInToken — a wholly separate ticket from that mechanism's
    registrationPassToken (see seat_purchase_qr.py's module docstring for
    why they're kept independent).
    """
    token = str(payload.get("token", "")).strip()
    action = str(payload.get("action", "resolve")).strip().lower()
    if not token:
        return respond(400, {"error": "Check-in token is required"})
    if action not in {"resolve", "check_in"}:
        return respond(400, {"error": "Unsupported check-in action"})

    claims = ((event.get("requestContext") or {}).get("authorizer") or {}).get("claims") or {}
    checked_in_by = str(claims.get("email") or claims.get("username") or "admin").strip()

    try:
        if action == "check_in":
            purchase = seat_purchase_logic.check_in_purchase(token, checked_in_by=checked_in_by)
        else:
            purchase = seat_purchase_logic.get_purchase_by_check_in_token(token)
            if not purchase:
                raise ValueError("Invalid check-in code")
    except ValueError as exc:
        status = 404 if str(exc) == "Invalid check-in code" else 409
        return respond(status, {"error": str(exc)})
    except RuntimeError as exc:
        return respond(500, {"error": str(exc)})

    # Same consolidated view as the guest check-in ticket PDF this purchase's
    # QR is attached to, so the door screen shows a registrant's zone
    # upgrade / celebrity dinner add-ons too, not just their seats.
    addons = seat_purchase_emailer.gather_addons(purchase.get("registrationId", ""))

    return respond(200, {"purchase": normalize_purchase(purchase), "addons": addons})


def lambda_handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    http_method = event.get("httpMethod")

    if http_method == "OPTIONS":
        return respond(200, {})

    path_params = event.get("pathParameters") or {}
    event_type = str(path_params.get("eventType", "")).strip()
    seat_id = str(path_params.get("seatId", "")).strip()
    category = str(path_params.get("category", "")).strip().lower()
    purchase_id = str(path_params.get("purchaseId", "")).strip()
    resource = event.get("resource") or event.get("path") or ""

    # Not event-scoped — a scanned check-in QR carries only its own token,
    # so this route sits directly under /admin (see api_gateway.tf), not
    # /admin/events/{eventType}, and must be handled before the eventType
    # guard below.
    if "/seat-check-in" in resource:
        if http_method != "POST":
            return respond(405, {"error": "Method not allowed"})
        return handle_seat_check_in(parse_json_body(event), event)

    if not event_type:
        return respond(400, {"error": "eventType path parameter is required"})

    if "/seat-pricing" in resource:
        if http_method == "GET":
            return list_zone_pricing(event_type)
        if http_method == "PUT":
            if not category:
                return respond(400, {"error": "category path parameter is required"})
            return upsert_zone_price(event_type, category, parse_json_body(event))
        if http_method == "DELETE":
            if not category:
                return respond(400, {"error": "category path parameter is required"})
            return delete_zone_price(event_type, category)
        return respond(405, {"error": "Method not allowed"})

    if "/seat-purchases" in resource:
        if http_method == "GET":
            return list_seat_purchases(event_type, purchase_id)
        if http_method == "PATCH":
            if not purchase_id:
                return respond(400, {"error": "purchaseId path parameter is required"})
            return update_seat_purchase_status(event_type, purchase_id, parse_json_body(event))
        return respond(405, {"error": "Method not allowed"})

    if http_method == "GET":
        return list_seats(event_type)

    if http_method == "POST":
        payload = parse_json_body(event)
        target_seat = str(payload.get("seatId", "")).strip().upper()
        reason = str(payload.get("reason", "")).strip()
        category = str(payload.get("category", "")).strip().lower()
        if not target_seat:
            return respond(400, {"error": "seatId is required"})

        if not seat_selection_logic.validate_seat_ids([target_seat]):
            return respond(400, {"error": f"{target_seat} is not a valid seat for this event"})

        all_categories = seat_selection_logic.ZONE_OVERRIDE_CATEGORIES + seat_selection_logic.NON_BOOKABLE_CATEGORIES
        if category and category not in all_categories:
            return respond(400, {"error": f"Unknown category '{category}'. Valid: {', '.join(all_categories)}"})

        if category in seat_selection_logic.ZONE_OVERRIDE_CATEGORIES:
            # Bookable reassignment (Zone 1/2/3/Regular) — cosmetic only,
            # never touches the event_seats table, so a booked seat can
            # still have its zone corrected without releasing it.
            try:
                seat_selection_logic.set_category_override(event_type, target_seat, category)
            except (ValueError, RuntimeError) as exc:
                return respond(400, {"error": str(exc)})
            overrides = seat_selection_logic.get_category_overrides(event_type)
            seat_info = seat_selection_logic.find_seat(target_seat, overrides)
            occupied_item = seat_selection_logic.get_occupied_seats(event_type).get(target_seat)
            return respond(200, {"message": "Seat category updated", "seat": normalize_seat(seat_info, occupied_item)})

        # Non-bookable block (camera/projector) or a plain generic block
        # (no category — a broken chair, an AV blind spot).
        block_category = category if category in seat_selection_logic.NON_BOOKABLE_CATEGORIES else ""
        try:
            item = seat_selection_logic.block_seat(event_type, target_seat, reason, block_category)
        except ValueError as exc:
            return respond(409, {"error": str(exc)})
        except RuntimeError as exc:
            return respond(500, {"error": str(exc)})

        overrides = seat_selection_logic.get_category_overrides(event_type)
        seat_info = seat_selection_logic.find_seat(target_seat, overrides)
        return respond(201, {"message": "Seat blocked", "seat": normalize_seat(seat_info, item)})

    if http_method == "DELETE":
        if not seat_id:
            return respond(400, {"error": "seatId path parameter is required for DELETE"})
        try:
            seat_selection_logic.unblock_seat(event_type, seat_id)
        except ValueError as exc:
            return respond(409, {"error": str(exc)})
        except RuntimeError as exc:
            return respond(500, {"error": str(exc)})
        return respond(200, {"message": "Seat unblocked"})

    return respond(405, {"error": "Method not allowed"})
