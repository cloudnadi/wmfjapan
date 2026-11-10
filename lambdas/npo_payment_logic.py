"""
Shared NPO-membership payment logic (membership + npo_membership_webhook Lambdas).

A My Page order can cover the member and their spouse. The payer's ("primary")
transaction carries the checkout and lists the spouse's transaction under
`linked`; marking the primary paid marks every linked one paid too. When the
payer is already covered, their primary is a payment-only record
(`kind == "spouse-payment"`) that never starts a membership of its own.

A My Page sign-up is stored as a *pending* transaction in the npo_membership
table (no membDateFrom/To, so it doesn't count as membership). It becomes real
when it is marked paid, either automatically (Square checkout confirmed by
webhook or return-page check) or by an admin for bank transfer / PayPay.

`mark_paid_from_provider` is idempotent — a conditional write flips
pending -> paid exactly once, so the webhook and the return-page check can race
safely.
"""

import json
import logging
import os
from datetime import datetime, timezone
from html import escape

import boto3
from boto3.dynamodb.conditions import Key
from botocore.exceptions import ClientError

import member_emailer
import npo_membership

logger = logging.getLogger(__name__)

dynamodb = boto3.resource("dynamodb", region_name=os.environ["REGION"])
MEMBERSHIP_TABLE = os.environ.get("MEMBERSHIP_TABLE", "")
MEMBERS_TABLE = os.environ.get("MEMBERS_TABLE", "")
SITE_CONFIG_TABLE = os.environ.get("SITE_CONFIG_TABLE", "")
SES_FROM_EMAIL = os.environ.get("SES_FROM_EMAIL", "")
ADMIN_EMAIL = os.environ.get("ADMIN_EMAIL", "")
RENEWAL_MEMBERSHIP_YEAR = os.environ.get("RENEWAL_MEMBERSHIP_YEAR", "2026-27")
PROVIDER_INDEX = "providerPaymentId-index"
# Used for a membership year with no link saved in Admin → NPO Membership.
DEFAULT_WHATSAPP_LINKS = {"2026-27": "https://chat.whatsapp.com/JLYiFD4voZaLx5RRv6oLGO?s=cl&p=i&mlu=4&ilr=4&iam=2"}


def send_email(to_addresses, subject, html):
    if not SES_FROM_EMAIL or not to_addresses:
        return False
    try:
        boto3.client("ses", region_name=os.environ["REGION"]).send_email(
            Source=SES_FROM_EMAIL,
            Destination={"ToAddresses": to_addresses},
            Message={
                "Subject": {"Data": subject, "Charset": "UTF-8"},
                "Body": {"Html": {"Data": html, "Charset": "UTF-8"}},
            },
        )
        return True
    except ClientError:
        logger.exception("Could not send NPO email to %s", to_addresses)
        return False


def find_by_provider_payment_id(provider_payment_id):
    if not (MEMBERSHIP_TABLE and provider_payment_id):
        return None
    try:
        items = dynamodb.Table(MEMBERSHIP_TABLE).query(
            IndexName=PROVIDER_INDEX,
            KeyConditionExpression=Key("providerPaymentId").eq(provider_payment_id),
        ).get("Items", [])
    except ClientError:
        logger.exception("Could not look up NPO payment %s", provider_payment_id)
        return None
    return items[0] if items else None


