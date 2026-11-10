"""
Celebrity Dinner add-on: shared config/eligibility/CRUD/payment helpers.

The Celebrity Dinner is usually an opt-in add-on layered on top of an
existing registration — it never reads or writes the registrations table's
own attendee/payment fields, only reads `attendeeCounts`/`registrationType`
off a registration to compute how many guests that registration is eligible
to bring. It can also stand entirely on its own: someone with no event
registration at all can book a "guest" dinner sign-up, identified by email
instead — see get_active_guest_dinner_registration. A guest booking is the
same row shape with `registrationId` left blank; nothing else in this module
treats registrationId as anything more than an optional attribute.

Eligibility rule: a registration's eligible guest counts mirror its own
attendee counts, one cap per age category (adults, kids 9-14, kids 6-8, kids
under 6) — a booking can never request more guests in a category than the
registration actually has in that category. A guest booking (no
registration) has no such cap. Dinner pricing is age-tiered to match (see
normalize_config's dinnerPriceAdults/Kids914/Kids68/KidsUnder6), shared
identically by this standalone sign-up and the seat-upgrade plugin's own
bundled add-on.

Payment: a standalone sign-up (this module) carries its own PayPay/Square/
bank-transfer flow through pending -> paid -> [refund_requested] -> cancelled,
mirroring plugins/seat-upgrade/backend/lambdas/seat_upgrade_logic.py's own
upgrade-request lifecycle field-for-field. The seat-upgrade plugin's *bundled*
add-on (dinnerGuests > 0 on a seat_upgrades row) has no payment lifecycle of
its own here — it's paid for as part of that seat-upgrade request's own
totalAmount/checkout, never through this module.
"""

import json
import os
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Optional

import boto3
from botocore.exceptions import ClientError
from boto3.dynamodb.conditions import Key, Attr

dynamodb = boto3.resource("dynamodb", region_name=os.environ.get("REGION"))
CONFIG_TABLE = os.environ.get("CELEBRITY_DINNER_CONFIG_TABLE", "")
# The host events table — the Celebrity Dinner on/off switch lives on the
# event itself (eventInfo.celebrityDinner.enabled, set from the Events admin
# form), not in CONFIG_TABLE, so the admin dashboard can gate the "Celebrity
# Dinner" tab's visibility per event the same way it does for the
# seat-upgrade plugin, without an extra per-event API call.
EVENTS_TABLE_NAME = os.environ.get("EVENTS_TABLE", "")
REGISTRATIONS_TABLE_NAME = os.environ.get("CELEBRITY_DINNER_REGISTRATIONS_TABLE", "")
# Optional: the seat-upgrade plugin's own upgrade-requests table (plugins/
# seat-upgrade/backend/lambdas/seat_upgrade_logic.py's SEAT_UPGRADES_TABLE).
# Only my_registrations sets this — see backend/lambda.tf — so the standalone
# sign-up here and the seat-upgrade plugin's own bundled dinner add-on can
# never both be active for the same registration. Blank when the plugin
# isn't deployed, in which case the cross-check is simply skipped.
SEAT_UPGRADES_TABLE = os.environ.get("SEAT_UPGRADES_TABLE", "")

# "refund_requested" sits between paid and cancelled, same as the seat-upgrade
# plugin's own lifecycle — a self-service cancel of an already-paid booking
# lands here first (see cancel_dinner_registration) and still counts as
# "active" (has_active_seat_upgrade_dinner's mirror image, and the exclusion
# check against a second sign-up) until an admin finalizes it to cancelled
# once the refund has actually gone through.
VALID_DINNER_STATUSES = {"pending", "paid", "refund_requested", "cancelled"}
ACTIVE_DINNER_STATUSES = {"pending", "paid", "refund_requested"}
# Mirrors plugins/seat-upgrade/backend/lambdas/seat_upgrade_logic.py's
# ACTIVE_UPGRADE_STATUSES — duplicated rather than imported since the two
# Lambdas ship in separate deployment packages (same "copied constant"
# convention the seat-upgrade plugin itself uses for its own duplicated code).
ACTIVE_SEAT_UPGRADE_STATUSES = {"pending", "paid", "refund_requested"}
# The narrower set that actually holds a seat against maxTotalGuests. A
# refund_requested booking is one the registrant has already cancelled (its
# email says so) — it stays "active" above for the one-booking-per-person
# checks until an admin finalizes the refund, but its guests must not keep
# eating dinner capacity (or show up in the admin's Seats Left) meanwhile.
SEAT_HOLDING_DINNER_STATUSES = {"pending", "paid"}
SEAT_HOLDING_SEAT_UPGRADE_STATUSES = {"pending", "paid"}


def is_seat_holding_status(status) -> bool:
    """True for a booking (standalone or bundled — both use the same status
    names) whose guests count against the dinner's seat capacity."""
    return str(status or "pending").lower() in SEAT_HOLDING_DINNER_STATUSES

