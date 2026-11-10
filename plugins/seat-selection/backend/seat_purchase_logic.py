"""
Seat Purchase Lifecycle — paid, specific-seat purchases for the seat
selection plugin.

Where seat_selection_logic.py is deliberately ignorant of *why* seats are
claimed, this module is the plugin's own opinionated answer for one
particular "why": a registrant paying for specific seats they picked on the
grid. A purchase holds `seatIds` (not a tier or a quantity) and prices them
via seat_selection_logic.price_seats() — money math and pricing config stay
in that module; this one owns the purchase record's lifecycle (pending ->
paid -> ..., or -> cancelled) and its online-payment bookkeeping.

Deliberately independent of any registration table — a purchase only ever
references a `registrationId` string, the same pattern seat_selection_logic
itself already uses for `claim_seats_atomic`. The seats themselves are held
by claiming them (seat_selection_logic.claim_seats_atomic), not by a
separate capacity counter — DynamoDB's own per-seat conditional Put is
already the concurrency control, so there's nothing else to reserve.

Configuration (environment variables):
  SEAT_PURCHASES_TABLE  - required. Hash key purchaseId only.
"""

import os
import uuid
from datetime import datetime, timezone
from typing import Optional

import boto3
from botocore.exceptions import ClientError
from boto3.dynamodb.conditions import Key

import seat_selection_logic

dynamodb = boto3.resource("dynamodb", region_name=os.environ.get("REGION"))
SEAT_PURCHASES_TABLE = os.environ.get("SEAT_PURCHASES_TABLE", "")

PAYMENT_PROVIDERS = ("paypay", "square")

VALID_STATUSES = {"pending", "paid", "cancelled", "refund_requested"}
# "refund_requested" sits between paid and cancelled: a registrant cancelling
# an already-paid purchase lands here first (see cancel_purchase_request) and
# still holds its seats — only an admin finalizing it to "cancelled" (once
# the refund is actually confirmed, automatically or by hand) releases them.
# Mirrors the same "admin confirms" pattern bank-transfer payment uses.
ACTIVE_STATUSES = {"pending", "paid", "refund_requested"}  # statuses that hold their seats


def _is_conditional_check_failed(exc: ClientError) -> bool:
    return exc.response.get("Error", {}).get("Code") == "ConditionalCheckFailedException"


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def get_purchase(purchase_id: str) -> Optional[dict]:
    if not SEAT_PURCHASES_TABLE:
        return None
    try:
        return dynamodb.Table(SEAT_PURCHASES_TABLE).get_item(Key={"purchaseId": purchase_id}).get("Item")
    except ClientError as exc:
        raise RuntimeError(f"Error fetching seat purchase: {exc}")


def list_purchases_for_registration(registration_id: str) -> list:
    if not SEAT_PURCHASES_TABLE or not registration_id:
        return []
    try:
        response = dynamodb.Table(SEAT_PURCHASES_TABLE).query(
            IndexName="RegistrationIdIndex",
            KeyConditionExpression=Key("registrationId").eq(registration_id),
        )
        items = response.get("Items", [])
        items.sort(key=lambda item: item.get("requestedAt", ""), reverse=True)
        return items
    except ClientError as exc:
        raise RuntimeError(f"Error listing seat purchases for registration: {exc}")


def list_purchases_for_event(event_type: str) -> list:
    if not SEAT_PURCHASES_TABLE or not event_type:
        return []
    try:
        response = dynamodb.Table(SEAT_PURCHASES_TABLE).query(
            IndexName="EventTypeIndex",
            KeyConditionExpression=Key("eventType").eq(event_type),
        )
        items = response.get("Items", [])
        items.sort(key=lambda item: item.get("requestedAt", ""), reverse=True)
        return items
    except ClientError as exc:
        raise RuntimeError(f"Error listing seat purchases for event: {exc}")


