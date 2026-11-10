"""
Admin-side registration management for the event platform: table queries, dashboard numbers, manual
add, admin edit, refund tracking, QR check-in and bulk e-mail audiences. Storage goes through
event_registrations.store (the Lambda wires in event_reg_store, the tests a fake).
"""

import time
from typing import Optional

import event_addons
import event_exports
import event_model
import event_pricing
import event_registrations as logic
import form_engine

PAGE_SIZE_MAX = 200
MAX_BULK_RECIPIENTS = 500
PAID_VIA = ("cash", "bank", "paypay", "square", "other")
AUDIENCES = {
    "confirmed": lambda r: r["status"] == "confirmed",
    "pending": lambda r: r["status"] == "pending_payment",
    "waitlisted": lambda r: r["status"] == "waitlisted",
    "active": lambda r: r["status"] in ("confirmed", "pending_payment", "waitlisted"),
    "checked_in": lambda r: r["status"] == "confirmed" and bool(r.get("checkedInAt")),
    "not_checked_in": lambda r: r["status"] == "confirmed" and not r.get("checkedInAt"),
    "cancelled": lambda r: r["status"] == "cancelled",
}
HIDDEN_FIELDS = ("checkInToken", "providerPaymentId")


def _int(value, default, low, high):
    try:
        return max(low, min(high, int(value)))
    except (TypeError, ValueError):
        return default


def admin_view(reg: dict) -> dict:
    """A registration as admins see it (no registrant credentials)."""
    return {k: v for k, v in reg.items() if k not in HIDDEN_FIELDS}


# ── table ─────────────────────────────────────────────────────────────────────

def _search_text(reg: dict) -> str:
    parts = [reg.get("registrationId", ""), reg.get("fullName", ""), reg.get("email", ""), reg.get("phone", ""),
             event_exports.tickets_summary(reg), event_addons.describe(reg), reg.get("adminNote", "")]
    parts += [event_exports._flatten(v) for v in reg.get("answers", {}).values()]
    return " ".join(str(p) for p in parts).lower()


def _answer_matches(reg: dict, key: str, wanted: str) -> bool:
    value = reg.get("answers", {}).get(key)
    options = value if isinstance(value, list) else [value]
    return any(str(event_exports._flatten(o)).lower() == wanted.lower() for o in options if o is not None)


def filter_registrations(regs: list, params: dict) -> list:
    """Search, filter and sort. params are query-string values:
    q, status (comma list), payment, ticket, checkedIn=yes|no, f.<fieldKey>=value, sort, dir."""
    rows = list(regs)
    q = str(params.get("q", "")).strip().lower()
    if q:
        rows = [r for r in rows if all(term in _search_text(r) for term in q.split())]
    if params.get("status"):
        allowed = set(params["status"].split(","))
        rows = [r for r in rows if r["status"] in allowed]
    if params.get("payment"):
        allowed = set(params["payment"].split(","))
        rows = [r for r in rows if r.get("paymentStatus") in allowed]
    if params.get("ticket"):
        rows = [r for r in rows if any(l["ticketTypeId"] == params["ticket"] for l in r.get("tickets", []))]
    if params.get("checkedIn") in ("yes", "no"):
        want = params["checkedIn"] == "yes"
        rows = [r for r in rows if bool(r.get("checkedInAt")) == want]
    for key, value in params.items():
        if key.startswith("f.") and value != "":
            rows = [r for r in rows if _answer_matches(r, key[2:], value)]
    sort = params.get("sort") or "createdAt"
    descending = params.get("dir", "desc" if sort == "createdAt" else "asc") == "desc"
    allowed_sorts = {c["id"] for c in event_exports.FIXED_COLUMNS} | {"fullName", "email"}
    if sort in allowed_sorts or sort.startswith("a."):
        column = "a.fullName" if sort == "fullName" else "a.email" if sort == "email" else sort
        rows.sort(key=lambda r: event_exports.sort_value(r, column), reverse=descending)
    return rows


