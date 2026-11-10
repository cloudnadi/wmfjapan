# WMF Japan events site (wmfevents.cloudnadi.com)

Event registration site for WMF Japan: <https://wmfevents.cloudnadi.com/>. The organisation's own site is <https://www.wmfjapan.org/> (linked from the header, footer and structured data).

The site is the front end of the **event platform** that lives in this repository (`lambdas/event_platform_public`, `lambdas/manage_event_platform`, the `events-v2` tables; see `EVENT_PLATFORM.md`). Events, tickets, forms, prices, availability and per-event add-ons all come from the platform's API, and the organiser console at `/admin` is the same platform. Nothing in the UI or the backend is specific to one event, and there is no membership system: one price per ticket, open to everyone.

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
| Organiser console | `/admin` (`EventManager.jsx`) — separate Cognito pool, groups `Admins` / `EventManagers`; `/admin/check-in` for door scanning |
| Add-ons (slots, upgrades, seats) | `lambdas/event_addons.py`, `frontend/src/AddonsStep.jsx`, `EventAddons.jsx` |

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
npm test                        # Vitest (jsdom)
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
| `VITE_CONTACT_EMAIL` | contact address; confirm with WMF Japan (empty hides contact links) |
| `VITE_LOGO_URL` | official logo for the header (empty = text wordmark) |
| `VITE_AUTH_REGION`, `VITE_AUTH_USER_POOL_ID`, `VITE_AUTH_CLIENT_ID` | attendee pool + app client |
| `VITE_AUTH_COGNITO_DOMAIN` | Hosted UI domain, no scheme (needed for social sign-in) |
| `VITE_AUTH_SOCIAL_PROVIDERS` | comma list of Cognito provider names to offer, e.g. `Google,Facebook` (empty = e-mail only) |
| `VITE_AUTH_SCOPES` | default `openid,email,profile` |
| `VITE_COGNITO_*` | organiser (admin) pool used by `/admin` |

Event-level behaviour is **never** controlled by environment flags; see "Per-event features".

## Production build and deployment (wmfevents.cloudnadi.com)

Deployment is the existing GitHub Actions flow (`deploy-preview.yml`, `deploy-production.yml`, manual dispatch): Terraform apply, then `npm ci && npm run build` with the Terraform outputs injected as `VITE_*`, then `scripts/deploy_frontend.sh` (S3 sync + CloudFront invalidation).

Checklist for the new hostname:

1. In the production tfvars (supplied through the `TFVARS_PRODUCTION` secret) set `frontend_domain = "wmfevents.cloudnadi.com"`, `enable_domain_management = true`, and the Route 53 zone for `cloudnadi.com`. Terraform then issues the ACM certificate and DNS records (`domain.tf`). Preview: `preview-wmfevents.cloudnadi.com`.
2. Set `attendee_cognito_domain_prefix` to a globally unique prefix.
3. Optionally configure social providers (below).
4. Run the production deploy workflow. The sign-in callback and sign-out URLs for the domain are registered on the app client automatically.
5. Smoke test: create an account with a real mailbox, verify, sign in, register for a free test event, sign out.