def create_purchase_request(
    *,
    event_type: str,
    registration_id: str,
    email: str,
    name: str,
    seat_ids: list,
    pricing_mode: str,
    free: bool = False,
) -> dict:
    """Claim `seat_ids` and record the purchase request.

    Prices the seats first (via seat_selection_logic.price_seats) and
    refuses outright if any seat is unpriced/inactive — never silently
    charges 0 for a seat nobody configured a price for. Claims the seats
    (atomic multi-seat transaction) before writing the purchase row, and
    rolls the claim back if the write fails, so a failed request never
    leaves an orphaned hold.

    `free=True` skips pricing entirely instead — every seat is claimed at
    ¥0 regardless of its category or whether an admin has priced/activated
    it. For a host that's turned its own zone pricing off (e.g. gating zone
    access through a separate paid add-on instead — see wmfjapan's
    `eventInfo.seatSelection.pricingEnabled`) and already checked whatever
    eligibility that implies before calling this; this module has no
    opinion on why a selection should be free, only on what happens once it
    is. `pricing_mode` is still required and stored (some caller might
    still want the family/person distinction recorded even at ¥0), but
    plays no role in the math when `free` is set.

    Either way, a selection that comes out to ¥0 needs no payment at all,
    so it's written straight to "paid" with its check-in token already
    generated, instead of "pending" — there's nothing for an admin to
    confirm or a registrant to pay. The caller (my_registrations) sends the
    matching "paid" email/QR immediately rather than the usual "request
    received" one; see its own create_my_seat_purchase.

    Raises ValueError for a bad request (invalid seats, unpriced zone, or
    one or more seats already taken — same as
    seat_selection_logic.claim_seats_atomic's own contract, which this
    passes straight through), RuntimeError only for an actual system
    failure (table not configured, or the write itself failing after the
    seats were already claimed — rolled back either way).
    """
    if not SEAT_PURCHASES_TABLE:
        raise RuntimeError("Seat purchases table not configured")
    if not seat_ids:
        raise ValueError("At least one seat must be selected")
    if not seat_selection_logic.validate_seat_ids(seat_ids):
        raise ValueError("One or more selected seats are not valid for this event")
    if pricing_mode not in seat_selection_logic.PRICING_MODES:
        raise ValueError(f"pricing_mode must be one of: {', '.join(seat_selection_logic.PRICING_MODES)}")

    pricing = seat_selection_logic.price_seats(event_type, seat_ids, pricing_mode, free=free)
    if pricing["unpriced"]:
        raise ValueError(
            "These seats are not available for purchase yet: " + ", ".join(sorted(pricing["unpriced"]))
        )

    # Claim before writing the purchase row — a party's seats are held the
    # instant they're requested, same as the free picker used to do, just
    # now against a purchase instead of directly against the registration.
    seat_selection_logic.claim_seats_atomic(event_type, registration_id, seat_ids, holder_name=name)

    is_free = pricing["totalAmount"] == 0
    now = now_iso()
    purchase = {
        "purchaseId": str(uuid.uuid4()),
        "eventType": event_type,
        "registrationId": registration_id,
        "email": email,
        "name": name,
        "seatIds": sorted(seat_ids),
        "pricingMode": pricing_mode,
        "breakdown": pricing["breakdown"],
        "totalAmount": pricing["totalAmount"],
        # Distinct from `is_free`/totalAmount==0 below — a Regular-only seat
        # selection is ¥0 even with pricing on. This records whether pricing
        # applied to this purchase *at all*, so callers (the emailer, in
        # particular) can tell "nothing to charge" apart from "pricing was
        # off for this event", which needs different copy.
        "pricingEnabled": not free,
        "status": "paid" if is_free else "pending",
        "paymentMethod": "",
        "requestedAt": now,
        "updatedAt": now,
    }
    if is_free:
        purchase["checkInToken"] = uuid.uuid4().hex

    try:
        dynamodb.Table(SEAT_PURCHASES_TABLE).put_item(Item=purchase)
    except ClientError as exc:
        try:
            seat_selection_logic.release_seats(event_type, seat_ids)
        except Exception:
            pass
        raise RuntimeError(f"Error creating seat purchase request: {exc}")

    return purchase


