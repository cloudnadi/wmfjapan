# Frontend (React + Vite)

The WMF Japan events site: <https://wmfevents.cloudnadi.com/>. Full documentation: [../docs/WMF_EVENTS_SITE.md](../docs/WMF_EVENTS_SITE.md).

## Tech stack

React 18, Vite 5, plain JS/JSX, one global `styles.css` plus `publicEvents.css` / `site.css`. Hand-rolled router in `src/App.jsx`.
Cognito via `@aws-sdk/client-cognito-identity-provider` (loaded on demand). Tests: Vitest + Testing Library (jsdom).

## Commands

```bash
npm ci               # install
cp .env.example .env.local
npm run dev          # local dev server
npm test             # run the tests once  (npm run test:watch to watch)
npm run build        # production bundle in dist/
npm run build:preview
```

## Layout

- `src/siteConfig.js` organisation/branding settings (all `VITE_*` overridable)
- `src/eventsApi.js` public event-platform client; `src/eventModel.js` event data model, defaults and derived state
- `src/auth/` attendee sign-in: Cognito email flows, social sign-in with PKCE, session, redirect allow-list
- `src/HomePage.jsx`, `EventsList.jsx`, `EventPage.jsx`, `EventRegister.jsx`, `EventRegistered.jsx`, `MyEventRegistrations.jsx`, `SignInPage.jsx`
- `src/locales/en.json` strings (`ja.json` is registered and empty: missing keys fall back to English)
- `src/__tests__/` tests and neutral fixtures (no real events, people or credentials)

## Configuration

Copy `.env.example` (development), `env.preview.example` or `env.production.example` and fill in the placeholders. Everything in a
`VITE_*` variable is public. Deployment sets them from Terraform outputs (see the deploy workflows).

## Build and deploy

```bash
cp env.production.example .env.production   # fill in from the production Terraform outputs
npm run build                                # → dist/
npm run preview                              # serve the production build locally
```

Preview environment: `cp env.preview.example .env.preview && npm run build:preview`.

Hosting is S3 + CloudFront, deployed by the GitHub Actions workflows (`scripts/deploy_frontend.sh` syncs `dist/` and invalidates
CloudFront). To upload by hand: `aws s3 sync dist/ s3://<bucket> --delete` then invalidate the distribution.
