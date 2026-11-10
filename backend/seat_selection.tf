# ─────────────────────────────────────────────────────────────────────────────
# Seat Selection plugin — a self-contained, reusable theater-style seat map
# (own DynamoDB tables, own Lambdas + IAM role, own API Gateway routes)
# mounted under this app's existing REST API. See plugins/seat-selection/ for
# the module itself; this is the only file that wires it into wmfjapan.
#
# my_registrations/manage_registrations still import
# plugins/seat-selection/backend/seat_selection_logic.py (and, for the paid
# purchase flow, seat_purchase_logic.py + the payment adapters) directly —
# see their archive_file blocks in lambda.tf — to claim/release seats and
# create/checkout/cancel a purchase as part of their own request handling.
# That integration glue is this host app's, not the plugin's, so it stays
# outside the module (see the plugin README's "the one route this module
# doesn't own"). The `/my/registrations/{registrationId}/seat-purchases...`
# routes those Lambdas expose live in api_gateway_seats.tf accordingly.
#
# The module's own seat_purchase_webhook Lambda is the one piece of the paid
# flow that's fully self-contained: it never touches the registrations
# table, only the purchases table this module owns, so it needs no access
# beyond what the module already grants itself.
# ─────────────────────────────────────────────────────────────────────────────

module "seat_selection" {
  source = "../plugins/seat-selection/terraform"

  name_prefix           = local.name_prefix
  suffix                = local.suffix
  aws_region            = var.aws_region
  dynamodb_billing_mode = var.dynamodb_billing_mode
  lambda_timeout        = var.lambda_timeout
  lambda_memory         = var.lambda_memory
  log_retention_days    = var.log_retention_days
  cors_allowed_origin   = var.allowed_origins
  frontend_url          = local.lambda_env.FRONTEND_URL
  # Reuse this app's own qrcode-package layer for the check-in QR embedded
  # in the "paid" email, rather than publishing a second copy of it.
  qr_code_layer_arn = aws_lambda_layer_version.qr_code.arn
  tags              = { Environment = var.environment }

  # Optional read-only grants so the "paid" email's guest check-in ticket
  # PDF can show a registrant's zone upgrade / celebrity dinner add-ons
  # alongside their seats — see those variables' descriptions in the
  # module's own variables.tf.
  seat_upgrades_table_arn                   = module.seat_upgrade.seat_upgrades_table_arn
  seat_upgrades_table_name                  = module.seat_upgrade.seat_upgrades_table_name
  celebrity_dinner_registrations_table_arn  = aws_dynamodb_table.celebrity_dinner_registrations.arn
  celebrity_dinner_registrations_table_name = aws_dynamodb_table.celebrity_dinner_registrations.name

  rest_api_id               = aws_api_gateway_rest_api.main.id
  rest_api_execution_arn    = aws_api_gateway_rest_api.main.execution_arn
  webhooks_resource_id      = aws_api_gateway_resource.webhooks.id
  public_parent_resource_id = aws_api_gateway_resource.event_type.id
  admin_parent_resource_id  = aws_api_gateway_resource.admin_event_item.id
  admin_root_resource_id    = aws_api_gateway_resource.admin.id
  cognito_authorizer_id     = aws_api_gateway_authorizer.cognito.id

  # The webhook's own payment provider verification + purchase-confirmed
  # email — the registrant-facing checkout-creation side of these same
  # credentials is wired into my_registrations directly (see lambda.tf).
  ses_from_email               = var.ses_from_email
  admin_email                  = var.admin_email
  paypay_api_key               = var.paypay_api_key
  paypay_api_secret            = var.paypay_api_secret
  paypay_merchant_id           = var.paypay_merchant_id
  paypay_env                   = var.paypay_env
  square_access_token          = var.square_access_token
  square_location_id           = var.square_location_id
  square_webhook_signature_key = var.square_seat_selection_webhook_signature_key
  square_webhook_url           = var.square_seat_selection_webhook_url
  square_env                   = var.square_env
}