def void_purchase_request(purchase_id: str, *, event_type: str, seat_ids: list) -> None:
    """Undo a just-created purchase in full: release its seats and delete its
    row, exactly as create_purchase_request's own failure path does.

    For a caller-side business rule this module deliberately has no opinion
    on (see the module/create_purchase_request docstrings) that the caller
    re-checks *after* commit and finds violated — namely a host's own
    per-registration seat cap, which can't be enforced inside the atomic
    seat claim above because it depends on state (every other purchase this
    registration holds) outside any single seat's conditional write. Two
    concurrent requests from the same registration can each pass that cap
    check before either commits; the caller re-checks once both have (or
    haven't) landed and voids the one that pushed the registration over, so
    the client only ever sees the correct final outcome, never a false
    success. Best-effort on both steps: the caller is already reporting an
    error and has nothing further to roll back to either way.
    """
    try:
        seat_selection_logic.release_seats(event_type, seat_ids)
    except Exception:
        pass
    if not SEAT_PURCHASES_TABLE:
        return
    try:
        dynamodb.Table(SEAT_PURCHASES_TABLE).delete_item(Key={"purchaseId": purchase_id})
    except ClientError:
        pass


def set_purchase_seats(purchase_id: str, seat_ids: list, *, holder_name: str = "") -> dict:
    """Replace an existing ¥0 purchase's own seatIds with `seat_ids` in
    place, instead of writing a separate purchase — used when an admin
    re-opens their own earlier allocation to add, remove, or swap seats
    for a registration (see manage_registrations.allocate_registration_
    seats). Claims whatever's newly added, releases whatever's dropped,
    and leaves any seat present in both untouched, so repeated allocations
    edit one ticket/QR in place instead of fragmenting into several, each
    of which would only ever show its own slice of the seats.

    Refuses a purchase that isn't free (pricingEnabled is False), isn't
    `paid`, or has already been checked in — touching any of those would
    either corrupt a real payment's own record (a free re-price would wipe
    out what was actually charged) or touch seats already used at the
    door. The caller is expected to have already picked a genuinely
    mergeable purchase (see _find_mergeable_seat_purchase there); this
    function still refuses outright rather than trusting that.

    Raises ValueError for bad input (not mergeable, invalid/duplicate
    seats, or a newly-added seat someone else already holds), RuntimeError
    for a system failure. Reuses the purchase's own checkInToken — the
    same ticket a registrant may have already saved keeps working, now
    covering the edited seat set.
    """
    purchase = get_purchase(purchase_id)
    if not purchase:
        raise ValueError("Seat purchase request not found")
    if purchase.get("pricingEnabled", True) or purchase.get("status") != "paid":
        raise ValueError("This purchase can't be changed — it isn't a free, already-paid allocation")
    if purchase.get("checkedIn"):
        raise ValueError("This purchase has already been checked in and can't be changed")
    if not seat_ids:
        raise ValueError("At least one seat must be selected")
    if len(set(seat_ids)) != len(seat_ids):
        raise ValueError("Duplicate seats selected")
    if not seat_selection_logic.validate_seat_ids(seat_ids):
        raise ValueError("One or more selected seats are not valid for this event")

    event_type = purchase.get("eventType", "")
    existing_seat_ids = set(purchase.get("seatIds") or [])
    new_seat_ids = set(seat_ids)
    to_claim = sorted(new_seat_ids - existing_seat_ids)
    to_release = sorted(existing_seat_ids - new_seat_ids)

    if to_claim:
        # All-or-nothing for the newly-added seats only — anything already
        # held by this same purchase needs no reclaiming, and isn't at risk
        # of a conflict.
        seat_selection_logic.claim_seats_atomic(
            event_type, purchase.get("registrationId", ""), to_claim, holder_name=holder_name
        )
    if to_release:
        try:
            seat_selection_logic.release_seats(event_type, to_release)
        except Exception:
            pass

    pricing = seat_selection_logic.price_seats(
        event_type, sorted(seat_ids), purchase.get("pricingMode") or "person", free=True
    )
    now = now_iso()
    try:
        response = dynamodb.Table(SEAT_PURCHASES_TABLE).update_item(
            Key={"purchaseId": purchase_id},
            UpdateExpression="SET seatIds = :seat_ids, breakdown = :breakdown, updatedAt = :now",
            ExpressionAttributeValues={
                ":seat_ids": sorted(seat_ids),
                ":breakdown": pricing["breakdown"],
                ":now": now,
            },
            ReturnValues="ALL_NEW",
        )
    except ClientError as exc:
        if to_claim:
            try:
                seat_selection_logic.release_seats(event_type, to_claim)
            except Exception:
                pass
        raise RuntimeError(f"Error updating seat purchase: {exc}")

    return response.get("Attributes", {})


