# Seat Selection Plugin

A self-contained, theater-style seat-map module for an AWS serverless stack
(API Gateway + Lambda + DynamoDB). It was built for and lives inside
[wmfjapan](../../README.md)'s Celebrity Event, but nothing in here knows
about Onam, registrations, or attendee counts — it only knows about seats,
zones, and an opaque `registrationId` string a host app supplies. That's
what makes it a **plugin**: a second app can mount the Terraform module and
drop in the two frontend components without touching this app's own
registration model.

```
plugins/seat-selection/
├── backend/       Python — seat map + purchase logic, three Lambda handlers
├── frontend/      React — two presentational, prop-driven components
└── terraform/     A reusable module: tables, Lambdas, IAM, API routes
```

## What you get

- A fixed 501-seat, 19-row hall layout (two raked "preferred" zones near the
  stage, vertical aisles, physically narrower front rows) generated on
  demand — nothing about the layout is stored.
- Sparse occupancy tracking: only booked or admin-blocked seats get a row in
  DynamoDB, so a fresh hall costs nothing to represent.
- Atomic multi-seat claiming (`claim_seats_atomic`) — a party's whole
  selection commits in one DynamoDB transaction, so two people can never end
  up holding the same seat, and a failed request never leaves a partial
  booking.
- Six seat categories out of the box: **Zone 1 / Zone 2 / Zone 3 / Regular**
  (cosmetic, always bookable — an admin can override any seat's category
  without affecting its availability) and **Camera / Projector**
  (not bookable — same as an admin-blocked seat, just with their own icon).
- Per-zone pricing: `seat_selection_logic.set_zone_price(eventType, category,
  displayName, pricePerFamily, pricePerPerson, maxFamilySize)` lets an admin
  price each of the four bookable categories independently, with a flat
  family fee that covers up to `maxFamilySize` seats in that zone (0 =
  uncapped) and a per-seat fee for anything beyond that. `price_seats(...)`
  turns a set of seat IDs + a `"family"`/`"person"` pricing mode into a
  per-zone cost breakdown and a total — the pricing math itself, with no
  opinion on checkout/payment (that's a separate concern; see the module's
  admin `/admin/events/{eventType}/seat-pricing[/{category}]` routes for
  reading/writing prices, and this repo's `docs/`/host code for how a
  purchase actually gets paid for).