PAYMENT_PROVIDERS = {"paypay", "square"}
DEFAULT_PAYMENT_METHODS = ["paypay", "square", "bank"]


def _is_conditional_check_failed(exc: ClientError) -> bool:
    return exc.response.get("Error", {}).get("Code") == "ConditionalCheckFailedException"


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def decimal_to_python(obj):
    if isinstance(obj, Decimal):
        return int(obj) if obj == obj.to_integral_value() else float(obj)
    raise TypeError(f"Object of type {type(obj)} is not JSON serializable")


# ── Eligible guest count ──────────────────────────────────────────────────


def eligible_guest_counts_by_category(registration: dict) -> dict:
    """{"adults", "kids914", "kids68", "kidsUnder6"} — how many Celebrity
    Dinner guests a registration is eligible to bring, per age category.
    Mirrors the registration's own attendeeCounts schema (Onam:
    adults/kids914/kids68/kidsUnder6; a legacy non-Onam schema uses
    adult/teen/child with no separate under-6 bucket, mapped onto the same
    three categories it actually has).
    """
    counts = registration.get("attendeeCounts", {}) or {}
    if registration.get("registrationType") == "onam" or "adults" in counts:
        return {
            "adults": int(counts.get("adults", 0) or 0),
            "kids914": int(counts.get("kids914", 0) or 0),
            "kids68": int(counts.get("kids68", 0) or 0),
            "kidsUnder6": int(counts.get("kidsUnder6", 0) or 0),
        }
    return {
        "adults": int(counts.get("adult", 0) or 0),
        "kids914": int(counts.get("teen", 0) or 0),
        "kids68": int(counts.get("child", 0) or 0),
        "kidsUnder6": 0,
    }


def eligible_guest_count(registration: dict) -> int:
    """Total across every category — see eligible_guest_counts_by_category."""
    return sum(eligible_guest_counts_by_category(registration).values())


# ── Config (per event) ───────────────────────────────────────────────────


def is_event_celebrity_dinner_enabled(event_type: str) -> bool:
    """Read the per-event on/off switch from eventInfo.celebrityDinner.enabled
    (set via the Events admin form — see frontend/src/AdminDashboard.jsx's
    getCelebrityDinnerConfigFromEventInfo). This is the single source of
    truth for whether the feature is on for an event; CONFIG_TABLE only
    stores the registration window and dinner details.
    """
    if not EVENTS_TABLE_NAME or not event_type:
        return False
    try:
        item = dynamodb.Table(EVENTS_TABLE_NAME).get_item(Key={"eventType": event_type}).get("Item")
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


def normalize_config(item: Optional[dict], event_type: str, enabled: bool = False) -> dict:
    item = item or {}
    methods = item.get("paymentMethods")
    if isinstance(methods, list):
        methods = [m for m in methods if m in DEFAULT_PAYMENT_METHODS]
    if not methods:
        methods = DEFAULT_PAYMENT_METHODS
    return {
        "eventType": event_type,
        "enabled": enabled,
        "registrationStartsAt": item.get("registrationStartsAt", ""),
        "registrationEndsAt": item.get("registrationEndsAt", ""),
        # When the dinner itself actually happens — distinct from the
        # registration window above, which only bounds when people can
        # sign up. ISO timestamp; empty means not yet set by the admin.
        "dinnerDate": item.get("dinnerDate", ""),
        "venue": item.get("venue", ""),
        "venueAddress": item.get("venueAddress", ""),
        "googleMapUrl": item.get("googleMapUrl", ""),
        # Age-tiered pricing, shared identically by this standalone sign-up
        # AND the seat-upgrade plugin's own bundled add-on (read directly by
        # plugins/seat-upgrade/backend/lambdas/seat_upgrade/handler.py's
        # get_celebrity_dinner_addon_config — "copied module" convention,
        # same field names on both sides so they can never drift apart).
        # Kids under 6 have no price field at all — they don't get a priced
        # meal, just a headcount (see upsert_dinner_registration).
        "dinnerPriceAdults": int(item.get("dinnerPriceAdults", 0) or 0),
        "dinnerPriceKids914": int(item.get("dinnerPriceKids914", 0) or 0),
        "dinnerPriceKids68": int(item.get("dinnerPriceKids68", 0) or 0),
        "currency": item.get("currency", "JPY"),
        "conditions": item.get("conditions", ""),
        "notes": item.get("notes", ""),
        # Which of paypay/square/bank a registrant can pay a standalone
        # sign-up with — independent of the seat-upgrade plugin's own
        # paymentMethods, since this is a separate purchase.
        "paymentMethods": methods,
        # Whether this dinner can also be bundled into a seat-upgrade
        # request, instead of only being offered as this standalone sign-up.
        "addOnEnabled": item.get("addOnEnabled") is True,
        # Reserved for a future discount on the add-on specifically — stored
        # and admin-editable, but NOT currently applied to any computed
        # total (the Family Upgrade Discount scheme charges the add-on at
        # full price, same as the standalone booking).
        "addOnDiscountPercent": int(item.get("addOnDiscountPercent", 0) or 0),
        # Whether a registrant can self-service cancel/request a refund at
        # all — an admin kill switch, independent of the window below.
        # Defaults on (True) so an event created before this field existed
        # keeps its previous always-allowed behavior.
        "cancellationEnabled": bool(item.get("cancellationEnabled", True)),
        # ISO timestamp after which self-service cancellation closes, even
        # if cancellationEnabled is on. Empty = no cutoff (same convention
        # as registrationEndsAt) — see cancellation_allowed below.
        "refundWindowEndsAt": item.get("refundWindowEndsAt", ""),
        # Overall headcount cap across every active booking for this event —
        # standalone sign-ups AND the seat-upgrade plugin's bundled add-on
        # combined, since they share the same physical dinner (see
        # total_active_dinner_guest_count). Counts kids under 6 too: this is
        # a venue/seating capacity, not a billing figure. 0 = unlimited.
        "maxTotalGuests": int(item.get("maxTotalGuests", 0) or 0),
        "createdAt": item.get("createdAt", ""),
        "updatedAt": item.get("updatedAt", ""),
    }


