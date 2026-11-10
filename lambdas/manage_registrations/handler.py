"""
Lambda: /admin/registration (+ item) and /admin/users
Protected by Cognito JWT authorizer.

Responsibilities:
  - POST   /admin/registration              → create or update a registration
  - DELETE /admin/registration/{id}         → delete a registration
    - POST   /admin/registrations/bulk        → batch payment/email actions
  - GET    /admin/users                     → members master list + NPO status

Onam registrations are priced server-side via onam_pricing (the same
authoritative rules the public flow uses); the admin can set the option,
attendee counts, and membership action, but never the amount. Standard events
keep the legacy attendee-count schema.
"""

import json
import os
import re
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Optional

import boto3
from botocore.exceptions import ClientError

from boto3.dynamodb.conditions import Key, Attr

import onam_pricing
import npo_membership
import emailer
import entry_slots_logic
import entry_allocations
import seat_selection_logic
import seat_purchase_logic
import seat_purchase_emailer
import qr_email
import celebrity_dinner_logic
import celebrity_dinner_emailer

dynamodb = boto3.resource("dynamodb", region_name=os.environ["REGION"])
REGISTRATIONS_TABLE = os.environ["DYNAMODB_TABLE"]
MEMBERSHIP_TABLE = os.environ.get("MEMBERSHIP_TABLE", "")
MEMBERS_TABLE = os.environ.get("MEMBERS_TABLE", "")
ENTRY_SLOTS_TABLE = os.environ.get("ENTRY_SLOTS_TABLE", "")
SLOT_RESERVATIONS_TABLE = os.environ.get("SLOT_RESERVATIONS_TABLE", "")
ENTRY_ALLOCATIONS_TABLE = os.environ.get("ENTRY_ALLOCATIONS_TABLE", "")

RENEWAL_MEMBERSHIP_YEAR = os.environ.get("RENEWAL_MEMBERSHIP_YEAR", "2026-27")
MEMBERSHIP_AS_OF = os.environ.get("MEMBERSHIP_AS_OF", "")


def _required_seats_for_registration(registration: dict) -> int:
    """Map attendeeCounts (Onam or standard schema) to entry-slot seats."""
    return entry_allocations.required_seats_for_registration(registration)


