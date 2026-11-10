# Event Platform (v2) — data model

Additive layer for admin-designed events. The legacy `events`/`registrations` tables and the Onam flow are unchanged; legacy data is **mirrored** into v2 for listing/reporting (`registrationMode: "legacy"`, registration still goes through `registrationPath`).

## Tables (`backend/dynamodb_event_platform.tf`)
| Table | Key | Notes |
|---|---|---|
| `events-v2` | `eventId`; GSI `SlugIndex`, `StatusStartIndex(status, startsAt)` | status: draft/published/unlisted/closed/archived; times ISO with `+09:00`; `access{membersOnly, guestsAllowed (default true), requireRenewalCover (default true)}` |
| `event-ticket-types` | `eventId` + `ticketTypeId` | whole-JPY `memberPriceJpy`, `nonMemberPriceJpy`, optional `earlyBirdMemberPriceJpy` + `earlyBirdCutoff` (early bird is for NPO members only), `quantityLimit`, `perPersonCap` |
| `form-schemas` | `formId` + `version` (N); GSI `KindIndex` for templates | immutable versions; registrations store `(formId, version)` |
| `event-registrations` | `registrationId`; GSIs `EventIndex`, `EmailIndex`, `CheckInTokenIndex` | `answers` map + form version snapshot; `source` native/legacy |
| `event-counters` | `counterKey` | atomic capacity counters (`EVENT#id`, `TICKET#id#type`) via conditional updates |
| `audit-log` | `entityKey` + `sortKey` | admin changes to events/forms |

## Code
- `lambdas/event_model.py` — pure logic: validation, locked core fields (`ensure_core_fields` restores them on every save; `validate_form_schema` rejects any removal/weakening), form versioning (`new_form_version`), membership rules (`membership_status`, `meets_membership_requirement`: valid now **and** covers the renewal year), legacy mappers.
- `scripts/migrate_events_to_v2.py` — idempotent mirror with `--dry-run`, `--refresh`, `--rollback` (deletes only `source = "legacy"` items), and a verify step.
- Tests: `tests/test_event_model.py`.

## Deploy / migrate
1. `terraform apply` (creates the six new tables only; no existing resource changes).
2. `python3 scripts/migrate_events_to_v2.py --dry-run`, review warnings, then run without `--dry-run`.
3. Undo: `--rollback`, then remove the `.tf` file.

## Core fields (locked)
`fullName, email, phone` (profile-prefilled), `membershipId, membershipStatus, membershipType, membershipExpiry` (system, read-only), `consentPrivacy, consentTerms` (required). Admin may relabel/reorder, never delete. Account linkage is stored on the registration record, not as a form field.

---

# Phase 2 — Admin event management

## Using it
Admin dashboard → **Event platform** tab (or `/admin/event-platform`). Sign in with your normal admin account.
- **New event** → Basics (title, URL slug, short/rich description, category, tags), Schedule (JST start/end, registration window, capacity, waitlist), Location (venue, address, map link, online link shown only to registered attendees), Access (members only, guests allowed, renewal cover), Emails (confirmation/reminder templates with `{name} {event_title} {ticket_type} {registration_id} {event_date} {venue} {event_url}`).
- After the first save: **Design** (cover image, gallery, accent color, layout, page sections on/off and order), **Tickets & pricing** (member/non-member price in JPY, NPO-member early-bird price + cutoff, quantity limit, per-person cap), **History** (audit log).
- **Visibility**: Draft, Published, Unlisted (link only), Closed, Archived. Publishing needs a start time and at least one ticket type that is on sale. **Preview** shows an approximate page; the real public page arrives in Phase 4.
- **Duplicate** copies the event, its ticket types and its latest form as a draft. **Delete** only works for super admins and only while the event has no registrations; otherwise archive it.
- Events mirrored from the legacy Events tab are shown read-only; keep editing those in the original tab.

## Roles
Cognito groups, checked inside the new Lambda (the legacy `/admin/*` routes are unchanged and still accept any pool user):
| Group | Role | Can |
|---|---|---|
| `Admins` | super admin | everything here, including delete |
| `EventManagers` | event manager | create/edit/duplicate/archive events, tickets, images |
| neither | — | 403 on every event-platform route |

