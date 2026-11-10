"""
Lambda: member_announcer (background worker — not behind API Gateway)

Invoked asynchronously by manage_members when an admin sends a member
announcement (see announcement_logic.py for the audiences). It builds the
recipient list, emails each member with the shared announcement template and
records progress on the campaign item so the admin Members tab can show it.

Event: {"campaignId": "...", "type": "agm" | "join" | "renew" | "whatsapp"}
"""

import json
import logging
import os
import time
from datetime import datetime, timezone

import boto3

import announcement_logic as logic
import member_emailer as emailer

logger = logging.getLogger()
logger.setLevel(os.environ.get("LOG_LEVEL", "INFO").upper())

SITE_CONFIG_TABLE = os.environ.get("SITE_CONFIG_TABLE", "")
SEND_INTERVAL_SECONDS = 0.08  # stay under SES's default send rate
PROGRESS_EVERY = 50
FAILED_SAMPLE_LIMIT = 25


def load_fee():
    """The NPO fee from the admin NPO Fee setting (0 → leave the fee out of the email)."""
    if not SITE_CONFIG_TABLE:
        return 0
    try:
        item = boto3.resource("dynamodb", region_name=os.environ["REGION"]).Table(SITE_CONFIG_TABLE).get_item(
            Key={"configKey": "npo_membership_config"}
        ).get("Item")
        return int(json.loads((item or {}).get("value") or "{}").get("fee", 0))
    except Exception:
        logger.exception("Could not read the NPO fee")
        return 0


def build_message(kind, recipient, agm, fee, link=""):
    if kind == "agm":
        return emailer.build_agm(recipient, agm)
    if kind == "whatsapp":
        return emailer.build_whatsapp(recipient, logic.RENEWAL_YEAR, link)
    if kind == "join":
        return emailer.build_join(recipient, logic.RENEWAL_YEAR, fee)
    return emailer.build_renew(recipient, logic.RENEWAL_YEAR, fee)


def lambda_handler(event, _context):
    campaign_id = event["campaignId"]
    kind = event["type"]
    logic.update_campaign(campaign_id, status="sending", startedAt=datetime.now(timezone.utc).isoformat())
    try:
        recipients = logic.audience(kind)
        agm = logic.announceable_agm() if kind == "agm" else None
        if kind == "agm" and not agm:
            raise RuntimeError("There is no upcoming AGM to announce")
        link = logic.whatsapp_link() if kind == "whatsapp" else ""
        if kind == "whatsapp" and not link:
            raise RuntimeError(f"No WhatsApp group link is set for {logic.RENEWAL_YEAR} (Admin → NPO Membership)")
        fee = load_fee() if kind in ("join", "renew") else 0
        logic.update_campaign(campaign_id, recipientCount=len(recipients))

        sent, failed = 0, []
        for index, recipient in enumerate(recipients, 1):
            subject, html = build_message(kind, recipient, agm, fee, link)
            ok = emailer._send([recipient["email"]], subject, html)
            if not ok:  # one retry covers a transient throttle
                time.sleep(1)
                ok = emailer._send([recipient["email"]], subject, html)
            if ok:
                sent += 1
            else:
                failed.append(recipient["email"])
            if index % PROGRESS_EVERY == 0:
                logic.update_campaign(campaign_id, sent=sent, failed=len(failed))
            time.sleep(SEND_INTERVAL_SECONDS)

        logic.update_campaign(
            campaign_id,
            status="done",
            sent=sent,
            failed=len(failed),
            failedSample=failed[:FAILED_SAMPLE_LIMIT],
            finishedAt=datetime.now(timezone.utc).isoformat(),
        )
        return {"sent": sent, "failed": len(failed)}
    except Exception as exc:
        logger.exception("Member announcement %s failed", campaign_id)
        logic.update_campaign(
            campaign_id, status="failed", error=str(exc)[:300], finishedAt=datetime.now(timezone.utc).isoformat()
        )
        raise
