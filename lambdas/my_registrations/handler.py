"""
Lambda: registrant self-service for their own registrations.

Routes (API Gateway proxy) — all gated in-Lambda by the reg token minted on OTP
verify (Authorization header), NOT a Cognito credential:

  GET    /my/registrations          -> all registrations for the signed-in email
  PUT    /my/registrations/{id}     -> update one of the caller's registrations
  DELETE /my/registrations/{id}     -> delete one of the caller's registrations

The caller can only ever see or mutate registrations whose `email` matches the
verified token, so a registrant can never touch another person's record.

Onam registrations are re-priced server-side via onam_pricing (the same
authoritative rules the public register + admin flows use); the client may set
the option, attendee counts, and membership action, but never the amount.
"""

import base64
import hashlib
import hmac
import json
import logging
import os
import re
import time
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Optional

import boto3
from boto3.dynamodb.conditions import Key
from botocore.exceptions import ClientError

import onam_pricing
import npo_membership
import emailer
import entry_slots_logic
import entry_allocations
import seat_selection_logic
import seat_purchase_logic
import seat_purchase_emailer
import celebrity_dinner_logic as dinner
import celebrity_dinner_emailer as dinner_emailer
import paypay
import square

dynamodb = boto3.resource("dynamodb", region_name=os.environ["REGION"])

REGISTRATIONS_TABLE = os.environ["DYNAMODB_TABLE"]
EVENTS_TABLE = os.environ.get("EVENTS_TABLE", "")
FRONTEND_URL = os.environ.get("FRONTEND_URL", "")
DINNER_PAYMENT_ADAPTERS = {"paypay": paypay, "square": square}
MEMBERSHIP_TABLE = os.environ.get("MEMBERSHIP_TABLE", "")
MEMBERS_TABLE = os.environ.get("MEMBERS_TABLE", "")
ENTRY_SLOTS_TABLE = os.environ.get("ENTRY_SLOTS_TABLE", "")
SLOT_RESERVATIONS_TABLE = os.environ.get("SLOT_RESERVATIONS_TABLE", "")
ENTRY_ALLOCATIONS_TABLE = os.environ.get("ENTRY_ALLOCATIONS_TABLE", "")
EVENT_SEATS_TABLE = os.environ.get("SEATS_TABLE", "")
SEAT_UPGRADES_TABLE = os.environ.get("SEAT_UPGRADES_TABLE", "")
SEATED_OPTIONS = ("event", "combo")
FRONTEND_URL = os.environ.get("FRONTEND_URL", "")
TOKEN_SECRET = os.environ.get("OTP_TOKEN_SECRET", "")
RENEWAL_MEMBERSHIP_YEAR = os.environ.get("RENEWAL_MEMBERSHIP_YEAR", "2026-27")
MEMBERSHIP_AS_OF = os.environ.get("MEMBERSHIP_AS_OF", "")
LOG_LEVEL = os.environ.get("LOG_LEVEL", "INFO").upper()

logger = logging.getLogger(__name__)
logger.setLevel(LOG_LEVEL)

# Seat pools shared by the Onam registration options, as
# (capacity setting, closed switch, options drawing on the pool, label). Mirrors
# SEAT_POOLS in the register Lambda.
SEAT_POOLS = (
    ("sadhyaCapacity", "onamRegistrationClosed", ("onam", "combo"), "Onam Sadhya"),
    ("celebrityCapacity", "celebrityRegistrationClosed", ("event", "combo"), "Celebrity Event"),
)
# Attendee categories that occupy a seat (under-6s are lap seats).
SEATED_CATEGORIES = ("adults", "kids914", "kids68")


# ── Helpers ───────────────────────────────────────────────────────────────────


def decimal_to_python(obj):
    if isinstance(obj, Decimal):
        return int(obj) if obj == obj.to_integral_value() else float(obj)
    raise TypeError(f"Object of type {type(obj)} is not JSON serializable")


def cors_headers(origin="*"):
    return {
        "Access-Control-Allow-Origin": origin,
        "Access-Control-Allow-Headers": "Content-Type,Authorization",
        "Access-Control-Allow-Methods": "OPTIONS,GET,POST,PUT,DELETE",
        "Content-Type": "application/json",
    }


def respond(status: int, body: dict, origin: str = "*"):
    return {
        "statusCode": status,
        "headers": cors_headers(origin),
        "body": json.dumps(body, default=decimal_to_python),
    }


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def parse_json_body(event: dict) -> dict:
    if event.get("isBase64Encoded"):
        return {}
    try:
        parsed = json.loads(event.get("body") or "{}")
        return parsed if isinstance(parsed, dict) else {}
    except json.JSONDecodeError:
        return {}


def validate_phone(phone: str) -> bool:
    return bool(re.match(r"^\+\d{1,4}[\s\-]?\d[\d\s\-]{5,18}$", phone.strip()))


def _required_seats_for_registration(registration: dict) -> int:
    """Map attendeeCounts (Onam or standard schema) to entry-slot seats."""
    return entry_allocations.required_seats_for_registration(registration)


def _carry_over_slot_fields(record: dict, existing: dict) -> None:
    """Preserve slotId/slotReservationId/slotName across a full-record rewrite.

    _update_onam rebuilds the record from scratch, so without this the entry
    slot would silently disappear on a self-service edit.
    """
    for field in ("slotId", "slotReservationId", "slotName", "slotSeats", "allocations", "registrationPassToken"):
        if field in existing:
            record[field] = existing[field]


def get_capacity_used(event_type: str, options: tuple[str, ...] = (), exclude_registration_id: str = "") -> int:
    """Count attendees in an event or one of its registration capacity pools."""
    expression_values = {":event_type": event_type}
    filter_expression = "eventType = :event_type"
    if options:
        option_values = {f":option_{index}": option for index, option in enumerate(options)}
        filter_expression += " AND registrationOption IN (" + ", ".join(option_values) + ")"
        expression_values.update(option_values)

    total = 0
    scan_kwargs = {
        "FilterExpression": filter_expression,
        "ExpressionAttributeValues": expression_values,
    }
    table = dynamodb.Table(REGISTRATIONS_TABLE)
    while True:
        response = table.scan(**scan_kwargs)
        for item in response.get("Items", []):
            if item.get("registrationId") == exclude_registration_id:
                continue
            counts = item.get("attendeeCounts", {}) or {}
            attendee_keys = ("adults", "kids914", "kids68") if options else ("adult", "teen", "child", "toddler")
            total += sum(int(counts.get(key, 0) or 0) for key in attendee_keys)
        if "LastEvaluatedKey" not in response:
            return total
        scan_kwargs["ExclusiveStartKey"] = response["LastEvaluatedKey"]


def validate_capacity(event_type: str, requested_slots: int, existing: dict, origin: str, options: tuple[str, ...] = (), setting_key: str = "capacity", label: str = "Event"):
    """Return an over-capacity response, or None when a self-service edit fits."""
    event_config = emailer.get_event_config(event_type) or {}
    registration = event_config.get("registration", {})
    registration = registration if isinstance(registration, dict) else {}
    raw_capacity = registration.get(setting_key) if setting_key != "capacity" else event_config.get("capacity")
    try:
        capacity = int(raw_capacity or 0)
    except (TypeError, ValueError):
        capacity = 0
    if capacity <= 0:
        return None

    used_slots = get_capacity_used(event_type, options, existing.get("registrationId", ""))
    available_slots = max(capacity - used_slots, 0)
    if used_slots + requested_slots <= capacity:
        return None
    return respond(
        400,
        {
            "error": (
                f"{label} has {available_slots} available slot(s). "
                f"Your update requires {requested_slots} slot(s)."
            ),
            "capacityReached": True,
            "capacity": capacity,
            "usedSlots": used_slots,
            "availableSlots": available_slots,
            "requestedSlots": requested_slots,
        },
        origin,
    )


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


# ── NPO membership helpers (mirrors register + manage_registrations Lambdas) ──


def get_member_record(email: str) -> Optional[dict]:
    if not MEMBERS_TABLE or not email:
        return None
    try:
        return dynamodb.Table(MEMBERS_TABLE).get_item(Key={"email": email}).get("Item")
    except ClientError:
        logger.exception("Members read error for %s", email)
        return None


def get_membership_transactions(memberid: str) -> list:
    if not MEMBERSHIP_TABLE or not memberid:
        return []
    try:
        resp = dynamodb.Table(MEMBERSHIP_TABLE).query(
            KeyConditionExpression=Key("memberid").eq(str(memberid))
        )
        return resp.get("Items", [])
    except ClientError:
        logger.exception("Membership query error for %s", memberid)
        return []


def is_active_member(email: str) -> bool:
    """True if `email` is a current NPO member (flag active + a covering period).

    Used to price ADDITIONAL adults on an Onam edit: each is discounted on their
    own membership, but can't join/renew through this flow.
    """
    email = (email or "").strip().lower()
    if not email:
        return False
    member_record = get_member_record(email)
    if not member_record:
        return False
    memberid = str(member_record.get("memberid", ""))
    flag_active = bool(member_record.get("npoMembershipActive"))
    if not (memberid and flag_active):
        return False
    summary = npo_membership.evaluate(
        get_membership_transactions(memberid),
        as_of=MEMBERSHIP_AS_OF,
        renewal_year=RENEWAL_MEMBERSHIP_YEAR,
    )
    return bool(summary["validNow"])


def email_already_availed_discount(email: str, event_type: str, exclude_registration_id: str = "") -> bool:
    """True if `email` already received an NPO discount on another registration
    for this event — either as the registrant or as someone else's additional
    adult. One email can hold several registrations, but the NPO discount is a
    one-time benefit per event, not one per registration.
    """
    email = (email or "").strip().lower()
    if not email:
        return False

    table = dynamodb.Table(REGISTRATIONS_TABLE)

    try:
        response = table.query(
            IndexName="EmailIndex",
            KeyConditionExpression=Key("email").eq(email),
        )
        for item in response.get("Items", []):
            if item.get("eventType") != event_type:
                continue
            if exclude_registration_id and item.get("registrationId") == exclude_registration_id:
                continue
            option = item.get("registrationOption", "")
            tier = item.get("pricingTier", "none")
            if onam_pricing.DISCOUNT_PCT.get(tier, {}).get(option, 0) > 0:
                return True
    except ClientError:
        logger.exception("Could not check prior NPO discount usage for email=%s", email)

    scan_kwargs = {
        "FilterExpression": "eventType = :et",
        "ExpressionAttributeValues": {":et": event_type},
    }
    try:
        while True:
            response = table.scan(**scan_kwargs)
            for item in response.get("Items", []):
                if exclude_registration_id and item.get("registrationId") == exclude_registration_id:
                    continue
                option = item.get("registrationOption", "")
                for adult in item.get("additionalAdults") or []:
                    if not adult.get("isMember"):
                        continue
                    if str(adult.get("email", "")).strip().lower() != email:
                        continue
                    if onam_pricing.DISCOUNT_PCT.get("member", {}).get(option, 0) > 0:
                        return True
            if "LastEvaluatedKey" not in response:
                break
            scan_kwargs["ExclusiveStartKey"] = response["LastEvaluatedKey"]
    except ClientError:
        logger.exception("Could not scan prior NPO discount usage (additional adult) for email=%s", email)

    return False


def next_transaction_no(memberid: str, transactions: list) -> str:
    highest = 0
    for txn in transactions or []:
        try:
            highest = max(highest, int(str(txn.get("transactionNo", "0"))))
        except (TypeError, ValueError):
            continue
    return str(highest + 1)


def record_membership_transaction(memberid: str, txn: dict) -> bool:
    if not MEMBERSHIP_TABLE or not memberid:
        return False
    try:
        dynamodb.Table(MEMBERSHIP_TABLE).put_item(Item=txn)
    except ClientError:
        logger.exception("Membership write error for %s", memberid)
        return False
    if MEMBERS_TABLE and txn.get("email"):
        try:
            dynamodb.Table(MEMBERS_TABLE).update_item(
                Key={"email": txn["email"]},
                UpdateExpression="SET npoMembershipActive = :t, memberid = if_not_exists(memberid, :m)",
                ExpressionAttributeValues={":t": True, ":m": str(memberid)},
            )
        except ClientError:
            logger.exception("Members flag update error for %s", txn.get("email"))
    return True


