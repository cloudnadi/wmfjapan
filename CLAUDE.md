# CLAUDE.md — WMF Japan Events (wmfevents.cloudnadi.com)

Event registration site for WMF Japan (org site: https://www.wmfjapan.org/). A generic front end for the in-repo **event platform**: events/tickets/forms/availability/per-event features come from the platform API; attendees sign in with their own Amazon Cognito user pool (email + social via Hosted UI/PKCE) or check out as guests. **Read `docs/WMF_EVENTS_SITE.md` first.** Platform data model: `EVENT_PLATFORM.md`; admin guide: `docs/EVENT_ADMIN_GUIDE.md`. Run tests with `scripts/run_tests.sh` (backend) and `cd frontend && npm test` (frontend).

Key decisions: nothing event-specific is hard-coded (the former Onam page/route/assets are gone; an Onam event is just a platform event); per-event features are `event.capabilities` (defaults in `lambdas/event_capabilities.py`, mirrored in `frontend/src/eventModel.js`; enforced server-side); attendee accounts use a **separate** Cognito pool (`backend/cognito_attendees.tf`) — never put attendees in the admin pool; org-specific text/URLs live in `frontend/src/siteConfig.js`; strings go through `t()` (`en.json`, `ja.json` falls back to English); no secrets or deployment values are committed (`backend/*.tfvars` and `backend.*.hcl` are git-ignored, use the `*.example` templates). Legacy (original-pipeline) Onam-shaped Lambdas/admin tabs remain for existing records and are not reachable from public pages. Admin: `EventManagers` group + `Admins` = super admin; legacy admin routes unchanged. Phase 4 payment decisions: card (Square) + bank/PayPay manual; holds 30 min card / 7 days manual; waitlist promotion manual; member edit/cancel until registration close; paid cancellations become refund_pending; guests allowed by default (per-event toggle); members-only = valid now AND covers renewal year; early-bird applies to NPO members only. Work happens on the branch named in the task (currently `develop`), never directly on `main`.

## Stack
| Layer | Tech |
|---|---|
| Frontend | React 18 + Vite 5, plain JS/JSX, one global `styles.css` (+ `publicEvents.css`, `site.css`). **No react-router, no TypeScript, no i18n library.** Routing is a hand-rolled `currentPath` switch in `frontend/src/App.jsx` (pushState + lazy chunks). Deps: `react`, `qrcode`, Cognito SDK, `jsqr`; dev: Vitest + Testing Library. |
| Backend | Python AWS Lambdas (`lambdas/<name>/handler.py`) behind API Gateway REST (Terraform), boto3 only. No framework. |
| DB | DynamoDB, on-demand, PITR. Tables in `backend/dynamodb.tf` (+ plugin terraform). |
| Admin auth | Cognito user pool, groups `Admins` (super admin) and `EventManagers` (new; only enforced by the event-platform Lambda via `lambdas/admin_roles.py`), API Gateway Cognito authorizer on `/admin/*` (`api_gateway_admin.tf`). **Lambdas never inspect groups/claims** — any pool user is a full admin. |
| Member auth | Passwordless: `auth_otp` emails a 6-digit OTP, only for emails present in the `members` table; returns a custom HMAC-signed token (`TOKEN_SECRET`). Every `/my/*` and register Lambda re-verifies it in code (no API Gateway authorizer). |
| Payments | Square (card) and PayPay (QR) via `square.py` / `paypay.py`, with webhooks; bank transfer / PayPay-link are manual with admin "mark paid". Onam event registration payment is *manual* (links in `eventInfo`), NPO membership and seat purchases use Square webhooks. |
| Email | SES (`lambdas/*/emailer.py`, `qr_email.py`); QR via vendored `lambdas/qr_layer`. |
| Hosting/CI | S3 + CloudFront, Terraform in `backend/`, GitHub Actions deploy (preview/production, manual dispatch). CI test workflow: `.github/workflows/tests.yml`. |

## Layout
- `backend/` Terraform (API routes split across `api_gateway*.tf`, `lambda.tf` ~1.5k lines).
- `lambdas/` Lambdas + shared modules. **Shared modules are copy-pasted per Lambda** (`onam_pricing.py` ×3, `npo_membership.py` ×6, `checkin_pdf.py` ×2) and bundled by `scripts/package_lambdas.sh`. Edit all copies or consolidate deliberately.
- `plugins/seat-selection`, `plugins/seat-upgrade` — self-contained feature plugins (frontend + lambdas + terraform), imported via `@plugins/*` alias.
- `frontend/src/` — `AdminDashboard.jsx` (6.4k lines, all admin tabs), `MyRegistrations.jsx`, `api.js` (all API calls), `memberShared.jsx`.
- `tests/` — stdlib `unittest`, run `pip install boto3 && python3 -m unittest discover -s tests`.
- Docs: `MULTI_EVENT_ARCHITECTURE.md`, `ADMIN_DASHBOARD_SETUP.md`, `MEMBER_LOGIN_GATE.md`, `SEAT_SELECTION.md`.

## Data model (current)
- **members** (PK `email`): `memberid, fullname, phone, mobile, statusid, npoMembershipActive, npoMembInterested`. Imported from CSV (`scripts/import_members.py`); also the login gate.
- **npo_membership** (PK `memberid`, SK `transactionNo`; GSI `providerPaymentId-index`): one row per membership transaction with `membDateFrom/membDateTo`, amount, `paidVia`, pending→paid state, `linked` spouse txns. **Membership is valid by date range**, evaluated by `npo_membership.evaluate(...)` (`validNow`, `coversRenewal`, renewal year `RENEWAL_MEMBERSHIP_YEAR`). There is no stored "membership status" field; derive it.
- **events** (PK `eventType`, GSIs `HostIdIndex`, `StatusIndex`): `eventName, eventDate (free string), status active|inactive|archived, venue, capacity, registrationPath, registration{closesOn, sadhya*/celebrity* capacities+closed flags, cancellationEnabled…}, eventInfo{payment links, seatSelection, seatUpgrade, celebrityDinner…}`. Loose bag-of-config; no schema.
- **registrations** (PK `registrationId`; GSIs `EmailIndex`, `EventTypeIndex`, `EventHostIndex`, `SubmittedAtIndex`, `EventDateIndex`, `RegistrationPassTokenIndex`): flat Onam-shaped record — `registrationOption (onam|event|combo), attendeeCounts{adults,kids914,kids68,…}, additionalAdultEmails, amounts, paymentMethod, paymentStatus, slot/allocation ids, registrationPassToken, checkedIn…`. Multiple registrations per email+event are allowed.
- Others: `entry_slots`, `slot_reservations`, `entry_allocations`, `agms`, `agm_registrations`, `partners`, `site_config`, `login_otp` (TTL), `visits`, `member_announcements`, celebrity-dinner and seat tables (plugins).

## How event registration works today
1. `RegistrationLogin` → `/auth/request-otp` → `/auth/verify-otp` → token in localStorage.
2. (Original pipeline, no longer reachable from the public site: the `OnamRegistration.jsx` page and `/onam/register` route were removed.) New events register through `/events/{slug}/register` → `POST /public/events/{slug}/register`.
3. `register/handler.py` loads the event, then **branches to `handle_onam_registration`** for the Onam event: server-side re-pricing (`onam_pricing`), NPO discount tiers, optional join/renew, capacity check, entry-slot/allocation reservation, SES email + QR pass, admin notification. Only generic part: event lookup/status/closing date.
4. Self-service: `my_registrations` Lambda (list/update/delete, check-in, slot, celebrity-dinner). Admin: `manage_registrations` (CRUD, re-pricing), `export_csv`, `checkin_pdf`, `manage_reconciliation`.

**Hardcoded per event:** option names, attendee categories, pricing, ¥1000 fee, capacity pools (`SEAT_POOLS`), env `EVENT_TYPE_ONAM`/`ONAM_CAPACITY`, email bodies, admin report/table columns, frontend route and form. Nothing is driven by a schema.

## Admin today
Tabs in `AdminDashboard.jsx`: Registrations, Events, Users, Partners, plus plugin/AGM/NPO/site-config/announcement/celebrity-dinner/slot/seat panels. Events CRUD = `manage_events` (hard delete, no audit, no duplicate). **One role only (Admins group).**

## Known risks / debt
1. **Overbooking:** `get_capacity_used` *scans* the registrations table then writes — read-then-write race. Only slot/allocation code uses `ConditionExpression`. Needs counter items + conditional/transact writes.
2. Admin RBAC is not enforced in Lambdas (see above); an "event manager" role needs claim checks added to *every* admin Lambda or a separate set of routes.
3. Table scans for listing/capacity/stats; fine at current scale, not for the new dashboard.
4. Copy-pasted shared modules; 6k-line `AdminDashboard.jsx`/`styles.css`; `.bak`/`.deprecate` files in `backend/`.
5. Full-table `eventDate` is a free string; no timezone handling (JST assumed).
6. No audit log, no CI tests, no rate limiting beyond a global API usage plan (50 rps); OTP token is a custom HMAC format.
7. No i18n infrastructure (strings inline in JSX, some English-only emails).
8. File uploads do not exist yet (need S3 presigned uploads, type/size checks, malware-safe serving).
9. Test baseline (see below) has pre-existing isolation problems.
10. Members statuses: no canonical "active/expired/none" field; derived from date ranges + `npoMembershipActive` flag (can disagree).

## Test baseline (before any change)
`python3 -m unittest discover -s tests` → 165 run, 8 failures + 3 errors. **Each file passes when run alone** except `test_checked_in_locks.py` (import error) and `test_member_announcements.py` (import error); the `test_npo_orders` failures appear only in the combined run (cross-module `sys.path`/`importlib` collisions). `scripts/run_tests.sh` runs each file in its own process (all pass). Frontend: `cd frontend && npm test` (Vitest) and `npm run build`.

## Conventions to follow
- Match existing style: Lambda `respond()`/`cors_headers()` helpers, env-var config wired in `lambda.tf`, `parse_json_body`, server-side authoritative pricing (never trust client amounts), idempotent conditional writes for payment state.
- New code must be **additive**: new tables/attributes/routes only; do not change existing routes' request/response shapes. Existing `eventType` rows and registrations stay readable by legacy code paths.
- Never hardcode secrets; use Terraform vars/env vars (`TOKEN_SECRET`, Square/PayPay keys already exist).
- UI: reuse `styles.css` classes and existing admin tab patterns; add new frontend code in new files, not by growing `AdminDashboard.jsx`.
- Prices in JPY, times in JST (Asia/Tokyo), strings through a small `t()` helper once i18n lands (Phase 1/2 decision).
- Small commits, regression tests alongside features.
