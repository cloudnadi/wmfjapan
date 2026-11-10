"""
Event-platform registrations: create, pay, edit, cancel, expire, promote (no AWS calls here).

Storage goes through `store` and card checkout through `payments`; the Lambdas wire in the
DynamoDB store (event_reg_store.py) and square.py, the tests wire in fakes.

Seats (the unit capacity is counted in) are tickets. A registration holds seats while it is
`confirmed` or `pending_payment`; `waitlisted` and `cancelled` hold none. Every transition that
changes the seat count goes through one conditional transaction in the store, so concurrent
sign-ups can neither overbook an event nor release the same seats twice.
"""

from decimal import Decimal
import hashlib
import logging
import secrets
from datetime import timedelta
from typing import Optional

import event_model
import event_pricing
import form_engine

logger = logging.getLogger(__name__)

store = None       # set by the Lambda / tests
payments = None    # square-like adapter: is_configured(), create_checkout(...), cancel_order(...)
notify = None      # callable(kind, registration, event); failures are logged, never raised

MAX_UNPAID_PER_EMAIL = 3   # unpaid holds one address may have per event (stops seat-hoarding with throwaway sign-ups)
HOLD_CARD = timedelta(minutes=30)
HOLD_MANUAL = timedelta(days=7)
METHODS = ("square", "paypay", "bank")
HOLDING = ("confirmed", "pending_payment")
CROCKFORD = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"


class RegError(Exception):
    def __init__(self, status, message, fields=None, code=""):
        super().__init__(message)
        self.status, self.message, self.fields, self.code = status, message, fields or {}, code


class CapacityFull(Exception):
    def __init__(self, scope, key=""):
        super().__init__(scope)
        self.scope, self.key = scope, key


class AlreadyExists(Exception):
    pass


class StateConflict(Exception):
    pass


# ── payment methods (admin-managed in site config, shared by every payment flow) ─

PAYMENT_CONFIG_KEY = "payment_methods_config"
PAYMENT_LABELS = {"square": "Credit / Debit Card", "paypay": "PayPay", "bank": "Bank Transfer"}


def available_payment_methods(store_, square_module=None) -> dict:
    """The methods the admin has switched on (card only if Square is configured), with their instructions."""
    stored = store_.get_site_config(PAYMENT_CONFIG_KEY).get("methods") or {}
    methods = {}
    for key, label in PAYMENT_LABELS.items():
        entry = stored.get(key) if isinstance(stored.get(key), dict) else {}
        if entry.get("enabled") is not True:
            continue
        if key == "square" and not (square_module and square_module.is_configured()):
            continue
        url = str(entry.get("url", "")).strip()
        methods[key] = {"key": key, "label": label, "instructions": str(entry.get("instructions", "")).strip(),
                        "url": "" if key == "square" or not url.startswith(("http://", "https://")) else url}
    return methods


# ── helpers ───────────────────────────────────────────────────────────────────

def make_registration_id(event_id: str, email: str, key: str, salt: int = 0) -> str:
    """Readable ID like NK-7QX2-M9PA; deterministic per (event, email, idempotency key)."""
    number = int.from_bytes(hashlib.sha256(f"{event_id}|{email}|{key}|{salt}".encode()).digest()[:5], "big")
    chars = "".join(CROCKFORD[(number >> (5 * i)) & 31] for i in range(8))
    return f"NK-{chars[:4]}-{chars[4:]}"


def new_check_in_token() -> str:
    return secrets.token_urlsafe(24)


def release_ops(reg: dict) -> list:
    """[(counter_key, amount)] to give back when a seat-holding registration ends."""
    event_id = reg["eventId"]
    ops = [(f"EVENT#{event_id}", int(reg.get("seats", 0)))]
    ops += [(f"TICKET#{event_id}#{l['ticketTypeId']}", int(l["qty"])) for l in reg.get("tickets", [])]
    return ops