def get_config(event_type: str) -> Optional[dict]:
    if not CONFIG_TABLE or not event_type:
        return None
    try:
        return dynamodb.Table(CONFIG_TABLE).get_item(Key={"eventType": event_type}).get("Item")
    except ClientError as exc:
        raise RuntimeError(f"Error fetching Celebrity Dinner config: {exc}")


def registration_window_status(config: dict) -> str:
    """'not_open' | 'open' | 'closed' relative to now, given a normalized config."""
    now = now_iso()
    starts = str(config.get("registrationStartsAt", "") or "")
    ends = str(config.get("registrationEndsAt", "") or "")
    if starts and now < starts:
        return "not_open"
    if ends and now > ends:
        return "closed"
    return "open"


def cancellation_allowed(config: dict) -> bool:
    """Whether a registrant may self-service cancel/request a refund right
    now, per the admin's cancellationEnabled toggle and optional
    refundWindowEndsAt cutoff (empty = no cutoff) — see normalize_config.
    Checked both by the frontend (to show/hide the Cancel button) and here
    server-side, since a hidden button is only ever a UI convenience, never
    the actual enforcement.
    """
    if not config.get("enabled", True):  # feature switched off: no self-service changes
        return False
    if not config.get("cancellationEnabled", True):
        return False
    deadline = str(config.get("refundWindowEndsAt", "") or "")
    if deadline and now_iso() > deadline:
        return False
    return True


# ── Dinner registrations ──────────────────────────────────────────────────


def normalize_dinner_registration(item: dict) -> dict:
    return {
        "dinnerRegistrationId": item.get("dinnerRegistrationId", ""),
        "eventType": item.get("eventType", ""),
        "registrationId": item.get("registrationId", ""),
        "email": item.get("email", ""),
        "name": item.get("name", ""),
        # Each age category splits into a veg/non-veg headcount (for the
        # caterer) — diet never affects price, only the per-category total
        # (veg + non-veg) does. Kids under 6 are counted the same way but
        # are never priced (see upsert_dinner_registration).
        "numberOfAdultsVeg": int(item.get("numberOfAdultsVeg", 0) or 0),
        "numberOfAdultsNonVeg": int(item.get("numberOfAdultsNonVeg", 0) or 0),
        "numberOfKids914Veg": int(item.get("numberOfKids914Veg", 0) or 0),
        "numberOfKids914NonVeg": int(item.get("numberOfKids914NonVeg", 0) or 0),
        "numberOfKids68Veg": int(item.get("numberOfKids68Veg", 0) or 0),
        "numberOfKids68NonVeg": int(item.get("numberOfKids68NonVeg", 0) or 0),
        "numberOfKidsUnder6Veg": int(item.get("numberOfKidsUnder6Veg", 0) or 0),
        "numberOfKidsUnder6NonVeg": int(item.get("numberOfKidsUnder6NonVeg", 0) or 0),
        # Snapshot of the per-category price at booking time, so a later
        # admin price change doesn't rewrite what this registrant already
        # agreed to pay — same convention as the seat-upgrade plugin's own
        # dinner price snapshot. No pricePerKidsUnder6 — that category is
        # never priced.
        "pricePerAdult": int(item.get("pricePerAdult", 0) or 0),
        "pricePerKids914": int(item.get("pricePerKids914", 0) or 0),
        "pricePerKids68": int(item.get("pricePerKids68", 0) or 0),
        "currency": item.get("currency", "JPY"),
        "totalAmount": int(item.get("totalAmount", 0) or 0),
        # Snapshot of the config's dinnerDate at booking time — see
        # upsert_dinner_registration.
        "dinnerDate": item.get("dinnerDate", ""),
        "status": item.get("status", "pending"),
        "paymentMethod": item.get("paymentMethod", ""),
        "paymentProvider": item.get("paymentProvider", ""),
        "providerPaymentId": item.get("providerPaymentId", ""),
        "checkoutUrl": item.get("checkoutUrl", ""),
        "paymentReference": item.get("paymentReference", ""),
        "refundStatus": item.get("refundStatus", ""),
        "refundReference": item.get("refundReference", ""),
        "refundError": item.get("refundError", ""),
        "refundRequestedAt": item.get("refundRequestedAt", ""),
        "submittedAt": item.get("submittedAt", ""),
        "updatedAt": item.get("updatedAt", ""),
    }


