# Inputs the host app supplies — this is the plugin's entire coupling
# surface to the rest of the infrastructure. Nothing in main.tf reaches for a
# resource outside this module except through one of these.

variable "name_prefix" {
  type        = string
  description = "Host's project-environment prefix, reused so resource names read as part of the same app."
}

variable "suffix" {
  type        = string
  description = "Host's random disambiguation suffix for table/queue-style names."
}

variable "environment" {
  type = string
}

variable "aws_region" {
  type = string
}

variable "dynamodb_billing_mode" {
  type = string
}

variable "log_retention_days" {
  type = number
}

variable "lambda_timeout" {
  type = number
}

variable "lambda_memory" {
  type = number
}

variable "lambda_role_arn" {
  type        = string
  description = "The host's shared Lambda execution role ARN — this module attaches its own additional inline policy to it rather than creating a role of its own."
}

variable "lambda_role_id" {
  type        = string
  description = "That same role's id/name, for aws_iam_role_policy's `role` argument."
}

variable "common_env" {
  type        = map(string)
  description = "The host's shared lambda_env map (REGION, EVENTS_TABLE, DYNAMODB_TABLE, SES_FROM_EMAIL, ADMIN_EMAIL, FRONTEND_URL, ...). Merged with this module's own env vars per function."
}

variable "otp_token_secret" {
  type        = string
  sensitive   = true
  description = "The host's reg-token signing secret — the seat_upgrade Lambda verifies the same token my_registrations mints, so it must share this secret rather than minting its own."
}

variable "celebrity_dinner_registrations_table_name" {
  type        = string
  default     = ""
  description = "Name of the host's standalone Celebrity Dinner registrations table (plugins/celebrity-dinner is not deployed here — this table currently lives at the host level, see backend/dynamodb.tf). Only the seat_upgrade Lambda's dinner add-on reads it, to enforce that a registration picks either the standalone Celebrity Dinner registration or the seat-upgrade's bundled dinner add-on, never both. Optional/blank skips the cross-check (e.g. in an environment where the standalone feature isn't deployed)."
}

variable "celebrity_dinner_config_table_name" {
  type        = string
  default     = ""
  description = "Name of the host's Celebrity Dinner config table (backend/dynamodb.tf's aws_dynamodb_table.celebrity_dinner_config). This module owns none of the dinner add-on's own configuration — price, venue, guest-age condition, etc. all live there, set from the admin's Celebrity Dinner tab (frontend/src/CelebrityDinnerManagement.jsx) — the seat_upgrade Lambda only reads addOnEnabled/dinnerPriceAdults/dinnerPriceKids914/dinnerPriceKids68/dinnerPriceKidsUnder6 off it (see get_celebrity_dinner_addon_config; the same age-tiered prices as the standalone booking, never discounted). Optional/blank disables the add-on entirely (e.g. an environment where the Celebrity Dinner plugin isn't deployed)."
}

variable "assets_bucket_name" {
  type        = string
  description = "The host's shared public-assets S3 bucket (backend/assets.tf) — manage_seat_upgrades uploads seat map images here, the same bucket/IAM policy manage_site_config's carousel upload already uses. Not part of common_env since only this one function needs it."
}

variable "assets_base_url" {
  type        = string
  description = "The host's local.assets_base_url (CloudFront/custom-domain base) — prefixed onto an uploaded seat map image's S3 key to build its public URL."
}

# ── API Gateway wiring ───────────────────────────────────────────────────────

variable "rest_api_id" {
  type = string
}

variable "rest_api_execution_arn" {
  type = string
}

variable "webhooks_resource_id" {
  type        = string
  description = "Resource id of the host-owned, shared /webhooks mount point — this module's own webhook mounts /webhooks/seat-upgrade/{provider} under it as a sibling of every other plugin's webhook route, rather than creating its own top-level \"webhooks\" resource (which would conflict with any other plugin doing the same under the API root)."
}

variable "admin_event_item_resource_id" {
  type        = string
  description = "Resource id of /admin/events/{eventType} — this module's admin routes mount under it as siblings of entry-slots etc."
}

variable "my_resource_id" {
  type        = string
  description = "Resource id of /my — this module's self-service routes mount under it as a sibling of /my/registrations."
}

variable "cognito_authorizer_id" {
  type = string
}

variable "cors_response_parameters" {
  type        = map(string)
  description = "The host's local.cors_headers, reused verbatim so every OPTIONS response matches the rest of the API."
}

# ── Payment providers ────────────────────────────────────────────────────────
# All optional (default ""), so the plugin deploys cleanly before either
# provider account exists — checkout requests just come back with a clear
# "not configured yet" error (see seat_upgrade/handler.py) instead of the
# feature being half-wired. Fill these in via a .tfvars file that is NOT
# committed (see ../../docs/PAYMENTS.md).

variable "paypay_api_key" {
  type      = string
  sensitive = true
  default   = ""
}

variable "paypay_api_secret" {
  type      = string
  sensitive = true
  default   = ""
}

variable "paypay_merchant_id" {
  type      = string
  sensitive = true
  default   = ""
}

variable "paypay_env" {
  type    = string
  default = "sandbox"
}

variable "square_access_token" {
  type      = string
  sensitive = true
  default   = ""
}

variable "square_location_id" {
  type    = string
  default = ""
}

variable "square_webhook_signature_key" {
  type      = string
  sensitive = true
  default   = ""
}

variable "square_webhook_url" {
  type        = string
  default     = ""
  description = "Must exactly match the URL entered in the Square webhook subscription's dashboard settings — see square.py's verify_webhook_signature docstring for why a mismatch here silently fails verification."
}

variable "square_env" {
  type    = string
  default = "sandbox"
}