- Two React components — `SeatMapSelector` (a registrant's seat picker) and
  `SeatMapManagement` (an admin occupancy + category-editor screen) — both
  pure and prop-driven, with zero network or auth code of their own.
- **Paid purchases.** `seat_purchase_logic.py` claims a set of specific seat
  IDs (via `claim_seats_atomic`, not a capacity counter) into a purchase
  record with its own lifecycle — `pending` → `paid` (or → `cancelled`, or,
  for a paid one, → `refund_requested` → `cancelled`) — priced by
  `seat_selection_logic.price_seats`. `paypay.py`/`square.py` are
  dependency-free (stdlib `urllib`) hosted-checkout adapters; a bank-transfer
  purchase instead waits for an admin's manual "mark paid". The module's own
  `seat_purchase_webhook` Lambda verifies and confirms online payments
  (`POST /webhooks/seat-selection/{provider}`, unauthenticated by necessity —
  see that Lambda's own docstring for how it stays trustworthy anyway) and
  is otherwise self-contained; **purchase *creation*, checkout, and
  self-service cancel are not part of this module** — see "The one route
  this module doesn't own" below.
- **Free purchases.** `create_purchase_request(..., free=True)` skips
  pricing entirely — every seat is claimed at ¥0 and written straight to
  `paid`, regardless of category or whether it's priced/active. This module
  has no opinion on *why* a selection should be free (wmfjapan's own
  `eventInfo.seatSelection.pricingEnabled=false` gates zone1/2/3 access
  through a separate paid add-on instead, checked entirely in host code
  before calling this) — it only knows what to do once it is.

## What you bring

- **A registration/order/booking record of your own.** This plugin has no
  concept of "a party" beyond a plain `registration_id` string — you decide
  what that string identifies and how many seats it needs.
- **A REST API + Cognito authorizer to mount onto** (or the equivalent — the
  Terraform module just needs a parent `aws_api_gateway_resource` for the
  public route, one for the per-event admin routes, your bare `/admin`
  resource for the door check-in route, and a shared `/webhooks` resource
  for its own payment-provider webhook to mount a `seat-selection` sibling
  under — see any other plugin's webhook route for the same convention —
  plus an authorizer ID).
- **A fetch/auth layer for the frontend.** `SeatMapManagement` takes
  `getEventSeats`/`blockEventSeat`/`unblockEventSeat` as props; you supply
  functions that know how to call your own API Gateway stage with your own
  auth headers. `SeatMapSelector` takes plain data — no functions to wire up
  at all.

## Install (Terraform)

```hcl
module "seat_selection" {
  source = "./plugins/seat-selection/terraform"

  name_prefix = "myapp-prod"     # unique per account/region if mounted twice
  aws_region  = var.aws_region

  rest_api_id               = aws_api_gateway_rest_api.main.id
  rest_api_execution_arn    = aws_api_gateway_rest_api.main.execution_arn
  webhooks_resource_id      = aws_api_gateway_resource.webhooks.id           # your own shared /webhooks mount point
  public_parent_resource_id = aws_api_gateway_resource.my_public_parent.id   # e.g. /events/{eventType}
  admin_parent_resource_id  = aws_api_gateway_resource.my_admin_parent.id    # e.g. /admin/events/{eventType}
  admin_root_resource_id    = aws_api_gateway_resource.my_admin_root.id      # your bare /admin resource
  cognito_authorizer_id     = aws_api_gateway_authorizer.cognito.id

  cors_allowed_origin = var.allowed_origins
}
```

This creates, entirely on its own: four DynamoDB tables, three Lambda
functions, a dedicated IAM role scoped to just those tables, CloudWatch log
groups, and the API routes:

| Route | Method | Auth |
|---|---|---|
| `{public_parent}/seats` | GET | none (public) |
| `{admin_parent}/seats` | GET, POST | your Cognito authorizer |
| `{admin_parent}/seats/{seatId}` | DELETE | your Cognito authorizer |
| `{admin_parent}/seat-pricing[/{category}]` | GET, PUT, DELETE | your Cognito authorizer |
| `{admin_parent}/seat-purchases[/{purchaseId}]` | GET, PATCH | your Cognito authorizer |
| `{admin_root}/seat-check-in` | POST | your Cognito authorizer |
| `/webhooks/seat-selection/{provider}` | POST | none (provider-verified — see below) |

`{admin_root}` is your bare `/admin` resource (admin_root_resource_id), not
`{admin_parent}` — a scanned check-in QR carries only its own token, with no
eventType to nest the route under.

`{provider}` is `paypay` or `square`. Supply payment credentials as module
variables (`paypay_api_key`, `square_access_token`, etc. — all optional,
default `""`; see `terraform/variables.tf`) if you want online checkout to
actually work; without them the webhook simply never gets called (a
registrant's own bank-transfer or provider-declined path is unaffected).

Similarly, `qr_code_layer_arn` (optional, default `""`) points at a Lambda
layer providing the `qrcode` PyPI package, to embed a check-in QR in the
"paid" email — pass your own app's layer if it already has one for its own
purposes (wmfjapan does) rather than publishing a second copy. Omit it
and the "paid" email still sends, just without a QR attachment.

See `terraform/variables.tf` for every input and `terraform/outputs.tf` for
what it exposes back (table names/ARNs, function names/ARNs, resource IDs,
and a `deployment_trigger_inputs` list to fold into your own
`aws_api_gateway_deployment`'s trigger hash).

### The routes this module doesn't own

Claiming/releasing seats, and creating/checking-out/cancelling a *purchase*,
for *your* registration record is your app's own logic, so none of it is an
API route this module provides. Instead, your own Lambda (whatever already
owns updating a registration) imports `backend/seat_selection_logic.py` and
`backend/seat_purchase_logic.py` directly and calls:

```python
import seat_selection_logic
import seat_purchase_logic

seat_selection_logic.validate_seat_ids(requested_seat_ids)

# The free/no-payment case (e.g. an admin-reserved seat):
seat_selection_logic.claim_seats_atomic(event_type, registration_id, to_claim, holder_name=name)
seat_selection_logic.release_seats(event_type, to_release)
seat_selection_logic.get_seats_for_registration(event_type, registration_id)

# The paid case — a purchase claims the seats itself:
purchase = seat_purchase_logic.create_purchase_request(
    event_type=event_type, registration_id=registration_id, email=email,
    name=name, seat_ids=seat_ids, pricing_mode="family",  # or "person"
)
seat_purchase_logic.record_checkout(purchase["purchaseId"], provider="square", ...)  # after calling square.create_checkout
seat_purchase_logic.cancel_purchase_request(purchase["purchaseId"], registration_id=registration_id)
```

Bundle `seat_selection_logic.py` (and, for paid purchases,
`seat_purchase_logic.py` + `seat_purchase_emailer.py` + `paypay.py`/
`square.py`) alongside your handler (an `archive_file` `source` block, same
as any other shared module in your Lambda zip) and set `SEATS_TABLE`/
`SEAT_PURCHASES_TABLE` to `module.seat_selection.seats_table_name`/
`seat_purchases_table_name`. Grant your Lambda's IAM role `GetItem`/`Query`/
`PutItem`/`DeleteItem`/`TransactWriteItems` on `seats_table_arn` and
`GetItem`/`Query`/`PutItem`/`UpdateItem` on `seat_purchases_table_arn`
(both + `/index/*`, plus read-only `GetItem`/`Query` on `zone_pricing_table_arn`
if your Lambda prices a purchase itself). See wmfjapan's own
`backend/seat_selection.tf`, `backend/lambda.tf`, and
`lambdas/my_registrations/handler.py` (`create_my_seat_purchase`,
`checkout_my_seat_purchase`, `cancel_my_seat_purchase`) for a complete worked
example — including its own `get_seat_selection_config()`/eligibility checks,
which are exactly the kind of business rules that belong in your app, not in
this plugin.

## Install (Frontend)

Copy or reference `frontend/` from your app (wmfjapan does this with a
Vite `resolve.alias`, so the plugin code never has to physically live inside
`frontend/src/` — see `frontend/vite.config.js` and the `@import` in
`frontend/src/styles.css`):

```js
import { SeatMapSelector, SeatMapManagement } from "seat-selection-plugin";
import "seat-selection-plugin/seat-map.css";
```

`SeatMapSelector` is pure props-in/selection-out (same pattern as any other
picker component in your app). `SeatMapManagement` additionally takes
`getEventSeats`, `blockEventSeat`, `unblockEventSeat` — your own functions
for calling the admin routes above with your own auth.

The CSS reads a handful of custom properties for theming (`--wmf-red`,
`--amber`, `--wmf-green`, `--border`, `--surface-2`, `--text-muted`, `--text`,
`--radius-sm`, `--border-hover`, `--amber-glow`) and falls back to sane
defaults for any that aren't defined, so it renders reasonably even without
a matching design system.

## Extending

- **A different hall shape.** `_ROW_SPECS` in `seat_selection_logic.py` is
  the one fixed opinion this plugin has today — every `eventType` shares the
  same 501-seat layout. Making it per-`eventType`-configurable (e.g. reading
  a layout spec from a table keyed by `eventType`) is a natural next step if
  you need more than one hall shape live at once.
- **More non-bookable categories.** Add to `NON_BOOKABLE_CATEGORIES` in
  `seat_selection_logic.py` and give the new category an icon in both
  `SeatMapSelector.jsx` and `SeatMapManagement.jsx`.
- **A per-seat check-in flag.** Not tracked today (see wmfjapan's
  `SEAT_SELECTION.md` for the full list of known limitations that were
  scoped out of this first cut).