def get_dinner_registration(event_type: str, dinner_registration_id: str) -> Optional[dict]:
    if not REGISTRATIONS_TABLE_NAME or not dinner_registration_id:
        return None
    try:
        return dynamodb.Table(REGISTRATIONS_TABLE_NAME).get_item(
            Key={"eventType": event_type, "dinnerRegistrationId": dinner_registration_id}
        ).get("Item")
    except ClientError as exc:
        raise RuntimeError(f"Error fetching Celebrity Dinner registration: {exc}")


def get_active_dinner_registration_for_registration(registration_id: str) -> Optional[dict]:
    """The (at most one) active Celebrity Dinner sign-up tied to a registration."""
    if not REGISTRATIONS_TABLE_NAME or not registration_id:
        return None
    try:
        table = dynamodb.Table(REGISTRATIONS_TABLE_NAME)
        response = table.query(
            IndexName="RegistrationIdIndex",
            KeyConditionExpression=Key("registrationId").eq(registration_id),
        )
        for item in response.get("Items", []):
            if str(item.get("status", "pending")).lower() in ACTIVE_DINNER_STATUSES:
                return item
        return None
    except ClientError as exc:
        raise RuntimeError(f"Error fetching Celebrity Dinner registration: {exc}")


def get_active_guest_dinner_registration(event_type: str, email: str) -> Optional[dict]:
    """The (at most one) active Celebrity Dinner sign-up for someone with NO
    underlying event registration at all — a "guest" booking, identified by
    email instead of registrationId (which is left blank on these rows). The
    guest-booking equivalent of get_active_dinner_registration_for_registration.

    There's no email-keyed index on this table (it's a low-volume,
    per-event list), so this scans this event's own bookings the same way
    list_dinner_registrations_for_event already does, rather than adding a
    new GSI for what's normally at most a few dozen rows.
    """
    if not REGISTRATIONS_TABLE_NAME or not event_type or not email:
        return None
    email = email.strip().lower()
    try:
        table = dynamodb.Table(REGISTRATIONS_TABLE_NAME)
        response = table.query(KeyConditionExpression=Key("eventType").eq(event_type))
        for item in response.get("Items", []):
            if (
                not item.get("registrationId")
                and str(item.get("email", "")).strip().lower() == email
                and str(item.get("status", "pending")).lower() in ACTIVE_DINNER_STATUSES
            ):
                return item
        return None
    except ClientError as exc:
        raise RuntimeError(f"Error fetching guest Celebrity Dinner registration: {exc}")


def get_registration_by_provider_payment_id(provider_payment_id: str) -> Optional[dict]:
    if not REGISTRATIONS_TABLE_NAME or not provider_payment_id:
        return None
    try:
        table = dynamodb.Table(REGISTRATIONS_TABLE_NAME)
        response = table.query(
            IndexName="ProviderPaymentIdIndex",
            KeyConditionExpression=Key("providerPaymentId").eq(provider_payment_id),
            Limit=1,
        )
        items = response.get("Items", [])
        return items[0] if items else None
    except ClientError as exc:
        raise RuntimeError(f"Error looking up Celebrity Dinner registration by provider payment id: {exc}")


def has_active_seat_upgrade_dinner(registration_id: str) -> bool:
    """True if `registration_id` already has the seat-upgrade plugin's own
    bundled Celebrity Dinner add-on active (pending/paid/refund_requested
    with any dinner guest count > 0 — see plugins/seat-upgrade/backend/
    lambdas/seat_upgrade_logic.py's DINNER_GUEST_FIELDS, duplicated here
    per the usual "copied module" convention). Used to keep the two dinner
    paths — standalone sign-up here, or the seat-upgrade's bundled add-on —
    mutually exclusive. Always False when the plugin isn't deployed
    (SEAT_UPGRADES_TABLE unset).
    """
    dinner_guest_fields = (
        "dinnerAdultVegGuests", "dinnerAdultNonVegGuests",
        "dinnerKids914VegGuests", "dinnerKids914NonVegGuests",
        "dinnerKids68VegGuests", "dinnerKids68NonVegGuests",
        "dinnerKidsUnder6VegGuests", "dinnerKidsUnder6NonVegGuests",
    )
    if not SEAT_UPGRADES_TABLE or not registration_id:
        return False
    try:
        table = dynamodb.Table(SEAT_UPGRADES_TABLE)
        response = table.query(
            IndexName="RegistrationIdIndex",
            KeyConditionExpression=Key("registrationId").eq(registration_id),
        )
        for item in response.get("Items", []):
            status = str(item.get("status", "")).lower()
            dinner_guests = sum(int(item.get(field, 0) or 0) for field in dinner_guest_fields)
            if status in ACTIVE_SEAT_UPGRADE_STATUSES and dinner_guests > 0:
                return True
        return False
    except ClientError as exc:
        raise RuntimeError(f"Error checking seat-upgrade Celebrity Dinner add-on: {exc}")