def mark_paid(memberid, transaction_no, *, payment_reference=""):
    """Flip one pending transaction to paid and start the membership.

    Returns (transaction, newly_paid). newly_paid is False if it was already
    paid (or no longer exists), so callers only email once.
    """
    table = dynamodb.Table(MEMBERSHIP_TABLE)
    try:
        existing = table.get_item(Key={"memberid": memberid, "transactionNo": transaction_no}).get("Item")
    except ClientError:
        logger.exception("Could not read NPO transaction %s/%s", memberid, transaction_no)
        return None, False
    if not existing:
        return None, False
    if existing.get("paymentStatus") != "pending":
        return existing, False

    payment_only = existing.get("kind") == "spouse-payment"
    start, end = npo_membership.year_span(existing.get("membershipYear") or RENEWAL_MEMBERSHIP_YEAR)
    if not start and not payment_only:
        logger.error("NPO transaction %s/%s has no valid membership year", memberid, transaction_no)
        return existing, False
    now = datetime.now(timezone.utc).isoformat()
    values = {":paid": "paid", ":pending": "pending", ":now": now, ":ref": payment_reference or ""}
    expression = "SET paymentStatus = :paid, paidDate = :now, paymentReference = :ref, updatedAt = :now"
    if not payment_only:
        expression += ", membDateFrom = :f, membDateTo = :t"
        values.update({":f": start.isoformat(), ":t": end.isoformat()})
    try:
        updated = table.update_item(
            Key={"memberid": memberid, "transactionNo": transaction_no},
            UpdateExpression=expression,
            ConditionExpression="paymentStatus = :pending",
            ExpressionAttributeValues=values,
            ReturnValues="ALL_NEW",
        ).get("Attributes")
    except ClientError as exc:
        if exc.response.get("Error", {}).get("Code") == "ConditionalCheckFailedException":
            return existing, False  # someone else (webhook / admin) got there first
        logger.exception("Could not mark NPO transaction %s/%s paid", memberid, transaction_no)
        return existing, False

    email = str(updated.get("email", "")).strip().lower()
    if MEMBERS_TABLE and email and not payment_only:
        try:
            dynamodb.Table(MEMBERS_TABLE).update_item(
                Key={"email": email},
                UpdateExpression="SET npoMembershipActive = :t, memberid = if_not_exists(memberid, :m)",
                ExpressionAttributeValues={":t": True, ":m": str(memberid)},
            )
        except ClientError:
            logger.exception("Could not activate member %s", email)

    # The spouse's membership(s) ordered together with this payment.
    linked_paid = []
    for link in updated.get("linked") or []:
        spouse_txn, newly = mark_paid(
            str(link.get("memberid", "")), str(link.get("transactionNo", "")), payment_reference=payment_reference
        )
        if spouse_txn and newly:
            linked_paid.append(spouse_txn)
    updated["linkedPaid"] = linked_paid  # in-memory only, for send_paid_email
    return updated, True


def mark_attempt_failed(memberid, transaction_no):
    """Flag a still-pending payment as failed (the member can then retry it) and
    email the member once. Repeated failure notices for the same attempt are
    ignored; a retry clears the flag, so a second failure notifies again."""
    try:
        updated = dynamodb.Table(MEMBERSHIP_TABLE).update_item(
            Key={"memberid": memberid, "transactionNo": transaction_no},
            UpdateExpression="SET paymentAttemptStatus = :failed, lastAttemptFailedAt = :now",
            ConditionExpression=(
                "paymentStatus = :pending AND "
                "(attribute_not_exists(paymentAttemptStatus) OR paymentAttemptStatus <> :failed)"
            ),
            ExpressionAttributeValues={
                ":failed": "failed",
                ":pending": "pending",
                ":now": datetime.now(timezone.utc).isoformat(),
            },
            ReturnValues="ALL_NEW",
        ).get("Attributes")
    except ClientError as exc:
        if exc.response.get("Error", {}).get("Code") != "ConditionalCheckFailedException":
            logger.exception("Could not flag NPO payment %s/%s as failed", memberid, transaction_no)
        return False
    send_payment_failed_email(updated or {})
    return True


def send_payment_failed_email(txn):
    """Tell the member their card payment didn't go through and how to retry
    (branded template, shared with the member announcements)."""
    to = str(txn.get("email", "")).strip()
    if not to:
        return False
    name = _member_name(to)
    subject, html = member_emailer.build_payment_failed({"name": name}, txn)
    return member_emailer._send([to], subject, html)


