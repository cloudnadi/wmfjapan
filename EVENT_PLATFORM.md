# Event platform — data model and behaviour

The platform is the whole product: every event, ticket, form, registration and add-on is a platform record. There is no other registration pipeline. Admin how-to: [docs/EVENT_ADMIN_GUIDE.md](docs/EVENT_ADMIN_GUIDE.md); site/auth/deployment: [docs/WMF_EVENTS_SITE.md](docs/WMF_EVENTS_SITE.md).

## Tables (`backend/dynamodb_event_platform.tf`, `backend/dynamodb_site_config.tf`)

| Table | Key | Notes |
|---|---|---|
| `events-v2` | `eventId`; GSI `SlugIndex`, `StatusStartIndex(status, startsAt)` | status draft / published / unlisted / closed / archived; ISO times with `+09:00`; `access{guestsAllowed}` (default true); `capabilities`, `slots`, `upgrades`, `seatMap` for add-ons |
| `event-ticket-types` | `eventId` + `ticketTypeId` | whole-JPY `priceJpy` (0 = free), optional `earlyBirdPriceJpy` + `earlyBirdCutoff` (applies to everyone), `quantityLimit`, `perPersonCap` |
| `form-schemas` | `formId` + `version` | immutable versions; a registration stores `(formId, version)`; templates via `KindIndex` |
| `event-registrations` | `registrationId`; GSIs `EventIndex`, `EmailIndex`, `CheckInTokenIndex` and the payment/expiry indexes | `answers` map + form version snapshot, `tickets`, `addons`, `addonJpy`, `addonOps`, `accountEmail` / `accountSub` for signed-in registrants (guests store neither) |
| `event-counters` | `counterKey` | atomic capacity counters: `EVENT#id`, `TICKET#id#type`, and the add-on counters `SLOT#…`, `UPGRADE#…`, `SEAT#…` |
| `audit-log` | `entityKey` + `sortKey` | admin changes (events, forms, registrations, exports, emails) |
| `site-config` | `configKey` | runtime settings edited in the admin console; today only `payment_methods_config` |

## Code

| Module | Role |
|---|---|
| `lambdas/event_model.py` | validation, locked core form fields, form versioning |
| `lambdas/form_engine.py` | server-side validation of submissions (conditions, groups, files) |
| `lambdas/event_pricing.py` | authoritative pricing, booking window, access decision |
| `lambdas/event_capabilities.py` | per-event capabilities and the server-side add-on gate |
| `lambdas/event_addons.py` | entry slots, zone upgrades, seat reservation: config validation, public view, pricing, counter operations |
| `lambdas/event_registrations.py`, `event_reg_store.py` | registration lifecycle (book, hold, pay, cancel, expire, promote) with conditional transactions |
| `lambdas/event_registration_admin.py`, `event_exports.py` | admin registration tools, CSV/Excel export |
| `lambdas/event_mailer.py` | SES e-mails and QR tickets |
| `lambdas/cognito_jwt.py` | attendee ID-token verification (stdlib RS256 via JWKS) |
| `lambdas/square.py` | Square checkout links and webhook signature check |
| `lambdas/event_platform_public`, `event_platform_webhook`, `event_platform_expiry`, `manage_event_platform` | the four Lambda entry points |

Core form fields (locked: relabel/reorder only): full name, email, phone, privacy consent, terms consent. The account e-mail comes from the verified token, never from the client.

## Public behaviour

