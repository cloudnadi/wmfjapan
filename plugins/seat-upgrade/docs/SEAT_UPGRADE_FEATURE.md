# Celebrity Event Seat Upgrade

## Overview

Registrants who paid for the Celebrity Event (registration option `event` or
`combo`) can pay extra to upgrade their seating to an admin-defined **zone**
(e.g. "Zone 1", "Zone 2", "Zone 3"). Each zone is priced two ways:

- **Per Family** — a single flat fee that upgrades every seat in the
  registrant's party at once.
- **Per Person** — a per-seat fee for upgrading some or all of the party
  individually.

This is a standalone paid add-on, packaged as its own plugin under
`plugins/seat-upgrade/` (see that directory's `README.md` for the module
boundary). It is **never written into the `registrations` table** — every
upgrade purchase only ever references its registration by `registrationId`,
the same way `entry_allocations` does. This keeps the original registration
record (and its pricing logic) untouched.

Payment is either **online** (PayPay or Square hosted checkout, confirmed by
webhook + an authoritative status re-check) or **bank transfer** (an admin
marks it paid by hand, same as a registration payment). See
`docs/PAYMENTS.md` for the full payment flow and provider setup.

---

## Data Model

Two new DynamoDB tables, independent of `registrations`:

### `seat_upgrade_tiers` — admin-managed zones

| Attribute | Type | Notes |
|---|---|---|
| `eventType` | S (hash key) | Which event the zone belongs to |
| `tierId` | S (range key) | UUID |
| `name` | S | e.g. "Zone 1" |
| `description` | S | Optional |
| `pricePerFamily` | N | Flat fee (¥) for the whole party |
| `pricePerPerson` | N | Fee (¥) per upgraded seat |
| `maxFamilySize` | N | Largest party the flat family rate covers; `0` = no limit |
| `capacity` | N | Total seats sellable in this zone |
| `upgradedSeats` | N | Atomic counter — seats currently held by pending/paid requests |
| `status` | S | `ACTIVE` \| `INACTIVE` |
| `displayOrder` | N | Sort hint for the admin/self-service UI |
| `mapRegion` | M | Optional `{x, y, width, height}`, fractions (0-1) of the seat map image — see "Map regions" below. Absent entirely when the admin hasn't drawn one. |

GSI `EventTypeStatusIndex` (hash `eventType`, range `status`) — mirrors the
`entry_slots` table's pattern for querying active rows.

### Map regions

Each zone can optionally claim a rectangular hotspot on the event's seat
map image, drawn by dragging directly on the image in the Seat Upgrades
tab's zone form (`MapRegionEditor.jsx`) — no separate coordinate-entry UI.
`x`/`y`/`width`/`height` are fractions of the image's own box (not
pixels), rounded to 4 decimal places client-side before saving, so the
same numbers position the hotspot correctly however large or small the
image ends up rendered — including on the registrant-facing
`/my/seat-upgrade` page (`SeatUpgrade.jsx`), which overlays a clickable
button per mapped tier directly on its own copy of the same image.

Validated server-side by `seat_upgrade_logic._normalize_region()` — the
one definition of "valid" both `create_tier`/`update_tier` and
`manage_seat_upgrades/handler.py`'s `validate_tier_payload` share: each
value in `[0, 1]`, `width`/`height` strictly positive, and the box fully
inside the image (`x + width <= 1`, `y + height <= 1`). A tier without a
drawn region simply omits `mapRegion` — it still works through the
plain zone-card picker, unaffected.

