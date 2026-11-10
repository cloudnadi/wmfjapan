locals {
  lambda_env = {
    SEATS_TABLE                   = aws_dynamodb_table.seats.name
    SEAT_CATEGORY_OVERRIDES_TABLE = aws_dynamodb_table.category_overrides.name
    ZONE_PRICING_TABLE            = aws_dynamodb_table.zone_pricing.name
    REGION                        = var.aws_region
  }

  # manage_seats additionally handles admin purchase-management routes
  # (mark paid / confirm refund) — see manage_seats/handler.py — which send
  # the registrant a confirmation email (with a check-in QR once paid), so
  # it needs the same SES/frontend-URL config as the webhook below.
  # Both empty-string by default (see the matching variables' descriptions)
  # — the ticket PDF just omits those sections when unset.
  checkin_ticket_addon_env = {
    SEAT_UPGRADES_TABLE                  = var.seat_upgrades_table_name
    CELEBRITY_DINNER_REGISTRATIONS_TABLE = var.celebrity_dinner_registrations_table_name
  }

  manage_seats_env = merge(local.lambda_env, local.checkin_ticket_addon_env, {
    SEAT_PURCHASES_TABLE = aws_dynamodb_table.seat_purchases.name
    FRONTEND_URL         = var.frontend_url
    SES_FROM_EMAIL       = var.ses_from_email
    ADMIN_EMAIL          = var.admin_email
  })

  # The webhook is the only Lambda in this module that talks to a payment
  # provider or sends email directly (see seat_purchase_webhook/handler.py's
  # module docstring for why) — everything else here never needs these.
  seat_purchase_webhook_env = merge(local.lambda_env, local.checkin_ticket_addon_env, {
    SEAT_PURCHASES_TABLE         = aws_dynamodb_table.seat_purchases.name
    FRONTEND_URL                 = var.frontend_url
    SES_FROM_EMAIL               = var.ses_from_email
    ADMIN_EMAIL                  = var.admin_email
    PAYPAY_API_KEY               = var.paypay_api_key
    PAYPAY_API_SECRET            = var.paypay_api_secret
    PAYPAY_MERCHANT_ID           = var.paypay_merchant_id
    PAYPAY_ENV                   = var.paypay_env
    SQUARE_ACCESS_TOKEN          = var.square_access_token
    SQUARE_LOCATION_ID           = var.square_location_id
    SQUARE_WEBHOOK_SIGNATURE_KEY = var.square_webhook_signature_key
    SQUARE_WEBHOOK_URL           = var.square_webhook_url
    SQUARE_ENV                   = var.square_env
  })
}

data "archive_file" "get_available_seats" {
  type        = "zip"
  output_path = "${path.module}/../backend/dist/get_available_seats.zip"

  source {
    content  = file("${path.module}/../backend/get_available_seats/handler.py")
    filename = "handler.py"
  }

  source {
    content  = file("${path.module}/../backend/seat_selection_logic.py")
    filename = "seat_selection_logic.py"
  }
}

data "archive_file" "manage_seats" {
  type        = "zip"
  output_path = "${path.module}/../backend/dist/manage_seats.zip"

  source {
    content  = file("${path.module}/../backend/manage_seats/handler.py")
    filename = "handler.py"
  }

  source {
    content  = file("${path.module}/../backend/seat_selection_logic.py")
    filename = "seat_selection_logic.py"
  }

  source {
    content  = file("${path.module}/../backend/seat_purchase_logic.py")
    filename = "seat_purchase_logic.py"
  }

  source {
    content  = file("${path.module}/../backend/seat_purchase_emailer.py")
    filename = "seat_purchase_emailer.py"
  }

  source {
    content  = file("${path.module}/../backend/seat_purchase_qr.py")
    filename = "seat_purchase_qr.py"
  }

  source {
    content  = file("${path.module}/../backend/checkin_pdf.py")
    filename = "checkin_pdf.py"
  }
}