def update_purchase_status(purchase_id: str, status: str) -> dict:
    """Admin/system transition: pending -> paid, or either -> cancelled.

    Cancelling releases the seats the purchase was holding — except a
    purchase that's already been checked in at the venue, which can never
    be cancelled by any path (self-service or admin): those seats were
    already used, so releasing them for resale would double-book whoever
    scans a second ticket for the same seat.
    """
    if status not in VALID_STATUSES:
        raise ValueError(f"Unknown status: {status!r}")

    existing = get_purchase(purchase_id)
    if not existing:
        raise ValueError("Seat purchase request not found")

    prior_status = existing.get("status", "pending")
    if prior_status == status:
        return existing
    if status == "cancelled" and existing.get("checkedIn"):
        raise ValueError("This ticket has already been checked in at the venue and can no longer be cancelled.")

    try:
        table = dynamodb.Table(SEAT_PURCHASES_TABLE)
        update_expression = "SET #status = :status, updatedAt = :now"
        expression_values = {":status": status, ":now": now_iso()}
        if status == "paid":
            # A check-in QR only ever means something once a purchase is
            # actually paid for — generated here (not at purchase creation)
            # so the sparse CheckInTokenIndex only ever holds paid tickets.
            # if_not_exists keeps it stable across a redundant "mark paid"
            # call instead of invalidating a QR already emailed out.
            update_expression += ", checkInToken = if_not_exists(checkInToken, :token)"
            expression_values[":token"] = uuid.uuid4().hex
        response = table.update_item(
            Key={"purchaseId": purchase_id},
            UpdateExpression=update_expression,
            ExpressionAttributeNames={"#status": "status"},
            ExpressionAttributeValues=expression_values,
            ReturnValues="ALL_NEW",
        )
        updated = response.get("Attributes", {})
    except ClientError as exc:
        raise RuntimeError(f"Error updating seat purchase status: {exc}")

    if status == "cancelled" and prior_status in ACTIVE_STATUSES:
        try:
            seat_selection_logic.release_seats(existing["eventType"], existing.get("seatIds", []))
        except Exception:
            pass

    return updated


def cancel_purchase_request(purchase_id: str, *, registration_id: str) -> dict:
    """Self-service cancel: only the owning registration may act, and only
    while the request is still active.

    A pending (unpaid) request is cancelled outright — its seats are
    released immediately. A paid request instead moves to
    "refund_requested" (seats stay held); the caller is responsible for
    attempting a provider refund, and an admin still has to finalize it to
    "cancelled" once the money is actually back with the registrant. The
    one exception is a ¥0 purchase (Regular seats only — see
    create_purchase_request) — there's no refund to wait on, so it goes
    straight to "cancelled" and releases its seats immediately, same as a
    still-pending one.

    A purchase already checked in at the venue can never be cancelled —
    those seats were used, so there's nothing left to refund or release.
    """
    existing = get_purchase(purchase_id)
    if not existing:
        raise ValueError("Seat purchase request not found")
    if existing.get("registrationId") != registration_id:
        raise PermissionError("You can only cancel your own seat purchase requests")
    if existing.get("checkedIn"):
        raise ValueError("This ticket has already been checked in at the venue and can no longer be cancelled.")
    status = existing.get("status")
    if status == "pending" or (status == "paid" and not existing.get("totalAmount")):
        return update_purchase_status(purchase_id, "cancelled")
    if status == "paid":
        return update_purchase_status(purchase_id, "refund_requested")
    raise ValueError("This seat purchase request can no longer be cancelled here")


