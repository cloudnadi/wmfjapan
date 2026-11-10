"""Allocation-level entry pass creation and validation helpers."""

import os
import secrets
import uuid
from datetime import datetime, timezone

import boto3
from botocore.exceptions import ClientError
from boto3.dynamodb.conditions import Key

import entry_slots_logic

ALLOCATIONS_TABLE = os.environ.get("ENTRY_ALLOCATIONS_TABLE", "")
REGISTRATIONS_TABLE = os.environ.get("DYNAMODB_TABLE", "")

dynamodb = boto3.resource("dynamodb", region_name=os.environ.get("REGION"))
# `dynamodb.meta.client` is the resource's client, so boto3's high-level
# transform handlers are registered on it: transact_write_items takes (and
# returns) plain Python values, exactly like Table.put_item. Never hand it
# TypeSerializer output — it would be serialized a second time and every value
# would land as an {"M": ...} map.
dynamodb_client = dynamodb.meta.client


def now_iso():
    return datetime.now(timezone.utc).isoformat()


def required_seats_for_registration(registration):
    """Map a stored registration's attendeeCounts to entry-slot seats.

    Handles both the Onam schema (adults/kids914/kids68/kidsUnder6) and the
    standard one (adult/teen/child/toddler).
    """
    counts = registration.get("attendeeCounts", {}) or {}
    if registration.get("registrationType") == "onam" or "adults" in counts:
        adults = int(counts.get("adults", 0) or 0)
        kids914 = int(counts.get("kids914", 0) or 0)
        kids68 = int(counts.get("kids68", 0) or 0)
        kids_below_6 = int(counts.get("kidsUnder6", 0) or 0)
    else:
        adults = int(counts.get("adult", 0) or 0) + int(counts.get("teen", 0) or 0)
        kids914 = 0
        kids68 = int(counts.get("child", 0) or 0)
        kids_below_6 = int(counts.get("toddler", 0) or 0)
    return entry_slots_logic.calculate_required_seats(
        adults=adults,
        kids9_to_14=kids914,
        kids6_to_8=kids68,
        kids_below_6=kids_below_6,
    )


def normalize_requested_allocations(body, required_seats):
    """Return validated {slotId, seatCount} entries, with legacy fallback."""
    requested = body.get("allocations")
    if requested is None:
        slot_id = str(body.get("slotId", "")).strip()
        requested = [{"slotId": slot_id, "seatCount": required_seats}] if slot_id else []
    if not isinstance(requested, list) or not requested:
        raise ValueError("At least one entry-slot allocation is required")

    allocations = []
    total = 0
    for item in requested:
        if not isinstance(item, dict):
            raise TypeError("Each entry allocation must include a slotId and seatCount")
        slot_id = str(item.get("slotId", "")).strip()
        try:
            seat_count = int(item.get("seatCount", item.get("seats", 0)))
        except (TypeError, ValueError):
            seat_count = 0
        if not slot_id or seat_count <= 0:
            raise ValueError("Each entry allocation must have a slot and a positive seat count")
        allocations.append({"slotId": slot_id, "seatCount": seat_count})
        total += seat_count

    if total != required_seats:
        raise ValueError(
            f"Entry allocations must total {required_seats} seated people; received {total}"
        )
    return allocations


def create_allocation_item(event_type, registration_id, requested, slot, reservation):
    """Build an allocation item with a cryptographically secure public token."""
    timestamp = now_iso()
    return {
        "eventType": event_type,
        "allocationId": str(uuid.uuid4()),
        "registrationId": registration_id,
        "entrySlotId": requested["slotId"],
        "slotName": str((slot or {}).get("name", "")),
        "seatCount": requested["seatCount"],
        "checkedInCount": 0,
        "publicToken": secrets.token_urlsafe(32),
        "status": "ACTIVE",
        "reservationId": reservation.get("reservationId", ""),
        "createdAt": timestamp,
        "updatedAt": timestamp,
    }


def persist_allocation(item):
    if not ALLOCATIONS_TABLE:
        raise RuntimeError("Entry allocations are not configured")
    try:
        dynamodb.Table(ALLOCATIONS_TABLE).put_item(
            Item=item,
            ConditionExpression="attribute_not_exists(allocationId)",
        )
    except ClientError as exc:
        raise RuntimeError(f"Could not persist entry allocation: {exc}") from exc


