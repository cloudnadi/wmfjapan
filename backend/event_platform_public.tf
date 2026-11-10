# ── Event platform: public API, Square webhook, hold-expiry job ──────────────
# /public/{proxy+}                          public pages + registration + member self-service (one Lambda)
# /webhooks/event-registrations/{provider}  Square payment webhook for event registrations
# EventBridge every 5 minutes               releases unpaid registrations whose hold ran out

locals {
  event_platform_shared_sources = {
    "event_model.py"         = "${path.module}/../lambdas/event_model.py"
    "event_pricing.py"       = "${path.module}/../lambdas/event_pricing.py"
    "event_registrations.py" = "${path.module}/../lambdas/event_registrations.py"
    "event_reg_store.py"     = "${path.module}/../lambdas/event_reg_store.py"
    "event_mailer.py"        = "${path.module}/../lambdas/event_mailer.py"
    "form_engine.py"         = "${path.module}/../lambdas/form_engine.py"
    "event_addons.py"        = "${path.module}/../lambdas/event_addons.py"
    "event_capabilities.py"  = "${path.module}/../lambdas/event_capabilities.py"
  }

  event_platform_runtime_env = merge(local.lambda_env, {
    EVENTS_V2_TABLE           = aws_dynamodb_table.events_v2.name
    EVENT_TICKET_TYPES_TABLE  = aws_dynamodb_table.event_ticket_types.name
    FORM_SCHEMAS_TABLE        = aws_dynamodb_table.form_schemas.name
    EVENT_REGISTRATIONS_TABLE = aws_dynamodb_table.event_registrations.name
    EVENT_COUNTERS_TABLE      = aws_dynamodb_table.event_counters.name
    ASSETS_BUCKET             = aws_s3_bucket.assets.id
    SQUARE_ACCESS_TOKEN       = var.square_access_token
    SQUARE_LOCATION_ID        = var.square_location_id
    SQUARE_ENV                = var.square_env
  })
}

# ── Lambdas ──────────────────────────────────────────────────────────────────

resource "aws_cloudwatch_log_group" "event_platform_public" {
  name              = "/aws/lambda/${local.name_prefix}-event-platform-public"
  retention_in_days = var.log_retention_days
}

resource "aws_cloudwatch_log_group" "event_platform_webhook" {
  name              = "/aws/lambda/${local.name_prefix}-event-platform-webhook"
  retention_in_days = var.log_retention_days
}

resource "aws_cloudwatch_log_group" "event_platform_expiry" {
  name              = "/aws/lambda/${local.name_prefix}-event-platform-expiry"
  retention_in_days = var.log_retention_days
}

data "archive_file" "event_platform_public" {
  type        = "zip"
  output_path = "${path.module}/../lambdas/dist/event_platform_public.zip"

  source {
    content  = file("${path.module}/../lambdas/event_platform_public/handler.py")
    filename = "handler.py"
  }

  dynamic "source" {
    for_each = local.event_platform_shared_sources
    content {
      content  = file(source.value)
      filename = source.key
    }
  }

  # Verifies attendee ID tokens (Cognito JWTs) with the standard library only.
  source {
    content  = file("${path.module}/../lambdas/cognito_jwt.py")
    filename = "cognito_jwt.py"
  }

  source {
    content  = file("${path.module}/../lambdas/square.py")
    filename = "square.py"
  }
}

data "archive_file" "event_platform_webhook" {
  type        = "zip"
  output_path = "${path.module}/../lambdas/dist/event_platform_webhook.zip"

  source {
    content  = file("${path.module}/../lambdas/event_platform_webhook/handler.py")
    filename = "handler.py"
  }

  dynamic "source" {
    for_each = local.event_platform_shared_sources
    content {
      content  = file(source.value)
      filename = source.key
    }
  }

  source {
    content  = file("${path.module}/../lambdas/square.py")
    filename = "square.py"
  }
}

data "archive_file" "event_platform_expiry" {
  type        = "zip"
  output_path = "${path.module}/../lambdas/dist/event_platform_expiry.zip"

  source {
    content  = file("${path.module}/../lambdas/event_platform_expiry/handler.py")
    filename = "handler.py"
  }

  dynamic "source" {
    for_each = local.event_platform_shared_sources
    content {
      content  = file(source.value)
      filename = source.key
    }
  }
}