def list_dinner_registrations_for_event(event_type: str) -> list:
    if not REGISTRATIONS_TABLE_NAME or not event_type:
        return []
    try:
        table = dynamodb.Table(REGISTRATIONS_TABLE_NAME)
        response = table.query(KeyConditionExpression=Key("eventType").eq(event_type))
        items = sorted(response.get("Items", []), key=lambda x: x.get("submittedAt", ""), reverse=True)
        return [normalize_dinner_registration(item) for item in items]
    except ClientError as exc:
        raise RuntimeError(f"Error listing Celebrity Dinner registrations: {exc}")


# Mirrors has_active_seat_upgrade_dinner's field list above — the
# seat-upgrade plugin's own bundled add-on guest fields.
_SEAT_UPGRADE_DINNER_GUEST_FIELDS = (
    "dinnerAdultVegGuests", "dinnerAdultNonVegGuests",
    "dinnerKids914VegGuests", "dinnerKids914NonVegGuests",
    "dinnerKids68VegGuests", "dinnerKids68NonVegGuests",
    "dinnerKidsUnder6VegGuests", "dinnerKidsUnder6NonVegGuests",
)


def total_active_dinner_guest_count(event_type: str, exclude_dinner_registration_id: str = "") -> int:
    """Total guests (all 8 veg/non-veg category counts, kids under 6
    included — maxTotalGuests is a seating/headcount cap, not a billing
    figure) across every seat-holding (pending/paid — see
    SEAT_HOLDING_DINNER_STATUSES) Celebrity Dinner booking for this event: this module's own standalone sign-ups
    AND the seat-upgrade plugin's bundled add-on combined, since both feed
    the same physical dinner. Used to enforce normalize_config's
    maxTotalGuests.

    `exclude_dinner_registration_id`, when given, omits that one standalone
    booking from its own count — used when re-validating a booking that's
    being updated (not newly created) so it doesn't count against itself.
    """
    if not event_type:
        return 0
    total = 0
    for reg in list_dinner_registrations_for_event(event_type):
        if reg["dinnerRegistrationId"] == exclude_dinner_registration_id:
            continue
        if str(reg.get("status", "pending")).lower() not in SEAT_HOLDING_DINNER_STATUSES:
            continue
        total += (
            reg["numberOfAdultsVeg"] + reg["numberOfAdultsNonVeg"]
            + reg["numberOfKids914Veg"] + reg["numberOfKids914NonVeg"]
            + reg["numberOfKids68Veg"] + reg["numberOfKids68NonVeg"]
            + reg["numberOfKidsUnder6Veg"] + reg["numberOfKidsUnder6NonVeg"]
        )
    if SEAT_UPGRADES_TABLE and event_type:
        try:
            table = dynamodb.Table(SEAT_UPGRADES_TABLE)
            response = table.query(
                IndexName="EventTypeIndex",
                KeyConditionExpression=Key("eventType").eq(event_type),
            )
            for item in response.get("Items", []):
                if str(item.get("status", "")).lower() in SEAT_HOLDING_SEAT_UPGRADE_STATUSES:
                    total += sum(int(item.get(field, 0) or 0) for field in _SEAT_UPGRADE_DINNER_GUEST_FIELDS)
        except ClientError as exc:
            raise RuntimeError(f"Error counting seat-upgrade Celebrity Dinner add-on guests: {exc}")
    return total