def cancel_allocation(item):
    """Compensating rollback: mark CANCELLED without deleting the audit trail."""
    if not ALLOCATIONS_TABLE:
        return
    try:
        dynamodb.Table(ALLOCATIONS_TABLE).update_item(
            Key={"eventType": item["eventType"], "allocationId": item["allocationId"]},
            UpdateExpression="SET #status = :cancelled, updatedAt = :now",
            ExpressionAttributeNames={"#status": "status"},
            ExpressionAttributeValues={":cancelled": "CANCELLED", ":now": now_iso()},
        )
    except ClientError:
        pass


def get_allocation(event_type, allocation_id):
    """Fetch the full allocation item (includes reservationId), or None."""
    if not ALLOCATIONS_TABLE or not allocation_id:
        return None
    try:
        return dynamodb.Table(ALLOCATIONS_TABLE).get_item(
            Key={"eventType": event_type, "allocationId": allocation_id}
        ).get("Item")
    except ClientError:
        return None


def get_allocation_by_token(public_token):
    """Resolve an opaque pass token to its internal allocation item."""
    if not ALLOCATIONS_TABLE or not public_token:
        return None
    try:
        response = dynamodb.Table(ALLOCATIONS_TABLE).query(
            IndexName="PublicTokenIndex",
            KeyConditionExpression=Key("publicToken").eq(public_token),
            Limit=1,
        )
        items = response.get("Items", [])
        return items[0] if items else None
    except ClientError:
        return None


def backfill_legacy_allocation(registration):
    """Materialise the missing allocation row for a pre-allocation registration.

    Registrations made before the allocation model stored their single entry
    slot inline (slotId/slotName/slotSeats/slotReservationId) and have no
    allocation row, so their pass resolves but reports zero seats and cannot be
    checked in. Create the allocation for that slot on first scan. The slot
    capacity was already reserved when the registration was made, so this only
    creates the check-in record — it never reserves additional seats.

    Returns the registration, updated in place when a backfill happened.
    """
    registration_id = str(registration.get("registrationId", "") or "").strip()
    slot_id = str(registration.get("slotId", "") or "").strip()
    if registration.get("allocations") or not slot_id or not registration_id:
        return registration
    if not ALLOCATIONS_TABLE or not REGISTRATIONS_TABLE:
        return registration

    seat_count = int(registration.get("slotSeats", 0) or 0)
    if seat_count <= 0:
        # slotSeats predates a few records; fall back to the party's seat count.
        try:
            seat_count = required_seats_for_registration(registration)
        except (TypeError, ValueError):
            seat_count = 0
    if seat_count <= 0:
        return registration

    event_type = str(registration.get("eventType", "") or "")
    item = create_allocation_item(
        event_type,
        registration_id,
        {"slotId": slot_id, "seatCount": seat_count},
        {"name": registration.get("slotName", "")},
        {"reservationId": str(registration.get("slotReservationId", "") or "")},
    )
    try:
        persist_allocation(item)
    except RuntimeError as exc:
        print(f"Warning: Could not backfill allocation for {registration_id}: {exc}")
        return registration

    allocations = [public_allocation(item)]
    try:
        dynamodb.Table(REGISTRATIONS_TABLE).update_item(
            Key={"registrationId": registration_id},
            UpdateExpression="SET #allocations = :allocations",
            # Only the first scanner wins; a concurrent one must not orphan the
            # allocation the registration already points at.
            ConditionExpression=(
                "attribute_not_exists(#allocations) OR size(#allocations) = :empty"
            ),
            ExpressionAttributeNames={"#allocations": "allocations"},
            ExpressionAttributeValues={":allocations": allocations, ":empty": 0},
        )
    except ClientError as exc:
        cancel_allocation(item)
        if exc.response.get("Error", {}).get("Code") != "ConditionalCheckFailedException":
            print(f"Warning: Could not attach backfilled allocation to {registration_id}: {exc}")
            return registration
        # Another scanner backfilled first — adopt whatever it wrote.
        try:
            current = dynamodb.Table(REGISTRATIONS_TABLE).get_item(
                Key={"registrationId": registration_id}
            ).get("Item")
        except ClientError:
            current = None
        registration["allocations"] = (current or {}).get("allocations") or []
        return registration

    registration["allocations"] = allocations
    return registration


