# Celebrity Event Seat Selection

## Overview

Registrants whose Onam registration includes the **Celebrity Event**
(`registrationOption` = `event` or `combo`) buy specific seats from a
theater-style seat map — not just a swap against an aggregate capacity
number, but a real purchase: choose family or per-person pricing, pick any
number of seats (priced per zone), pay by PayPay, Square, or bank transfer,
and get a QR ticket by email once the payment clears. The hall layout and
zone boundaries match 葛西区民館ホール's own printed seating chart exactly
(see [Hall Layout](#hall-layout) below).

The seat map, occupancy tracking, atomic claiming, category overrides, zone
pricing, the paid-purchase lifecycle (PayPay/Square/bank checkout, refunds,
check-in QR), the plugin's three Lambdas, and the three React components are
a **standalone plugin** at [`plugins/seat-selection/`](plugins/seat-selection/README.md),
with no knowledge of Onam, registrations, or attendee counts. This document
describes how wmfjapan *integrates* that plugin (the registration-aware
parts: eligibility checks, the `/my/registrations/{id}/seat-purchases...`
routes, wiring into `AdminDashboard.jsx`/`HallSeatSelection.jsx`); see the
plugin's own README for what it provides on its own and how a different app
could reuse it.

This is a **separate system from the Onam Sadhya entry slots** and does not
touch them — including their check-in mechanisms, which are two
independent QR tokens/routes/screens that can never gate on each other's
state:

| | Entry Slots (existing) | Seat Selection (this feature) |
|---|---|---|
| Event | Onam Sadhya (the meal) | Celebrity Event |
| Applies to | `onam`, `combo` | `event`, `combo` |
| Granularity | An aggregate capacity pool per time window | An individually identified, purchased seat (e.g. `R04C12`) |
| Payment | Included in the registration's own total | A separate purchase per seat selection, priced per zone |
| Check-in | `registrationPassToken` QR, `/check-in`, `EntryPassCheckIn.jsx` | `checkInToken` QR, `/seat-check-in`, `SeatPurchaseCheckIn.jsx` |
| Locks on check-in | Yes — a slot is frozen once anyone is admitted | No — a purchase stays checked-in-or-not, but its seats aren't reusable regardless |

A registration with `combo` gets **both** an entry slot and seats; a
registration checked in for the Sadhya meal can still freely buy/change its
Celebrity Event seats, and vice versa. If you're extending this feature,
keep that separation — don't gate one on the other's state.

---

## Hall Layout

The layout is **fixed** — the same 501-seat hall for every event, not
configurable per event or derived from a capacity setting. It matches
葛西区民館ホール's own printed seating chart exactly. Every row is built
from three blocks — **Left / Center (always 11 seats) / Right** —
separated by a vertical aisle after the Left block and before the Right
block:

| Rows | Left | Center | Right | Row total | Blank past each end |
|---|---|---|---|---|---|
| 1 (い) | 5 | 11 (Zone 1) | 5 | 21 | 3 |
| 2 (ろ) | 6 | 11 (Zone 1) | 6 | 23 | 2 |
| 3 (は) | 7 | 11 (Zone 2) | 7 | 25 | 1 |
| 4–7 (に–と) | 8 | 11 (Zone 2) | 8 | 27 | 0 |
| 8–19 (ち–つ) | 8 | 11 (Regular) | 8 | 27 | 0 |

501 real seats total (21 + 23 + 25 + 27×16). **Zone 1** and **Zone 2** are
always exactly the Center block on their rows (22 and 55 seats
respectively); the Left and Right blocks are always **Regular**, on every
row (424 seats total) — those are the row-based *defaults*; see
[Seat Categories](#seat-categories) below for how an admin can override
any individual seat away from them.

- A cosmetic **WALKWAY** divider is shown after row 10 (`WALKWAY_AFTER_ROW`)
  — purely a frontend banner, not a gap in seat numbering.
- The vertical aisles are cosmetic too: each seat carries which block it's
  in (`seat.block`: `left` / `center` / `right`) straight from the backend,
  rather than the frontend inferring it from a column-number threshold —
  block sizes differ per row (row 1's Left is 5 seats, row 4's is 8), so a
  fixed threshold would misplace seats on the narrower rows. See
  `splitRowByAisles` in `SeatMapSelector.jsx` and `SeatMapManagement.jsx`
  (duplicated in both, same as other small presentational helpers in this
  app) — it just groups by `seat.block`.
- Rows 1–3 are physically **narrower** than the rest of the hall — there
  is no seat at all past each end, not merely an unavailable one. Each row's
  `blankEdges` (from `_ROW_SPECS`: row 1 → 3, row 2 → 2, row 3 → 1,
  everything else → 0) is how many blank, unclickable filler cells to
  render past each end, so every row's real columns still line up
  vertically across the whole grid — that's why rows 1–3 look
  indented/narrower in the UI. It's the hall shape, not a missing-seat bug.

Seat IDs are `R{row:02d}C{col:02d}`, e.g. `R04C12` (row 4, column 12 —
column numbering runs continuously 1..N per row, left to right, and has
nothing to do with `blankEdges`). Nothing about this layout is stored —
it's generated on every request from
`plugins/seat-selection/backend/seat_selection_logic.py`'s `_ROW_SPECS`
constant (`(row, leftCount, centerCount, rightCount, centerZone,
blankEdges)` per row). Only seats that are actually **booked** or
admin-**blocked** get a row in the database; an unlisted seat ID is
available. Change `_ROW_SPECS` there if the venue layout ever needs to
change — every Lambda and the frontend derive everything (block sizes,
zone boundaries, total seat count) from that one constant, via
`generate_seat_layout()`.

### Row lettering and real seat numbers

`seatId` (`R{row}C{col}`) is the internal, stored identifier — used for
claims, purchases, and QR check-in tokens — and never changes. But
葛西区民館ホール's own printed chart labels rows with the classical
**iroha** ordering (い/ろ/は/に/ほ/へ/と/ち/り/ぬ/る/を/わ/か/よ/た/れ/そ/つ,
rows 1–19) and its own seat *numbers* differ from the internal column
index in two ways:

- The Left block's own last seat (nearest the aisle) is always printed
  **9** — the digit 8 is never printed anywhere in the hall. The Center
  block's own first seat is *also* printed 9 (its own independent local
  count, always 9–19 regardless of row width) — two different physical
  seats sharing a printed number, disambiguated by which block/side of the
  aisle they're on, not a numbering collision.
- Rows narrower than the full 8-seat Left/Right blocks (rows 1–3) drop
  seats off the *low* end of the numbering, not the end nearest the
  aisle — e.g. row い's Left block prints 4, 5, 6, 7, 9 (5 seats), not
  1, 2, 3, 4, 5.

`plugins/seat-selection/frontend/rowLabels.js`'s `seatDisplayLabel()`
converts any `seatId` to this real printed label (e.g. `R01C04` →
`"い-4"`), used everywhere a seat is shown to a person — the row gutter,
every seat tooltip, and every seat list (purchase summaries, admin purchase
management, the door check-in screen) — without touching the underlying
`seatId` anywhere.

A registration may claim **up to one seat per eligible attendee** — adults
plus kids aged 6-14, excluding kids under 6 (who don't need their own
seat) — across all of its still-active purchases (`pending`/`paid`/
`refund_requested`; a `cancelled` purchase releases its seats back to the
allowance). This is enforced server-side in
`my_registrations._seat_allowance`/`_celebrity_seats_allowed` and
`create_my_seat_purchase`, and mirrored client-side as the seat picker's
`maxSeats` prop so a registrant can't even select more than their
allowance before submitting. The plugin itself has no opinion here either
way — it only ever deals in seat IDs and an opaque `registrationId`
string; the cap is entirely wmfjapan's own call.

---

## Seat Categories

Every seat's category starts from its `_ROW_SPECS` row default (Zone 1 /
Zone 2 / Regular), but an admin can reassign **any individual seat**
from the Seat Map screen, click a seat, and pick a category. There are
two kinds of category, with very different bookability:

| Category | Bookable by registrants? | Priceable by admin? | Mechanism | Icon |
|---|---|---|---|---|
| Zone 1 | Yes | Yes | row default, or override | 👑 |
| Zone 2 | Yes | Yes | row default, or override | 👑 |
| Zone 3 | Yes | Yes | override only (no row defaults to Zone 3) | 👑 |
| Regular | Yes | **No — always free** | row default, or override | — |
| Camera | **No** | — | admin-blocked (with a tag) | 📷 |
| Projector | **No** | — | admin-blocked (with a tag) | 🎥 |

Regular is the hall's baseline seating — already covered by the base
registration fee, never an admin-priced upsell. `price_seats` always bills
it ¥0 and never treats it as "unpriced"/unavailable, regardless of
`zone_pricing`; `set_zone_price` outright refuses a `category` outside
`PRICEABLE_CATEGORIES` (`zone1`/`zone2`/`zone3`), so there's no admin UI
path to price it either. A purchase whose seats are *all* Regular (total
¥0) skips payment entirely — see [Registrant Flow](#registrant-flow).

- **Zone 1 / Zone 2 / Zone 3 / Regular** are purely cosmetic — reassigning
  a seat's zone (e.g. carving a Zone 3 out of what would otherwise be
  Regular) never creates a row in the sparse seats table, so the seat stays
  exactly as bookable as it always was.
  `seat_selection_logic.set_category_override(eventType, seatId, category)`
  stores the reassignment as `{seatId: category}` in the plugin's own
  dedicated `category_overrides` DynamoDB table (one item per `eventType`,
  keyed by hash key `eventType`) — a table the plugin's Terraform module
  provisions and grants its own Lambdas access to, so the plugin has no
  dependency on any of wmfjapan's other tables.
  `get_category_overrides(eventType)` reads it back; every layout-producing
  function (`generate_seat_layout`, `find_seat`, `get_zone_totals`) takes
  the resulting `{seatId: category}` dict as an optional parameter and
  applies it on top of the row defaults.
- **Camera / Projector** are **not bookable** — a camera platform or
  projector booth is a physical obstruction, so both reuse the exact same
  mechanism as today's generic "blocked" seat (`block_seat` /
  `unblock_seat`, a real row in the seats
  table), just with a `blockCategory` attribute on the item so the frontend
  renders that seat's own icon instead of a plain gray "blocked" swatch.
  `unblock_seat` resets a seat back to its hall default either way — it
  releases a block *and* clears a zone override, whichever applies (a
  harmless no-op for whichever doesn't), so there's one consistent "Reset
  to Hall Default" action for admins regardless of which kind of override a
  seat currently has.

Both the registrant seat map (`GET /events/{eventType}/seats`) and the
admin seat map (`GET /admin/events/{eventType}/seats`) apply overrides
before responding, so a Zone 3 seat (or a Camera/Projector seat) looks
correct to registrants immediately after an admin sets it — there's no
caching or lag.

---

## Data Model

All four tables below are provisioned by the plugin's own Terraform module
(`plugins/seat-selection/terraform`), not `backend/dynamodb.tf` — see
`backend/seat_selection.tf` for how wmfjapan mounts that module.

### Seats table

Composite key, sparse (see above); named
`${name_prefix}-seat-selection-seats${suffix}`.

| Attribute | Type | Notes |
|---|---|---|
| `eventType` (hash) | String | |
| `seatId` (range) | String | e.g. `"R04C12"` |
| `registrationId` | String | Empty for an admin-blocked seat |
| `holderName` | String | Registrant name, for admin display only |
| `status` | String | `BOOKED` \| `BLOCKED` |
| `reason` | String | Admin-supplied reason for a blocked seat |
| `bookedAt` / `updatedAt` | String (ISO) | |

**GSI** `RegistrationIdIndex` (hash `registrationId`) — finds/releases a
registration's seats without scanning the table.

### Category overrides table

One item per `eventType`: `{eventType, overrides: {seatId: category}, updatedAt}`.
Named `${name_prefix}-seat-selection-category-overrides${suffix}`. Only the
cosmetic, still-bookable categories ever appear in `overrides` — see
[Seat Categories](#seat-categories) above.

### Zone pricing table

One item per `(eventType, category)`: `{eventType (hash), category (range),
displayName, pricePerFamily, pricePerPerson, maxFamilySize, status,
updatedAt}`. Named `${name_prefix}-seat-selection-zone-pricing${suffix}`.
`status` is `ACTIVE` or `INACTIVE` — an admin takes a zone off sale by
deleting its item (`delete_zone_price`) or setting `status: INACTIVE`;
either way, `price_seats` treats it as unpriced and the registrant seat map
shows it as "not for sale" and un-selectable. Read by
`seat_selection_logic.get_zone_pricing`/`get_zone_price`, written by
`set_zone_price`/`delete_zone_price`.

### Seat purchases table

One item per purchase attempt, hash key `purchaseId`; named
`${name_prefix}-seat-selection-purchases${suffix}`.

| Attribute | Type | Notes |
|---|---|---|
| `purchaseId` (hash) | String | |
| `registrationId` | String | Opaque, same convention as the seats table |
| `eventType` | String | |
| `email` / `name` | String | Snapshotted at creation, for emails that never need a registration lookup |
| `seatIds` | List\<String\> | |
| `pricingMode` | String | `family` \| `person` |
| `breakdown` / `totalAmount` | List / Number | From `seat_selection_logic.price_seats`, computed server-side at creation — never trusts a client-supplied amount. `breakdown` still groups seats by category (with a proper display name) even for a `pricingEnabled: false` purchase — see `price_seats`'s own `free` param — so the purchase/email/admin views always have a real zone name to show, never "N/A" |
| `pricingEnabled` | Boolean | Whether this event's seat-selection pricing was on at the moment this purchase was created (`not free` — see `create_purchase_request`). Distinct from `totalAmount == 0`, which is also true for an ordinary Regular-only purchase under the priced flow. Missing on a purchase from before this field existed, which `normalize_purchase` defaults to `true`. The purchase emails skip the Amount/payment-method rows entirely when this is `false` — there was nothing to charge |
| `status` | String | `pending` → `paid` → (`refund_requested` →) `cancelled` |
| `paymentMethod` | String | `paypay` \| `square` \| `bank` |
| `paymentProvider` / `providerPaymentId` / `checkoutUrl` | String | Only for `paypay`/`square`; empty for `bank`. `providerPaymentId` is *our own* `merchantPaymentId` (the purchase's own `purchaseId`) — used to look the purchase back up from a webhook/return redirect |
| `paymentReference` | String | The provider's own transaction/payment id (PayPay's `paymentId`, Square's `paymentId`) — set once by `mark_paid_from_provider` at the pending→paid transition (webhook or return-page reconcile); empty for `bank` or a still-pending purchase. Exposed to both the registrant and admin APIs; shown on the admin purchase table for reconciling against the provider's own dashboard |
| `refundStatus` / `refundReference` | String | Best-effort bookkeeping on a `refund_requested` item; never drives the state machine itself |
| `checkInToken` | String | Generated the moment `status` becomes `paid` (sparse — a still-pending or cancelled purchase never has one) |
| `checkedIn` / `checkedInAt` / `checkedInBy` | Boolean / String / String | Set once, at the door, via `check_in_purchase` |
| `requestedAt` / `updatedAt` | String (ISO) | |

**GSIs**: `RegistrationIdIndex` (hash `registrationId`, range `requestedAt`)
lists one registration's purchases; `EventTypeIndex` (hash `eventType`,
range `requestedAt`) is the admin purchase-management list; sparse
`ProviderPaymentIdIndex` (hash `providerPaymentId`) lets the webhook/return-
page resolve a purchase from nothing but the provider's own reference;
sparse `CheckInTokenIndex` (hash `checkInToken`) lets the door screen
resolve a scanned QR.

### Concurrency control

A party's whole seat selection is claimed in **one DynamoDB transaction**
(`seat_selection_logic.claim_seats_atomic`), with a conditional `Put` per
seat (`ConditionExpression="attribute_not_exists(seatId)"`). Either every
seat in the request is claimed, or none are — there's no window where two
people can both grab the same seat, and no partial booking to clean up on
failure. A purchase claims its seats this same way at creation, so there's
no separate capacity counter to keep in sync with the seats themselves.
Releasing seats (cancellation, admin block/unblock) is a plain, best-effort
`DeleteItem` per seat, since there's nothing to race there.

---

## Backend API

| Route | Method | Auth | Lambda | Owned by |
|---|---|---|---|---|
| `/events/{eventType}/seats` | GET | none (public) | `get_available_seats` | plugin |
| `/my/registrations/{id}/seat-purchases` | GET, POST | registrant token | `my_registrations` | wmfjapan |
| `/my/registrations/{id}/seat-purchases/{purchaseId}` | GET, DELETE | registrant token | `my_registrations` | wmfjapan |
| `/my/registrations/{id}/seat-purchases/{purchaseId}/checkout` | POST | registrant token | `my_registrations` | wmfjapan |
| `/admin/events/{eventType}/seats` | GET, POST | Cognito | `manage_seats` | plugin |
| `/admin/events/{eventType}/seats/{seatId}` | DELETE | Cognito | `manage_seats` | plugin |
| `/admin/events/{eventType}/seat-pricing[/{category}]` | GET, PUT, DELETE | Cognito | `manage_seats` | plugin |
| `/admin/events/{eventType}/seat-purchases[/{purchaseId}]` | GET, PATCH | Cognito | `manage_seats` | plugin |
| `/admin/seat-check-in` | POST | Cognito | `manage_seats` | plugin |
| `/admin/registrations/seat-selection-announcement` | POST | Cognito | `manage_registrations` | wmfjapan |
| `/admin/registrations/allocate-seats` | GET, POST | Cognito | `manage_registrations` | wmfjapan |
| `/webhooks/seat-selection/{provider}` | POST | none (provider-verified) | `seat_purchase_webhook` | plugin |

The `/my/registrations/...` routes are wmfjapan's own integration
glue — `my_registrations` already owns the registration record and its
eligibility rules, so purchase creation/checkout/cancel is the one thing
the plugin doesn't provide directly (see the plugin's README, "The routes
this module doesn't own"); it's built on `seat_purchase_logic.py` imported
straight into that Lambda.

- **GET `/events/{eventType}/seats`** — `{ totalSeats, layout, bookedSeatIds, specialSeats, zoneTotals, zoneAvailable, zonePricing, walkwayAfterRow, available }`. `layout` is `[{ row, blankEdges, seats: [{ seatId, category, block }] }]`. `zonePricing` is `{category: {displayName, pricePerFamily, pricePerPerson, maxFamilySize, status}}` — a category missing here (or `status: "INACTIVE"`) is not for sale. Used by the registrant wizard's seat-picking step.
- **GET `/my/registrations/{id}/seat-purchases`** — `{ enabled, pricingEnabled, enabledPaymentMethods, seatSelectionWindow, purchases, seatsAllowed, seatsClaimed, seatsRemaining, categoryCaps }` for that registration: the admin-configured `eventInfo.seatSelection` toggle/dates, every purchase already made against it, and the attendee-count-based seat allowance (see above). `categoryCaps` (`{zone1: N, ...}`, only present while `pricingEnabled` is `false`) is how many more of each premium zone this registration may still pick this session — see "Free selection gated by a zone upgrade" above.
- **POST `/my/registrations/{id}/seat-purchases`** — body `{ seatIds, pricingMode, paymentMethod }`. Requires `paymentStatus == "paid"` and `registrationOption` in `event`/`combo`, the seat-selection window open, `paymentMethod` in the admin's enabled list, and `seatIds.length` not exceeding `seatsRemaining` (400 otherwise). Prices and claims the seats server-side (never trusts a client-supplied amount) in one call. `paymentMethod` is optional and ignored when the seats price out to ¥0 (Regular only) — that purchase is written straight to `paid` (with its check-in token and confirmation email already sent) instead of `pending`.
- **POST `.../seat-purchases/{purchaseId}/checkout`** — starts (or resumes) online checkout for a `pending`, non-bank purchase: creates a PayPay/Square checkout session and returns its redirect URL. A bank-transfer purchase needs no checkout call — it's already awaiting admin confirmation.
- **GET `.../seat-purchases/{purchaseId}`** — reconciles with the payment provider first (in case the webhook hasn't landed yet) before returning the purchase, so a registrant's return trip from checkout always sees the latest status.
- **DELETE `.../seat-purchases/{purchaseId}`** — self-service cancel. A still-`pending` purchase goes straight to `cancelled` (seats released); an already-`paid` one moves to `refund_requested` and *keeps* its seats until an admin confirms the refund — except a ¥0 (Regular-only) purchase, which has nothing to refund and goes straight to `cancelled` too.
- **Admin `GET .../seats`** — full occupancy view, same as before, plus `zonePricing`.
- **Admin `POST`/`DELETE .../seats[/{seatId}]`** — block/unblock and category overrides, unchanged from the free-picker era.
- **Admin `GET .../seat-pricing`** / **`PUT .../seat-pricing/{category}`** / **`DELETE .../seat-pricing/{category}`** — read, set, or unprice one zone's pricing.
- **Admin `GET .../seat-purchases[/{purchaseId}]`** / **`PATCH .../seat-purchases/{purchaseId}`** — list/get an event's purchases; `PATCH` body `{ status }` transitions it (typically `paid` to confirm a bank transfer by hand, or `cancelled` to finalize a refund) — same admin-confirms pattern seat-upgrade uses. Emails the registrant on a `paid` or `cancelled` transition, attaching the check-in QR on `paid`.
- **Admin `POST /admin/seat-check-in`** — body `{ token, action }`, `action` `resolve` (peek) or `check_in` (atomically consume); see [Check-in](#check-in) below.
- **Admin `POST /admin/registrations/seat-selection-announcement`** — body `{ eventType }`; emails every paid `event`/`combo` registrant of that event that seat selection is open, with each priced zone's rate when any zone has one configured yet (not required to send).
- **Admin `GET /admin/registrations/allocate-seats?registrationId=...`** / **`POST /admin/registrations/allocate-seats`** (body `{ registrationId, seatIds }`) — see [Admin-allocated seats](#admin-allocated-seats-from-the-registrations-tab) above.
- **`POST /webhooks/seat-selection/{provider}`** — PayPay/Square payment confirmation. See the plugin README for how each provider is verified.

Seats are also released automatically when a registration is deleted
(`my_registrations.delete_my_registration`, `manage_registrations.delete_registration`) —
its active purchases are cancelled first — unless the party has already
been admitted at the venue, in which case the seat is deliberately left
stranded rather than handed back for resale (same "safe direction to be
wrong" the entry-slot code already uses).

### Environment variables

| Variable | Set on | Description |
|---|---|---|
| `SEATS_TABLE` | `get_available_seats`, `manage_seats`, `my_registrations`, `manage_registrations` | The plugin's seats table name — `module.seat_selection.seats_table_name` |
| `SEAT_CATEGORY_OVERRIDES_TABLE` | `get_available_seats`, `manage_seats` | The plugin's category-overrides table name |
| `ZONE_PRICING_TABLE` | `get_available_seats`, `manage_seats`, `my_registrations`, `manage_registrations` | The plugin's zone-pricing table name |
| `SEAT_PURCHASES_TABLE` | `manage_seats`, `seat_purchase_webhook`, `my_registrations`, `manage_registrations` | The plugin's purchases table name |
| `FRONTEND_URL` / `SES_FROM_EMAIL` / `ADMIN_EMAIL` | `manage_seats`, `seat_purchase_webhook`, `my_registrations`, `manage_registrations` | Needed by `seat_purchase_emailer.py`/`seat_purchase_qr.py` for the CTA link/logo, the SES from-address, and the admin-notification address |
| `PAYPAY_*` / `SQUARE_*` | `seat_purchase_webhook` (verify + confirm), `my_registrations` (create checkout) | Same credentials, wired into both sides of the online-checkout flow independently |
| `REGION` | all of the above | Set directly by the module for its own three Lambdas, independent of wmfjapan's own `REGION` env var |

IAM: `get_available_seats`/`manage_seats`/`seat_purchase_webhook` run under
the plugin module's own dedicated IAM role (scoped to just its four
tables, plus `ses:SendEmail`/`ses:SendRawEmail` — restricted to
`var.ses_from_email` — for `manage_seats`'/`seat_purchase_webhook`'s own
paid/cancelled/refund and admin-notification emails; see
`plugins/seat-selection/terraform/iam.tf`). `my_registrations`/
`manage_registrations` run under wmfjapan's shared Lambda role, which is
granted the matching per-table permissions on
`module.seat_selection.{seats,zone_pricing,seat_purchases}_table_arn` (+
their GSIs) in `backend/lambda.tf` — neither ever touches the
category-overrides table, since only the admin/public seat-map Lambdas deal
in categories.

---

## Registrant Flow

Seat selection lives on its **own page**, `/my/hall-seat-selection`
(`frontend/src/HallSeatSelection.jsx`) — not inline on My Registrations —
but that page is now *just* the picker: existing purchases, their status,
and the seat allowance all show inline on **My Registrations**' own
registration card instead, so there's no separate landing/summary step to
click through before picking seats.

1. On **My Registrations**, a paid `event`/`combo` registration's card
   shows a "Celebrity Event Seats" section: any existing purchases (status,
   seats via their real venue label, amount, and a cancel/resume-payment
   action while still `pending`/`paid`), the seat allowance
   (`seatsAllowed`/`seatsClaimed`/`seatsRemaining` — one seat per attendee,
   excluding kids under 6), and a **"Select Your Seats"**/**"Select More
   Seats"** button (disabled once the allowance is fully claimed, or the
   booking window isn't open). Clicking it stashes that registration's ID
   in `sessionStorage` (key `nk-seat-selection-registration-id`) and
   navigates to the hall page.
2. The hall page resolves the stashed (or, failing that, the registrant's
   one eligible) registration, loads its config, and — the moment that's
   ready — jumps straight into the wizard's **pick** step; there's nothing
   to click through first. If seat selection isn't enabled, the booking
   window isn't open, or the allowance is already fully claimed, it shows
   that instead and offers a way back to My Registrations rather than
   opening the wizard.
3. **Pick.** Pricing mode and seat picking share one screen. Family pricing
   charges one flat fee per zone (covering up to that zone's configured
   seat count); person pricing charges each seat individually — chosen
   once and applied throughout. Below it, the hall map (STAGE/WALKWAY
   banners, zone-colored seats, zone-availability header) with the legend
   showing each priced zone's live rate; an unpriced zone renders disabled
   with a "not for sale" striped pattern — Regular is never in that state,
   since it's always free (see [Seat Categories](#seat-categories)).
   Selection is capped at the registration's remaining seat allowance
   (`maxSeats` on `SeatMapSelector`) — a registrant can't pick more seats
   than they have eligible attendees for. A running total (mirroring the
   backend's `price_seats` formula exactly, client-side, for instant
   feedback) shows as seats are picked. "Review Order" is enabled once a
   pricing mode and at least one seat are chosen.
4. **Review and pay.** A summary of the selected seats, the price
   breakdown, and the total. A selection that comes to ¥0 (Regular seats
   only) skips the payment step entirely — a note says no payment is
   needed, and "Confirm Seats" reserves them immediately as `paid`, no
   admin confirmation involved. Otherwise, a payment-method choice
   (whichever the admin has enabled) and "Confirm and Pay": PayPay/Square
   redirect to the provider's hosted checkout and back; bank transfer needs
   no redirect — the purchase sits `pending` until an admin confirms it by
   hand. Either way, confirming returns the registrant to My Registrations,
   where the new purchase now shows on the card. Returning from a hosted
   checkout redirect reconciles the purchase's status automatically (in
   case the webhook hasn't landed yet) and shows a confirmation banner on
   the hall page before the registrant navigates on.

Once payment clears (or immediately, for a free Regular-only selection),
the registrant gets an email with a QR code — see [Check-in](#check-in)
below.

The previously-disabled "Book My Seat" call-to-action on the **Seat
Selection Guide** page (`/seat-selection-guide`) links directly to this page.

Components: `SeatMapSelector` from the `seat-selection-plugin` package
(physically `plugins/seat-selection/frontend/SeatMapSelector.jsx`, reached
via a Vite `resolve.alias` — see `frontend/vite.config.js`) — presentational
only (props in, selection out, plus an optional `zonePricing` prop for the
legend/disabled-seat behavior), the same pattern as `EntrySlotSelector.jsx`.
All wizard state, pricing estimation, and purchase-API calls live in
`HallSeatSelection.jsx`.

---

## Check-in

Each purchase gets its own `checkInToken`, generated the moment it's marked
`paid` (by an admin, by the payment-provider webhook, or by the
return-page reconcile — see `seat_purchase_logic.update_purchase_status`).
The "paid" confirmation email carries a QR encoding
`{FRONTEND_URL}/seat-check-in?token={checkInToken}` (built by
`seat_purchase_qr.py`, which needs a Lambda layer providing the `qrcode`
package — see `qr_code_layer_arn` in the plugin's `variables.tf`).

That same email also attaches a printable PDF ticket
(`checkin_pdf.build_check_in_ticket_pdf`, called from
`seat_purchase_emailer._build_ticket_attachment`) — the same QR code plus
the registrant's name, a venue map card drawn from the real hall layout
(stage, walkway, and the purchased seats highlighted in place), and a
seat list annotated with each seat's iroha row-letter/printed
number/block (e.g. `R10C12 (ぬ-12 · Center)`), matching the confirmation
email's own seat display exactly. When the registration also has
Celebrity Dinner guests bundled in (a standalone signup or a zone
upgrade that carries a dinner add-on), the ticket shows their count
broken down by age category (Adults / Kids 9-14 / Kids 6-8 / Kids under
6, each split veg/non-veg) instead of one flat guest count. The whole
PDF is hand-rolled against the raw PDF spec with no runtime rendering
dependency (no Pillow/reportlab at runtime — non-Latin glyphs like the
iroha labels and the logo are pre-baked bitmaps embedded as base64 at
dev time); its left column dynamically shrinks row spacing to fit
whatever combination of seats and dinner rows a party has, rather than
risk overflowing the page for a large family. This module is duplicated
verbatim as both `lambdas/checkin_pdf.py` (used by the Sadhya entry-pass
ticket) and `plugins/seat-selection/backend/checkin_pdf.py` (this
plugin's own Lambda package) — the two must be kept in sync by hand when
either changes, same as the other backend modules this plugin copies
rather than imports across Lambda packages.

At the venue, staff scan the code with any phone camera, which opens
`/seat-check-in?token=...` (`frontend/src/SeatPurchaseCheckIn.jsx`) — gated
behind the same admin Cognito login as the dashboard. The screen resolves
the ticket (name, seats, amount, payment method, status) and offers a
"Check in" button once, calling `POST /admin/seat-check-in` with
`action: "check_in"`; re-scanning an already-checked-in ticket just shows
that it's already in, harmlessly. Only a `paid` purchase can ever be
checked in — a cancelled or still-pending one is refused outright.

The reverse also holds: once a purchase is checked in, it can never be
cancelled — by the registrant's self-service cancel or by an admin's
"Cancel"/"Confirm Refund Sent" action — since those seats were already
used at the door and releasing them would risk double-booking whoever
scans a duplicate or resold ticket for the same seat
(`seat_purchase_logic.update_purchase_status`/`cancel_purchase_request`
both refuse it outright). The registrant's My Registrations card and the
admin purchase table both hide the cancel action once `checkedIn` is
true, rather than showing one that would just fail.

This is **deliberately independent** of the existing Sadhya entry-pass
check-in (`registrationPassToken`, `/check-in`,
`EntryPassCheckIn.jsx`) — its own token, its own route, its own screen —
so admitting someone to the Celebrity Event can never be tangled up with,
or blocked by, their Sadhya entry-pass state, and vice versa.

---

## Admin Flow

A **Seat Map** tab sits next to **Entry Slots** in the admin dashboard.
Pick an event to see occupancy stats, the zone-colored seat grid, STAGE/WALKWAY
banners, and the same legend as the registrant page (plus Camera/Projector
icons). Click any non-booked seat to open its category picker — Zone 1/2/3/
Regular (bookable), Camera/Projector (not bookable, prompts for an
optional reason), a plain "Block (other reason)", or "Reset to Hall Default"
to revert whichever override the seat currently has. Booked seats are shown
but aren't clickable here — cancel or reassign the registration that holds
them instead.

This screen also carries the **Zone Pricing** panel (one row per zone —
display name, family price + how many seats it covers, per-person price,
active/inactive, save/unprice) and an **"Announce Seat Selection"** button
that fires the `/admin/registrations/seat-selection-announcement` email to
every eligible (paid, `event`/`combo`) registrant of that event.

To announce to only *some* registrants instead of the whole event, the
**Registrations** tab's own bulk-action dropdown (next to Mark Paid/
resend-email/Sadhya-reminder) has a **"Send seat selection announcement
(paid Celebrity Event/Combo)"** option: check the rows to email, pick it,
and run it — same underlying announcement email
(`seat_purchase_emailer.send_announcement_email`, same "Host Your Event
With Us" footer as the broadcast button), just scoped to the selected
registrations. `process_bulk_registrations` enforces the same
eligibility (`event`/`combo`, `paid`, and the event's seat selection
actually enabled — `manage_registrations._seat_announcement_context`,
shared with the broadcast button so the two entry points can never
disagree on what "ready to announce" means) and reports a
per-registration reason for anything it skips, same as every other bulk
action. Zone pricing is **not** required to announce either way — a
priced zone shows a rate card in the email, but an event with no zone
priced yet (always-free, or pricing simply not decided) still gets a
plain "seat selection is open, here's the link" announcement rather
than being refused.

The announcement email itself ("Your seats are waiting", `send_
announcement_email`) always shows the deadline line (when a booking
window end date is set) and the zone-price cards (when any zone is
priced), but ends with an optional closing line the admin sets per event
via **Seat Selection Configuration**'s "Announcement email closing note"
field (`eventInfo.seatSelection.announcementClosingNote`, parsed by
`seat_selection_logic.parse_seat_selection_config`, defaults to `""`) —
e.g. "We look forward to celebrating this milestone with you." for one
event, something else for another, or omitted entirely for a third. Left
blank, the email simply doesn't show that line.

Every seat-selection email (`seat_purchase_emailer._send`, shared by the
announcement and every purchase-lifecycle email) sends from a friendly
`FROM_DISPLAY` (`"WMF Japan" <office@wmfjapan.com>`) rather than
a bare address, and sets a `Reply-To` back to the same support inbox the
footer already advertises — a no-reply-looking sender with no way to
reply is one of the signals mail providers weigh toward Promotions/spam.
The underlying `SES_FROM_EMAIL` env var stays the bare address Terraform
verifies as an SES identity (`aws_ses_email_identity.from`) and matches
in IAM's `ses:FromAddress` condition — only the outgoing `Source`/`From`
header gets the display name, since SES and IAM both parse the address
back out of a `"Name" <address>` string.

A separate **Seat Purchases** tab (`SeatPurchaseManagement`, same package)
lists an event's purchases — registrant, seats, pricing mode, amount,
payment method, status, requested date — with Mark Paid / Confirm Refund
Sent / Cancel actions per row, and header stats (purchase count, amount
collected, awaiting-payment count, refund-requested count).

The event edit form (in the **Events** tab) has a **Seat Selection**
section: an enabled toggle, a "Charge for seat selection" toggle
(`pricingEnabled`, see below), a booking-window start/end date pair, and —
only while pricing is on — payment-method checkboxes (PayPay/Square/Bank
Transfer). Saved into `eventInfo.seatSelection`, the same convention as the
existing payment-links sub-block, and read back by
`my_registrations.get_seat_selection_config` (delegating the actual parsing
to `seat_selection_logic.parse_seat_selection_config`, shared with
`manage_registrations`'s announcement mailer so the two never drift).

### Free selection gated by a zone upgrade (`pricingEnabled`)

`eventInfo.seatSelection.pricingEnabled` (default `true`) lets an admin turn
this plugin's own zone pricing off for an event without disabling seat
selection itself. With it off:

- Every seat is claimed at ¥0 — `seat_purchase_logic.create_purchase_request`
  is called with `free=True`, which skips `price_seats` entirely (no zone
  needs an ACTIVE price row any more) and writes the purchase straight to
  `paid` with its check-in QR, exactly like today's Regular-only ¥0
  purchases already do.
- **Regular** stays open to everyone, same as always.
- **Zone 1/2/3** are locked down per registration instead of priced: a
  registrant may only pick from a premium zone up to however many seats
  they've separately paid for via the **seat-upgrade** plugin
  (`plugins/seat-upgrade/`) — a completely different plugin/table, wired
  together only through this one field. A Zone Upgrade tier optionally
  names one of `zone1`/`zone2`/`zone3` as its `seatMapCategory` (Seat
  Upgrades tab → tier form → "Seat Map Zone"); once a registrant's purchase
  of that tier is `paid` (or `refund_requested` — still holding its capacity
  until an admin finalizes the refund), `lambdas/my_registrations/
  handler.py`'s `_seat_zone_category_totals`/`_seat_category_caps` reads
  `SEAT_UPGRADES_TABLE` directly (that plugin's own "copied module"
  convention — no cross-plugin import) and sums the quantity snapshotted
  onto each qualifying purchase's `zoneBreakdown` entries, per category,
  minus however many of that category this registration already holds
  (`get_seats_for_registration`). The result is `categoryCaps` — "how many
  MORE zone-N seats may still be picked this session" — returned from `GET
  /my/registrations/{id}/seat-purchases` and enforced again in
  `create_my_seat_purchase` before ever calling into the plugin, so a direct
  API call can't bypass it. A tier with no `seatMapCategory` set is still a
  perfectly normal standalone paid zone upgrade; it just doesn't unlock
  anything here.
- The registrant-facing seat map (`SeatMapSelector`'s `categoryCaps` prop)
  greys out any zone1/2/3 seat beyond what's been paid for — the same
  diagonally-striped "unpriced" treatment a normal paid flow uses for a
  zone that isn't for sale, showing that zone's own crown icon (dimmed)
  rather than a generic lock, so a registrant can still see which zone a
  locked seat belongs to — and `HallSeatSelection.jsx` skips the
  family/person pricing-mode step entirely (meaningless once nothing is
  priced).

Turning `pricingEnabled` back on reverts an event to this plugin's own
zone pricing immediately — `categoryCaps`/the zone-upgrade tie-in simply
stop being read.

The purchase's own `pricingEnabled` field (stored on the record itself,
not read back from the event) is what the confirmation/request/refund
emails check to skip the Amount and payment-method rows entirely, rather
than showing a confusing ¥0 or blank payment method. A free claim also
always goes straight to `paid` (there's no unpaid state to have a
"request" email for), so `seat_purchase_emailer._render_purchase_email`
swaps in a `paid_free` template variant instead of the normal `paid`
one — same layout, but "Your seat selection is confirmed" instead of
"Payment received for your seat selection", since nothing was paid.

Components: `SeatMapManagement`/`SeatPurchaseManagement` from the
`seat-selection-plugin` package (physically
`plugins/seat-selection/frontend/{SeatMapManagement,SeatPurchaseManagement}.jsx`),
wired in `frontend/src/AdminDashboard.jsx` — which passes them
wmfjapan's own fetch/auth functions as props (`getEventSeats`,
`blockEventSeat`, `unblockEventSeat`, `saveZonePrice`, `deleteZonePrice`,
`getEventSeatPurchases`, `updateSeatPurchaseStatus`,
`sendSeatSelectionAnnouncement`), since neither component has fetch/auth
code of its own. There's no seat-map capacity field — the layout is fixed
(see [Hall Layout](#hall-layout)), so every event always has the same 501
seats; only their prices vary per event.

### Admin-allocated seats (from the Registrations tab)

An "Allocate Seats" button on each `event`/`combo` row in the admin
**Registrations** tab (`AdminDashboard.jsx`, next to "Change Slot") opens a
modal with the same `SeatMapSelector` the registrant picker uses, fed from
the public `GET /events/{eventType}/seats` layout — but with `zonePricing`
and `categoryCaps` both forced to `null`, so no zone shows as "not for
sale" and there's no zone-upgrade entitlement gate: an admin can put a
party in any open seat, in any zone, regardless of what that event's
pricing or that registration's own zone-upgrade entitlement would
otherwise allow.

Opening the modal first calls `GET /admin/registrations/allocate-seats
?registrationId=...` (`get_registration_seat_allocation`), which returns
`{eventType, editableSeatIds, lockedSeatIds, seatsAllowed,
adminOverrideMaxSeats}` for that registration. `editableSeatIds` — the
seats held by this registration's own still-open, free allocation, if
any — are pre-selected in the picker (shown highlighted, not disabled) and
excluded from the seat map's own `bookedSeatIds` so they render as normal,
togglable "selected" seats; an admin can freely deselect one to release it
or select a fresh one to add, same as picking any other seat.
`lockedSeatIds` — seats this registration holds that AREN'T editable here
(already checked in at the venue, or a purchase that involved real
payment) — stay booked/disabled, exactly like any other registration's
seat, so an admin can never accidentally touch a used or paid-for seat
from this screen.

Confirming calls `POST /admin/registrations/allocate-seats`
(`registrations/allocate-seats` route in `manage_registrations`, alongside
`registrations/reassign`) with the FULL desired set of editable seats —
not just seats to add. `allocate_registration_seats` first looks for a
purchase this registration already holds for the same event that's still
editable (`_find_mergeable_seat_purchase`: `status == "paid"`,
`pricingEnabled` is `False` — i.e. a previous free admin allocation, never
one real money was collected for — and not yet `checkedIn`). If one
exists, that purchase's own `seatIds` are updated in place
(`seat_purchase_logic.set_purchase_seats`): whatever's newly selected is
claimed, whatever's been deselected is released, anything present in both
the old and new set is left untouched — same `purchaseId`, same
`checkInToken`. So a second (or third) trip through this modal edits one
ticket in place instead of fragmenting into a separate purchase that
would only ever show its own slice of the seats (a registrant re-reading
an admin-allocation email after a second allocation would otherwise see
only the seats from that second one, with the first allocation's seats
looking like they'd vanished — they hadn't, they were just on a different
ticket). Only when there's no such purchase to edit — the first
allocation ever, or the existing one is already checked in — does it fall
back to `seat_purchase_logic.create_purchase_request(..., free=True)`,
the same ¥0-purchase path a Regular-only or pricing-disabled selection
already uses. Either way the registrant gets the normal confirmation
email with QR code and check-in PDF ticket, without ever being asked to
pay, and it always reflects the full current set of seats that purchase
holds.

Caps the total at the per-registration seat allowance (one seat per
eligible attendee — adults + kids 9-14 + kids 6-8, computed server-side
by a `manage_registrations._celebrity_seats_allowed` that mirrors
`my_registrations`' own formula exactly) unless
`eventInfo.seatSelection.adminOverrideMaxSeats` is on for this event, in
which case an admin may allocate any number of seats —
`SeatSelectionConfigPanel`'s "Allow admins to exceed the per-registration
seat limit" checkbox, parsed server-side by
`seat_selection_logic.parse_seat_selection_config` (defaults to `false`,
same convention as `pricingEnabled`). The picker's own `maxSeats` prop
mirrors this cap client-side (`Infinity` when the override is on) so an
admin can't even select past it before submitting, but the server
enforces it too since a direct API call could otherwise bypass the UI.

This is the one thing `manage_registrations` needed no new IAM/Terraform
table access for beyond one new API Gateway `GET` method on the existing
`allocate-seats` resource: it already bundles
`seat_selection_logic.py`/`seat_purchase_logic.py`/
`seat_purchase_emailer.py`/`seat_purchase_qr.py`/`checkin_pdf.py` (for its
own registration-edit re-send flow) and already carries every table
grant this needs, including `TransactWriteItems` and `UpdateItem` on the
seats/purchases tables respectively.

---

## Known Limitations

- **No admin-driven seat *re*assignment.** An admin can now allocate new
  seats directly to a registration (see [Admin-allocated
  seats](#admin-allocated-seats-from-the-registrations-tab) above), but
  there's still no equivalent of the entry-slot "reassign" flow for moving
  an *already-purchased* seat to a different one on the admin's behalf —
  changing a seat a registrant already holds is still self-service only
  (cancel and rebuy, or a fresh admin allocation on top of it).
- **One hall for every event.** The layout constant isn't per-event; if two
  events ever need seat selection with different physical layouts at the
  same time, `_ROW_SPECS` will need to become event-aware.
- **Zone pricing, not per-seat pricing.** Every seat in a zone shares that
  zone's price — there's no way to price an individual seat differently
  from its zone-mates.

---

## Deployment

1. `cd backend && terraform apply` — provisions the `seat_selection` module
   (its four tables, three Lambdas, dedicated IAM role, and API Gateway
   routes — see `backend/seat_selection.tf`); updates `my_registrations` and
   `manage_registrations` with the new `SEATS_TABLE`/`ZONE_PRICING_TABLE`/
   `SEAT_PURCHASES_TABLE` environment variables and bundled
   `seat_selection_logic.py`/`seat_purchase_logic.py`/`seat_purchase_emailer.py`/
   `seat_purchase_qr.py`. Set `paypay_*`/`square_*` module variables (and
   `qr_code_layer_arn`, reusing this app's own `aws_lambda_layer_version.qr_code`)
   if online checkout and check-in QRs should actually work.
2. Register each provider's webhook URL
   (`.../webhooks/seat-selection/{paypay|square}`) with that provider, once
   the API Gateway stage's invoke URL is known.
3. Redeploy the frontend (`npm run build` + S3 sync + CloudFront
   invalidation) to ship `HallSeatSelection.jsx`, `SeatPurchaseCheckIn.jsx`,
   and the `seat-selection-plugin` components they and `AdminDashboard.jsx`
   import.
4. From the admin dashboard's **Seat Map** tab, price at least one zone
   per event before enabling seat selection for it (in the **Events** tab) —
   an event with no priced zones shows every seat as "not for sale".

No CSV import or manual data migration is needed: all four tables start
empty and only ever hold booked/blocked seats, explicit overrides, prices
an admin has actually set, or purchases registrants have actually made —
and the hall layout is available immediately for every event.

See [`plugins/seat-selection/README.md`](plugins/seat-selection/README.md)
for how the plugin itself is organized, and how a different app could reuse
it.

---

## Public availability API

`GET /events/{eventType}/availability` — public, no auth, cached 30s. Built for
external consumers (e.g. Kaira AI). Aggregate counts only; no personal data.

```json
{
  "eventType": "onam_2026",
  "generatedAt": "2026-10-02T09:00:00+00:00",
  "celebrityEvent": { "status": "open|full|closed", "registrationOpen": true,
                      "closesOn": "2026-10-20", "capacity": 400, "registered": 310, "remainingSeats": 90 },
  "dinner":         { "status": "open|full|not_open|closed|not_offered", "registrationOpen": true,
                      "maxTotalGuests": 120, "guestsBooked": 80, "remainingSeats": 40, "...": "dates, venue, prices" },
  "seatSelection":  { "status": "open|sold_out|not_open|closed|not_offered", "selectionOpen": true,
                      "totalSeats": 501, "availableSeats": 350,
                      "zones": { "zone1": { "total": 22, "available": 5 } } }
}
```

`remainingSeats: null` means unlimited (capacity 0). A section that fails to
load returns `{"status": "unavailable"}` without affecting the others. 404 if
the event doesn't exist. For the full seat map use `GET /events/{eventType}/seats`.
