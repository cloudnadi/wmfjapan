"""
Lambda: public payment-provider webhook receiver for the seat-upgrade plugin.

  POST /webhooks/seat-upgrade/{provider}     provider = "paypay" | "square"

Unauthenticated by design (a payment provider can't carry our reg token or a
Cognito credential) — trust instead comes from provider-specific
verification, which the two branches below do very differently on purpose:

  Square: signature-verified (square.verify_webhook_signature). Square's
    HMAC scheme is stable and well-documented, so a body that passes
    verification is trusted directly — that's the entire point of a
    verified webhook. An unverifiable request is rejected outright (401).

  PayPay: NOT signature-verified — see paypay.py's module docstring for why
    (its signing scheme was reconstructed from public SDKs and hasn't been
    exercised against a live endpoint). Instead, a PayPay notification is
    only ever treated as a hint to re-query PayPay's own status API with our
    own credentials; nothing in the notification body itself is trusted.
    That re-query is the actual source of truth, so an unverified signature
    scheme here still can't be used to fake a payment.

Both branches funnel into seat_upgrade_logic.mark_paid_from_provider, which
is idempotent — safe against a provider's routine retry-until-2xx behavior.

Always returns 200 once an event has been durably looked at (even a no-op
one, e.g. an event for a payment no longer pending) so the provider doesn't
retry forever; the one exception is a Square request that fails signature
verification, which returns 401 so a real misconfiguration is loud instead
of silently swallowed.
"""

import json
import logging
import os
from typing import Any

import paypay
import seat_upgrade_emailer
import seat_upgrade_logic
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
        # The registrant's own return-page visit (seat_upgrade handler's
        # reconcile_with_provider) is the backstop if this keeps failing.
        return respond(200, {"ok": False})

    if status.get("status") == "COMPLETED":
        updated, was_newly_paid = seat_upgrade_logic.mark_paid_from_provider(
            merchant_payment_id, payment_reference=status.get("paymentId", "")
        )
        if updated and was_newly_paid:
            seat_upgrade_emailer.send_paid_email(updated)
            seat_upgrade_emailer.send_admin_notification(updated, "paid")
    return respond(200, {"ok": True})


def handle_square(headers: dict, raw_body: str) -> dict:
    signature = headers.get("x-square-hmacsha256-signature") or headers.get("X-Square-Hmacsha256-Signature") or ""
    if not square.verify_webhook_signature(SQUARE_WEBHOOK_URL, raw_body, signature):
        logger.warning("Square webhook signature verification failed")
        return respond(401, {"error": "Invalid signature"})

    event = square.parse_webhook_event(raw_body)
    if event.get("status") == "COMPLETED" and event.get("orderId"):
        updated, was_newly_paid = seat_upgrade_logic.mark_paid_from_provider(
            event["orderId"], payment_reference=event.get("paymentId", "")
        )
        if updated and was_newly_paid:
            seat_upgrade_emailer.send_paid_email(updated)
            seat_upgrade_emailer.send_admin_notification(updated, "paid")
    return respond(200, {"ok": True})


def lambda_handler(event: dict, context: Any) -> dict:
    path_params = event.get("pathParameters") or {}
    provider = str(path_params.get("provider", "")).strip().lower()
    raw_body = event.get("body") or ""
    # API Gateway lower-cases header names in the proxy event, but be
    # defensive since that's a runtime detail, not a documented guarantee.
    headers = {str(k).lower(): v for k, v in (event.get("headers") or {}).items()}

    if provider == "paypay":
        return handle_paypay(raw_body)
    if provider == "square":
        return handle_square(headers, raw_body)

    return respond(404, {"error": f"Unknown payment provider: {provider!r}"})