def get_registration_by_pass_token(public_token):
    """Resolve one opaque registration pass token to its registration record."""
    if not REGISTRATIONS_TABLE or not public_token:
        return None
    try:
        response = dynamodb.Table(REGISTRATIONS_TABLE).query(
            IndexName="RegistrationPassTokenIndex",
            KeyConditionExpression=Key("registrationPassToken").eq(public_token),
            Limit=1,
        )
        items = response.get("Items", [])
        if items:
            registration = items[0]
        else:
            # Compatibility for registrations created before registration-level tokens.
            allocation = get_allocation_by_token(public_token)
            if not allocation or not REGISTRATIONS_TABLE:
                return None
            registration = dynamodb.Table(REGISTRATIONS_TABLE).get_item(
                Key={"registrationId": allocation.get("registrationId", "")}
            ).get("Item")
            if not registration:
                return None
        backfill_legacy_allocation(registration)
        return refresh_allocations(registration)
    except ClientError:
        return None


def payment_is_complete(registration):
    """True once the money is in. Only `paid` counts — a pending or refunded
    registration has no settled payment behind it."""
    return str(registration.get("paymentStatus", "")).strip().lower() == "paid"


def checked_in_seats(registration):
    """How many people on this registration have already been admitted.

    Reads the allocations table rather than the registration's snapshot, which
    can lag behind a check-in. Counters that predate the serialization fix are
    maps rather than numbers; they count as zero here, and check-in itself
    rejects them with a pointer to the repair script.
    """
    event_type = str(registration.get("eventType", "") or "")
    total = 0
    for snapshot in registration.get("allocations") or []:
        current = get_allocation(event_type, snapshot.get("allocationId", "")) or snapshot
        try:
            total += int(current.get("checkedInCount", 0) or 0)
        except (TypeError, ValueError):
            continue
    return total


def refresh_allocations(registration):
    """Replace a registration's allocation snapshot with the live rows.

    The allocations table is the source of truth for check-in counters; the
    registration record only carries a snapshot taken when the passes were
    issued. Anything that shows check-in state has to refresh it, or it reports
    nobody as checked in.

    Returns the registration, updated in place.
    """
    event_type = str(registration.get("eventType", "") or "")
    refreshed = []
    for snapshot in registration.get("allocations") or []:
        current = get_allocation(event_type, snapshot.get("allocationId", ""))
        refreshed.append(public_allocation(current) if current else snapshot)
    registration["allocations"] = refreshed
    return registration


def _sync_registration_snapshot(registration_id, allocations):
    """Mirror live allocation state back onto the registration record.

    Keeps everything that reads registration records (the registrant's page,
    admin views, exports) in step without a per-row lookup. Best-effort: the
    check-in has already been committed, and refresh_allocations repairs any
    divergence on the next read.
    """
    if not REGISTRATIONS_TABLE or not registration_id:
        return
    try:
        dynamodb.Table(REGISTRATIONS_TABLE).update_item(
            Key={"registrationId": registration_id},
            UpdateExpression="SET #allocations = :allocations",
            ExpressionAttributeNames={"#allocations": "allocations"},
            ExpressionAttributeValues={":allocations": allocations},
        )
    except ClientError as exc:
        print(f"Warning: Could not refresh allocation snapshot for {registration_id}: {exc}")


def public_allocation(item):
    """Return only registrant-safe allocation fields, including the QR token."""
    return {
        "allocationId": item["allocationId"],
        "entrySlotId": item["entrySlotId"],
        "slotName": item.get("slotName", ""),
        "seatCount": int(item["seatCount"]),
        "checkedInCount": int(item.get("checkedInCount", 0)),
        "remaining": max(int(item["seatCount"]) - int(item.get("checkedInCount", 0)), 0),
        "publicToken": item["publicToken"],
        "status": item.get("status", "ACTIVE"),
        "createdAt": item.get("createdAt", ""),
        "updatedAt": item.get("updatedAt", ""),
    }


def _seat_counters(item):
    """(seatCount, checkedInCount) as ints, tolerating missing/empty values."""
    return (
        int(item.get("seatCount", 0) or 0),
        int(item.get("checkedInCount", 0) or 0),
    )


def _no_overflow_condition():
    """Guard that a check-in cannot push checkedInCount past seatCount.

    DynamoDB condition expressions cannot do arithmetic (`checkedInCount +
    :quantity <= seatCount` is a syntax error), so the bound is precomputed by
    the caller: `:max_before` is seatCount − quantity, which makes
    `checkedInCount <= :max_before` the same test. As in
    entry_slots_logic.reserve_seats_atomic, the counter is also the optimistic
    lock — a concurrent check-in moves it past the bound and cancels us.
    """
    return (
        "#status = :active "
        "AND (attribute_not_exists(checkedInCount) OR checkedInCount <= :max_before)"
    )


