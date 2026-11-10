# Seat Upgrade Plugin

A self-contained add-on that lets registrants pay to upgrade their seating
for an event into an admin-priced zone — with online checkout via PayPay or
Square, or bank transfer confirmed by an admin.

```
plugins/seat-upgrade/
├── README.md                    ← you are here
├── backend/
│   ├── lambdas/
│   │   ├── seat_upgrade_logic.py       shared data-layer logic
│   │   ├── manage_seat_upgrades/       admin Lambda (Cognito-protected)
│   │   ├── seat_upgrade/               registrant self-service Lambda
│   │   ├── seat_upgrade_webhook/       payment-provider webhook receiver
│   │   ├── paypay.py                   PayPay OpenPaymentAPI adapter
│   │   └── square.py                   Square Checkout API adapter
│   └── terraform/               the module — see "Mounting this plugin"
├── frontend/
│   ├── SeatUpgrade.jsx                 registrant self-service page
│   ├── SeatUpgradeManagement.jsx       admin dashboard tab
│   └── api.js                          this plugin's own API client
└── docs/
    ├── SEAT_UPGRADE_FEATURE.md         data model, business rules, routes
    └── PAYMENTS.md                     PayPay/Square setup + caveats
```

## What it owns vs. what it borrows

**Owns, independently of the host app:** two DynamoDB tables
(`seat_upgrade_tiers`, `seat_upgrades`), three Lambdas, and its own slice of
the REST API. Nothing here is written into the host's own registrations
table — every record only ever references a registration by id, the same
arm's-length relationship an entry pass has to its registration.

**Borrows from the host, deliberately, at exactly these seams:**
- Its two self-service routes (`/my/seat-upgrades`, `/my/seat-upgrades/{id}`)
  trust the same reg-token the host's login flow mints (`otp_token_secret`
  passed into the module) — a registrant who's already logged into the host
  app doesn't log in again for this.
- Its admin routes sit behind the host's existing Cognito authorizer.
- The frontend imports the host's `Navbar` and auth helpers via the `@app`
  alias (see `frontend/vite.config.js`) rather than duplicating them.

Everything else — pricing, capacity accounting, payment adapters, the admin
UI — is this plugin's own.

## Mounting this plugin

1. **Terraform**: add a module block pointing at
   `plugins/seat-upgrade/backend/terraform`. See `backend/plugin.tf` in this
   repo for the reference wiring — every input the module needs (REST API
   id, a parent resource to mount `/admin/events/{eventType}/...` under, a
   parent resource for `/my/...`, the Cognito authorizer id, the shared
   Lambda role, etc.) is documented in `backend/terraform/variables.tf`.
2. Fold `module.seat_upgrade.api_trigger_hash` into your own
   `aws_api_gateway_deployment`'s trigger hash, and add
   `module.seat_upgrade` to its `depends_on` — see `backend/api_gateway.tf`
   for exactly how.
3. **Frontend**: add the two aliases from `frontend/vite.config.js` (`@app`
   → your `src/`, `@plugins` → the `plugins/` directory), then import
   `SeatUpgrade` and `SeatUpgradeManagement` wherever your own routing and
   admin dashboard need them.
4. **Payments**: optional at first — the plugin deploys and the bank-transfer
   path works with zero payment configuration. See `docs/PAYMENTS.md` before
   turning on PayPay or Square.

## Why a Terraform module instead of a copy-paste template

A module has one real coupling surface (its `variables.tf`/`outputs.tf`) that
Terraform itself checks — a copy-pasted set of `.tf` files invites silent
drift the moment either copy gets a fix the other doesn't. Extracting this
one plugin first, before there's a second host app to prove it against, is
what makes "market this as a separate plugin" more than a filesystem
reshuffle: the module boundary is the thing that would actually have to hold
up if this were mounted somewhere else.

## Read next

- `docs/SEAT_UPGRADE_FEATURE.md` — data model, business rules (family vs.
  per-person pricing, capacity accounting), and the full API surface.
- `docs/PAYMENTS.md` — what PayPay and Square each need, what's already
  built vs. what still needs a sandbox pass, and the webhook trust model.