data "archive_file" "seat_purchase_webhook" {
  type        = "zip"
  output_path = "${path.module}/../backend/dist/seat_purchase_webhook.zip"

  source {
    content  = file("${path.module}/../backend/seat_purchase_webhook/handler.py")
    filename = "handler.py"
  }

  source {
    content  = file("${path.module}/../backend/seat_selection_logic.py")
    filename = "seat_selection_logic.py"
  }

  source {
    content  = file("${path.module}/../backend/seat_purchase_logic.py")
    filename = "seat_purchase_logic.py"
  }

  source {
    content  = file("${path.module}/../backend/seat_purchase_emailer.py")
    filename = "seat_purchase_emailer.py"
  }

  source {
    content  = file("${path.module}/../backend/seat_purchase_qr.py")
    filename = "seat_purchase_qr.py"
  }

  source {
    content  = file("${path.module}/../backend/checkin_pdf.py")
    filename = "checkin_pdf.py"
  }

  source {
    content  = file("${path.module}/../backend/paypay.py")
    filename = "paypay.py"
  }

  source {
    content  = file("${path.module}/../backend/square.py")
    filename = "square.py"
  }
}

resource "aws_cloudwatch_log_group" "get_available_seats" {
  name              = "/aws/lambda/${var.name_prefix}-seat-selection-get-available-seats"
  retention_in_days = var.log_retention_days
  tags              = var.tags
}

resource "aws_cloudwatch_log_group" "manage_seats" {
  name              = "/aws/lambda/${var.name_prefix}-seat-selection-manage-seats"
  retention_in_days = var.log_retention_days
  tags              = var.tags
}

resource "aws_cloudwatch_log_group" "seat_purchase_webhook" {
  name              = "/aws/lambda/${var.name_prefix}-seat-selection-purchase-webhook"
  retention_in_days = var.log_retention_days
  tags              = var.tags
}

resource "aws_lambda_function" "get_available_seats" {
  function_name    = "${var.name_prefix}-seat-selection-get-available-seats"
  role             = aws_iam_role.lambda_exec.arn
  handler          = "handler.lambda_handler"
  runtime          = var.lambda_runtime
  filename         = data.archive_file.get_available_seats.output_path
  source_code_hash = data.archive_file.get_available_seats.output_base64sha256
  timeout          = var.lambda_timeout
  memory_size      = var.lambda_memory

  environment {
    variables = local.lambda_env
  }

  tags = var.tags

  depends_on = [
    aws_cloudwatch_log_group.get_available_seats,
    aws_iam_role_policy.lambda_policy
  ]
}

resource "aws_lambda_function" "manage_seats" {
  function_name    = "${var.name_prefix}-seat-selection-manage-seats"
  role             = aws_iam_role.lambda_exec.arn
  handler          = "handler.lambda_handler"
  runtime          = var.lambda_runtime
  filename         = data.archive_file.manage_seats.output_path
  source_code_hash = data.archive_file.manage_seats.output_base64sha256
  timeout          = var.lambda_timeout
  memory_size      = var.lambda_memory
  layers           = var.qr_code_layer_arn != "" ? [var.qr_code_layer_arn] : []

  environment {
    variables = local.manage_seats_env
  }

  tags = var.tags

  depends_on = [
    aws_cloudwatch_log_group.manage_seats,
    aws_iam_role_policy.lambda_policy
  ]
}

resource "aws_lambda_function" "seat_purchase_webhook" {
  function_name    = "${var.name_prefix}-seat-selection-purchase-webhook"
  role             = aws_iam_role.lambda_exec.arn
  handler          = "handler.lambda_handler"
  runtime          = var.lambda_runtime
  filename         = data.archive_file.seat_purchase_webhook.output_path
  source_code_hash = data.archive_file.seat_purchase_webhook.output_base64sha256
  timeout          = var.lambda_timeout
  memory_size      = var.lambda_memory
  layers           = var.qr_code_layer_arn != "" ? [var.qr_code_layer_arn] : []

  environment {
    variables = local.seat_purchase_webhook_env
  }

  tags = var.tags

  depends_on = [
    aws_cloudwatch_log_group.seat_purchase_webhook,
    aws_iam_role_policy.lambda_policy
  ]
}
