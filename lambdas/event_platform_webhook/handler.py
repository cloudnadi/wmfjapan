"""
Lambda: Square webhook for event-platform registrations.

  POST /webhooks/event-registrations/{provider}     provider = "square"

Unauthenticated by design; trust comes from Square's HMAC signature (401 if it fails).
Bank transfer and PayPay are confirmed by an admin. Always 200 once an event has been
looked at so Square does not retry forever; marking paid is idempotent.
"""

import json
import logging
import os
from datetime import datetime

import event_mailer
import event_model
import event_registrations as logic
import event_reg_store as store
import square

logger = logging.getLogger(__name__)
logger.setLevel(os.environ.get("LOG_LEVEL", "INFO").upper())

SQUARE_WEBHOOK_URL = os.environ.get("SQUARE_WEBHOOK_URL", "")


def respond(status, body):
    return {"statusCode": status, "headers": {"Content-Type": "application/json"}, "body": json.dumps(body)}


def _notify(kind, reg, event):
    event_mailer.send(kind, reg, event, {})


def _paid_amount(raw_body):
    try:
        payment = ((json.loads(raw_body).get("data") or {}).get("object") or {}).get("payment") or {}
        return (payment.get("amount_money") or {}).get("amount")
    except (ValueError, AttributeError):
        return None


def lambda_handler(event, context):
    provider = str((event.get("pathParameters") or {}).get("provider", "")).strip().lower()
    if provider != "square":
        return respond(404, {"error": f"Unknown payment provider: {provider!r}"})
    raw_body = event.get("body") or ""
    headers = {str(k).lower(): v for k, v in (event.get("headers") or {}).items()}
    if not square.verify_webhook_signature(SQUARE_WEBHOOK_URL, raw_body, headers.get("x-square-hmacsha256-signature", "")):
        logger.warning("Square webhook signature verification failed")
        return respond(401, {"error": "Invalid signature"})

    logic.store, logic.payments, logic.notify = store, square, _notify
    parsed = square.parse_webhook_event(raw_body)
    order_id = parsed.get("orderId")
    if not order_id:
        return respond(200, {"ok": True})
    reg = store.find_by_provider_id(order_id)
    if not reg:
        return respond(200, {"ok": True})            # not ours (membership / seat orders use other webhooks)
    when = datetime.now(event_model.JST)
    if parsed.get("status") == "COMPLETED":
        if not logic.payment_amount_ok(reg, _paid_amount(raw_body)):
            logic.flag_payment_mismatch(reg, _paid_amount(raw_body), when)
            return respond(200, {"ok": True})
        logic.mark_paid(reg, via="square", reference=parsed.get("paymentId", ""), now=when, event=store.get_event(reg["eventId"]))
    elif parsed.get("status") in ("FAILED", "CANCELED") and reg["status"] == "pending_payment":
        try:
            store.update_registration(reg["registrationId"], {"lastPaymentFailedAt": when.isoformat(timespec="seconds")},
                                      expect_status="pending_payment")
        except logic.StateConflict:
            pass
    return respond(200, {"ok": True})