# ── GET /my/registrations ─────────────────────────────────────────────────────


def list_my_registrations(email: str, origin: str):
    """All registrations for the signed-in email, across every event."""
    try:
        resp = dynamodb.Table(REGISTRATIONS_TABLE).query(
            IndexName="EmailIndex",
            KeyConditionExpression=Key("email").eq(email),
        )
        items = resp.get("Items", [])
    except ClientError:
        logger.exception("Could not list registrations for email=%s", email)
        return respond(500, {"error": "Failed to load your registrations"}, origin)

    for item in items:
        item["cancellationEnabled"] = event_user_cancellation_enabled(item.get("eventType", ""))
        # Celebrity Event seats are never stored on the registration record
        # itself — a registrant can hold seats across several purchase
        # requests (see seat_purchase_logic.py), each with its own price and
        # lifecycle. This is just a convenience summary of every seat
        # currently held (any active purchase, paid or still pending) for
        # the registration card; the actual purchase list/status/pricing
        # comes from GET /my/registrations/{id}/seat-purchases.
        if item.get("registrationOption") in ("event", "combo"):
            item["seatIds"] = sorted(
                seat["seatId"]
                for seat in seat_selection_logic.get_seats_for_registration(
                    item.get("eventType", ""), item.get("registrationId", "")
                )
            )
        item["seatUpgrade"] = event_seat_upgrade_config(item.get("eventType", ""))
        slot_window, slot_registration = event_sadhya_slot_window_status(item.get("eventType", ""))
        item["sadhyaSlotWindow"] = {
            "status": slot_window,
            "startDate": slot_registration.get("sadhyaSlotStartDate", ""),
            "endDate": slot_registration.get("sadhyaSlotEndDate", ""),
        }
        # The record's allocations are a snapshot from when the passes were
        # issued; check-in only moves the counters on the allocations table, so
        # read them back or the page shows nobody as checked in.
        if item.get("allocations"):
            entry_allocations.refresh_allocations(item)
            continue
        # Legacy records predate allocation entities. Present their single slot
        # in the same shape so clients can migrate without losing existing
        # passes. Their counters stay at zero until the pass is first scanned,
        # which backfills a real allocation.
        if not item.get("slotId"):
            continue
        item["allocations"] = [{
            "entrySlotId": item.get("slotId", ""),
            "slotName": item.get("slotName", ""),
            "seatCount": int(item.get("slotSeats", 0) or 0),
            "checkedInCount": 0,
            "remaining": int(item.get("slotSeats", 0) or 0),
            "status": "ACTIVE",
            "legacy": True,
        }]

    # Most-recent first so the manage page reads naturally.
    items.sort(key=lambda r: str(r.get("submittedAt", "")), reverse=True)
    return respond(200, {"registrations": items, "count": len(items)}, origin)


# ── PUT /my/registrations/{id} ────────────────────────────────────────────────


def _load_owned_registration(registration_id: str, email: str):
    """Fetch a registration and confirm the signed-in email owns it.

    Returns (item, error_response). Exactly one is non-None.
    """
    try:
        item = dynamodb.Table(REGISTRATIONS_TABLE).get_item(
            Key={"registrationId": registration_id}
        ).get("Item")
    except ClientError:
        logger.exception("Registration lookup error for id=%s", registration_id)
        return None, respond(500, {"error": "Failed to load registration"})

    if not item:
        return None, respond(404, {"error": "Registration not found"})
    # Ownership check: a registrant can only touch their own record.
    if str(item.get("email", "")).strip().lower() != email:
        return None, respond(403, {"error": "You can only manage your own registrations"})
    return item, None


def event_user_cancellation_enabled(event_type: str) -> bool:
    """Return whether the owning event permits self-service cancellation."""
    if not EVENTS_TABLE or not event_type:
        return True
    try:
        event = dynamodb.Table(EVENTS_TABLE).get_item(
            Key={"eventType": event_type}
        ).get("Item")
        registration = (event or {}).get("registration", {})
        return not isinstance(registration, dict) or registration.get("cancellationEnabled") is not False
    except ClientError:
        logger.exception("Could not read event cancellation setting for %s", event_type)
        return True


def event_sadhya_slot_window_status(event_type: str) -> tuple[str, dict]:
    """("not_started"|"open"|"ended", registration) for the owning event's
    Sadhya slot booking window (registration.sadhyaSlotStartDate/
    sadhyaSlotEndDate, both optional "YYYY-MM-DD", inclusive). Mirrors
    register/handler.py's sadhya_slot_window_status — that Lambda gates the
    initial pick, this one gates a post-payment self-service change; each
    Lambda is a standalone deployment with no shared import (same
    convention as event_seat_upgrade_config below), so both keep their own
    copy of this date comparison rather than one importing the other.
    """
    if not EVENTS_TABLE or not event_type:
        return "open", {}
    try:
        event = dynamodb.Table(EVENTS_TABLE).get_item(Key={"eventType": event_type}).get("Item")
    except ClientError:
        logger.exception("Could not read Sadhya slot window for event=%s", event_type)
        return "open", {}
    registration = (event or {}).get("registration", {})
    if not isinstance(registration, dict):
        return "open", {}
    today = datetime.now(timezone.utc).date()
    start = str(registration.get("sadhyaSlotStartDate", "")).strip()
    end = str(registration.get("sadhyaSlotEndDate", "")).strip()
    if start and today < datetime.strptime(start, "%Y-%m-%d").date():
        return "not_started", registration
    if end and today > datetime.strptime(end, "%Y-%m-%d").date():
        return "ended", registration
    return "open", registration


def event_seat_upgrade_config(event_type: str) -> dict:
    """Return the owning event's seat-upgrade plugin config for the "Upgrade
    My Seat" button — {"enabled": bool, "url": str, "windowStatus": str}.
    `url` is the admin's configured override (see
    frontend/src/AdminDashboard.jsx's Events form); empty means "use the
    plugin's built-in /my/seat-upgrade page". `windowStatus` is
    "not_started" | "open" | "ended" against the plugin's own
    startDate/endDate (both optional "YYYY-MM-DD") — MyRegistrations.jsx
    uses it to hide the button once the window has closed, rather than
    sending someone through to a page that will just tell them so.

    Disabled by default: an event with no eventInfo.seatUpgrade block, or
    `enabled` not exactly `true`, has the feature off, same convention as
    plugins/seat-upgrade/backend/lambdas/seat_upgrade/handler.py's own
    get_seat_upgrade_config (this Lambda and the plugin's are independent
    copies reading the same eventInfo shape — see that module's docstring
    for why they're not shared code, including this window_status copy).
    """
    disabled = {"enabled": False, "url": "", "windowStatus": "open"}
    if not EVENTS_TABLE or not event_type:
        return disabled
    try:
        item = dynamodb.Table(EVENTS_TABLE).get_item(Key={"eventType": event_type}).get("Item") or {}
    except ClientError:
        logger.exception("Could not read seat upgrade config for event=%s", event_type)
        return disabled

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
        return disabled

    today = datetime.now(timezone.utc).date()
    start = str(raw.get("startDate", "")).strip()
    end = str(raw.get("endDate", "")).strip()
    if start and today < datetime.strptime(start, "%Y-%m-%d").date():
        window_status = "not_started"
    elif end and today > datetime.strptime(end, "%Y-%m-%d").date():
        window_status = "ended"
    else:
        window_status = "open"

    return {
        "enabled": raw.get("enabled") is True,
        "url": raw.get("url") if isinstance(raw.get("url"), str) else "",
        "windowStatus": window_status,
    }


def validate_pool_open(
    event_type: str,
    requested_slots: int,
    existing: dict,
    origin: str,
    options: tuple[str, ...],
    setting_key: str,
    label: str,
):
    """Return a closed-pool response, or None when the edit doesn't grow the pool.

    A pool closed by the admin switch takes no new bookings and lets no existing
    one grow into it, but an unchanged-or-smaller edit still goes through.
    """
    event_config = emailer.get_event_config(event_type) or {}
    registration = event_config.get("registration", {})
    registration = registration if isinstance(registration, dict) else {}
    if registration.get(setting_key) is not True:
        return None

    if existing.get("registrationOption") in options:
        counts = existing.get("attendeeCounts", {}) or {}
        held_slots = sum(int(counts.get(key, 0) or 0) for key in SEATED_CATEGORIES)
    else:
        held_slots = 0
    if requested_slots <= held_slots:
        return None

    return respond(
        400,
        {
            "error": f"{label} registrations are closed.",
            "registrationClosed": True,
        },
        origin,
    )


def update_my_registration(registration_id: str, body: dict, email: str, origin: str):
    existing, err = _load_owned_registration(registration_id, email)
    if err:
        return err

    # Checked in at the venue: the party is inside against an entry pass sized
    # for this record, so it is closed to self-service edits for good. Checked
    # before the payment gate below only to give the accurate reason.
    if entry_allocations.checked_in_seats(existing):
        return respond(
            409,
            {
                "error": "You've been checked in at the venue, so this registration can "
                         "no longer be edited. Please speak to a volunteer.",
                "checkedIn": True,
                "locked": True,
            },
            origin,
        )

    # Once payment is completed the registrant can no longer self-edit; changes
    # would alter the priced order after money has been collected. Admins can
    # still amend it (which recomputes the balance due).
    if entry_allocations.payment_is_complete(existing):
        return respond(
            403,
            {
                "error": "This registration is paid and can no longer be edited. "
                         "Please contact us if you need to make a change.",
                "locked": True,
            },
            origin,
        )

    # Onam records are re-priced server-side; standard records update in place.
    is_onam = bool(body.get("registrationOption")) or existing.get("registrationType") == "onam"
    if is_onam:
        return _update_onam(existing, body, email, origin)
    return _update_standard(existing, body, email, origin)


def _release_existing_allocations(event_type: str, existing: dict) -> None:
    """Cancel every reservation/pass currently on file for a registration."""
    for allocation in existing.get("allocations") or []:
        full_item = entry_allocations.get_allocation(event_type, allocation.get("allocationId", ""))
        if not full_item:
            continue
        if full_item.get("reservationId"):
            try:
                entry_slots_logic.cancel_reservation(full_item["reservationId"], event_type)
            except Exception:
                logger.warning("Could not release reservation for allocation %s", allocation.get("allocationId"))
        entry_allocations.cancel_allocation(full_item)

    # Legacy registrations predating the allocation model.
    if not existing.get("allocations") and existing.get("slotReservationId"):
        try:
            entry_slots_logic.cancel_reservation(existing["slotReservationId"], event_type)
        except Exception:
            logger.warning("Could not release legacy reservation %s", existing.get("slotReservationId"))


def _reserve_new_allocations(event_type: str, registration_id: str, requested: list) -> list:
    """Reserve capacity + create one pass per requested slot; all-or-nothing."""
    created_items = []
    try:
        for allocation in requested:
            reservation = entry_slots_logic.reserve_seats_atomic(
                event_type=event_type,
                slot_id=allocation["slotId"],
                registration_id=registration_id,
                required_seats=allocation["seatCount"],
            )
            slot = entry_slots_logic.get_slot(event_type, allocation["slotId"])
            item = entry_allocations.create_allocation_item(
                event_type, registration_id, allocation, slot, reservation
            )
            entry_allocations.persist_allocation(item)
            created_items.append(item)
        return created_items
    except (ValueError, RuntimeError, ClientError) as exc:
        for item in created_items:
            try:
                if item.get("reservationId"):
                    entry_slots_logic.cancel_reservation(item["reservationId"], event_type)
            except Exception:
                logger.exception("Could not release reservation during allocation rollback")
            entry_allocations.cancel_allocation(item)
        if isinstance(exc, ClientError):
            raise ValueError(f"Entry allocation failed: {exc}") from exc
        raise


