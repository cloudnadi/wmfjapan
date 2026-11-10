# CLAUDE.md — WMF Japan Events (wmfevents.cloudnadi.com)

Event registration and management site for WMF Japan (org site: https://www.wmfjapan.org/). One in-repo **event platform** is the whole product: the public site, the organiser console (`/admin`) and the APIs all run on it. Attendees sign in with their own Amazon Cognito user pool (email + social via Hosted UI/PKCE) or check out as guests. **Read `docs/WMF_EVENTS_SITE.md` first**, then `EVENT_PLATFORM.md` (data model, rules, add-ons) and `docs/EVENT_ADMIN_GUIDE.md`. Run tests with `scripts/run_tests.sh` (backend) and `cd frontend && npm test` (frontend).

Key decisions: nothing is event-specific (an event is just data); there is **no membership system** (one `priceJpy` per ticket, optional early bird for everyone); per-event features are `event.capabilities` (defaults in `lambdas/event_capabilities.py`, mirrored in `frontend/src/eventModel.js`, enforced server-side); add-ons (entry slots, zone upgrades, seat reservation) are native (`lambdas/event_addons.py`); attendees use a **separate** Cognito pool (`backend/cognito_attendees.tf`) — never put attendees in the admin pool; org-specific text/URLs live in `frontend/src/siteConfig.js`; strings go through `t()` (`en.json`, `ja.json` falls back to English); no secrets or deployment values are committed (`backend/*.tfvars` and `backend.*.hcl` are git-ignored). Admin: `EventManagers` group + `Admins` = super admin, enforced inside `manage_event_platform` via `lambdas/admin_roles.py`. Payment decisions: card (Square) + bank/PayPay manual; holds 30 min card / 7 days manual; waitlist promotion manual; registrants edit/cancel until registration close; paid cancellations become refund_pending; guests allowed by default (per-event toggle). Work happens on the branch named in the task (currently `develop`), never directly on `main`.

## Stack
| Layer | Tech |
|---|---|
| Frontend | React 18 + Vite 5, plain JS/JSX, one global `styles.css` (+ `publicEvents.css`, `site.css`, `eventPlatform.css`). **No react-router, no TypeScript, no i18n library.** Routing is a hand-rolled `currentPath` switch in `frontend/src/App.jsx`. Deps: `react`, `qrcode`, Cognito SDK, `jsqr`; dev: Vitest + Testing Library. |
| Backend | Python AWS Lambdas (`lambdas/<name>/handler.py`) behind one API Gateway REST API (Terraform), boto3 only. No framework. |
| DB | DynamoDB on-demand tables in `backend/dynamodb_event_platform.tf` (+ `site-config`), atomic counter items and `transact_write_items` for capacity. |
| Auth | Admin pool (`cognito.tf`; API Gateway Cognito authorizer on `/admin/event-platform/*`; groups checked in the Lambda). Attendee pool (`cognito_attendees.tf`; the public Lambda verifies ID tokens itself via `cognito_jwt.py`). |
| Payments | Square checkout + webhook (`lambdas/square.py`), bank transfer / PayPay manual with admin "mark paid". Payment-method settings are stored in `site-config` (`payment_methods_config`). |
| Email | SES (`event_mailer.py`); QR codes via the vendored `lambdas/qr_layer`. |
| Hosting/CI | S3 + CloudFront, Terraform in `backend/`, GitHub Actions deploy (preview/production, manual dispatch). Tests: `.github/workflows/tests.yml`. |

## Layout
- `lambdas/` — entry points `event_platform_public`, `event_platform_webhook`, `event_platform_expiry`, `manage_event_platform`; shared modules at the top level (`event_*.py`, `form_engine.py`, `cognito_jwt.py`, `admin_roles.py`, `square.py`). Shared modules are bundled per Lambda by Terraform (`event_platform_shared_sources` in `backend/event_platform_public.tf`): **a new shared module must be added there** (`tests/test_event_packaging.py` fails if it is missing).
- `backend/` Terraform; `frontend/src/` the SPA (`EventManager.jsx` = organiser console, `EventRegister.jsx` = checkout, `AddonsStep.jsx` / `EventAddons.jsx` = add-ons, `auth/` = attendee sign-in); `tests/`, `docs/`, `scripts/`.

## Conventions
- Match existing style: Lambda `respond()`/`cors_headers()` helpers, env-var config wired in Terraform, server-side authoritative pricing (never trust client amounts), idempotent conditional writes for payment state, every capacity unit reserved in the same transaction as the registration.
- Never hardcode secrets or AWS/Cognito identifiers; use Terraform vars/outputs and `VITE_*` build variables (public only). Never log tokens, passwords or codes.
- Prices in JPY, times in JST (Asia/Tokyo). New UI strings go in `frontend/src/locales/en.json`. New frontend code goes in new files rather than growing `EventManager.jsx`.
- Small commits; regression tests alongside features. Checks before pushing: `scripts/run_tests.sh`, `cd frontend && npm test && npm run build`, and `terraform validate` for infra changes.
