"""
Lambda: /admin/members (+ item) and /admin/npo-membership (+ item)
Protected by Cognito JWT authorizer.

Routes:
  GET    /admin/members                               → list all members with NPO status
  POST   /admin/members                               → upsert a member record
  GET    /admin/members/{email}                       → get one member with NPO status
  PUT    /admin/members/{email}                       → update a member record
  DELETE /admin/members/{email}                       → delete a member record
  GET    /admin/npo-membership/{memberid}             → list NPO transactions for a member
  POST   /admin/npo-membership/{memberid}             → add a new NPO transaction
  PUT    /admin/npo-membership/{memberid}/{txn}       → update a transaction
  DELETE /admin/npo-membership/{memberid}/{txn}       → delete a transaction

A paid transaction is cancelled (and optionally refunded) with
PUT /admin/npo-membership/{memberid}/{txn} {"action": "cancel", ...}.
"""

import json
import os
import re
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Optional
from urllib.parse import unquote

import boto3
from boto3.dynamodb.conditions import Key
from botocore.exceptions import ClientError

import announcement_logic
import npo_membership
import npo_payment_logic
import square
import member_emailer

dynamodb = boto3.resource("dynamodb", region_name=os.environ["REGION"])
MEMBERS_TABLE = os.environ.get("MEMBERS_TABLE", "")
MEMBERSHIP_TABLE = os.environ.get("MEMBERSHIP_TABLE", "")
RENEWAL_MEMBERSHIP_YEAR = os.environ.get("RENEWAL_MEMBERSHIP_YEAR", "2026-27")
MEMBERSHIP_AS_OF = os.environ.get("MEMBERSHIP_AS_OF", "")

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


# ── Helpers ──────────────────────────────────────────────────────────────────

def decimal_to_python(obj):
    if isinstance(obj, Decimal):
        return int(obj) if obj == obj.to_integral_value() else float(obj)
    raise TypeError(f"Object of type {type(obj)} is not JSON serializable")


