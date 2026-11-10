# ─────────────────────────────────────────────────────────────────────────────
# Seat Upgrade plugin — Terraform module
#
# Everything the plugin owns: its two DynamoDB tables, its three Lambdas
# (admin CRUD, registrant self-service, payment-provider webhook receiver),
# their log groups, and its slice of the REST API (mounted under the host's
# existing /admin/events/{eventType}, /my, and API root resources — see
# variables.tf for the exact coupling points).
#
# The host (backend/) calls this as:
#   module "seat_upgrade" { source = "../plugins/seat-upgrade/backend/terraform" ... }
# and folds `module.seat_upgrade.api_trigger_hash` into its own API Gateway
# deployment's trigger hash so a route change here forces a redeploy there.
# ─────────────────────────────────────────────────────────────────────────────

locals {
  # Every function gets the host's shared env vars plus this module's own
  # table names. Payment credentials are added only on the seat_upgrade and
  # seat_upgrade_webhook functions (manage_seat_upgrades never talks to a
  # payment provider — admins mark bank transfers paid, they don't collect
  # card/PayPay payments through the dashboard).
  base_env = merge(var.common_env, {
    SEAT_UPGRADE_TIERS_TABLE           = aws_dynamodb_table.seat_upgrade_tiers.name
    SEAT_UPGRADES_TABLE                = aws_dynamodb_table.seat_upgrades.name
    SEAT_UPGRADE_EMAIL_TEMPLATES_TABLE = aws_dynamodb_table.seat_upgrade_email_templates.name
  })

  payment_env = {
    PAYPAY_API_KEY               = var.paypay_api_key
    PAYPAY_API_SECRET            = var.paypay_api_secret
    PAYPAY_MERCHANT_ID           = var.paypay_merchant_id
    PAYPAY_ENV                   = var.paypay_env
    SQUARE_ACCESS_TOKEN          = var.square_access_token
    SQUARE_LOCATION_ID           = var.square_location_id
    SQUARE_WEBHOOK_SIGNATURE_KEY = var.square_webhook_signature_key
    SQUARE_WEBHOOK_URL           = var.square_webhook_url
    SQUARE_ENV                   = var.square_env
  }
}

# ── DynamoDB ─────────────────────────────────────────────────────────────────

resource "aws_dynamodb_table" "seat_upgrade_tiers" {
  name         = "${var.name_prefix}-seat-upgrade-tiers-${var.suffix}"
  billing_mode = var.dynamodb_billing_mode
  hash_key     = "eventType"
  range_key    = "tierId"

  attribute {
    name = "eventType"
    type = "S"
  }

  attribute {
    name = "tierId"
    type = "S"
  }

  attribute {
    name = "status"
    type = "S"
  }

  # GSI: query ACTIVE tiers within an event without scanning INACTIVE ones.
  global_secondary_index {
    name            = "EventTypeStatusIndex"
    hash_key        = "eventType"
    range_key       = "status"
    projection_type = "ALL"
  }

  point_in_time_recovery {
    enabled = true
  }

  server_side_encryption {
    enabled = true
  }

  tags = {
    Name        = "${var.name_prefix}-seat-upgrade-tiers"
    Environment = var.environment
    Plugin      = "seat-upgrade"
  }
}

resource "aws_dynamodb_table" "seat_upgrades" {
  name         = "${var.name_prefix}-seat-upgrades-${var.suffix}"
  billing_mode = var.dynamodb_billing_mode
  hash_key     = "upgradeId"

  attribute {
    name = "upgradeId"
    type = "S"
  }

  attribute {
    name = "registrationId"
    type = "S"
  }

  attribute {
    name = "eventType"
    type = "S"
  }

  attribute {
    name = "requestedAt"
    type = "S"
  }

  attribute {
    name = "providerPaymentId"
    type = "S"
  }

  # GSI: a registrant's own upgrade requests (self-service view).
  global_secondary_index {
    name            = "RegistrationIdIndex"
    hash_key        = "registrationId"
    range_key       = "requestedAt"
    projection_type = "ALL"
  }

  # GSI: admin listing of all upgrade requests for an event.
  global_secondary_index {
    name            = "EventTypeIndex"
    hash_key        = "eventType"
    range_key       = "requestedAt"
    projection_type = "ALL"
  }

  # GSI: look up a request by the payment provider's own reference (a PayPay
  # merchantPaymentId or a Square Order id) when a webhook or a return-page
  # reconcile call only has that to go on. Sparse — only populated once a
  # checkout has actually started (see seat_upgrade_logic.record_checkout).
  global_secondary_index {
    name            = "ProviderPaymentIdIndex"
    hash_key        = "providerPaymentId"
    projection_type = "ALL"
  }

  point_in_time_recovery {
    enabled = true
  }

  server_side_encryption {
    enabled = true
  }

  tags = {
    Name        = "${var.name_prefix}-seat-upgrades"
    Environment = var.environment
    Plugin      = "seat-upgrade"
  }
}