def _tell(kind, reg, event):
    if not notify:
        return
    try:
        notify(kind, reg, event)
    except Exception:  # an email failure must never undo a registration
        logger.exception("notification %s failed for %s", kind, reg.get("registrationId"))


def public_registration(reg: dict) -> dict:
    """What the registrant themselves may see."""
    hidden = {"providerPaymentId"}
    return {k: v for k, v in reg.items() if k not in hidden}


def member_context(member: Optional[dict]) -> dict:
    """Server-side facts that fill the system fields of the form."""
    if not member or not member.get("loggedIn"):
        return {"membershipStatus": "none"}
    summary = member.get("summary") or {}
    status = event_model.membership_status({**summary, "latestTo": summary.get("latestTo", "")})
    ctx = {"email": member["email"], "memberId": member.get("memberId", ""), "membershipStatus": status}
    if status != "none":
        ctx["membershipType"] = "NPO member"
        ctx["membershipExpiry"] = summary.get("latestTo", "")
    return ctx


def check_registrable(event: dict, member: Optional[dict], now) -> None:
    window = event_pricing.registration_window(event, now)
    if window == event_pricing.WINDOW_NOT_OPEN:
        raise RegError(409, "Registration has not opened yet", code="not_open")
    if window == event_pricing.WINDOW_CLOSED:
        raise RegError(409, "Registration is closed", code="closed")
    decision = event_pricing.access_decision(event, member)
    if not decision["allowed"]:
        messages = {
            "login_required": "Please sign in to register for this event",
            "members_only": "This event is for members with an active NPO membership",
            "renewal_required": "Please renew your NPO membership to register for this event",
        }
        raise RegError(403, messages[decision["reason"]], code=decision["reason"])


def choose_payment_method(total: int, requested, methods: dict) -> str:
    if total == 0:
        return "none"
    if requested not in METHODS or requested not in methods:
        raise RegError(400, "Choose one of the available payment methods", {"paymentMethod": "Choose a payment method"})
    return requested


def hold_until(method: str, now) -> str:
    return (now + (HOLD_CARD if method == "square" else HOLD_MANUAL)).isoformat(timespec="seconds")


# ── create ────────────────────────────────────────────────────────────────────

