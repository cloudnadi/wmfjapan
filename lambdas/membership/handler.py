"""
Lambda: NPO membership status for the public registration gate.

Route (API Gateway proxy):
  GET /membership/status   (Authorization: <reg token>)  -> membership record

The caller must present the HMAC access token minted by the auth_otp Lambda on
OTP verification. We recompute the signature with the shared OTP_TOKEN_SECRET,
confirm it hasn't expired, and only then look the caller's own email up — a
registrant can therefore only read their own status.

Membership is validated by date period, not a year string:
  1. Resolve email → memberid via the members master table (and read the
     npoMembershipActive flag).
  2. Query the npo_membership table by memberid for that member's transactions.
  3. A member is "valid now" when any transaction's [membDateFrom, membDateTo]
     range covers today.
The registration form uses the returned flags to decide whether to (a) apply
member pricing, (b) offer a "register as NPO member" join, or (c) offer a
renewal for the coming year.
"""

import base64
import hashlib
import hmac
import json
import logging
import os
import re
import time
from datetime import datetime, timedelta, timezone
from html import escape

import boto3
from boto3.dynamodb.conditions import Key
from botocore.exceptions import ClientError

import agm_logic
import member_emailer
import npo_membership
import npo_payment_logic as npo_pay
import square

# ── Clients ───────────────────────────────────────────────────────────────────
dynamodb = boto3.resource("dynamodb", region_name=os.environ["REGION"])

MEMBERSHIP_TABLE = os.environ["MEMBERSHIP_TABLE"]
MEMBERS_TABLE = os.environ.get("MEMBERS_TABLE", "")
SITE_CONFIG_TABLE = os.environ.get("SITE_CONFIG_TABLE", "")
SES_FROM_EMAIL = os.environ.get("SES_FROM_EMAIL", "")
ADMIN_EMAIL = os.environ.get("ADMIN_EMAIL", "")
TOKEN_SECRET = os.environ.get("OTP_TOKEN_SECRET", "")
FRONTEND_URL = os.environ.get("FRONTEND_URL", "").rstrip("/")
MEMBER_REGISTER_URL = os.environ.get("MEMBER_REGISTER_URL", "https://www.wmfjapan.com/en/member/register")
AGM_TABLE = os.environ.get("AGM_TABLE", "")
AGMS_TABLE = os.environ.get("AGMS_TABLE", "")
LOG_LEVEL = os.environ.get("LOG_LEVEL", "INFO").upper()

# The membership year an upsell/renewal enrolls into, and an optional pinned
# "today" for the validity check (mostly for testing).
RENEWAL_MEMBERSHIP_YEAR = os.environ.get("RENEWAL_MEMBERSHIP_YEAR", "2026-27")
MEMBERSHIP_AS_OF = os.environ.get("MEMBERSHIP_AS_OF", "")

logger = logging.getLogger(__name__)
logger.setLevel(LOG_LEVEL)

EMAIL_REGEX = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

# Admin-managed NPO payment settings live in the site-config table under this
# key as a JSON string (see manage_site_config). Methods the admin hasn't
# enabled are never offered or accepted.
NPO_CONFIG_KEY = "npo_membership_config"  # {"fee": int}
PAYMENT_CONFIG_KEY = "payment_methods_config"  # site-wide {"methods": {...}}
# "square" is an automatic hosted checkout (confirmed by webhook); "paypay" and
# "bank" are shown as a link/instructions and confirmed by an admin.
PAYMENT_METHOD_LABELS = {
    "square": "Credit / Debit Card",
    "paypay": "PayPay",
    "bank": "Bank Transfer",
}
DEFAULT_FEE = 1000
AGM_MAX_PER_GROUP = 20


# ── Helpers ───────────────────────────────────────────────────────────────────


def cors_headers(origin="*"):
    return {
        "Access-Control-Allow-Origin": origin,
        "Access-Control-Allow-Headers": "Content-Type,Authorization",
        "Access-Control-Allow-Methods": "OPTIONS,GET,POST",
        "Content-Type": "application/json",
    }


def respond(status, body, origin="*"):
    return {
        "statusCode": status,
        "headers": cors_headers(origin),
        "body": json.dumps(body, default=str),
    }