Add an event manager: `aws cognito-idp admin-add-user-to-group --user-pool-id <pool> --username <email> --group-name EventManagers`.

## API (`/admin/event-platform/…`, one Lambda `manage_event_platform`)
`GET me` · `GET|POST events` · `GET|PUT|DELETE events/{id}` · `POST events/{id}/status|duplicate|images` · `GET|POST events/{id}/tickets` · `PUT|DELETE events/{id}/tickets/{tid}` · `GET events/{id}/audit`.
- Updates must send the current `revision`; a stale one gets 409 (no lost updates).
- Slug uniqueness is enforced with a transactional `SLUG#<slug>` lock item in the events table.
- Server-owned fields (status, source, formId, ids, timestamps) cannot be set through update.
- Descriptions are sanitised server-side with an allow-list; images are type-, magic-byte- and size-checked and stored under `media/events/` (CloudFront `/media/*`).
- Every create/update/status/ticket/image/delete writes an `audit-log` entry (actor, summary, changed fields).

## Infra notes
- New: `backend/event_platform_admin.tf` (Lambda, proxy route, `EventManagers` group), IAM for the six tables.
- `assets.tf`: the S3 lifecycle rule that expired everything under `media/` after 90 days now targets `media/carousel/` and `media/seat-upgrade/` (the two prefixes that used it), so event images are kept.
- UI strings live in `frontend/src/locales/en.json` via `t()` (`frontend/src/i18n.js`); add `ja.json`/`ml.json` and register them to translate.

---

# Phase 3 — Registration form builder

## Using it
Event → **Registration form** tab. **Build** mode: add fields from the palette (click, or drag into the form), reorder by drag or with the ↑/↓ buttons (keyboard friendly), duplicate or remove custom fields, and edit the selected field on the right. **Try it** mode renders the form like a registrant sees it (fields appear and disappear live) and **Check with server** runs the real validator against the saved version.

- **Field types:** short/long text, email, phone, number, dropdown, single choice, checkbox, multiple choice, date, file upload, consent, section heading, info text, repeatable group.
- **Per field:** label, help text, placeholder, required, default value, options, validation (length, pattern, min/max, date range, selection count, file types and size), and **conditional logic** ("show only when Attending dinner is Yes", all/any of several rules).
- **Repeatable group:** a set of fields repeated up to N times (attendees, family members). Groups cannot nest. Conditions inside a group may use earlier top-level fields and earlier fields in the same group.
- **Locked core fields** (🔒): full name, email, phone (prefilled from the member profile); membership ID/status/type/expiry (system, read-only); privacy and terms consent (always required). They can be relabelled and moved, never removed or weakened. This is enforced by the backend on every save (`ensure_core_fields`), and by `validate_form_schema`.
- **Versions:** every save creates an immutable new version; the Version history list shows older ones read-only, and "Edit this version" saves it as a new version. Once an event has registrations, a field key cannot change type (add a new field instead) so exports stay consistent.
- **Templates / reuse:** save the current form as a template, start from a template, or copy another event's form. Applying replaces the form with a new version. Deleting a template is super-admin only.

## Server-side validation (`lambdas/form_engine.py`)
`validate_submission(schema, answers, context)` is what Phase 4 will call for every registration:
- evaluates conditions in schema order; hidden fields are dropped and never required (and fields depending on hidden fields are hidden too);
- drops unknown keys; checks types, options, lengths (hard caps 1000/5000 even with no rule), patterns, numbers, dates, selection counts, group size and per-entry rules, file references (type, size, name sanitised);
- fills system fields (membership info) and the account email from the server-side `context`, ignoring anything the client sent;
- rejects oversized submissions. Patterns with nested repetition like `(a+)+` are refused when the form is saved (ReDoS).
Errors are keyed by field path (`diet`, `guests[1].name`). Store registrations from the returned clean answers only, together with `(formId, version)`.
File answers are references `{fileId, name, size, contentType}`; the upload endpoint that issues `fileId` arrives with the public flow.

