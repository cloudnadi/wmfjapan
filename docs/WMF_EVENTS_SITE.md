# WMF Japan events site (wmfevents.cloudnadi.com)

Event registration site for WMF Japan: <https://wmfevents.cloudnadi.com/>. The organisation's own site is <https://www.wmfjapan.org/> (linked from the header, footer and structured data).

The site is a generic front end for the **event platform** that lives in this repository (`lambdas/event_platform_public`, `lambdas/manage_event_platform`, the `events-v2` tables; see `EVENT_PLATFORM.md`). Events, tickets, forms, prices, availability and per-event features all come from the platform's API. Nothing in the UI is specific to one event.

## Architecture in one page

```
Browser (React + Vite SPA, S3 + CloudFront)
  ├─ /public/* ───────────────► event-platform public Lambda  ──► DynamoDB (events-v2, tickets, forms, registrations, counters)
  │      Authorization: Cognito ID token (attendee pool) — verified server-side, signature included
  ├─ Cognito Identity Provider API (sign up / verify / sign in / reset)   [public client, no secret]
  └─ Cognito Hosted UI (/oauth2/authorize, /oauth2/token)  [social sign-in, authorization code + PKCE]
```

| Concern | Where |
|---|---|
| Branding / org settings | `frontend/src/siteConfig.js` (all values overridable with `VITE_*`) |
| Platform API client | `frontend/src/eventsApi.js` (token handling, 401 refresh-and-retry) |
| Event data model, defaults, derived state | `frontend/src/eventModel.js` (JSDoc-typed) |
| Attendee auth | `frontend/src/auth/*` (`config`, `redirect`, `pkce`, `social`, `session`, `cognitoApi`, `AuthPanel`, `AuthCallback`) |
| Pages | `HomePage`, `EventsList`, `EventPage`, `EventRegister`, `EventRegistered`, `MyEventRegistrations`, `SignInPage` |
| Server identity + capability checks | `lambdas/cognito_jwt.py`, `lambdas/event_capabilities.py`, `lambdas/event_platform_public/handler.py` |
| Attendee user pool | `backend/cognito_attendees.tf` |
| Admin (unchanged) | `/admin`, `/admin/event-platform` — separate Cognito pool, groups `Admins` / `EventManagers` |

The pre-existing member area (NPO membership, AGM, one-time-code member login, "Registrations" for the original pipeline) is unchanged and still reachable; the public API accepts either an attendee ID token or the older member token.

## Look and feel

The site follows https://www.wmfjapan.org/: a single **light** theme (there is no dark mode or theme switch), white pages, a pale-mint (`#dbe8e6`) gradient header with bold grey navigation, plum (`#572f47` → near-black) hero bands, a green footer band, and a Proxima-Nova-style sans-serif (falls back to Helvetica/Arial; no web font is downloaded). Design tokens live at the top of `frontend/src/styles.css`; page layouts in `publicEvents.css`; header/footer/sign-in in `site.css`.

Event-management layout: home hero with live counts and search/category filter, event cards with a calendar-style date tab and status badges, event pages with a plum header band and a sticky registration panel, and a three-step checkout (progress indicator + sticky order summary).

Accessibility note: the organisation's brand green (`#009757`) is only 3.8:1 against white, so buttons, links and the footer use the next shade down (`#007a46`, 5.4:1); the brand shade is kept for large decoration only. The organisation's logo is not bundled (see placeholders below).

## Local development

```bash
cd frontend
npm ci
cp .env.example .env.local      # fill in the placeholders (see below)
npm run dev                     # http://localhost:5173
npm test                        # Vitest (jsdom): 170+ tests
npm run build                   # production bundle in frontend/dist
```

```bash
pip install boto3 openpyxl "moto[dynamodb]"
scripts/run_tests.sh            # every backend test file in its own process
```

For sign-in to work locally you need a deployed attendee pool (preview stack) whose app client lists `http://localhost:5173/auth/callback` and `http://localhost:5173/` (set `attendee_extra_origins = ["http://localhost:5173"]` in the **preview** tfvars only). Without `VITE_AUTH_CLIENT_ID` the sign-in panel says sign-in is unavailable and guest checkout still works.

There is no formatter or linter configured in this repository, so none was added. `npm run build` and `npm test` are the frontend checks; `.github/workflows/tests.yml` runs both plus the backend suite.

## Configuration

All frontend values are public build-time variables (`frontend/.env.example`, `env.production.example`, `env.preview.example`). Never put a secret in a `VITE_*` variable.