def query_registrations(regs: list, params: dict) -> dict:
    """filter_registrations plus pagination (page, pageSize)."""
    rows = filter_registrations(regs, params)
    total = len(rows)
    size = _int(params.get("pageSize"), 50, 1, PAGE_SIZE_MAX)
    pages = max(1, -(-total // size))
    page = _int(params.get("page"), 1, 1, pages)
    return {"rows": [admin_view(r) for r in rows[(page - 1) * size:page * size]], "total": total, "page": page,
            "pages": pages, "pageSize": size}


# ── dashboard ─────────────────────────────────────────────────────────────────

def dashboard(event: dict, tickets: list, regs: list, counters: dict) -> dict:
    counts = {s: 0 for s in event_model.REGISTRATION_STATUSES}
    seats = {s: 0 for s in event_model.REGISTRATION_STATUSES}
    money = {"paidJpy": 0, "pendingJpy": 0, "refundPendingJpy": 0, "refundedJpy": 0}
    breakdown = {t["ticketTypeId"]: {"ticketTypeId": t["ticketTypeId"], "name": t["name"], "confirmed": 0, "pending": 0,
                                     "waitlisted": 0, "revenueJpy": 0} for t in tickets}
    checked_in = 0
    for reg in regs:
        status = reg["status"]
        counts[status] = counts.get(status, 0) + 1
        seats[status] = seats.get(status, 0) + int(reg.get("seats", 0))
        total = int(reg.get("totalJpy") or 0)
        payment = reg.get("paymentStatus")
        if payment == "paid":
            money["paidJpy"] += total
        elif payment in ("refund_pending", "refunding"):
            money["refundPendingJpy"] += total
        elif payment == "refunded":
            money["refundedJpy"] += total
        if status == "pending_payment":
            money["pendingJpy"] += total
        if reg.get("checkedInAt") and status == "confirmed":
            checked_in += int(reg.get("seats", 0))
        bucket = {"confirmed": "confirmed", "pending_payment": "pending", "waitlisted": "waitlisted"}.get(status)
        for line in reg.get("tickets", []):
            row = breakdown.setdefault(line["ticketTypeId"], {"ticketTypeId": line["ticketTypeId"], "name": line["name"],
                                                              "confirmed": 0, "pending": 0, "waitlisted": 0, "revenueJpy": 0})
            if bucket:
                row[bucket] += int(line["qty"])
            if payment == "paid":
                row["revenueJpy"] += int(line.get("subtotalJpy") or 0)
    capacity = int(event["capacity"]) if event.get("capacity") else None
    used = int(counters.get(f"EVENT#{event['eventId']}", 0))
    return {
        "registrations": counts, "seats": seats, "total": len(regs),
        "capacity": {"limit": capacity, "used": used, "remaining": None if capacity is None else max(0, capacity - used),
                     "percent": None if not capacity else round(100 * used / capacity)},
        "revenue": {**money, "netJpy": money["paidJpy"]},
        "tickets": list(breakdown.values()),
        "checkedInSeats": checked_in, "confirmedSeats": seats["confirmed"],
        "needsAttention": {"refundPending": sum(1 for r in regs if r.get("paymentStatus") in ("refund_pending", "refunding")),
                           "awaitingPayment": counts["pending_payment"], "waitlisted": counts["waitlisted"]},
    }


# ── manual add / edit ─────────────────────────────────────────────────────────

def manual_add(*, event, tickets, form, body, account, now, actor) -> tuple:
    """Add a registration on someone's behalf (phone/paper sign-up, comp tickets, corrections).

    Skips the registration window and access rules, but not validation: tickets, form answers and capacity are
    still checked (capacity only when `ignoreCapacity` is off). Returns (registration, created).
    """
    lines, quoted, errors = event_pricing.quote(tickets, body.get("tickets"), now)
    if errors:
        raise logic.RegError(400, "Please check the tickets", errors)
    total = quoted
    override = body.get("totalJpy")
    if override not in (None, ""):
        if not isinstance(override, int) or isinstance(override, bool) or override < 0:
            raise logic.RegError(400, "The total must be whole yen, 0 or more", {"totalJpy": "Invalid amount"})
        total = override
    clean, answer_errors = form_engine.validate_submission(form, body.get("answers"), logic.account_context(account))
    if answer_errors:
        raise logic.RegError(400, "Please check the highlighted answers", answer_errors)

    payment = body.get("paymentStatus", "pending")
    if total == 0:
        payment = "none"
    elif payment not in ("paid", "pending"):
        raise logic.RegError(400, "Payment must be paid or pending", {"paymentStatus": "Invalid"})
    via = body.get("paidVia", "cash") if payment == "paid" else None
    if via is not None and via not in PAID_VIA:
        raise logic.RegError(400, "Unknown payment method", {"paidVia": "Invalid"})
    method = body.get("paymentMethod", "bank") if payment == "pending" else (via or "none")
    if payment == "pending" and method not in ("bank", "paypay"):
        raise logic.RegError(400, "Pending payments are collected by bank transfer or PayPay", {"paymentMethod": "Invalid"})
    note = str(body.get("adminNote", "")).strip()[:1000]
    if total != quoted and not note:
        raise logic.RegError(400, "Please add a note explaining the changed total", {"adminNote": "Required when the total is changed"})

    email = clean["email"]
    key = str(body.get("idempotencyKey") or time.time_ns())[:64]
    stamp = now.isoformat(timespec="seconds")
    ops = event_pricing.seat_ops(event, event_pricing.attach_limits(lines, tickets))
    if body.get("ignoreCapacity"):
        ops = [(k, n, None) for k, n, _ in ops]
    for salt in range(4):
        reg_id = logic.make_registration_id(event["eventId"], email, f"admin-{key}", salt)
        reg = {
            "registrationId": reg_id, "eventId": event["eventId"], "eventTitle": event["title"], "eventSlug": event["slug"],
            "email": email, "fullName": clean.get("fullName", ""), "phone": clean.get("phone", ""), "answers": clean,
            "formId": form["formId"], "formVersion": int(form["version"]), "formHash": form["schemaHash"],
            "tickets": [{k: v for k, v in l.items() if k != "limit"} for l in lines], "seats": sum(l["qty"] for l in lines),
            "totalJpy": total, "paymentMethod": method, "paymentStatus": payment, "status": "confirmed",
            "checkInToken": logic.new_check_in_token(), "revision": 1, "addedByAdmin": True,
            "createdBy": actor, "consentedAt": stamp, "consentRecordedBy": actor, "createdAt": stamp, "updatedAt": stamp,
        }
        if note:
            reg["adminNote"] = note
        if account:
            reg["accountEmail"] = account["email"]
        if payment == "paid":
            reg.update({"paidAt": stamp, "paidVia": via, "markedPaidBy": actor})
        elif payment == "pending":
            reg.update({"status": "pending_payment", "holdUntil": logic.hold_until(method, now)})
        try:
            if body.get("waitlist"):
                reg.update({"status": "waitlisted", "paymentStatus": "none", "paymentMethod": "none", "waitlistedFor": method})
                for k in ("holdUntil", "paidAt", "paidVia", "markedPaidBy"):
                    reg.pop(k, None)
                logic.store.create_waitlisted(reg)
            else:
                logic.store.reserve_and_create(reg, ops)
            break
        except logic.AlreadyExists:
            existing = logic.store.get_registration(reg_id)
            if existing and existing.get("email") == email and existing.get("eventId") == event["eventId"]:
                return existing, False
        except logic.CapacityFull as full:
            raise logic.RegError(409, "There are not enough free seats. Tick 'ignore capacity' or add to the waiting list.",
                                 code="sold_out", fields={"scope": full.scope})
    else:
        raise logic.RegError(500, "Could not allocate a registration ID, please try again")
    if body.get("notify", True):
        logic._tell({"confirmed": "confirmed", "pending_payment": "pending_payment", "waitlisted": "waitlisted"}[reg["status"]], reg, event)
    return reg, True


def admin_edit(reg: dict, form_version: dict, body: dict, actor: str, now) -> dict:
    """Correct a registration's answers and note. Tickets and totals are not editable (cancel and add a new one)."""
    if not isinstance(body.get("revision"), int) or isinstance(body.get("revision"), bool):
        raise logic.RegError(400, "revision is required (reload the registration and try again)")
    updates = {"updatedAt": now.isoformat(timespec="seconds"), "updatedBy": actor}
    if "answers" in body:
        context = {"email": reg["email"]} if reg.get("accountEmail") else {}
        clean, errors = form_engine.validate_submission(form_version, body["answers"], context)
        if errors:
            raise logic.RegError(400, "Please check the highlighted answers", errors)
        updates.update({"answers": clean, "fullName": clean.get("fullName", reg.get("fullName", "")),
                        "phone": clean.get("phone", ""), "email": clean["email"]})
    if "adminNote" in body:
        updates["adminNote"] = str(body["adminNote"]).strip()[:1000]
    try:
        return logic.store.update_registration(reg["registrationId"], updates, expect_revision=body["revision"], bump=True)
    except logic.StateConflict:
        raise logic.RegError(409, "This registration was changed by someone else. Reload it and try again.")


# ── refunds ───────────────────────────────────────────────────────────────────

def refund(reg: dict, body: dict, actor: str, now, payments) -> dict:
    """Record a refund of a cancelled, paid registration (full amount). mode "square" also refunds the card payment.

    The registration is first *claimed* (paymentStatus "refunding", conditional on the revision we read) and only
    then is Square called, so two admins or a double click can never refund the same payment twice.
    """
    if reg.get("paymentStatus") != "refund_pending":
        raise logic.RegError(409, "Only registrations with a pending refund can be refunded")
    mode = body.get("mode", "manual")
    if mode not in ("square", "manual"):
        raise logic.RegError(400, "mode must be square or manual", {"mode": "Invalid"})
    if mode == "square" and (reg.get("paidVia") != "square" or not reg.get("paymentReference")):
        raise logic.RegError(409, "This registration was not paid by card, so it cannot be refunded through Square")
    note = str(body.get("note", "")).strip()[:500]
    stamp = now.isoformat(timespec="seconds")
    try:
        claimed = logic.store.update_registration(reg["registrationId"], {"paymentStatus": "refunding", "updatedAt": stamp},
                                                  expect_revision=reg["revision"], bump=True)
    except logic.StateConflict:
        raise logic.RegError(409, "This registration changed in the meantime. Reload it and check whether it was already refunded.")
    updates = {"paymentStatus": "refunded", "refundedAt": stamp, "refundedBy": actor, "refundMode": mode, "updatedAt": stamp}
    if note:
        updates["refundNote"] = note
    if mode == "square":
        try:
            result = payments.create_refund(payment_id=reg["paymentReference"], amount_yen=int(reg["totalJpy"]),
                                            reason=f"Event registration {reg['registrationId']}")
        except Exception as exc:
            logic.store.update_registration(reg["registrationId"], {"paymentStatus": "refund_pending", "updatedAt": stamp},
                                            expect_revision=claimed["revision"], bump=True)
            raise logic.RegError(502, f"Square could not process the refund: {str(exc)[:200]}")
        updates.update({"refundReference": result["refundId"], "refundProviderStatus": result.get("status", "")})
    return logic.store.update_registration(reg["registrationId"], updates, expect_revision=claimed["revision"], bump=True)


# ── check-in ──────────────────────────────────────────────────────────────────

def parse_scan(text: str) -> Optional[tuple]:
    """(registrationId, token) from a QR payload "WMFEV1.<id>.<token>", or (id, None) for a typed registration ID."""
    text = str(text or "").strip()
    if text.startswith("WMFEV1."):
        parts = text.split(".", 2)
        return (parts[1], parts[2]) if len(parts) == 3 and parts[1] and parts[2] else None
    return (text.upper(), None) if text else None


def _summary(reg: dict) -> dict:
    return {"registrationId": reg["registrationId"], "fullName": reg.get("fullName", ""), "email": reg.get("email", ""),
            "seats": reg.get("seats", 0), "tickets": [{"name": l["name"], "qty": l["qty"]} for l in reg.get("tickets", [])],
            "status": reg["status"], "paymentStatus": reg.get("paymentStatus"), "totalJpy": reg.get("totalJpy", 0),
            "checkedInAt": reg.get("checkedInAt"), "adminNote": reg.get("adminNote", "")}


def check_in(reg: Optional[dict], token: Optional[str], event_id: str, actor: str, now, undo: bool = False) -> dict:
    """Check someone in (or undo). Always returns a result code the scanner screen can show:
    ok | already | not_found | wrong_event | cancelled | waitlisted | payment_due | not_checked_in (undo of nothing)."""
    import hmac
    if not reg:
        return {"result": "not_found"}
    if token is not None and not hmac.compare_digest(str(reg.get("checkInToken", "")), str(token)):
        return {"result": "not_found"}                    # a forged or stale code reveals nothing
    if reg["eventId"] != event_id:
        return {"result": "wrong_event", "registration": {"fullName": reg.get("fullName", ""), "eventTitle": reg.get("eventTitle", "")}}
    summary = _summary(reg)
    if undo:
        if not reg.get("checkedInAt"):
            return {"result": "not_checked_in", "registration": summary}
        updated = logic.store.update_registration(reg["registrationId"], {"updatedAt": now.isoformat(timespec="seconds")},
                                                  remove=["checkedInAt", "checkedInBy"])
        return {"result": "undone", "registration": _summary(updated)}
    if reg["status"] == "cancelled":
        return {"result": "cancelled", "registration": summary}
    if reg["status"] == "waitlisted":
        return {"result": "waitlisted", "registration": summary}
    if reg["status"] == "pending_payment":
        return {"result": "payment_due", "registration": summary}
    if reg.get("checkedInAt"):
        return {"result": "already", "registration": summary}
    stamp = now.isoformat(timespec="seconds")
    try:
        updated = logic.store.update_registration(reg["registrationId"], {"checkedInAt": stamp, "checkedInBy": actor, "updatedAt": stamp},
                                                  expect_status="confirmed", expect_absent="checkedInAt")
    except logic.StateConflict:
        latest = logic.store.get_registration(reg["registrationId"]) or reg      # a second scanner got there first
        return {"result": "already" if latest.get("checkedInAt") else latest["status"], "registration": _summary(latest)}
    return {"result": "ok", "registration": _summary(updated)}


# ── bulk e-mail ───────────────────────────────────────────────────────────────

def bulk_recipients(regs: list, audience: str) -> list:
    """One registration per e-mail address (the earliest), for the chosen audience."""
    if audience not in AUDIENCES:
        raise logic.RegError(400, "Choose who should receive the message", {"audience": "Invalid"})
    chosen = sorted((r for r in regs if AUDIENCES[audience](r)), key=lambda r: r.get("createdAt", ""))
    seen, out = set(), []
    for reg in chosen:
        email = reg.get("email", "").lower()
        if email and email not in seen:
            seen.add(email)
            out.append(reg)
    return out


def validate_message(subject: str, body: str) -> dict:
    errors = {}
    if not str(subject).strip() or len(subject) > 200:
        errors["subject"] = "Subject is required (up to 200 characters)"
    if not str(body).strip() or len(body) > 10000:
        errors["body"] = "Message is required (up to 10000 characters)"
    problems = event_model.validate_email_templates({"confirmation": {"subject": subject, "body": body}})
    if "confirmation" in problems:
        errors["body"] = problems["confirmation"]
    return errors


def send_bulk(event: dict, recipients: list, subject: str, body: str, send, pause: float = 0.07) -> dict:
    """Send one personalised message per recipient. `send(to, subject, text)` raises on failure."""
    import event_mailer
    sent, failed = 0, []
    for reg in recipients:
        values = event_mailer.placeholder_values(reg, event)
        try:
            send(reg["email"], event_model.render_template(subject, values), event_model.render_template(body, values))
            sent += 1
        except Exception:
            failed.append(reg["email"])
        if pause:
            time.sleep(pause)
    return {"sent": sent, "failed": failed}


# ── personal data (APPI) ──────────────────────────────────────────────────────

def file_refs(answers: dict) -> list:
    """Every uploaded-file reference in an answers dict (including inside repeatable groups)."""
    out: list = []

    def walk(value):
        if isinstance(value, dict):
            if "fileId" in value:
                out.append(value)
            else:
                for v in value.values():
                    walk(v)
        elif isinstance(value, list):
            for v in value:
                walk(v)

    walk(answers)
    return out


def anonymize(reg: dict, actor: str, now, *, event_over: bool, delete_file) -> dict:
    """Erase a registrant's personal data (APPI deletion request), keeping what accounting needs.

    Kept: ticket lines, totals, payment and refund facts, status, timestamps. Erased: name, email, phone, every
    answer, the account link, uploaded files, and the registrant's access link.
    Irreversible. Only for cancelled registrations or events that have ended.
    """
    if reg.get("anonymizedAt"):
        raise logic.RegError(409, "This registration has already been anonymised")
    if reg["status"] != "cancelled" and not event_over:
        raise logic.RegError(409, "Cancel the registration first, or wait until the event has ended")
    if reg.get("paymentStatus") in ("refund_pending", "refunding"):
        raise logic.RegError(409, "Record the refund first; its details are needed to complete it")
    for ref in file_refs(reg.get("answers", {})):
        delete_file(reg["eventId"], ref["fileId"])
    stamp = now.isoformat(timespec="seconds")
    updates = {"fullName": "Erased", "email": "erased", "phone": "", "answers": {}, "checkInToken": logic.new_check_in_token(),
               "anonymizedAt": stamp, "anonymizedBy": actor, "updatedAt": stamp}
    try:
        return logic.store.update_registration(reg["registrationId"], updates, expect_revision=reg["revision"], bump=True,
                                               remove=["accountEmail", "accountSub", "addons", "adminNote", "refundNote", "cancelReason"])
    except logic.StateConflict:
        raise logic.RegError(409, "This registration changed in the meantime. Reload it and try again.")