# ── Online payment (PayPay / Square hosted checkout) ────────────────────────
#
# `status` stays the admin-facing pending/paid/cancelled tri-state (bank
# transfer still ends there via an admin's "Mark Paid"). These fields track
# the online-checkout attempt underneath it without changing that contract.


def set_payment_method(purchase_id: str, payment_method: str) -> dict:
    """Stamp a pending request with the registrant's declared payment
    method (informational for "bank", a precursor to record_checkout for
    "paypay"/"square")."""
    if not SEAT_PURCHASES_TABLE:
        raise RuntimeError("Seat purchases table not configured")
    try:
        table = dynamodb.Table(SEAT_PURCHASES_TABLE)
        response = table.update_item(
            Key={"purchaseId": purchase_id},
            UpdateExpression="SET paymentMethod = :method, updatedAt = :now",
            ExpressionAttributeValues={":method": payment_method, ":now": now_iso()},
            ReturnValues="ALL_NEW",
        )
        return response.get("Attributes", {})
    except ClientError as exc:
        raise RuntimeError(f"Error setting payment method: {exc}")


def record_checkout(purchase_id: str, *, provider: str, provider_payment_id: str, checkout_url: str) -> dict:
    """Stamp a request with the checkout session just created for it.

    Only allowed while still pending. Re-callable (e.g. the registrant
    abandons PayPay and retries with Square): the latest attempt simply
    overwrites the fields.
    """
    if provider not in PAYMENT_PROVIDERS:
        raise ValueError(f"Unknown payment provider: {provider!r}")

    existing = get_purchase(purchase_id)
    if not existing:
        raise ValueError("Seat purchase request not found")
    if existing.get("status") != "pending":
        raise ValueError("Only a pending seat purchase request can start a checkout")

    try:
        table = dynamodb.Table(SEAT_PURCHASES_TABLE)
        response = table.update_item(
            Key={"purchaseId": purchase_id},
            UpdateExpression=(
                "SET paymentProvider = :provider, providerPaymentId = :ppid, "
                "checkoutUrl = :url, checkoutCreatedAt = :now, updatedAt = :now"
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
        raise RuntimeError(f"Error recording checkout: {exc}")


def get_purchase_by_provider_payment_id(provider_payment_id: str) -> Optional[dict]:
    if not SEAT_PURCHASES_TABLE or not provider_payment_id:
        return None
    try:
        response = dynamodb.Table(SEAT_PURCHASES_TABLE).query(
            IndexName="ProviderPaymentIdIndex",
            KeyConditionExpression=Key("providerPaymentId").eq(provider_payment_id),
            Limit=1,
        )
        items = response.get("Items", [])
        return items[0] if items else None
    except ClientError as exc:
        raise RuntimeError(f"Error looking up seat purchase by provider payment id: {exc}")


def mark_paid_from_provider(provider_payment_id: str, *, payment_reference: str = "") -> tuple:
    """Idempotent webhook/return-page transition: pending -> paid.

    Returns (record, was_newly_paid). `was_newly_paid` is True only for the
    call that actually performed the pending->paid transition — callers use
    that to decide whether to fire the "payment received" email/QR, so a
    webhook that fires twice, or races the return-page reconcile call, only
    ever sends it once.
    """
    existing = get_purchase_by_provider_payment_id(provider_payment_id)
    if not existing:
        return None, False
    if existing.get("status") == "paid":
        return existing, False  # already processed — most likely a duplicate webhook delivery

    updated = update_purchase_status(existing["purchaseId"], "paid")
    if payment_reference:
        try:
            table = dynamodb.Table(SEAT_PURCHASES_TABLE)
            response = table.update_item(
                Key={"purchaseId": existing["purchaseId"]},
                UpdateExpression="SET paymentReference = :ref",
                ExpressionAttributeValues={":ref": payment_reference},
                ReturnValues="ALL_NEW",
            )
            updated = response.get("Attributes", updated)
        except ClientError:
            pass  # the status transition already succeeded; the reference is best-effort metadata
    return updated, True


def record_refund_attempt(purchase_id: str, *, refund_status: str, refund_reference: str = "") -> dict:
    """Stamp a "refund_requested" record with the outcome of attempting it
    against the payment provider (or a note that it needs manual handling).

    Purely informational bookkeeping for the admin dashboard — it never
    changes `status` itself; an admin still finalizes the request to
    "cancelled" once the refund is actually confirmed.
    """
    if not SEAT_PURCHASES_TABLE:
        raise RuntimeError("Seat purchases table not configured")
    updates = [
        "refundStatus = :status",
        "refundRequestedAt = if_not_exists(refundRequestedAt, :now)",
        "updatedAt = :now",
    ]
    values = {":status": refund_status, ":now": now_iso()}
    if refund_reference:
        updates.append("refundReference = :ref")
        values[":ref"] = refund_reference
    try:
        table = dynamodb.Table(SEAT_PURCHASES_TABLE)
        response = table.update_item(
            Key={"purchaseId": purchase_id},
            UpdateExpression="SET " + ", ".join(updates),
            ExpressionAttributeValues=values,
            ReturnValues="ALL_NEW",
        )
        return response.get("Attributes", {})
    except ClientError as exc:
        raise RuntimeError(f"Error recording refund attempt: {exc}")


def get_purchase_by_check_in_token(token: str) -> Optional[dict]:
    """Look up a purchase by its check-in QR token — a purpose-built token,
    independent of any host-owned entry-pass/registration token, so a
    seat-purchase ticket can never be confused with (or accidentally admit
    on) an unrelated check-in mechanism the host might already have."""
    if not SEAT_PURCHASES_TABLE or not token:
        return None
    try:
        response = dynamodb.Table(SEAT_PURCHASES_TABLE).query(
            IndexName="CheckInTokenIndex",
            KeyConditionExpression=Key("checkInToken").eq(token),
            Limit=1,
        )
        items = response.get("Items", [])
        return items[0] if items else None
    except ClientError as exc:
        raise RuntimeError(f"Error looking up seat purchase by check-in token: {exc}")


def check_in_purchase(token: str, *, checked_in_by: str = "") -> dict:
    """Atomically consume this purchase's check-in QR at the door.

    Idempotent by design (a scanner can hit the same code twice by
    accident) — re-checking in an already-checked-in purchase just returns
    the existing check-in record instead of erroring. Only a `paid`
    purchase can be checked in; a cancelled or still-pending one is refused
    so a stale or unpaid ticket can never admit anyone.
    """
    if not SEAT_PURCHASES_TABLE:
        raise RuntimeError("Seat purchases table not configured")
    existing = get_purchase_by_check_in_token(token)
    if not existing:
        raise ValueError("Invalid check-in code")
    if existing.get("status") != "paid":
        raise ValueError(f"This ticket is {existing.get('status', 'not paid')} — it cannot be checked in")
    if existing.get("checkedIn"):
        return existing

    try:
        table = dynamodb.Table(SEAT_PURCHASES_TABLE)
        response = table.update_item(
            Key={"purchaseId": existing["purchaseId"]},
            UpdateExpression="SET checkedIn = :true, checkedInAt = :now, checkedInBy = :by",
            ConditionExpression="attribute_not_exists(checkedIn) OR checkedIn = :false",
            ExpressionAttributeValues={":true": True, ":false": False, ":now": now_iso(), ":by": checked_in_by},
            ReturnValues="ALL_NEW",
        )
        return response.get("Attributes", {})
    except ClientError as exc:
        if _is_conditional_check_failed(exc):
            # Raced another scan of the same code — the other scan won the
            # write, but this is still a successful check-in from the
            # door's point of view.
            return get_purchase_by_check_in_token(token) or existing
        raise RuntimeError(f"Error checking in seat purchase: {exc}")