| Variable | Purpose |
|---|---|
| `VITE_API_BASE_URL` | API Gateway stage URL (serves `/public/*`) |
| `VITE_SITE_URL` | canonical origin (default `https://wmfevents.cloudnadi.com`) |
| `VITE_SITE_NAME`, `VITE_SITE_TITLE`, `VITE_SITE_DESCRIPTION`, `VITE_ORG_NAME` | branding text |
| `VITE_ORG_URL` | organisation site (default `https://www.wmfjapan.org/`) |
| `VITE_CONTACT_EMAIL`, `VITE_MEMBER_REGISTER_URL` | **placeholders** carried over from the previous site; confirm with WMF Japan (empty contact email hides contact links) |
| `VITE_AUTH_REGION`, `VITE_AUTH_USER_POOL_ID`, `VITE_AUTH_CLIENT_ID` | attendee pool + app client |
| `VITE_AUTH_COGNITO_DOMAIN` | Hosted UI domain, no scheme (needed for social sign-in) |
| `VITE_AUTH_SOCIAL_PROVIDERS` | comma list of Cognito provider names to offer, e.g. `Google,Facebook` (empty = e-mail only) |
| `VITE_AUTH_SCOPES` | default `openid,email,profile` |
| `VITE_COGNITO_*` | admin dashboard pool (not used by attendees) |
| `VITE_API_MOCK` | dev-server only, `true` fakes the member login; ignored in production builds |

Event-level behaviour is **never** controlled by environment flags; see "Per-event features".

## Production build and deployment (wmfevents.cloudnadi.com)

Deployment is the existing GitHub Actions flow (`deploy-preview.yml`, `deploy-production.yml`, manual dispatch): Terraform apply, then `npm ci && npm run build` with the Terraform outputs injected as `VITE_*`, then `scripts/deploy_frontend.sh` (S3 sync + CloudFront invalidation).

Checklist for the new hostname:

1. In the production tfvars (supplied through the `TFVARS_PRODUCTION` secret) set `frontend_domain = "wmfevents.cloudnadi.com"`, `enable_domain_management = true`, and the Route 53 zone for `cloudnadi.com`. Terraform then issues the ACM certificate and DNS records (`domain.tf`). Preview: `preview-wmfevents.cloudnadi.com`.
2. Set `attendee_cognito_domain_prefix` to a globally unique prefix.
3. Optionally configure social providers (below).
4. Run the production deploy workflow. The sign-in callback and sign-out URLs for the domain are registered on the app client automatically.
5. Smoke test: create an account with a real mailbox, verify, sign in, register for a free test event, sign out.

New Terraform inputs/outputs: variables `attendee_cognito_domain_prefix`, `attendee_extra_origins`, `attendee_social_providers`, `attendee_social_provider_secrets` (sensitive); outputs `attendee_user_pool_id`, `attendee_app_client_id`, `attendee_hosted_ui_domain`, `attendee_social_providers`. Terraform `>= 1.3` is now required (optional object attributes). The Terraform in this change was written but **not** planned or applied (no Terraform binary or AWS access in the authoring environment): run `terraform validate` / `plan` on the preview stack first.

## Amazon Cognito: required configuration

`backend/cognito_attendees.tf` creates a **separate user pool** for attendees. Do not reuse the admin pool: the admin API Gateway authorizer accepts any user of its pool and the admin Lambdas treat them as administrators.

App client (public SPA client):

| Setting | Value |
|---|---|
| Client secret | **none** (`generate_secret = false`) |
| OAuth flow | authorization code only (`code`); PKCE is used by the browser; implicit flow off |
| Scopes | `openid`, `email`, `profile` |
| Auth flows | `ALLOW_USER_PASSWORD_AUTH`, `ALLOW_REFRESH_TOKEN_AUTH` |
| Callback URLs | production `https://wmfevents.cloudnadi.com/auth/callback`; preview `https://preview-wmfevents.cloudnadi.com/auth/callback`; local `http://localhost:5173/auth/callback` (preview/dev pool only) |
| Sign-out URLs | `https://wmfevents.cloudnadi.com/`, `https://preview-wmfevents.cloudnadi.com/`, `http://localhost:5173/` (dev only) |
| Token validity | ID/access 60 min, refresh 30 days; token revocation enabled |
| `prevent_user_existence_errors` | `ENABLED` |
| Pool | e-mail as username, e-mail verification by code, self sign-up on, password ≥ 10 chars with upper/lower/number, no MFA |

Callback and sign-out URLs are derived by Terraform from `frontend_domain` plus `attendee_extra_origins`; they must match exactly (scheme, host, port, path).

### Social identity providers

Nothing is enabled by default. To add one:

