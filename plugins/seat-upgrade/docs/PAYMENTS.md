# Payments

Three ways a registrant can pay for a seat upgrade. Only one of them needs
any setup below — the other two work the moment the plugin is deployed.

| Method | Setup needed | Confirmation |
|---|---|---|
| Bank transfer | None | Admin marks it paid by hand, same as a registration payment |
| PayPay | PayPay for Business account + API credentials | Webhook, backed up by a return-page status check |
| Square (credit card) | Square account + access token | Webhook (signature-verified), backed up the same way |

## Current status — read this before turning either provider on

Both providers have now had at least one real checkout attempt against
sandbox credentials:

- **Square** — confirmed working end-to-end against a real checkout.
- **PayPay** — the first live attempt returned HTTP 401 UNAUTHORIZED.
  Root cause: `_content_digest()` in `backend/lambdas/paypay.py` was
  inserting a `\n` between the content type and the request body before
  MD5'ing them; PayPay's own SDKs concatenate the two directly with no
  separator, so the digest — and therefore the whole HMAC signature — never
  matched what PayPay computed on their end. That's fixed now, but it's
  from exactly one data point (one 401, one fix, not yet re-tested), so
  budget one more sandbox checkout attempt before trusting it in
  production. If it 401s again, `_authorization_header()` is still the
  first place to look — compare it against PayPay's current developer
  documentation. Importantly, a wrong signature only ever produces an HTTP
  401 from PayPay; it can never misprocess a real payment, so this is safe
  to iterate on.

For the same reason, **PayPay's webhook is deliberately not
signature-verified** — instead of trusting a notification's own contents,
receiving one just triggers a fresh call to PayPay's own status API
(`GET /v2/payments/{id}`) with our credentials, and only *that* response is
trusted. It's a slightly more defensive design than "verify the signature
and trust the body," chosen specifically because the signing scheme itself
hasn't been proven out yet. Square's webhook, by contrast, is verified
properly — that's a mature, trustworthy scheme — and it's still double
checked via `GET /v2/orders/{id}` before marking anything paid.

## Setting up PayPay

1. Register as a PayPay for Business merchant and create OpenPaymentAPI
   credentials in their merchant dashboard (sandbox first).
2. Set these on the `seat_upgrade` module (via a **gitignored**
   `terraform.tfvars` or `-var-file` — never commit real credentials):
   ```hcl
   paypay_api_key      = "..."
   paypay_api_secret   = "..."
   paypay_merchant_id  = "..."
   paypay_env          = "sandbox"   # then "production" when ready
   ```
3. `terraform apply`. No webhook subscription is required to make PayPay
   work at all — see "PayPay's webhook" above — but PayPay's dashboard may
   still want a notification URL on file; point it at
   `{api invoke url}/webhooks/seat-upgrade/paypay` if so.
4. Test a full checkout in sandbox before flipping `paypay_env` to
   `"production"`.

## Setting up Square

1. Create a Square developer account and an application; generate an access
   token (sandbox first) and note your Location id.
2. Set:
   ```hcl
   square_access_token           = "..."
   square_location_id            = "..."
   square_env                    = "sandbox"   # then "production"
   ```
3. `terraform apply`, then note the deployed webhook URL:
   `{api invoke url}/webhooks/seat-upgrade/square`.
4. In the Square developer dashboard, create a webhook subscription pointed
   at that exact URL, subscribed to `payment.updated` (and `payment.created`
   if you want it), and copy its **signature key** into:
   ```hcl
   square_webhook_signature_key = "..."
   square_webhook_url           = "{the exact URL you entered above}"
   ```
   `square_webhook_url` must match byte-for-byte what's on file in Square's
   dashboard — that's the value Square signs against, and the most common
   cause of "valid" webhooks failing verification is this string drifting
   from what Square has (e.g. a CloudFront/custom-domain alias instead of
   the raw execute-api URL Square was actually configured with).
5. `terraform apply` again, then send a test payment through Square's
   sandbox checkout and confirm the webhook lands (CloudWatch logs for the
   `seat_upgrade_webhook` Lambda) and the request flips to `paid`.

## How a checkout actually flows

1. Registrant picks PayPay or Square and submits a request
   (`POST /my/seat-upgrades`) — this reserves the tier capacity immediately,
   before any money has moved, so it can't be oversold by two people
   checking out at once.
2. The frontend immediately calls
   `POST /my/seat-upgrades/{id}/checkout` with the chosen provider, gets
   back a hosted checkout URL, and redirects the browser there.
3. The registrant pays on PayPay's or Square's own page.
4. The provider redirects back to `/my/seat-upgrade?upgradeId=...`. The page
   calls `GET /my/seat-upgrades/{id}` immediately, which re-checks the
   provider directly (`reconcile_with_provider` in `seat_upgrade/handler.py`)
   — so the status is usually already correct by the time the registrant
   sees the page, without waiting on step 5.
5. Independently, the provider's webhook arrives at
   `seat_upgrade_webhook` and marks the request paid if it hasn't been
   already. Both paths call the same idempotent
   `seat_upgrade_logic.mark_paid_from_provider` — whichever gets there
   first wins, the other is a safe no-op.

If a registrant abandons checkout, the request just stays `pending` with the
capacity still held — they can retry with the same or a different provider
from the same page, or cancel it themselves while it's still pending.

## Refunds

Cancelling an already-**paid** request (self-service, `DELETE
/my/seat-upgrades/{id}`) doesn't cancel it outright — it moves to
`refund_requested` and `seat_upgrade/handler.py`'s `attempt_refund()` tries a
provider refund immediately:

- **Square**: `POST /v2/refunds` against the stored Payment id. Square
  refunds settle asynchronously (a successful call returns `PENDING`), so an
  admin currently confirms final settlement in the Square dashboard.
- **PayPay**: `POST /v2/refunds`, sharing `_authorization_header`'s unverified
  signing scheme — same caution as checkout creation applies.
- **Bank transfer**, or any provider call that fails, is recorded as
  `manual_pending` and left for the admin to refund by hand — same as how
  bank-transfer payment itself is confirmed.

Either way, the zone's capacity stays held until an admin finalizes the
request to `cancelled` from the Seat Upgrades admin tab, once the refund has
actually landed. See `docs/SEAT_UPGRADE_FEATURE.md`'s "Refunds" section for
the full field-level detail.
