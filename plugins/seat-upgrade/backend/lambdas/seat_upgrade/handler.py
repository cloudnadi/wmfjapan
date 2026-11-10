"""
Lambda: registrant self-service seat upgrades for the Celebrity Event.

Routes (API Gateway proxy) — gated in-Lambda by the reg token minted on OTP
verify (Authorization header), the same convention as my_registrations:

  GET    /my/seat-upgrades           -> upgrade options + requests for every
                                         eligible registration the caller owns
  POST   /my/seat-upgrades           -> request an upgrade for one of them
  GET    /my/seat-upgrades/{id}      -> one request's current status (also
                                         reconciles it against the payment
                                         provider if a checkout is pending)
  DELETE /my/seat-upgrades/{id}      -> cancel one's own request: a pending
                                         one outright, a paid one into
                                         refund_requested (attempts a
                                         provider refund — see attempt_refund)
  POST   /my/seat-upgrades/{id}/checkout
                                      -> start a PayPay or Square hosted
                                         checkout for a pending request

Only registrations for the Celebrity Event (registrationOption "event" or
"combo") that are paid can be upgraded — the upgrade is priced and reserved
independently of the original registration, and is never written into the
registrations table.

Bank transfer has no checkout step here: it's account details shown in the
UI (unchanged) and an admin confirming payment by hand, same as a
registration's own paymentStatus. PayPay and Square go through
provider-hosted checkout (paypay.py / square.py) and get confirmed either by
the async webhook (see the seat_upgrade_webhook Lambda) or, defense in depth,
by this Lambda re-checking the provider directly when the registrant returns
to /my/seat-upgrade or refreshes it.
"""

import base64
import hashlib
import hmac
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

import paypay
import seat_upgrade_emailer
import seat_upgrade_logic
import square

dynamodb = boto3.resource("dynamodb", region_name=os.environ["REGION"])

REGISTRATIONS_TABLE = os.environ["DYNAMODB_TABLE"]
EVENTS_TABLE = os.environ.get("EVENTS_TABLE", "")
TOKEN_SECRET = os.environ.get("OTP_TOKEN_SECRET", "")
FRONTEND_URL = os.environ.get("FRONTEND_URL", "")
# Optional: the host's standalone Celebrity Dinner registrations table (see
# lambdas/celebrity_dinner_logic.py). Only set when that host-level feature
# is deployed — blank skips the cross-check below entirely.
CELEBRITY_DINNER_REGISTRATIONS_TABLE = os.environ.get("CELEBRITY_DINNER_REGISTRATIONS_TABLE", "")
# Optional: the Celebrity Dinner plugin's own per-event config table (price,
# discount, addOnEnabled, ...) — see get_celebrity_dinner_addon_config. This
# module owns none of that configuration itself; it only reads it. Blank
# (Celebrity Dinner plugin not deployed) means the add-on is never offered.
CELEBRITY_DINNER_CONFIG_TABLE = os.environ.get("CELEBRITY_DINNER_CONFIG_TABLE", "")

ELIGIBLE_OPTIONS = {"event", "combo"}

PAYMENT_ADAPTERS = {"paypay": paypay, "square": square}

logger = logging.getLogger(__name__)
logger.setLevel(os.environ.get("LOG_LEVEL", "INFO").upper())


def decimal_to_python(obj):
    if isinstance(obj, Decimal):
        return int(obj) if obj == obj.to_integral_value() else float(obj)
    raise TypeError(f"Object of type {type(obj)} is not JSON serializable")


def cors_headers(origin="*"):
    return {
        "Access-Control-Allow-Origin": origin,
        "Access-Control-Allow-Headers": "Content-Type,Authorization",
        "Access-Control-Allow-Methods": "OPTIONS,GET,POST,DELETE",
        "Content-Type": "application/json",
    }


def respond(status: int, body: dict, origin: str = "*"):
    return {
        "statusCode": status,
        "headers": cors_headers(origin),
        "body": json.dumps(body, default=decimal_to_python),
    }


def parse_json_body(event: dict) -> dict:
    if event.get("isBase64Encoded"):
        return {}
    raw_body = event.get("body") or "{}"
    try:
        parsed = json.loads(raw_body)
        return parsed if isinstance(parsed, dict) else {}
    except json.JSONDecodeError:
        return {}