1. Create an OAuth client with the provider (Google Cloud console → OAuth consent screen + OAuth client "Web application"; Facebook for Developers → app with Facebook Login; Login with Amazon → security profile). As the authorized redirect URI register the **Cognito** endpoint `https://<hosted-ui-domain>/oauth2/idpresponse` (the `attendee_hosted_ui_domain` output).
2. Add to the tfvars:
   ```hcl
   attendee_social_providers = {
     Google = { provider_type = "Google", client_id = "…apps.googleusercontent.com" }
   }
   ```
   and supply the secret through a CI secret as `attendee_social_provider_secrets = { Google = "…" }`. The secret lives in Terraform state: keep the state bucket private.
3. Apply, then add the provider name to the frontend build via `attendee_social_providers` output → `VITE_AUTH_SOCIAL_PROVIDERS` (the workflows already do this). A "Continue with Google" button appears. Cognito requires the provider's e-mail to be verified for the site to accept the account (see below).
4. Sign in with Apple needs extra provider details (team id, key id, private key); it is not wired into the Terraform variable yet.

## Authentication behaviour

* **Email accounts** (in page, `AuthPanel`): create account → verification code by e-mail → signed in; sign in; forgot password → code → new password; resend code with cool-down; an unverified account that tries to sign in is moved to the verification step. Errors are translated from Cognito exception names to fixed messages (no raw Cognito text). Wrong password and unknown user give the same message; password-reset never says whether an address has an account. Sign-up necessarily reports "an account already exists" (Cognito does not allow hiding that), and says so with a pointer to sign-in/reset.
* **Social sign-in**: `Continue with <provider>` → Hosted UI `/oauth2/authorize` with a fresh PKCE challenge (S256), random `state` and `nonce` → `/auth/callback` → state checked (constant-time) → code exchanged at `/oauth2/token` with the verifier (no secret) → ID token `aud`, `iss`, `nonce`, `exp` checked → session. Cancelled sign-in (`access_denied`), provider errors, state mismatch, expired/replayed callbacks and a failed exchange each show a specific message and a way back. The pending record is single use and expires in 10 minutes; `?code=` is removed from the address bar immediately.
* **Redirects**: the post-sign-in destination is validated against an allow-list (`/events…`, `/my/events…`, `/signin`) at the moment it is stored **and** again when it is used; anything else (absolute URLs, `//host`, backslashes, control characters, `..`) becomes `/`.
* **Sessions**: ID token is refreshed a minute before expiry; a 401 from the API triggers one forced refresh and retry; a rejected refresh token ends the session with a "session expired" message; sign-out in one tab ends the others; sign-out calls `GlobalSignOut` (revokes the refresh token) and, after social sign-in, also ends the Hosted UI session.
* **Server-side**: the public Lambda verifies the ID token itself (`cognito_jwt.py`: RS256 signature against the pool's JWKS, issuer, audience = our client, `token_use = id`, expiry, not-before) and requires `email_verified`. A token that is present but invalid is a 401, never silently treated as a guest. Authorization decisions (guest allowed, members-only, ownership of a registration) are made on the server from that verified identity; the UI only mirrors them. The JWT verifier is stdlib-only because the Lambdas ship with boto3 only.

### Token storage (client-only SPA) and its risks

There is no server component that could set an HTTP-only cookie, so:

* ID and access tokens are held **in memory only** and disappear on reload.
* The **refresh token** is in `localStorage` so sessions survive reloads and browser restarts. Script injected into the site's origin could read it. Mitigations: no `innerHTML` on user data (admin-authored rich text is allow-list sanitised server-side), no third-party scripts, revocable refresh token, sign-out revokes it. **Recommended follow-up:** add a strict Content-Security-Policy response header on CloudFront (not part of this change).
* Draft checkout state (ticket counts + typed answers) is kept in `sessionStorage` only while the visitor leaves for a social provider, for at most 30 minutes, and is deleted when restored or submitted. It contains personal details typed by the visitor, never tokens or passwords.
* Tokens, authorization codes and passwords are never logged; a test enforces that auth code contains no `console.*`.
* CSRF: the API authenticates with a bearer header (not cookies), so cross-site requests carry no credentials; OAuth CSRF is covered by `state`.

## Guest checkout

* Anyone can register without an account when the event allows it (`capabilities.guestCheckout.enabled`, derived from the event's Access settings: default allowed, off for members-only or when "Guests allowed" is cleared).
* The checkout opens with an explicit choice: *Sign in or create an account* or *Continue as a guest* ("no account is created for guests"). No Cognito user is ever created implicitly.
* A guest record stores only the registration (name, e-mail, phone and the answers the event's form asks for, consent time). No account identifier. Registrations made while signed in additionally store `memberEmail` and `accountSub` (the Cognito subject).
* Signing in during checkout does not lose the cart: with e-mail/password the page state is untouched and the visitor resumes on the same step; with social sign-in the draft is saved before leaving and restored on return. A session that expires during submit sends the visitor back to sign-in and resumes on the review step.
* Guests are rate-limited per IP and per e-mail; payment is unchanged (Square/PayPay/bank transfer handled by the platform; this app never sees card data, and a paid registration is never marked paid by the browser).
* Confirmation page wording is generic for free and paid events.

## Per-event features (capabilities)

Every public event (`GET /public/events`, `GET /public/events/{slug}`) carries:

```json
"capabilities": {
  "guestCheckout":   { "enabled": true },
  "authentication":  { "required": false },
  "seatReservation": { "enabled": false },
  "zoneUpgrade":     { "enabled": false },
  "entrySlots":      { "enabled": false },
  "celebrityDinner": { "enabled": false }
}
```

Single sources of truth (nothing is duplicated in the frontend):

| Capability | Source |
|---|---|
| `guestCheckout`, `authentication` | derived from the event's `access.guestsAllowed` / `access.membersOnly` (admin → event → Access). `authentication.required` is always `not guestCheckout.enabled`. |
| `seatReservation`, `zoneUpgrade`, `celebrityDinner` | events from the original pipeline: `eventInfo.seatSelection.enabled`, `eventInfo.seatUpgrade.enabled`, `eventInfo.celebrityDinner.enabled` in the legacy events table (the existing admin Events form). Mirrored into `capabilities` by `legacy_event_to_v2`. |
| `entrySlots` | `eventInfo.entrySlots.enabled` (new, optional). |

**Defaults for records that predate capabilities** (enforced by `event_capabilities.py` on the server and `normalizeCapabilities` in the browser): add-ons are off unless explicitly `true`; guests allowed unless access says otherwise; `entrySlots` on events from the original pipeline is **on** unless `eventInfo.entrySlots.enabled` is exactly `false` (entry slots could not be switched before, so existing events keep them).

**Enforcement is server-side**, UI hiding is only cosmetic:

* `POST /public/events/{slug}/register`: an `addons` payload naming a feature the event does not offer is rejected with `400 {"code": "feature_disabled", "fields": {"addons.<name>": …}}` and nothing is stored; guests are rejected with `403 login_required` when guest checkout is off; members-only rules are unchanged.
* Entry slots on the original pipeline: `GET /registrations/available-slots` returns no slots, `POST /events/{eventType}/register` with `slotId`/`allocations` returns `400 feature_disabled`, and the member slot-change endpoint returns `400 feature_disabled`, when the event has `entrySlots.enabled = false`.
* Seat selection, zone upgrade and the dinner were already refused server-side by their Lambdas when `eventInfo.*.enabled` is not `true`.
* Capacity, booking windows and pricing keep being decided at write time by the platform (conditional DynamoDB transactions); the browser never sends prices and refreshes the event after a sold-out response, so stale pages cannot bypass them.

### Enabling features for an event

* Guest checkout: admin dashboard → **Event platform** → event → **Access**: untick "Guests allowed" (or tick "Members only").
* Add-ons for events from the original pipeline: admin dashboard → **Events** → edit the event → switch on seat selection / zone upgrade / dinner, and set `entrySlots` in the event's info JSON (`"entrySlots": {"enabled": false}` turns slots off). Then refresh the mirror (see migration) so the public API reflects it.
* **Limitation (important):** the add-on fulfilment code (seat map, upgrades, entry slots, dinner) runs on the original registration pipeline. Registrations created through the platform's native flow cannot book them yet, so the admin API refuses to enable an add-on on a platform-native event (`capabilities.<name>.enabled: true` → `400 Invalid event details` with a `capabilities.<name>` field error) rather than showing a feature that does nothing, and the register endpoint rejects add-on payloads. Native add-on support is a platform task: build the booking step, then add the key to `NATIVE_ADDONS_SUPPORTED` in `event_capabilities.py` (a one-line change). The frontend already renders add-ons from `capabilities` for any event that reports them.

### Upgrade and migration notes

* No table or key changes. `capabilities` is an optional attribute on `events-v2` items. Records without it behave as documented above.
* After deploying, run `python3 scripts/migrate_events_to_v2.py --dry-run` and then `--refresh` so the mirrors of original-pipeline events carry `capabilities` (until then the public API falls back to the defaults, i.e. `entrySlots` on, other add-ons off).
* The public API response only gained fields (`capabilities`, `source`, `viewer.provider`); existing fields and routes are unchanged. Tokens from the one-time-code login are still accepted.
* Browser storage keys and CSS class prefixes were renamed from `nk-*` to `wmf-*`. Visitors and admins signed in on the old build are signed out once and the theme choice resets; no data is lost.
* **Rollback:** redeploy the previous Lambda zips and frontend build. Stored `capabilities` attributes and `accountSub` on registrations are ignored by the old code. The mirror refresh is idempotent (`--rollback` removes mirrors as before). The attendee Cognito pool has `deletion_protection = ACTIVE`; to remove it, disable protection first. Destroying it deletes the attendee accounts (they are not recoverable from this repository), so export users first if needed.

## What was removed

The hard-coded Onam registration page and route (`/onam/register`), the Onam/anniversary poster carousel, the community "thank you" landing page and its photos, the Sadhya / seat-selection / zone-upgrade guide pages (static copy with pinned 2026 deadlines), the "host your event" footers (a hosting-vendor promotion, not WMF content), event-specific cancellation-policy sections, the `VITE_EVENT_TYPE_ONAM` / `VITE_ONAM_MOCK` / `VITE_ONAM_IMAGE` variables and the mock-by-default behaviour of the member login. An Onam event created in the platform is just an event: it appears in the listing, has its own `/events/{slug}` page and registers through the generic checkout.

Still present, by design (legacy, not reachable from the public pages): the original registration/admin code paths and tables (`register`, `my_registrations`, `manage_registrations`, the Events/Registrations admin tabs with Onam-shaped pricing tables), `EVENT_TYPE_ONAM` / `ONAM_CAPACITY` Lambda settings, and the check-in format `NKEV1.NK-…` plus `NK-` registration ids. These are data formats and admin tooling for existing records; retire them in a separate change once the old registrations are no longer needed. `frontend/public/parking-guide.html` is a static page linked from old confirmation e-mails.

## Items that need WMF Japan input (placeholders)

* Contact e-mail (`info@wmfjapan.com`) and the membership sign-up URL are the values the repository already contained; confirm (the organisation site is `wmfjapan.org`). Set `VITE_CONTACT_EMAIL` / `VITE_MEMBER_REGISTER_URL`.
* Terms, Privacy and Cancellation pages are the existing text with links/contact taken from configuration. The event-specific cancellation sections were removed and replaced by a neutral "contact us" line. **Have the legal text reviewed.**
* **Logo:** the logo files that were in `frontend/public` were the previous organisation's artwork, so they were removed. `logo.png` / `logo-sm.png` / `favicon.ico` are now plain-text **placeholders** (a `WMF Japan` wordmark and a `W` icon; the wordmark is also baked into the check-in PDF generators) and the header shows a text wordmark. Supply the official WMF Japan files (replace those files, regenerate the PDF blob as described in `lambdas/checkin_pdf.py`, and/or set `VITE_LOGO_URL`). No addresses or legal wording were created.
* Japanese: the Japanese catalog (`frontend/src/locales/ja.json`) is registered but empty, so every string falls back to English; add translations key by key (reviewed by a native speaker). There is no language switcher yet.

## External actions still required

1. Rotate the credentials that were committed (see "Security findings").
2. Apply Terraform to preview, then production; set the unique Hosted UI domain prefix.
3. (Optional) Create social-provider OAuth clients and supply their secrets.
4. SES/Cognito e-mail: attendee verification mails use Cognito's default sender (limited daily volume, generic branding). For production volume configure SES for the pool.
5. Run the mirror refresh script; enable/disable features on the existing events as desired.
6. Add a CloudFront response-headers policy with CSP, HSTS, `frame-ancestors`.
7. Add an SNS target to the CloudWatch alarms (pre-existing TODO).

## Security findings (pre-existing)

* `backend/terraform.production.tfvars` and `terraform.preview.tfvars` were **tracked in git with live-looking payment credentials** (PayPay API key/secret/merchant id, Square access token and webhook signature keys) and the Route 53 zone id; the `backend.*.hcl` files held the state bucket name. They are now untracked, git-ignored and replaced by sanitised `*.example` files, but **they remain in git history and on the remote**. Treat those values as compromised: rotate them at PayPay/Square, update the `TFVARS_*` CI secrets, and consider a history rewrite. (CI already writes tfvars from secrets, so deployments are unaffected.)
* `allowed_origins = "*"` (API CORS) and `Access-Control-Allow-Origin: *` in the Lambdas: acceptable for a bearer-token API but should be narrowed to the site origins.
* The Lambdas' global API usage plan is 50 rps; the per-IP/per-e-mail limits in the public Lambda fail open if DynamoDB is unavailable.