def check_in_by_token(public_token, quantity, checked_in_by):
    """Atomically consume quantity and append its audit record."""
    check_ins_table = os.environ.get("ENTRY_CHECK_INS_TABLE", "")
    if not ALLOCATIONS_TABLE or not check_ins_table or quantity <= 0:
        raise ValueError("A positive check-in quantity is required")
    allocations_table = dynamodb.Table(ALLOCATIONS_TABLE)
    result = allocations_table.query(
        IndexName="PublicTokenIndex",
        KeyConditionExpression=Key("publicToken").eq(public_token),
        Limit=1,
    )
    items = result.get("Items", [])
    if not items:
        raise ValueError("Invalid entry pass")
    allocation = items[0]
    allocation_id = allocation["allocationId"]
    seat_count, checked_in = _seat_counters(allocation)
    if checked_in + quantity > seat_count:
        raise ValueError("Entry allocation has no remaining capacity")
    check_in_id = str(uuid.uuid4())
    timestamp = now_iso()
    try:
        dynamodb_client.transact_write_items(TransactItems=[
            {
                "Update": {
                    "TableName": ALLOCATIONS_TABLE,
                    "Key": {"eventType": allocation["eventType"], "allocationId": allocation_id},
                    "UpdateExpression": (
                        "SET checkedInCount = if_not_exists(checkedInCount, :zero) + :quantity, "
                        "updatedAt = :updatedAt"
                    ),
                    "ConditionExpression": _no_overflow_condition(),
                    "ExpressionAttributeNames": {"#status": "status"},
                    "ExpressionAttributeValues": {
                        ":quantity": quantity, ":updatedAt": timestamp, ":active": "ACTIVE",
                        ":zero": 0, ":max_before": seat_count - quantity,
                    },
                }
            },
            {
                "Put": {
                    "TableName": check_ins_table,
                    "Item": {
                        "allocationId": allocation_id, "checkInId": check_in_id,
                        "quantity": quantity, "checkedInAt": timestamp,
                        "checkedInBy": checked_in_by, "createdAt": timestamp,
                    },
                    "ConditionExpression": "attribute_not_exists(checkInId)",
                }
            },
        ])
    except ClientError as exc:
        if exc.response.get("Error", {}).get("Code") == "TransactionCanceledException":
            raise ValueError("Entry allocation has no remaining capacity") from exc
        raise RuntimeError(f"Could not check in entry pass: {exc}") from exc
    return {
        **public_allocation({**allocation, "checkedInCount": int(allocation.get("checkedInCount", 0)) + quantity, "updatedAt": timestamp}),
        "checkInId": check_in_id,
    }


def get_registration_by_id(registration_id):
    """Resolve a registration record by its own ID (admin manual check-in path,
    used when there is no pass token to scan)."""
    if not REGISTRATIONS_TABLE or not registration_id:
        return None
    registration = dynamodb.Table(REGISTRATIONS_TABLE).get_item(
        Key={"registrationId": registration_id}
    ).get("Item")
    if not registration:
        return None
    backfill_legacy_allocation(registration)
    return refresh_allocations(registration)


def check_in_registration_by_token(public_token, checked_in_by):
    """Atomically check in every remaining person across all allocations."""
    registration = get_registration_by_pass_token(public_token)
    if not registration:
        raise ValueError("Invalid entry pass")
    return check_in_registration(registration, checked_in_by)


def check_in_registration_by_id(registration_id, checked_in_by):
    """Admin path: check in a registration directly by ID, no pass token
    required (e.g. the attendee lost or never received their QR pass)."""
    registration = get_registration_by_id(registration_id)
    if not registration:
        raise ValueError("Registration not found")
    return check_in_registration(registration, checked_in_by)