def _b64url_decode(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode(value + padding)


def verify_token(token: str) -> Optional[dict]:
    """Return the payload dict if the reg token is valid & unexpired, else None."""
    try:
        payload_part, sig_part = token.split(".", 1)
        payload_b = _b64url_decode(payload_part)
        expected = hmac.new(TOKEN_SECRET.encode("utf-8"), payload_b, hashlib.sha256).digest()
        if not hmac.compare_digest(expected, _b64url_decode(sig_part)):
            return None
        payload = json.loads(payload_b)
        if int(payload.get("exp", 0)) < int(time.time()):
            return None
        return payload
    except Exception:
        return None


def normalize_tier(item: dict) -> dict:
    capacity = int(item.get("capacity", 0) or 0)
    sold = int(item.get("upgradedSeats", 0) or 0)
    return {
        "tierId": item.get("tierId", ""),
        "name": item.get("name", ""),
        "description": item.get("description", ""),
        "upgradePrice": int(item.get("upgradePrice", 0) or 0),
        "remainingCapacity": max(capacity - sold, 0),
        "mapRegion": item.get("mapRegion"),
    }


def normalize_upgrade(item: dict) -> dict:
    return {
        "upgradeId": item.get("upgradeId", ""),
        "registrationId": item.get("registrationId", ""),
        "eventType": item.get("eventType", ""),
        "tierName": item.get("tierName", ""),
        "zoneBreakdown": [{
            "tierId": z.get("tierId", ""),
            "tierName": z.get("tierName", ""),
            "count": int(z.get("count", 0) or 0),
            "regularUnitPrice": int(z.get("regularUnitPrice", 0) or 0),
            "subtotal": int(z.get("subtotal", 0) or 0),
        } for z in item.get("zoneBreakdown", [])],
        "seatCount": int(item.get("seatCount", 0) or 0),
        "upgradeAmount": int(item.get("upgradeAmount", 0) or 0),
        "totalAmount": int(item.get("totalAmount", 0) or 0),
        "status": item.get("status", "pending"),
        "paymentMethod": item.get("paymentMethod", ""),
        "paymentProvider": item.get("paymentProvider", ""),
        "checkoutUrl": item.get("checkoutUrl", ""),
        "refundStatus": item.get("refundStatus", ""),
        **{field: int(item.get(field, 0) or 0) for field in seat_upgrade_logic.DINNER_GUEST_FIELDS},
        "dinnerPriceAdults": int(item.get("dinnerPriceAdults", 0) or 0),
        "dinnerPriceKids914": int(item.get("dinnerPriceKids914", 0) or 0),
        "dinnerPriceKids68": int(item.get("dinnerPriceKids68", 0) or 0),
        "dinnerAmount": int(item.get("dinnerAmount", 0) or 0),
        "requestedAt": item.get("requestedAt", ""),
    }


def required_seats(reg: dict) -> int:
    """Mirrors lambdas/entry_slots_logic.py's calculate_required_seats — a
    kid below 6 occupies no seat, so isn't counted here. This is what
    seatsInParty (and therefore how many zones a registration can upgrade
    into) is based on."""
    counts = reg.get("attendeeCounts") or {}
    try:
        return (
            int(counts.get("adults", 0) or 0)
            + int(counts.get("kids914", 0) or 0)
            + int(counts.get("kids68", 0) or 0)
        )
    except (TypeError, ValueError):
        return 0


def dinner_eligible_seats(reg: dict) -> int:
    """Every party member is eligible for the Celebrity Dinner, priced by
    age category — see dinner_eligible_by_category."""
    adults, kids914, kids68, kids_under6 = dinner_eligible_by_category(reg)
    return adults + kids914 + kids68 + kids_under6


def dinner_eligible_by_category(reg: dict) -> tuple:
    """(adultsEligible, kids914Eligible, kids68Eligible, kidsUnder6Eligible)
    for the Celebrity Dinner — each age category is priced and capped
    separately, matching the standalone Celebrity Dinner booking's own
    per-category eligibility (see lambdas/celebrity_dinner_logic.py's
    eligible_guest_counts_by_category, which this mirrors)."""
    counts = reg.get("attendeeCounts") or {}
    try:
        return (
            int(counts.get("adults", 0) or 0),
            int(counts.get("kids914", 0) or 0),
            int(counts.get("kids68", 0) or 0),
            int(counts.get("kidsUnder6", 0) or 0),
        )
    except (TypeError, ValueError):
        return 0, 0, 0, 0


def has_active_standalone_celebrity_dinner(registration_id: str) -> bool:
    """True if `registration_id` already has the host's standalone Celebrity
    Dinner sign-up active. Keeps the two dinner paths — this bundled 50%-off
    add-on, or the standalone full-price registration — mutually exclusive.
    Always False when that feature isn't deployed (table env var unset).
    """
    if not CELEBRITY_DINNER_REGISTRATIONS_TABLE or not registration_id:
        return False
    try:
        table = dynamodb.Table(CELEBRITY_DINNER_REGISTRATIONS_TABLE)
        response = table.query(
            IndexName="RegistrationIdIndex",
            KeyConditionExpression=Key("registrationId").eq(registration_id),
        )
        return any(
            str(item.get("status", "ACTIVE")).upper() == "ACTIVE"
            for item in response.get("Items", [])
        )
    except ClientError:
        logger.exception("Could not check standalone Celebrity Dinner registration for %s", registration_id)
        # Fail open rather than block a purchase over a read error — an
        # admin can still catch a double-dip from the two admin views if
        # this ever actually races with the read failing.
        return False


def get_event_name(event_type: str) -> str:
    if not EVENTS_TABLE or not event_type:
        return event_type
    try:
        item = dynamodb.Table(EVENTS_TABLE).get_item(Key={"eventType": event_type}).get("Item")
        return (item or {}).get("eventName", event_type)
    except ClientError:
        return event_type


DEFAULT_SEAT_UPGRADE_PAYMENT_METHODS = ["paypay", "square", "bank"]


def get_seat_upgrade_config(event_type: str) -> dict:
    """Read this event's admin-configured seat-upgrade settings from
    eventInfo.seatUpgrade — {"enabled": bool, "paymentMethods": [...],
    "seatMapImageUrl": str, "startDate": str, "endDate": str}.

    The plugin is opt-in per event (see frontend/src/AdminDashboard.jsx's
    Events form): an event with no seatUpgrade block, or `enabled` not
    exactly `true`, has the feature off entirely. Once enabled, an event
    that has never had paymentMethods configured defaults to offering all
    three, matching the plugin's original always-on behavior. A seat map
    image is mandatory when enabling from the admin form, but this reads
    back whatever's actually stored without re-enforcing that here.

    startDate/endDate are optional "YYYY-MM-DD" strings (blank = no bound
    on that side) gating the registrant-facing booking window — see
    window_status().
    """
    empty = {
        "enabled": False,
        "paymentMethods": DEFAULT_SEAT_UPGRADE_PAYMENT_METHODS,
        "seatMapImageUrl": "",
        "startDate": "",
        "endDate": "",
    }
    if not EVENTS_TABLE or not event_type:
        return empty
    try:
        item = dynamodb.Table(EVENTS_TABLE).get_item(Key={"eventType": event_type}).get("Item") or {}
    except ClientError:
        logger.exception("Could not read seat upgrade config for event=%s", event_type)
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

    methods = raw.get("paymentMethods")
    if isinstance(methods, list):
        methods = [m for m in methods if m in DEFAULT_SEAT_UPGRADE_PAYMENT_METHODS]
    if not methods:
        methods = DEFAULT_SEAT_UPGRADE_PAYMENT_METHODS
    return {
        "enabled": raw.get("enabled") is True,
        "paymentMethods": methods,
        "seatMapImageUrl": raw.get("seatMapImageUrl") if isinstance(raw.get("seatMapImageUrl"), str) else "",
        "startDate": raw.get("startDate") if isinstance(raw.get("startDate"), str) else "",
        "endDate": raw.get("endDate") if isinstance(raw.get("endDate"), str) else "",
    }


def _is_event_celebrity_dinner_enabled(event_type: str) -> bool:
    """Mirrors lambdas/celebrity_dinner_logic.py's
    is_event_celebrity_dinner_enabled() exactly — duplicated rather than
    imported since the two Lambdas ship in separate deployment packages
    (same "copied module" convention as seat_upgrade_emailer.py). This is
    the Celebrity Dinner plugin's own per-event on/off switch; it says
    nothing about whether the add-on bundle specifically is offered — see
    get_celebrity_dinner_addon_config for that.
    """
    if not EVENTS_TABLE or not event_type:
        return False
    try:
        item = dynamodb.Table(EVENTS_TABLE).get_item(Key={"eventType": event_type}).get("Item")
    except ClientError:
        return False
    if not item:
        return False
    event_info = item.get("eventInfo", {})
    if isinstance(event_info, str):
        try:
            event_info = json.loads(event_info) if event_info.strip() else {}
        except json.JSONDecodeError:
            event_info = {}
    if not isinstance(event_info, dict):
        return False
    celebrity_dinner = event_info.get("celebrityDinner", {})
    return isinstance(celebrity_dinner, dict) and celebrity_dinner.get("enabled") is True


def get_celebrity_dinner_addon_config(event_type: str) -> dict:
    """{"enabled", "dinnerPriceAdults", "dinnerPriceKids914",
    "dinnerPriceKids68", "maxTotalGuests"} for the Celebrity Dinner add-on
    bundle on this event. No dinnerPriceKidsUnder6 — that category is never
    priced, only counted (see create_upgrade_request).

    This module owns none of that configuration — price, venue, guest-age
    condition, etc. all live on the Celebrity Dinner plugin (see
    lambdas/celebrity_dinner_logic.py and the admin's Celebrity Dinner tab,
    frontend/src/CelebrityDinnerManagement.jsx). `enabled` here requires
    BOTH that the Celebrity Dinner plugin is turned on for this event
    (eventInfo.celebrityDinner.enabled) AND that its own config has
    explicitly opted into being offered as a seat-upgrade add-on
    (addOnEnabled) — an event can run standalone Celebrity Dinner sign-ups
    without ever exposing this bundle, and vice versa isn't possible
    (addOnEnabled without the plugin being enabled at all is meaningless).
    Always disabled when the Celebrity Dinner plugin isn't deployed
    (CELEBRITY_DINNER_CONFIG_TABLE unset).

    The add-on is priced using the exact same age-tiered prices
    (dinnerPriceAdults/Kids914/Kids68) as the standalone Celebrity Dinner
    booking — never discounted under the Family Upgrade Discount.
    addOnDiscountPercent is stored on the config but not yet applied here
    (reserved for a future add-on-specific discount).
    """
    empty = {
        "enabled": False,
        "dinnerPriceAdults": 0,
        "dinnerPriceKids914": 0,
        "dinnerPriceKids68": 0,
        "maxTotalGuests": 0,
    }
    if not CELEBRITY_DINNER_CONFIG_TABLE or not event_type:
        return empty
    if not _is_event_celebrity_dinner_enabled(event_type):
        return empty
    try:
        item = dynamodb.Table(CELEBRITY_DINNER_CONFIG_TABLE).get_item(Key={"eventType": event_type}).get("Item") or {}
    except ClientError:
        logger.exception("Could not read Celebrity Dinner add-on config for event=%s", event_type)
        return empty
    if item.get("addOnEnabled") is not True:
        return empty
    try:
        return {
            "enabled": True,
            "dinnerPriceAdults": max(int(item.get("dinnerPriceAdults", 0) or 0), 0),
            "dinnerPriceKids914": max(int(item.get("dinnerPriceKids914", 0) or 0), 0),
            "dinnerPriceKids68": max(int(item.get("dinnerPriceKids68", 0) or 0), 0),
            # Overall headcount cap shared with the host's standalone
            # Celebrity Dinner sign-ups — see total_active_celebrity_dinner_guests.
            # 0 = unlimited.
            "maxTotalGuests": max(int(item.get("maxTotalGuests", 0) or 0), 0),
        }
    except (TypeError, ValueError):
        return empty


# The statuses whose guests hold a Celebrity Dinner seat — mirrors
# lambdas/celebrity_dinner_logic.py's SEAT_HOLDING_DINNER_STATUSES. Narrower
# than the "active" set used for the one-booking-per-person checks: a
# refund_requested booking (standalone or bundled) has already been
# cancelled by the registrant, so its guests no longer take up capacity.
_SEAT_HOLDING_DINNER_STATUSES = {"pending", "paid"}


def total_active_celebrity_dinner_guests(event_type: str, exclude_upgrade_id: str = "") -> int:
    """Total guests (all 8 veg/non-veg category counts, kids under 6
    included — this is a seating/headcount cap, not a billing figure)
    across every active Celebrity Dinner booking for this event: the host's
    standalone sign-ups (CELEBRITY_DINNER_REGISTRATIONS_TABLE) AND this
    plugin's own bundled add-on (SEAT_UPGRADES_TABLE) combined, since both
    feed the same physical dinner. Mirrors lambdas/celebrity_dinner_logic.py's
    own total_active_dinner_guest_count (duplicated per the usual "copied
    module" convention — separate deployment packages).

    `exclude_upgrade_id`, when given, omits that one upgrade request from
    the bundled half of the count — used when re-validating a request being
    resubmitted so it doesn't count against itself.
    """
    if not event_type:
        return 0
    total = 0
    if CELEBRITY_DINNER_REGISTRATIONS_TABLE:
        try:
            table = dynamodb.Table(CELEBRITY_DINNER_REGISTRATIONS_TABLE)
            response = table.query(KeyConditionExpression=Key("eventType").eq(event_type))
            for item in response.get("Items", []):
                if str(item.get("status", "pending")).lower() not in _SEAT_HOLDING_DINNER_STATUSES:
                    continue
                total += (
                    int(item.get("numberOfAdultsVeg", 0) or 0) + int(item.get("numberOfAdultsNonVeg", 0) or 0)
                    + int(item.get("numberOfKids914Veg", 0) or 0) + int(item.get("numberOfKids914NonVeg", 0) or 0)
                    + int(item.get("numberOfKids68Veg", 0) or 0) + int(item.get("numberOfKids68NonVeg", 0) or 0)
                    + int(item.get("numberOfKidsUnder6Veg", 0) or 0) + int(item.get("numberOfKidsUnder6NonVeg", 0) or 0)
                )
        except ClientError:
            logger.exception("Could not count standalone Celebrity Dinner guests for event=%s", event_type)
    for item in seat_upgrade_logic.list_upgrades_for_event(event_type):
        if item.get("upgradeId") == exclude_upgrade_id:
            continue
        if str(item.get("status", "")).lower() not in _SEAT_HOLDING_DINNER_STATUSES:
            continue
        total += sum(int(item.get(field, 0) or 0) for field in seat_upgrade_logic.DINNER_GUEST_FIELDS)
    return total


def window_status(config: dict) -> str:
    """"not_started" | "open" | "ended", from today's UTC date against the
    admin-configured startDate/endDate (both optional, inclusive). Dates are
    plain calendar days, not timestamps — same YYYY-MM-DD convention as
    eventDate elsewhere in the app — compared in UTC like every other
    timestamp this plugin writes (see seat_upgrade_logic.now_iso).
    """
    today = datetime.now(timezone.utc).date().isoformat()
    start = config.get("startDate") or ""
    end = config.get("endDate") or ""
    if start and today < start:
        return "not_started"
    if end and today > end:
        return "ended"
    return "open"


def eligible_registrations(email: str) -> list:
    """Every paid Celebrity Event registration owned by `email`."""
    try:
        resp = dynamodb.Table(REGISTRATIONS_TABLE).query(
            IndexName="EmailIndex",
            KeyConditionExpression=Key("email").eq(email),
        )
        items = resp.get("Items", [])
    except ClientError:
        logger.exception("Could not list registrations for email=%s", email)
        return []

    eligible = []
    for reg in items:
        option = reg.get("registrationOption", "")
        if option not in ELIGIBLE_OPTIONS:
            continue
        if str(reg.get("paymentStatus", "")).lower() != "paid":
            continue
        eligible.append(reg)
    return eligible


def handle_get(email: str, origin: str):
    registrations = eligible_registrations(email)
    results = []
    for reg in registrations:
        event_type = reg.get("eventType", "")
        seat_upgrade_config = get_seat_upgrade_config(event_type)
        if not seat_upgrade_config["enabled"]:
            continue
        window = window_status(seat_upgrade_config)
        # Outside the booking window, no new zones are offered — but a
        # registration that already has requests still shows them (and can
        # still cancel one), so tiers is the only thing gated here.
        tiers = [
            normalize_tier(t)
            for t in seat_upgrade_logic.list_active_tiers_for_event(event_type)
        ] if window == "open" else []
        upgrades = [
            normalize_upgrade(u)
            for u in seat_upgrade_logic.list_upgrades_for_registration(reg["registrationId"])
        ]
        already_upgraded = sum(
            u["seatCount"] for u in upgrades if u["status"] in seat_upgrade_logic.ACTIVE_UPGRADE_STATUSES
        )
        active_upgrades = [u for u in upgrades if u["status"] in seat_upgrade_logic.ACTIVE_UPGRADE_STATUSES]
        already_dinner_adult = sum(u["dinnerAdultVegGuests"] + u["dinnerAdultNonVegGuests"] for u in active_upgrades)
        already_dinner_kids914 = sum(u["dinnerKids914VegGuests"] + u["dinnerKids914NonVegGuests"] for u in active_upgrades)
        already_dinner_kids68 = sum(u["dinnerKids68VegGuests"] + u["dinnerKids68NonVegGuests"] for u in active_upgrades)
        already_dinner_kids_under6 = sum(u["dinnerKidsUnder6VegGuests"] + u["dinnerKidsUnder6NonVegGuests"] for u in active_upgrades)
        dinner_addon_config = get_celebrity_dinner_addon_config(event_type)
        dinner_adult_eligible, dinner_kids914_eligible, dinner_kids68_eligible, dinner_kids_under6_eligible = (
            dinner_eligible_by_category(reg)
        )
        # Event-wide headcount cap, on top of this registration's own
        # per-category eligibility above — whichever is smaller wins. Only
        # queried when a cap is actually set, to skip the extra cross-table
        # scan otherwise. This clamps each category independently, which is
        # an approximation when a cap is shared across categories (picking
        # right up to the clamped max in several categories at once could
        # still combine to exceed it) — handle_post's own check below is the
        # authoritative enforcement; this is just so the form doesn't offer
        # more than is realistically available.
        dinner_event_remaining = None
        if dinner_addon_config["enabled"] and dinner_addon_config["maxTotalGuests"] > 0:
            dinner_event_remaining = max(
                dinner_addon_config["maxTotalGuests"] - total_active_celebrity_dinner_guests(event_type), 0
            )

        def _dinner_remaining(value):
            return value if dinner_event_remaining is None else min(value, dinner_event_remaining)

        results.append({
            "registrationId": reg["registrationId"],
            "eventType": event_type,
            "eventName": get_event_name(event_type),
            "registrantName": reg.get("name", ""),
            "seatsInParty": required_seats(reg),
            "seatsAlreadyUpgraded": already_upgraded,
            "tiers": tiers,
            "upgrades": upgrades,
            "enabledPaymentMethods": seat_upgrade_config["paymentMethods"],
            "seatMapImageUrl": seat_upgrade_config["seatMapImageUrl"],
            # The Family Upgrade Discount: the registration's first
            # fullPriceSeatCount upgraded seats (across every active
            # purchase, not just a new one) are full price; the rest are
            # familyDiscountPercent off — see seat_upgrade_logic.price_seat_picks.
            "familyDiscountPercent": seat_upgrade_logic.FAMILY_DISCOUNT_PERCENT,
            "fullPriceSeatCount": seat_upgrade_logic.FULL_PRICE_SEAT_COUNT,
            "dinnerEnabled": dinner_addon_config["enabled"],
            "dinnerPriceAdults": dinner_addon_config["dinnerPriceAdults"],
            "dinnerPriceKids914": dinner_addon_config["dinnerPriceKids914"],
            "dinnerPriceKids68": dinner_addon_config["dinnerPriceKids68"],
            # Remaining dinner guests this registration can still add, by
            # category — never discounted either way.
            "dinnerAdultRemaining": _dinner_remaining(max(dinner_adult_eligible - already_dinner_adult, 0)),
            "dinnerKids914Remaining": _dinner_remaining(max(dinner_kids914_eligible - already_dinner_kids914, 0)),
            "dinnerKids68Remaining": _dinner_remaining(max(dinner_kids68_eligible - already_dinner_kids68, 0)),
            "dinnerKidsUnder6Remaining": _dinner_remaining(max(dinner_kids_under6_eligible - already_dinner_kids_under6, 0)),
            # Only checked when the dinner add-on is even offered for this
            # event — skips a needless cross-table query otherwise. Lets the
            # frontend hide/explain the add-on up front instead of the
            # registrant only finding out via handle_post's 400 on submit —
            # see has_active_standalone_celebrity_dinner's docstring for why
            # the two dinner paths are mutually exclusive.
            "hasStandaloneCelebrityDinner": (
                has_active_standalone_celebrity_dinner(reg["registrationId"])
                if dinner_addon_config["enabled"] else False
            ),
            "upgradeWindow": {
                "status": window,
                "startDate": seat_upgrade_config["startDate"],
                "endDate": seat_upgrade_config["endDate"],
            },
        })
    return respond(200, {"registrations": results}, origin)


def handle_post(email: str, body: dict, origin: str):
    registration_id = str(body.get("registrationId", "")).strip()
    payment_method = str(body.get("paymentMethod", "")).strip()
    raw_picks = body.get("picks")

    def _parse_dinner_count(field):
        try:
            return int(body.get(field, 0) or 0)
        except (TypeError, ValueError):
            return 0

    dinner_guest_counts = {field: _parse_dinner_count(field) for field in seat_upgrade_logic.DINNER_GUEST_FIELDS}
    dinner_opt_in = any(v > 0 for v in dinner_guest_counts.values())

    if not registration_id:
        return respond(400, {"error": "registrationId is required"}, origin)
    if not isinstance(raw_picks, list) or not raw_picks:
        return respond(400, {"error": "picks (a list of {tierId, count}) is required"}, origin)
    if any(v < 0 for v in dinner_guest_counts.values()):
        return respond(400, {"error": "Dinner guest counts must be non-negative"}, origin)

    try:
        reg = dynamodb.Table(REGISTRATIONS_TABLE).get_item(Key={"registrationId": registration_id}).get("Item")
    except ClientError:
        logger.exception("Registration lookup error for id=%s", registration_id)
        return respond(500, {"error": "Failed to load registration"}, origin)

    if not reg or str(reg.get("email", "")).strip().lower() != email:
        return respond(403, {"error": "You can only upgrade your own registrations"}, origin)
    if reg.get("registrationOption") not in ELIGIBLE_OPTIONS:
        return respond(400, {"error": "This registration is not eligible for a zone upgrade"}, origin)
    if str(reg.get("paymentStatus", "")).lower() != "paid":
        return respond(400, {"error": "This registration must be paid before requesting a zone upgrade"}, origin)

    event_type = reg.get("eventType", "")
    seat_upgrade_config = get_seat_upgrade_config(event_type)
    if not seat_upgrade_config["enabled"]:
        return respond(400, {"error": "Zone upgrades are not enabled for this event"}, origin)
    window = window_status(seat_upgrade_config)
    if window == "not_started":
        return respond(400, {
            "error": f"Zone upgrades for this event open on {seat_upgrade_config['startDate']}."
        }, origin)
    if window == "ended":
        return respond(400, {
            "error": f"The zone upgrade window for this event closed on {seat_upgrade_config['endDate']}."
        }, origin)
    if payment_method and payment_method not in seat_upgrade_config["paymentMethods"]:
        return respond(400, {"error": f"{payment_method} is not an available payment method for this event"}, origin)

    # Resolve and validate every zone pick before touching capacity.
    picks = []
    total_new_seats = 0
    for raw_pick in raw_picks:
        if not isinstance(raw_pick, dict):
            return respond(400, {"error": "Each pick must be an object with tierId and count"}, origin)
        tier_id = str(raw_pick.get("tierId", "")).strip()
        try:
            count = int(raw_pick.get("count", 0))
        except (TypeError, ValueError):
            count = 0
        if not tier_id or count <= 0:
            return respond(400, {"error": "Each pick needs a tierId and a positive count"}, origin)
        tier = seat_upgrade_logic.get_tier(event_type, tier_id)
        if not tier or str(tier.get("status", "")).upper() != "ACTIVE":
            return respond(404, {"error": "Upgrade zone not found or no longer available"}, origin)
        picks.append({"tier": tier, "count": count})
        total_new_seats += count

    seats_in_party = required_seats(reg)
    existing_upgraded_count = seat_upgrade_logic.get_active_upgraded_unit_count(registration_id)
    if existing_upgraded_count + total_new_seats > seats_in_party:
        return respond(400, {
            "error": f"You can upgrade at most {max(seats_in_party - existing_upgraded_count, 0)} more seat(s) "
                     f"on this registration ({seats_in_party} seat(s) in the party)."
        }, origin)

    dinner_price_adults = 0
    dinner_price_kids914 = 0
    dinner_price_kids68 = 0
    if dinner_opt_in:
        dinner_addon_config = get_celebrity_dinner_addon_config(event_type)
        if not dinner_addon_config["enabled"]:
            return respond(400, {"error": "The Celebrity Dinner add-on isn't available for this event"}, origin)
        if has_active_standalone_celebrity_dinner(registration_id):
            return respond(400, {
                "error": "This registration already has a standalone Celebrity Dinner registration. "
                         "Cancel it first if you'd rather bundle the dinner with this zone upgrade instead.",
                "conflictsWithStandaloneDinner": True,
            }, origin)
        dinner_price_adults = dinner_addon_config["dinnerPriceAdults"]
        dinner_price_kids914 = dinner_addon_config["dinnerPriceKids914"]
        dinner_price_kids68 = dinner_addon_config["dinnerPriceKids68"]

        eligible = dict(zip(
            ("adults", "kids914", "kids68", "kidsUnder6"),
            dinner_eligible_by_category(reg),
        ))
        already_by_field = seat_upgrade_logic.get_active_dinner_guest_counts(registration_id)
        already = {
            "adults": already_by_field["dinnerAdultVegGuests"] + already_by_field["dinnerAdultNonVegGuests"],
            "kids914": already_by_field["dinnerKids914VegGuests"] + already_by_field["dinnerKids914NonVegGuests"],
            "kids68": already_by_field["dinnerKids68VegGuests"] + already_by_field["dinnerKids68NonVegGuests"],
            "kidsUnder6": already_by_field["dinnerKidsUnder6VegGuests"] + already_by_field["dinnerKidsUnder6NonVegGuests"],
        }
        requested = {
            "adults": dinner_guest_counts["dinnerAdultVegGuests"] + dinner_guest_counts["dinnerAdultNonVegGuests"],
            "kids914": dinner_guest_counts["dinnerKids914VegGuests"] + dinner_guest_counts["dinnerKids914NonVegGuests"],
            "kids68": dinner_guest_counts["dinnerKids68VegGuests"] + dinner_guest_counts["dinnerKids68NonVegGuests"],
            "kidsUnder6": dinner_guest_counts["dinnerKidsUnder6VegGuests"] + dinner_guest_counts["dinnerKidsUnder6NonVegGuests"],
        }
        category_labels = {
            "adults": "adult",
            "kids914": "kid (9-14)",
            "kids68": "kid (6-8)",
            "kidsUnder6": "kid (under 6)",
        }
        for category, count in requested.items():
            if count > 0 and already[category] + count > eligible[category]:
                return respond(400, {
                    "error": f"You can add at most {max(eligible[category] - already[category], 0)} more "
                             f"{category_labels[category]} Celebrity Dinner guest(s) on this registration."
                }, origin)

        # Event-wide headcount cap, checked last so the per-category errors
        # above (which point at this registration's own eligibility) take
        # priority over a shared-capacity message. Authoritative — the
        # per-category clamping in handle_get is only an approximation of
        # this shared total (see its own comment).
        if dinner_addon_config["maxTotalGuests"] > 0:
            others_total = total_active_celebrity_dinner_guests(event_type)
            remaining = dinner_addon_config["maxTotalGuests"] - others_total
            requested_total = sum(requested.values())
            if requested_total > remaining:
                return respond(400, {
                    "error": f"Only {max(remaining, 0)} Celebrity Dinner seat(s) left "
                             f"(capacity {dinner_addon_config['maxTotalGuests']} reached).",
                    "remainingCapacity": max(remaining, 0),
                }, origin)

    try:
        upgrade = seat_upgrade_logic.create_upgrade_request(
            event_type=event_type,
            picks=picks,
            registration_id=registration_id,
            email=email,
            name=reg.get("name", ""),
            existing_upgraded_count=existing_upgraded_count,
            payment_method=payment_method,
            dinner_guest_counts=dinner_guest_counts,
            dinner_price_adults=dinner_price_adults,
            dinner_price_kids914=dinner_price_kids914,
            dinner_price_kids68=dinner_price_kids68,
        )
    except ValueError as exc:
        return respond(400, {"error": str(exc)}, origin)
    except RuntimeError as exc:
        return respond(409, {"error": str(exc)}, origin)

    seat_upgrade_emailer.send_request_email(upgrade)
    seat_upgrade_emailer.send_admin_notification(upgrade, "requested")
    return respond(201, {"upgrade": normalize_upgrade(upgrade)}, origin)


def _load_owned_upgrade(email: str, upgrade_id: str):
    """Fetch an upgrade and confirm the signed-in email owns it.
    Returns (item, error_response); exactly one is non-None."""
    existing = seat_upgrade_logic.get_upgrade(upgrade_id)
    if not existing:
        return None, respond(404, {"error": "Zone upgrade request not found"})
    if str(existing.get("email", "")).strip().lower() != email:
        return None, respond(403, {"error": "You can only manage your own zone upgrade requests"})
    return existing, None


def reconcile_with_provider(upgrade: dict) -> dict:
    """If a checkout is pending, ask the provider directly whether it's
    actually completed yet — the registrant returning to this page (or just
    refreshing it) shouldn't have to wait on the webhook alone. Safe to call
    repeatedly: mark_paid_from_provider() is idempotent.
    """
    if upgrade.get("status") != "pending":
        return upgrade
    provider = upgrade.get("paymentProvider", "")
    provider_payment_id = upgrade.get("providerPaymentId", "")
    adapter = PAYMENT_ADAPTERS.get(provider)
    if not adapter or not provider_payment_id:
        return upgrade

    try:
        result = adapter.get_payment_status(provider_payment_id)
    except Exception:
        logger.exception("Could not check %s payment status for upgrade %s", provider, upgrade.get("upgradeId"))
        return upgrade

    completed_statuses = {"COMPLETED"}  # both paypay.py and square.py normalize to this string on success
    if result.get("status") in completed_statuses:
        updated, was_newly_paid = seat_upgrade_logic.mark_paid_from_provider(
            provider_payment_id, payment_reference=result.get("paymentId", "")
        )
        if updated:
            if was_newly_paid:
                seat_upgrade_emailer.send_paid_email(updated)
                seat_upgrade_emailer.send_admin_notification(updated, "paid")
            return updated
    return upgrade


def handle_get_one(email: str, upgrade_id: str, origin: str):
    existing, err = _load_owned_upgrade(email, upgrade_id)
    if err:
        return err
    existing = reconcile_with_provider(existing)
    return respond(200, {"upgrade": normalize_upgrade(existing)}, origin)


def handle_checkout(email: str, upgrade_id: str, body: dict, origin: str):
    existing, err = _load_owned_upgrade(email, upgrade_id)
    if err:
        return err

    provider = str(body.get("provider", "")).strip().lower()
    adapter = PAYMENT_ADAPTERS.get(provider)
    if not adapter:
        return respond(400, {"error": "provider must be 'paypay' or 'square'"}, origin)
    seat_upgrade_config = get_seat_upgrade_config(existing.get("eventType", ""))
    if provider not in seat_upgrade_config["paymentMethods"]:
        return respond(400, {"error": f"{provider.title()} is not an available payment method for this event"}, origin)
    if existing.get("status") != "pending":
        return respond(400, {"error": "This request is no longer awaiting payment"}, origin)
    if not adapter.is_configured():
        return respond(503, {
            "error": f"{provider.title()} isn't configured yet. Please use bank transfer, "
                     "or contact us to complete this payment."
        }, origin)

    upgrade_id_ = existing["upgradeId"]
    description = f"{existing.get('tierName', 'Zone upgrade')} — {existing.get('name', '')}"[:255]
    redirect_url = f"{FRONTEND_URL.rstrip('/')}/my/seat-upgrade?upgradeId={upgrade_id_}&provider={provider}"

    try:
        if provider == "paypay":
            checkout = adapter.create_checkout(
                merchant_payment_id=upgrade_id_,  # our own id — no extra lookup needed on the paypay side
                amount_yen=existing.get("totalAmount", 0),
                description=description,
                redirect_url=redirect_url,
            )
        else:
            checkout = adapter.create_checkout(
                reference_id=upgrade_id_,
                amount_yen=existing.get("totalAmount", 0),
                description=description,
                redirect_url=redirect_url,
            )
    except RuntimeError as exc:
        logger.exception("Could not create %s checkout for upgrade %s", provider, upgrade_id_)
        return respond(502, {"error": f"Could not start {provider.title()} checkout: {exc}"}, origin)

    updated = seat_upgrade_logic.record_checkout(
        upgrade_id_,
        provider=provider,
        provider_payment_id=checkout["providerPaymentId"],
        checkout_url=checkout["checkoutUrl"],
    )
    return respond(200, {
        "checkoutUrl": updated.get("checkoutUrl", checkout["checkoutUrl"]),
        "provider": provider,
    }, origin)


def attempt_refund(upgrade: dict) -> dict:
    """Best-effort provider refund for a just-cancelled paid upgrade. Always
    records an outcome (even "needs manual handling") so the admin dashboard
    shows something actionable regardless of what happens here — a failed or
    skipped auto-refund is never silent, it just falls back to the same
    admin-confirms flow bank transfer already uses.

    Returns the upgrade record (refreshed with refund bookkeeping fields) and
    never raises: a refund-attempt failure must not break the cancellation
    that already succeeded.
    """
    upgrade_id = upgrade["upgradeId"]
    provider = upgrade.get("paymentProvider", "")
    payment_id = upgrade.get("paymentReference", "")
    adapter = PAYMENT_ADAPTERS.get(provider)

    if not adapter or not adapter.is_configured() or not payment_id:
        try:
            return seat_upgrade_logic.record_refund_attempt(upgrade_id, refund_status="manual_pending")
        except RuntimeError:
            logger.exception("Could not record manual refund status for upgrade %s", upgrade_id)
            return upgrade

    try:
        if provider == "paypay":
            result = adapter.create_refund(
                payment_id=payment_id,
                merchant_refund_id=f"{upgrade_id}-refund",
                amount_yen=upgrade.get("totalAmount", 0),
                reason="Customer requested cancellation",
            )
        else:
            result = adapter.create_refund(
                payment_id=payment_id,
                idempotency_key=f"{upgrade_id}-refund",
                amount_yen=upgrade.get("totalAmount", 0),
                reason="Customer requested cancellation",
            )
        return seat_upgrade_logic.record_refund_attempt(
            upgrade_id,
            refund_status=f"provider_{str(result.get('status', 'pending')).lower()}",
            refund_reference=result.get("refundId", ""),
        )
    except Exception as exc:
        logger.exception("Could not initiate %s refund for upgrade %s", provider, upgrade_id)
        try:
            return seat_upgrade_logic.record_refund_attempt(
                upgrade_id, refund_status="provider_failed", refund_error=str(exc)
            )
        except RuntimeError:
            return upgrade


def handle_delete(email: str, upgrade_id: str, origin: str):
    existing = seat_upgrade_logic.get_upgrade(upgrade_id)
    if not existing:
        return respond(404, {"error": "Zone upgrade request not found"}, origin)
    if str(existing.get("email", "")).strip().lower() != email:
        return respond(403, {"error": "You can only cancel your own zone upgrade requests"}, origin)
    try:
        updated = seat_upgrade_logic.cancel_upgrade_request(upgrade_id, registration_id=existing.get("registrationId", ""))
    except (ValueError, PermissionError) as exc:
        return respond(400, {"error": str(exc)}, origin)

    if updated.get("status") == "refund_requested":
        updated = attempt_refund(updated)
        auto_initiated = str(updated.get("refundStatus", "")).startswith("provider_")
        seat_upgrade_emailer.send_refund_requested_email(updated, auto_initiated=auto_initiated)
        seat_upgrade_emailer.send_admin_notification(
            updated, "refund requested",
            extra_note=f"- Refund status: {updated.get('refundStatus', 'N/A')} (reference: {updated.get('refundReference', 'none')})",
        )
    else:
        seat_upgrade_emailer.send_admin_notification(updated, "cancelled")

    return respond(200, {"upgrade": normalize_upgrade(updated)}, origin)


def lambda_handler(event: dict, context: Any) -> dict:
    origin = "*"
    http_method = event.get("httpMethod")

    if http_method == "OPTIONS":
        return respond(200, {}, origin)

    headers = event.get("headers") or {}
    token = headers.get("Authorization") or headers.get("authorization") or ""
    payload = verify_token(token) if token else None
    if not payload or not payload.get("email"):
        return respond(401, {"error": "Please sign in again"}, origin)
    email = str(payload["email"]).strip().lower()

    path_params = event.get("pathParameters") or {}
    upgrade_id = str(path_params.get("upgradeId", "")).strip()
    resource_path = str(event.get("resource") or event.get("path") or "")
    is_checkout_route = resource_path.endswith("/checkout")

    if http_method == "GET":
        if upgrade_id:
            return handle_get_one(email, upgrade_id, origin)
        return handle_get(email, origin)
    if http_method == "POST":
        if is_checkout_route:
            if not upgrade_id:
                return respond(400, {"error": "upgradeId path parameter is required"}, origin)
            return handle_checkout(email, upgrade_id, parse_json_body(event), origin)
        return handle_post(email, parse_json_body(event), origin)
    if http_method == "DELETE":
        if not upgrade_id:
            return respond(400, {"error": "upgradeId path parameter is required"}, origin)
        return handle_delete(email, upgrade_id, origin)

    return respond(405, {"error": "Method not allowed"}, origin)