resource "aws_lambda_function" "event_platform_public" {
  function_name    = "${local.name_prefix}-event-platform-public"
  role             = aws_iam_role.lambda_exec.arn
  handler          = "handler.lambda_handler"
  runtime          = "python3.12"
  filename         = data.archive_file.event_platform_public.output_path
  source_code_hash = data.archive_file.event_platform_public.output_base64sha256
  timeout          = var.lambda_timeout
  memory_size      = var.lambda_memory
  layers           = [aws_lambda_layer_version.qr_code.arn]

  environment {
    variables = merge(local.event_platform_runtime_env, {
      ATTENDEE_USER_POOL_ID   = aws_cognito_user_pool.attendees.id
      ATTENDEE_APP_CLIENT_ID  = aws_cognito_user_pool_client.attendees.id
      ATTENDEE_COGNITO_REGION = var.aws_region
    })
  }

  depends_on = [aws_cloudwatch_log_group.event_platform_public]
}

resource "aws_lambda_function" "event_platform_webhook" {
  function_name    = "${local.name_prefix}-event-platform-webhook"
  role             = aws_iam_role.lambda_exec.arn
  handler          = "handler.lambda_handler"
  runtime          = "python3.12"
  filename         = data.archive_file.event_platform_webhook.output_path
  source_code_hash = data.archive_file.event_platform_webhook.output_base64sha256
  timeout          = var.lambda_timeout
  memory_size      = var.lambda_memory
  layers           = [aws_lambda_layer_version.qr_code.arn]

  environment {
    variables = merge(local.event_platform_runtime_env, {
      SQUARE_WEBHOOK_SIGNATURE_KEY = var.square_events_webhook_signature_key
      SQUARE_WEBHOOK_URL           = var.square_events_webhook_url
    })
  }

  depends_on = [aws_cloudwatch_log_group.event_platform_webhook]
}

resource "aws_lambda_function" "event_platform_expiry" {
  function_name    = "${local.name_prefix}-event-platform-expiry"
  role             = aws_iam_role.lambda_exec.arn
  handler          = "handler.lambda_handler"
  runtime          = "python3.12"
  filename         = data.archive_file.event_platform_expiry.output_path
  source_code_hash = data.archive_file.event_platform_expiry.output_base64sha256
  timeout          = 120
  memory_size      = var.lambda_memory
  layers           = [aws_lambda_layer_version.qr_code.arn]

  environment {
    variables = local.event_platform_runtime_env
  }

  depends_on = [aws_cloudwatch_log_group.event_platform_expiry]
}

# ── Hold expiry schedule ─────────────────────────────────────────────────────

resource "aws_cloudwatch_event_rule" "event_platform_expiry" {
  name                = "${local.name_prefix}-event-platform-expiry"
  description         = "Release unpaid event registrations whose payment hold has expired"
  schedule_expression = "rate(5 minutes)"
}

resource "aws_cloudwatch_event_target" "event_platform_expiry" {
  rule = aws_cloudwatch_event_rule.event_platform_expiry.name
  arn  = aws_lambda_function.event_platform_expiry.arn
}

resource "aws_lambda_permission" "event_platform_expiry" {
  statement_id  = "AllowEventBridgeInvokeEventPlatformExpiry"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.event_platform_expiry.function_name
  principal     = "events.amazonaws.com"
  source_arn    = aws_cloudwatch_event_rule.event_platform_expiry.arn
}

# ── API: /public/{proxy+} ────────────────────────────────────────────────────

resource "aws_lambda_permission" "event_platform_public" {
  statement_id  = "AllowAPIGatewayInvokeEventPlatformPublic"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.event_platform_public.function_name
  principal     = "apigateway.amazonaws.com"
  source_arn    = "${aws_api_gateway_rest_api.main.execution_arn}/*/*"
}

resource "aws_api_gateway_resource" "public" {
  rest_api_id = aws_api_gateway_rest_api.main.id
  parent_id   = aws_api_gateway_rest_api.main.root_resource_id
  path_part   = "public"
}