The browser applies the same condition rules (`frontend/src/formLogic.js`); `tests/fixtures/form_conditions.json` is run against both implementations so they cannot drift apart.

## API additions (`/admin/event-platform/…`)
`GET|PUT events/{id}/form` (PUT needs `baseVersion`; stale → 409) · `GET events/{id}/form/versions/{v}` · `POST events/{id}/form/validate` · `POST events/{id}/form/apply` (`templateId` or `copyFromEventId`) · `GET|POST form-templates` · `GET|DELETE form-templates/{id}`.

## Reusable pieces for Phase 4
`DynamicForm.jsx` renders any schema (used by "Try it", ready for the public form); `formLogic.js` for visibility.

---

# Phase 4 — Public pages and registration

## What visitors get
- **`/events`**: listing with Upcoming / Past tabs and a category filter. Unlisted events never appear; closed ones show "Registration is closed". Events mirrored from the old system link to their existing registration page.
- **`/events/{slug}`**: rendered from the admin's design (layout classic / hero / minimal, accent colour, cover, gallery, enabled sections in the admin's order, with their text). Shows ticket prices (the visitor's own price highlighted), early-bird note, "only N left" / sold out, and the registration box: not open yet, closed, sign-in needed, members-only (with a **Join or renew membership** button), or Register / Join the waiting list. Online links are never in the public page, only shown to confirmed registrants.
- **`/events/{slug}/register`**: sign in or continue as guest → tickets → your details (the admin's form, conditional fields live) → review & choose payment → submit. Guests are allowed unless the event says otherwise. Members get their name/email prefilled, email locked to the account, membership info shown read-only.
- **`/events/{slug}/registered?reg=…&token=…`**: confirmation page (also the link in the email, and where Square returns). Confirmed: registration ID + QR code + details. Awaiting payment: amount, hold deadline, bank/PayPay instructions or card link, and a way to switch method. Waiting list and cancelled states. The token in the link is the registrant's proof of ownership, so guests can come back to it too.
- **`/my/events`** (signed-in members; also in the account menu): their event registrations with QR, payment details, **edit** (answers only) and **cancel** until registration closes.

## Business rules (as agreed)
- **Prices** are computed on the server from ticket quantities; the browser never sends amounts. Members (flag active **and** a membership period covering today, same as the legacy flow) pay the member price; NPO members also get the early-bird price until its cutoff. Guests and lapsed members pay the non-member price.
- **Members-only** events need a valid membership that also covers the renewal year (per-event switch). Others are told to renew, with the link.
- **Payment**: free events confirm immediately. Paid events hold the seats as *awaiting payment*: card (Square checkout, confirmed by webhook, 30-minute hold) or bank transfer / PayPay (instructions from the existing Payment Methods settings, 7-day hold, an admin marks paid). Unpaid holds are released automatically every 5 minutes; a payment that arrives after release re-takes the seats if still free, otherwise the registration is flagged **refund pending**.
- **Capacity & waiting list**: event and per-ticket limits are enforced by one conditional DynamoDB transaction per booking or release (no read-then-write), so concurrent sign-ups cannot overbook, and replays of the same submit are idempotent. When the event is full and the waiting list is on, new registrations are waitlisted (no seats, no charge). **Promotion is manual** (admin action; the operation exists, its button arrives in Phase 5).
- **Edit / cancel** by members until the registration close time (or the event's end when no close time is set). Editing changes answers only, validated against the form version the person registered with. Cancelling releases the seats; if already paid it becomes **refund pending** for an admin to refund (same as today). Guests contact the organisers.
- **Emails** (SES): confirmation (uses the event's template and placeholders, plus QR), awaiting-payment (with instructions), waiting list, promotion, cancellation, hold expired.
- **Membership during registration**: signed-in members register at member prices; non-members are offered a **Join or renew** link to the existing membership page. Selling a membership inside the same checkout was not built (it would mean combining two payment flows).

## Security
Public endpoints are rate-limited (per IP and per e-mail, stored as hashes, fixed windows with TTL; the limiter fails open if DynamoDB is unavailable). A present-but-expired login token is a 401, never a silent guest registration. Uploads: type allow-list, magic-byte check, size cap (4 MB), stored in a private prefix (`event-uploads/`, never served by CloudFront), and registrations re-read the stored file's real metadata instead of trusting the browser. The registrant link token is compared in constant time. Registrations store a consent timestamp and no IP addresses (APPI-minimal).

## Deploying
1. `terraform apply`: three Lambdas (`event_platform_public`, `event_platform_webhook`, `event_platform_expiry`), `/public/{proxy+}` and `/webhooks/event-registrations/{provider}` routes, an EventBridge rule (every 5 minutes), two new GSIs on `event-registrations`, TTL on `event-counters`. If you add the GSIs to an already-deployed table, Terraform creates them one at a time.
2. In the Square dashboard add a **new webhook subscription** (events `payment.created`, `payment.updated`) pointing at `https://<api>/<stage>/webhooks/event-registrations/square`; put its signature key and the exact URL in `square_events_webhook_signature_key` / `square_events_webhook_url`.
3. Card payments reuse `square_access_token`, `square_location_id`, `square_env`; bank/PayPay use the existing Payment Methods admin settings.

## Known limits
- Uploaded files that never end up in a registration stay in S3 (add a lifecycle rule on `event-uploads/` if you want them cleaned up; registrations reference them, so do not expire attached ones).
- Reminder emails and bulk email are Phase 5; the template field exists.
- Promotion and "mark paid" have no admin buttons yet (Phase 5); the code paths are tested.

---

# Phase 5 — Registration management (admin)

Event → **Registrations** tab (both `Admins` and `EventManagers`). Native events only; legacy events keep using the old Registrations tab.

## What it does
- **Dashboard:** registrations by status, seats used vs capacity, revenue (paid / awaiting payment / refunds pending and done), ticket-type breakdown, seats checked in. Chips jump to what needs attention (refunds to process, unpaid, waiting list).
- **Table:** search across name, email, ID, tickets, notes and *every answer*; filters for status, payment, ticket type, checked-in and **any form field** (equals); sort by any column including form fields; choose extra form-field columns; server-side pagination (10-200 per page). On phones rows become cards.
- **Detail panel:** all answers (labelled from the form version the person registered with), payment and refund details, history. Actions: edit answers/note, **mark paid** (bank, PayPay, cash, card, other, with a reference), **cancel** (optional email; paid cancellations become *refund pending*), **promote** from the waiting list, **record refund**, check in / undo.
- **Refunds:** "refund pending" is tracked until an admin records it: manually (bank/cash/PayPay, with a note), or, for card payments, through Square (full amount). The registration is *claimed* (`refunding`) before Square is called, so a double click or two admins can never refund the same payment twice; if Square rejects it the claim is released.
- **Add registration:** for phone/paper sign-ups and corrections. Tickets, answers and capacity are validated like public ones; the registration window and members-only rules are skipped. Options: link to a member by email (member price and account), already paid (how) or pay later, override the total (a note is required), respect capacity / ignore capacity / put on the waiting list, and email or not. The admin confirms the registrant agreed to the privacy policy and terms (stored with who recorded it).
- **Export:** CSV (UTF-8 with BOM, opens correctly in Excel; cells starting with `= + - @` are neutralised) and Excel (.xlsx written without third-party libraries; all cells are text, never formulas). Exports follow the current filters, include per-ticket-type quantity columns and one column per form field (across form versions).
- **Check-in** (`/admin/check-in?event=…`, linked from the tab; mobile first): scan the QR from the confirmation email with the phone camera (uses the browser's built-in detector, with a bundled decoder for iPhone/Safari), or type the registration ID, or search by name/email. Results are colour-coded: checked in, already checked in (with time), payment due, cancelled, waiting list, wrong event, not found; undo is one tap. Two scanners racing cannot both check the same person in. The counter shows seats checked in / confirmed.
- **Email registrants:** to everyone not cancelled, confirmed, awaiting payment, waiting list, not yet / already checked in, or cancelled; one message per address, personalised with the usual placeholders, sent in batches of 100 from the browser (so each request stays short), with a "send a test to me" button and the option to use the event's saved **reminder** template.

## API (`/admin/event-platform/events/{id}/…`)
`GET dashboard` · `GET|POST registrations` · `GET|PUT registrations/{rid}` · `POST registrations/{rid}/{cancel|promote|mark-paid|refund|check-in}` · `POST check-in` (QR payload or typed ID) · `GET export?format=csv|xlsx&<filters>` (file returned as base64 JSON) · `POST email`.
Every action is written to the audit log (`REG#<id>` history shown in the detail panel; exports, bulk emails and manual adds also appear on the event's history).

## Notes and limits
- Admin edits change answers and the note only. Tickets and totals are fixed; to change them cancel and add a new registration.
- Scanning uses the registrant's QR (`NKEV1.<id>.<token>`); a wrong token looks exactly like an unknown registration.
- A refund stuck in `refunding` (a crash between claiming and finishing) is shown with a warning and must be checked in Square before retrying; the app never retries it automatically.
- Bulk email is not a mailing-list tool: it targets registrants of one event, caps nothing but SES's own limits, and does not include an unsubscribe link (these are event communications).
- New dependency: `jsqr` (lazy-loaded only on the check-in page, as the iPhone fallback decoder).


---

# Phase 6 — Hardening, tests, docs

Admin how-to: `docs/EVENT_ADMIN_GUIDE.md`.

## Security review (summary)
- **Input:** server-side validation for every field; control characters rejected everywhere, CR/LF rejected in single-line fields; email must be one plain address (no header/recipient injection); rich text through an allow-list sanitiser; CSV/Excel exports neutralise formula injection.
- **Payments:** the amount the provider reports (webhook and return-page reconcile) must equal the server-calculated total; a mismatch is flagged (`paymentMismatch`) and the registration stays unpaid. Refund is claim-then-call (no double refund).
- **Abuse:** per-IP and per-email rate limits on register/quote/lookup; at most 3 unpaid holds per email per event (seat hoarding).
- **Capacity:** one conditional DynamoDB transaction per booking/release; idempotency keys; deterministic registration ids.
- **Uploads:** type/size/magic-byte checks; stored privately; new uploads land in `event-uploads-pending/` (auto-expires after 2 days) and move to `event-uploads/` only when attached to a registration; admin download via 5-minute presigned URL, attachment disposition, audit-logged.
- **Access:** role checks inside the new Lambdas (Admins / EventManagers); member routes re-verify the HMAC token; other members get 404.
- **APPI:** “Erase personal data” (super admin) anonymises a registration and deletes its files, keeping only accounting facts.
- **Legacy code:** no legacy Lambda or plugin file is modified by this initiative.

## Tests and CI
- `scripts/run_tests.sh` runs each test file in its own process (the combined `discover` run still has the pre-existing cross-module collisions). `.github/workflows/tests.yml` runs it plus `npm ci && npm run build`.
- `test_event_moto_e2e` runs the real stores and handlers against a mocked DynamoDB with the real table layout (needs `pip install "moto[dynamodb]"`; skipped without it). It catches what the in-memory fakes cannot (number types, serialisation, IAM-independent API misuse).
- New: `test_event_hardening`, `test_event_critical_paths` (login-token compatibility with the real `auth_otp`, price → register → pay → check-in → refund, capacity, idempotency, expiry-vs-payment race), `test_event_packaging` (every Lambda archive contains all modules it imports), `test_event_performance` (5,000 registrations).

## Monitoring
`backend/cloudwatch.tf` adds error alarms for the four event Lambdas and a “hold-expiry job stalled” alarm. **No notification target is attached yet**: add an SNS topic/email subscription to `alarm_actions` to be alerted.

## Known limits
- The registrant link `…/registration?token=…` is a bearer secret; it is rate-limited but anyone holding it can view that one registration.
- Dashboard/list/export read all registrations of one event through an index (fine to thousands; add pagination server-side beyond ~20k).
- Terraform was parsed but not `plan`ned or applied, and nothing was run against real AWS, Square or SES; do a preview-environment deploy and a Square sandbox payment before production.