def register(*, event, tickets, form, body, member, methods, now, redirect_base="") -> tuple:
    """Create a registration. Returns (registration, created) — created is False for an idempotent replay."""
    check_registrable(event, member, now)
    eligible = event_pricing.is_member_price_eligible(member)
    lines, total, errors = event_pricing.quote(tickets, body.get("tickets"), eligible, now)
    if errors:
        raise RegError(400, "Please check your tickets", errors)
    clean, answer_errors = form_engine.validate_submission(form, body.get("answers"), member_context(member))
    if answer_errors:
        raise RegError(400, "Please check the highlighted answers", answer_errors)
    method = choose_payment_method(total, body.get("paymentMethod"), methods)

    email = clean["email"]
    if total > 0:
        unpaid = [r for r in store.list_by_email(email) if r["eventId"] == event["eventId"] and r["status"] == "pending_payment"]
        if len(unpaid) >= MAX_UNPAID_PER_EMAIL:
            raise RegError(429, "You already have unpaid registrations for this event. Please pay for them or cancel them first.",
                           code="too_many_unpaid")
    key = str(body.get("idempotencyKey") or secrets.token_hex(8))[:64]
    lines_limited = event_pricing.attach_limits(lines, tickets)
    ops = event_pricing.seat_ops(event, lines_limited)
    seats = sum(l["qty"] for l in lines)
    stamp = now.isoformat(timespec="seconds")

    for salt in range(4):
        reg_id = make_registration_id(event["eventId"], email, key, salt)
        reg = {
            "registrationId": reg_id, "eventId": event["eventId"], "eventTitle": event["title"],
            "eventSlug": event["slug"], "email": email, "fullName": clean.get("fullName", ""),
            "phone": clean.get("phone", ""), "answers": clean, "formId": form["formId"],
            "formVersion": int(form["version"]), "formHash": form["schemaHash"],
            "tickets": [{k: v for k, v in l.items() if k != "limit"} for l in lines_limited],
            "seats": seats, "totalJpy": total, "paymentMethod": method,
            "paymentStatus": "pending" if total else "none", "status": "confirmed",
            "checkInToken": new_check_in_token(), "revision": 1, "source": "native",
            "consentedAt": stamp, "createdAt": stamp, "updatedAt": stamp,
        }
        if member and member.get("loggedIn"):
            reg["memberEmail"] = member["email"]
            if member.get("memberId"):
                reg["memberId"] = member["memberId"]
        if total:
            reg["status"] = "pending_payment"
            reg["holdUntil"] = hold_until(method, now)
        try:
            try:
                store.reserve_and_create(reg, ops)
            except CapacityFull:
                if not event.get("waitlistEnabled"):
                    raise
                reg.update({"status": "waitlisted", "paymentMethod": "none", "paymentStatus": "none"})
                reg.pop("holdUntil", None)
                reg["waitlistedFor"] = method
                store.create_waitlisted(reg)
            break
        except AlreadyExists:
            existing = store.get_registration(reg_id)
            if existing and existing.get("email") == email and existing.get("eventId") == event["eventId"]:
                return existing, False          # same request sent twice
            continue                              # an ID collision with someone else: try the next salt
        except CapacityFull as full:
            raise RegError(409, "Sorry, this event is full" if full.scope == "event"
                           else "Sorry, that ticket type is sold out", code="sold_out")
    else:
        raise RegError(500, "Could not allocate a registration ID, please try again")

    if reg["status"] == "pending_payment" and method == "square":
        reg = start_square_checkout(reg, event, now, redirect_base)
    _tell({"confirmed": "confirmed", "pending_payment": "pending_payment", "waitlisted": "waitlisted"}[reg["status"]], reg, event)
    return reg, True


def start_square_checkout(reg: dict, event: dict, now, redirect_base: str) -> dict:
    """Create the hosted card checkout for a seat-holding registration; undo the booking if that fails."""
    try:
        checkout = payments.create_checkout(
            reference_id=reg["registrationId"], amount_yen=reg["totalJpy"],
            description=f"{event['title']} ({reg['registrationId']})",
            redirect_url=f"{redirect_base}/events/{event['slug']}/registered?reg={reg['registrationId']}&token={reg['checkInToken']}"
            if redirect_base else "")
    except Exception:
        logger.exception("Square checkout failed for %s", reg["registrationId"])
        try:
            cancel(reg, by="system", reason="payment_setup_failed", now=now, event=event, quiet=True)
        except StateConflict:
            pass
        raise RegError(502, "We couldn't start the card payment. Please try again.", code="payment_unavailable")
    updates = {"providerPaymentId": checkout["providerPaymentId"], "checkoutUrl": checkout["checkoutUrl"]}
    return store.update_registration(reg["registrationId"], updates, expect_status="pending_payment")


def begin_payment(reg: dict, event: dict, method: str, methods: dict, now, redirect_base: str = "") -> dict:
    """(Re)start payment for a registration that is waiting for it (after promotion, or a failed card attempt)."""
    if reg["status"] != "pending_payment":
        raise RegError(409, "This registration is not waiting for payment")
    if method not in METHODS or method not in methods:
        raise RegError(400, "Choose one of the available payment methods", {"paymentMethod": "Choose a payment method"})
    updates = {"paymentMethod": method, "holdUntil": hold_until(method, now)}
    reg = store.update_registration(reg["registrationId"], updates, expect_status="pending_payment")
    if method == "square":
        reg = start_square_checkout(reg, event, now, redirect_base)
    return reg


# ── cancel / expire / pay / promote ───────────────────────────────────────────