resource "aws_api_gateway_resource" "public_proxy" {
  rest_api_id = aws_api_gateway_rest_api.main.id
  parent_id   = aws_api_gateway_resource.public.id
  path_part   = "{proxy+}"
}

# Authorization is NONE: guests may register where an event allows it. Member routes
# verify the registrant token inside the Lambda, like the other /my/* routes.
resource "aws_api_gateway_method" "public_any" {
  rest_api_id   = aws_api_gateway_rest_api.main.id
  resource_id   = aws_api_gateway_resource.public_proxy.id
  http_method   = "ANY"
  authorization = "NONE"
  request_parameters = {
    "method.request.path.proxy" = true
  }
}

resource "aws_api_gateway_integration" "public_any" {
  rest_api_id             = aws_api_gateway_rest_api.main.id
  resource_id             = aws_api_gateway_resource.public_proxy.id
  http_method             = aws_api_gateway_method.public_any.http_method
  integration_http_method = "POST"
  type                    = "AWS_PROXY"
  uri                     = aws_lambda_function.event_platform_public.invoke_arn
}

resource "aws_api_gateway_method" "public_options" {
  rest_api_id   = aws_api_gateway_rest_api.main.id
  resource_id   = aws_api_gateway_resource.public_proxy.id
  http_method   = "OPTIONS"
  authorization = "NONE"
}

resource "aws_api_gateway_integration" "public_options" {
  rest_api_id       = aws_api_gateway_rest_api.main.id
  resource_id       = aws_api_gateway_resource.public_proxy.id
  http_method       = aws_api_gateway_method.public_options.http_method
  type              = "MOCK"
  request_templates = { "application/json" = "{\"statusCode\": 200}" }
}

resource "aws_api_gateway_method_response" "public_options_200" {
  rest_api_id = aws_api_gateway_rest_api.main.id
  resource_id = aws_api_gateway_resource.public_proxy.id
  http_method = aws_api_gateway_method.public_options.http_method
  status_code = "200"
  response_parameters = {
    "method.response.header.Access-Control-Allow-Headers" = true
    "method.response.header.Access-Control-Allow-Methods" = true
    "method.response.header.Access-Control-Allow-Origin"  = true
  }
}

resource "aws_api_gateway_integration_response" "public_options" {
  rest_api_id         = aws_api_gateway_rest_api.main.id
  resource_id         = aws_api_gateway_resource.public_proxy.id
  http_method         = aws_api_gateway_method.public_options.http_method
  status_code         = aws_api_gateway_method_response.public_options_200.status_code
  response_parameters = local.cors_headers
}

# ── API: /webhooks/event-registrations/{provider} ────────────────────────────

resource "aws_api_gateway_resource" "webhooks_event_registrations" {
  rest_api_id = aws_api_gateway_rest_api.main.id
  parent_id   = aws_api_gateway_resource.webhooks.id
  path_part   = "event-registrations"
}

resource "aws_api_gateway_resource" "webhooks_event_registrations_provider" {
  rest_api_id = aws_api_gateway_rest_api.main.id
  parent_id   = aws_api_gateway_resource.webhooks_event_registrations.id
  path_part   = "{provider}"
}

resource "aws_api_gateway_method" "webhooks_event_registrations_post" {
  rest_api_id   = aws_api_gateway_rest_api.main.id
  resource_id   = aws_api_gateway_resource.webhooks_event_registrations_provider.id
  http_method   = "POST"
  authorization = "NONE"
  request_parameters = {
    "method.request.path.provider" = true
  }
}

resource "aws_api_gateway_integration" "webhooks_event_registrations_post" {
  rest_api_id             = aws_api_gateway_rest_api.main.id
  resource_id             = aws_api_gateway_resource.webhooks_event_registrations_provider.id
  http_method             = aws_api_gateway_method.webhooks_event_registrations_post.http_method
  integration_http_method = "POST"
  type                    = "AWS_PROXY"
  uri                     = aws_lambda_function.event_platform_webhook.invoke_arn
}

resource "aws_lambda_permission" "event_platform_webhook" {
  statement_id  = "AllowAPIGatewayInvokeEventPlatformWebhook"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.event_platform_webhook.function_name
  principal     = "apigateway.amazonaws.com"
  source_arn    = "${aws_api_gateway_rest_api.main.execution_arn}/*/*"
}
