"""
Member announcements: who receives each one, and the campaign records.

Four announcements, sent from the admin Members tab:

  agm    every member            — the upcoming AGM and how to register
  join   every member            — register / renew as an NPO member
                                   (the email says to ignore it if already done)
  renew  existing NPO members    — covered for the current membership year but
         not yet renewed           not for the renewal year (and without a
                                   renewal already awaiting payment)
  whatsapp  members covered for the renewal year — the members' WhatsApp group
            invite link set for that year in Admin → NPO Membership

Shared by the manage_members Lambda (recipient preview + queueing a send) and
the member_announcer worker Lambda (the send itself). Sending is a background
job because the member list is in the thousands — far past an API request.
"""

import json
import logging
import os
import re
import uuid
from datetime import datetime, timedelta, timezone

import boto3
from botocore.exceptions import ClientError

import npo_membership

logger = logging.getLogger(__name__)

dynamodb = boto3.resource("dynamodb", region_name=os.environ["REGION"])
MEMBERS_TABLE = os.environ.get("MEMBERS_TABLE", "")
MEMBERSHIP_TABLE = os.environ.get("MEMBERSHIP_TABLE", "")
AGMS_TABLE = os.environ.get("AGMS_TABLE", "")
CAMPAIGNS_TABLE = os.environ.get("MEMBER_ANNOUNCEMENTS_TABLE", "")
RENEWAL_YEAR = os.environ.get("RENEWAL_MEMBERSHIP_YEAR", "2026-27")
# The year existing members are renewing *from* (the one before the renewal year).
CURRENT_YEAR = os.environ.get("CURRENT_MEMBERSHIP_YEAR", "2025-26")

TYPES = ("agm", "join", "renew", "whatsapp")
# Used for a membership year with no link saved in Admin → NPO Membership.
DEFAULT_WHATSAPP_LINKS = {"2026-27": "https://chat.whatsapp.com/JLYiFD4voZaLx5RRv6oLGO?s=cl&p=i&mlu=4&ilr=4&iam=2"}
SITE_CONFIG_TABLE = os.environ.get("SITE_CONFIG_TABLE", "")
EMAIL_REGEX = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
ACTIVE_STATUSES = ("queued", "sending")
LOCK_MINUTES = 30  # a stuck "sending" campaign stops blocking a resend after this


def scan_all(table):
    items, kwargs = [], {}
    while True:
        resp = table.scan(**kwargs)
        items.extend(resp.get("Items", []))
        if "LastEvaluatedKey" not in resp:
            return items
        kwargs["ExclusiveStartKey"] = resp["LastEvaluatedKey"]


def today_jst():
    return datetime.now(timezone(timedelta(hours=9))).date().isoformat()


def covers_year(transactions, year_code):
    start, _ = npo_membership.year_span(year_code)
    return bool(start) and any(npo_membership._period_covers(t, start) for t in transactions)


def has_pending_renewal(transactions):
    return any(t.get("paymentStatus") == "pending" and t.get("membershipYear") == RENEWAL_YEAR for t in transactions)


def audience(kind):
    """Recipients for `kind`: a list of {email, name, validUntil} (deduplicated by email)."""
    if kind not in TYPES:
        raise ValueError(f"Unknown announcement type: {kind}")
    members = scan_all(dynamodb.Table(MEMBERS_TABLE))
    by_member = {}
    if kind in ("renew", "whatsapp"):
        for txn in scan_all(dynamodb.Table(MEMBERSHIP_TABLE)):
            by_member.setdefault(str(txn.get("memberid", "")).strip(), []).append(txn)

    seen, recipients = set(), []
    for member in members:
        email = str(member.get("email", "")).strip().lower()
        if not EMAIL_REGEX.match(email) or email in seen:
            continue
        txns = by_member.get(str(member.get("memberid", "")).strip(), [])
        valid_until = ""
        if kind == "whatsapp":
            if not covers_year(txns, RENEWAL_YEAR):
                continue
        elif kind == "renew":
            if not covers_year(txns, CURRENT_YEAR) or covers_year(txns, RENEWAL_YEAR) or has_pending_renewal(txns):
                continue
            valid_until = npo_membership.evaluate(txns, renewal_year=RENEWAL_YEAR)["latestTo"]
        seen.add(email)
        recipients.append({"email": email, "name": member.get("fullname", ""), "validUntil": valid_until})
    return recipients


def whatsapp_link(year=None):
    """The WhatsApp group link for `year` (default: the renewal year): the one saved in Admin → NPO
    Membership, else the built-in default."""
    year = year or RENEWAL_YEAR
    if not SITE_CONFIG_TABLE:
        return DEFAULT_WHATSAPP_LINKS.get(year, "")
    try:
        item = dynamodb.Table(SITE_CONFIG_TABLE).get_item(Key={"configKey": "npo_membership_config"}).get("Item")
        links = (json.loads((item or {}).get("value") or "{}") or {}).get("whatsappLinks") or {}
        return str(links.get(year) or DEFAULT_WHATSAPP_LINKS.get(year, ""))
    except (ClientError, TypeError, ValueError, AttributeError):
        logger.exception("Could not read the WhatsApp group link")
        return DEFAULT_WHATSAPP_LINKS.get(year, "")


def announceable_agm():
    """The AGM to announce: the soonest one that hasn't happened yet, or None."""
    if not AGMS_TABLE:
        return None
    upcoming = [a for a in scan_all(dynamodb.Table(AGMS_TABLE)) if (a.get("date") or "9999-12-31") >= today_jst()]
    upcoming.sort(key=lambda a: a.get("date") or "9999-12-31")
    return upcoming[0] if upcoming else None


# ── Campaign records ─────────────────────────────────────────────────────────


def create_campaign(kind, recipient_count, requested_by=""):
    campaign = {
        "campaignId": str(uuid.uuid4()),
        "type": kind,
        "status": "queued",
        "recipientCount": recipient_count,
        "sent": 0,
        "failed": 0,
        "failedSample": [],
        "requestedBy": requested_by,
        "createdAt": datetime.now(timezone.utc).isoformat(),
    }
    dynamodb.Table(CAMPAIGNS_TABLE).put_item(Item=campaign)
    return campaign


def update_campaign(campaign_id, **fields):
    names = {f"#{k}": k for k in fields}
    values = {f":{k}": v for k, v in fields.items()}
    expression = "SET " + ", ".join(f"#{k} = :{k}" for k in fields)
    dynamodb.Table(CAMPAIGNS_TABLE).update_item(
        Key={"campaignId": campaign_id},
        UpdateExpression=expression,
        ExpressionAttributeNames=names,
        ExpressionAttributeValues=values,
    )


def get_campaign(campaign_id):
    return dynamodb.Table(CAMPAIGNS_TABLE).get_item(Key={"campaignId": campaign_id}).get("Item")


def list_campaigns(limit=20):
    items = scan_all(dynamodb.Table(CAMPAIGNS_TABLE))
    items.sort(key=lambda c: c.get("createdAt", ""), reverse=True)
    return items[:limit]


def active_campaign(kind):
    """A campaign of this type still queued/sending (and recent) — blocks double-sends."""
    cutoff = (datetime.now(timezone.utc) - timedelta(minutes=LOCK_MINUTES)).isoformat()
    for campaign in list_campaigns(limit=100):
        if campaign.get("type") == kind and campaign.get("status") in ACTIVE_STATUSES and campaign.get("createdAt", "") >= cutoff:
            return campaign
    return None