# Admin-editable copy for the plugin's registrant-facing emails (request /
# paid / refund_requested / cancelled) — see seat_upgrade_emailer.py's
# DEFAULT_TEMPLATES for the built-in defaults each row overrides. One item
# per template key; a template with no row here just uses the default.
# Global to the plugin, not per-event — see docs/SEAT_UPGRADE_FEATURE.md's
# "Editable email templates" section for why.
resource "aws_dynamodb_table" "seat_upgrade_email_templates" {
  name         = "${var.name_prefix}-seat-upgrade-email-templates-${var.suffix}"
  billing_mode = var.dynamodb_billing_mode
  hash_key     = "templateKey"

  attribute {
    name = "templateKey"
    type = "S"
  }

  point_in_time_recovery {
    enabled = true
  }

  server_side_encryption {
    enabled = true
  }

  tags = {
    Name        = "${var.name_prefix}-seat-upgrade-email-templates"
    Environment = var.environment
    Plugin      = "seat-upgrade"
  }
}

# ── IAM ──────────────────────────────────────────────────────────────────────
# An additional inline policy on the host's existing shared Lambda role,
# rather than a role of its own — an IAM role can carry several inline
# policies, so this attaches cleanly without touching the host's own
# aws_iam_role_policy.lambda_policy resource.

resource "aws_iam_role_policy" "seat_upgrade" {
  name = "${var.name_prefix}-seat-upgrade-policy"
  role = var.lambda_role_id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = [
          "dynamodb:GetItem",
          "dynamodb:Query",
          "dynamodb:Scan",
          "dynamodb:PutItem",
          "dynamodb:UpdateItem",
          "dynamodb:DeleteItem"
        ]
        Resource = [
          aws_dynamodb_table.seat_upgrade_tiers.arn,
          "${aws_dynamodb_table.seat_upgrade_tiers.arn}/index/*",
          aws_dynamodb_table.seat_upgrades.arn,
          "${aws_dynamodb_table.seat_upgrades.arn}/index/*",
          aws_dynamodb_table.seat_upgrade_email_templates.arn
        ]
      }
    ]
  })
}

# ── Lambda packaging ─────────────────────────────────────────────────────────

data "archive_file" "manage_seat_upgrades" {
  type        = "zip"
  output_path = "${path.module}/../lambdas/dist/manage_seat_upgrades.zip"

  source {
    content  = file("${path.module}/../lambdas/manage_seat_upgrades/handler.py")
    filename = "handler.py"
  }
  source {
    content  = file("${path.module}/../lambdas/seat_upgrade_logic.py")
    filename = "seat_upgrade_logic.py"
  }
  source {
    content  = file("${path.module}/../lambdas/seat_upgrade_emailer.py")
    filename = "seat_upgrade_emailer.py"
  }
}

data "archive_file" "seat_upgrade" {
  type        = "zip"
  output_path = "${path.module}/../lambdas/dist/seat_upgrade.zip"

  source {
    content  = file("${path.module}/../lambdas/seat_upgrade/handler.py")
    filename = "handler.py"
  }
  source {
    content  = file("${path.module}/../lambdas/seat_upgrade_logic.py")
    filename = "seat_upgrade_logic.py"
  }
  source {
    content  = file("${path.module}/../lambdas/seat_upgrade_emailer.py")
    filename = "seat_upgrade_emailer.py"
  }
  source {
    content  = file("${path.module}/../lambdas/paypay.py")
    filename = "paypay.py"
  }
  source {
    content  = file("${path.module}/../lambdas/square.py")
    filename = "square.py"
  }
}

data "archive_file" "seat_upgrade_webhook" {
  type        = "zip"
  output_path = "${path.module}/../lambdas/dist/seat_upgrade_webhook.zip"

  source {
    content  = file("${path.module}/../lambdas/seat_upgrade_webhook/handler.py")
    filename = "handler.py"
  }
  source {
    content  = file("${path.module}/../lambdas/seat_upgrade_logic.py")
    filename = "seat_upgrade_logic.py"
  }
  source {
    content  = file("${path.module}/../lambdas/seat_upgrade_emailer.py")
    filename = "seat_upgrade_emailer.py"
  }
  source {
    content  = file("${path.module}/../lambdas/paypay.py")
    filename = "paypay.py"
  }
  source {
    content  = file("${path.module}/../lambdas/square.py")
    filename = "square.py"
  }
}

