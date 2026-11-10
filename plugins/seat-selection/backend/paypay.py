"""
PayPay OpenPaymentAPI adapter — "Web Payment" (hosted redirect checkout).

Deliberately dependency-free (stdlib `urllib` only): this repo's Lambdas are
packaged as flat zipped .py files with no pip-install step (see
backend/lambda.tf's `archive_file` blocks), so a third-party SDK like
`paypayopa` can't be added without introducing a packaging pipeline this repo
doesn't have. Every other integration in this codebase (SES, DynamoDB) uses
what's already in the Lambda runtime; this follows the same rule for the one
piece that isn't already there.

*** VERIFY BEFORE PRODUCTION USE ***
`_authorization_header` reconstructs PayPay's HMAC request-signing scheme
from their publicly published OpenPaymentAPI SDKs. A first live attempt
against real sandbox credentials returned HTTP 401 UNAUTHORIZED — root
cause was `_content_digest` inserting a "\n" between the content type and
the body before MD5'ing them; PayPay's own SDKs concatenate them directly
with no separator, so the digest (and everything downstream of it in the
HMAC) never matched. That's now fixed, but it was found from exactly one
data point (one 401, one fix, not yet re-tested against a live checkout) —
if checkout creation still 401s, this is still the first place to look; a
wrong signature only ever produces an HTTP 401, it can never silently
misprocess a payment.

Env vars:
  PAYPAY_API_KEY, PAYPAY_API_SECRET, PAYPAY_MERCHANT_ID — from the PayPay for
    Business merchant dashboard.
  PAYPAY_ENV — "production" or anything else (defaults to sandbox).
"""

import base64
import hashlib
import hmac
import json
import os
import random
import string
import time
import urllib.error
import urllib.request

API_KEY = os.environ.get("PAYPAY_API_KEY", "")
API_SECRET = os.environ.get("PAYPAY_API_SECRET", "")
MERCHANT_ID = os.environ.get("PAYPAY_MERCHANT_ID", "")

PRODUCTION_BASE = "https://api.paypay.ne.jp"
SANDBOX_BASE = "https://stg-api.sandbox.paypay.ne.jp"


def _base_url() -> str:
    return PRODUCTION_BASE if os.environ.get("PAYPAY_ENV") == "production" else SANDBOX_BASE


def is_configured() -> bool:
    return bool(API_KEY and API_SECRET and MERCHANT_ID)


def _nonce(length: int = 8) -> str:
    return "".join(random.choices(string.ascii_letters + string.digits, k=length))


def _content_digest(content_type: str, body_json: str) -> str:
    """base64(MD5(content_type + body)) — PayPay hashes the request body
    directly onto its content type before it goes into the signature, with
    NO separator between them (confirmed the hard way: an earlier version of
    this function inserted a "\\n" here, which is what produced the very
    HTTP 401 this fix addresses — PayPay's own SDKs concatenate the two
    strings as-is before MD5'ing)."""
    raw = f"{content_type}{body_json}".encode("utf-8")
    return base64.b64encode(hashlib.md5(raw).digest()).decode("ascii")


def _authorization_header(method: str, path_and_query: str, body_json: str = "") -> str:
    """Build the `Authorization: hmac OPA-Auth:...` header PayPay's
    OpenPaymentAPI expects. See the module docstring's *** VERIFY BEFORE
    PRODUCTION USE *** note.
    """
    epoch = str(int(time.time()))
    nonce = _nonce()
    content_type = "application/json;charset=UTF-8" if body_json else "empty"
    content_digest = _content_digest(content_type, body_json) if body_json else "empty"

    signature_raw = "\n".join([path_and_query, method, nonce, epoch, content_type, content_digest])
    mac = base64.b64encode(
        hmac.new(API_SECRET.encode("utf-8"), signature_raw.encode("utf-8"), hashlib.sha256).digest()
    ).decode("ascii")

    return f"hmac OPA-Auth:{API_KEY}:{mac}:{nonce}:{epoch}:{content_digest}"