**Clearing a region** needs to distinguish "the admin didn't touch this
field" from "the admin explicitly removed it" — plain `Optional[dict] =
None` can't tell those apart the way it can for a string field, where
`None` already always means "not sent". `update_tier`'s `map_region`
parameter defaults to a dedicated `UNSET` sentinel instead: omitted
entirely (`UNSET`) leaves the stored region alone, `None` issues a
DynamoDB `REMOVE mapRegion`, and a dict issues a `SET`.

### `seat_upgrades` — purchase requests

| Attribute | Type | Notes |
|---|---|---|
| `upgradeId` | S (hash key) | UUID |
| `eventType`, `tierId`, `tierName` | S | Denormalized for display |
| `pricingMode` | S | `family` \| `person` |
| `unitPrice` | N | Flat family price, or per-person price |
| `quantity` | N | Seats held against the zone's capacity (the whole party's seat count under `family` pricing) |
| `totalAmount` | N | What the registrant owes |
| `registrationId`, `email`, `name` | S | Links back to the registration, but never mutates it |
| `status` | S | `pending` \| `paid` \| `refund_requested` \| `cancelled` (mirrors the registrations `paymentStatus` convention, plus the refund step — see Business Rules) |
| `paymentMethod` | S | Self-declared at request time: `paypay` \| `square` \| `bank` |
| `paymentProvider` | S | Set once a checkout starts — `paypay` or `square` (empty for bank transfer) |
| `providerPaymentId` | S | The provider's own reference: a PayPay `merchantPaymentId` (= our `upgradeId`) or a Square Order id |
| `checkoutUrl` | S | The hosted checkout URL last issued, for resuming an abandoned checkout |
| `paymentReference` | S | The provider's transaction/**payment** id (not the Order id), stamped once paid — this is what a refund is issued against |
| `refundStatus` | S | Set once a refund is requested: `manual_pending` \| `provider_pending` \| `provider_completed` \| `provider_rejected` \| `provider_failed` |
| `refundReference` | S | The provider's own refund id, when an automatic refund was initiated |
| `refundRequestedAt` | S | ISO timestamp of the first refund attempt |
| `dinnerGuests` | N | Celebrity Dinner add-on guest count; `0` = not opted in |
| `dinnerPricePerGuest` | N | Snapshot of the Celebrity Dinner plugin's price/guest at request time; `0` when `dinnerGuests` is `0` |
| `dinnerDiscountPercent` | N | Snapshot of the Celebrity Dinner plugin's add-on discount % at request time; `0` when `dinnerGuests` is `0` |
| `dinnerAmount` | N | What the dinner add-on actually costs — `dinnerGuests * dinnerPricePerGuest` less `dinnerDiscountPercent`, already folded into `totalAmount` |
| `requestedAt`, `updatedAt` | S | ISO timestamps |

GSIs: `RegistrationIdIndex` (self-service lookups), `EventTypeIndex` (admin
listing) — both hash + `requestedAt` range — and `ProviderPaymentIdIndex`
(hash `providerPaymentId`, sparse — only populated once a checkout starts)
for the webhook and return-page reconcile paths to find a request from
nothing but the provider's own reference.

**Capacity accounting**: `upgradedSeats` is adjusted with a conditional
`UpdateItem` (same technique as `entry_slots.reservedSeats`) so concurrent
purchases can never oversell a zone. A cancellation releases the seats it was
holding back to the pool.

### `seat_upgrade_email_templates` — admin overrides for registrant emails

| Attribute | Type | Notes |
|---|---|---|
| `templateKey` | S (hash key) | `request` \| `paid` \| `refund_requested` \| `cancelled` |
| `subject`, `title`, `tone`, `bannerText`, `introText`, `ctaLabel` | S | Any subset — only fields an admin has actually edited are present; see "Editable email templates" below |
| `updatedAt` | S | ISO timestamp |

Global to the plugin (no `eventType`) — a row only exists once a template
has been customized at least once; an unedited template has no row at all
and the built-in default (`seat_upgrade_emailer.py`'s `DEFAULT_TEMPLATES`)
is used as-is.

---

## Business Rules

- Eligible registrations: `registrationOption` is `event` or `combo`, and
  `paymentStatus` is `paid`. Unpaid or Onam-only registrations can't request
  an upgrade.
- **Family pricing** is only offered while the registration has **no**
  existing upgrade request (pending or paid) — it's a single flat charge
  covering every seat in the party, so it can't be layered with a partial
  per-person purchase. It's also capped per zone by `maxFamilySize` (admin
  Zones form; `0` = no cap) — without one, a large party would pay the same
  flat fee as a small one, undercharging relative to what the tier's price
  was actually set for. A party larger than the cap must use per-person
  pricing instead; enforced server-side in `seat_upgrade/handler.py`'s
  `handle_post`, not just hidden in the UI.
- **Person pricing** can be requested repeatedly, up to the number of seats
  in the party minus seats already covered by another pending/paid request.
- A registrant can cancel their own request (self-service) at either of two
  points, and each behaves differently:
  - **pending** (unpaid) → cancelled outright. Nothing was charged, so the
    zone capacity is released immediately.
  - **paid** → moves to **`refund_requested`**, not straight to `cancelled`.
    The plugin attempts an automatic refund against the payment provider
    (Square/PayPay — see "Refunds" below); bank transfer has no provider to
    call, so it's flagged for the admin to refund by hand. Either way, the
    zone capacity stays **held** — an admin still has to finalize the request
    to `cancelled` (Mark Paid/Cancel actions in the admin dashboard) once the
    money has actually gone back, which is the point capacity is released.
    This mirrors the same "admin confirms" pattern bank-transfer payment
    already uses, so a refund that's still in flight can't accidentally free
    a seat someone else immediately buys before the money issue is resolved.
- Deleting a zone is blocked while it has any sold seats (`upgradedSeats >
  0`) — set it `INACTIVE` instead, matching the `entry_slots` delete guard.
- **Celebrity Dinner add-on**: an opt-in extra on a seat-upgrade request
  (never on its own) — a number of guests, charged at a discount off the
  Celebrity Dinner plugin's own configured price per guest. This module
  owns **none** of that configuration: price, discount %, venue, address,
  Google Map link, and the guest-age condition all live on the Celebrity
  Dinner plugin (`lambdas/celebrity_dinner_logic.py`, admin-edited from the
  Celebrity Dinner tab — `frontend/src/CelebrityDinnerManagement.jsx`), and
  this module only *reads* them —
  `seat_upgrade/handler.py`'s `get_celebrity_dinner_addon_config()` is the
  one place it does. The add-on is only offered when **both**:
  1. the Celebrity Dinner plugin itself is enabled for the event
     (`eventInfo.celebrityDinner.enabled`, the Events tab toggle), and
  2. that plugin's own config has explicitly opted into being bundled
     (`addOnEnabled`, set on the Celebrity Dinner tab, alongside the
     `discountPercent` applied when bundled this way — the standalone
     sign-up stays full price).

  The charge is folded straight into the request's `totalAmount`, so
  checkout, refunds, and revenue stats all see one correct number without
  knowing the add-on exists. `dinnerPricePerGuest` and `dinnerDiscountPercent`
  are snapshotted onto the upgrade record at request time, so a later admin
  change to either doesn't rewrite what an existing registrant was actually
  charged.
  - **Kids under 6 aren't permitted at the dinner** — `dinner_eligible_seats()`
    (seat_upgrade/handler.py) is the registration's `attendeeCounts` minus
    `kidsUnder6`, so they're excluded from the eligible headcount entirely
    rather than just warned about. This mirrors the Celebrity Dinner
    plugin's own `eligible_guest_count()` — duplicated code implementing
    the same fixed rule (see "copied module" convention below), not a
    separately configurable value.
  - **Cross-checked against the party, cumulatively**: `dinnerGuests`
    requested, plus whatever's already committed on this registration's
    other active (pending/paid/refund_requested) requests, can't exceed
    that eligible headcount — enforced server-side in `handle_post`, the
    same pattern as the per-person seat quantity check just above. `GET
    /my/seat-upgrades` surfaces `dinnerEligibleGuests` (the headcount) and
    `dinnerGuestsBooked` (already committed) per registration so the
    frontend can show and cap the remaining count before submitting.
  - **Mutually exclusive with the host's standalone Celebrity Dinner
    registration** (a separate, always-full-price sign-up not tied to a
    seat upgrade) — a registration may use one dinner path or the other,
    never both. Enforced from both sides: `handle_post` here refuses to
    bundle the add-on while `has_active_standalone_celebrity_dinner()`
    finds an active standalone sign-up (env var
    `CELEBRITY_DINNER_REGISTRATIONS_TABLE`, blank/skipped if that host
    feature isn't deployed), and the host's own `upsert_my_celebrity_dinner`
    symmetrically refuses a standalone sign-up while this add-on is active
    (`has_active_seat_upgrade_dinner()`, env var `SEAT_UPGRADES_TABLE` on
    `my_registrations`). `GET /my/seat-upgrades` also surfaces
    `hasStandaloneCelebrityDinner` per registration (checked only when the
    add-on is enabled) so `SeatUpgrade.jsx` can hide the add-on and explain
    why up front, instead of the registrant only finding out from
    `handle_post`'s 400 on submit — the same proactive treatment
    `MyRegistrations.jsx`'s own Celebrity Dinner section already gives the
    reverse case.
  - **Both plugins work independently**: an event can enable seat upgrades
    without the Celebrity Dinner at all (the add-on simply never appears),
    or enable the Celebrity Dinner as a standalone sign-up without ever
    exposing it as a seat-upgrade add-on (`addOnEnabled` left off). Neither
    plugin depends on the other being deployed — every env var this module
    reads from the Celebrity Dinner plugin (`CELEBRITY_DINNER_CONFIG_TABLE`,
    `CELEBRITY_DINNER_REGISTRATIONS_TABLE`) defaults to blank, which simply
    disables the add-on and the exclusivity check rather than erroring.

### Per-event enablement

The plugin is **opt-in per event**, off by default — configured from the
Events tab in the admin dashboard, stored at `eventInfo.seatUpgrade`:

```json
{
  "enabled": true,
  "url": "",
  "seatMapImageUrl": "https://.../media/seat-upgrade/onam_2026_1234567.jpg",
  "startDate": "2026-10-01",
  "endDate": "2026-10-20",
  "paymentMethods": ["paypay", "square", "bank"]
}
```

The Celebrity Dinner add-on's own on/off switch and pricing live entirely
on the *Celebrity Dinner* plugin's config, not here — see "Celebrity
Dinner add-on" under Business Rules above.

- **`enabled`** — while `false` (or the block is absent, e.g. an event
  created before this existed), the feature is off entirely: the "Seat
  Upgrades" admin tab is hidden unless *some* event has it enabled, that
  event doesn't appear in the tab's event picker, the registrant-facing
  `/my/seat-upgrade` page won't list that event's registrations, and the
  "Upgrade My Zone" button in *My Registrations* doesn't render for them.
  Both the self-service Lambda's `handle_get` (list) and `handle_post`
  (create request) check this server-side — the frontend hiding it is a
  convenience, not the enforcement.
- **`url`** — overrides where "Upgrade My Zone" links to (default: the
  built-in `/my/seat-upgrade` page). Set an external URL (`http(s)://...`)
  to route an event through a different vendor/form entirely, or an
  internal path (starting with `/`) to point elsewhere in this app. Read by
  `lambdas/my_registrations/handler.py`'s `event_seat_upgrade_config()` (host
  code — a registrant's own page has no other way to see an event's config)
  and surfaced per-registration as `reg.seatUpgrade = {enabled, url}`.