def list_bundled_dinner_guests_for_event(event_type: str) -> list:
    """Celebrity Dinner guests booked through the seat-upgrade plugin's
    bundled add-on (SEAT_UPGRADES_TABLE), normalized into the same shape as
    list_dinner_registrations_for_event's own rows — plus "source" and
    "tierName" — so the admin sign-ups list can show every guest for the
    event regardless of which path they booked through (see
    total_active_dinner_guest_count, which already combines both for the
    capacity math; this is the equivalent for the list view). Always empty
    when the plugin isn't deployed (SEAT_UPGRADES_TABLE unset).

    Read-only from this side: an admin marks one of these paid/cancelled
    from the Zone Upgrades tab (SeatUpgradeManagement.jsx), never here —
    this module doesn't own that record.
    """
    if not SEAT_UPGRADES_TABLE or not event_type:
        return []
    try:
        table = dynamodb.Table(SEAT_UPGRADES_TABLE)
        response = table.query(
            IndexName="EventTypeIndex",
            KeyConditionExpression=Key("eventType").eq(event_type),
        )
    except ClientError as exc:
        raise RuntimeError(f"Error listing bundled Celebrity Dinner guests: {exc}")

    results = []
    for item in response.get("Items", []):
        if str(item.get("status", "")).lower() not in VALID_DINNER_STATUSES:
            continue
        total_guests = sum(int(item.get(f, 0) or 0) for f in _SEAT_UPGRADE_DINNER_GUEST_FIELDS)
        if total_guests <= 0:
            continue
        results.append({
            "dinnerRegistrationId": f"zu-{item.get('upgradeId', '')}",
            "source": "zoneUpgrade",
            "tierName": item.get("tierName", ""),
            "eventType": event_type,
            "registrationId": item.get("registrationId", ""),
            "email": item.get("email", ""),
            "name": item.get("name", ""),
            "numberOfAdultsVeg": int(item.get("dinnerAdultVegGuests", 0) or 0),
            "numberOfAdultsNonVeg": int(item.get("dinnerAdultNonVegGuests", 0) or 0),
            "numberOfKids914Veg": int(item.get("dinnerKids914VegGuests", 0) or 0),
            "numberOfKids914NonVeg": int(item.get("dinnerKids914NonVegGuests", 0) or 0),
            "numberOfKids68Veg": int(item.get("dinnerKids68VegGuests", 0) or 0),
            "numberOfKids68NonVeg": int(item.get("dinnerKids68NonVegGuests", 0) or 0),
            "numberOfKidsUnder6Veg": int(item.get("dinnerKidsUnder6VegGuests", 0) or 0),
            "numberOfKidsUnder6NonVeg": int(item.get("dinnerKidsUnder6NonVegGuests", 0) or 0),
            "pricePerAdult": int(item.get("dinnerPriceAdults", 0) or 0),
            "pricePerKids914": int(item.get("dinnerPriceKids914", 0) or 0),
            "pricePerKids68": int(item.get("dinnerPriceKids68", 0) or 0),
            "currency": "JPY",
            "totalAmount": int(item.get("dinnerAmount", 0) or 0),
            "dinnerDate": "",
            "status": str(item.get("status", "pending")).lower(),
            "paymentMethod": item.get("paymentMethod", ""),
            "paymentProvider": item.get("paymentProvider", ""),
            "providerPaymentId": item.get("providerPaymentId", ""),
            "checkoutUrl": item.get("checkoutUrl", ""),
            "paymentReference": item.get("paymentReference", ""),
            "refundStatus": item.get("refundStatus", ""),
            "refundReference": item.get("refundReference", ""),
            "refundError": item.get("refundError", ""),
            "refundRequestedAt": item.get("refundRequestedAt", ""),
            "submittedAt": item.get("requestedAt", ""),
            "updatedAt": item.get("updatedAt", ""),
        })
    return results