def change_my_registration_slot(registration_id: str, body: dict, email: str, origin: str):
    """Change the entry-slot allocation(s) on a paid registration.

    Accepts either `allocations: [{slotId, seatCount}, ...]` or a legacy
    `slotId` (assigning it all the required seats). This never reprices the
    order, but it is available only after payment and before check-in.
    """
    existing, err = _load_owned_registration(registration_id, email)
    if err:
        return err

    event_type = existing.get("eventType", "")
    if not event_type:
        return respond(400, {"error": "Registration has no eventType"}, origin)

    if str(existing.get("paymentStatus", "")).strip().lower() != "paid":
        return respond(
            403,
            {"error": "Entry slot selection is available after payment is confirmed.", "paid": False},
            origin,
        )

    if not ENTRY_SLOTS_TABLE or not SLOT_RESERVATIONS_TABLE or not ENTRY_ALLOCATIONS_TABLE:
        return respond(500, {"error": "Entry slots are not configured"}, origin)

    try:
        required_seats = _required_seats_for_registration(existing)
    except (TypeError, ValueError) as exc:
        return respond(400, {"error": f"Invalid attendee counts: {exc}"}, origin)

    try:
        requested = entry_allocations.normalize_requested_allocations(body, required_seats)
    except ValueError as exc:
        return respond(400, {"error": str(exc)}, origin)

    existing_signature = sorted(
        (str(a.get("entrySlotId", "")), int(a.get("seatCount", 0) or 0))
        for a in (existing.get("allocations") or [])
    )
    requested_signature = sorted((a["slotId"], a["seatCount"]) for a in requested)
    if requested_signature == existing_signature:
        return respond(200, {"registration": existing}, origin)

    # A real change past this point — gate it on the booking window. A
    # no-op resubmission of the same slot (caught above) is never blocked,
    # even after the window closes; it isn't asking for anything new.
    window, window_registration = event_sadhya_slot_window_status(event_type)
    if window == "not_started":
        return respond(400, {
            "error": f"Sadhya slot selection opens on {window_registration['sadhyaSlotStartDate']}.",
        }, origin)
    if window == "ended":
        return respond(400, {
            "error": f"Sadhya slot selection closed on {window_registration['sadhyaSlotEndDate']}.",
        }, origin)

    # A real change from here on, which releases the current passes and their
    # seats. Once anyone has been admitted those seats have been used, so the
    # slot is frozen: releasing it would hand the capacity back while the party
    # is inside, and invalidate a pass that has already been honoured.
    if entry_allocations.checked_in_seats(existing):
        return respond(
            409,
            {
                "error": "This registration has already been checked in, so the entry "
                         "slot can no longer be changed.",
                "checkedIn": True,
            },
            origin,
        )

    _release_existing_allocations(event_type, existing)

    try:
        created_items = _reserve_new_allocations(event_type, registration_id, requested)
    except ValueError as exc:
        return respond(400, {"error": str(exc)}, origin)
    except RuntimeError as exc:
        return respond(400, {"error": str(exc)}, origin)

    allocations = [entry_allocations.public_allocation(item) for item in created_items]
    existing["allocations"] = allocations
    if len(allocations) == 1:
        existing["slotId"] = allocations[0]["entrySlotId"]
        existing["slotReservationId"] = created_items[0].get("reservationId", "")
        existing["slotName"] = allocations[0]["slotName"]
        existing["slotSeats"] = allocations[0]["seatCount"]
    else:
        for field in ("slotId", "slotReservationId", "slotName", "slotSeats"):
            existing.pop(field, None)
    existing["updatedAt"] = now_iso()

    try:
        dynamodb.Table(REGISTRATIONS_TABLE).put_item(Item=existing)
    except ClientError:
        logger.exception("Failed to persist slot change for registration=%s", registration_id)
        return respond(500, {"error": "Failed to save your entry slot selection"}, origin)

    # Slot changed — send a dedicated confirmation with the new slot(s).
    _send_update_emails(existing, notification_type="slot")

    return respond(200, {"registration": existing}, origin)


PAYMENT_ADAPTERS = {"paypay": paypay, "square": square}


def get_seat_selection_config(event_type: str) -> dict:
    """This event's admin-configured seat-selection settings, read from
    eventInfo.seatSelection. Reads eventInfo (host-owned) ourselves, then
    delegates the parsing to the shared plugin logic so manage_registrations'
    announcement mailer stays in sync with this without duplicating the
    default/validation rules.
    """
    empty = {
        "enabled": False,
        "paymentMethods": list(seat_selection_logic.SEAT_SELECTION_PAYMENT_METHODS),
        "startDate": "",
        "endDate": "",
    }
    if not EVENTS_TABLE or not event_type:
        return empty
    try:
        item = dynamodb.Table(EVENTS_TABLE).get_item(Key={"eventType": event_type}).get("Item") or {}
    except ClientError:
        logger.exception("Could not read seat selection config for event=%s", event_type)
        return empty

    event_info = item.get("eventInfo", {})
    if isinstance(event_info, str):
        try:
            event_info = json.loads(event_info) if event_info.strip() else {}
        except json.JSONDecodeError:
            event_info = {}
    if not isinstance(event_info, dict):
        event_info = {}

    return seat_selection_logic.parse_seat_selection_config(event_info)


def normalize_purchase(item: dict) -> dict:
    return {
        "purchaseId": item.get("purchaseId", ""),
        "registrationId": item.get("registrationId", ""),
        "eventType": item.get("eventType", ""),
        "seatIds": item.get("seatIds", []),
        "pricingMode": item.get("pricingMode", "person"),
        "breakdown": item.get("breakdown", []),
        "totalAmount": int(item.get("totalAmount", 0) or 0),
        "pricingEnabled": item.get("pricingEnabled", True),
        "status": item.get("status", "pending"),
        "paymentMethod": item.get("paymentMethod", ""),
        "paymentProvider": item.get("paymentProvider", ""),
        "paymentReference": item.get("paymentReference", ""),
        "checkoutUrl": item.get("checkoutUrl", ""),
        "refundStatus": item.get("refundStatus", ""),
        "requestedAt": item.get("requestedAt", ""),
        "checkedIn": item.get("checkedIn", False),
        "checkedInAt": item.get("checkedInAt", ""),
    }


def _eligible_for_seat_purchase(existing: dict, origin: str):
    """Shared eligibility gate for every seat-purchase route: paid
    event/combo registration, feature enabled for the event, booking
    window open. Returns None if eligible, else an error response."""
    option = str(existing.get("registrationOption", "")).strip().lower()
    if option not in SEATED_OPTIONS:
        return respond(
            400,
            {"error": "Seat selection is only available for Celebrity Event and Combo registrations."},
            origin,
        )
    if str(existing.get("paymentStatus", "")).strip().lower() != "paid":
        return respond(
            403,
            {"error": "Seat selection is available after payment is confirmed.", "paid": False},
            origin,
        )
    if not EVENT_SEATS_TABLE:
        return respond(500, {"error": "Seat selection is not configured"}, origin)
    return None


def _celebrity_seats_allowed(registration: dict) -> int:
    """Adults + kids 6-14 only — kids under 6 don't need their own seat.
    Handles both the Onam schema (adults/kids914/kids68/kidsUnder6) and the
    standard one (adult/teen/child/toddler).

    Deliberately NOT entry_allocations.required_seats_for_registration —
    that one (for the Sadhya entry slot) includes kids under 6 in its
    total; a Celebrity Event seat cap must not.
    """
    counts = registration.get("attendeeCounts", {}) or {}
    if registration.get("registrationType") == "onam" or "adults" in counts:
        return (
            int(counts.get("adults", 0) or 0)
            + int(counts.get("kids914", 0) or 0)
            + int(counts.get("kids68", 0) or 0)
        )
    return int(counts.get("adult", 0) or 0) + int(counts.get("teen", 0) or 0) + int(counts.get("child", 0) or 0)


def _seat_allowance(registration_id: str, registration: dict) -> dict:
    """{"seatsAllowed", "seatsClaimed", "seatsRemaining"} for this
    registration — a cap on top of (not instead of) normal seat
    availability: at most one seat per eligible attendee, counting every
    seat the registration currently holds across its still-active
    purchases (pending/paid/refund_requested — cancelled ones don't count,
    same set seat_purchase_logic itself treats as holding seats)."""
    allowed = _celebrity_seats_allowed(registration)
    try:
        claimed = sum(
            len(purchase.get("seatIds") or [])
            for purchase in seat_purchase_logic.list_purchases_for_registration(registration_id)
            if purchase.get("status") in seat_purchase_logic.ACTIVE_STATUSES
        )
    except RuntimeError:
        logger.exception("Could not compute seat allowance for registration=%s", registration_id)
        claimed = 0
    return {
        "seatsAllowed": allowed,
        "seatsClaimed": claimed,
        "seatsRemaining": max(allowed - claimed, 0),
    }


# A still-pending, unpaid zone upgrade hasn't actually been paid for, so it
# grants no seat-map entitlement — see _seat_zone_category_totals. Mirrors
# seat_purchase_logic.ACTIVE_STATUSES' own "still holds it" set, minus
# "pending", for the same reason create_purchase_request's own callers never
# treat a pending purchase as already paid.
_ZONE_UPGRADE_ENTITLING_STATUSES = {"paid", "refund_requested"}

ZONE_CATEGORY_LABELS = {"zone1": "Zone 1", "zone2": "Zone 2", "zone3": "Zone 3"}


def _seat_zone_category_totals(registration_id: str) -> dict:
    """{"zone1": totalSeats, "zone2": ..., "zone3": ...} this registration
    has ever been entitled to pick from the seat-selection hall, derived
    entirely from the *separate* seat-upgrade plugin's own purchase records
    (SEAT_UPGRADES_TABLE) — this Lambda reads that plugin's table directly
    rather than importing its logic, the same "copied module" convention
    already used for the Celebrity Dinner add-on exclusivity check above.

    Only paid/refund_requested upgrades count. Summed across every zone pick
    in every qualifying purchase's zoneBreakdown — a single purchase can
    cover several zones/tiers at once (plugins/seat-upgrade's Family Upgrade
    Discount is "valid in all zones") — using each pick's seatMapCategory,
    snapshotted onto it at purchase time from the tier's own admin-set field
    (see that plugin's seat_upgrade_logic.price_seat_picks). A tier with no
    seatMapCategory set contributes nothing here; it's still a perfectly
    normal standalone paid upgrade, just one that doesn't unlock anything on
    this side. Always {} when the plugin isn't deployed (SEAT_UPGRADES_TABLE
    unset) or the registration has no qualifying purchase.
    """
    totals: dict = {}
    if not SEAT_UPGRADES_TABLE or not registration_id:
        return totals
    try:
        response = dynamodb.Table(SEAT_UPGRADES_TABLE).query(
            IndexName="RegistrationIdIndex",
            KeyConditionExpression=Key("registrationId").eq(registration_id),
        )
    except ClientError:
        logger.exception("Could not read seat-upgrade entitlement for registration=%s", registration_id)
        return totals
    for item in response.get("Items", []):
        if str(item.get("status", "")).lower() not in _ZONE_UPGRADE_ENTITLING_STATUSES:
            continue
        for zone in item.get("zoneBreakdown", []) or []:
            category = str(zone.get("seatMapCategory", "")).strip()
            if category not in seat_selection_logic.PRICEABLE_CATEGORIES:
                continue
            totals[category] = totals.get(category, 0) + int(zone.get("count", 0) or 0)
    return totals


def _seat_category_counts_held(event_type: str, registration_id: str) -> dict:
    """{category: count} of seats this registration currently holds (across
    every still-active seat purchase), by seat-map category — how much of
    each zone's entitlement is already spent."""
    counts: dict = {}
    overrides = seat_selection_logic.get_category_overrides(event_type)
    for seat in seat_selection_logic.get_seats_for_registration(event_type, registration_id):
        found = seat_selection_logic.find_seat(seat.get("seatId", ""), overrides)
        if not found:
            continue
        counts[found["category"]] = counts.get(found["category"], 0) + 1
    return counts


