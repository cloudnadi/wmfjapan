# WMF Japan Events (wmfevents.cloudnadi.com)

Event registration and management site for [WMF Japan](https://www.wmfjapan.org/). One platform does everything: organisers create events, tickets, registration forms and add-ons (entry slots, zone upgrades, seat reservation) in the console at `/admin`; attendees browse events, register as guests or with an Amazon Cognito account (email or social sign-in), pay by card / bank transfer / PayPay, and receive a QR ticket that is scanned at the door. There are no memberships and nothing is specific to one event.

Start with [docs/WMF_EVENTS_SITE.md](docs/WMF_EVENTS_SITE.md).

## Repository layout

| Path | Contents |
|---|---|
| `frontend/` | React 18 + Vite SPA (public site, attendee sign-in, organiser console, check-in scanner) |
| `lambdas/` | Python Lambdas (boto3 only): public API, admin API, payment webhook, hold-expiry job, and their shared modules |
| `backend/` | Terraform: API Gateway, Lambdas, DynamoDB, two Cognito pools, S3/CloudFront, SES, alarms |
| `scripts/` | `run_tests.sh`, `deploy_frontend.sh`, `package_lambdas.sh` |
| `tests/` | Backend tests (stdlib `unittest`; `moto` for the end-to-end DynamoDB test) |
| `docs/` | Site, Cognito and deployment guide; organiser guide |

## Documentation

- [docs/WMF_EVENTS_SITE.md](docs/WMF_EVENTS_SITE.md) — architecture, look and feel, configuration, Cognito setup, social sign-in, guest checkout, per-event add-ons, deployment, items needing WMF Japan input
- [EVENT_PLATFORM.md](EVENT_PLATFORM.md) — data model, registration rules, add-ons, admin API, security notes
- [docs/EVENT_ADMIN_GUIDE.md](docs/EVENT_ADMIN_GUIDE.md) — organiser how-to
- [CLOUDFRONT_DEPLOYMENT.md](CLOUDFRONT_DEPLOYMENT.md) — S3 + CloudFront hosting details

## Run locally

```bash
cd frontend
npm ci
cp .env.example .env.local     # fill in the placeholders
npm run dev                    # http://localhost:5173
npm test                       # Vitest
npm run build                  # frontend/dist
```

```bash
pip install boto3 openpyxl "moto[dynamodb]"
scripts/run_tests.sh           # every backend test file in its own process
```

The frontend is configured only through public `VITE_*` build variables (see `frontend/.env.example`). Never put a secret in one.

## Deploy

Infrastructure is Terraform in `backend/`; the GitHub Actions workflows (`.github/workflows/deploy-preview.yml`, `deploy-production.yml`, manual dispatch) run Terraform, build the frontend with the Terraform outputs injected as `VITE_*`, sync `frontend/dist` to S3 and invalidate CloudFront.

Nothing environment-specific is committed. Provide, per environment:

1. **Terraform variables** (a git-ignored `backend/terraform.<env>.tfvars`, or the `TFVARS_PREVIEW` / `TFVARS_PRODUCTION` secret in CI). The variables are declared in `backend/variables.tf`; the ones without a sensible default are `project_name`, `ses_from_email`, `admin_email`, `cognito_admin_email` and, for the custom domain, `frontend_domain` / `route53_zone_id` / `enable_domain_management`. Square credentials (`square_access_token`, `square_location_id`, `square_events_webhook_*`) are optional until card payments are used.
2. **Remote state:** `terraform init -reconfigure -backend-config=backend.<env>.hcl` with a git-ignored file containing `bucket`, `key`, `region` (and `encrypt = true`). CI reads the `TF_STATE_BUCKET` secret and the optional `TF_STATE_KEY_PREFIX` variable (default `wmfjapan`).
3. **GitHub:** repository variable `AWS_REGION`; environment secrets `AWS_ROLE_TO_ASSUME` (OIDC, recommended) or `AWS_ACCESS_KEY_ID` + `AWS_SECRET_ACCESS_KEY`, `TF_STATE_BUCKET`, `TFVARS_PREVIEW`, `TFVARS_PRODUCTION`. Use GitHub Environments named `preview` and `production`, with required reviewers on `production`.

```bash
cd backend
terraform init -reconfigure -backend-config=backend.preview.hcl
terraform plan  -var-file=terraform.preview.tfvars
terraform apply -var-file=terraform.preview.tfvars
```

After the first apply, create the first organiser with the `create_admin_user_command` output. See [docs/WMF_EVENTS_SITE.md](docs/WMF_EVENTS_SITE.md) for the production checklist (domain, Cognito, social providers, Square webhook).