- **`seatMapImageUrl`** — a venue seat map shown on the `/my/seat-upgrade`
  page above the zone picker, so registrants can see which physical seats
  each zone refers to. **Mandatory** when enabling the plugin from the
  Events form — the Save button is blocked client-side without one (see
  "Uploading it" below); not re-enforced server-side by `manage_events`,
  which treats `eventInfo` as an opaque pass-through blob for every plugin,
  not just this one.
- **`paymentMethods`** — which of `paypay`/`square`/`bank` registrants can
  choose for *this event*; defaults to all three once enabled if never
  configured before (matches the plugin's original always-on
  behavior). Enforced server-side at request creation (`handle_post`) *and*
  at checkout (`handle_checkout`) — not just hidden from the payment-method
  `<select>` — so a direct API call can't bypass an admin's restriction.
- **`startDate`/`endDate`** — optional `YYYY-MM-DD` bounds on the
  registrant-facing booking window (either or both may be blank: no start
  bound opens immediately once enabled, no end bound stays open
  indefinitely). Compared against today's UTC date by
  `seat_upgrade/handler.py`'s `window_status()` — `"not_started"` /
  `"open"` / `"ended"`. Outside `"open"`, `handle_get` returns an empty
  `tiers` list (existing requests still show, and can still be cancelled —
  only *new* zone offers are gated) and `handle_post` rejects a new request
  with a 400 naming the relevant date; this is enforced server-side, same
  as `enabled`. Every registration in `GET /my/seat-upgrades`' response
  carries `upgradeWindow: {status, startDate, endDate}` so the frontend can
  explain *why* no zones are showing instead of just showing nothing. This
  only gates new bookings — it doesn't restrict when an admin can send the
  announcement email below (e.g. announcing "opens {date}" ahead of time is
  expected).