- `/events`, `/events/{slug}`, `/events/{slug}/register`, `/events/{slug}/registered`, `/my/events`.
- **Prices** are computed on the server from ticket quantities and selected add-ons; the browser never sends amounts. One price per ticket type; an optional early-bird price applies to everyone until its cut-off.
- **Access:** guests may register unless the event turns "guests allowed" off, in which case a signed-in account is required.
- **Payment:** free events confirm immediately. Paid events hold the seats *awaiting payment*: card (Square checkout, webhook-confirmed, 30-minute hold) or bank transfer / PayPay (instructions from the Payment methods settings, 7-day hold, an admin marks paid). Unpaid holds are released every 5 minutes; a payment arriving after release re-takes the seats if still free, otherwise the registration is flagged *refund pending*.
- **Capacity and waiting list:** event and per-ticket limits are enforced by one conditional DynamoDB transaction per booking or release, so concurrent sign-ups cannot overbook and replays are idempotent. A full event with the waiting list on waitlists new registrations (promotion is manual).
- **Edit / cancel:** signed-in registrants until registration closes (answers only). Cancelling releases seats and every add-on unit; a paid cancellation becomes *refund pending*. Guests use the link in their confirmation e-mail or contact the organisers.
- **E-mails (SES):** confirmation (with QR), awaiting payment, waiting list, promotion, cancellation, hold expired.

## Add-ons

Switched on per event with `capabilities.<name>.enabled: true` (anything else is off) and configured on the event:

| Add-on | Event config | Rule |
|---|---|---|
| `entrySlots` | `slots: [{slotId, name, startsAt?, endsAt?, capacity}]` | the registrant picks one slot; every ticket takes one place in it |
| `zoneUpgrade` | `upgrades: [{upgradeId, name, description?, priceJpy, limit?}]` | 0..n of each upgrade, at most one per ticket; priced per unit |
| `seatReservation` | `seatMap: {sections: [{sectionId, name, rows, seatsPerRow, extraPriceJpy}]}` | exactly one seat per ticket; a seat can be held once |

Registration payload: `addons: {entrySlots: {slotId}, zoneUpgrade: {upgradeId: qty}, seatReservation: {seats: [...]}}`. The server rejects an add-on the event does not offer with `400 feature_disabled`, and a slot/upgrade/seat that is full or taken with `409 addon_unavailable` (add-on conflicts are never waitlisted). Units are reserved in the same transaction as the registration, stored in `addonOps`, and returned exactly on cancel or expiry; they are re-taken on promotion or a late payment.

## Admin

Roles are Cognito groups on the admin pool, checked inside `manage_event_platform`: `Admins` (super admin: everything, including delete, payment settings and erasing personal data) and `EventManagers` (events, tickets, forms, add-ons, registrations, check-in). Anyone else gets 403.

API (`/admin/event-platform/…`): `GET me` · `GET|POST events` · `GET|PUT|DELETE events/{id}` · `POST events/{id}/status|duplicate|images` · tickets, form, form-templates, audit, dashboard, registrations (list, detail, cancel, promote, mark-paid, refund, check-in, export, email, erase) · `GET|PUT payment-methods` (PUT super admin only). Updates carry the current `revision` (stale = 409); slugs are unique through a transactional lock item; rich text and images are sanitised and size/type-checked server-side; every change writes an audit entry.

Check-in: QR payload `WMFEV1.<registrationId>.<token>` (registration ids look like `WMF-7QX2-M9PA`); a forged token looks like an unknown registration, and two scanners cannot check the same person in twice.

## Security notes

- Public endpoints are rate-limited per IP and per e-mail (hashed, fixed windows; fails open if DynamoDB is unavailable); at most 3 unpaid holds per e-mail per event.
- A present-but-invalid token is a 401, never a silent guest registration.
- The amount a payment provider reports must equal the server total, otherwise the registration is flagged `paymentMismatch` and stays unpaid. Refunds claim the registration first, so a double click cannot refund twice.
- Uploads: type allow-list, magic-byte and size checks; new uploads land in `event-uploads-pending/` (expire after 2 days) and move to the private `event-uploads/` when attached; admin download uses a 5-minute presigned URL.
- Personal-data erasure (super admin) anonymises a registration and deletes its files, keeping accounting facts.
- Exports neutralise spreadsheet formula injection.

## Tests and monitoring

`scripts/run_tests.sh` runs every backend test file in its own process; `test_event_moto_e2e` runs the real stores against mocked DynamoDB with the real table layout (`pip install "moto[dynamodb]"`). `backend/cloudwatch.tf` has error alarms for the four Lambdas and a "hold-expiry job stalled" alarm; no notification target is attached yet (add an SNS topic to `alarm_actions`).
