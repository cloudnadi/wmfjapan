# WMF Japan Event Registration

This project includes:

- `backend/` Terraform for AWS infrastructure
- `lambdas/` Python Lambda handlers
- `frontend/` React (Vite) web app
- `scripts/` operational scripts (Lambda packaging, member CSV import)

## Documentation

- [MULTI_EVENT_ARCHITECTURE.md](MULTI_EVENT_ARCHITECTURE.md) — multi-event data model, tables, and API routes
- [ADMIN_DASHBOARD_SETUP.md](ADMIN_DASHBOARD_SETUP.md) — Cognito admin auth, dashboard tabs (Registrations / Events / Users / Partners)
- [MEMBER_LOGIN_GATE.md](MEMBER_LOGIN_GATE.md) — members master list, login gate, and CSV import
- [SEAT_SELECTION.md](SEAT_SELECTION.md) — Celebrity Event seat map, seat locking, and admin seat management
- [CLOUDFRONT_DEPLOYMENT.md](CLOUDFRONT_DEPLOYMENT.md) — S3 + CloudFront frontend hosting

## Frontend (React)

The frontend has been migrated from static HTML/JS to a React application.

### Run locally

```bash
cd frontend
npm install
npm run dev
```

### Configure API base URL

The React app reads the API base URL from `VITE_API_BASE_URL`.

Create `frontend/.env`:

```bash
VITE_API_BASE_URL=https://<your-api-id>.execute-api.<region>.amazonaws.com/prod
```

If `VITE_API_BASE_URL` is not set, it falls back to the existing API URL in `frontend/src/api.js`.

### Build for S3 static hosting

```bash
cd frontend
npm run build
```

Production files are generated in `frontend/dist/`.

Upload the contents of `frontend/dist/` to your S3 bucket (configured for static website hosting), then optionally place CloudFront in front for HTTPS and caching.

## Preview Environment

You can run a full preview stack (separate API stage, Lambda resources, DynamoDB tables, and frontend domain) using Terraform `environment = "preview"`.

1. Create preview backend variables:

```bash
cd backend
cp terraform.preview.tfvars.example terraform.preview.tfvars
cp backend.preview.hcl.example backend.preview.hcl
```

1. Edit `terraform.preview.tfvars` with real values, then deploy preview:

```bash
terraform init -reconfigure -backend-config=backend.preview.hcl
terraform plan -var-file=terraform.preview.tfvars
terraform apply -var-file=terraform.preview.tfvars
```

1. Configure preview frontend env:

```bash
cd ../frontend
cp env.preview.example .env.preview
```

1. Build frontend using preview mode variables:

```bash
npm run build:preview
```

1. Deploy `frontend/dist/` to the preview S3 bucket from preview Terraform outputs.

## GitHub Actions Deployment Workflows

Two workflows are available:

1. `.github/workflows/deploy-preview.yml`
1. `.github/workflows/deploy-production.yml`

Both workflows run Terraform in `backend/`, build the frontend in `frontend/`, sync `frontend/dist` to S3, and invalidate CloudFront.

Required GitHub configuration:

1. Repository variable: `AWS_REGION` (example: `ap-northeast-1`).
1. Environment secrets for `preview` and `production`:
1. `AWS_ROLE_TO_ASSUME` (recommended, for OIDC) or `AWS_ACCESS_KEY_ID` + `AWS_SECRET_ACCESS_KEY`.
1. `TFVARS_PREVIEW` for preview environment (full contents of preview tfvars file).
1. `TFVARS_PRODUCTION` for production environment (full contents of production tfvars file).

Recommended setup:

1. Use GitHub Environments named `preview` and `production`.
1. Enable required reviewers on the `production` environment for manual approval.
1. Configure Terraform remote state (S3 backend with lockfile) before using CI deployments.

## Terraform S3 Backend (Remote State)

This repository now uses a partial backend block (`backend "s3" {}`) in Terraform, and the concrete backend values are provided during `terraform init`.

For local preview:

```bash
cd backend
cp backend.preview.hcl.example backend.preview.hcl
terraform init -reconfigure -backend-config=backend.preview.hcl
```

For local production:

```bash
cd backend
cp backend.production.hcl.example backend.production.hcl
terraform init -reconfigure -backend-config=backend.production.hcl
```

For GitHub Actions workflows, configure these environment secrets:

1. `TF_STATE_BUCKET`

Optional repository variable:

1. `TF_STATE_KEY_PREFIX` (default: `wmfjapan`)

To configure these values interactively with the GitHub CLI, authenticate with
`gh auth login` and run this from the repository root:

```bash
./scripts/configure_github_actions.sh
```

The script creates the `AWS_REGION` and `TF_STATE_KEY_PREFIX` repository
variables, then configures the `preview` and `production` environment secrets.
It reads the Terraform variable files locally and sends their contents directly
to GitHub without putting them in command-line arguments. Set `GH_REPO=OWNER/REPOSITORY`
when running the script outside a checked-out GitHub repository.

### GitHub OIDC role for workflows

The deployment and `import-members.yml` workflows can use a GitHub OIDC role
instead of stored AWS access keys. Apply the Terraform in `backend/` with
`create_github_actions_oidc_provider = true` in exactly one state (normally
production) to create the account-level GitHub OIDC provider. Set it to `false`
in every other state, where Terraform reads the already-created provider.

Terraform outputs `github_actions_deploy_role_arn`. Add that value as
the `AWS_ROLE_TO_ASSUME` secret on both GitHub Environments (`preview` and
`production`). This is one shared, account-level `github-actions-deploy` role.
It trusts only GitHub Actions OIDC tokens issued for the
`cloudnadi/wmfjapan` repository. It uses the AWS-managed `AdministratorAccess` policy because the
deployment workflows run Terraform apply across the full application
infrastructure; access is bounded by the OIDC repository trust policy.