def check_in_registration(registration, checked_in_by):
    """Atomically check in every remaining person across all allocations."""
    check_ins_table = os.environ.get("ENTRY_CHECK_INS_TABLE", "")
    if not ALLOCATIONS_TABLE or not check_ins_table:
        raise RuntimeError("Entry check-in is not configured")

    # No entry until the registration is paid for. Checked before anything is
    # written, so an unpaid scan leaves the counters untouched and can be
    # retried once the payment is recorded.
    if not payment_is_complete(registration):
        raise ValueError("Payment is not complete for this registration, so entry cannot be granted")
    event_type = registration.get("eventType", "")

    items = []
    for allocation in registration.get("allocations") or []:
        item = get_allocation(event_type, allocation.get("allocationId", ""))
        if item:
            items.append(item)
    if not items:
        raise ValueError("Registration has no entry allocations")
    # Counters written before the double-serialization fix landed as maps;
    # arithmetic on them cancels the whole transaction with no useful message.
    if any(isinstance(item.get("checkedInCount"), dict) for item in items):
        raise RuntimeError(
            "Entry allocation data is invalid: checkedInCount must be numeric. "
            "Run repair_entry_allocation_counters.py before checking in."
        )
    if any(str(item.get("status", "ACTIVE")) == "CANCELLED" for item in items):
        raise ValueError("Entry pass is cancelled")

    # Only allocations with seats left take part: an already FULLY_USED one
    # fails the `status = ACTIVE` condition and would cancel the transaction
    # for the rest of the party (and write a pointless 0-seat audit row).
    pending = []
    for item in items:
        seat_count, checked_in = _seat_counters(item)
        if seat_count - checked_in > 0:
            pending.append((item, seat_count, checked_in))
    if not pending:
        raise ValueError("Entry pass has already been fully used")

    timestamp = now_iso()
    transact_items = []
    audit_items = []
    for item, seat_count, checked_in in pending:
        remaining = seat_count - checked_in
        check_in_id = str(uuid.uuid4())
        transact_items.append({
            "Update": {
                "TableName": ALLOCATIONS_TABLE,
                "Key": {"eventType": item["eventType"], "allocationId": item["allocationId"]},
                "UpdateExpression": (
                    "SET checkedInCount = if_not_exists(checkedInCount, :zero) + :quantity, "
                    "updatedAt = :updatedAt, #status = :used"
                ),
                "ConditionExpression": _no_overflow_condition(),
                "ExpressionAttributeNames": {"#status": "status"},
                "ExpressionAttributeValues": {
                    ":quantity": remaining,
                    # We consume the whole remainder, so seatCount − quantity
                    # is exactly the counter we read.
                    ":max_before": checked_in,
                    ":zero": 0,
                    ":updatedAt": timestamp,
                    ":active": "ACTIVE",
                    ":used": "FULLY_USED",
                },
            }
        })
        audit_items.append((item, remaining, check_in_id))

    for item, quantity, check_in_id in audit_items:
        transact_items.append({
            "Put": {
                "TableName": check_ins_table,
                "Item": {
                    "allocationId": item["allocationId"],
                    "checkInId": check_in_id,
                    "quantity": quantity,
                    "checkedInAt": timestamp,
                    "checkedInBy": checked_in_by,
                    "createdAt": timestamp,
                },
                "ConditionExpression": "attribute_not_exists(checkInId)",
            }
        })

    try:
        dynamodb_client.transact_write_items(TransactItems=transact_items)
    except ClientError as exc:
        if exc.response.get("Error", {}).get("Code") == "TransactionCanceledException":
            raise ValueError("Entry pass has already been fully used") from exc
        raise RuntimeError(f"Could not check in registration: {exc}") from exc

    consumed = {item["allocationId"]: quantity for item, quantity, _ in audit_items}
    updated = []
    for item in items:
        quantity = consumed.get(item["allocationId"], 0)
        seat_count, checked_in = _seat_counters(item)
        checked_in += quantity
        updated.append(public_allocation({
            **item,
            "checkedInCount": checked_in,
            "status": "FULLY_USED" if checked_in >= seat_count else item.get("status", "ACTIVE"),
            "updatedAt": timestamp if quantity else item.get("updatedAt", ""),
        }))
    _sync_registration_snapshot(registration.get("registrationId", ""), updated)
    return {
        "allocations": updated,
        "totalAllocated": sum(int(item["seatCount"]) for item in updated),
        "totalCheckedIn": sum(int(item["checkedInCount"]) for item in updated),
        "status": summarize_status(updated),
    }


def summarize_status(allocations):
    """Registration-level pass status derived from its allocations."""
    if any(item.get("status") == "CANCELLED" for item in allocations):
        return "CANCELLED"
    if allocations and all(
        int(item.get("checkedInCount", 0) or 0) >= int(item.get("seatCount", 0) or 0)
        for item in allocations
    ):
        return "FULLY_USED"
    return "ACTIVE"