def _seat_category_caps(event_type: str, registration_id: str) -> dict:
    """{"zone1": remaining, ...} — how many MORE zone1/zone2/zone3 seats this
    registration may still pick, when eventInfo.seatSelection.pricingEnabled
    is off (see create_my_seat_purchase) and picking is instead gated by
    what the registration has separately paid for via the seat-upgrade
    plugin. "regular" is deliberately absent — always allowed under this
    mechanism, bounded only by the overall seat allowance (_seat_allowance).
    A category with nothing remaining is omitted entirely (never a 0 entry).
    """
    totals = _seat_zone_category_totals(registration_id)
    if not totals:
        return {}
    held = _seat_category_counts_held(event_type, registration_id)
    caps = {}
    for category, total in totals.items():
        remaining = total - held.get(category, 0)
        if remaining > 0:
            caps[category] = remaining
    return caps


def list_my_seat_purchases(registration_id: str, email: str, origin: str):
    """GET /my/registrations/{id}/seat-purchases — this registration's own
    seat purchase requests plus the event's booking-window/payment-method
    config (the seat map + prices themselves come from the plugin's public
    GET /events/{eventType}/seats, already fetched separately)."""
    existing, err = _load_owned_registration(registration_id, email)
    if err:
        return err
    err = _eligible_for_seat_purchase(existing, origin)
    if err:
        return err

    event_type = existing.get("eventType", "")
    config = get_seat_selection_config(event_type)
    window = seat_selection_logic.seat_selection_window_status(config)
    purchases = [
        normalize_purchase(p) for p in seat_purchase_logic.list_purchases_for_registration(registration_id)
    ]
    response_body = {
        "eventType": event_type,
        "enabled": config["enabled"],
        "pricingEnabled": config["pricingEnabled"],
        "enabledPaymentMethods": config["paymentMethods"],
        "seatSelectionWindow": {
            "status": window,
            "startDate": config["startDate"],
            "endDate": config["endDate"],
        },
        "purchases": purchases,
        **_seat_allowance(registration_id, existing),
    }
    if not config["pricingEnabled"]:
        response_body["categoryCaps"] = _seat_category_caps(event_type, registration_id)
    return respond(200, response_body, origin)


# Temporary, launch-day hold: these seats must still show as available on
# the public seat map (get_available_seats' bookedSeatIds comes only from
# the seats table — this constant never touches that), but any attempt to
# actually claim one of them here fails with the exact same "already
# taken" wording claim_seats_atomic itself uses for a real conflict, so it
# reads as bad luck — someone else just grabbed it — rather than a
# deliberate hold. Checked only in this self-service path: an admin can
# still hand-assign one of these seats to a specific registration via the
# Registrations tab's "Allocate Seats" tool
# (manage_registrations.allocate_registration_seats), a separate code path
# this constant doesn't touch. Remove the event's entry (or the whole
# dict) once the row no longer needs to be held.
_PHANTOM_HELD_SEAT_IDS = {
    "onam_2026": {
        "R07C09", "R07C10", "R07C11", "R07C12", "R07C13",
        "R07C14", "R07C15", "R07C16", "R07C17", "R07C18", "R07C19",
    },
}


def create_my_seat_purchase(registration_id: str, body: dict, email: str, origin: str):
    """POST /my/registrations/{id}/seat-purchases — request specific seats,
    priced under a family/person pricing mode. Claims the seats immediately
    (pending payment); nothing is refunded/released until cancelled."""
    existing, err = _load_owned_registration(registration_id, email)
    if err:
        return err
    err = _eligible_for_seat_purchase(existing, origin)
    if err:
        return err

    event_type = existing.get("eventType", "")
    config = get_seat_selection_config(event_type)
    if not config["enabled"]:
        return respond(400, {"error": "Seat selection is not enabled for this event"}, origin)
    window = seat_selection_logic.seat_selection_window_status(config)
    if window == "not_started":
        return respond(400, {"error": f"Seat selection for this event opens on {config['startDate']}."}, origin)
    if window == "ended":
        return respond(400, {"error": f"The seat selection window for this event closed on {config['endDate']}."}, origin)

    free = not config["pricingEnabled"]
    payment_method = str(body.get("paymentMethod", "")).strip().lower()
    if not free and payment_method and payment_method not in config["paymentMethods"]:
        return respond(400, {"error": f"{payment_method} is not an available payment method for this event"}, origin)

    pricing_mode = str(body.get("pricingMode", "")).strip().lower()
    if free and not pricing_mode:
        # The frontend hides the family/person choice entirely once pricing
        # is off (there's nothing to price) — pick a harmless default rather
        # than requiring a client change just to satisfy this still-required
        # argument.
        pricing_mode = "person"
    seat_ids = body.get("seatIds")
    if not isinstance(seat_ids, list) or not seat_ids:
        return respond(400, {"error": "At least one seat must be selected"}, origin)
    seat_ids = [str(seat_id).strip().upper() for seat_id in seat_ids]
    if len(set(seat_ids)) != len(seat_ids):
        return respond(400, {"error": "Duplicate seats selected"}, origin)

    held = _PHANTOM_HELD_SEAT_IDS.get(event_type, set())
    already_held = [seat_id for seat_id in seat_ids if seat_id in held]
    if already_held:
        return respond(400, {"error": f"Seat(s) already taken: {', '.join(already_held)}"}, origin)

    allowance = _seat_allowance(registration_id, existing)
    if len(seat_ids) > allowance["seatsRemaining"]:
        return respond(
            400,
            {
                "error": (
                    f"This registration is allowed up to {allowance['seatsAllowed']} seat(s) "
                    "(one per attendee, excluding children under 6) — "
                    f"{allowance['seatsClaimed']} already claimed, {allowance['seatsRemaining']} remaining."
                ),
                **allowance,
            },
            origin,
        )

    if free:
        # Pricing is off for this event — seats are claimed for free, but
        # only Regular is unconditionally open; zone1/2/3 each require
        # entitlement separately paid for through the seat-upgrade plugin
        # (see _seat_category_caps). Checked here, not just left to the
        # frontend graying those seats out, so a direct API call can't
        # bypass it.
        overrides = seat_selection_logic.get_category_overrides(event_type)
        caps = _seat_category_caps(event_type, registration_id)
        requested_by_category: dict = {}
        for seat_id in seat_ids:
            seat = seat_selection_logic.find_seat(seat_id, overrides)
            if not seat:
                return respond(400, {"error": f"Seat {seat_id} is not a valid seat for this event"}, origin)
            if seat["category"] == "regular":
                continue
            requested_by_category[seat["category"]] = requested_by_category.get(seat["category"], 0) + 1
        for category, requested_count in requested_by_category.items():
            available = caps.get(category, 0)
            if requested_count > available:
                label = ZONE_CATEGORY_LABELS.get(category, category)
                return respond(
                    400,
                    {
                        "error": (
                            f"You're entitled to {available} {label} seat(s) from your zone upgrade — "
                            f"select {available} or fewer, or pick Regular seats instead."
                        ),
                        "categoryCaps": caps,
                    },
                    origin,
                )

    try:
        purchase = seat_purchase_logic.create_purchase_request(
            event_type=event_type,
            registration_id=registration_id,
            email=email,
            name=existing.get("name", ""),
            seat_ids=seat_ids,
            pricing_mode=pricing_mode,
            free=free,
        )
    except ValueError as exc:
        # Covers both plain bad input (empty/invalid seat IDs, an unpriced
        # zone) and a genuine seat conflict (already claimed by someone
        # else) — seat_purchase_logic.create_purchase_request passes
        # claim_seats_atomic's own ValueError straight through for the
        # latter. Either way the client can recover by picking different
        # seats, so one status code is fine here.
        return respond(400, {"error": str(exc)}, origin)
    except RuntimeError as exc:
        logger.exception("Seat purchase creation failed for registration=%s", registration_id)
        return respond(500, {"error": str(exc)}, origin)

    # The allowance check above ran BEFORE this purchase committed — a
    # concurrent second request from this same registration (a double-tap,
    # a retry, two open tabs) could have read the same "not yet claimed"
    # count and passed the same check before either committed, together
    # claiming more seats than the registration is allowed (confirmed this
    # happens under real concurrent load). The individual seat claims
    # themselves can't over-book — that's DynamoDB's own atomic conditional
    # write — but nothing tied the allowance check to this write the same
    # way. Re-check the actual total now that this purchase is committed,
    # and void it if it pushed the registration over its cap, so the client
    # only ever sees the correct final outcome, never a false "success".
    allowance_after = _seat_allowance(registration_id, existing)
    if allowance_after["seatsClaimed"] > allowance_after["seatsAllowed"]:
        seat_purchase_logic.void_purchase_request(
            purchase["purchaseId"], event_type=event_type, seat_ids=seat_ids
        )
        return respond(
            400,
            {
                "error": (
                    f"This registration is allowed up to {allowance_after['seatsAllowed']} seat(s) "
                    "(one per attendee, excluding children under 6) — another request for this "
                    "registration used up the remaining allowance at the same moment. Please "
                    "choose fewer seats."
                ),
                **_seat_allowance(registration_id, existing),
            },
            origin,
        )

    if payment_method and purchase.get("status") != "paid":
        try:
            purchase = seat_purchase_logic.set_payment_method(purchase["purchaseId"], payment_method)
        except RuntimeError:
            logger.exception("Could not stamp paymentMethod on purchase=%s", purchase.get("purchaseId"))

    # An all-Regular (¥0) selection comes back already "paid" — see
    # seat_purchase_logic.create_purchase_request — so it gets the same
    # confirmation email/QR a payment would normally trigger, not the
    # "request received" one (there's nothing pending to tell them about).
    if purchase.get("status") == "paid":
        seat_purchase_emailer.send_paid_email(purchase)
        seat_purchase_emailer.send_admin_notification(purchase, "confirmed")
    else:
        seat_purchase_emailer.send_request_email(purchase)
        seat_purchase_emailer.send_admin_notification(purchase, "requested")
    return respond(201, {"purchase": normalize_purchase(purchase)}, origin)


def _load_owned_purchase(registration_id: str, purchase_id: str, email: str):
    """Fetch a purchase and confirm it belongs to this registration, AND that
    the caller's signed-in email owns that registration (same check as
    _load_owned_registration) — without this second check, a valid token
    could be pointed at someone else's registrationId/purchaseId pair (e.g.
    leaked via a checkout redirect URL) to view, checkout, or cancel their
    seat purchase. Returns (item, error_response); exactly one is non-None."""
    _, err = _load_owned_registration(registration_id, email)
    if err:
        return None, err
    existing = seat_purchase_logic.get_purchase(purchase_id)
    if not existing:
        return None, respond(404, {"error": "Seat purchase request not found"})
    if existing.get("registrationId") != registration_id:
        return None, respond(403, {"error": "You can only manage your own seat purchase requests"})
    return existing, None


def _reconcile_purchase_with_provider(purchase: dict) -> dict:
    """If a checkout is pending, ask the provider directly whether it's
    actually completed yet, rather than waiting on the webhook alone. Safe
    to call repeatedly: mark_paid_from_provider() is idempotent."""
    if purchase.get("status") != "pending":
        return purchase
    provider = purchase.get("paymentProvider", "")
    provider_payment_id = purchase.get("providerPaymentId", "")
    adapter = PAYMENT_ADAPTERS.get(provider)
    if not adapter or not provider_payment_id:
        return purchase

    try:
        result = adapter.get_payment_status(provider_payment_id)
    except Exception:
        logger.exception("Could not check %s payment status for purchase %s", provider, purchase.get("purchaseId"))
        return purchase

    if result.get("status") == "COMPLETED":
        updated, was_newly_paid = seat_purchase_logic.mark_paid_from_provider(
            provider_payment_id, payment_reference=result.get("paymentId", "")
        )
        if updated:
            if was_newly_paid:
                seat_purchase_emailer.send_paid_email(updated)
                seat_purchase_emailer.send_admin_notification(updated, "paid")
            return updated
    return purchase