def cors_headers():
    return {
        "Access-Control-Allow-Origin": "*",
        "Access-Control-Allow-Headers": "Content-Type,Authorization",
        "Access-Control-Allow-Methods": "OPTIONS,GET,POST,PUT,DELETE",
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


def normalize_email(value: Any) -> str:
    return unquote(str(value or "").strip()).lower()


def parse_json_body(event: dict) -> dict:
    if event.get("isBase64Encoded"):
        return {}
    try:
        parsed = json.loads(event.get("body") or "{}")
        return parsed if isinstance(parsed, dict) else {}
    except json.JSONDecodeError:
        return {}


def scan_all(table) -> list[dict]:
    items: list[dict] = []
    kwargs: dict[str, Any] = {}
    while True:
        response = table.scan(**kwargs)
        items.extend(response.get("Items", []))
        if "LastEvaluatedKey" not in response:
            break
        kwargs["ExclusiveStartKey"] = response["LastEvaluatedKey"]
    return items


def get_transactions(memberid: str) -> list[dict]:
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


def annotate_member(member: dict) -> dict:
    """Merge NPO status fields into a member record."""
    memberid = str(member.get("memberid", "")).strip()
    flag_active = bool(member.get("npoMembershipActive"))
    transactions = get_transactions(memberid) if memberid else []
    summary = npo_membership.evaluate(
        transactions,
        as_of=MEMBERSHIP_AS_OF,
        renewal_year=RENEWAL_MEMBERSHIP_YEAR,
    )
    return {
        **member,
        "isMember": flag_active and summary["validNow"],
        "npoSummary": {
            "validNow": summary["validNow"],
            "coversRenewal": summary["coversRenewal"],
            "latestTo": summary["latestTo"],
            "renewalYear": RENEWAL_MEMBERSHIP_YEAR,
        },
        "transactions": transactions,
    }


def next_transaction_no(memberid: str, transactions: list) -> str:
    highest = 0
    for txn in transactions or []:
        try:
            highest = max(highest, int(str(txn.get("transactionNo", "0"))))
        except (TypeError, ValueError):
            continue
    return str(highest + 1)


def sync_member_flag(email: str, memberid: str, active: bool):
    """Keep npoMembershipActive flag on the member record in sync."""
    if not MEMBERS_TABLE or not email:
        return
    try:
        dynamodb.Table(MEMBERS_TABLE).update_item(
            Key={"email": email},
            UpdateExpression="SET npoMembershipActive = :v, memberid = if_not_exists(memberid, :m)",
            ExpressionAttributeValues={":v": active, ":m": memberid},
        )
    except ClientError as exc:
        print(f"Member flag sync error for {email}: {exc}")


# ── Members CRUD ──────────────────────────────────────────────────────────────

def handle_list_members(_event: dict):
    if not MEMBERS_TABLE:
        return respond(500, {"error": "Members table not configured"})
    members = scan_all(dynamodb.Table(MEMBERS_TABLE))
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
        result.append({
            **member,
            "email": email,
            "isMember": flag_active and summary["validNow"],
            "npoSummary": {
                "validNow": summary["validNow"],
                "coversRenewal": summary["coversRenewal"],
                "latestTo": summary["latestTo"],
                "renewalYear": RENEWAL_MEMBERSHIP_YEAR,
            },
        })
    result.sort(key=lambda m: str(m.get("fullname", "") or m.get("memberName", "") or m["email"]).lower())
    return respond(200, {"members": result})


WHATSAPP_STATUSES = ("joined", "not_joined")
YEAR_CODE_RE = re.compile(r"^\d{4}-\d{2}$")


def merge_whatsapp_status(current, changes) -> dict:
    """Merge {"2026-27": "joined" | "not_joined" | ""} into the member's per-year WhatsApp group status.

    An empty value clears that year ("not recorded"); years not mentioned are kept as they are."""
    if not isinstance(changes, dict):
        raise ValueError("WhatsApp group status must be a year → status map")
    merged = dict(current) if isinstance(current, dict) else {}
    for year, status in changes.items():
        year, status = str(year).strip(), str(status or "").strip().lower()
        if not YEAR_CODE_RE.match(year):
            raise ValueError(f"'{year}' isn't a membership year — use the form 2026-27")
        if status and status not in WHATSAPP_STATUSES:
            raise ValueError("WhatsApp group status must be 'joined', 'not_joined' or blank")
        if status:
            merged[year] = status
        else:
            merged.pop(year, None)
    return merged


def handle_upsert_member(event: dict, email_path: Optional[str] = None):
    if not MEMBERS_TABLE:
        return respond(500, {"error": "Members table not configured"})
    body = parse_json_body(event)
    print(f"Upsert member body: {body}")
    email = normalize_email(email_path or body.get("email", ""))
    print(f"Upsert member email: {email}")
    if not email or not EMAIL_RE.match(email):
        return respond(400, {"error": "Valid email is required", "fields": {"email": "Valid email is required"}})

    # Fetch existing record so we preserve fields not sent in this request.
    try:
        existing = dynamodb.Table(MEMBERS_TABLE).get_item(Key={"email": email}).get("Item") or {}
    except ClientError as exc:
        print(f"Member fetch error for {email}: {exc}")
        existing = {}

    record = {
        **existing,
        "email": email,
        "updatedAt": now_iso(),
    }
    if "fullname" in body:
        record["fullname"] = str(body["fullname"]).strip()
    if "memberName" in body:
        record["memberName"] = str(body["memberName"]).strip()
    if "phone" in body:
        record["phone"] = str(body["phone"]).strip()
    if "mobile" in body:
        record["mobile"] = str(body["mobile"]).strip()
    if "memberid" in body:
        record["memberid"] = str(body["memberid"]).strip()
    if "npoMembershipActive" in body:
        record["npoMembershipActive"] = bool(body["npoMembershipActive"])
    if "whatsappGroup" in body:
        try:
            record["whatsappGroup"] = merge_whatsapp_status(existing.get("whatsappGroup"), body["whatsappGroup"])
        except ValueError as exc:
            return respond(400, {"error": str(exc), "fields": {"whatsappGroup": str(exc)}})
    # Preserve any extra fields passed through (e.g. address, notes).
    for key, value in body.items():
        if key not in record and key not in ("email",):
            record[key] = value

    if "createdAt" not in record:
        record["createdAt"] = record["updatedAt"]

    try:
        dynamodb.Table(MEMBERS_TABLE).put_item(Item=record)
    except ClientError as exc:
        print(f"Member upsert error for {email}: {exc}")
        return respond(500, {"error": "Failed to save member"})

    return respond(200, {"member": annotate_member(record), "created": not bool(existing)})


def handle_get_member(_event: dict, email: str):
    if not MEMBERS_TABLE:
        return respond(500, {"error": "Members table not configured"})
    email = normalize_email(email)
    try:
        item = dynamodb.Table(MEMBERS_TABLE).get_item(Key={"email": email}).get("Item")
    except ClientError as exc:
        print(f"Member fetch error for {email}: {exc}")
        return respond(500, {"error": "Failed to fetch member"})
    if not item:
        return respond(404, {"error": "Member not found"})
    return respond(200, {"member": annotate_member(item)})


def handle_delete_member(_event: dict, email: str):
    if not MEMBERS_TABLE:
        return respond(500, {"error": "Members table not configured"})
    email = normalize_email(email)
    try:
        dynamodb.Table(MEMBERS_TABLE).delete_item(Key={"email": email})
    except ClientError as exc:
        print(f"Member delete error for {email}: {exc}")
        return respond(500, {"error": "Failed to delete member"})
    return respond(200, {"deleted": True, "email": email})


# ── NPO Membership CRUD ───────────────────────────────────────────────────────

def handle_list_npo_transactions(_event: dict, memberid: str):
    if not MEMBERSHIP_TABLE:
        return respond(500, {"error": "Membership table not configured"})
    transactions = get_transactions(memberid)
    transactions.sort(key=lambda t: str(t.get("membDateFrom", "")))
    return respond(200, {"memberid": memberid, "transactions": transactions})


def _membership_year(start) -> str:
    """Membership-year code for a period starting on `start` (years run 1 Nov – 31 Oct): 2026-11-01 → "2026-27"."""
    try:
        d = datetime.strptime(str(start or "")[:10], "%Y-%m-%d")
    except ValueError:
        return ""
    first = d.year if d.month >= 11 else d.year - 1
    return f"{first}-{(first + 1) % 100:02d}"


def handle_create_npo_transaction(event: dict, memberid: str):
    if not MEMBERSHIP_TABLE:
        return respond(500, {"error": "Membership table not configured"})
    body = parse_json_body(event)
    transactions = get_transactions(memberid)
    txn_no = str(body.get("transactionNo", "")).strip() or next_transaction_no(memberid, transactions)

    txn: dict[str, Any] = {
        "memberid": memberid,
        "transactionNo": txn_no,
        "createdAt": now_iso(),
    }
    for field in ("membDateFrom", "membDateTo", "membershipAmountPaid", "paidDate",
                  "paidVia", "email", "membershipYear", "notes"):
        if field in body:
            txn[field] = body[field]

    try:
        dynamodb.Table(MEMBERSHIP_TABLE).put_item(Item=txn)
    except ClientError as exc:
        print(f"NPO transaction write error for {memberid}/{txn_no}: {exc}")
        return respond(500, {"error": "Failed to save transaction"})

    # Sync the active flag on the member record if we can resolve the email.
    email = str(txn.get("email", "")).strip().lower()
    if email:
        sync_member_flag(email, memberid, active=True)

    # An admin-added transaction is a confirmed membership, so the member can be told —
    # only when asked (back-filling old years shouldn't email anyone).
    emailed = False
    if body.get("notify") is True and EMAIL_RE.match(email):
        confirmation = {**txn, "membershipYear": txn.get("membershipYear") or _membership_year(txn.get("membDateFrom"))}
        try:
            emailed = bool(npo_payment_logic.send_paid_email(confirmation, notify_admin=False))
        except Exception as exc:  # the transaction is saved; a mail problem must not fail the request
            print(f"Confirmation email error for {memberid}/{txn_no}: {exc}")

    return respond(200, {"transaction": txn, "created": True, "emailed": emailed})


def _activate_linked(primary: dict):
    """Marking an order's primary transaction paid also pays the spouse's linked one."""
    table = dynamodb.Table(MEMBERSHIP_TABLE)
    paid = []
    for link in primary.get("linked") or []:
        key = {"memberid": str(link.get("memberid", "")), "transactionNo": str(link.get("transactionNo", ""))}
        try:
            linked = table.get_item(Key=key).get("Item")
            if not linked or linked.get("paymentStatus") != "pending":
                continue
            start, end = npo_membership.year_span(linked.get("membershipYear") or RENEWAL_MEMBERSHIP_YEAR)
            if not start:
                continue
            linked.update(
                paymentStatus="paid",
                membDateFrom=linked.get("membDateFrom") or start.isoformat(),
                membDateTo=linked.get("membDateTo") or end.isoformat(),
                paidDate=linked.get("paidDate") or now_iso(),
                updatedAt=now_iso(),
            )
            table.put_item(Item=linked)
            sync_member_flag(str(linked.get("email", "")).strip().lower(), key["memberid"], active=True)
            paid.append(linked)
        except ClientError as exc:
            print(f"Linked transaction activation error for {key}: {exc}")
    return paid


def handle_update_npo_transaction(event: dict, memberid: str, txn_no: str):
    if not MEMBERSHIP_TABLE:
        return respond(500, {"error": "Membership table not configured"})
    body = parse_json_body(event)
    try:
        existing = dynamodb.Table(MEMBERSHIP_TABLE).get_item(
            Key={"memberid": memberid, "transactionNo": txn_no}
        ).get("Item")
    except ClientError as exc:
        print(f"NPO transaction fetch error for {memberid}/{txn_no}: {exc}")
        return respond(500, {"error": "Failed to fetch transaction"})
    if not existing:
        return respond(404, {"error": "Transaction not found"})
    if body.get("action") == "cancel":
        return cancel_npo_transaction(existing, body)

    txn = {**existing, "updatedAt": now_iso()}
    # A cancelled membership stays cancelled (its dates must not come back); only notes can change.
    editable = ("notes",) if existing.get("paymentStatus") == "cancelled" else (
        "membDateFrom", "membDateTo", "membershipAmountPaid", "paidDate",
        "paidVia", "email", "membershipYear", "notes", "paymentStatus")
    for field in editable:
        if field in body:
            txn[field] = body[field]

    # Marking a My Page application paid is what starts the membership: stamp
    # the paid date and the membership-year period, and activate the member.
    activate = txn.get("paymentStatus") == "paid" and existing.get("paymentStatus") == "pending"
    emailed = False
    # A payment-only record (payer already covered, paying for a spouse) never
    # starts a membership of its own.
    payment_only = txn.get("kind") == "spouse-payment"
    if activate:
        txn["paidDate"] = txn.get("paidDate") or now_iso()
        if not payment_only:
            start, end = npo_membership.year_span(txn.get("membershipYear") or RENEWAL_MEMBERSHIP_YEAR)
            if not start:
                return respond(400, {"error": "Transaction has no valid membership year"})
            txn["membDateFrom"] = txn.get("membDateFrom") or start.isoformat()
            txn["membDateTo"] = txn.get("membDateTo") or end.isoformat()

    try:
        dynamodb.Table(MEMBERSHIP_TABLE).put_item(Item=txn)
    except ClientError as exc:
        print(f"NPO transaction update error for {memberid}/{txn_no}: {exc}")
        return respond(500, {"error": "Failed to update transaction"})

    if activate and not payment_only and str(txn.get("email", "")).strip():
        sync_member_flag(str(txn["email"]).strip().lower(), memberid, active=True)
    if activate:
        txn["linkedPaid"] = _activate_linked(txn)
        # The member gets the same "payment received" email as an automatic (Square) payment.
        try:
            emailed = bool(npo_payment_logic.send_paid_email(txn, notify_admin=False))
        except Exception as exc:  # the payment is recorded; a mail problem must not fail the request
            print(f"Paid-confirmation email error for {memberid}/{txn_no}: {exc}")
        txn.pop("linkedPaid", None)  # in-memory only; not part of the admin response

    return respond(200, {"transaction": txn, "emailed": emailed})


REFUND_MODES = ("square", "manual", "none")


def _cancel_one(txn: dict, *, reason: str, now: str) -> dict:
    """Mark one paid transaction cancelled. Its membership dates move to
    cancelledMembDateFrom/To and are cleared, so — exactly like a pending
    request — it no longer counts as a membership anywhere."""
    cancelled = {**txn, "paymentStatus": "cancelled", "cancelledAt": now, "updatedAt": now}
    if reason:
        cancelled["cancelReason"] = reason
    for field, saved in (("membDateFrom", "cancelledMembDateFrom"), ("membDateTo", "cancelledMembDateTo")):
        if cancelled.get(field):
            cancelled[saved] = cancelled.pop(field)
    dynamodb.Table(MEMBERSHIP_TABLE).put_item(Item=cancelled)
    return cancelled


def _refresh_member_flag(memberid: str, email: str):
    """After a cancellation the member is only 'active' if another period still covers today."""
    summary = npo_membership.evaluate(
        get_transactions(memberid), as_of=MEMBERSHIP_AS_OF, renewal_year=RENEWAL_MEMBERSHIP_YEAR
    )
    sync_member_flag(email, memberid, active=bool(summary["validNow"]))


def cancel_npo_transaction(existing: dict, body: dict):
    """Cancel a paid membership order and (optionally) refund it.

    body: {"action": "cancel", "refund": "square"|"manual"|"none",
           "refundAmount": int (default: the order total), "reason": str, "notify": bool}
    - "square"  refunds the card payment through Square (full or partial);
    - "manual"  records a refund the committee already paid out (bank / PayPay);
    - "none"    cancels without a refund.
    Cancelling the payer's transaction also cancels the spouse's linked one.
    """
    memberid, txn_no = str(existing["memberid"]), str(existing["transactionNo"])
    status = existing.get("paymentStatus")
    if status == "cancelled":
        return respond(409, {"error": "This transaction is already cancelled."})
    if status == "pending":
        return respond(409, {"error": "Nothing was paid for this request, so there is nothing to cancel — delete it instead."})
    if existing.get("paidByEmail") and not existing.get("linked"):
        return respond(409, {
            "error": f"This membership was paid for by {existing['paidByEmail']}. Cancel it from their transaction so the refund covers the whole order."
        })

    mode = str(body.get("refund") or "none")
    if mode not in REFUND_MODES:
        return respond(400, {"error": "Choose how to refund: card, already refunded manually, or no refund."})
    order_total = int(existing.get("orderTotal") or existing.get("membershipAmountPaid") or 0)
    amount = order_total
    if mode != "none":
        try:
            amount = int(body.get("refundAmount") if body.get("refundAmount") not in (None, "") else order_total)
        except (TypeError, ValueError):
            return respond(400, {"error": "The refund amount must be a whole number of yen."})
        if amount <= 0 or amount > order_total:
            return respond(400, {"error": f"The refund must be between ¥1 and ¥{order_total:,}."})
    reason = str(body.get("reason") or "").strip()[:300]
    now = now_iso()

    refund = {"refundStatus": "none"}
    if mode == "square":
        if existing.get("paidVia") != "square" or not existing.get("paymentReference"):
            return respond(409, {"error": "This wasn't paid by card through the site, so it can't be refunded automatically. Refund it yourself and choose \"already refunded\"."})
        try:
            result = square.create_refund(
                payment_id=existing["paymentReference"],
                idempotency_key=f"npo-{memberid}-{txn_no}-refund",  # same key on a retry → never refunds twice
                amount_yen=amount,
                reason=reason or "NPO membership cancelled",
            )
        except Exception as exc:  # nothing has been changed yet, so the admin can simply retry
            print(f"Square refund error for {memberid}/{txn_no}: {exc}")
            return respond(502, {"error": "Square couldn't process the refund, so nothing was cancelled. Please try again or refund it in the Square dashboard."})
        refund = {"refundStatus": "processing" if result["status"] in ("PENDING", "") else result["status"].lower(),
                  "refundId": result["refundId"]}
    elif mode == "manual":
        refund = {"refundStatus": "refunded-manually"}
    if mode != "none":
        refund.update(refundAmount=amount, refundedAt=now)

    try:
        primary = _cancel_one({**existing, **refund}, reason=reason, now=now)
    except ClientError as exc:
        print(f"NPO cancel write error for {memberid}/{txn_no}: {exc}")
        return respond(500, {"error": "The refund went through but we couldn't save the cancellation. Please cancel it again (the refund won't be repeated)."})

    cancelled_spouses = []
    for link in existing.get("linked") or []:
        key = {"memberid": str(link.get("memberid", "")), "transactionNo": str(link.get("transactionNo", ""))}
        try:
            linked = dynamodb.Table(MEMBERSHIP_TABLE).get_item(Key=key).get("Item")
            if linked and linked.get("paymentStatus") not in ("cancelled", "pending"):
                cancelled_spouses.append(_cancel_one({**linked, "refundStatus": "included-in-order-refund" if mode != "none" else "none"},
                                                     reason=reason, now=now))
            elif linked and linked.get("paymentStatus") == "pending":
                _cancel_one(linked, reason=reason, now=now)
        except ClientError as exc:
            print(f"Linked cancel error for {key}: {exc}")

    if existing.get("kind") != "spouse-payment":
        _refresh_member_flag(memberid, str(existing.get("email", "")).strip().lower())
    for spouse in cancelled_spouses:
        _refresh_member_flag(str(spouse["memberid"]), str(spouse.get("email", "")).strip().lower())

    if body.get("notify", True):
        try:
            npo_payment_logic.send_cancelled_email(primary, cancelled_spouses)
        except Exception as exc:  # the cancellation is recorded; a mail problem must not fail the request
            print(f"Cancellation email error for {memberid}/{txn_no}: {exc}")
    return respond(200, {"transaction": primary, "cancelledLinked": len(cancelled_spouses)})


def handle_delete_npo_transaction(_event: dict, memberid: str, txn_no: str):
    if not MEMBERSHIP_TABLE:
        return respond(500, {"error": "Membership table not configured"})
    try:
        dynamodb.Table(MEMBERSHIP_TABLE).delete_item(
            Key={"memberid": memberid, "transactionNo": txn_no}
        )
    except ClientError as exc:
        print(f"NPO transaction delete error for {memberid}/{txn_no}: {exc}")
        return respond(500, {"error": "Failed to delete transaction"})
    return respond(200, {"deleted": True, "memberid": memberid, "transactionNo": txn_no})


# ── Router ────────────────────────────────────────────────────────────────────

# ── Member announcements ──────────────────────────────────────────────────────
# The send itself runs in the member_announcer Lambda (thousands of emails take
# minutes); this handler previews the audience, queues the job and lists history.

ANNOUNCER_FUNCTION = os.environ.get("MEMBER_ANNOUNCER_FUNCTION", "")


def handle_list_announcements():
    if not announcement_logic.CAMPAIGNS_TABLE:
        return respond(500, {"error": "Announcements are not configured"})
    return respond(200, {"campaigns": announcement_logic.list_campaigns()})


def handle_send_announcement(event: dict):
    body = parse_json_body(event)
    kind = str(body.get("type", "")).strip()
    if kind not in announcement_logic.TYPES:
        return respond(400, {"error": f"type must be one of: {', '.join(announcement_logic.TYPES)}"})
    if not (announcement_logic.CAMPAIGNS_TABLE and ANNOUNCER_FUNCTION):
        return respond(500, {"error": "Announcements are not configured"})

    agm = None
    if kind == "agm":
        agm = announcement_logic.announceable_agm()
        if not agm:
            return respond(409, {"error": "There is no upcoming AGM to announce. Add one in the AGM tab first."})

    link = ""
    if kind == "whatsapp":
        link = announcement_logic.whatsapp_link()
        if not link:
            return respond(409, {"error": f"Set the {announcement_logic.RENEWAL_YEAR} WhatsApp group link in the NPO Membership tab first."})

    recipients = announcement_logic.audience(kind)
    preview = {"type": kind, "recipientCount": len(recipients), "sample": [r["email"] for r in recipients[:5]]}
    if agm:
        preview["agm"] = {"title": agm.get("title", ""), "date": agm.get("date", "")}
    if link:
        preview["whatsappLink"] = link
    if body.get("dryRun"):
        return respond(200, preview)
    if not recipients:
        return respond(409, {"error": "There is nobody to send this announcement to."})
    if announcement_logic.active_campaign(kind):
        return respond(409, {"error": "This announcement is already being sent. Check its progress below."})

    claims = ((event.get("requestContext") or {}).get("authorizer") or {}).get("claims") or {}
    campaign = announcement_logic.create_campaign(kind, len(recipients), str(claims.get("email", "")))
    try:
        boto3.client("lambda", region_name=os.environ["REGION"]).invoke(
            FunctionName=ANNOUNCER_FUNCTION,
            InvocationType="Event",
            Payload=json.dumps({"campaignId": campaign["campaignId"], "type": kind}).encode("utf-8"),
        )
    except ClientError as exc:
        print(f"Could not start member announcer: {exc}")
        announcement_logic.update_campaign(campaign["campaignId"], status="failed", error="Could not start the send")
        return respond(500, {"error": "Could not start sending. Please try again."})
    return respond(202, {"campaign": campaign, **preview})


def lambda_handler(event: dict, _context):
    method = event.get("httpMethod", "")
    path = event.get("path", "")
    params = event.get("pathParameters") or {}

    if method == "OPTIONS":
        return respond(200, {})

    # /admin/members/{email} — item operations
    if params.get("email"):
        email = params["email"]
        if method == "GET":
            return handle_get_member(event, email)
        if method in ("POST", "PUT"):
            return handle_upsert_member(event, email_path=email)
        if method == "DELETE":
            return handle_delete_member(event, email)
        return respond(405, {"error": "Method not allowed"})

    # /admin/npo-membership/{memberid}/{transactionNo} — transaction item
    if params.get("memberid") and params.get("transactionNo"):
        memberid = params["memberid"]
        txn_no = params["transactionNo"]
        if method == "PUT":
            return handle_update_npo_transaction(event, memberid, txn_no)
        if method == "DELETE":
            return handle_delete_npo_transaction(event, memberid, txn_no)
        return respond(405, {"error": "Method not allowed"})

    # /admin/npo-membership/{memberid} — member's transactions
    if params.get("memberid"):
        memberid = params["memberid"]
        if method == "GET":
            return handle_list_npo_transactions(event, memberid)
        if method == "POST":
            return handle_create_npo_transaction(event, memberid)
        return respond(405, {"error": "Method not allowed"})

    # /admin/member-announcements — send / list member announcements
    if path.rstrip("/").endswith("/member-announcements"):
        if method == "GET":
            return handle_list_announcements()
        if method == "POST":
            return handle_send_announcement(event)
        return respond(405, {"error": "Method not allowed"})

    # /admin/members — collection
    if "/members" in path:
        if method == "GET":
            return handle_list_members(event)
        if method == "POST":
            return handle_upsert_member(event)
        return respond(405, {"error": "Method not allowed"})

    return respond(404, {"error": "Not found"})
