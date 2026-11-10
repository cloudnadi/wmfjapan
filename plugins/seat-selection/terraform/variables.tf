variable "name_prefix" {
  description = "Prefix for this module's own resource names (tables, Lambdas, log groups, IAM role), e.g. \"myapp-prod\". Keep it unique within the AWS account/region if you mount this module more than once."
  type        = string
}

variable "suffix" {
  description = "Optional suffix appended to the two DynamoDB table names (e.g. a random_id hex, matching the host's own convention for globally-unique table names across destroy/recreate cycles). Omit for a plain name."
  type        = string
  default     = ""
}

variable "aws_region" {
  description = "AWS region — passed through to the module's Lambdas as REGION."
  type        = string
}

variable "dynamodb_billing_mode" {
  type    = string
  default = "PAY_PER_REQUEST"
}

variable "lambda_runtime" {
  type    = string
  default = "python3.12"
}

variable "lambda_timeout" {
  type    = number
  default = 10
}

variable "lambda_memory" {
  type    = number
  default = 128
}

variable "log_retention_days" {
  type    = number
  default = 14
}

variable "rest_api_id" {
  description = "ID of the existing aws_api_gateway_rest_api this module mounts its routes under."
  type        = string
}

variable "rest_api_execution_arn" {
  description = "Execution ARN of that REST API, for the Lambda invoke permissions."
  type        = string
}

variable "webhooks_resource_id" {
  description = "Resource id of the host-owned, shared /webhooks mount point — this module's own webhook mounts /webhooks/seat-selection/{provider} under it as a sibling of every other plugin's webhook route, rather than creating its own top-level \"webhooks\" resource (which would conflict with any other plugin doing the same under the API root)."
  type        = string
}

variable "frontend_url" {
  description = "The registrant-facing site's base URL (no trailing slash), for payment-provider checkout redirect_urls and the CTA link/logo in purchase-lifecycle emails."
  type        = string
  default     = ""
}

variable "qr_code_layer_arn" {
  description = "ARN of a Lambda layer providing the `qrcode` PyPI package (this project has no pip-install step, so it's a layer, not a bundled dependency). Required to embed a check-in QR in the \"paid\" email — omit it and that email still sends, just without a QR attachment. Pass your host's own qr_code layer ARN if it already has one (e.g. this repo's aws_lambda_layer_version.qr_code) rather than publishing a second copy."
  type        = string
  default     = ""
}

# The next four variables let the "paid" email's guest check-in ticket PDF
# show a registrant's zone upgrade / celebrity dinner add-ons alongside
# their seats (looked up by registrationId, same RegistrationIdIndex
# convention both tables already use). All four are optional and default to
# "" — a host with no such tables (or a second app mounting this module
# without them) just gets a ticket with no Zone Upgrade / Celebrity Dinner
# section, same graceful-omission pattern as qr_code_layer_arn above. Each
# grant is read-only and scoped to exactly this one table, so mounting this
# plugin never needs to know about the rest of a host's schema.
variable "seat_upgrades_table_arn" {
  description = "ARN of the host's seat-upgrade plugin's upgrades table (e.g. module.seat_upgrade.seat_upgrades_table_arn), granted read-only. Optional."
  type        = string
  default     = ""
}

variable "seat_upgrades_table_name" {
  description = "Name of the same table, for the Lambda env var. Optional, paired with seat_upgrades_table_arn."
  type        = string
  default     = ""
}

variable "celebrity_dinner_registrations_table_arn" {
  description = "ARN of the host's Celebrity Dinner registrations table, granted read-only. Optional."
  type        = string
  default     = ""
}

variable "celebrity_dinner_registrations_table_name" {
  description = "Name of the same table, for the Lambda env var. Optional, paired with celebrity_dinner_registrations_table_arn."
  type        = string
  default     = ""
}

variable "public_parent_resource_id" {
  description = "API Gateway resource ID to nest the public GET route under (e.g. an existing /events/{eventType} resource). The module creates GET /{parent}/seats there."
  type        = string
}

variable "admin_parent_resource_id" {
  description = "API Gateway resource ID to nest the admin routes under (e.g. an existing /admin/events/{eventType} resource, already protected the way your app protects admin routes). The module creates GET+POST /{parent}/seats and DELETE /{parent}/seats/{seatId} there, both requiring cognito_authorizer_id."
  type        = string
}

variable "admin_root_resource_id" {
  description = "API Gateway resource ID of your bare /admin resource (not the per-event one above) — the door check-in screen scans a QR carrying only a token, with no eventType in the URL to nest under, so the module creates POST /admin/seat-check-in directly here instead, requiring cognito_authorizer_id the same as every other admin route."
  type        = string
}

variable "cognito_authorizer_id" {
  description = "API Gateway Cognito User Pools authorizer ID protecting the admin routes."
  type        = string
}

variable "cors_allowed_origin" {
  description = "Value for Access-Control-Allow-Origin on this module's OPTIONS responses."
  type        = string
  default     = "*"
}

variable "tags" {
  description = "Extra tags merged onto every resource this module creates (in addition to a Name tag the module sets itself)."
  type        = map(string)
  default     = {}
}

# ── Payment + email (for the online-checkout webhook Lambda only) ──────────
#
# The registrant-facing purchase-creation/checkout routes are NOT part of
# this module — they live in your own host Lambda (see the plugin README's
# "the one route this module doesn't own"), which needs these same values
# wired into its own environment separately. Only the public webhook here
# needs them, to re-verify a payment directly with the provider and send
# the "paid" confirmation email/admin notification itself.

variable "ses_from_email" {
  description = "Verified SES sender address for purchase-lifecycle emails the webhook sends directly (a payment confirmed async, with no registrant request in flight to send it from instead)."
  type        = string
  default     = ""
}

variable "admin_email" {
  description = "Where the webhook's admin notification emails go. Empty disables them."
  type        = string
  default     = ""
}

variable "paypay_api_key" {
  type      = string
  default   = ""
  sensitive = true
}

variable "paypay_api_secret" {
  type      = string
  default   = ""
  sensitive = true
}

variable "paypay_merchant_id" {
  type    = string
  default = ""
}

variable "paypay_env" {
  description = "\"production\" or anything else (defaults to sandbox)."
  type        = string
  default     = ""
}

variable "square_access_token" {
  type      = string
  default   = ""
  sensitive = true
}

variable "square_location_id" {
  type    = string
  default = ""
}

variable "square_webhook_signature_key" {
  type      = string
  default   = ""
  sensitive = true
}

variable "square_webhook_url" {
  description = "The exact URL configured in Square's webhook subscription settings (must byte-match what Square is told to call, usually this API Gateway stage's own invoke URL for the webhook route)."
  type        = string
  default     = ""
}

variable "square_env" {
  description = "\"production\" or anything else (defaults to sandbox)."
  type        = string
  default     = ""
}