def get_my_seat_purchase(registration_id: str, purchase_id: str, email: str, origin: str):
    existing, err = _load_owned_purchase(registration_id, purchase_id, email)
    if err:
        return err
    existing = _reconcile_purchase_with_provider(existing)
    return respond(200, {"purchase": normalize_purchase(existing)}, origin)


def checkout_my_seat_purchase(registration_id: str, purchase_id: str, body: dict, email: str, origin: str):
    existing, err = _load_owned_purchase(registration_id, purchase_id, email)
    if err:
        return err

    provider = str(body.get("provider", "")).strip().lower()
    adapter = PAYMENT_ADAPTERS.get(provider)
    if not adapter:
        return respond(400, {"error": "provider must be 'paypay' or 'square'"}, origin)
    config = get_seat_selection_config(existing.get("eventType", ""))
    if not config["enabled"]:
        return respond(400, {"error": "Seat selection is currently closed for this event."}, origin)
    if provider not in config["paymentMethods"]:
        return respond(400, {"error": f"{provider.title()} is not an available payment method for this event"}, origin)
    if existing.get("status") != "pending":
        return respond(400, {"error": "This request is no longer awaiting payment"}, origin)
    if not adapter.is_configured():
        return respond(503, {
            "error": f"{provider.title()} isn't configured yet. Please use bank transfer, "
                     "or contact us to complete this payment."
        }, origin)

    purchase_id_ = existing["purchaseId"]
    zone_names = ", ".join(sorted({row.get("displayName", row.get("category", "")) for row in existing.get("breakdown", [])}))
    description = f"Seats — {zone_names or 'Celebrity Event'} — {existing.get('name', '')}"[:255]
    redirect_url = f"{FRONTEND_URL.rstrip('/')}/my/hall-seat-selection?purchaseId={purchase_id_}&provider={provider}"

    try:
        if provider == "paypay":
            checkout = adapter.create_checkout(
                merchant_payment_id=purchase_id_,
                amount_yen=existing.get("totalAmount", 0),
                description=description,
                redirect_url=redirect_url,
            )
        else:
            checkout = adapter.create_checkout(
                reference_id=purchase_id_,
                amount_yen=existing.get("totalAmount", 0),
                description=description,
                redirect_url=redirect_url,
            )
    except RuntimeError as exc:
        logger.exception("Could not create %s checkout for purchase %s", provider, purchase_id_)
        return respond(502, {"error": f"Could not start {provider.title()} checkout: {exc}"}, origin)

    updated = seat_purchase_logic.record_checkout(
        purchase_id_,
        provider=provider,
        provider_payment_id=checkout["providerPaymentId"],
        checkout_url=checkout["checkoutUrl"],
    )
    return respond(200, {
        "checkoutUrl": updated.get("checkoutUrl", checkout["checkoutUrl"]),
        "provider": provider,
    }, origin)


def _attempt_purchase_refund(purchase: dict) -> dict:
    """Best-effort provider refund for a just-cancelled paid purchase.
    Always records an outcome and never raises — a refund-attempt failure
    must not break the cancellation that already succeeded."""
    purchase_id = purchase["purchaseId"]
    provider = purchase.get("paymentProvider", "")
    payment_id = purchase.get("paymentReference", "")
    adapter = PAYMENT_ADAPTERS.get(provider)

    if not adapter or not adapter.is_configured() or not payment_id:
        try:
            return seat_purchase_logic.record_refund_attempt(purchase_id, refund_status="manual_pending")
        except RuntimeError:
            logger.exception("Could not record manual refund status for purchase %s", purchase_id)
            return purchase

    try:
        if provider == "paypay":
            result = adapter.create_refund(
                payment_id=payment_id,
                merchant_refund_id=f"{purchase_id}-refund",
                amount_yen=purchase.get("totalAmount", 0),
                reason="Customer requested cancellation",
            )
        else:
            result = adapter.create_refund(
                payment_id=payment_id,
                idempotency_key=f"{purchase_id}-refund",
                amount_yen=purchase.get("totalAmount", 0),
                reason="Customer requested cancellation",
            )
        return seat_purchase_logic.record_refund_attempt(
            purchase_id,
            refund_status=f"provider_{str(result.get('status', 'pending')).lower()}",
            refund_reference=result.get("refundId", ""),
        )
    except Exception:
        logger.exception("Could not initiate %s refund for purchase %s", provider, purchase_id)
        try:
            return seat_purchase_logic.record_refund_attempt(purchase_id, refund_status="provider_failed")
        except RuntimeError:
            return purchase


def cancel_my_seat_purchase(registration_id: str, purchase_id: str, email: str, origin: str):
    existing, err = _load_owned_purchase(registration_id, purchase_id, email)
    if err:
        return err
    # Seat Selection switched off for this event: purchases stay visible but are read-only.
    if not get_seat_selection_config(existing.get("eventType", ""))["enabled"]:
        return respond(400, {"error": "Seat selection is currently closed for this event, so this purchase can't be changed here."}, origin)
    try:
        updated = seat_purchase_logic.cancel_purchase_request(purchase_id, registration_id=registration_id)
    except (ValueError, PermissionError) as exc:
        return respond(400, {"error": str(exc)}, origin)

    if updated.get("status") == "refund_requested":
        updated = _attempt_purchase_refund(updated)
        auto_initiated = str(updated.get("refundStatus", "")).startswith("provider_")
        seat_purchase_emailer.send_refund_requested_email(updated, auto_initiated=auto_initiated)
        seat_purchase_emailer.send_admin_notification(
            updated, "refund requested",
            extra_note=f"- Refund status: {updated.get('refundStatus', 'N/A')} (reference: {updated.get('refundReference', 'none')})",
        )
    else:
        seat_purchase_emailer.send_admin_notification(updated, "cancelled")

    return respond(200, {"purchase": normalize_purchase(updated)}, origin)


def _send_update_emails(record: dict, notification_type: str = "update"):
    """After a self-service edit, re-send the confirmation + notify admins.

    Ordinary edits notify the registrant and admins. Slot changes send just
    the refreshed registrant confirmation. Email failures never block the
    update response.
    """
    event_config = emailer.get_event_config(record.get("eventType", "")) or {}
    try:
        emailer.send_confirmation_email(
            record.get("email", ""), record.get("name", ""), event_config,
            is_update=True, registration_details=record,
            subject_prefix="Entry Slot Updated" if notification_type == "slot" else None,
        )
        if notification_type == "update":
            emailer.send_admin_notification(record, event_config, is_update=True)
    except Exception:  # pragma: no cover - emails must never break the update
        pass


def _update_onam(existing: dict, body: dict, email: str, origin: str):
    name = str(body.get("name", body.get("fullName", existing.get("name", "")))).strip()
    phone = str(body.get("phone", existing.get("phone", ""))).strip()
    option = body.get("registrationOption", existing.get("registrationOption", ""))
    counts = body.get("counts") or body.get("attendeeCounts") or existing.get("attendeeCounts") or {}
    payment_method = str(body.get("paymentMethod", existing.get("paymentMethod", ""))).strip()
    requested_action = str(
        (body.get("membership") or {}).get("action", body.get("membershipAction", "none"))
    ).lower()
    # Emails of adults 2…N. Preserve the stored ones when the client omits the
    # field entirely, so a partial update can't wipe the additional adults.
    if "additionalAdultEmails" in body:
        additional_adult_emails = body.get("additionalAdultEmails") or []
    else:
        additional_adult_emails = [
            a.get("email", "") for a in (existing.get("additionalAdults") or [])
        ]

    if not name:
        return respond(400, {"error": "Name is required", "fields": {"name": "Name is required"}}, origin)
    if phone and not validate_phone(phone):
        return respond(400, {"error": "Phone must include country code (e.g. +81 90-1234-5678)", "fields": {"phone": "Use format: +country code and number"}}, origin)

    # Authoritative membership state: email → memberid → dated transactions.
    member_record = get_member_record(email)
    memberid = str(member_record.get("memberid", "")) if member_record else ""
    flag_active = bool(member_record.get("npoMembershipActive")) if member_record else False
    transactions = get_membership_transactions(memberid) if memberid else []
    summary = npo_membership.evaluate(
        transactions, as_of=MEMBERSHIP_AS_OF, renewal_year=RENEWAL_MEMBERSHIP_YEAR
    )
    already_member = flag_active and summary["validNow"]
    covers_renewal = summary["coversRenewal"]

    wants_join = requested_action in {"join", "renew"}
    do_join_or_renew = wants_join and not covers_renewal
    resolved_action = ("renew" if already_member else "join") if do_join_or_renew else "none"

    discount_tier = onam_pricing.resolve_discount_tier(
        valid_now=already_member,
        covers_renewal=covers_renewal,
        do_join_or_renew=do_join_or_renew,
    )

    # The NPO discount is a one-time benefit per event: if this email already
    # drew a discount on another registration for this event, editing this one
    # doesn't grant a second one (joining/renewing membership is unaffected).
    event_type = existing.get("eventType", "")
    registration_id = existing.get("registrationId", "")
    if discount_tier != "none" and email_already_availed_discount(
        email, event_type, exclude_registration_id=registration_id
    ):
        discount_tier = "none"

    def _additional_adult_is_discountable(addl_email: str) -> bool:
        return is_active_member(addl_email) and not email_already_availed_discount(
            addl_email, event_type, exclude_registration_id=registration_id
        )

    # Per-adult tiers: adult 1 is the registrant; adults 2…N are each priced on
    # their own membership. Each email must be valid and distinct.
    adult_count = onam_pricing._int(counts.get("adults", 0)) or 0
    adult_tiers, additional_adults, adult_err = onam_pricing.resolve_additional_adults(
        additional_adult_emails,
        adult_count,
        discount_tier,
        registrant_email=email,
        is_member=_additional_adult_is_discountable,
    )
    if adult_err:
        return respond(400, {"error": adult_err["error"], "fields": adult_err["fields"]}, origin)

    order, err = onam_pricing.compute_order(
        option, counts, discount_tier=discount_tier,
        adult_tiers=adult_tiers, apply_npo_fee=do_join_or_renew
    )
    if err:
        return respond(400, {"error": err}, origin)
    if order["totalAttendees"] < 1:
        return respond(400, {"error": "Add at least one attendee", "fields": {"counts": "Add at least one attendee"}}, origin)

    requested_slots = sum(
        onam_pricing._int(counts.get(key, 0)) or 0
        for key in SEATED_CATEGORIES
    )
    for capacity_key, closed_key, options, label in SEAT_POOLS:
        if order["option"] not in options:
            continue
        closed_error = validate_pool_open(
            existing.get("eventType", ""), requested_slots, existing, origin,
            options, closed_key, label,
        )
        if closed_error:
            return closed_error
        capacity_error = validate_capacity(
            existing.get("eventType", ""), requested_slots, existing, origin,
            options, capacity_key, label,
        )
        if capacity_error:
            return capacity_error

    timestamp = now_iso()

    membership_txn = None
    if do_join_or_renew:
        memb_from, memb_to = npo_membership.year_span(RENEWAL_MEMBERSHIP_YEAR)
        membership_txn = {
            "memberid": memberid or email,
            "transactionNo": next_transaction_no(memberid, transactions),
            "membershipAmountPaid": order["npoFee"],
            "paidDate": timestamp,
            "membDateFrom": memb_from.isoformat() if memb_from else "",
            "membDateTo": memb_to.isoformat() if memb_to else "",
            "paidVia": payment_method or "self-service",
            "email": email,
        }

    membership_summary = {
        "wasMember": already_member,
        "action": resolved_action,
        "year": RENEWAL_MEMBERSHIP_YEAR if do_join_or_renew else None,
        "validUntil": summary["latestTo"] or None,
        "fee": order["npoFee"],
    }

    record = {
        "registrationId": existing["registrationId"],
        "eventType": existing.get("eventType", ""),
        "eventDate": existing.get("eventDate", ""),
        "email": email,
        "name": name,
        "phone": phone,
        "registrationType": "onam",
        "registrationOption": order["option"],
        "registrationOptionLabel": order["optionLabel"],
        "pricingTier": order["pricingTier"],
        "npoName": str(body.get("npoName", existing.get("npoName", ""))).strip(),
        "additionalAdults": additional_adults,
        "attendeeCounts": {
            "adults": onam_pricing._int(counts.get("adults", 0)) or 0,
            "kids914": onam_pricing._int(counts.get("kids914", 0)) or 0,
            "kids68": onam_pricing._int(counts.get("kids68", 0)) or 0,
            "kidsUnder6": onam_pricing._int(counts.get("kidsUnder6", 0)) or 0,
        },
        "lineItems": order["lineItems"],
        "ticketsAmount": order["ticketsAmount"],
        "discountApplied": order["discountApplied"],
        "npoFee": order["npoFee"],
        "totalAmount": order["totalAmount"],
        "membership": membership_summary,
        "paymentMethod": payment_method,
        # Payment status is admin-controlled; a self-service edit preserves it.
        "paymentStatus": existing.get("paymentStatus", "pending"),
        "submittedAt": existing.get("submittedAt", timestamp),
        "updatedAt": timestamp,
    }

    _carry_over_slot_fields(record, existing)

    try:
        dynamodb.Table(REGISTRATIONS_TABLE).put_item(Item=record)
    except ClientError:
        logger.exception("Onam registration self-update write error")
        return respond(500, {"error": "Failed to update registration"}, origin)

    if do_join_or_renew and membership_txn:
        record_membership_transaction(memberid or email, membership_txn)

    _send_update_emails(record)

    return respond(200, {"registration": record}, origin)


