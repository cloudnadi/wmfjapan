"""
Seat Selection Plugin — Lambda: GET /events/{eventType}/seats
Public endpoint (no auth) — lets a caller view the seat map for one
hall/eventType before picking seats.

Response: the full hall layout (rows of seats, each with its zone category)
plus which seats are already taken (booked or admin-blocked), and per-zone
totals/availability for a summary header. The layout is fixed (see
seat_selection_logic._ROW_SPECS) — every eventType shares the same hall.
"""

import json
from decimal import Decimal
from typing import Any

import seat_selection_logic


def decimal_to_python(obj):
    if isinstance(obj, Decimal):
        return int(obj) if obj == obj.to_integral_value() else float(obj)
    raise TypeError(f"Object of type {type(obj)} is not JSON serializable")


def cors_headers():
    return {
        "Access-Control-Allow-Origin": "*",
        "Access-Control-Allow-Headers": "Content-Type,Authorization",
        "Access-Control-Allow-Methods": "OPTIONS,GET",
        "Content-Type": "application/json",
    }


def respond(status: int, body: dict):
    return {
        "statusCode": status,
        "headers": cors_headers(),
        "body": json.dumps(body, default=decimal_to_python),
    }


def lambda_handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    if event.get("httpMethod") == "OPTIONS":
        return respond(200, {})

    if event.get("httpMethod") != "GET":
        return respond(405, {"error": "Only GET is allowed"})

    path_params = event.get("pathParameters") or {}
    event_type = str(path_params.get("eventType", "")).strip()
    if not event_type:
        return respond(400, {"error": "eventType path parameter is required"})

    overrides = seat_selection_logic.get_category_overrides(event_type)
    layout = seat_selection_logic.generate_seat_layout(overrides)
    occupied = seat_selection_logic.get_occupied_seats(event_type)
    booked_seat_ids = sorted(occupied.keys())

    # Seats blocked with a camera/projector tag aren't selectable (same
    # as any other blocked seat, via bookedSeatIds above) but get their own
    # icon instead of the plain "blocked" swatch.
    special_seats = {
        seat_id: item["blockCategory"]
        for seat_id, item in occupied.items()
        if item.get("status") == "BLOCKED" and item.get("blockCategory")
    }

    zone_totals = seat_selection_logic.get_zone_totals(overrides)
    zone_available = dict(zone_totals)
    for row in layout:
        for seat in row["seats"]:
            if seat["seatId"] in occupied:
                zone_available[seat["category"]] -= 1

    return respond(
        200,
        {
            "eventType": event_type,
            "totalSeats": seat_selection_logic.TOTAL_SEATS,
            "layout": layout,
            "bookedSeatIds": booked_seat_ids,
            "specialSeats": special_seats,
            "zoneTotals": zone_totals,
            "zoneAvailable": zone_available,
            "zonePricing": seat_selection_logic.get_zone_pricing(event_type),
            "walkwayAfterRow": seat_selection_logic.WALKWAY_AFTER_ROW,
            "available": True,
        },
    )