New Terraform inputs/outputs: variables `attendee_cognito_domain_prefix`, `attendee_extra_origins`, `attendee_social_providers`, `attendee_social_provider_secrets` (sensitive); outputs `attendee_user_pool_id`, `attendee_app_client_id`, `attendee_hosted_ui_domain`, `attendee_social_providers`. Terraform `>= 1.3` is required (optional object attributes). The Terraform is slimmed to the platform only (API Gateway, four Lambdas + the expiry schedule, event tables and `site-config`, two Cognito pools, assets, CloudFront, SES, alarms). It was `terraform validate`d but **not** planned or applied (no AWS access in the authoring environment): run `terraform plan` on the preview stack first. Because the previous stack's resources are no longer declared, a plan against an existing deployment will propose destroying them (old Lambdas, tables, routes); review that list and export any data you want to keep before applying.

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
* **Server-side**: the public Lambda verifies the ID token itself (`cognito_jwt.py`: RS256 signature against the pool's JWKS, issuer, audience = our client, `token_use = id`, expiry, not-before) and requires `email_verified`. A token that is present but invalid is a 401, never silently treated as a guest. Authorization decisions (guest allowed, ownership of a registration) are made on the server from that verified identity; the UI only mirrors them. The JWT verifier is stdlib-only because the Lambdas ship with boto3 only.

### Token storage (client-only SPA) and its risks

There is no server component that could set an HTTP-only cookie, so:

* ID and access tokens are held **in memory only** and disappear on reload.
* The **refresh token** is in `localStorage` so sessions survive reloads and browser restarts. Script injected into the site's origin could read it. Mitigations: no `innerHTML` on user data (admin-authored rich text is allow-list sanitised server-side), no third-party scripts, revocable refresh token, sign-out revokes it. **Recommended follow-up:** add a strict Content-Security-Policy response header on CloudFront (not part of this change).
* Draft checkout state (ticket counts + typed answers) is kept in `sessionStorage` only while the visitor leaves for a social provider, for at most 30 minutes, and is deleted when restored or submitted. It contains personal details typed by the visitor, never tokens or passwords.
* Tokens, authorization codes and passwords are never logged; a test enforces that auth code contains no `console.*`.
* CSRF: the API authenticates with a bearer header (not cookies), so cross-site requests carry no credentials; OAuth CSRF is covered by `state`.

## Guest checkout

* Anyone can register without an account when the event allows it (`capabilities.guestCheckout.enabled`, derived from the event's Access settings: default allowed, off when "Guests allowed" is cleared).
* The checkout opens with an explicit choice: *Sign in or create an account* or *Continue as a guest* ("no account is created for guests"). No Cognito user is ever created implicitly.
* A guest record stores only the registration (name, e-mail, phone and the answers the event's form asks for, consent time). No account identifier. Registrations made while signed in additionally store `accountEmail` and `accountSub` (the Cognito subject).
* Signing in during checkout does not lose the cart: with e-mail/password the page state is untouched and the visitor resumes on the same step; with social sign-in the draft is saved before leaving and restored on return. A session that expires during submit sends the visitor back to sign-in and resumes on the review step.
* Guests are rate-limited per IP and per e-mail; payment is unchanged (card via Square, or bank transfer / PayPay confirmed by an admin; this app never sees card data, and a paid registration is never marked paid by the browser).
* Confirmation page wording is generic for free and paid events.

## Per-event features (capabilities)

Every public event (`GET /public/events`, `GET /public/events/{slug}`) carries:

```json
"capabilities": {
  "guestCheckout":   { "enabled": true },
  "authentication":  { "required": false },
  "seatReservation": { "enabled": false },
  "zoneUpgrade":     { "enabled": false },
  "entrySlots":      { "enabled": false }
}
```

Single sources of truth (nothing is duplicated in the frontend):

| Capability | Source |
|---|---|
| `guestCheckout`, `authentication` | derived from the event's `access.guestsAllowed` (admin → event → Access, default allowed). `authentication.required` is always `not guestCheckout.enabled`. |
| `entrySlots`, `zoneUpgrade`, `seatReservation` | `capabilities.<name>.enabled` on the event (admin → event → **Add-ons**), plus the event's `slots`, `upgrades`, `seatMap` configuration. Missing or anything other than `true` means off. |

**Enforcement is server-side**, UI hiding is only cosmetic:

* `POST /public/events/{slug}/register`: an `addons` payload naming a feature the event does not offer is rejected with `400 {"code": "feature_disabled", "fields": {"addons.<name>": …}}` and nothing is stored; guests get `403 login_required` when guest checkout is off.
* A full slot/upgrade, or a seat someone else holds, is rejected with `409 addon_unavailable`; the whole registration is refused (add-on conflicts are never waitlisted).
* Capacity, booking windows, add-on availability and pricing are decided at write time by conditional DynamoDB transactions; the browser never sends prices and refreshes the event after a conflict, so stale pages cannot bypass them.

### Enabling features for an event

* Guest checkout: admin → **Events** → event → **Access** → untick "Guests allowed".
* Add-ons: event → **Add-ons** → switch on entry slots / zone upgrades / seat reservation and configure them (time slots with capacities; named upgrades with a price and optional limit; seat-map sections with rows × seats and a per-seat extra price). Details and the registration contract are in `EVENT_PLATFORM.md`.

### Rollback

Redeploy the previous Lambda zips and frontend build. The attendee Cognito pool has `deletion_protection = ACTIVE`; to remove it, disable protection first. Destroying it deletes the attendee accounts (not recoverable from this repository), so export users first if needed.

## What was removed

This repository started as a copy of another organisation's registration site. All of that is gone: the hard-coded Onam registration flow, the original events/registrations tables and Lambdas, NPO membership, AGM registration, the one-time-code member login, sponsor/partner pages, the celebrity-dinner module, the seat-selection and seat-upgrade plugins, the legacy admin dashboard, the member/CSV import scripts and the static venue/parking pages. An Onam-style event is just an event created in the console.

## Items that need WMF Japan input (placeholders)

* Contact e-mail: set `VITE_CONTACT_EMAIL` (the organisation site is `wmfjapan.org`).
* Terms, Privacy and Cancellation pages are inherited text with links/contact taken from configuration, and **may still contain wording about membership or the previous organisation. Have the legal text reviewed and rewritten** before launch.
* **Logo:** the previous organisation's artwork was removed. `logo.png` / `logo-sm.png` / `favicon.ico` are plain placeholders and the header shows a text wordmark. Supply the official WMF Japan files (replace those files and/or set `VITE_LOGO_URL`). No addresses or legal wording were invented.
* Japanese: the Japanese catalog (`frontend/src/locales/ja.json`) is registered but empty, so every string falls back to English; add translations key by key (reviewed by a native speaker). There is no language switcher yet.

## External actions still required

1. Rotate the credentials that were committed (see "Security findings").
2. Apply Terraform to preview, then production; set the unique Hosted UI domain prefix.
3. (Optional) Create social-provider OAuth clients and supply their secrets.
4. SES/Cognito e-mail: attendee verification mails use Cognito's default sender (limited daily volume, generic branding). For production volume configure SES for the pool.
5. Create the first admin user (`create_admin_user_command` Terraform output) and add them to `Admins` or `EventManagers`; then create events in the console. Set the card/bank/PayPay details under **Payment methods** (super admin).
6. Add a CloudFront response-headers policy with CSP, HSTS, `frame-ancestors`.
7. Add an SNS target to the CloudWatch alarms.

## Security findings (pre-existing)

* `backend/terraform.production.tfvars` and `terraform.preview.tfvars` were **tracked in git with live-looking payment credentials** (PayPay API key/secret/merchant id, Square access token and webhook signature keys) and the Route 53 zone id; the `backend.*.hcl` files held the state bucket name. They are now untracked, git-ignored and replaced by sanitised `*.example` files, but **they remain in git history and on the remote**. Treat those values as compromised: rotate them at PayPay/Square, update the `TFVARS_*` CI secrets, and consider a history rewrite. (CI already writes tfvars from secrets, so deployments are unaffected.)
* `allowed_origins = "*"` (API CORS) and `Access-Control-Allow-Origin: *` in the Lambdas: acceptable for a bearer-token API but should be narrowed to the site origins.
* The API usage plan allows 50 rps (burst 100); the per-IP/per-e-mail limits in the public Lambda fail open if DynamoDB is unavailable.