def _parse_non_negative_int(value):
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed >= 0 else None


def _update_standard(existing: dict, body: dict, email: str, origin: str):
    name = str(body.get("name", existing.get("name", ""))).strip()
    phone = str(body.get("phone", existing.get("phone", ""))).strip()

    raw_counts = body.get("attendeeCounts") or existing.get("attendeeCounts") or {}
    counts = {}
    for key in ("adult", "teen", "child", "toddler"):
        parsed = _parse_non_negative_int(raw_counts.get(key, 0))
        if parsed is None:
            return respond(400, {"error": "Attendee counts must be whole numbers 0 or greater",
                                 "fields": {"attendeeCounts": "Attendee counts must be whole numbers 0 or greater"}}, origin)
        counts[key] = parsed

    vegetarian_count = _parse_non_negative_int(body.get("vegetarianCount", existing.get("vegetarianCount", 0)))

    if not name:
        return respond(400, {"error": "Name is required", "fields": {"name": "Name is required"}}, origin)
    if phone and not validate_phone(phone):
        return respond(400, {"error": "Phone must include country code (e.g. +81 90-1234-5678)", "fields": {"phone": "Use format: +country code and number"}}, origin)
    if vegetarian_count is None:
        return respond(400, {"error": "Vegetarian count must be a whole number 0 or greater",
                             "fields": {"vegetarianCount": "Vegetarian count must be a whole number 0 or greater"}}, origin)

    total_attendees = sum(counts.values())
    if total_attendees > 0 and vegetarian_count > total_attendees:
        return respond(400, {"error": "Vegetarian count cannot exceed total attendees",
                             "fields": {"vegetarianCount": "Vegetarian count cannot exceed total attendees"}}, origin)

    capacity_error = validate_capacity(
        existing.get("eventType", ""), total_attendees, existing, origin
    )
    if capacity_error:
        return capacity_error

    timestamp = now_iso()
    record = {
        **existing,
        "name": name,
        "phone": phone,
        "whatsapp": str(body.get("whatsapp", existing.get("whatsapp", ""))).strip() or phone,
        "attendeeCounts": counts,
        "vegetarianCount": vegetarian_count,
        "email": email,
        "updatedAt": timestamp,
    }

    try:
        dynamodb.Table(REGISTRATIONS_TABLE).put_item(Item=record)
    except ClientError:
        logger.exception("Standard registration self-update write error")
        return respond(500, {"error": "Failed to update registration"}, origin)

    _send_update_emails(record)

    return respond(200, {"registration": record}, origin)


# ── DELETE /my/registrations/{id} ─────────────────────────────────────────────


def delete_my_registration(registration_id: str, email: str, origin: str):
    existing, err = _load_owned_registration(registration_id, email)
    if err:
        return err

    if not event_user_cancellation_enabled(existing.get("eventType", "")):
        return respond(
            403,
            {
                "error": "Online cancellation is currently unavailable for this event. Please contact us for assistance.",
                "cancellationDisabled": True,
            },
            origin,
        )

    # Deleting is the strongest form of the edits gated above, so it answers to
    # the same two locks. The UI hides the button in both cases; this is what
    # stops a direct API call.
    if entry_allocations.checked_in_seats(existing):
        return respond(
            409,
            {
                "error": "You've been checked in at the venue, so this registration can "
                         "no longer be deleted. Please speak to a volunteer.",
                "checkedIn": True,
                "locked": True,
            },
            origin,
        )
    if entry_allocations.payment_is_complete(existing):
        return respond(
            403,
            {
                "error": "This registration is paid and can no longer be deleted. "
                         "Please contact us if you need to cancel.",
                "locked": True,
            },
            origin,
        )

    try:
        dynamodb.Table(REGISTRATIONS_TABLE).delete_item(
            Key={"registrationId": existing["registrationId"]}
        )
    except ClientError:
        logger.exception("Registration self-delete error for id=%s", registration_id)
        return respond(500, {"error": "Failed to delete registration"}, origin)

    # Hand the entry-slot seats back, exactly as the admin delete path does.
    # Without this a self-delete silently held the slot's capacity forever, so
    # the seats could never be sold again. Runs after the delete and can never
    # fail it: the record is already gone, and a stranded reservation is a
    # capacity problem to log, not an error to report to the registrant.
    try:
        _release_existing_allocations(existing.get("eventType", ""), existing)
    except Exception:
        logger.exception(
            "Could not release entry slots for deleted registration=%s", registration_id
        )

    # Same reasoning for Celebrity Event seats: without this a self-delete
    # would silently hold seats forever. Cancelling each active purchase
    # (rather than just deleting the seats table rows directly) also keeps
    # the purchase records themselves from lingering as "pending"/"paid"
    # against a registration that no longer exists.
    try:
        event_type = existing.get("eventType", "")
        for purchase in seat_purchase_logic.list_purchases_for_registration(registration_id):
            if purchase.get("status") in seat_purchase_logic.ACTIVE_STATUSES:
                seat_purchase_logic.update_purchase_status(purchase["purchaseId"], "cancelled")
        held_seats = [
            item["seatId"]
            for item in seat_selection_logic.get_seats_for_registration(event_type, registration_id)
        ]
        if held_seats:
            seat_selection_logic.release_seats(event_type, held_seats)
    except Exception:
        logger.exception(
            "Could not release seats for deleted registration=%s", registration_id
        )

    return respond(200, {"deleted": True, "registrationId": registration_id}, origin)


# ── /my/registrations/{registrationId}/celebrity-dinner ───────────────────────


def _reconcile_dinner_with_provider(dinner_reg: dict) -> dict:
    """If a checkout is pending, ask the provider directly whether it's
    actually completed yet — same defense-in-depth as the seat-upgrade
    plugin's own reconcile_with_provider: the registrant returning to this
    page (or refreshing it) shouldn't have to wait on the webhook alone.
    Safe to call repeatedly: mark_paid_from_provider() is idempotent.
    """
    if str(dinner_reg.get("status", "pending")).lower() != "pending":
        return dinner_reg
    provider = dinner_reg.get("paymentProvider", "")
    provider_payment_id = dinner_reg.get("providerPaymentId", "")
    adapter = DINNER_PAYMENT_ADAPTERS.get(provider)
    if not adapter or not provider_payment_id:
        return dinner_reg

    try:
        result = adapter.get_payment_status(provider_payment_id)
    except Exception:
        logger.exception("Could not check %s payment status for Celebrity Dinner %s", provider, dinner_reg.get("dinnerRegistrationId"))
        return dinner_reg

    if result.get("status") == "COMPLETED":  # both paypay.py and square.py normalize to this string on success
        updated, was_newly_paid = dinner.mark_paid_from_provider(provider_payment_id, payment_reference=result.get("paymentId", ""))
        if updated:
            if was_newly_paid:
                dinner_emailer.send_paid_email(updated)
                dinner_emailer.send_admin_notification(updated, "paid")
            return updated
    return dinner_reg


def get_my_celebrity_dinner(registration_id: str, email: str, origin: str):
    existing, err = _load_owned_registration(registration_id, email)
    if err:
        return err

    event_type = existing.get("eventType", "")
    try:
        config_item = dinner.get_config(event_type)
        dinner_enabled = dinner.is_event_celebrity_dinner_enabled(event_type)
        dinner_reg = dinner.get_active_dinner_registration_for_registration(registration_id)
        if dinner_reg:
            dinner_reg = _reconcile_dinner_with_provider(dinner_reg)
    except RuntimeError as exc:
        logger.exception("Could not load Celebrity Dinner state for %s", registration_id)
        return respond(500, {"error": str(exc)}, origin)

    config = dinner.normalize_config(config_item, event_type, dinner_enabled)
    remaining_capacity = None
    eligible_counts = dinner.eligible_guest_counts_by_category(existing)
    if config["maxTotalGuests"] > 0:
        others_total = dinner.total_active_dinner_guest_count(
            event_type, exclude_dinner_registration_id=(dinner_reg or {}).get("dinnerRegistrationId", "")
        )
        remaining_capacity = max(config["maxTotalGuests"] - others_total, 0)
        # Clamped per category by the event-wide remaining capacity, same
        # approximation as the seat-upgrade plugin's own dinnerXRemaining
        # fields (see plugins/seat-upgrade/backend/lambdas/seat_upgrade/
        # handler.py's _dinner_remaining) — upsert_my_celebrity_dinner's own
        # maxTotalGuests check above is the authoritative enforcement.
        eligible_counts = {k: min(v, remaining_capacity) for k, v in eligible_counts.items()}
    return respond(
        200,
        {
            "config": config,
            "windowStatus": dinner.registration_window_status(config),
            "eligibleGuestCounts": eligible_counts,
            "registration": dinner.normalize_dinner_registration(dinner_reg) if dinner_reg else None,
            "remainingCapacity": remaining_capacity,
        },
        origin,
    )


def _parse_guest_count(body: dict, field: str) -> int:
    try:
        value = int(body.get(field, 0) or 0)
    except (TypeError, ValueError):
        return -1
    return value


DINNER_CATEGORIES = ("adults", "kids914", "kids68", "kidsUnder6")


def _parse_dinner_guest_counts(body: dict) -> dict:
    """{"adultsVeg", "adultsNonVeg", "kids914Veg", ...} — the 8 veg/non-veg
    sub-counts a PUT body carries as numberOf{Category}{Veg,NonVeg} fields.
    Feeds straight into celebrity_dinner_logic.upsert_dinner_registration's
    guest_counts shape. Invalid/missing values come back as -1 so the
    caller's `any(v < 0 ...)` check catches them uniformly.
    """
    counts = {}
    for category in DINNER_CATEGORIES:
        capitalized = category[0].upper() + category[1:]
        for diet in ("Veg", "NonVeg"):
            counts[f"{category}{diet}"] = _parse_guest_count(body, f"numberOf{capitalized}{diet}")
    return counts


def _dinner_category_totals(guest_counts: dict) -> dict:
    """Collapse the 8 veg/non-veg counts back to one total per age category
    — diet never affects the per-category eligibility cap, only headcount
    does."""
    return {
        category: guest_counts.get(f"{category}Veg", 0) + guest_counts.get(f"{category}NonVeg", 0)
        for category in DINNER_CATEGORIES
    }


