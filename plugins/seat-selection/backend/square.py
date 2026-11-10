"""
Square adapter — Checkout API (hosted Payment Links) for credit-card seat
upgrades, plus webhook signature verification.

Dependency-free (stdlib `urllib` only) for the same reason as paypay.py: this
repo's Lambdas ship as flat zipped .py files with no pip-install step, so the
`squareup` SDK isn't an option without adding a packaging pipeline.

Square's Checkout API and webhook signing are stable, long-documented APIs —
unlike paypay.py, nothing here is a from-memory reconstruction of an obscure
signing scheme. Still worth a sandbox smoke test (SQUARE_ENV=sandbox) before
going live, same as any new integration.

Env vars:
  SQUARE_ACCESS_TOKEN — a Square API access token (sandbox or production).
  SQUARE_LOCATION_ID — the Square location the payment is taken against.
  SQUARE_WEBHOOK_SIGNATURE_KEY — from the webhook subscription's settings.
  SQUARE_ENV — "production" or anything else (defaults to sandbox).
"""

import base64
import hashlib
import hmac
import json
import os
import urllib.error
import urllib.request
import uuid

ACCESS_TOKEN = os.environ.get("SQUARE_ACCESS_TOKEN", "")
LOCATION_ID = os.environ.get("SQUARE_LOCATION_ID", "")
WEBHOOK_SIGNATURE_KEY = os.environ.get("SQUARE_WEBHOOK_SIGNATURE_KEY", "")

PRODUCTION_BASE = "https://connect.squareup.com"
SANDBOX_BASE = "https://connect.squareupsandbox.com"
SQUARE_VERSION = "2024-10-17"  # Square requires a pinned API version per request.


def _base_url() -> str:
    return PRODUCTION_BASE if os.environ.get("SQUARE_ENV") == "production" else SANDBOX_BASE


def is_configured() -> bool:
    return bool(ACCESS_TOKEN and LOCATION_ID)


def _request(method: str, path: str, body: dict | None = None) -> dict:
    if not is_configured():
        raise RuntimeError("Square is not configured (missing SQUARE_ACCESS_TOKEN/LOCATION_ID)")

    req = urllib.request.Request(
        f"{_base_url()}{path}",
        data=json.dumps(body).encode("utf-8") if body is not None else None,
        method=method,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {ACCESS_TOKEN}",
            "Square-Version": SQUARE_VERSION,
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Square API error {exc.code}: {detail}") from exc


def create_checkout(*, reference_id: str, amount_yen: int, description: str, redirect_url: str) -> dict:
    """Create a Square-hosted Payment Link. Returns
    {"checkoutUrl", "providerPaymentId" (the Order id), "raw"}.

    We use the Order's id as `providerPaymentId` — not the payment link's own
    id, and not a Payment id (a Payment doesn't exist until someone actually
    pays, so it isn't known yet at creation time). The Order id is: known
    immediately here, and present directly on the Payment object a
    `payment.updated` webhook delivers (`payment.order_id`) — so lookups
    never need an extra round trip to translate one id into another.

    JPY has no minor unit (no "yen and sen" like USD cents) — Square's
    `amount_money.amount` for JPY is the whole-yen integer directly, unlike
    currencies where it's the smallest unit (e.g. cents for USD).
    """
    idempotency_key = str(uuid.uuid4())
    response = _request("POST", "/v2/online-checkout/payment-links", {
        "idempotency_key": idempotency_key,
        "order": {
            "location_id": LOCATION_ID,
            "reference_id": reference_id,
            "line_items": [{
                "name": description[:255],
                "quantity": "1",
                "base_price_money": {"amount": int(amount_yen), "currency": "JPY"},
            }],
        },
        "checkout_options": {
            "redirect_url": redirect_url,
        },
    })
    link = response.get("payment_link") or {}
    checkout_url = link.get("url")
    order_id = link.get("order_id", "")
    if not checkout_url or not order_id:
        raise RuntimeError(f"Square did not return a checkout URL/order id: {response}")
    return {
        "checkoutUrl": checkout_url,
        "providerPaymentId": order_id,
        "raw": response,
    }


def get_payment_status(order_id: str) -> dict:
    """Authoritative status check, keyed by the Order id recorded at checkout
    creation (see create_checkout) — GET /v2/orders/{id}. An Order reaches
    state COMPLETED once Square has captured full payment against it via the
    hosted checkout page.
    """
    response = _request("GET", f"/v2/orders/{order_id}")
    order = response.get("order") or {}
    tenders = order.get("tenders") or []
    return {
        "status": order.get("state", ""),  # OPEN | COMPLETED | CANCELED
        "paymentId": (tenders[0].get("payment_id", "") if tenders else ""),
        "raw": response,
    }


def create_refund(*, payment_id: str, amount_yen: int, reason: str = "") -> dict:
    """Refund a completed payment — POST /v2/refunds. `payment_id` is the
    Square Payment id (not the Order id create_checkout returns as
    providerPaymentId), which is why seat_upgrade_logic stores it separately
    as `paymentReference` once mark_paid_from_provider runs.

    Square refunds are asynchronous: a successful call here returns a refund
    in state PENDING, not COMPLETED — Square settles it shortly after, and
    that final state currently has to be checked back manually in the Square
    dashboard (no seat-upgrade webhook subscribes to refund.updated). Returns
    {"refundId", "status", "raw"}.
    """
    idempotency_key = str(uuid.uuid4())
    body = {
        "idempotency_key": idempotency_key,
        "amount_money": {"amount": int(amount_yen), "currency": "JPY"},
        "payment_id": payment_id,
    }
    if reason:
        body["reason"] = reason[:192]  # Square caps `reason` at 192 characters
    response = _request("POST", "/v2/refunds", body)
    refund = response.get("refund") or {}
    refund_id = refund.get("id", "")
    if not refund_id:
        raise RuntimeError(f"Square did not return a refund id: {response}")
    return {
        "refundId": refund_id,
        "status": refund.get("status", ""),  # PENDING | COMPLETED | REJECTED | FAILED
        "raw": response,
    }


def verify_webhook_signature(notification_url: str, raw_body: str, signature_header: str) -> bool:
    """Square signs `notification_url + raw_body` with HMAC-SHA256 using the
    webhook subscription's signature key, base64-encoded. `notification_url`
    MUST be the exact URL configured in the Square webhook subscription
    (usually the API Gateway invoke URL, not a CloudFront/custom-domain
    alias) — a mismatch here is the most common cause of "valid" webhooks
    failing verification.
    """
    if not WEBHOOK_SIGNATURE_KEY or not signature_header:
        return False
    payload = (notification_url + (raw_body or "")).encode("utf-8")
    expected = base64.b64encode(
        hmac.new(WEBHOOK_SIGNATURE_KEY.encode("utf-8"), payload, hashlib.sha256).digest()
    ).decode("ascii")
    return hmac.compare_digest(expected, signature_header)


def parse_webhook_event(raw_body: str) -> dict:
    """Pull the bits we care about out of a `payment.updated` /
    `payment.created` event. Returns {} if the body isn't one of those."""
    try:
        payload = json.loads(raw_body or "{}")
    except json.JSONDecodeError:
        return {}

    event_type = payload.get("type", "")
    if not event_type.startswith("payment."):
        return {}

    payment = ((payload.get("data") or {}).get("object") or {}).get("payment") or {}
    return {
        "type": event_type,
        "paymentId": payment.get("id", ""),
        "orderId": payment.get("order_id", ""),
        "status": payment.get("status", ""),
    }
