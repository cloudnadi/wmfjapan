"""
Lambda: public Square webhook receiver for NPO membership payments (My Page).

  POST /webhooks/npo-membership/{provider}     provider = "square"

Square-only: bank transfer and PayPay are confirmed by an admin. Unauthenticated
by design; trust comes from Square's HMAC signature (401 if it fails). Always
200 once an event has been looked at, so Square doesn't retry forever.
"""

import json
import logging
import os
from typing import Any

import npo_payment_logic as npo
import square

logger = logging.getLogger(__name__)
logger.setLevel(os.environ.get("LOG_LEVEL", "INFO").upper())

SQUARE_WEBHOOK_URL = os.environ.get("SQUARE_WEBHOOK_URL", "")


def respond(status: int, body: dict) -> dict:
    return {"statusCode": status, "headers": {"Content-Type": "application/json"}, "body": json.dumps(body)}


def lambda_handler(event: dict, context: Any) -> dict:
    provider = str((event.get("pathParameters") or {}).get("provider", "")).strip().lower()
    if provider != "square":
        return respond(404, {"error": f"Unknown payment provider: {provider!r}"})

    raw_body = event.get("body") or ""
    headers = {str(k).lower(): v for k, v in (event.get("headers") or {}).items()}
    signature = headers.get("x-square-hmacsha256-signature", "")
    if not square.verify_webhook_signature(SQUARE_WEBHOOK_URL, raw_body, signature):
        logger.warning("Square webhook signature verification failed")
        return respond(401, {"error": "Invalid signature"})

    parsed = square.parse_webhook_event(raw_body)
    if parsed.get("status") in ("FAILED", "CANCELED") and parsed.get("orderId"):
        # The member sees "Retry Payment" on their membership page.
        npo.mark_attempt_failed_from_provider(parsed["orderId"])
    if parsed.get("status") == "COMPLETED" and parsed.get("orderId"):
        updated, newly_paid = npo.mark_paid_from_provider(
            parsed["orderId"], payment_reference=parsed.get("paymentId", "")
        )
        if updated and newly_paid:
            npo.send_paid_email(updated)
    return respond(200, {"ok": True})