#### Uploading the seat map image

`POST /admin/events/{eventType}/seat-upgrade-seat-map` (Cognito-protected,
`manage_seat_upgrades/handler.py`'s `handle_seat_map_upload`) accepts a
base64-encoded image in the JSON body and uploads it to the host's shared
assets S3 bucket (`backend/assets.tf` — the same bucket
`manage_site_config`'s carousel upload already writes to), returning
`{"imageUrl": "..."}`. It doesn't persist anything itself — the Events
form (`AdminDashboard.jsx`'s `handleSeatMapFileSelect`) uploads
immediately on file selection, then stores the returned URL in
`eventForm.seatUpgradeSeatMapImageUrl` for the *next* normal event Save to
write into `eventInfo.seatUpgrade.seatMapImageUrl` alongside everything
else on that form. JPEG/PNG/WebP, 5 MB max. Unlike the carousel upload,
replacing an image doesn't delete the old S3 object (there's no per-event
config record here to read the old key back from before overwriting it) —
orphaned objects under `media/` self-expire via the existing 90-day
lifecycle rule in `assets.tf`.

The upload function lives in the *plugin's* `frontend/api.js`
(`uploadSeatUpgradeSeatMap`), imported into the *host's* `AdminDashboard.jsx`
via the `@plugins` alias — the image is captured on the Events form (a host
page) but the endpoint and S3 access are plugin-owned, so this is the one
place a host component reaches into plugin frontend code rather than the
reverse.

Both the plugin's own `seat_upgrade/handler.py` (`get_seat_upgrade_config`)
and the host's `my_registrations/handler.py`
(`event_seat_upgrade_config`) read this same `eventInfo.seatUpgrade` shape
independently — they're separate Lambda deployments with no shared import,
same "copied module" convention as `seat_upgrade_emailer.py`.

**Upgrading from before this existed**: every event defaults to
`enabled: false` until an admin explicitly turns it on (Events tab → Seat
Upgrade Plugin → Enabled → pick payment methods → Save). An event that was
relying on the old always-on behavior will look like the feature vanished
until that one-time step is done.

### Refunds

`seat_upgrade/handler.py`'s `attempt_refund()` runs automatically the moment
a paid request moves to `refund_requested`:

- **Square** — calls `square.create_refund()` against the stored
  `paymentReference` (the Square **Payment** id, not the Order id). Square
  refunds are asynchronous: a successful call returns `PENDING`, not
  `COMPLETED` — there's no `refund.updated` webhook subscription wired up
  yet, so an admin currently confirms final settlement in the Square
  dashboard before marking the request `cancelled`.
- **PayPay** — calls `paypay.create_refund()`, sharing the same
  *** VERIFY BEFORE PRODUCTION USE *** caveat as PayPay's checkout path (see
  `docs/PAYMENTS.md`) — a failed call here is recorded as `provider_failed`
  and falls back to the same manual flow as bank transfer.
- **Bank transfer**, or any refund attempt that fails or can't find a stored
  payment reference — recorded as `manual_pending`; the admin dashboard's
  Purchase Requests tab surfaces `refundStatus` next to the request so it's
  never a silent dead end.

### Email notifications

Sent via `seat_upgrade_emailer.py` (bundled into all three Lambdas):

| Trigger | Registrant email | Admin email |
|---|---|---|
| Request submitted | "Seat Upgrade Requested" | "Seat upgrade requested" |
| Payment confirmed (checkout webhook, return-page reconcile, or admin marks bank transfer paid) | "Seat Upgrade Confirmed" | "Seat upgrade paid" |
| Self-service cancel of a paid request | "Refund Requested" (notes whether it was auto-initiated or is being handled manually) | "Seat upgrade refund requested" (includes `refundStatus`/`refundReference`) |
| Admin finalizes to `cancelled` | "Seat Upgrade Cancelled" | — (the admin performed the action) |

The "payment confirmed" email fires from exactly one place per payment no
matter which of the three paths reaches it — `mark_paid_from_provider`
returns `(record, was_newly_paid)`, and only a real pending→paid transition
sends it, so a webhook retry or a webhook racing the return-page reconcile
call never double-sends.

### Editable email templates

The copy for the four registrant-facing emails (`request` / `paid` /
`refund_requested` / `cancelled` — the admin notification isn't included,
it's an internal ops email, not brand-facing) is admin-editable from the
Seat Upgrades tab's **Email Templates** sub-tab, without a code change or
redeploy. Global to the plugin, not per-event — one set of templates for
every event using the plugin.

**Storage**: `seat_upgrade_emailer.py`'s `DEFAULT_TEMPLATES` dict holds the
built-in copy for each of the four keys. An admin's customization is a row
in the `seat_upgrade_email_templates` DynamoDB table (hash key
`templateKey`), read back and merged onto the default field-by-field — a
field an admin leaves blank always falls through to its default rather
than rendering an empty sentence. Only six fields are editable per
template: `subject`, `title`, `tone` (`success`/`pending`/`danger` — the
banner's color), `bannerText`, `introText`, `ctaLabel` (blank = no
button). Layout, the logo, footer, and detail table stay fixed in code —
see `seat_upgrade_emailer.py`'s module docstring and its `_shell`/
`_banner`/`_button`/`_detail_table` building blocks.

**Placeholders**: `{eventName}`, `{tierName}`, `{seats}`, `{amount}` are
available in every field, substituted by `_format()`. A typo'd or unknown
placeholder doesn't break the send — it falls back to the literal
template text and logs a warning, rather than raising.

**API** (`manage_seat_upgrades/handler.py`'s `handle_email_templates`,
Cognito-protected):

| Method | Path | Purpose |
|---|---|---|
| GET | `/admin/events/{eventType}/seat-upgrade-email-templates` | List all 4 (default + override merged), each flagged `isCustomized` |
| PATCH | `/admin/events/{eventType}/seat-upgrade-email-templates/{templateKey}` | Save an override (partial — only sent fields change) |
| DELETE | `/admin/events/{eventType}/seat-upgrade-email-templates/{templateKey}` | Reset to default |
| POST | `.../seat-upgrade-email-templates/{templateKey}/preview` | Render draft (not-yet-saved) field values against sample data — nothing persisted |

`{eventType}` in the URL is ignored by these routes — see the note at
"Per-event enablement" above. They ride that mount point purely to avoid
new API Gateway wiring; the frontend calls them with a constant
`"_global"` placeholder (`plugins/seat-upgrade/frontend/api.js`).

**Preview**: the editor's Preview button calls the `/preview` endpoint
with the form's current (unsaved) values and renders the returned HTML in
an iframe — the same `_render_upgrade_email()` codepath a real send uses,
against a canned sample upgrade, so what's previewed is exactly what a
registrant would see.

### Announcement email

A one-off broadcast, distinct from the four upgrade-lifecycle templates
above — it isn't keyed to an upgrade record, has no admin-editable copy
or live preview, and isn't in `DEFAULT_TEMPLATES`/`EMAIL_TEMPLATE_KEYS`.
An admin sends it (Seat Upgrades tab → **Send Announcement**, next to
Refresh) to tell every paid **event**/**combo** registrant on that event
that premium seating is available — the same population `/my/seat-upgrade`
itself serves (see `ELIGIBLE_OPTIONS`).

**`POST /admin/events/{eventType}/seat-upgrade-announcement`**
(Cognito-protected, `manage_seat_upgrades/handler.py`'s
`handle_announcement`) — no body. Rejects with a 400 if the plugin isn't
`enabled` for the event, or it has no `ACTIVE` tier yet (nothing to
announce). Otherwise queries `EventTypeIndex` on the registrations table
directly (the one place this Lambda touches that table — every other
route goes through `seat_upgrade_logic`, which is deliberately independent
of it), filters to paid event/combo registrations, and sends
`seat_upgrade_emailer.send_announcement_email()` to each — a premium-styled
email (tier cards for up to 3 active zones, the seat map image if
configured, a "booking opens/available through {date}" line from the
booking window, and a "View Upgrade Options" button to
`/my/seat-upgrade`). One SES failure doesn't abort the batch; the response
is `{"sent", "failed", "total", "eventName"}`.

**Re-triggerable, on purpose**: there's no "already sent" tracking — every
call re-sends to the *current* full list of eligible registrants. Calling
it again later (the admin UI's button relabels to **Resend Announcement**
once it's been sent this session) is both how you remind people and how
new registrants since the last send get caught up, in one action rather
than a per-registrant one.

---

## Backend

All under `plugins/seat-upgrade/backend/lambdas/`:

### Shared logic — `seat_upgrade_logic.py`

Tier CRUD, atomic capacity reservation/release (`_adjust_tier_seats`), and
upgrade-request CRUD (`create_upgrade_request`, `update_upgrade_status`,
`cancel_upgrade_request`). `price_for_mode(tier, pricing_mode, quantity)`
computes `(unitPrice, totalAmount)` for either pricing mode. Payment-specific
additions: `record_checkout`, `get_upgrade_by_provider_payment_id`,
`mark_paid_from_provider` (idempotent, returns `(record, was_newly_paid)` —
see `docs/PAYMENTS.md`), `record_refund_attempt` (bookkeeping only — see
"Refunds" above).

### Emailer — `seat_upgrade_emailer.py`

Shared SES sender, bundled into all three Lambdas (same "copied module"
convention as the host's own `emailer.py` files). Works entirely off an
`upgrade` record — no registration or event-config lookup needed — so the
unauthenticated webhook Lambda can send the same emails as the others. See
"Email notifications" and "Editable email templates" above for what it
sends, when, and how an admin customizes the copy without a redeploy.

### Payment adapters — `paypay.py`, `square.py`

Dependency-free (stdlib `urllib` only — this repo's Lambdas ship as flat
zipped `.py` files with no pip-install step). Each exposes `create_checkout`,
`get_payment_status`, and provider-specific webhook handling. See
`docs/PAYMENTS.md` for the trust model and current verification status of
each.

### Admin Lambda — `manage_seat_upgrades/` (Cognito-protected)

| Method | Path | Purpose |
|---|---|---|
| GET / POST | `/admin/events/{eventType}/seat-upgrade-tiers` | List / create zones |
| GET / PATCH / DELETE | `/admin/events/{eventType}/seat-upgrade-tiers/{tierId}` | View / edit / delete a zone |
| GET | `/admin/events/{eventType}/seat-upgrades` | List purchase requests for the event |
| PATCH | `/admin/events/{eventType}/seat-upgrades/{upgradeId}` | Update status (`paid` / `cancelled` / `refund_requested`) — see "Refunds" above for finalizing a refund |
| POST | `/admin/events/{eventType}/seat-upgrade-seat-map` | Upload a seat map image; returns `{imageUrl}` — see "Uploading the seat map image" above |
| POST | `/admin/events/{eventType}/seat-upgrade-announcement` | Email every paid event/combo registrant that premium seating is available — see "Announcement email" above |
| GET | `/admin/events/{eventType}/seat-upgrade-email-templates` | List all 4 email templates — see "Editable email templates" above |
| PATCH / DELETE | `.../seat-upgrade-email-templates/{templateKey}` | Save an override / reset to default |
| POST | `.../seat-upgrade-email-templates/{templateKey}/preview` | Render draft field values against sample data |

Every 500 response here includes a `detail` field with the actual exception
message (via `logger.exception` + `str(exc)`), surfaced by the plugin's
`requestAdminJson` into the thrown Error's message — an admin sees the real
cause immediately rather than a generic "Failed to update seat upgrade
tier" with no way to tell why short of pulling CloudWatch logs.

### Self-service Lambda — `seat_upgrade/` (reg-token gated)

Same auth convention as `my_registrations` — the Authorization header carries
the HMAC-signed token minted at OTP verification; the Lambda scopes every
result to the caller's own email.

| Method | Path | Purpose |
|---|---|---|
| GET | `/my/seat-upgrades` | Every eligible, paid Celebrity Event registration the caller owns, with that event's active zones (empty outside the booking window — see `upgradeWindow`), and the caller's existing requests |
| POST | `/my/seat-upgrades` | Request an upgrade: `{ registrationId, tierId, pricingMode, quantity, paymentMethod, dinnerOptIn, dinnerGuests }` (`quantity` is ignored/optional for `pricingMode: "family"`; `dinnerOptIn`/`dinnerGuests` are optional — see "Celebrity Dinner add-on" above) |
| GET | `/my/seat-upgrades/{upgradeId}` | One request's current status — also re-checks the payment provider directly if a checkout is still pending |
| DELETE | `/my/seat-upgrades/{upgradeId}` | Cancel one's own request — a pending one outright, a paid one into `refund_requested` (see "Refunds" above) |
| POST | `/my/seat-upgrades/{upgradeId}/checkout` | Start a PayPay or Square hosted checkout: `{ provider }` → `{ checkoutUrl, provider }` |

See "Email notifications" above for what's sent at each of these steps.

### Webhook Lambda — `seat_upgrade_webhook/` (public, unauthenticated)

| Method | Path | Purpose |
|---|---|---|
| POST | `/webhooks/seat-upgrade/{provider}` | Payment-provider callback — `provider` is `paypay` or `square` |

Trust comes from provider-specific verification, not a shared secret — see
`docs/PAYMENTS.md` for exactly how each branch establishes it.

---

## Frontend

Both under `plugins/seat-upgrade/frontend/`, imported into the host app via
the `@plugins` Vite alias (see the plugin's `README.md`):

- **Admin dashboard** → "Seat Upgrades" tab (`SeatUpgradeManagement.jsx`):
  manage zones (name, description, both prices, capacity, status) per event,
  and review/confirm/cancel purchase requests with running revenue stats. The
  Purchase Requests table's **Transaction ID** column shows `paymentReference`
  (the provider's actual payment id, stamped once paid) or, before that,
  `providerPaymentId` (the checkout-time reference) — whichever is set; a
  bank-transfer request has neither, since there's no provider to reference. A
  `refund_requested` row shows its `refundStatus` inline and swaps the Cancel
  button for "Confirm Refund Done" — finalizing it to `cancelled` once the
  money has actually gone back. The tab itself, and which events its event
  picker offers, are both gated on "Per-event enablement" above — computed
  in `AdminDashboard.jsx` (`getSeatUpgradeConfigFromEventInfo`), not inside
  this component (it just gets a pre-filtered `events` prop). The Enable
  toggle, Seat Upgrade URL, and payment-method checkboxes themselves live in
  the **Events** tab's event form, not here. A third sub-tab, **Email
  Templates** (`EmailTemplatesPanel`, defined in the same file), lets an
  admin edit the subject/title/tone/banner/message/button copy for each of
  the four registrant emails and preview the rendered result in an iframe
  before saving — see "Editable email templates" above.
- **Registrant self-service** → `/my/seat-upgrade`
  (`SeatUpgrade.jsx`), linked from a paid Celebrity Event registration's card
  in *My Registrations*. Shows the event's seat map image (from
  `reg.seatMapImageUrl`) above the zone option cards, which match the
  host's own registration-option card style; lets the registrant pick a
  zone and pricing mode, shows a live estimated total, and — for
  PayPay/Square — redirects straight to hosted checkout after submitting.
  Also handles the post-checkout return (`?upgradeId=&provider=`),
  reconciling that one request's status before showing the page.

---

## Infrastructure

Packaged as a Terraform module at `plugins/seat-upgrade/backend/terraform/`
(its own `main.tf` / `api_gateway.tf` / `variables.tf` / `outputs.tf`) rather
than inline resources in `backend/` — see the plugin's `README.md` for why.
The host wires it in from a single file, `backend/plugin.tf`:

```hcl
module "seat_upgrade" {
  source = "../plugins/seat-upgrade/backend/terraform"
  # ... every input documented in that module's variables.tf
}
```

`backend/api_gateway.tf` folds `module.seat_upgrade.api_trigger_hash` into
its own deployment's trigger hash and adds `module.seat_upgrade` to its
`depends_on`, so a route change in the plugin still redeploys the shared API
Gateway stage in the same `terraform apply`.

No schema changes were needed on any existing table — DynamoDB is schemaless
beyond declared keys/indexes, and the registrations table was deliberately
left untouched.

Deploy with the usual flow:

```bash
cd backend
terraform plan
terraform apply
```

Payment provider credentials are optional Terraform variables (default
`""`); see `docs/PAYMENTS.md` before setting them.

---

## Example: Admin Creates a Zone

```bash
curl -X POST \
  https://your-api-gateway-url/admin/events/celebrity_event/seat-upgrade-tiers \
  -H "Authorization: <cognito-id-token>" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "Zone 1",
    "description": "Front rows, closest to stage",
    "pricePerFamily": 20000,
    "pricePerPerson": 10000,
    "capacity": 100,
    "status": "ACTIVE"
  }'
```

## Example: Registrant Requests an Upgrade

```bash
curl -X POST \
  https://your-api-gateway-url/my/seat-upgrades \
  -H "Authorization: <reg-token>" \
  -H "Content-Type: application/json" \
  -d '{
    "registrationId": "uuid-123",
    "tierId": "uuid-456",
    "pricingMode": "family",
    "paymentMethod": "bank"
  }'
```

## Example: Registrant Starts a Square Checkout

```bash
curl -X POST \
  https://your-api-gateway-url/my/seat-upgrades/upgrade-uuid-789/checkout \
  -H "Authorization: <reg-token>" \
  -H "Content-Type: application/json" \
  -d '{"provider": "square"}'
# => { "checkoutUrl": "https://square.link/u/...", "provider": "square" }
```

The frontend redirects the browser to `checkoutUrl`; Square redirects back to
`/my/seat-upgrade?upgradeId=upgrade-uuid-789&provider=square` once the
registrant pays.