def upsert_my_celebrity_dinner(registration_id: str, body: dict, email: str, origin: str):
    existing, err = _load_owned_registration(registration_id, email)
    if err:
        return err

    event_type = existing.get("eventType", "")
    guest_counts = _parse_dinner_guest_counts(body)
    if any(v < 0 for v in guest_counts.values()):
        return respond(400, {"error": "Guest counts must be whole numbers, 0 or greater"}, origin)
    if sum(guest_counts.values()) < 1:
        return respond(400, {"error": "Enter at least 1 guest"}, origin)
    payment_method = str(body.get("paymentMethod", "")).strip()

    try:
        config_item = dinner.get_config(event_type)
        dinner_enabled = dinner.is_event_celebrity_dinner_enabled(event_type)
    except RuntimeError as exc:
        logger.exception("Could not load Celebrity Dinner config for %s", event_type)
        return respond(500, {"error": str(exc)}, origin)

    config = dinner.normalize_config(config_item, event_type, dinner_enabled)
    if not config["enabled"]:
        return respond(400, {"error": "The Celebrity Dinner is not open for this event."}, origin)

    window_status = dinner.registration_window_status(config)
    if window_status == "not_open":
        return respond(400, {"error": "Celebrity Dinner registration has not opened yet.", "windowStatus": window_status}, origin)
    if window_status == "closed":
        return respond(400, {"error": "Celebrity Dinner registration has closed.", "windowStatus": window_status}, origin)
    if payment_method and payment_method not in config["paymentMethods"]:
        return respond(400, {"error": f"{payment_method} is not an available payment method for this event"}, origin)

    # A registration picks one dinner path or the other, never both: the
    # seat-upgrade plugin's own bundled Celebrity Dinner add-on (tied to a
    # seat upgrade) is mutually exclusive with this standalone sign-up.
    try:
        if dinner.has_active_seat_upgrade_dinner(registration_id):
            return respond(
                400,
                {
                    "error": "This registration already has a Celebrity Dinner add-on through a Zone Upgrade "
                             "request. Manage it there instead of registering separately.",
                    "conflictsWithSeatUpgrade": True,
                },
                origin,
            )
    except RuntimeError as exc:
        logger.exception("Could not check seat-upgrade Celebrity Dinner add-on for %s", registration_id)
        return respond(500, {"error": str(exc)}, origin)

    eligible_counts = dinner.eligible_guest_counts_by_category(existing)
    if sum(eligible_counts.values()) <= 0:
        return respond(
            400,
            {"error": "This registration has no attendees eligible for the Celebrity Dinner."},
            origin,
        )
    category_labels = {"adults": "adult", "kids914": "kid (9-14)", "kids68": "kid (6-8)", "kidsUnder6": "kid (under 6)"}
    for category, requested in _dinner_category_totals(guest_counts).items():
        if requested > eligible_counts.get(category, 0):
            return respond(
                400,
                {
                    "error": f"This registration has {eligible_counts.get(category, 0)} {category_labels[category]} "
                             f"guest(s) eligible; {requested} requested.",
                    "eligibleGuestCounts": eligible_counts,
                },
                origin,
            )

    try:
        current = dinner.get_active_dinner_registration_for_registration(registration_id)
        if current and str(current.get("status", "pending")).lower() != "pending":
            return respond(400, {
                "error": "This booking is already paid or being refunded — cancel it before changing guest count."
            }, origin)
        if config["maxTotalGuests"] > 0:
            others_total = dinner.total_active_dinner_guest_count(
                event_type, exclude_dinner_registration_id=(current or {}).get("dinnerRegistrationId", "")
            )
            remaining = config["maxTotalGuests"] - others_total
            requested_total = sum(guest_counts.values())
            if requested_total > remaining:
                return respond(400, {
                    "error": f"Only {max(remaining, 0)} Celebrity Dinner seat(s) left "
                             f"(capacity {config['maxTotalGuests']} reached).",
                    "remainingCapacity": max(remaining, 0),
                }, origin)
        saved = dinner.upsert_dinner_registration(
            event_type, existing, guest_counts, config, payment_method=payment_method, existing=current
        )
    except RuntimeError as exc:
        logger.exception("Could not save Celebrity Dinner registration for %s", registration_id)
        return respond(500, {"error": str(exc)}, origin)

    dinner_emailer.send_request_email(saved, is_update=bool(current))
    dinner_emailer.send_admin_notification(saved, "updated" if current else "booked")
    return respond(200, {"registration": dinner.normalize_dinner_registration(saved)}, origin)


def checkout_my_celebrity_dinner(registration_id: str, body: dict, email: str, origin: str):
    existing, err = _load_owned_registration(registration_id, email)
    if err:
        return err

    event_type = existing.get("eventType", "")
    dinner_reg = dinner.get_active_dinner_registration_for_registration(registration_id)
    if not dinner_reg:
        return respond(404, {"error": "No Celebrity Dinner registration found"}, origin)
    if str(dinner_reg.get("status", "pending")).lower() != "pending":
        return respond(400, {"error": "This booking is no longer awaiting payment"}, origin)

    provider = str(body.get("provider", "")).strip().lower()
    adapter = DINNER_PAYMENT_ADAPTERS.get(provider)
    if not adapter:
        return respond(400, {"error": "provider must be 'paypay' or 'square'"}, origin)

    config = dinner.normalize_config(dinner.get_config(event_type), event_type, dinner.is_event_celebrity_dinner_enabled(event_type))
    if provider not in config["paymentMethods"]:
        return respond(400, {"error": f"{provider.title()} is not an available payment method for this event"}, origin)
    if not adapter.is_configured():
        return respond(503, {
            "error": f"{provider.title()} isn't configured yet. Please use bank transfer, "
                     "or contact us to complete this payment."
        }, origin)

    dinner_registration_id = dinner_reg["dinnerRegistrationId"]
    description = f"Celebrity Dinner — {dinner_reg.get('name', '')}"[:255]
    redirect_url = f"{FRONTEND_URL.rstrip('/')}/my/registrations?celebrityDinnerId={dinner_registration_id}&provider={provider}"

    try:
        if provider == "paypay":
            checkout = adapter.create_checkout(
                merchant_payment_id=dinner_registration_id,
                amount_yen=dinner_reg.get("totalAmount", 0),
                description=description,
                redirect_url=redirect_url,
            )
        else:
            checkout = adapter.create_checkout(
                reference_id=dinner_registration_id,
                amount_yen=dinner_reg.get("totalAmount", 0),
                description=description,
                redirect_url=redirect_url,
            )
    except RuntimeError as exc:
        logger.exception("Could not create %s checkout for Celebrity Dinner %s", provider, dinner_registration_id)
        return respond(502, {"error": f"Could not start {provider.title()} checkout: {exc}"}, origin)

    try:
        updated = dinner.record_checkout(
            event_type, dinner_registration_id,
            provider=provider,
            provider_payment_id=checkout["providerPaymentId"],
            checkout_url=checkout["checkoutUrl"],
        )
    except (ValueError, RuntimeError) as exc:
        return respond(400, {"error": str(exc)}, origin)

    return respond(200, {
        "checkoutUrl": updated.get("checkoutUrl", checkout["checkoutUrl"]),
        "provider": provider,
    }, origin)


def _attempt_refund_dinner(event_type: str, dinner_reg: dict) -> dict:
    """Best-effort provider refund for a just-cancelled paid booking — mirrors
    the seat-upgrade plugin's own attempt_refund. Always records an outcome
    and never raises: a refund-attempt failure must not break the
    cancellation that already succeeded."""
    dinner_registration_id = dinner_reg["dinnerRegistrationId"]
    provider = dinner_reg.get("paymentProvider", "")
    payment_id = dinner_reg.get("paymentReference", "")
    adapter = DINNER_PAYMENT_ADAPTERS.get(provider)

    if not adapter or not adapter.is_configured() or not payment_id:
        try:
            return dinner.record_refund_attempt(event_type, dinner_registration_id, refund_status="manual_pending")
        except RuntimeError:
            logger.exception("Could not record manual refund status for Celebrity Dinner %s", dinner_registration_id)
            return dinner_reg

    try:
        if provider == "paypay":
            result = adapter.create_refund(
                payment_id=payment_id,
                merchant_refund_id=f"{dinner_registration_id}-refund",
                amount_yen=dinner_reg.get("totalAmount", 0),
                reason="Customer requested cancellation",
            )
        else:
            result = adapter.create_refund(
                payment_id=payment_id,
                idempotency_key=f"{dinner_registration_id}-refund",
                amount_yen=dinner_reg.get("totalAmount", 0),
                reason="Customer requested cancellation",
            )
        return dinner.record_refund_attempt(
            event_type, dinner_registration_id,
            refund_status=f"provider_{str(result.get('status', 'pending')).lower()}",
            refund_reference=result.get("refundId", ""),
        )
    except Exception as exc:
        logger.exception("Could not initiate %s refund for Celebrity Dinner %s", provider, dinner_registration_id)
        try:
            return dinner.record_refund_attempt(
                event_type, dinner_registration_id, refund_status="provider_failed", refund_error=str(exc)
            )
        except RuntimeError:
            return dinner_reg


def delete_my_celebrity_dinner(registration_id: str, email: str, origin: str):
    existing, err = _load_owned_registration(registration_id, email)
    if err:
        return err

    event_type = existing.get("eventType", "")
    current = dinner.get_active_dinner_registration_for_registration(registration_id)
    if not current:
        return respond(404, {"error": "No Celebrity Dinner registration found"}, origin)

    config = dinner.normalize_config(dinner.get_config(event_type), event_type, dinner.is_event_celebrity_dinner_enabled(event_type))
    if not dinner.cancellation_allowed(config):
        return respond(400, {"error": "Cancellations are not open for this event right now."}, origin)

    try:
        updated = dinner.cancel_dinner_registration(event_type, current.get("dinnerRegistrationId", ""))
    except ValueError as exc:
        return respond(400, {"error": str(exc)}, origin)
    except RuntimeError as exc:
        logger.exception("Could not cancel Celebrity Dinner registration for %s", registration_id)
        return respond(500, {"error": str(exc)}, origin)

    if str(updated.get("status", "")).lower() == "refund_requested":
        updated = _attempt_refund_dinner(event_type, updated)
        auto_initiated = str(updated.get("refundStatus", "")).startswith("provider_")
        dinner_emailer.send_refund_requested_email(updated, auto_initiated=auto_initiated)
        dinner_emailer.send_admin_notification(
            updated, "refund requested",
            extra_note=f"- Refund status: {updated.get('refundStatus', 'N/A')} (reference: {updated.get('refundReference', 'none')})",
        )
    else:
        dinner_emailer.send_admin_notification(updated, "cancelled")

    return respond(200, {"deleted": True, "registrationId": registration_id, "registration": dinner.normalize_dinner_registration(updated)}, origin)


# ── /my/celebrity-dinner/{eventType} — a Celebrity Dinner sign-up for ─────────
# someone with NO underlying event registration at all (a "guest" booking).
# Mirrors the registration-scoped routes above field-for-field, but is
# identified by (eventType, email) instead of registrationId, and has no
# per-category eligibility cap — see celebrity_dinner_logic.py's module
# docstring and get_active_guest_dinner_registration.


def get_my_guest_celebrity_dinner(event_type: str, email: str, origin: str):
    try:
        config_item = dinner.get_config(event_type)
        dinner_enabled = dinner.is_event_celebrity_dinner_enabled(event_type)
        dinner_reg = dinner.get_active_guest_dinner_registration(event_type, email)
        if dinner_reg:
            dinner_reg = _reconcile_dinner_with_provider(dinner_reg)
    except RuntimeError as exc:
        logger.exception("Could not load guest Celebrity Dinner state for event=%s email=%s", event_type, email)
        return respond(500, {"error": str(exc)}, origin)

    config = dinner.normalize_config(config_item, event_type, dinner_enabled)
    remaining_capacity = None
    if config["maxTotalGuests"] > 0:
        others_total = dinner.total_active_dinner_guest_count(
            event_type, exclude_dinner_registration_id=(dinner_reg or {}).get("dinnerRegistrationId", "")
        )
        remaining_capacity = max(config["maxTotalGuests"] - others_total, 0)
    return respond(
        200,
        {
            "config": config,
            "windowStatus": dinner.registration_window_status(config),
            "registration": dinner.normalize_dinner_registration(dinner_reg) if dinner_reg else None,
            "remainingCapacity": remaining_capacity,
        },
        origin,
    )