def mark_attempt_failed_from_provider(provider_payment_id):
    txn = find_by_provider_payment_id(provider_payment_id)
    if not txn:
        return False
    return mark_attempt_failed(txn["memberid"], txn["transactionNo"])


def mark_paid_from_provider(provider_payment_id, *, payment_reference=""):
    txn = find_by_provider_payment_id(provider_payment_id)
    if not txn:
        return None, False
    return mark_paid(txn["memberid"], txn["transactionNo"], payment_reference=payment_reference)


def whatsapp_group_link(year):
    """The WhatsApp group invite link for `year`: the one saved in Admin → NPO Membership, else the default."""
    year = str(year or RENEWAL_MEMBERSHIP_YEAR)
    if not SITE_CONFIG_TABLE:
        return DEFAULT_WHATSAPP_LINKS.get(year, "")
    try:
        item = dynamodb.Table(SITE_CONFIG_TABLE).get_item(Key={"configKey": "npo_membership_config"}).get("Item")
        links = (json.loads((item or {}).get("value") or "{}") or {}).get("whatsappLinks") or {}
        return str(links.get(year) or DEFAULT_WHATSAPP_LINKS.get(year, ""))
    except (ClientError, TypeError, ValueError, AttributeError):
        logger.exception("Could not read the WhatsApp group link")
        return DEFAULT_WHATSAPP_LINKS.get(year, "")


def _member_name(email):
    email = str(email or "").strip().lower()
    if not (MEMBERS_TABLE and email):
        return ""
    try:
        return dynamodb.Table(MEMBERS_TABLE).get_item(Key={"email": email}).get("Item", {}).get("fullname", "")
    except ClientError:
        logger.exception("Could not read the member name for %s", email)
        return ""


def send_cancelled_email(txn, cancelled_spouses=()):
    """Branded "membership cancelled" email to the payer and to each spouse
    whose membership was cancelled with the order."""
    payer = str(txn.get("email", "")).strip()
    if payer:
        subject, html = member_emailer.build_membership_cancelled({"name": _member_name(payer)}, txn)
        member_emailer._send([payer], subject, html)
    for spouse_txn in cancelled_spouses:
        spouse = str(spouse_txn.get("email", "")).strip()
        if spouse:
            subject, html = member_emailer.build_membership_cancelled(
                {"name": _member_name(spouse)}, spouse_txn, cancelled_by_payer=payer
            )
            member_emailer._send([spouse], subject, html)


def send_paid_email(txn, *, notify_admin=True):
    """Branded "payment received" email to the payer, a "confirmed" email to each
    spouse whose membership was paid for with it, and (optionally) a note to the
    admin. `txn` is the paid primary transaction; `linkedPaid` lists the spouses."""
    payer = str(txn.get("email", "")).strip()
    link = whatsapp_group_link(txn.get("membershipYear"))
    payer_sent = False
    if payer:
        subject, html = member_emailer.build_payment_confirmed({"name": _member_name(payer)}, txn, whatsapp_url=link)
        payer_sent = bool(member_emailer._send([payer], subject, html))
    for spouse_txn in txn.get("linkedPaid") or []:
        spouse = str(spouse_txn.get("email", "")).strip()
        if spouse:
            subject, html = member_emailer.build_confirmed_by_other(
                {"name": _member_name(spouse)}, spouse_txn, payer, whatsapp_url=link
            )
            member_emailer._send([spouse], subject, html)
    if notify_admin and ADMIN_EMAIL:
        year = str(txn.get("membershipYear", ""))
        amount = int(txn.get("orderTotal") or txn.get("membershipAmountPaid", 0) or 0)
        send_email(
            [ADMIN_EMAIL],
            f"NPO membership paid: {payer}",
            f"<p>{escape(payer)} paid &yen;{amount:,} for NPO membership {escape(year)} via Square.</p>",
        )
    return payer_sent