def _request(method: str, path: str, body: dict | None = None) -> dict:
    if not is_configured():
        raise RuntimeError("PayPay is not configured (missing PAYPAY_API_KEY/SECRET/MERCHANT_ID)")

    body_json = json.dumps(body, separators=(",", ":")) if body is not None else ""
    url = f"{_base_url()}{path}"

    req = urllib.request.Request(
        url,
        data=body_json.encode("utf-8") if body_json else None,
        method=method,
        headers={
            "Content-Type": "application/json;charset=UTF-8",
            "X-ASSUME-MERCHANT": MERCHANT_ID,
            "Authorization": _authorization_header(method, path, body_json),
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"PayPay API error {exc.code}: {detail}") from exc


def create_checkout(*, merchant_payment_id: str, amount_yen: int, description: str, redirect_url: str) -> dict:
    """Create a Web Payment code. Returns {"checkoutUrl", "providerPaymentId", "raw"}.

    `merchant_payment_id` is OUR OWN id (we use the seat-upgrade's upgradeId)
    so a webhook or return-redirect can be matched back to our record without
    a separate lookup table.
    """
    response = _request("POST", "/v2/codes", {
        "merchantPaymentId": merchant_payment_id,
        "amount": {"amount": int(amount_yen), "currency": "JPY"},
        "codeType": "ORDER_QR",
        "orderDescription": description[:255],
        "isAuthorization": False,
        "redirectUrl": redirect_url,
        "redirectType": "WEB_LINK",
    })
    data = response.get("data") or {}
    checkout_url = data.get("url") or data.get("deeplink")
    if not checkout_url:
        raise RuntimeError(f"PayPay did not return a checkout URL: {response}")
    return {
        "checkoutUrl": checkout_url,
        "providerPaymentId": merchant_payment_id,
        "raw": response,
    }


def get_payment_status(merchant_payment_id: str) -> dict:
    """Authoritative status check — GET /v2/payments/{id}.

    Used both by the return-page reconcile call and as the source of truth
    when a notification arrives (see handle_notification below): we never
    trust a webhook payload's own status field, only that PayPay's own API
    confirms it when we ask with our credentials.
    """
    response = _request("GET", f"/v2/payments/{merchant_payment_id}")
    data = response.get("data") or {}
    return {
        "status": data.get("status", ""),  # CREATED | AUTHORIZED | COMPLETED | FAILED | CANCELED | EXPIRED
        "paymentId": data.get("paymentId", ""),
        "raw": response,
    }


def create_refund(*, payment_id: str, merchant_refund_id: str, amount_yen: int, reason: str = "") -> dict:
    """Refund a completed payment — POST /v2/refunds. `payment_id` is
    PayPay's own paymentId (stored as `paymentReference` once
    mark_paid_from_provider runs), not our merchantPaymentId.

    Shares `_authorization_header`'s *** VERIFY BEFORE PRODUCTION USE ***
    caveat (see the module docstring) — a wrong signature here only ever
    produces an HTTP 401, never a duplicate or lost refund, but budget a
    sandbox test before relying on this against real money.
    """
    response = _request("POST", "/v2/refunds", {
        "merchantRefundId": merchant_refund_id,
        "paymentId": payment_id,
        "amount": {"amount": int(amount_yen), "currency": "JPY"},
        "reason": (reason or "Customer requested cancellation")[:255],
    })
    data = response.get("data") or {}
    status = data.get("status", "")
    if not status:
        raise RuntimeError(f"PayPay did not return a refund status: {response}")
    return {
        "refundId": data.get("merchantRefundId", merchant_refund_id),
        "status": status,  # PENDING | SUCCESS | FAILED
        "raw": response,
    }


def handle_notification(raw_body: str) -> str | None:
    """Extract the merchantPaymentId a webhook delivery refers to, if any.

    PayPay's webhook payload shape has historically been less consistently
    documented than the REST API itself, so this only pulls out an id to
    re-query — it deliberately does not trust any status field in the body.
    Returns None if no id could be found (caller should just ignore it).
    """
    try:
        payload = json.loads(raw_body or "{}")
    except json.JSONDecodeError:
        return None
    return (
        payload.get("merchantPaymentId")
        or payload.get("merchant_payment_id")
        or (payload.get("data") or {}).get("merchantPaymentId")
        or None
    )