def _celebrity_seats_allowed(registration: dict) -> int:
    """Adults + kids 6-14 only — kids under 6 don't need their own seat.
    Handles both the Onam schema (adults/kids914/kids68/kidsUnder6) and the
    standard one (adult/teen/child/toddler). Mirrors my_registrations'
    own _celebrity_seats_allowed exactly — this is the cap
    allocate_registration_seats enforces unless the event's
    adminOverrideMaxSeats is on.

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


def _carry_over_slot_fields(record: dict, existing: Optional[dict]) -> None:
    """Preserve slotId/slotReservationId/slotName across a full-record rewrite.

    save_onam_registration/save_standard_registration rebuild the record from
    scratch, so without this the entry slot would silently disappear on edit.
    """
    if not existing:
        return
    for field in ("slotId", "slotReservationId", "slotName", "slotSeats", "allocations", "registrationPassToken"):
        if field in existing:
            record[field] = existing[field]

# Payment lifecycle the admin controls: a new record starts "pending"; the admin
# marks it "paid" (or "refunded"). Anything unrecognised falls back to "pending".
VALID_PAYMENT_STATUSES = {"pending", "paid", "refunded"}


def resolve_payment_status(value, fallback="pending"):
    status = str(value or "").strip().lower()
    return status if status in VALID_PAYMENT_STATUSES else fallback


def resolve_paid_amount(new_status, prior_status, prior_paid_amount, order_total):
    """Snapshot the amount actually received against the order.

    - Transition into "paid": capture the current order total as paid.
    - Transition into "refunded": money returned, so paid drops to 0.
    - Otherwise: preserve whatever was previously recorded.

    The difference the admin cares about after an edit is then simply
    order_total − paidAmount (see balance_due).
    """
    prior_paid = onam_pricing._int(prior_paid_amount) or 0
    if new_status == "paid" and prior_status != "paid":
        return onam_pricing._int(order_total) or 0
    if new_status == "refunded":
        return 0
    return prior_paid


def balance_due(order_total, paid_amount):
    """Outstanding balance: positive = registrant owes more, negative = overpaid."""
    return (onam_pricing._int(order_total) or 0) - (onam_pricing._int(paid_amount) or 0)


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


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def parse_json_body(event: dict[str, Any]) -> dict[str, Any]:
    if event.get("isBase64Encoded"):
        return {}
    try:
        parsed = json.loads(event.get("body") or "{}")
        return parsed if isinstance(parsed, dict) else {}
    except json.JSONDecodeError:
        return {}


def validate_email(email: str) -> bool:
    return bool(re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", email))


def validate_phone(phone: str) -> bool:
    return bool(re.match(r"^\+\d{1,4}[\s\-]?\d[\d\s\-]{5,18}$", phone.strip()))


def parse_non_negative_int(value, default=0):
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed >= 0 else None

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
            if exclude_registration_id and item.get("registrationId") == exclude_registration_id:
                continue
            counts = item.get("attendeeCounts", {}) or {}
            attendee_keys = ("adults", "kids914", "kids68") if options else ("adult", "teen", "child", "toddler")
            total += sum(int(counts.get(key, 0) or 0) for key in attendee_keys)
        if "LastEvaluatedKey" not in response:
            return total
        scan_kwargs["ExclusiveStartKey"] = response["LastEvaluatedKey"]


def validate_capacity(event_type: str, requested_slots: int, existing: Optional[dict], options: tuple[str, ...] = (), setting_key: str = "capacity", label: str = "Event"):
    """Return an over-capacity response, or None when the registration fits."""
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

    used_slots = get_capacity_used(
        event_type,
        options=options,
        exclude_registration_id=(existing or {}).get("registrationId", ""),
    )
    available_slots = max(capacity - used_slots, 0)
    if used_slots + requested_slots <= capacity:
        return None
    return respond(
        400,
        {
            "error": (
                f"{label} has {available_slots} available slot(s). "
                f"This registration requires {requested_slots} slot(s)."
            ),
            "capacityReached": True,
            "capacity": capacity,
            "usedSlots": used_slots,
            "availableSlots": available_slots,
            "requestedSlots": requested_slots,
        },
    )


# ── NPO membership helpers (mirrors register + membership Lambdas) ────────────
# Membership validity is by date period, keyed by memberid: resolve email →
# memberid via the members master table, then query the npo_membership table for
# transactions whose [membDateFrom, membDateTo] range covers the reference date.


def get_member_record(email: str) -> Optional[dict]:
    """Resolve email → members-table record (memberid, npoMembershipActive)."""
    if not MEMBERS_TABLE or not email:
        return None
    try:
        return dynamodb.Table(MEMBERS_TABLE).get_item(Key={"email": email}).get("Item")
    except ClientError as exc:
        print(f"Members read error for {email}: {exc}")
        return None


def get_membership_transactions(memberid: str) -> list:
    """All npo_membership transactions for a memberid (empty if none/unset)."""
    if not MEMBERSHIP_TABLE or not memberid:
        return []
    try:
        resp = dynamodb.Table(MEMBERSHIP_TABLE).query(
            KeyConditionExpression=Key("memberid").eq(str(memberid))
        )
        return resp.get("Items", [])
    except ClientError as exc:
        print(f"Membership query error for {memberid}: {exc}")
        return []


def next_transaction_no(memberid: str, transactions: list) -> str:
    highest = 0
    for txn in transactions or []:
        try:
            highest = max(highest, int(str(txn.get("transactionNo", "0"))))
        except (TypeError, ValueError):
            continue
    return str(highest + 1)


def is_active_member(email: str) -> bool:
    """True if `email` is a current NPO member (flag active + a covering period).

    Used to price ADDITIONAL adults: each is discounted on their own membership.
    """
    email = (email or "").strip().lower()
    if not email or not validate_email(email):
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


def record_membership_transaction(memberid: str, txn: dict) -> bool:
    """Append a paid NPO transaction and flip the members flag active."""
    if not MEMBERSHIP_TABLE or not memberid:
        return False
    try:
        dynamodb.Table(MEMBERSHIP_TABLE).put_item(Item=txn)
    except ClientError as exc:
        print(f"Membership write error for {memberid}: {exc}")
        return False
    if MEMBERS_TABLE and txn.get("email"):
        try:
            dynamodb.Table(MEMBERS_TABLE).update_item(
                Key={"email": txn["email"]},
                UpdateExpression="SET npoMembershipActive = :t, memberid = if_not_exists(memberid, :m)",
                ExpressionAttributeValues={":t": True, ":m": str(memberid)},
            )
        except ClientError as exc:
            print(f"Members flag update error for {txn.get('email')}: {exc}")
    return True


def scan_all(table) -> list[dict]:
    items: list[dict] = []
    scan_kwargs: dict[str, Any] = {}
    while True:
        response = table.scan(**scan_kwargs)
        items.extend(response.get("Items", []))
        if "LastEvaluatedKey" not in response:
            break
        scan_kwargs["ExclusiveStartKey"] = response["LastEvaluatedKey"]
    return items


# ── GET /admin/users — members master list annotated with NPO status ──────────


def _last_active_period(transactions: list) -> dict:
    """Return the [from, to] range of the transaction with the latest end date.

    "Last active period" is the most recent membership window the member paid
    for. Returns {"from": "", "to": ""} when there are no dated transactions.
    """
    latest = None
    latest_to = None
    for txn in transactions or []:
        end = npo_membership._to_date(txn.get("membDateTo"))
        if end and (latest_to is None or end > latest_to):
            latest_to = end
            latest = txn
    if not latest:
        return {"from": "", "to": ""}
    start = npo_membership._to_date(latest.get("membDateFrom"))
    end = npo_membership._to_date(latest.get("membDateTo"))
    return {
        "from": start.isoformat() if start else "",
        "to": end.isoformat() if end else "",
    }


def _covers_year(transactions: list, year_code: str) -> bool:
    start, _ = npo_membership.year_span(year_code)
    return bool(start) and any(npo_membership._period_covers(t, start) for t in transactions or [])


def _had_earlier_membership(transactions: list, year_code: str) -> bool:
    """True if any dated membership period ended before `year_code` started."""
    start, _ = npo_membership.year_span(year_code)
    if not start:
        return False
    for txn in transactions or []:
        end = npo_membership._to_date(txn.get("membDateTo"))
        if end and end < start:
            return True
    return False


def _year_statuses(transactions: list) -> dict:
    """Admin-list NPO status per membership year.

    2025-26: Member / Non-member.
    2026-27: Renewed (also covered 2025-26); Returning Member (covered 2026-27, not 2025-26,
    but had an earlier paid period); New Member (covered 2026-27 and never before); or Non-member.
    """
    in_2025 = _covers_year(transactions, "2025-26")
    in_2026 = _covers_year(transactions, "2026-27")
    if not in_2026:
        status_2026 = "Non-member"
    elif in_2025:
        status_2026 = "Renewed"
    elif _had_earlier_membership(transactions, "2025-26"):
        status_2026 = "Returning Member"
    else:
        status_2026 = "New Member"
    return {
        "npoStatus2025": "Member" if in_2025 else "Non-member",
        "npoStatus2026": status_2026,
    }


def list_members() -> list[dict]:
    """Members master list with NPO status and last active period (no registrations)."""
    if not MEMBERS_TABLE:
        return []

    members = scan_all(dynamodb.Table(MEMBERS_TABLE))

    # NPO transactions grouped by memberid for period evaluation.
    txns_by_id: dict[str, list] = {}
    if MEMBERSHIP_TABLE:
        for txn in scan_all(dynamodb.Table(MEMBERSHIP_TABLE)):
            mid = str(txn.get("memberid", "")).strip()
            if mid:
                txns_by_id.setdefault(mid, []).append(txn)

    result = []
    for member in members:
        email = str(member.get("email", "")).strip().lower()
        if not email:
            continue

        memberid = str(member.get("memberid", "")).strip()
        flag_active = bool(member.get("npoMembershipActive"))
        transactions = txns_by_id.get(memberid, [])
        summary = npo_membership.evaluate(
            transactions, as_of=MEMBERSHIP_AS_OF, renewal_year=RENEWAL_MEMBERSHIP_YEAR
        )
        is_member = flag_active and summary["validNow"]

        result.append({
            "email": email,
            "name": member.get("fullname", "") or member.get("memberName", ""),
            "phone": member.get("phone", "") or member.get("mobile", ""),
            "memberId": memberid or None,
            "isMember": is_member,
            "npoFlagActive": flag_active,
            "renewedForNextYear": summary["coversRenewal"],
            "lastActivePeriod": _last_active_period(transactions),
            # Admin-maintained WhatsApp group joining status per membership year ({"2026-27": "joined"}).
            "whatsappGroup": member.get("whatsappGroup") if isinstance(member.get("whatsappGroup"), dict) else {},
            **_year_statuses(transactions),
        })

    result.sort(key=lambda m: str(m.get("name", "")).lower() or m["email"])
    return result


# ── POST /admin/registration — create or update ───────────────────────────────


def save_onam_registration(body: dict, existing: Optional[dict]):
    name = str(body.get("name", "")).strip()
    email = str(body.get("email", "")).strip().lower()
    phone = str(body.get("phone", "")).strip()
    option = body.get("registrationOption", "")
    counts = body.get("counts") or body.get("attendeeCounts") or {}
    payment_method = str(body.get("paymentMethod", "")).strip()
    # Payment status: use the submitted value if valid, else keep the stored one
    # (new records default to "pending"). Track the prior value to detect the
    # transition into "paid" so we only send the payment-received email once.
    prior_payment_status = resolve_payment_status((existing or {}).get("paymentStatus"))
    payment_status = (
        resolve_payment_status(body.get("paymentStatus"), fallback=prior_payment_status)
        if "paymentStatus" in body
        else prior_payment_status
    )
    requested_action = str((body.get("membership") or {}).get("action", body.get("membershipAction", "none"))).lower()
    # Emails of adults 2…N. Preserve the stored ones when the field is omitted so
    # an edit that doesn't touch the party can't wipe the additional adults.
    if "additionalAdultEmails" in body:
        additional_adult_emails = body.get("additionalAdultEmails") or []
    else:
        additional_adult_emails = [
            a.get("email", "") for a in ((existing or {}).get("additionalAdults") or [])
        ]

    if not name:
        return respond(400, {"error": "Name is required", "fields": {"name": "Name is required"}})
    if not email or not validate_email(email):
        return respond(400, {"error": "Valid email is required", "fields": {"email": "Valid email is required"}})
    if phone and not validate_phone(phone):
        return respond(400, {"error": "Phone must include country code (e.g. +81 90-1234-5678)", "fields": {"phone": "Use format: +country code and number"}})

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

    # Per-adult tiers: adult 1 is the registrant; adults 2…N are each priced on
    # their own membership. Each email must be valid and distinct.
    adult_count = onam_pricing._int(counts.get("adults", 0)) or 0
    adult_tiers, additional_adults, adult_err = onam_pricing.resolve_additional_adults(
        additional_adult_emails,
        adult_count,
        discount_tier,
        registrant_email=email,
        is_member=is_active_member,
    )
    if adult_err:
        return respond(400, {"error": adult_err["error"], "fields": adult_err["fields"]})

    order, err = onam_pricing.compute_order(
        option, counts, discount_tier=discount_tier,
        adult_tiers=adult_tiers, apply_npo_fee=do_join_or_renew
    )
    if err:
        return respond(400, {"error": err})
    if order["totalAttendees"] < 1:
        return respond(400, {"error": "Add at least one attendee", "fields": {"counts": "Add at least one attendee"}})

    event_type = str(body.get("eventType", (existing or {}).get("eventType", ""))).strip()
    requested_slots = sum(
        onam_pricing._int(counts.get(key, 0)) or 0
        for key in ("adults", "kids914", "kids68")
    )
    capacity_pools = (
        ("sadhyaCapacity", ("onam", "combo"), "Onam Sadhya"),
        ("celebrityCapacity", ("event", "combo"), "Celebrity Event"),
    )
    for setting_key, options, label in capacity_pools:
        if order["option"] not in options:
            continue
        capacity_error = validate_capacity(
            event_type, requested_slots, existing, options, setting_key, label
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
            "paidVia": payment_method or "admin-entry",
            "email": email,
        }

    membership_summary = {
        "wasMember": already_member,
        "action": resolved_action,
        "year": RENEWAL_MEMBERSHIP_YEAR if do_join_or_renew else None,
        "validUntil": summary["latestTo"] or None,
        "fee": order["npoFee"],
    }

    # Amount recorded as received, and the resulting balance vs. the (possibly
    # re-priced) order total. Editing a paid registration that changes the total
    # surfaces here as a non-zero balance the admin can collect or refund.
    paid_amount = resolve_paid_amount(
        payment_status, prior_payment_status,
        (existing or {}).get("paidAmount"), order["totalAmount"],
    )

    record = {
        "registrationId": existing.get("registrationId") if existing else str(uuid.uuid4()),
        "eventType": event_type,
        "eventDate": str(body.get("eventDate", (existing or {}).get("eventDate", ""))).strip(),
        "email": email,
        "name": name,
        "phone": phone,
        "registrationType": "onam",
        "registrationOption": order["option"],
        "registrationOptionLabel": order["optionLabel"],
        "pricingTier": order["pricingTier"],
        "npoName": str(body.get("npoName", "")).strip(),
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
        "paymentStatus": payment_status,
        "paidAmount": paid_amount,
        "balanceDue": balance_due(order["totalAmount"], paid_amount),
        "submittedAt": existing.get("submittedAt", timestamp) if existing else timestamp,
        "updatedAt": timestamp,
        "updatedByAdmin": True,
    }

    _carry_over_slot_fields(record, existing)

    try:
        dynamodb.Table(REGISTRATIONS_TABLE).put_item(Item=record)
    except ClientError as exc:
        print(f"Onam registration write error: {exc}")
        return respond(500, {"error": "Failed to save registration"})

    if do_join_or_renew and membership_txn:
        record_membership_transaction(memberid or email, membership_txn)

    _send_registration_emails(record, existing, prior_payment_status)

    return respond(200, {"registration": record, "created": existing is None})


def save_standard_registration(body: dict, existing: Optional[dict]):
    first_name = str(body.get("firstName", "")).strip()
    last_name = str(body.get("lastName", "")).strip()
    name = str(body.get("name", "")).strip() or f"{first_name} {last_name}".strip()
    email = str(body.get("email", "")).strip().lower()
    phone = str(body.get("phone", "")).strip() or str(body.get("whatsapp", "")).strip()

    raw_counts = body.get("attendeeCounts") or {}
    counts = {}
    for key in ("adult", "teen", "child", "toddler"):
        parsed = parse_non_negative_int(raw_counts.get(key, 0))
        if parsed is None:
            return respond(400, {"error": "Attendee counts must be whole numbers 0 or greater",
                                 "fields": {"attendeeCounts": "Attendee counts must be whole numbers 0 or greater"}})
        counts[key] = parsed

    vegetarian_count = parse_non_negative_int(body.get("vegetarianCount", 0))

    if not name:
        return respond(400, {"error": "Name is required", "fields": {"name": "Name is required"}})
    if not email or not validate_email(email):
        return respond(400, {"error": "Valid email is required", "fields": {"email": "Valid email is required"}})
    if phone and not validate_phone(phone):
        return respond(400, {"error": "Phone must include country code (e.g. +81 90-1234-5678)", "fields": {"phone": "Use format: +country code and number"}})
    if vegetarian_count is None:
        return respond(400, {"error": "Vegetarian count must be a whole number 0 or greater",
                             "fields": {"vegetarianCount": "Vegetarian count must be a whole number 0 or greater"}})

    total_attendees = sum(counts.values())
    if total_attendees > 0 and vegetarian_count > total_attendees:
        return respond(400, {"error": "Vegetarian count cannot exceed total attendees",
                             "fields": {"vegetarianCount": "Vegetarian count cannot exceed total attendees"}})

    event_type = str(body.get("eventType", (existing or {}).get("eventType", ""))).strip()
    capacity_error = validate_capacity(event_type, total_attendees, existing)
    if capacity_error:
        return capacity_error

    prior_payment_status = resolve_payment_status((existing or {}).get("paymentStatus"))
    payment_status = (
        resolve_payment_status(body.get("paymentStatus"), fallback=prior_payment_status)
        if "paymentStatus" in body
        else prior_payment_status
    )
    # Standard events have no server-computed total; snapshot against any stored
    # total (usually absent) so the field stays consistent across record types.
    existing_total = (existing or {}).get("totalAmount", 0)
    paid_amount = resolve_paid_amount(
        payment_status, prior_payment_status,
        (existing or {}).get("paidAmount"), existing_total,
    )

    timestamp = now_iso()
    record = {
        "registrationId": existing.get("registrationId") if existing else str(uuid.uuid4()),
        "eventType": event_type,
        "eventDate": str(body.get("eventDate", (existing or {}).get("eventDate", ""))).strip(),
        "email": email,
        "name": name,
        "firstName": first_name,
        "lastName": last_name,
        "phone": phone,
        "whatsapp": str(body.get("whatsapp", "")).strip() or phone,
        "attendeeCounts": counts,
        "vegetarianCount": vegetarian_count,
        "registrationType": str(body.get("registrationType", "standard")),
        "paymentStatus": payment_status,
        "paidAmount": paid_amount,
        "balanceDue": balance_due(existing_total, paid_amount),
        "submittedAt": existing.get("submittedAt", timestamp) if existing else timestamp,
        "updatedAt": timestamp,
        "updatedByAdmin": True,
        "additionalData": body.get("additionalData", {}),
    }

    _carry_over_slot_fields(record, existing)

    try:
        dynamodb.Table(REGISTRATIONS_TABLE).put_item(Item=record)
    except ClientError as exc:
        print(f"Standard registration write error: {exc}")
        return respond(500, {"error": "Failed to save registration"})

    _send_registration_emails(record, existing, prior_payment_status)

    return respond(200, {"registration": record, "created": existing is None})


def _send_registration_emails(
    record: dict,
    existing: Optional[dict],
    prior_payment_status: str,
    notification_type: str = "update",
):
    """After a successful admin save, notify the registrant + admins.

    Ordinary saves send the confirmation/update email and an admin notification.
    A payment transition sends only the dedicated payment confirmation, while a
    slot reassignment sends only the refreshed registration confirmation. Email
    failures are logged inside the emailer and never block the save response.
    """
    event_config = emailer.get_event_config(record.get("eventType", "")) or {}
    is_update = existing is not None
    became_paid = (
        record.get("paymentStatus") == "paid" and prior_payment_status != "paid"
    )
    try:
        if notification_type == "payment" or became_paid:
            emailer.send_payment_received_email(
                record.get("email", ""), record.get("name", ""), event_config,
                registration_details=record,
            )
            return

        emailer.send_confirmation_email(
            record.get("email", ""), record.get("name", ""), event_config,
            is_update=is_update, registration_details=record,
            subject_prefix="Entry Slot Updated" if notification_type == "slot" else None,
        )
        if notification_type == "update":
            emailer.send_admin_notification(record, event_config, is_update=is_update)
    except Exception:  # pragma: no cover - emails must never break the save
        pass


def _seat_count_change_blocked(body: dict, existing: Optional[dict]):
    """Refuse an edit that resizes a party after somebody has been admitted.

    The entry allocation on file was sized for the old head count and has
    already been partly consumed, and the slot can no longer be reassigned to
    match — so a changed seat count would leave the two permanently out of
    step. Only the seated count is frozen: names, contact details, payment and
    under-6s (who occupy no seat) stay editable.

    Returns an error response, or None when the edit may proceed.
    """
    if not existing or not entry_allocations.checked_in_seats(existing):
        return None

    counts = body.get("counts") or body.get("attendeeCounts")
    if counts is None:  # this edit doesn't touch the party
        return None

    requested = {
        "registrationType": existing.get("registrationType", ""),
        "attendeeCounts": counts,
    }
    try:
        if _required_seats_for_registration(requested) == _required_seats_for_registration(existing):
            return None
    except (TypeError, ValueError) as exc:
        return respond(400, {"error": f"Invalid attendee counts: {exc}"})

    return respond(
        409,
        {
            "error": "This registration has already been checked in, so the number of "
                     "seated attendees can no longer be changed.",
            "checkedIn": True,
            "fields": {"counts": "Locked after check-in"},
        },
    )


def save_registration(body: dict):
    registration_id = str(body.get("registrationId", "")).strip()
    existing = None
    if registration_id:
        try:
            existing = dynamodb.Table(REGISTRATIONS_TABLE).get_item(
                Key={"registrationId": registration_id}
            ).get("Item")
        except ClientError as exc:
            print(f"Registration lookup error: {exc}")
        if not existing:
            return respond(404, {"error": "Registration not found"})

    seat_lock = _seat_count_change_blocked(body, existing)
    if seat_lock:
        return seat_lock

    # Onam if an option is supplied, or the record being edited is an Onam one.
    is_onam = bool(body.get("registrationOption")) or (existing or {}).get("registrationType") == "onam"
    if is_onam:
        return save_onam_registration(body, existing)
    return save_standard_registration(body, existing)


def _allocation_admitted_count(allocation: dict) -> int:
    """How many people this single pass has admitted.

    Counters written before the serialization fix are maps rather than numbers;
    they read as zero here, matching entry_allocations.checked_in_seats.
    """
    try:
        return int(allocation.get("checkedInCount", 0) or 0)
    except (TypeError, ValueError):
        return 0


def delete_registration(registration_id: str, force: bool = False):
    if not registration_id:
        return respond(400, {"error": "Missing registrationId"})

    # Read before deleting: a checked-in registration stays. Its pass has been
    # honoured and its seats consumed, so deleting the record would release that
    # capacity back to the slot and let it be sold twice.
    try:
        existing = dynamodb.Table(REGISTRATIONS_TABLE).get_item(
            Key={"registrationId": registration_id}
        ).get("Item")
    except ClientError as exc:
        print(f"Registration lookup error for {registration_id}: {exc}")
        return respond(500, {"error": "Failed to load registration"})

    if not existing:
        return respond(404, {"error": "Registration not found"})

    admitted = entry_allocations.checked_in_seats(existing)
    if admitted and not force:
        # `canForce` tells the caller an override exists, so the UI can offer it
        # rather than presenting this as a dead end.
        return respond(
            409,
            {
                "error": "This registration has already been checked in and can no "
                         "longer be deleted.",
                "checkedIn": True,
                "checkedInSeats": admitted,
                "canForce": True,
            },
        )
    if admitted:
        # Worth a line in the log: an admin overrode the check-in lock, and the
        # seats below stay reserved, so the slot's numbers will not add up to the
        # registrations that remain.
        print(
            f"Force-deleting checked-in registration {registration_id}: "
            f"{admitted} seat(s) already admitted"
        )

    try:
        response = dynamodb.Table(REGISTRATIONS_TABLE).delete_item(
            Key={"registrationId": registration_id},
            ReturnValues="ALL_OLD",
        )
    except ClientError as exc:
        print(f"Registration delete error: {exc}")
        return respond(500, {"error": "Failed to delete registration"})

    if "Attributes" not in response:
        return respond(404, {"error": "Registration not found"})

    # Release every reservation behind this registration's entry passes, not
    # just the legacy single-slot field (a registration may hold several).
    deleted_record = response.get("Attributes", {})
    event_type = deleted_record.get("eventType", "")
    for allocation in deleted_record.get("allocations", []) or []:
        allocation_id = allocation.get("allocationId", "")
        try:
            full_item = entry_allocations.get_allocation(event_type, allocation_id)
            if not full_item or not full_item.get("reservationId"):
                continue
            # Never hand back a seat somebody has already used. Only a forced
            # delete reaches here with check-ins on file; those seats are spent,
            # so cancelling would advertise capacity the people inside occupy.
            # Leaving the reservation stranded only undercounts what's available,
            # which is the safe direction to be wrong in.
            if _allocation_admitted_count(full_item):
                print(
                    f"Keeping reservation for allocation {allocation_id}: "
                    "its seats have already been admitted"
                )
                continue
            entry_slots_logic.cancel_reservation(
                event_type=event_type, reservation_id=full_item["reservationId"]
            )
        except Exception as exc:
            print(f"Warning: Failed to release allocation {allocation_id}: {exc}")

    # Legacy single-slot field, for registrations predating the allocation model.
    if not deleted_record.get("allocations") and deleted_record.get("slotReservationId"):
        try:
            reservation_id = deleted_record.get("slotReservationId", "")
            if event_type and reservation_id:
                entry_slots_logic.cancel_reservation(
                    event_type=event_type,
                    reservation_id=reservation_id,
                )
        except Exception as exc:
            # Log the error but don't fail the deletion
            print(f"Warning: Failed to cancel slot reservation {deleted_record.get('slotReservationId')}: {exc}")

    # Release Celebrity Event seats too, unless the party has already been
    # admitted (same reasoning as above: a physically occupied seat must not
    # be handed back for resale).
    if not admitted:
        try:
            for purchase in seat_purchase_logic.list_purchases_for_registration(registration_id):
                if purchase.get("status") in seat_purchase_logic.ACTIVE_STATUSES:
                    seat_purchase_logic.update_purchase_status(purchase["purchaseId"], "cancelled")
            held_seats = [
                item["seatId"]
                for item in seat_selection_logic.get_seats_for_registration(event_type, registration_id)
            ]
            if held_seats:
                seat_selection_logic.release_seats(event_type, held_seats)
        except Exception as exc:
            print(f"Warning: Failed to release seats for registration {registration_id}: {exc}")

    return respond(
        200,
        {
            "deleted": True,
            "registrationId": registration_id,
            "forced": bool(admitted),
            "checkedInSeats": admitted,
        },
    )


def _release_existing_allocations(event_type: str, registration: dict) -> None:
    """Cancel every reservation/pass currently on file for a registration."""
    for allocation in registration.get("allocations") or []:
        full_item = entry_allocations.get_allocation(event_type, allocation.get("allocationId", ""))
        if not full_item:
            continue
        if full_item.get("reservationId"):
            try:
                entry_slots_logic.cancel_reservation(full_item["reservationId"], event_type)
            except Exception as exc:
                print(f"Warning: Could not release reservation for allocation {allocation.get('allocationId')}: {exc}")
        entry_allocations.cancel_allocation(full_item)

    # Legacy registrations predating the allocation model.
    if not registration.get("allocations") and registration.get("slotReservationId"):
        try:
            entry_slots_logic.cancel_reservation(registration["slotReservationId"], event_type)
        except Exception as exc:
            print(f"Warning: Could not release legacy reservation {registration.get('slotReservationId')}: {exc}")


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
                print("Warning: Could not release reservation during allocation rollback")
            entry_allocations.cancel_allocation(item)
        if isinstance(exc, ClientError):
            raise ValueError(f"Entry allocation failed: {exc}") from exc
        raise


def check_in_entry_pass(body: dict, event: dict):
    """Resolve or atomically consume the complete registration pass.

    Normally driven by scanning the attendee's QR token, but an admin can also
    look up and check in a registration directly by ID — e.g. the attendee
    lost their pass or it never arrived — by sending `registrationId` instead
    of `token`.
    """
    token = str(body.get("token", "")).strip()
    registration_id = str(body.get("registrationId", "")).strip()
    action = str(body.get("action", "resolve")).strip().lower()
    if not token and not registration_id:
        return respond(400, {"error": "Pass token or registration ID is required"})
    if action not in {"resolve", "check_in"}:
        return respond(400, {"error": "Unsupported check-in action"})

    claims = ((event.get("requestContext") or {}).get("authorizer") or {}).get("claims") or {}
    checked_in_by = str(claims.get("email") or claims.get("username") or "admin").strip()

    registration = (
        entry_allocations.get_registration_by_pass_token(token)
        if token
        else entry_allocations.get_registration_by_id(registration_id)
    )
    if not registration:
        return respond(404, {"error": "Invalid entry pass" if token else "Registration not found"})

    allocations = registration.get("allocations", [])
    if action == "check_in":
        try:
            summary = entry_allocations.check_in_registration(registration, checked_in_by)
        except ValueError as exc:
            message = str(exc)
            status = 404 if message in {"Invalid entry pass", "Registration not found"} else 409
            return respond(status, {"error": message})
        except RuntimeError as exc:
            return respond(500, {"error": str(exc)})
        # The registration was read before the check-in, so refresh the copy we
        # echo back — otherwise it still reports zero people checked in.
        allocations = summary.get("allocations", [])
        registration["allocations"] = allocations
    else:
        summary = {
            "allocations": allocations,
            "totalAllocated": sum(int(item.get("seatCount", 0) or 0) for item in allocations),
            "totalCheckedIn": sum(int(item.get("checkedInCount", 0) or 0) for item in allocations),
            "status": entry_allocations.summarize_status(allocations),
        }

    # The gate needs to know why it may not admit this party, not just that the
    # button is disabled: an unpaid registration cannot be checked in.
    summary["paymentStatus"] = resolve_payment_status(registration.get("paymentStatus"))
    summary["paymentComplete"] = entry_allocations.payment_is_complete(registration)

    # Same consolidated view as the check-in ticket PDF, so the door staff
    # scanning the QR see the registrant's zone upgrade / celebrity dinner /
    # seat selection add-ons too, not just their entry slots.
    addons = qr_email.gather_addons(registration.get("registrationId", ""))

    return respond(
        200,
        {"allocations": allocations, "summary": summary, "registration": registration, "addons": addons},
    )


def reassign_registration(body: dict):
    """
    Reassign a registration's entry-slot allocation(s).
    POST body: {
        "registrationId": "...",
        "allocations": [{"slotId": "...", "seatCount": N}, ...]
    }
    A legacy `newSlotId` (assigning it all the required seats) is also accepted.
    """
    registration_id = str(body.get("registrationId", "")).strip()
    if not registration_id:
        return respond(400, {"error": "registrationId is required"})

    if body.get("allocations") is None and body.get("newSlotId"):
        body = {**body, "slotId": str(body["newSlotId"]).strip()}

    # Get the registration
    try:
        response = dynamodb.Table(REGISTRATIONS_TABLE).get_item(
            Key={"registrationId": registration_id}
        )
    except ClientError as exc:
        print(f"Registration lookup error for {registration_id}: {exc}")
        return respond(500, {"error": "Failed to load registration"})

    if "Item" not in response:
        return respond(404, {"error": "Registration not found"})

    registration = response["Item"]
    event_type = registration.get("eventType", "")

    if not event_type:
        return respond(400, {"error": "Registration has no eventType"})

    # Calculate required seats for this registration
    try:
        required_seats = _required_seats_for_registration(registration)
    except (TypeError, ValueError) as exc:
        return respond(400, {"error": f"Invalid attendee counts: {exc}"})

    try:
        requested = entry_allocations.normalize_requested_allocations(body, required_seats)
    except ValueError as exc:
        return respond(400, {"error": str(exc)})

    existing_signature = sorted(
        (str(a.get("entrySlotId", "")), int(a.get("seatCount", 0) or 0))
        for a in (registration.get("allocations") or [])
    )
    requested_signature = sorted((a["slotId"], a["seatCount"]) for a in requested)
    if requested_signature == existing_signature:
        return respond(200, {"message": "No change", "registration": registration})

    # A real change from here on, which releases the current passes and their
    # seats. Once anyone has been admitted those seats have been used, so the
    # slot is frozen even for an admin: releasing it would hand the capacity
    # back while the party is inside, and invalidate an honoured pass.
    if entry_allocations.checked_in_seats(registration):
        return respond(
            409,
            {
                "error": "This registration has already been checked in, so its entry "
                         "slot can no longer be reassigned.",
                "checkedIn": True,
            },
        )

    _release_existing_allocations(event_type, registration)

    try:
        created_items = _reserve_new_allocations(event_type, registration_id, requested)
    except ValueError as exc:
        return respond(400, {"error": str(exc)})
    except RuntimeError as exc:
        return respond(400, {"error": str(exc)})

    allocations = [entry_allocations.public_allocation(item) for item in created_items]
    registration["allocations"] = allocations
    if len(allocations) == 1:
        registration["slotId"] = allocations[0]["entrySlotId"]
        registration["slotReservationId"] = created_items[0].get("reservationId", "")
        registration["slotName"] = allocations[0]["slotName"]
        registration["slotSeats"] = allocations[0]["seatCount"]
    else:
        for field in ("slotId", "slotReservationId", "slotName", "slotSeats"):
            registration.pop(field, None)
    registration["updatedAt"] = now_iso()
    registration["updatedByAdmin"] = True

    try:
        dynamodb.Table(REGISTRATIONS_TABLE).put_item(Item=registration)
    except ClientError as exc:
        print(f"Failed to persist reassignment for {registration_id}: {exc}")
        return respond(500, {"error": "Failed to save the new entry slot selection"})

    # Slots changed — send one refreshed confirmation with the new pass(es).
    _send_registration_emails(
        registration,
        registration,
        registration.get("paymentStatus"),
        notification_type="slot",
    )

    return respond(200, {"message": "Registration reassigned", "registration": registration})


def _find_mergeable_seat_purchase(registration_id: str, event_type: str):
    """The registration's own previous free admin allocation for this event,
    if any, that a new one can be edited in place instead of fragmenting
    into yet another separate purchase/ticket (see
    allocate_registration_seats). Never a purchase that involved real
    payment — editing it would re-price the whole thing at ¥0 and wipe out
    what was actually charged — and never one already checked in, since
    those seats are already used. list_purchases_for_registration sorts
    newest-first, so this picks the most recent eligible one."""
    for purchase in seat_purchase_logic.list_purchases_for_registration(registration_id):
        if (
            purchase.get("eventType") == event_type
            and purchase.get("status") == "paid"
            and not purchase.get("pricingEnabled", True)
            and not purchase.get("checkedIn")
        ):
            return purchase
    return None


def _admin_override_max_seats(event_type: str) -> bool:
    event_config = emailer.get_event_config(event_type)
    if not event_config:
        return False
    seat_config = seat_selection_logic.parse_seat_selection_config(emailer.normalize_event_info(event_config))
    return seat_config.get("adminOverrideMaxSeats", False)


def get_registration_seat_allocation(registration_id: str):
    """
    GET admin/registrations/allocate-seats?registrationId=... — what the
    "Allocate Seats" modal needs before it opens: this registration's
    currently-editable seats (its own mergeable free allocation, if any —
    see _find_mergeable_seat_purchase), any OTHER seats it holds that
    aren't editable here (already checked in, or genuinely paid for —
    shown to the admin as already booked, same as anyone else's seat), the
    per-registration seat allowance, and whether this event's
    adminOverrideMaxSeats setting lifts that cap.
    """
    registration_id = str(registration_id or "").strip()
    if not registration_id:
        return respond(400, {"error": "registrationId is required"})

    try:
        response = dynamodb.Table(REGISTRATIONS_TABLE).get_item(
            Key={"registrationId": registration_id}
        )
    except ClientError as exc:
        print(f"Registration lookup error for {registration_id}: {exc}")
        return respond(500, {"error": "Failed to load registration"})

    if "Item" not in response:
        return respond(404, {"error": "Registration not found"})
    registration = response["Item"]

    option = str(registration.get("registrationOption", "")).strip().lower()
    if option not in ("event", "combo"):
        return respond(
            400,
            {"error": "Seat allocation is only available for Celebrity Event and Combo registrations."},
        )

    event_type = registration.get("eventType", "")
    if not event_type:
        return respond(400, {"error": "Registration has no eventType"})

    editable_seat_ids = []
    locked_seat_ids = []
    mergeable = _find_mergeable_seat_purchase(registration_id, event_type)
    for purchase in seat_purchase_logic.list_purchases_for_registration(registration_id):
        if purchase.get("eventType") != event_type or purchase.get("status") not in seat_purchase_logic.ACTIVE_STATUSES:
            continue
        seat_ids = purchase.get("seatIds") or []
        if mergeable and purchase.get("purchaseId") == mergeable.get("purchaseId"):
            editable_seat_ids.extend(seat_ids)
        else:
            locked_seat_ids.extend(seat_ids)

    return respond(200, {
        "eventType": event_type,
        "editableSeatIds": editable_seat_ids,
        "lockedSeatIds": locked_seat_ids,
        "seatsAllowed": _celebrity_seats_allowed(registration),
        "adminOverrideMaxSeats": _admin_override_max_seats(event_type),
    })


def allocate_registration_seats(body: dict):
    """
    Admin: directly assign specific Celebrity Event hall seats to a
    registration, bypassing the registrant's own self-service booking
    window/payment-method checks and the zone-upgrade entitlement gate —
    an admin can put a party anywhere (comp seats, a walk-in, fixing a
    registration that couldn't complete self-service). `seatIds` is the
    FULL desired set of editable seats for this registration, not just
    seats to add — the admin dashboard pre-populates its picker with
    whatever this registration already holds (get_registration_seat_
    allocation's editableSeatIds) and lets the admin freely re-select, so
    a plain deselect-then-save removes a seat.

    If this registration already holds an open, ¥0, not-yet-checked-in
    allocation for the same event, that purchase's own seatIds are
    updated in place (seat_purchase_logic.set_purchase_seats) instead of
    creating a separate one — so repeated allocations edit one ticket/QR
    that always shows the full current set, rather than each allocation
    producing its own ticket that only shows its own slice (previously
    allocated seats "disappearing" from the latest email was this exact
    fragmentation, not seats actually being lost). Otherwise a new ¥0
    purchase is created (seat_purchase_logic.create_purchase_request(
    free=True)) — the same path a Regular-only or pricing-disabled
    selection already uses. Either way the registrant gets the normal
    "paid" confirmation email/QR/check-in PDF ticket, just without ever
    being asked to pay. This is the admin-driven counterpart
    SEAT_SELECTION.md's "Known Limitations" flagged as missing (there was
    no way to assign a registration's seats on the admin's behalf, only
    self-service cancel-and-rebuy).

    Caps the total at the per-registration seat allowance (one seat per
    eligible attendee — adults + kids 9-14 + kids 6-8) unless this event's
    eventInfo.seatSelection.adminOverrideMaxSeats is on, in which case an
    admin may allocate any number of seats.

    POST body: {"registrationId": "...", "seatIds": ["R04C12", ...]}
    """
    registration_id = str(body.get("registrationId", "")).strip()
    if not registration_id:
        return respond(400, {"error": "registrationId is required"})

    try:
        response = dynamodb.Table(REGISTRATIONS_TABLE).get_item(
            Key={"registrationId": registration_id}
        )
    except ClientError as exc:
        print(f"Registration lookup error for {registration_id}: {exc}")
        return respond(500, {"error": "Failed to load registration"})

    if "Item" not in response:
        return respond(404, {"error": "Registration not found"})
    registration = response["Item"]

    option = str(registration.get("registrationOption", "")).strip().lower()
    if option not in ("event", "combo"):
        return respond(
            400,
            {"error": "Seat allocation is only available for Celebrity Event and Combo registrations."},
        )

    event_type = registration.get("eventType", "")
    if not event_type:
        return respond(400, {"error": "Registration has no eventType"})

    seat_ids = body.get("seatIds")
    if not isinstance(seat_ids, list) or not seat_ids:
        return respond(400, {"error": "At least one seat must be selected"})
    seat_ids = [str(seat_id).strip().upper() for seat_id in seat_ids]
    if len(set(seat_ids)) != len(seat_ids):
        return respond(400, {"error": "Duplicate seats selected"})

    if not _admin_override_max_seats(event_type):
        seats_allowed = _celebrity_seats_allowed(registration)
        if len(seat_ids) > seats_allowed:
            return respond(
                400,
                {
                    "error": (
                        f"This registration is allowed up to {seats_allowed} seat(s) "
                        "(one per adult/kid 9-14/kid 6-8) — select fewer seats, or turn on "
                        "\"Allow admins to exceed the per-registration seat limit\" for this "
                        "event in Seat Selection Configuration."
                    ),
                    "seatsAllowed": seats_allowed,
                },
            )

    mergeable = _find_mergeable_seat_purchase(registration_id, event_type)

    try:
        if mergeable:
            purchase = seat_purchase_logic.set_purchase_seats(
                mergeable["purchaseId"], seat_ids, holder_name=registration.get("name", "")
            )
        else:
            purchase = seat_purchase_logic.create_purchase_request(
                event_type=event_type,
                registration_id=registration_id,
                email=registration.get("email", ""),
                name=registration.get("name", ""),
                seat_ids=seat_ids,
                pricing_mode="person",
                free=True,
            )
    except ValueError as exc:
        # Bad input, or a seat someone else already holds — the admin can
        # pick different seats, same recovery as the self-service flow.
        return respond(400, {"error": str(exc)})
    except RuntimeError as exc:
        print(f"Admin seat allocation failed for {registration_id}: {exc}")
        return respond(500, {"error": str(exc)})

    seat_purchase_emailer.send_paid_email(purchase)
    seat_purchase_emailer.send_admin_notification(
        purchase,
        "allocated by admin",
        extra_note="Seats were assigned directly by an admin from the Registrations page — no payment was collected.",
    )
    return respond(201, {"message": "Seats allocated", "purchase": purchase})


def process_bulk_registrations(body: dict):
    """Apply one payment/email/reminder action to up to 100 selected registrations."""
    action = str(body.get("action", "")).strip()
    registration_ids = body.get("registrationIds")
    if action not in {
        "mark_paid", "send_payment_pending", "send_payment_confirmation", "send_sadhya_slot_reminder",
        "send_seat_selection_announcement", "send_seat_selection_reminder",
        "send_celebrity_dinner_announcement",
    }:
        return respond(400, {"error": "Unsupported bulk action"})
    if not isinstance(registration_ids, list) or not registration_ids:
        return respond(400, {"error": "Select at least one registration"})
    registration_ids = list(dict.fromkeys(
        str(registration_id).strip() for registration_id in registration_ids if str(registration_id).strip()
    ))
    if len(registration_ids) > 100:
        return respond(400, {"error": "Select no more than 100 registrations at a time"})

    updated = []
    failed = []
    table = dynamodb.Table(REGISTRATIONS_TABLE)
    # One _seat_announcement_context/_dinner_announcement_context lookup per
    # distinct eventType among the selected registrations, not per
    # registration — selections are usually all from the same event.
    seat_announcement_cache: dict = {}
    dinner_announcement_cache: dict = {}
    for registration_id in registration_ids:
        try:
            record = table.get_item(Key={"registrationId": registration_id}).get("Item")
        except ClientError as exc:
            print(f"Bulk registration lookup error for {registration_id}: {exc}")
            failed.append({"registrationId": registration_id, "error": "Could not load registration"})
            continue
        if not record:
            failed.append({"registrationId": registration_id, "error": "Registration not found"})
            continue

        payment_status = resolve_payment_status(record.get("paymentStatus"))
        if action == "send_payment_pending" and payment_status != "pending":
            failed.append({"registrationId": registration_id, "error": "Payment is not pending"})
            continue
        if action == "send_payment_confirmation" and payment_status != "paid":
            failed.append({"registrationId": registration_id, "error": "Payment is not marked paid"})
            continue
        if action == "send_sadhya_slot_reminder":
            # Sadhya entry slots only apply to the Sadhya-inclusive options
            # (Onam-only and the combo) — the Celebrity-Event-only option has
            # no meal seating to select. Reminders are for paid registrants
            # who haven't picked a slot yet.
            if str(record.get("registrationOption", "")).strip().lower() not in {"onam", "combo"}:
                failed.append({"registrationId": registration_id, "error": "Sadhya entry slots only apply to Onam and Combo registrations"})
                continue
            if payment_status != "paid":
                failed.append({"registrationId": registration_id, "error": "Payment is not marked paid"})
                continue
            if record.get("allocations") or record.get("slotId"):
                failed.append({"registrationId": registration_id, "error": "A Sadhya slot is already selected"})
                continue

        seat_announcement_context = None
        if action in {"send_seat_selection_announcement", "send_seat_selection_reminder"}:
            # Celebrity Event / Combo only — Onam-only registrations have no
            # seats to select. Same eligibility gate as the "announce to
            # everyone" broadcast (send_seat_selection_announcement above).
            if str(record.get("registrationOption", "")).strip().lower() not in {"event", "combo"}:
                failed.append({"registrationId": registration_id, "error": "Seat selection only applies to Celebrity Event and Combo registrations"})
                continue
            if payment_status != "paid":
                failed.append({"registrationId": registration_id, "error": "Payment is not marked paid"})
                continue
            reg_event_type = record.get("eventType", "")
            if reg_event_type not in seat_announcement_cache:
                seat_announcement_cache[reg_event_type] = _seat_announcement_context(reg_event_type)
            seat_announcement_context, seat_announcement_error = seat_announcement_cache[reg_event_type]
            if seat_announcement_error:
                failed.append({"registrationId": registration_id, "error": seat_announcement_error})
                continue
            if action == "send_seat_selection_reminder":
                if seat_announcement_context[2].get("status") == "ended":
                    failed.append({"registrationId": registration_id, "error": "The seat selection window has closed"})
                    continue
                if _registration_has_seats(registration_id):
                    failed.append({"registrationId": registration_id, "error": "Seats are already selected"})
                    continue

        dinner_announcement_context = None
        if action == "send_celebrity_dinner_announcement":
            # Celebrity Dinner sign-up isn't gated on registrationOption or
            # paymentStatus at all (see celebrity_dinner_logic's own
            # eligibility rule) — only on whether this registration has any
            # attendees left to bring as dinner guests.
            if celebrity_dinner_logic.eligible_guest_count(record) <= 0:
                failed.append({"registrationId": registration_id, "error": "This registration has no eligible Celebrity Dinner guests"})
                continue
            if celebrity_dinner_logic.get_active_dinner_registration_for_registration(registration_id):
                failed.append({"registrationId": registration_id, "error": "Already registered for Celebrity Dinner"})
                continue
            reg_event_type = record.get("eventType", "")
            if reg_event_type not in dinner_announcement_cache:
                dinner_announcement_cache[reg_event_type] = _dinner_announcement_context(reg_event_type)
            dinner_announcement_context, dinner_announcement_error = dinner_announcement_cache[reg_event_type]
            if dinner_announcement_error:
                failed.append({"registrationId": registration_id, "error": dinner_announcement_error})
                continue

        if action == "mark_paid" and payment_status != "paid":
            paid_amount = resolve_paid_amount(
                "paid", payment_status, record.get("paidAmount"), record.get("totalAmount", 0)
            )
            try:
                response = table.update_item(
                    Key={"registrationId": registration_id},
                    UpdateExpression=(
                        "SET paymentStatus = :status, paidAmount = :paid_amount, "
                        "balanceDue = :balance_due, updatedAt = :updated_at"
                    ),
                    ExpressionAttributeValues={
                        ":status": "paid",
                        ":paid_amount": paid_amount,
                        ":balance_due": balance_due(record.get("totalAmount", 0), paid_amount),
                        ":updated_at": now_iso(),
                    },
                    ReturnValues="ALL_NEW",
                )
                record = response["Attributes"]
            except ClientError as exc:
                print(f"Bulk payment update error for {registration_id}: {exc}")
                failed.append({"registrationId": registration_id, "error": "Could not mark payment paid"})
                continue

        if action == "send_seat_selection_announcement":
            event_name, zone_pricing, window = seat_announcement_context
            try:
                email_sent = seat_purchase_emailer.send_announcement_email(record, event_name, zone_pricing, window)
            except Exception as exc:
                print(f"Seat selection announcement send error for {registration_id}: {exc}")
                email_sent = False
            if not email_sent:
                failed.append({"registrationId": registration_id, "error": "Email could not be sent"})
                continue
            updated.append(record)
            continue

        if action == "send_seat_selection_reminder":
            event_name, _zone_pricing, window = seat_announcement_context
            try:
                email_sent = seat_purchase_emailer.send_reminder_email(record, event_name, window)
            except Exception as exc:
                print(f"Seat selection reminder send error for {registration_id}: {exc}")
                email_sent = False
            if not email_sent:
                failed.append({"registrationId": registration_id, "error": "Email could not be sent"})
                continue
            updated.append(record)
            continue

        if action == "send_celebrity_dinner_announcement":
            event_name, dinner_config = dinner_announcement_context
            try:
                email_sent = celebrity_dinner_emailer.send_announcement_email(record, event_name, dinner_config)
            except Exception as exc:
                print(f"Celebrity Dinner announcement send error for {registration_id}: {exc}")
                email_sent = False
            if not email_sent:
                failed.append({"registrationId": registration_id, "error": "Email could not be sent"})
                continue
            updated.append(record)
            continue

        event_config = emailer.get_event_config(record.get("eventType", "")) or {}
        if action == "send_payment_pending":
            email_sent = emailer.send_payment_pending_email(
                record.get("email", ""), record.get("name", ""), event_config,
                registration_details=record,
            )
        elif action == "send_sadhya_slot_reminder":
            email_sent = emailer.send_sadhya_slot_reminder_email(
                record.get("email", ""), record.get("name", ""), event_config,
                registration_details=record,
            )
        else:
            email_sent = emailer.send_payment_received_email(
                record.get("email", ""), record.get("name", ""), event_config,
                registration_details=record,
            )
        if not email_sent:
            failed.append({"registrationId": registration_id, "error": "Email could not be sent"})
            continue
        updated.append(record)

    return respond(200, {"registrations": updated, "failed": failed})


def _registration_has_seats(registration_id: str) -> bool:
    """True if the registration holds any seats through a still-active
    purchase (pending/paid/refund_requested, including admin-assigned ones).
    Cancelled purchases don't count, so someone who cancelled is treated as
    not having selected seats. If the purchases can't be read, assume they
    do — better to skip one reminder than nag someone who already chose."""
    try:
        purchases = seat_purchase_logic.list_purchases_for_registration(registration_id)
    except Exception as exc:
        print(f"Seat purchase lookup error for {registration_id}: {exc}")
        return True
    return any(
        p.get("status") in seat_purchase_logic.ACTIVE_STATUSES and (p.get("seatIds") or [])
        for p in purchases
    )


def _seat_announcement_context(event_type: str):
    """(event_name, zone_pricing, window), None on success, or (None,
    error_message) if seat selection isn't ready to announce for this
    event yet (not enabled). Shared by "announce to every eligible
    registrant" (send_seat_selection_announcement) and "announce to
    selected registrations" (the send_seat_selection_announcement bulk
    action in process_bulk_registrations), so the two entry points can
    never drift on what counts as ready-to-announce.

    zone_pricing may come back {} — an event can announce that seat
    selection is open before any zone has a price configured (e.g. an
    always-free event, or pricing not decided yet); send_announcement_
    email renders a plain "seats are open, here's the link" message
    without price cards in that case, rather than refusing to send.
    """
    event_config = emailer.get_event_config(event_type)
    if not event_config:
        return None, f"Event '{event_type}' not found"

    seat_config = seat_selection_logic.parse_seat_selection_config(emailer.normalize_event_info(event_config))
    if not seat_config["enabled"]:
        return None, "Seat selection is not enabled for this event yet"

    zone_pricing = seat_selection_logic.get_zone_pricing(event_type)
    window = {**seat_config, "status": seat_selection_logic.seat_selection_window_status(seat_config)}
    event_name = event_config.get("eventName") or event_type
    return (event_name, zone_pricing, window), None


def _dinner_announcement_context(event_type: str):
    """(event_name, dinner_config), None on success, or (None, error_
    message) if Celebrity Dinner isn't ready to announce for this event
    yet (not enabled, not configured, or its registration window isn't
    currently open — unlike seat selection, there's no sensible "open but
    nothing configured yet" state to announce here, since the invite
    itself needs a registration window). Shared by the
    send_celebrity_dinner_announcement bulk action with itself across
    registrations of the same event (one lookup per distinct eventType,
    not per registration).
    """
    if not celebrity_dinner_logic.is_event_celebrity_dinner_enabled(event_type):
        return None, "Celebrity Dinner is not enabled for this event"

    config_item = celebrity_dinner_logic.get_config(event_type)
    if not config_item:
        return None, "Celebrity Dinner has not been configured for this event yet"
    config = celebrity_dinner_logic.normalize_config(config_item, event_type, enabled=True)

    status = celebrity_dinner_logic.registration_window_status(config)
    if status != "open":
        return None, (
            "Celebrity Dinner registration hasn't opened yet for this event"
            if status == "not_open"
            else "Celebrity Dinner registration is no longer open for this event"
        )

    event_config = emailer.get_event_config(event_type)
    event_name = (event_config or {}).get("eventName") or event_type
    return (event_name, config), None


def send_seat_selection_announcement(event_type: str, reminder: bool = False):
    """Email every paid event/combo registrant of `event_type` that seat
    selection has opened, with each priced zone's rate (when any zone has
    one configured yet — see _seat_announcement_context) and the booking
    window. Re-sendable on demand (e.g. after fixing a price) — this is an
    admin-triggered broadcast, not a one-time system event, so there's no
    "already sent" tracking to dedupe against. For announcing to only
    some registrants, see process_bulk_registrations' own
    "send_seat_selection_announcement" action instead.

    reminder=True instead emails only the eligible registrants who haven't
    selected any seats yet (see _registration_has_seats), with the reminder
    template, and refuses once the selection window has closed.
    """
    event_type = str(event_type or "").strip()
    if not event_type:
        return respond(400, {"error": "eventType is required"})

    context, error = _seat_announcement_context(event_type)
    if error:
        return respond(404 if "not found" in error else 400, {"error": error})
    event_name, zone_pricing, window = context
    if reminder and window.get("status") == "ended":
        return respond(400, {"error": "The seat selection window has closed"})

    table = dynamodb.Table(REGISTRATIONS_TABLE)
    eligible = []
    scan_kwargs = {
        "FilterExpression": "eventType = :event_type AND registrationOption IN (:opt_event, :opt_combo) AND paymentStatus = :paid",
        "ExpressionAttributeValues": {
            ":event_type": event_type,
            ":opt_event": "event",
            ":opt_combo": "combo",
            ":paid": "paid",
        },
    }
    while True:
        try:
            response = table.scan(**scan_kwargs)
        except ClientError as exc:
            print(f"Seat selection announcement scan error for {event_type}: {exc}")
            return respond(500, {"error": "Failed to load registrations for this event"})
        eligible.extend(response.get("Items", []))
        if "LastEvaluatedKey" not in response:
            break
        scan_kwargs["ExclusiveStartKey"] = response["LastEvaluatedKey"]

    if reminder:
        eligible = [reg for reg in eligible if not _registration_has_seats(reg.get("registrationId", ""))]

    sent = 0
    failed = []
    for reg in eligible:
        if not reg.get("email"):
            continue
        try:
            if reminder:
                ok = seat_purchase_emailer.send_reminder_email(reg, event_name, window)
            else:
                ok = seat_purchase_emailer.send_announcement_email(reg, event_name, zone_pricing, window)
        except Exception as exc:
            print(f"Seat selection announcement send error for {reg.get('registrationId')}: {exc}")
            ok = False
        if ok:
            sent += 1
        else:
            failed.append(reg.get("registrationId") or reg.get("email"))

    return respond(200, {
        "message": (
            f"Reminder sent to {sent} of {len(eligible)} registrant(s) without seats"
            if reminder
            else f"Announcement sent to {sent} of {len(eligible)} eligible registrant(s)"
        ),
        "sent": sent,
        "eligibleCount": len(eligible),
        "failed": failed,
    })


def lambda_handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    http_method = event.get("httpMethod")
    if http_method == "OPTIONS":
        return respond(200, {})

    resource = event.get("resource", "") or ""
    path_params = event.get("pathParameters") or {}

    if "users" in resource:
        if http_method != "GET":
            return respond(405, {"error": "Method not allowed"})
        try:
            members = list_members()
        except Exception as exc:
            print(f"Member listing error: {exc}")
            return respond(500, {"error": "Failed to load members"})
        return respond(200, {"members": members, "count": len(members)})

    if "registrations/bulk" in resource:
        if http_method != "POST":
            return respond(405, {"error": "Method not allowed"})
        return process_bulk_registrations(parse_json_body(event))

    if "registrations/reassign" in resource:
        if http_method != "POST":
            return respond(405, {"error": "Method not allowed"})
        return reassign_registration(parse_json_body(event))

    if "registrations/allocate-seats" in resource:
        if http_method == "GET":
            query = event.get("queryStringParameters") or {}
            return get_registration_seat_allocation(str(query.get("registrationId", "")).strip())
        if http_method != "POST":
            return respond(405, {"error": "Method not allowed"})
        return allocate_registration_seats(parse_json_body(event))

    if "seat-selection-announcement" in resource:
        if http_method != "POST":
            return respond(405, {"error": "Method not allowed"})
        body = parse_json_body(event)
        return send_seat_selection_announcement(body.get("eventType", ""), reminder=body.get("reminder") is True)

    if "check-in" in resource:
        if http_method != "POST":
            return respond(405, {"error": "Method not allowed"})
        return check_in_entry_pass(parse_json_body(event), event)

    if http_method == "POST":
        return save_registration(parse_json_body(event))

    if http_method == "DELETE":
        # ?force=true overrides the check-in lock, for the mistaken-entry case.
        # A query parameter rather than a body: DELETE bodies are unreliable
        # through API Gateway, and the proxy integration passes these through
        # with no route change.
        query = event.get("queryStringParameters") or {}
        force = str(query.get("force", "")).strip().lower() in {"1", "true", "yes"}
        return delete_registration(
            str(path_params.get("registrationId", "")).strip(), force=force
        )

    return respond(405, {"error": "Method not allowed"})