def upsert_my_guest_celebrity_dinner(event_type: str, body: dict, email: str, origin: str):
    name = str(body.get("name", "")).strip()
    if not name:
        return respond(400, {"error": "Name is required"}, origin)

    guest_counts = _parse_dinner_guest_counts(body)
    if any(v < 0 for v in guest_counts.values()):
        return respond(400, {"error": "Guest counts must be whole numbers, 0 or greater"}, origin)
    if sum(guest_counts.values()) < 1:
        return respond(400, {"error": "Enter at least 1 guest"}, origin)
    payment_method = str(body.get("paymentMethod", "")).strip()

    try:
        config_item = dinner.get_config(event_type)
        dinner_enabled = dinner.is_event_celebrity_dinner_enabled(event_type)
    except RuntimeError as exc:
        logger.exception("Could not load Celebrity Dinner config for %s", event_type)
        return respond(500, {"error": str(exc)}, origin)

    config = dinner.normalize_config(config_item, event_type, dinner_enabled)
    if not config["enabled"]:
        return respond(400, {"error": "The Celebrity Dinner is not open for this event."}, origin)

    window_status = dinner.registration_window_status(config)
    if window_status == "not_open":
        return respond(400, {"error": "Celebrity Dinner registration has not opened yet.", "windowStatus": window_status}, origin)
    if window_status == "closed":
        return respond(400, {"error": "Celebrity Dinner registration has closed.", "windowStatus": window_status}, origin)
    if payment_method and payment_method not in config["paymentMethods"]:
        return respond(400, {"error": f"{payment_method} is not an available payment method for this event"}, origin)

    try:
        current = dinner.get_active_guest_dinner_registration(event_type, email)
        if current and str(current.get("status", "pending")).lower() != "pending":
            return respond(400, {
                "error": "This booking is already paid or being refunded — cancel it before changing guest count."
            }, origin)
        if config["maxTotalGuests"] > 0:
            others_total = dinner.total_active_dinner_guest_count(
                event_type, exclude_dinner_registration_id=(current or {}).get("dinnerRegistrationId", "")
            )
            remaining = config["maxTotalGuests"] - others_total
            requested_total = sum(guest_counts.values())
            if requested_total > remaining:
                return respond(400, {
                    "error": f"Only {max(remaining, 0)} Celebrity Dinner seat(s) left "
                             f"(capacity {config['maxTotalGuests']} reached).",
                    "remainingCapacity": max(remaining, 0),
                }, origin)
        saved = dinner.upsert_dinner_registration(
            event_type,
            {"registrationId": "", "email": email, "name": name},
            guest_counts,
            config,
            payment_method=payment_method,
            existing=current,
        )
    except RuntimeError as exc:
        logger.exception("Could not save guest Celebrity Dinner registration for event=%s email=%s", event_type, email)
        return respond(500, {"error": str(exc)}, origin)

    dinner_emailer.send_request_email(saved, is_update=bool(current))
    dinner_emailer.send_admin_notification(saved, "updated" if current else "booked")
    return respond(200, {"registration": dinner.normalize_dinner_registration(saved)}, origin)


def checkout_my_guest_celebrity_dinner(event_type: str, body: dict, email: str, origin: str):
    dinner_reg = dinner.get_active_guest_dinner_registration(event_type, email)
    if not dinner_reg:
        return respond(404, {"error": "No Celebrity Dinner registration found"}, origin)
    if str(dinner_reg.get("status", "pending")).lower() != "pending":
        return respond(400, {"error": "This booking is no longer awaiting payment"}, origin)

    provider = str(body.get("provider", "")).strip().lower()
    adapter = DINNER_PAYMENT_ADAPTERS.get(provider)
    if not adapter:
        return respond(400, {"error": "provider must be 'paypay' or 'square'"}, origin)

    config = dinner.normalize_config(dinner.get_config(event_type), event_type, dinner.is_event_celebrity_dinner_enabled(event_type))
    if provider not in config["paymentMethods"]:
        return respond(400, {"error": f"{provider.title()} is not an available payment method for this event"}, origin)
    if not adapter.is_configured():
        return respond(503, {
            "error": f"{provider.title()} isn't configured yet. Please use bank transfer, "
                     "or contact us to complete this payment."
        }, origin)

    dinner_registration_id = dinner_reg["dinnerRegistrationId"]
    description = f"Celebrity Dinner — {dinner_reg.get('name', '')}"[:255]
    redirect_url = f"{FRONTEND_URL.rstrip('/')}/celebrity-dinner?celebrityDinnerId={dinner_registration_id}&provider={provider}"

    try:
        if provider == "paypay":
            checkout = adapter.create_checkout(
                merchant_payment_id=dinner_registration_id,
                amount_yen=dinner_reg.get("totalAmount", 0),
                description=description,
                redirect_url=redirect_url,
            )
        else:
            checkout = adapter.create_checkout(
                reference_id=dinner_registration_id,
                amount_yen=dinner_reg.get("totalAmount", 0),
                description=description,
                redirect_url=redirect_url,
            )
    except RuntimeError as exc:
        logger.exception("Could not create %s checkout for guest Celebrity Dinner %s", provider, dinner_registration_id)
        return respond(502, {"error": f"Could not start {provider.title()} checkout: {exc}"}, origin)

    try:
        updated = dinner.record_checkout(
            event_type, dinner_registration_id,
            provider=provider,
            provider_payment_id=checkout["providerPaymentId"],
            checkout_url=checkout["checkoutUrl"],
        )
    except (ValueError, RuntimeError) as exc:
        return respond(400, {"error": str(exc)}, origin)

    return respond(200, {
        "checkoutUrl": updated.get("checkoutUrl", checkout["checkoutUrl"]),
        "provider": provider,
    }, origin)


def delete_my_guest_celebrity_dinner(event_type: str, email: str, origin: str):
    current = dinner.get_active_guest_dinner_registration(event_type, email)
    if not current:
        return respond(404, {"error": "No Celebrity Dinner registration found"}, origin)

    config = dinner.normalize_config(dinner.get_config(event_type), event_type, dinner.is_event_celebrity_dinner_enabled(event_type))
    if not dinner.cancellation_allowed(config):
        return respond(400, {"error": "Cancellations are not open for this event right now."}, origin)

    try:
        updated = dinner.cancel_dinner_registration(event_type, current.get("dinnerRegistrationId", ""))
    except ValueError as exc:
        return respond(400, {"error": str(exc)}, origin)
    except RuntimeError as exc:
        logger.exception("Could not cancel guest Celebrity Dinner registration for event=%s email=%s", event_type, email)
        return respond(500, {"error": str(exc)}, origin)

    if str(updated.get("status", "")).lower() == "refund_requested":
        updated = _attempt_refund_dinner(event_type, updated)
        auto_initiated = str(updated.get("refundStatus", "")).startswith("provider_")
        dinner_emailer.send_refund_requested_email(updated, auto_initiated=auto_initiated)
        dinner_emailer.send_admin_notification(
            updated, "refund requested",
            extra_note=f"- Refund status: {updated.get('refundStatus', 'N/A')} (reference: {updated.get('refundReference', 'none')})",
        )
    else:
        dinner_emailer.send_admin_notification(updated, "cancelled")

    return respond(200, {"deleted": True, "registration": dinner.normalize_dinner_registration(updated)}, origin)


def lambda_handler(event: dict[str, Any], context: Any):
    if event.get("httpMethod") == "OPTIONS":
        return respond(200, {})

    headers = {k.lower(): v for k, v in (event.get("headers") or {}).items()}
    origin = headers.get("origin", "*")

    if not TOKEN_SECRET:
        logger.error("OTP_TOKEN_SECRET is not configured.")
        return respond(500, {"error": "Server is not configured for this request."}, origin)

    token = headers.get("authorization", "").strip()
    payload = verify_token(token)
    if not payload:
        return respond(401, {"error": "Please sign in again to continue."}, origin)

    email = str(payload.get("email", "")).strip().lower()
    if not email:
        return respond(401, {"error": "Please sign in again to continue."}, origin)

    http_method = event.get("httpMethod")
    path_params = event.get("pathParameters") or {}
    registration_id = str(path_params.get("registrationId", "")).strip()
    purchase_id = str(path_params.get("purchaseId", "")).strip()
    resource = event.get("resource", "") or ""
    is_seat_purchase_route = "/seat-purchases" in resource
    is_checkout_route = resource.endswith("/checkout")

    if is_seat_purchase_route:
        if not registration_id:
            return respond(400, {"error": "Missing registrationId"}, origin)
        if http_method == "GET":
            if purchase_id:
                return get_my_seat_purchase(registration_id, purchase_id, email, origin)
            return list_my_seat_purchases(registration_id, email, origin)
        if http_method == "POST":
            if is_checkout_route:
                if not purchase_id:
                    return respond(400, {"error": "Missing purchaseId"}, origin)
                return checkout_my_seat_purchase(registration_id, purchase_id, parse_json_body(event), email, origin)
            return create_my_seat_purchase(registration_id, parse_json_body(event), email, origin)
        if http_method == "DELETE":
            if not purchase_id:
                return respond(400, {"error": "Missing purchaseId"}, origin)
            return cancel_my_seat_purchase(registration_id, purchase_id, email, origin)
        return respond(405, {"error": "Method not allowed"}, origin)

    if http_method == "POST" and resource.endswith("/celebrity-dinner/checkout"):
        if not registration_id:
            return respond(400, {"error": "Missing registrationId"}, origin)
        return checkout_my_celebrity_dinner(registration_id, parse_json_body(event), email, origin)

    if http_method == "GET" and resource.endswith("/celebrity-dinner"):
        if not registration_id:
            return respond(400, {"error": "Missing registrationId"}, origin)
        return get_my_celebrity_dinner(registration_id, email, origin)

    if http_method == "PUT" and resource.endswith("/celebrity-dinner"):
        if not registration_id:
            return respond(400, {"error": "Missing registrationId"}, origin)
        return upsert_my_celebrity_dinner(registration_id, parse_json_body(event), email, origin)

    if http_method == "DELETE" and resource.endswith("/celebrity-dinner"):
        if not registration_id:
            return respond(400, {"error": "Missing registrationId"}, origin)
        return delete_my_celebrity_dinner(registration_id, email, origin)

    if resource == "/my/celebrity-dinner/{eventType}" or resource == "/my/celebrity-dinner/{eventType}/checkout":
        event_type = str(path_params.get("eventType", "")).strip()
        if not event_type:
            return respond(400, {"error": "Missing eventType"}, origin)
        if http_method == "GET":
            return get_my_guest_celebrity_dinner(event_type, email, origin)
        if http_method == "PUT":
            return upsert_my_guest_celebrity_dinner(event_type, parse_json_body(event), email, origin)
        if http_method == "DELETE":
            return delete_my_guest_celebrity_dinner(event_type, email, origin)
        if http_method == "POST" and resource.endswith("/checkout"):
            return checkout_my_guest_celebrity_dinner(event_type, parse_json_body(event), email, origin)
        return respond(405, {"error": "Method not allowed"}, origin)

    if http_method == "GET":
        return list_my_registrations(email, origin)

    if http_method == "PUT" and resource.endswith("/slot"):
        if not registration_id:
            return respond(400, {"error": "Missing registrationId"}, origin)
        return change_my_registration_slot(registration_id, parse_json_body(event), email, origin)

    if http_method == "PUT":
        if not registration_id:
            return respond(400, {"error": "Missing registrationId"}, origin)
        return update_my_registration(registration_id, parse_json_body(event), email, origin)

    if http_method == "DELETE":
        if not registration_id:
            return respond(400, {"error": "Missing registrationId"}, origin)
        return delete_my_registration(registration_id, email, origin)

    return respond(405, {"error": "Method not allowed"}, origin)