# ── CloudWatch log groups ────────────────────────────────────────────────────

resource "aws_cloudwatch_log_group" "manage_seat_upgrades" {
  name              = "/aws/lambda/${var.name_prefix}-manage-seat-upgrades"
  retention_in_days = var.log_retention_days
}

resource "aws_cloudwatch_log_group" "seat_upgrade" {
  name              = "/aws/lambda/${var.name_prefix}-seat-upgrade"
  retention_in_days = var.log_retention_days
}

resource "aws_cloudwatch_log_group" "seat_upgrade_webhook" {
  name              = "/aws/lambda/${var.name_prefix}-seat-upgrade-webhook"
  retention_in_days = var.log_retention_days
}

# ── Lambda functions ─────────────────────────────────────────────────────────

resource "aws_lambda_function" "manage_seat_upgrades" {
  function_name    = "${var.name_prefix}-manage-seat-upgrades"
  role             = var.lambda_role_arn
  handler          = "handler.lambda_handler"
  runtime          = "python3.12"
  filename         = data.archive_file.manage_seat_upgrades.output_path
  source_code_hash = data.archive_file.manage_seat_upgrades.output_base64sha256
  timeout          = var.lambda_timeout
  memory_size      = var.lambda_memory

  environment {
    variables = merge(local.base_env, {
      ASSETS_BUCKET   = var.assets_bucket_name
      ASSETS_BASE_URL = var.assets_base_url
    })
  }

  depends_on = [aws_cloudwatch_log_group.manage_seat_upgrades, aws_iam_role_policy.seat_upgrade]
}

resource "aws_lambda_function" "seat_upgrade" {
  function_name    = "${var.name_prefix}-seat-upgrade"
  role             = var.lambda_role_arn
  handler          = "handler.lambda_handler"
  runtime          = "python3.12"
  filename         = data.archive_file.seat_upgrade.output_path
  source_code_hash = data.archive_file.seat_upgrade.output_base64sha256
  timeout          = var.lambda_timeout
  memory_size      = var.lambda_memory

  environment {
    variables = merge(local.base_env, local.payment_env, {
      OTP_TOKEN_SECRET                     = var.otp_token_secret
      CELEBRITY_DINNER_REGISTRATIONS_TABLE = var.celebrity_dinner_registrations_table_name
      CELEBRITY_DINNER_CONFIG_TABLE        = var.celebrity_dinner_config_table_name
    })
  }

  depends_on = [aws_cloudwatch_log_group.seat_upgrade, aws_iam_role_policy.seat_upgrade]
}

resource "aws_lambda_function" "seat_upgrade_webhook" {
  function_name    = "${var.name_prefix}-seat-upgrade-webhook"
  role             = var.lambda_role_arn
  handler          = "handler.lambda_handler"
  runtime          = "python3.12"
  filename         = data.archive_file.seat_upgrade_webhook.output_path
  source_code_hash = data.archive_file.seat_upgrade_webhook.output_base64sha256
  timeout          = var.lambda_timeout
  memory_size      = var.lambda_memory

  environment {
    variables = merge(local.base_env, local.payment_env)
  }

  depends_on = [aws_cloudwatch_log_group.seat_upgrade_webhook, aws_iam_role_policy.seat_upgrade]
}

# ── Lambda invoke permissions ────────────────────────────────────────────────

resource "aws_lambda_permission" "manage_seat_upgrades" {
  statement_id  = "AllowAPIGatewayInvokeManageSeatUpgrades"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.manage_seat_upgrades.function_name
  principal     = "apigateway.amazonaws.com"
  source_arn    = "${var.rest_api_execution_arn}/*/*"
}

resource "aws_lambda_permission" "seat_upgrade" {
  statement_id  = "AllowAPIGatewayInvokeSeatUpgrade"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.seat_upgrade.function_name
  principal     = "apigateway.amazonaws.com"
  source_arn    = "${var.rest_api_execution_arn}/*/*"
}

resource "aws_lambda_permission" "seat_upgrade_webhook" {
  statement_id  = "AllowAPIGatewayInvokeSeatUpgradeWebhook"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.seat_upgrade_webhook.function_name
  principal     = "apigateway.amazonaws.com"
  source_arn    = "${var.rest_api_execution_arn}/*/*"
}