def upsert_dinner_registration(
    event_type: str,
    registration: dict,
    guest_counts: dict,
    config: dict,
    payment_method: str = "",
    existing: Optional[dict] = None,
) -> dict:
    """Create or update the caller's Celebrity Dinner sign-up for `registration`.

    `guest_counts` is {"adultsVeg", "adultsNonVeg", "kids914Veg",
    "kids914NonVeg", "kids68Veg", "kids68NonVeg", "kidsUnder6Veg",
    "kidsUnder6NonVeg"} — each category's veg+non-veg total is priced at
    that category's rate from `config` (dinnerPriceAdults/Kids914/Kids68);
    diet never affects price. Kids under 6 are counted (for the caterer) but
    never priced — there's no dinnerPriceKidsUnder6 field at all. Starts
    life as "pending" — nothing is actually paid until checkout completes
    (PayPay/Square) or an admin marks a bank transfer paid, same as the
    seat-upgrade plugin's own create_upgrade_request. Validation (enabled,
    registration window, guest counts vs. per-category eligibility, payment
    method) is the caller's responsibility — this only persists.
    """
    if not REGISTRATIONS_TABLE_NAME:
        raise RuntimeError("Celebrity Dinner registrations table not configured")

    timestamp = now_iso()
    price_adult = int(config.get("dinnerPriceAdults", 0) or 0)
    price_kids914 = int(config.get("dinnerPriceKids914", 0) or 0)
    price_kids68 = int(config.get("dinnerPriceKids68", 0) or 0)
    num_adults_veg = int(guest_counts.get("adultsVeg", 0) or 0)
    num_adults_non_veg = int(guest_counts.get("adultsNonVeg", 0) or 0)
    num_kids914_veg = int(guest_counts.get("kids914Veg", 0) or 0)
    num_kids914_non_veg = int(guest_counts.get("kids914NonVeg", 0) or 0)
    num_kids68_veg = int(guest_counts.get("kids68Veg", 0) or 0)
    num_kids68_non_veg = int(guest_counts.get("kids68NonVeg", 0) or 0)
    num_kids_under6_veg = int(guest_counts.get("kidsUnder6Veg", 0) or 0)
    num_kids_under6_non_veg = int(guest_counts.get("kidsUnder6NonVeg", 0) or 0)
    total_amount = (
        (num_adults_veg + num_adults_non_veg) * price_adult
        + (num_kids914_veg + num_kids914_non_veg) * price_kids914
        + (num_kids68_veg + num_kids68_non_veg) * price_kids68
        # kidsUnder6 is never priced — a headcount only.
    )
    item = {
        "eventType": event_type,
        "dinnerRegistrationId": (existing or {}).get("dinnerRegistrationId") or str(uuid.uuid4()),
        "email": registration.get("email", ""),
        "name": registration.get("name", ""),
        "numberOfAdultsVeg": num_adults_veg,
        "numberOfAdultsNonVeg": num_adults_non_veg,
        "numberOfKids914Veg": num_kids914_veg,
        "numberOfKids914NonVeg": num_kids914_non_veg,
        "numberOfKids68Veg": num_kids68_veg,
        "numberOfKids68NonVeg": num_kids68_non_veg,
        "numberOfKidsUnder6Veg": num_kids_under6_veg,
        "numberOfKidsUnder6NonVeg": num_kids_under6_non_veg,
        "pricePerAdult": price_adult,
        "pricePerKids914": price_kids914,
        "pricePerKids68": price_kids68,
        "currency": config.get("currency", "JPY"),
        "totalAmount": total_amount,
        # Snapshot, same reasoning as the price fields above — the
        # confirmation/payment emails (celebrity_dinner_emailer.py) only
        # ever see this registration record, never the config, and a later
        # admin edit to the date shouldn't retroactively change what
        # already-sent emails told this registrant.
        "dinnerDate": config.get("dinnerDate", ""),
        "status": "pending",
        "paymentMethod": payment_method or "",
        "submittedAt": (existing or {}).get("submittedAt", timestamp),
        "updatedAt": timestamp,
    }
    # DynamoDB rejects an empty string as a GSI key attribute value
    # (RegistrationIdIndex), so a guest booking (no registrationId) simply
    # omits the attribute rather than writing "" — the index is sparse, so
    # that row is just absent from it, exactly as intended (there's nothing
    # to look up a guest booking BY registrationId).
    registration_id = str(registration.get("registrationId", "") or "").strip()
    if registration_id:
        item["registrationId"] = registration_id
    try:
        dynamodb.Table(REGISTRATIONS_TABLE_NAME).put_item(Item=item)
    except ClientError as exc:
        raise RuntimeError(f"Error saving Celebrity Dinner registration: {exc}")
    return item


def update_dinner_registration_status(event_type: str, dinner_registration_id: str, status: str) -> dict:
    """Transition a registration's status, conditioned on the status this
    call just read — see plugins/seat-upgrade/backend/lambdas/
    seat_upgrade_logic.py's update_upgrade_status for why: without it, two
    concurrent callers (e.g. a registrant double-cancelling from two tabs)
    could both read "paid" before either write lands and both go on to
    attempt a provider refund for the same booking.
    """
    if status not in VALID_DINNER_STATUSES:
        raise ValueError(f"Unknown status: {status!r}")

    existing = get_dinner_registration(event_type, dinner_registration_id)
    if not existing:
        raise ValueError("Celebrity Dinner registration not found")

    prior_status = str(existing.get("status", "pending")).lower()
    if prior_status == status:
        return existing

    try:
        table = dynamodb.Table(REGISTRATIONS_TABLE_NAME)
        response = table.update_item(
            Key={"eventType": event_type, "dinnerRegistrationId": dinner_registration_id},
            UpdateExpression="SET #status = :status, updatedAt = :now",
            ConditionExpression=Attr("status").eq(existing.get("status", "pending")),
            ExpressionAttributeNames={"#status": "status"},
            ExpressionAttributeValues={":status": status, ":now": now_iso()},
            ReturnValues="ALL_NEW",
        )
        return response.get("Attributes", {})
    except ClientError as exc:
        if _is_conditional_check_failed(exc):
            raise ValueError("This booking's status just changed — reload and try again.")
        raise RuntimeError(f"Error updating Celebrity Dinner registration status: {exc}")


