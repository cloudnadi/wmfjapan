"""
Lambda: public payment-provider webhook receiver for the standalone
Celebrity Dinner sign-up.

  POST /webhooks/celebrity-dinner/{provider}     provider = "paypay" | "square"

Mirrors plugins/seat-upgrade/backend/lambdas/seat_upgrade_webhook/handler.py
almost exactly — see that file's docstring for why Square is trusted via
signature verification while PayPay is only ever treated as a hint to
re-query PayPay's own status API. A separate Lambda/endpoint from the
seat-upgrade plugin's own webhook (distinct URL, so each has its own Square
webhook subscription and signing key) since these are two independently
payable products even though they share the same PayPay/Square merchant
credentials.

Unauthenticated by design (a payment provider can't carry our reg token or
a Cognito credential). Always returns 200 once an event has been durably
looked at, so the provider doesn't retry forever; the one exception is a
Square request that fails signature verification (401), so a real
misconfiguration is loud instead of silently swallowed.
"""

import json
import logging
import os
from typing import Any

import paypay
import celebrity_dinner_emailer as dinner_emailer
import celebrity_dinner_logic as dinner
import square

logger = logging.getLogger(__name__)
logger.setLevel(os.environ.get("LOG_LEVEL", "INFO").upper())

SQUARE_WEBHOOK_URL = os.environ.get("SQUARE_WEBHOOK_URL", "")


def respond(status: int, body: dict) -> dict:
    return {
        "statusCode": status,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps(body),
    }


def handle_paypay(raw_body: str) -> dict:
    merchant_payment_id = paypay.handle_notification(raw_body)
    if not merchant_payment_id:
        logger.info("PayPay notification carried no merchantPaymentId; ignoring")
        return respond(200, {"ok": True, "ignored": True})

    try:
        status = paypay.get_payment_status(merchant_payment_id)
    except Exception:
        logger.exception("Could not verify PayPay payment status for %s", merchant_payment_id)
        # Still 200: PayPay would just retry the same unhelpful notification.
        # The registrant's own return-page visit (my_registrations handler's
        # _reconcile_dinner_with_provider) is the backstop if this keeps failing.
        return respond(200, {"ok": False})

    if status.get("status") == "COMPLETED":
        updated, was_newly_paid = dinner.mark_paid_from_provider(merchant_payment_id, payment_reference=status.get("paymentId", ""))
        if updated and was_newly_paid:
            dinner_emailer.send_paid_email(updated)
            dinner_emailer.send_admin_notification(updated, "paid")
    return respond(200, {"ok": True})


def handle_square(headers: dict, raw_body: str) -> dict:
    signature = headers.get("x-square-hmacsha256-signature") or headers.get("X-Square-Hmacsha256-Signature") or ""
    if not square.verify_webhook_signature(SQUARE_WEBHOOK_URL, raw_body, signature):
        logger.warning("Square webhook signature verification failed")
        return respond(401, {"error": "Invalid signature"})

    event = square.parse_webhook_event(raw_body)
    if event.get("status") == "COMPLETED" and event.get("orderId"):
        updated, was_newly_paid = dinner.mark_paid_from_provider(event["orderId"], payment_reference=event.get("paymentId", ""))
        if updated and was_newly_paid:
            dinner_emailer.send_paid_email(updated)
            dinner_emailer.send_admin_notification(updated, "paid")
    return respond(200, {"ok": True})


def lambda_handler(event: dict, context: Any) -> dict:
    path_params = event.get("pathParameters") or {}
    provider = str(path_params.get("provider", "")).strip().lower()
    raw_body = event.get("body") or ""
    headers = {str(k).lower(): v for k, v in (event.get("headers") or {}).items()}

    if provider == "paypay":
        return handle_paypay(raw_body)
    if provider == "square":
        return handle_square(headers, raw_body)

    return respond(404, {"error": f"Unknown payment provider: {provider!r}"})
