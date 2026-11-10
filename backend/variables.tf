variable "aws_region" {
  description = "AWS region to deploy resources"
  type        = string
  default     = "ap-northeast-1" # Tokyo
}

variable "github_actions_repository" {
  description = "GitHub owner/repository allowed to assume GitHub Actions OIDC roles"
  type        = string
  default     = "cloudnadi/wmfjapan"
}

variable "github_actions_state_bucket" {
  description = "S3 bucket containing the Terraform state read by GitHub Actions"
  type        = string
  default     = "cloudnadi-terraform-state"
}

variable "github_actions_state_key_prefix" {
  description = "Terraform state key prefix used by GitHub Actions"
  type        = string
  default     = "wmfjapan"
}

variable "create_github_actions_oidc_provider" {
  description = "Create the account-level GitHub Actions OIDC provider; set true in exactly one Terraform state only"
  type        = bool
  default     = false
}

variable "create_github_actions_deploy_role" {
  description = "Create the account-level GitHub Actions deployment role; set true in exactly one Terraform state only"
  type        = bool
  default     = false
}

variable "environment" {
  description = "Deployment environment (dev / staging / preview / prod)"
  type        = string
  default     = "prod"

  validation {
    condition     = contains(["dev", "staging", "preview", "prod"], var.environment)
    error_message = "environment must be one of: dev, staging, preview, prod"
  }
}

variable "project_name" {
  description = "Project name used in resource naming"
  type        = string
}

variable "ses_from_email" {
  description = "Verified SES sender email address (used in confirmation emails)"
  type        = string
}

variable "admin_email" {
  description = "Admin email to receive new registration notifications"
  type        = string
}

variable "admin_email_additional" {
  description = "Additional admin emails (comma-separated) to CC on notifications"
  type        = string
  default     = ""
}

variable "member_register_url" {
  description = "Public membership registration URL shown to emails not present in the members master table"
  type        = string
  default     = "https://www.wmfjapan.com/en/member/register"
}

variable "event_name" {
  description = "Event display name used in emails"
  type        = string
}

variable "event_date" {
  description = "Event date string used in emails"
  type        = string
  default     = "July 12, 2026"
}

variable "event_venue" {
  description = "Event venue used in emails"
  type        = string
  default     = "Edogawa East Citizen Hall, Tokyo"
}

variable "event_address" {
  description = "Event address used in emails"
  type        = string
  default     = "1 Chome-17-1 Higashimizue, Edogawa City, Tokyo 132-0014"
}

variable "lambda_timeout" {
  description = "Lambda function timeout in seconds"
  type        = number
  default     = 30
}

variable "lambda_memory" {
  description = "Lambda function memory in MB"
  type        = number
  default     = 256
}

variable "dynamodb_billing_mode" {
  description = "DynamoDB billing mode: PAY_PER_REQUEST or PROVISIONED"
  type        = string
  default     = "PAY_PER_REQUEST"
}

variable "cognito_admin_email" {
  description = "Email for the initial Cognito admin user"
  type        = string
}

variable "allowed_origins" {
  description = "CORS allowed origins for API Gateway (e.g. your website domain)"
  type        = string
  default     = "*"
}

variable "log_retention_days" {
  description = "CloudWatch log retention in days"
  type        = number
  default     = 30
}

variable "frontend_domain" {
  description = "Custom domain for the frontend SPA (optional, e.g., example.com). Leave empty to use CloudFront default domain."
  type        = string
  default     = ""
}

variable "acm_certificate_arn" {
  description = "ARN of ACM certificate for HTTPS custom domain (required if frontend_domain is set). Must be in us-east-1 for CloudFront."
  type        = string
  default     = ""
}

variable "enable_s3_frontend" {
  description = "Enable S3 bucket + CloudFront for SPA hosting"
  type        = bool
  default     = true
}

variable "route53_zone_id" {
  description = "Route53 hosted zone ID for the parent domain (e.g., cloudnadi.com). Required if frontend_domain is set and enable_domain_management is true."
  type        = string
  default     = ""
}

variable "enable_domain_management" {
  description = "Enable ACM certificate and Route53 DNS management for frontend_domain"
  type        = bool
  default     = true
}

# ── Seat Upgrade plugin: payment providers ───────────────────────────────────
# All optional (empty string = "not configured yet" — the checkout endpoint
# returns a clear error rather than failing at apply time). Set real values
# via a gitignored terraform.tfvars or -var-file once PayPay/Square accounts
# exist; see plugins/seat-upgrade/docs/PAYMENTS.md.

variable "paypay_api_key" {
  description = "PayPay for Business API key"
  type        = string
  sensitive   = true
  default     = ""
}