def _b64url_decode(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode(value + padding)


def verify_token(token):
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


def get_member(email):
    """Resolve email → members-table record (memberid, npoMembershipActive)."""
    if not MEMBERS_TABLE:
        logger.warning("MEMBERS_TABLE not configured; cannot resolve memberid.")
        return None
    try:
        return dynamodb.Table(MEMBERS_TABLE).get_item(Key={"email": email}).get("Item")
    except ClientError:
        logger.exception("Could not read members table for email=%s", email)
        return None


def get_transactions(memberid):
    """All npo_membership transactions for a memberid."""
    if not memberid:
        return []
    try:
        resp = dynamodb.Table(MEMBERSHIP_TABLE).query(
            KeyConditionExpression=Key("memberid").eq(str(memberid))
        )
        return resp.get("Items", [])
    except ClientError:
        logger.exception("Could not query membership for memberid=%s", memberid)
        return []


def load_npo_config():
    """NPO fee + the site-wide payment methods. Falls back to "nothing enabled"."""
    config = {"fee": DEFAULT_FEE, "methods": {}}

    def read(key):
        if not SITE_CONFIG_TABLE:
            return {}
        try:
            item = dynamodb.Table(SITE_CONFIG_TABLE).get_item(Key={"configKey": key}).get("Item")
            parsed = json.loads((item or {}).get("value") or "{}")
            return parsed if isinstance(parsed, dict) else {}
        except (ClientError, TypeError, ValueError):
            logger.exception("Could not read site config %s", key)
            return {}

    stored = read(NPO_CONFIG_KEY)
    try:
        fee = int(stored.get("fee", DEFAULT_FEE))
        config["fee"] = fee if fee >= 0 else DEFAULT_FEE
    except (TypeError, ValueError):
        pass
    payment = read(PAYMENT_CONFIG_KEY)
    stored_methods = payment.get("methods") if isinstance(payment.get("methods"), dict) else {}
    for key, label in PAYMENT_METHOD_LABELS.items():
        entry = stored_methods.get(key) if isinstance(stored_methods.get(key), dict) else {}
        if entry.get("enabled") is not True:
            continue
        if key == "square" and not square.is_configured():
            continue  # can't offer an online checkout we can't create
        url = str(entry.get("url", "")).strip()
        config["methods"][key] = {
            "key": key,
            "label": label,
            "url": "" if key == "square" else (url if url.startswith(("http://", "https://")) else ""),
            "instructions": str(entry.get("instructions", "")).strip(),
        }
    return config


def load_agm_config():
    """The AGM currently open for registration, or {"enabled": False}.

    AGMs live in the agms table (managed from the admin AGM tab). The one
    offered is the soonest that has registration switched on and hasn't
    happened yet (dates are Japan time).
    """
    if not (AGMS_TABLE and AGM_TABLE):
        return {"enabled": False}
    try:
        table = dynamodb.Table(AGMS_TABLE)
        items, kwargs = [], {}
        while True:
            resp = table.scan(**kwargs)
            items.extend(resp.get("Items", []))
            if "LastEvaluatedKey" not in resp:
                break
            kwargs["ExclusiveStartKey"] = resp["LastEvaluatedKey"]
    except ClientError:
        logger.exception("Could not read AGMs")
        return {"enabled": False}
    today = datetime.now(timezone(timedelta(hours=9))).date().isoformat()
    open_now = [
        a for a in items
        if a.get("enabled") is True and a.get("agmId") and (a.get("date") or "9999-12-31") >= today
    ]
    if not open_now:
        return {"enabled": False}
    open_now.sort(key=lambda a: a.get("date") or "9999-12-31")
    return {**open_now[0], "enabled": True}


AGM_PUBLIC_FIELDS = (
    "agmId", "title", "date", "startTime", "endTime", "venue", "venueJa",
    "access", "mapUrl", "remoteNote", "refreshmentsNote", "notes",
)


def public_agm(config):
    return {"enabled": True, **{k: config.get(k, "") for k in AGM_PUBLIC_FIELDS}}


def get_agm_registration(agm_id, email):
    if not (AGM_TABLE and agm_id):
        return None
    try:
        item = dynamodb.Table(AGM_TABLE).get_item(Key={"agmId": agm_id, "email": email}).get("Item")
    except ClientError:
        logger.exception("Could not read AGM registration for %s", email)
        return None
    if not item:
        return None
    fields = ("adults", *(key for key, _ in agm_logic.YOUNG_GROUPS), "attendance", "totalAttendees", "registeredAt", "updatedAt")
    registration = {k: item.get(k) for k in fields}
    registration["adultAttendees"] = [
        {k: a.get(k) for k in ("email", "name", "status", "registrant")} for a in item.get("adultAttendees") or []
    ]
    return registration


def parse_agm_registration(raw):
    """Validate an AGM registration request. Returns (clean dict, error message).

    Adults (18+) are named by email — the registrant is adult #1 and `adultEmails`
    lists everyone else — because only NPO members may attend. Teenagers,
    children and toddlers are just counted.
    """
    raw = raw if isinstance(raw, dict) else {}
    clean = {}
    for key, _label in agm_logic.YOUNG_GROUPS:
        try:
            value = int(raw.get(key) or 0)
        except (TypeError, ValueError):
            return None, "Attendee counts must be whole numbers."
        if not 0 <= value <= AGM_MAX_PER_GROUP:
            return None, f"Each attendee count must be between 0 and {AGM_MAX_PER_GROUP}."
        clean[key] = value

    emails = raw.get("adultEmails") or []
    if not isinstance(emails, list):
        return None, "Adult attendees must be given as a list of email addresses."
    adult_emails = []
    for entry in emails:
        email = str(entry or "").strip().lower()
        if not EMAIL_REGEX.match(email):
            return None, "Enter a valid email address for every additional adult."
        if email in adult_emails:
            return None, f"{email} is listed twice."
        adult_emails.append(email)
    if len(adult_emails) > AGM_MAX_PER_GROUP - 1:
        return None, f"You can register up to {AGM_MAX_PER_GROUP} adults at a time."
    clean["adultEmails"] = adult_emails

    attendance = str(raw.get("attendance") or "in_person")
    if attendance not in ("in_person", "remote"):
        return None, "Choose in person or remote attendance."
    clean["attendance"] = attendance
    return clean, ""


def agm_person_status(email, agm, *, ordering=False):
    """(status, member) of one person for this AGM — see agm_logic.status_from."""
    member = get_member(email)
    memberid = str(member.get("memberid", "")) if member else ""
    transactions = get_transactions(memberid) if memberid else []
    return agm_logic.status_from(member, transactions, agm.get("date", ""), ordering=ordering), member


def resolve_agm_adults(agm, registrant_email, registrant_member, registrant_status, adult_emails, ordering_emails=()):
    """Check every adult attendee. Returns (attendees, error); attendees[0] is the registrant.

    Anyone who isn't an NPO member (or about to become one) blocks the registration.
    """
    attendees = [{
        "email": registrant_email, "name": (registrant_member or {}).get("fullname", ""),
        "status": registrant_status, "registrant": True,
    }]
    for email in adult_emails:
        if email == registrant_email:
            return None, "That's your own email — you're counted as the first adult automatically."
        status, member = agm_person_status(email, agm, ordering=email in ordering_emails)
        if status not in agm_logic.ALLOWED:
            return None, f"{email}: {agm_logic.MESSAGES[status]}"
        attendees.append({"email": email, "name": member.get("fullname", ""), "status": status, "registrant": False})
    return attendees, ""


def registration_record(counts, attendees):
    """The counts + attendee list that are stored, emailed and shown."""
    record = {k: v for k, v in counts.items() if k != "adultEmails"}
    record["adults"] = len(attendees)
    record["adultAttendees"] = attendees
    record["totalAttendees"] = len(attendees) + sum(counts[key] for key, _ in agm_logic.YOUNG_GROUPS)
    return record


def save_agm_registration(config, email, name, record, source):
    table = dynamodb.Table(AGM_TABLE)
    now = datetime.now(timezone.utc).isoformat()
    try:
        existing = table.get_item(Key={"agmId": config["agmId"], "email": email}).get("Item") or {}
        table.put_item(Item={
            "agmId": config["agmId"],
            "email": email,
            "name": name,
            **record,
            "source": existing.get("source") or source,
            "registeredAt": existing.get("registeredAt") or now,
            "updatedAt": now,
        })
        return True
    except ClientError:
        logger.exception("Could not save AGM registration for %s", email)
        return False


def agm_self_message(agm):
    return (
        "Only NPO members can attend the AGM, and your membership needs to cover the AGM date "
        f"({agm.get('date', '')}). Please join or renew first (membership year {agm_logic.year_code_for(agm.get('date', ''))})."
    )


def evaluate_spouse(payer_email, spouse_email):
    """Can `spouse_email` be signed up (join/renew) alongside the payer?

    The spouse must already be a WMF Japan member (present in the members
    list); someone who isn't gets "not_member" and has to register first.

    Returns {"status": "join"|"renew"|"covered"|"pending"|"not_member"|"invalid"|"self", ...}.
    """
    spouse_email = str(spouse_email or "").strip().lower()
    if not EMAIL_REGEX.match(spouse_email):
        return {"status": "invalid", "email": spouse_email}
    if spouse_email == payer_email:
        return {"status": "self", "email": spouse_email}
    member, memberid, transactions, summary, is_member = build_status(spouse_email)
    base = {"email": spouse_email, "member": member, "memberid": memberid, "transactions": transactions}
    if not member:
        return {**base, "status": "not_member"}
    if summary["coversRenewal"]:
        return {**base, "status": "covered"}
    if find_pending_application(transactions):
        return {**base, "status": "pending"}
    return {**base, "status": "renew" if is_member else "join"}


SPOUSE_ERRORS = {
    "invalid": "Enter a valid email address for your spouse.",
    "self": "Your spouse needs their own email address.",
    "not_member": (
        "This email isn't registered as a WMF Japan member yet. "
        "Please request your spouse to become an NK member first, then add them here."
    ),
    "covered": f"Your spouse is already covered for {RENEWAL_MEMBERSHIP_YEAR}.",
    "pending": "Your spouse already has a membership request awaiting payment.",
}


def build_status(email):
    member = get_member(email)
    memberid = str(member.get("memberid", "")) if member else ""
    flag_active = bool(member.get("npoMembershipActive")) if member else False
    transactions = get_transactions(memberid) if memberid else []
    summary = npo_membership.evaluate(
        transactions, as_of=MEMBERSHIP_AS_OF, renewal_year=RENEWAL_MEMBERSHIP_YEAR
    )
    # Valid membership requires both the flag on the master record AND a
    # transaction period covering today. (The flag alone can't grant pricing.)
    is_member = flag_active and summary["validNow"]
    return member, memberid, transactions, summary, is_member


def transaction_history(transactions):
    """The member's own NPO transactions, newest first, trimmed to display fields."""
    rows = [
        {
            "transactionNo": str(t.get("transactionNo", "")),
            "year": t.get("membershipYear", ""),
            "kind": t.get("kind", "membership"),
            "forEmail": (t.get("linked") or [{}])[0].get("email", "") if t.get("kind") == "spouse-payment" else "",
            "paidBy": t.get("paidByEmail", ""),
            # What this member actually paid: the whole order for the payer of an
            # order that includes a spouse, otherwise the record's own amount.
            "amount": t.get("orderTotal") if t.get("linked") else t.get("membershipAmountPaid"),
            "paidDate": t.get("paidDate", ""),
            "from": t.get("membDateFrom", ""),
            "to": t.get("membDateTo", ""),
            "via": str(t.get("paidVia", "")),
            "createdAt": t.get("createdAt", ""),
            "status": t.get("paymentStatus") or "paid",  # legacy imports have no status
        }
        for t in transactions or []
    ]
    rows.sort(key=lambda r: (r["from"] or r["paidDate"] or r["createdAt"], r["transactionNo"].zfill(8)), reverse=True)
    return rows


def reconcile_square(txn):
    """True if a pending Square order turned out to be paid (and was marked so).

    Checks the current order and any earlier ones from retried attempts (a payer
    can still complete an old link). A cancelled current order is flagged as a
    failed attempt so the member is offered Retry Payment.
    """
    order_ids = [txn["providerPaymentId"], *(txn.get("previousPaymentIds") or [])]
    for index, order_id in enumerate(order_ids):
        try:
            result = square.get_payment_status(order_id)
        except Exception:
            logger.exception("Could not verify Square order %s", order_id)
            continue
        if result.get("status") == "COMPLETED":
            updated, newly_paid = npo_pay.mark_paid(
                txn["memberid"], txn["transactionNo"], payment_reference=result.get("paymentId", "")
            )
            if updated and newly_paid:
                npo_pay.send_paid_email(updated)
            return bool(updated and updated.get("paymentStatus") == "paid")
        if index == 0 and result.get("status") == "CANCELED" and txn.get("paymentAttemptStatus") != "failed":
            if npo_pay.mark_attempt_failed(txn["memberid"], txn["transactionNo"]):
                txn["paymentAttemptStatus"] = "failed"
    return False


def npo_method_key(value):
    value = value.strip().lower()
    if value in {"card", "credit", "creditcard", "credit_card"}:
        return "square"
    if value in {"bank_transfer", "banktransfer", "transfer"}:
        return "bank"
    return value


def find_pending_application(transactions):
    """The member's unpaid application for the renewal year, if any."""
    for txn in transactions or []:
        if (
            txn.get("paymentStatus") == "pending"
            and txn.get("membershipYear") == RENEWAL_MEMBERSHIP_YEAR
        ):
            return txn
    return None


def next_transaction_no(transactions):
    highest = 0
    for txn in transactions or []:
        try:
            highest = max(highest, int(str(txn.get("transactionNo", "0"))))
        except (TypeError, ValueError):
            continue
    return str(highest + 1)


def email_is_active_member(email):
    """True if `email` is a current NPO member (flag active + period covers today)."""
    member = get_member(email)
    if not member:
        return False
    memberid = str(member.get("memberid", ""))
    flag_active = bool(member.get("npoMembershipActive"))
    if not (memberid and flag_active):
        return False
    transactions = get_transactions(memberid)
    summary = npo_membership.evaluate(
        transactions, as_of=MEMBERSHIP_AS_OF, renewal_year=RENEWAL_MEMBERSHIP_YEAR
    )
    return bool(summary["validNow"])


# ── Route handlers ────────────────────────────────────────────────────────────


def handle_agm_adult_check(payer_email, body, origin):
    """Signed-in members only: may this person attend the AGM as an adult?"""
    email = str((body or {}).get("email", "")).strip().lower()
    config = load_agm_config()
    if not EMAIL_REGEX.match(email):
        return respond(200, {"email": email, "status": "invalid", "valid": False,
                             "message": "Enter a valid email address."}, origin)
    if email == payer_email:
        return respond(200, {"email": email, "status": "self", "valid": False,
                             "message": "That's you — you're counted as the first adult automatically."}, origin)
    if not config["enabled"]:
        return respond(200, {"email": email, "status": "closed", "valid": False,
                             "message": "AGM registration isn't open."}, origin)
    status, _member = agm_person_status(email, config)
    payload = {"email": email, "status": status, "valid": status in agm_logic.ALLOWED}
    if not payload["valid"]:
        payload["message"] = agm_logic.MESSAGES[status]
    return respond(200, payload, origin)


def handle_spouse_check(payer_email, body, origin):
    """Signed-in members only: what would adding this spouse email do, and at what cost?"""
    result = evaluate_spouse(payer_email, (body or {}).get("email", ""))
    status = result["status"]
    payload = {"email": result["email"], "status": status, "valid": status in ("join", "renew")}
    if payload["valid"]:
        payload["fee"] = load_npo_config()["fee"]
    else:
        payload["message"] = SPOUSE_ERRORS[status]
        if status == "not_member":
            payload["registerUrl"] = MEMBER_REGISTER_URL
    return respond(200, payload, origin)


def handle_check(body, origin):
    """Public, unauthenticated: is a given email a current NPO member?

    Used to preview the per-adult discount for additional adults during Onam
    registration. Returns only a boolean — no membership dates, name, or org —
    so it can't be used to harvest member details. The register Lambda re-checks
    every email server-side, so a spoofed `true` here never affects the charge.
    """
    email = str((body or {}).get("email", "")).strip().lower()
    if not email or not EMAIL_REGEX.match(email):
        return respond(400, {"error": "A valid email is required.", "isMember": False}, origin)
    return respond(200, {"email": email, "isMember": email_is_active_member(email)}, origin)


def agm_status(email):
    config = load_agm_config()
    if not config["enabled"]:
        return {"enabled": False}
    status, _member = agm_person_status(email, config)
    return {
        **public_agm(config),
        "eligible": status in agm_logic.ALLOWED,
        "eligibility": status,  # member | pending | not_member | not_nk_member
        "registration": get_agm_registration(config["agmId"], email),
    }


def handle_status(email, origin):
    member, memberid, transactions, summary, is_member = build_status(email)
    # Back-stop for a missed/slow Square webhook: when the member returns from
    # checkout, ask Square directly whether the pending order was paid.
    pending = find_pending_application(transactions)
    if pending and pending.get("providerPaymentId") and reconcile_square(pending):
        member, memberid, transactions, summary, is_member = build_status(email)
    flag_active = bool(member.get("npoMembershipActive")) if member else False
    covers_renewal = summary["coversRenewal"]
    config = load_npo_config()
    renewal_start, renewal_end = npo_membership.year_span(RENEWAL_MEMBERSHIP_YEAR)
    pending = find_pending_application(transactions)
    pending_payment = None
    if pending:
        method = config["methods"].get(str(pending.get("paidVia", "")))
        if method and pending.get("checkoutUrl"):
            method = {**method, "url": pending["checkoutUrl"]}
        pending_payment = {
            "action": pending.get("action", "join"),
            "kind": pending.get("kind", "membership"),
            "paidByOther": bool(pending.get("paidByEmail")),
            # PayPay / bank transfer: the member says they have paid (the committee still confirms it).
            "reportedAt": pending.get("paymentReportedAt", ""),
            # A failed/cancelled card attempt: the page offers "Retry Payment".
            "attemptFailed": pending.get("paymentAttemptStatus") == "failed"
            and pending.get("paidVia") == "square"
            and not pending.get("paidByEmail"),
            "paidBy": pending.get("paidByEmail", ""),
            "fee": int(pending.get("orderTotal") or pending.get("membershipAmountPaid", config["fee"]) or 0),
            "paymentMethod": method
            or {"key": pending.get("paidVia", ""), "label": PAYMENT_METHOD_LABELS.get(pending.get("paidVia"), ""), "url": "", "instructions": ""},
        }

    return respond(
        200,
        {
            "email": email,
            "memberId": memberid or None,
            "isMember": is_member,
            "npoFlagActive": flag_active,
            "validUntil": summary["latestTo"] or None,
            "coversRenewal": covers_renewal,
            "memberName": (member or {}).get("fullname", ""),
            "organization": (member or {}).get("organization", ""),
            "renewalYear": RENEWAL_MEMBERSHIP_YEAR,
            "renewalFrom": renewal_start.isoformat() if renewal_start else None,
            "renewalTo": renewal_end.isoformat() if renewal_end else None,
            # A valid member can renew for the coming year (unless already
            # covered). Anyone not currently valid can join for the renewal year.
            "canRenew": is_member and not covers_renewal,
            "canJoin": not is_member,
            # My Page: what the signed-in member can do right now, and how the
            # admin has configured payment.
            "fee": config["fee"],
            "paymentMethods": list(config["methods"].values()),
            # Applied but not yet confirmed paid — membership starts once an
            # admin marks the payment received.
            "pendingPayment": pending_payment,
            "agm": agm_status(email),
            "profile": {
                "fullName": (member or {}).get("fullname", ""),
                "email": email,
                "memberId": memberid or None,
                "phone": (member or {}).get("phone", ""),
                "mobile": (member or {}).get("mobile", ""),
            },
            "transactions": transaction_history(transactions),
        },
        origin,
    )


def _own_txn(**fields):
    return {"source": "my-page", "membershipYear": RENEWAL_MEMBERSHIP_YEAR, "paymentStatus": "pending", **fields}


def handle_report_paid(email, transactions, reported, origin):
    """PayPay / bank transfer: the member says they have paid (or takes it back).

    The system can't see a manual payment, so this is only a signal — the order
    stays "awaiting payment" until an admin confirms it. It changes what the member
    sees (no more "Pay now") and tells the committee which orders to check first.
    """
    pending = find_pending_application(transactions)
    if not pending or pending.get("paidByEmail") or pending.get("paidVia") == "square":
        return respond(409, {"error": "There is no manual payment awaiting confirmation."}, origin)
    table = dynamodb.Table(MEMBERSHIP_TABLE)
    key = {"memberid": pending["memberid"], "transactionNo": pending["transactionNo"]}
    already = bool(pending.get("paymentReportedAt"))
    now = datetime.now(timezone.utc).isoformat()
    try:
        if reported:
            if not already:
                table.update_item(
                    Key=key,
                    UpdateExpression="SET paymentReportedAt = :now, updatedAt = :now",
                    ConditionExpression="paymentStatus = :pending",
                    ExpressionAttributeValues={":now": now, ":pending": "pending"},
                )
        elif already:
            table.update_item(
                Key=key,
                UpdateExpression="REMOVE paymentReportedAt SET updatedAt = :now",
                ConditionExpression="paymentStatus = :pending",
                ExpressionAttributeValues={":now": now, ":pending": "pending"},
            )
    except ClientError:
        logger.exception("Could not record the payment report for %s", email)
        return respond(409, {"error": "This request is no longer awaiting payment — please refresh the page."}, origin)
    if reported and not already and ADMIN_EMAIL:
        total = int(pending.get("orderTotal") or pending.get("membershipAmountPaid") or 0)
        npo_pay.send_email(
            [ADMIN_EMAIL],
            f"NPO membership: {email} says they have paid",
            f"<p>{escape(email)} reports having paid &yen;{total:,} by "
            f"{escape(PAYMENT_METHOD_LABELS.get(pending.get('paidVia'), str(pending.get('paidVia', ''))))} "
            f"for NPO membership {escape(str(pending.get('membershipYear', '')))}. "
            f"Please check for the payment and mark it paid in Admin &rarr; Members.</p>",
        )
    return respond(200, {"ok": True, "reportedAt": now if reported else ""}, origin)


def handle_retry(email, transactions, origin):
    """Start a fresh Square checkout for the member's pending card payment.

    Only for a payment that really failed (or whose order was cancelled): a
    payment that may still be in progress just returns the existing link, and
    one that actually completed is marked paid — so a retry can never charge
    twice. The old order is cancelled and remembered so a late payment on it is
    still recognised.
    """
    pending = find_pending_application(transactions)
    if not pending or pending.get("paidVia") != "square" or not pending.get("providerPaymentId") or pending.get("paidByEmail"):
        return respond(409, {"error": "There is no card payment to retry."}, origin)

    old_id = pending["providerPaymentId"]
    try:
        result = square.get_payment_status(old_id)
    except Exception:
        logger.exception("Could not verify Square order %s before retrying", old_id)
        return respond(502, {"error": "We couldn't check your payment. Please try again."}, origin)
    if result.get("status") == "COMPLETED":
        updated, newly_paid = npo_pay.mark_paid(
            pending["memberid"], pending["transactionNo"], payment_reference=result.get("paymentId", "")
        )
        if updated and newly_paid:
            npo_pay.send_paid_email(updated)
        return respond(200, {"ok": True, "alreadyPaid": True}, origin)

    failed = pending.get("paymentAttemptStatus") == "failed" or result.get("status") == "CANCELED"
    if not failed:
        return respond(200, {"ok": True, "reused": True, "checkoutUrl": pending.get("checkoutUrl", "")}, origin)

    total = int(pending.get("orderTotal") or pending.get("membershipAmountPaid") or 0)
    try:
        checkout = square.create_checkout(
            reference_id=f"npo-{pending['transactionNo']}-{str(pending['memberid'])[:20]}",
            amount_yen=total,
            description=f"WMF Japan NPO membership {pending.get('membershipYear') or RENEWAL_MEMBERSHIP_YEAR}",
            redirect_url=f"{FRONTEND_URL}/member/membership?payment=return" if FRONTEND_URL else "",
        )
    except Exception:
        logger.exception("Could not create a retry checkout for %s", email)
        return respond(502, {"error": "We couldn't start the card payment. Please try again."}, origin)

    try:
        dynamodb.Table(MEMBERSHIP_TABLE).update_item(
            Key={"memberid": pending["memberid"], "transactionNo": pending["transactionNo"]},
            UpdateExpression=(
                "SET providerPaymentId = :new, checkoutUrl = :url, "
                "previousPaymentIds = list_append(if_not_exists(previousPaymentIds, :none), :old), "
                "retryCount = if_not_exists(retryCount, :zero) + :one "
                "REMOVE paymentAttemptStatus"
            ),
            ConditionExpression="paymentStatus = :pending AND providerPaymentId = :oldid",
            ExpressionAttributeValues={
                ":new": checkout["providerPaymentId"], ":url": checkout["checkoutUrl"], ":none": [], ":old": [old_id],
                ":zero": 0, ":one": 1, ":pending": "pending", ":oldid": old_id,
            },
        )
    except ClientError:
        logger.exception("Could not save the retry checkout for %s", email)
        return respond(409, {"error": "Your payment changed — please refresh and try again."}, origin)

    try:  # close the old link so it can't also be paid
        square.cancel_order(old_id)
    except Exception:
        logger.warning("Could not cancel old Square order %s", old_id, exc_info=True)
    return respond(200, {"ok": True, "checkoutUrl": checkout["checkoutUrl"]}, origin)


CONSENT_KEYS = ("terms", "cancellation", "privacy")


def handle_apply(email, body, origin):
    """Join/renew NPO membership for the signed-in member and optionally their
    spouse, optionally registering for the AGM in the same step (My Page)."""
    body = body or {}
    member, memberid, transactions, summary, is_member = build_status(email)
    if not member:
        return respond(404, {"error": "We couldn't find your member record."}, origin)
    if body.get("retry"):
        return handle_retry(email, transactions, origin)
    if "reportPaid" in body:
        return handle_report_paid(email, transactions, bool(body["reportPaid"]), origin)
    consent = body.get("consent") or {}
    if not all(consent.get(k) is True for k in CONSENT_KEYS):
        return respond(
            400,
            {"error": "Please accept the terms & conditions, cancellation policy and privacy policy to continue."},
            origin,
        )
    if find_pending_application(transactions):
        return respond(
            409, {"error": "You already have a membership request awaiting payment."}, origin
        )
    covered = summary["coversRenewal"]

    spouse_email = str(body.get("spouseEmail") or "").strip().lower()
    spouse = None
    if spouse_email:
        spouse = evaluate_spouse(email, spouse_email)
        if spouse["status"] not in ("join", "renew"):
            return respond(409, {"error": SPOUSE_ERRORS[spouse["status"]]}, origin)
    if covered and not spouse:
        return respond(
            409, {"error": f"You're already covered for {RENEWAL_MEMBERSHIP_YEAR}."}, origin
        )

    config = load_npo_config()
    method_key = npo_method_key(str(body.get("paymentMethod", "")))
    method = config["methods"].get(method_key)
    if not method:
        return respond(400, {"error": "Please choose an available payment method."}, origin)
    if not npo_membership.year_span(RENEWAL_MEMBERSHIP_YEAR)[0]:
        return respond(500, {"error": "Membership year is not configured."}, origin)

    agm_config = agm_record = None
    if body.get("agm"):
        agm_config = load_agm_config()
        if not agm_config["enabled"]:
            return respond(400, {"error": "AGM registration isn't open."}, origin)
        counts, error = parse_agm_registration(body["agm"])
        if error:
            return respond(400, {"error": error}, origin)
        # Ordering membership for the AGM's year makes the registrant (and a spouse
        # in the same order) provisionally eligible while payment is awaited.
        order_covers_agm = agm_logic.year_code_for(agm_config.get("date", "")) == RENEWAL_MEMBERSHIP_YEAR
        registrant_status = agm_logic.status_from(
            member, transactions, agm_config.get("date", ""), ordering=order_covers_agm and not covered
        )
        if registrant_status not in agm_logic.ALLOWED:
            return respond(400, {"error": agm_self_message(agm_config)}, origin)
        ordering_emails = {spouse_email} if spouse and order_covers_agm else set()
        attendees, error = resolve_agm_adults(
            agm_config, email, member, registrant_status, counts["adultEmails"], ordering_emails
        )
        if error:
            return respond(409, {"error": error}, origin)
        agm_record = registration_record(counts, attendees)

    fee = config["fee"]
    lines = []
    if not covered:
        lines.append((f"Your membership ({'renewal' if is_member else 'new'})", fee))
    if spouse:
        lines.append((f"Spouse membership ({spouse_email})", fee))
    total = sum(amount for _, amount in lines)

    now = datetime.now(timezone.utc).isoformat()
    memberid = memberid or email
    action = "renew" if is_member else "join"
    txn_no = next_transaction_no(transactions)

    checkout = None
    if method_key == "square" and total > 0:
        # Create the hosted checkout first so a Square failure leaves no
        # orphaned pending application behind.
        try:
            checkout = square.create_checkout(
                reference_id=f"npo-{txn_no}-{str(memberid)[:20]}",
                amount_yen=total,
                description=f"WMF Japan NPO membership {RENEWAL_MEMBERSHIP_YEAR}",
                redirect_url=f"{FRONTEND_URL}/member/membership?payment=return" if FRONTEND_URL else "",
            )
        except Exception:
            logger.exception("Could not create Square checkout for %s", email)
            return respond(502, {"error": "We couldn't start the card payment. Please try again."}, origin)

    membership_table = dynamodb.Table(MEMBERSHIP_TABLE)
    spouse_txn = None
    try:
        if spouse:
            spouse_memberid = spouse["memberid"] or spouse_email
            spouse_txn = _own_txn(
                memberid=spouse_memberid,
                transactionNo=next_transaction_no(spouse["transactions"]),
                membershipAmountPaid=fee,
                paidVia=method_key,
                action=spouse["status"],
                email=spouse_email,
                paidByEmail=email,
                notes=f"Paid by {email} (their order #{txn_no})",
                createdAt=now,
            )
            membership_table.put_item(
                Item=spouse_txn,
                ConditionExpression="attribute_not_exists(memberid) AND attribute_not_exists(transactionNo)",
            )

        if covered:
            # Already a member: this record just carries the payment for the spouse.
            # The spouse's own record holds the membership fee, so this one books
            # ¥0 (orderTotal has what was charged) — otherwise summing the
            # amounts would count the same payment twice.
            txn = _own_txn(
                memberid=memberid, transactionNo=txn_no, kind="spouse-payment",
                membershipAmountPaid=0, paidVia=method_key, action="renew", email=email, createdAt=now,
                notes=f"Payment for spouse's membership ({spouse_email}) — no membership of its own",
            )
        else:
            txn = _own_txn(
                memberid=memberid, transactionNo=txn_no, membershipAmountPaid=fee,
                paidVia=method_key, action=action, email=email, createdAt=now,
            )
            if spouse:
                txn["notes"] = f"Includes spouse's membership ({spouse_email})"
        txn["orderTotal"] = total
        # Audit trail: when the member accepted the policies for this order.
        txn["consentAcceptedAt"] = now
        txn["consentPolicies"] = list(CONSENT_KEYS)
        if spouse_txn:
            txn["linked"] = [
                {"memberid": spouse_txn["memberid"], "transactionNo": spouse_txn["transactionNo"], "email": spouse_email}
            ]
        if checkout:
            txn["providerPaymentId"] = checkout["providerPaymentId"]
            txn["checkoutUrl"] = checkout["checkoutUrl"]
        membership_table.put_item(
            Item=txn,
            ConditionExpression="attribute_not_exists(memberid) AND attribute_not_exists(transactionNo)",
        )
    except ClientError:
        logger.exception("Could not record NPO membership for %s", email)
        if spouse_txn:  # don't leave the spouse half of the order behind
            try:
                membership_table.delete_item(
                    Key={"memberid": spouse_txn["memberid"], "transactionNo": spouse_txn["transactionNo"]}
                )
            except ClientError:
                logger.exception("Could not roll back spouse transaction")
        return respond(500, {"error": "We couldn't save your membership. Please try again."}, origin)

    agm_saved = None
    if agm_record:
        agm_saved = save_agm_registration(
            agm_config, email, (member or {}).get("fullname", ""), agm_record, "membership-order"
        )

    pay_method = {**method, "url": checkout["checkoutUrl"]} if checkout else method
    subject, html = member_emailer.build_order_received(
        {"name": (member or {}).get("fullname", "")}, RENEWAL_MEMBERSHIP_YEAR, lines, total, pay_method,
        agm_config if agm_saved else None, agm_record if agm_saved else None,
    )
    member_emailer._send([email], subject, html)
    if agm_saved:
        # The spouse already gets the "signed up" email; tell any other adults too.
        member_emailer.send_agm_guest_emails(
            agm_config, agm_record, email, (member or {}).get("fullname", ""),
            already_notified={spouse_email} if spouse else set(),
        )
    if spouse:
        subject, html = member_emailer.build_signed_up_by_other(
            {"name": (spouse["member"] or {}).get("fullname", "")}, email, RENEWAL_MEMBERSHIP_YEAR
        )
        member_emailer._send([spouse_email], subject, html)
    if ADMIN_EMAIL:
        npo_pay.send_email(
            [ADMIN_EMAIL],
            f"NPO membership order: {email}",
            f"<p>{escape(email)} ordered NPO membership for {escape(RENEWAL_MEMBERSHIP_YEAR)}: "
            + escape("; ".join(f"{t} ¥{a:,}" for t, a in lines))
            + f" — total &yen;{total:,} via {escape(method['label'])}. Payment status: pending."
            + (" AGM registration included." if agm_saved else "")
            + "</p>",
        )

    return respond(
        200,
        {
            "ok": True,
            "action": "spouse" if covered else action,
            "year": RENEWAL_MEMBERSHIP_YEAR,
            "fee": total,
            "lines": [{"label": t, "amount": a} for t, a in lines],
            "paymentMethod": pay_method,
            "transactionNo": txn["transactionNo"],
            "agmSaved": agm_saved,
        },
        origin,
    )


def handle_agm(email, body, origin):
    """Register (or update / cancel) the signed-in member's AGM attendance."""
    config = load_agm_config()
    if not config["enabled"]:
        return respond(400, {"error": "AGM registration isn't open."}, origin)
    member = get_member(email)
    if not member:
        return respond(404, {"error": "We couldn't find your member record."}, origin)
    if (body or {}).get("cancel"):  # always allowed, even if the membership has since lapsed
        try:
            dynamodb.Table(AGM_TABLE).delete_item(Key={"agmId": config["agmId"], "email": email})
        except ClientError:
            logger.exception("Could not cancel AGM registration for %s", email)
            return respond(500, {"error": "We couldn't cancel your registration. Please try again."}, origin)
        return respond(200, {"ok": True, "registration": None}, origin)

    registrant_status, _ = agm_person_status(email, config)
    if registrant_status not in agm_logic.ALLOWED:
        return respond(403, {"error": agm_self_message(config)}, origin)

    counts, error = parse_agm_registration(body)
    if error:
        return respond(400, {"error": error}, origin)
    attendees, error = resolve_agm_adults(config, email, member, registrant_status, counts["adultEmails"])
    if error:
        return respond(409, {"error": error}, origin)
    record = registration_record(counts, attendees)
    previous = get_agm_registration(config["agmId"], email)
    already_registered = previous is not None
    previously_listed = {str(x.get("email", "")).lower() for x in (previous or {}).get("adultAttendees") or []}
    if not save_agm_registration(config, email, member.get("fullname", ""), record, "my-page"):
        return respond(500, {"error": "We couldn't save your registration. Please try again."}, origin)
    subject, html = member_emailer.build_agm_registered(
        {"name": member.get("fullname", "")}, config, record, updated=already_registered
    )
    member_emailer._send([email], subject, html)
    member_emailer.send_agm_guest_emails(  # only adults added since the last save
        config, record, email, member.get("fullname", ""), already_notified=previously_listed
    )
    return respond(200, {"ok": True, "registration": get_agm_registration(config["agmId"], email)}, origin)


def lambda_handler(event, context):
    if event.get("httpMethod") == "OPTIONS":
        return respond(200, {})

    headers = {k.lower(): v for k, v in (event.get("headers") or {}).items()}
    origin = headers.get("origin", "*")

    resource_path = event.get("resource") or event.get("path") or ""

    # Public, unauthenticated: preview whether an arbitrary email is an NPO
    # member (for pricing additional adults). Returns only a boolean.
    if resource_path.endswith("/check"):
        try:
            body = json.loads(event.get("body") or "{}")
        except json.JSONDecodeError:
            return respond(400, {"error": "Invalid JSON body.", "isMember": False}, origin)
        # Signed-in members can ask for the detailed spouse check.
        if isinstance(body, dict) and body.get("detail"):
            payload = verify_token(headers.get("authorization", "").strip())
            payer = str((payload or {}).get("email", "")).strip().lower()
            if not payer:
                return respond(401, {"error": "Please sign in again to continue."}, origin)
            if body.get("forAgm"):
                return handle_agm_adult_check(payer, body, origin)
            return handle_spouse_check(payer, body, origin)
        return handle_check(body, origin)

    if not TOKEN_SECRET:
        logger.error("OTP_TOKEN_SECRET is not configured.")
        return respond(500, {"error": "Server is not configured for membership lookup."}, origin)

    token = headers.get("authorization", "").strip()
    payload = verify_token(token)
    if not payload:
        return respond(401, {"error": "Please sign in again to continue."}, origin)

    email = str(payload.get("email", "")).strip().lower()
    if not email:
        return respond(401, {"error": "Please sign in again to continue."}, origin)

    if resource_path.endswith("/status"):
        return handle_status(email, origin)

    if resource_path.endswith("/apply"):
        if event.get("httpMethod") != "POST":
            return respond(405, {"error": "Method not allowed."}, origin)
        try:
            body = json.loads(event.get("body") or "{}")
        except json.JSONDecodeError:
            return respond(400, {"error": "Invalid JSON body."}, origin)
        return handle_apply(email, body if isinstance(body, dict) else {}, origin)

    if resource_path.endswith("/agm"):
        if event.get("httpMethod") != "POST":
            return respond(405, {"error": "Method not allowed."}, origin)
        try:
            body = json.loads(event.get("body") or "{}")
        except json.JSONDecodeError:
            return respond(400, {"error": "Invalid JSON body."}, origin)
        return handle_agm(email, body if isinstance(body, dict) else {}, origin)

    return respond(404, {"error": "Unknown membership route."}, origin)