def cancel(reg: dict, *, by: str, reason: str, now, event: Optional[dict] = None, quiet: bool = False) -> dict:
    """Cancel a registration and give its seats back, atomically. Paid ones become refund_pending."""
    status = reg["status"]
    if status not in HOLDING + ("waitlisted",):
        raise StateConflict()
    stamp = now.isoformat(timespec="seconds")
    payment = "refund_pending" if reg.get("paymentStatus") == "paid" else "none"
    updates = {"status": "cancelled", "cancelledAt": stamp, "cancelledBy": by, "cancelReason": reason,
               "paymentStatus": payment, "updatedAt": stamp}
    ops = release_ops(reg) if status in HOLDING else []
    updated = store.release(reg["registrationId"], status, updates, ops, remove=["holdUntil"])
    if reg.get("providerPaymentId") and payment == "none" and payments and hasattr(payments, "cancel_order"):
        try:
            payments.cancel_order(reg["providerPaymentId"])
        except Exception:
            logger.warning("could not cancel Square order for %s", reg["registrationId"])
    if not quiet and event is not None:
        _tell("expired" if reason == "hold_expired" else "cancelled", updated, event)
    return updated


def expire_holds(now, events_by_id: dict) -> int:
    """Cancel unpaid registrations whose hold ran out. Returns how many were released."""
    released = 0
    for reg in store.list_expired_holds(now.isoformat(timespec="seconds")):
        try:
            cancel(reg, by="system", reason="hold_expired", now=now, event=events_by_id.get(reg["eventId"]))
            released += 1
        except StateConflict:
            continue            # paid or cancelled a moment ago
    return released


def payment_amount_ok(reg: dict, paid) -> bool:
    """Does the amount the provider says was paid equal what we asked for? (Unknown amounts are not blocked.)"""
    if paid is None:
        return True
    try:
        amount = Decimal(str(paid))
        return amount == amount.to_integral_value() and int(amount) == int(reg["totalJpy"])
    except (TypeError, ValueError, ArithmeticError):
        return False


def flag_payment_mismatch(reg: dict, paid, now) -> dict:
    """A payment arrived for the wrong amount: keep the registration unconfirmed and mark it for an organiser."""
    logger.error("payment amount mismatch for %s: expected %s, provider reported %s", reg["registrationId"], reg["totalJpy"], paid)
    try:
        return store.update_registration(reg["registrationId"], {
            "paymentMismatch": {"expectedJpy": int(reg["totalJpy"]), "reportedJpy": paid, "at": now.isoformat(timespec="seconds")}},
            expect_status="pending_payment")
    except StateConflict:
        return reg


def mark_paid(reg: dict, *, via: str, reference: str, now, event: Optional[dict] = None, actor: str = "") -> dict:
    """Payment received (Square webhook, or an admin for bank/PayPay). Idempotent."""
    stamp = now.isoformat(timespec="seconds")
    paid = {"paymentStatus": "paid", "paidAt": stamp, "paidVia": via, "paymentReference": reference,
            "updatedAt": stamp}
    if actor:
        paid["markedPaidBy"] = actor
    if reg["status"] == "confirmed" and reg.get("paymentStatus") == "paid":
        return reg
    if reg["status"] == "pending_payment":
        try:
            updated = store.update_registration(reg["registrationId"], {**paid, "status": "confirmed"},
                                                expect_status="pending_payment", remove=["holdUntil"])
        except StateConflict:
            return mark_paid(store.get_registration(reg["registrationId"]), via=via, reference=reference, now=now,
                             event=event, actor=actor)
        if event:
            _tell("confirmed", updated, event)
        return updated
    if reg["status"] == "cancelled" and reg.get("cancelReason") == "hold_expired":
        # Paid after the hold lapsed: take the seats back if they are still free, else flag a refund.
        ops = [(k, n, None) for k, n in release_ops(reg)]
        if event:
            ops = event_pricing.seat_ops(event, event_pricing.attach_limits(reg["tickets"], store.get_tickets(reg["eventId"])))
        try:
            return store.reserve_existing(reg["registrationId"], "cancelled", {**paid, "status": "confirmed"}, ops,
                                          remove=["holdUntil", "cancelledAt", "cancelledBy", "cancelReason"])
        except (CapacityFull, StateConflict):
            return store.update_registration(reg["registrationId"], {"paymentStatus": "refund_pending", "paidAt": stamp,
                                                                     "paymentReference": reference, "updatedAt": stamp})
    return store.update_registration(reg["registrationId"], {"paymentStatus": "refund_pending", "paidAt": stamp,
                                                             "paymentReference": reference, "updatedAt": stamp})