variable "paypay_api_secret" {
  description = "PayPay for Business API secret"
  type        = string
  sensitive   = true
  default     = ""
}

variable "paypay_merchant_id" {
  description = "PayPay for Business merchant id"
  type        = string
  sensitive   = true
  default     = ""
}

variable "paypay_env" {
  description = "\"production\" or \"sandbox\""
  type        = string
  default     = "sandbox"
}

variable "square_application_id" {
  description = "Square Application ID"
  type        = string
  default     = ""
}

variable "square_access_token" {
  description = "Square API access token"
  type        = string
  sensitive   = true
  default     = ""
}

variable "square_location_id" {
  description = "Square location id payments are taken against — shared by the seat-upgrade plugin and the standalone Celebrity Dinner sign-up, same merchant account"
  type        = string
  default     = ""
}

variable "square_seat_upgrade_webhook_signature_key" {
  description = "Signature key from the Square webhook subscription's settings, for the seat-upgrade plugin's own webhook"
  type        = string
  sensitive   = true
  default     = ""
}

variable "square_seat_upgrade_webhook_url" {
  description = "Must exactly match the URL entered in the Square webhook subscription dashboard (usually https://{api-id}.execute-api.{region}.amazonaws.com/{stage}/webhooks/seat-upgrade/square)"
  type        = string
  default     = ""
}

variable "square_npo_webhook_signature_key" {
  description = "Signature key from the Square webhook subscription's settings, for the NPO membership (My Page) webhook — its own subscription/key, same merchant account"
  type        = string
  sensitive   = true
  default     = ""
}

variable "square_npo_webhook_url" {
  description = "Must exactly match the URL entered in the Square webhook subscription dashboard (usually https://{api-id}.execute-api.{region}.amazonaws.com/{stage}/webhooks/npo-membership/square)"
  type        = string
  default     = ""
}

variable "square_celebrity_dinner_webhook_signature_key" {
  description = "Signature key from the Square webhook subscription's settings, for the standalone Celebrity Dinner sign-up's own webhook — a separate subscription/key from the seat-upgrade plugin's, even though both use the same square_access_token merchant account"
  type        = string
  sensitive   = true
  default     = ""
}

variable "square_celebrity_dinner_webhook_url" {
  description = "Must exactly match the URL entered in the Square webhook subscription dashboard (usually https://{api-id}.execute-api.{region}.amazonaws.com/{stage}/webhooks/celebrity-dinner/square)"
  type        = string
  default     = ""
}

variable "square_seat_selection_webhook_signature_key" {
  description = "Signature key from the Square webhook subscription's settings, for the seat-selection plugin's own webhook"
  type        = string
  sensitive   = true
  default     = ""
}

variable "square_seat_selection_webhook_url" {
  description = "Must exactly match the URL entered in the Square webhook subscription dashboard (usually https://{api-id}.execute-api.{region}.amazonaws.com/{stage}/webhooks/seat-selection/square)"
  type        = string
  default     = ""

}

variable "square_env" {
  description = "\"production\" or \"sandbox\""
  type        = string
  default     = "sandbox"
}

variable "square_events_webhook_signature_key" {
  description = "Signature key of the Square webhook subscription for event-platform registrations (its own subscription/key, same merchant account)"
  type        = string
  sensitive   = true
  default     = ""
}

variable "square_events_webhook_url" {
  description = "Must exactly match the URL entered in the Square webhook subscription dashboard (usually https://{api-id}.execute-api.{region}.amazonaws.com/{stage}/webhooks/event-registrations/square)"
  type        = string
  default     = ""
}

# ── Attendee accounts (Cognito) ──────────────────────────────────────────────

variable "attendee_cognito_domain_prefix" {
  description = "Hosted UI domain prefix for the attendee user pool (globally unique per region). Empty = <project>-<env>-attendees."
  type        = string
  default     = ""
}

variable "attendee_extra_origins" {
  description = "Extra site origins allowed for sign-in redirects, e.g. [\"http://localhost:5173\"] for local development. Never add localhost in production."
  type        = list(string)
  default     = []
}

variable "attendee_social_providers" {
  description = "Social identity providers to enable, keyed by the Cognito provider name (Google, Facebook, LoginWithAmazon). Example: { Google = { provider_type = \"Google\", client_id = \"...apps.googleusercontent.com\" } }"
  type = map(object({
    provider_type    = string
    client_id        = string
    authorize_scopes = optional(string)
  }))
  default = {}
}

variable "attendee_social_provider_secrets" {
  description = "OAuth client secrets for attendee_social_providers, keyed by the same provider names. Stored in Terraform state: keep the state bucket private and supply this via CI secrets."
  type        = map(string)
  default     = {}
  sensitive   = true
}
