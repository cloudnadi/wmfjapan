# ─────────────────────────────────────────────────────────────────────────────
# Plugins — self-contained feature modules under ../plugins/*, each owning
# its own tables, Lambdas and slice of the REST API. This file is the entire
# coupling surface between this app and any of them: everything a plugin
# needs from the host is passed in explicitly below, and everything the host
# needs back from a plugin (like the API deployment trigger hash) comes out
# through its outputs — nothing reaches across module boundaries any other
# way. See plugins/seat-upgrade/README.md for what the module itself does.
# ─────────────────────────────────────────────────────────────────────────────

module "seat_upgrade" {
  source = "../plugins/seat-upgrade/backend/terraform"

  name_prefix           = local.name_prefix
  suffix                = local.suffix
  environment           = var.environment
  aws_region            = var.aws_region
  dynamodb_billing_mode = var.dynamodb_billing_mode
  log_retention_days    = var.log_retention_days
  lambda_timeout        = var.lambda_timeout
  lambda_memory         = var.lambda_memory

  lambda_role_arn = aws_iam_role.lambda_exec.arn
  lambda_role_id  = aws_iam_role.lambda_exec.id
  common_env      = local.lambda_env

  # Same secret the host's own my_registrations Lambda mints reg tokens with,
  # so the plugin can verify them without minting one of its own.
  otp_token_secret = random_password.otp_token_secret.result

  # Lets the seat_upgrade Lambda's dinner add-on refuse to bundle a dinner
  # onto a seat upgrade when the registration already has a standalone
  # Celebrity Dinner sign-up. The reverse check (host-level SEAT_UPGRADES_TABLE
  # env var on my_registrations/manage_celebrity_dinner) is wired below.
  celebrity_dinner_registrations_table_name = aws_dynamodb_table.celebrity_dinner_registrations.name

  # The Celebrity Dinner plugin's own config (price, discount %, addOnEnabled,
  # ...) — the seat_upgrade Lambda reads this directly rather than owning any
  # dinner pricing itself. See plugins/seat-upgrade/backend/lambdas/seat_upgrade/
  # handler.py's get_celebrity_dinner_addon_config.
  celebrity_dinner_config_table_name = aws_dynamodb_table.celebrity_dinner_config.name

  # For manage_seat_upgrades' seat-map image upload — same S3 bucket/URL
  # manage_site_config's carousel upload already writes to (backend/assets.tf).
  assets_bucket_name = aws_s3_bucket.assets.id
  assets_base_url    = local.assets_base_url

  rest_api_id                  = aws_api_gateway_rest_api.main.id
  rest_api_execution_arn       = aws_api_gateway_rest_api.main.execution_arn
  admin_event_item_resource_id = aws_api_gateway_resource.admin_event_item.id
  my_resource_id               = aws_api_gateway_resource.my.id
  # Shared across every plugin's payment webhook — see its own comment in
  # api_gateway.tf for why this can't be a resource the module creates itself.
  webhooks_resource_id     = aws_api_gateway_resource.webhooks.id
  cognito_authorizer_id    = aws_api_gateway_authorizer.cognito.id
  cors_response_parameters = local.cors_headers

  # Payment provider credentials — all optional (default ""); see
  # plugins/seat-upgrade/docs/PAYMENTS.md for how to fill these in once
  # PayPay/Square accounts exist. Supply real values via a gitignored
  # terraform.tfvars or -var-file, never inline here.
  paypay_api_key               = var.paypay_api_key
  paypay_api_secret            = var.paypay_api_secret
  paypay_merchant_id           = var.paypay_merchant_id
  paypay_env                   = var.paypay_env
  square_access_token          = var.square_access_token
  square_location_id           = var.square_location_id
  square_webhook_signature_key = var.square_seat_upgrade_webhook_signature_key
  square_webhook_url           = var.square_seat_upgrade_webhook_url
  square_env                   = var.square_env
}