def cancel_dinner_registration(event_type: str, dinner_registration_id: str) -> dict:
    """Self-service cancel: a pending (unpaid) booking is cancelled outright;
    a paid one moves to "refund_requested" instead — the caller is
    responsible for attempting a provider refund and an admin still
    finalizes it to "cancelled" once the money is actually back, same
    pattern as the seat-upgrade plugin's own cancel_upgrade_request.
    """
    existing = get_dinner_registration(event_type, dinner_registration_id)
    if not existing:
        raise ValueError("Celebrity Dinner registration not found")
    status = str(existing.get("status", "pending")).lower()
    if status == "pending":
        return update_dinner_registration_status(event_type, dinner_registration_id, "cancelled")
    if status == "paid":
        return update_dinner_registration_status(event_type, dinner_registration_id, "refund_requested")
    raise ValueError("This Celebrity Dinner booking can no longer be cancelled here")


# ── Online payment (PayPay / Square hosted checkout) ────────────────────────
#
# Mirrors plugins/seat-upgrade/backend/lambdas/seat_upgrade_logic.py's own
# record_checkout/mark_paid_from_provider/record_refund_attempt field-for-field.


def record_checkout(event_type: str, dinner_registration_id: str, *, provider: str, provider_payment_id: str, checkout_url: str) -> dict:
    if provider not in PAYMENT_PROVIDERS:
        raise ValueError(f"Unknown payment provider: {provider!r}")

    existing = get_dinner_registration(event_type, dinner_registration_id)
    if not existing:
        raise ValueError("Celebrity Dinner registration not found")
    if str(existing.get("status", "pending")).lower() != "pending":
        raise ValueError("Only a pending Celebrity Dinner booking can start a checkout")

    try:
        table = dynamodb.Table(REGISTRATIONS_TABLE_NAME)
        response = table.update_item(
            Key={"eventType": event_type, "dinnerRegistrationId": dinner_registration_id},
            UpdateExpression=(
                "SET paymentProvider = :provider, providerPaymentId = :ppid, "
                "checkoutUrl = :url, updatedAt = :now"
            ),
            ExpressionAttributeValues={
                ":provider": provider,
                ":ppid": provider_payment_id,
                ":url": checkout_url,
                ":now": now_iso(),
            },
            ReturnValues="ALL_NEW",
        )
        return response.get("Attributes", {})
    except ClientError as exc:
        raise RuntimeError(f"Error recording Celebrity Dinner checkout: {exc}")


def mark_paid_from_provider(provider_payment_id: str, *, payment_reference: str = "") -> tuple:
    """Idempotent webhook/return-page transition: pending -> paid. Returns
    (record, was_newly_paid) — see seat_upgrade_logic's own version for why
    callers use was_newly_paid to decide whether to fire a "paid" notice."""
    existing = get_registration_by_provider_payment_id(provider_payment_id)
    if not existing:
        return None, False
    if str(existing.get("status", "")).lower() == "paid":
        return existing, False  # already processed — most likely a duplicate webhook delivery

    event_type = existing["eventType"]
    dinner_registration_id = existing["dinnerRegistrationId"]
    updated = update_dinner_registration_status(event_type, dinner_registration_id, "paid")
    if payment_reference:
        try:
            table = dynamodb.Table(REGISTRATIONS_TABLE_NAME)
            response = table.update_item(
                Key={"eventType": event_type, "dinnerRegistrationId": dinner_registration_id},
                UpdateExpression="SET paymentReference = :ref",
                ExpressionAttributeValues={":ref": payment_reference},
                ReturnValues="ALL_NEW",
            )
            updated = response.get("Attributes", updated)
        except ClientError:
            pass  # the status transition already succeeded; the reference is best-effort metadata
    return updated, True


def record_refund_attempt(
    event_type: str, dinner_registration_id: str, *, refund_status: str, refund_reference: str = "", refund_error: str = ""
) -> dict:
    """Bookkeeping only — never changes `status` itself; an admin still
    finalizes the booking to "cancelled" once the refund is actually
    confirmed. `refund_error` is admin-only diagnostic text (the provider's
    own error detail), never surfaced to the registrant."""
    if not REGISTRATIONS_TABLE_NAME:
        raise RuntimeError("Celebrity Dinner registrations table not configured")
    updates = ["refundStatus = :status", "refundRequestedAt = if_not_exists(refundRequestedAt, :now)", "updatedAt = :now"]
    values = {":status": refund_status, ":now": now_iso()}
    if refund_reference:
        updates.append("refundReference = :ref")
        values[":ref"] = refund_reference
    if refund_error:
        updates.append("refundError = :err")
        values[":err"] = refund_error[:500]
    try:
        table = dynamodb.Table(REGISTRATIONS_TABLE_NAME)
        response = table.update_item(
            Key={"eventType": event_type, "dinnerRegistrationId": dinner_registration_id},
            UpdateExpression="SET " + ", ".join(updates),
            ExpressionAttributeValues=values,
            ReturnValues="ALL_NEW",
        )
        return response.get("Attributes", {})
    except ClientError as exc:
        raise RuntimeError(f"Error recording Celebrity Dinner refund attempt: {exc}")
