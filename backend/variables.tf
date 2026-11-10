variable "aws_region" {
  description = "AWS region to deploy resources"
  type        = string
  default     = "ap-northeast-1" # Tokyo
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

# ── Payments (Square card payments for event registrations) ──────────────────
# Optional: empty = not configured yet (card checkout returns a clear error).
# Set real values via a git-ignored tfvars file; never commit them.

variable "square_access_token" {
  description = "Square API access token"
  type        = string
  sensitive   = true
  default     = ""
}

variable "square_location_id" {
  description = "Square location id payments are taken against"
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