def promote(reg: dict, event: dict, tickets: list, *, now, actor: str) -> dict:
    """Move a waitlisted registration into the event if seats are free (admin action)."""
    if reg["status"] != "waitlisted":
        raise RegError(409, "Only waitlisted registrations can be promoted")
    ops = event_pricing.seat_ops(event, event_pricing.attach_limits(reg["tickets"], tickets))
    stamp = now.isoformat(timespec="seconds")
    paid_event = reg["totalJpy"] > 0
    updates = {"status": "pending_payment" if paid_event else "confirmed", "promotedAt": stamp, "promotedBy": actor,
               "paymentStatus": "pending" if paid_event else "none", "updatedAt": stamp}
    if paid_event:
        updates["holdUntil"] = hold_until("bank", now)
        updates["paymentMethod"] = reg.get("waitlistedFor") or "bank"
    try:
        updated = store.reserve_existing(reg["registrationId"], "waitlisted", updates, ops, remove=[])
    except CapacityFull:
        raise RegError(409, "There are not enough free seats for this registration yet", code="sold_out")
    except StateConflict:
        raise RegError(409, "This registration changed in the meantime. Reload and try again.")
    _tell("promoted", updated, event)
    return updated


# ── member self-service ───────────────────────────────────────────────────────

def _owned_by(reg: dict, member: Optional[dict]) -> bool:
    return bool(member and member.get("loggedIn") and reg.get("memberEmail") == member["email"])


def self_service_check(reg: Optional[dict], event: Optional[dict], member: Optional[dict], now) -> None:
    if not reg or not _owned_by(reg, member):
        raise RegError(404, "Registration not found")           # same answer for "not yours" and "missing"
    if reg["status"] not in HOLDING + ("waitlisted",):
        raise RegError(409, "This registration can no longer be changed")
    if event is None or event_pricing.registration_window(event, now) != event_pricing.WINDOW_OPEN:
        raise RegError(409, "Registration has closed, so this can only be changed by an organiser", code="closed")


def member_cancel(reg, event, member, now) -> dict:
    self_service_check(reg, event, member, now)
    try:
        return cancel(reg, by=member["email"], reason="member_cancelled", now=now, event=event)
    except StateConflict:
        raise RegError(409, "This registration changed in the meantime. Reload and try again.")


def member_edit(reg, event, form_version, answers, member, now) -> dict:
    """Edit answers only (tickets and price are fixed). Validated against the form version the member registered with."""
    self_service_check(reg, event, member, now)
    context = {k: reg["answers"][k] for k in ("membershipId", "membershipStatus", "membershipType", "membershipExpiry")
               if k in reg["answers"]}
    context.update({"email": reg["email"], "memberId": reg.get("memberId", "")})
    clean, errors = form_engine.validate_submission(form_version, answers, context)
    if errors:
        raise RegError(400, "Please check the highlighted answers", errors)
    updates = {"answers": clean, "fullName": clean.get("fullName", reg.get("fullName", "")),
               "phone": clean.get("phone", ""), "updatedAt": now.isoformat(timespec="seconds")}
    try:
        return store.update_registration(reg["registrationId"], updates, expect_revision=reg["revision"],
                                         expect_status=reg["status"], bump=True)
    except StateConflict:
        raise RegError(409, "This registration changed in the meantime. Reload and try again.")
