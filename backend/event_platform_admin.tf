# ── Event platform: admin API ────────────────────────────────────────────────
# One Lambda behind a greedy proxy route, /admin/event-platform/{proxy+}. The
# Cognito authorizer proves the caller is a pool user; the Lambda then checks
# the Admins / EventManagers group.

resource "aws_cognito_user_group" "event_managers" {
  name         = "EventManagers"
  user_pool_id = aws_cognito_user_pool.admin.id
  description  = "Can manage events, forms and registrations (not payment settings)"
}

resource "aws_cloudwatch_log_group" "manage_event_platform" {
  name              = "/aws/lambda/${local.name_prefix}-manage-event-platform"
  retention_in_days = var.log_retention_days
}

data "archive_file" "manage_event_platform" {
  type        = "zip"
  output_path = "${path.module}/../lambdas/dist/manage_event_platform.zip"

  source {
    content  = file("${path.module}/../lambdas/manage_event_platform/handler.py")
    filename = "handler.py"
  }

  source {
    content  = file("${path.module}/../lambdas/manage_event_platform/store.py")
    filename = "store.py"
  }

  source {
    content  = file("${path.module}/../lambdas/admin_roles.py")
    filename = "admin_roles.py"
  }

  # event_model, form_engine, pricing, registrations logic/store, mailer, add-ons (shared with the public Lambda)
  dynamic "source" {
    for_each = local.event_platform_shared_sources
    content {
      content  = file(source.value)
      filename = source.key
    }
  }

  source {
    content  = file("${path.module}/../lambdas/event_exports.py")
    filename = "event_exports.py"
  }

  source {
    content  = file("${path.module}/../lambdas/event_registration_admin.py")
    filename = "event_registration_admin.py"
  }


  source {
    content  = file("${path.module}/../lambdas/square.py")
    filename = "square.py"
  }
}

resource "aws_lambda_function" "manage_event_platform" {
  function_name    = "${local.name_prefix}-manage-event-platform"
  role             = aws_iam_role.lambda_exec.arn
  handler          = "handler.lambda_handler"
  runtime          = "python3.12"
  filename         = data.archive_file.manage_event_platform.output_path
  source_code_hash = data.archive_file.manage_event_platform.output_base64sha256
  timeout          = 60
  memory_size      = var.lambda_memory
  layers           = [aws_lambda_layer_version.qr_code.arn]

  environment {
    variables = merge(local.lambda_env, {
      SQUARE_ACCESS_TOKEN       = var.square_access_token
      SQUARE_LOCATION_ID        = var.square_location_id
      SQUARE_ENV                = var.square_env
      EVENTS_V2_TABLE           = aws_dynamodb_table.events_v2.name
      EVENT_TICKET_TYPES_TABLE  = aws_dynamodb_table.event_ticket_types.name
      FORM_SCHEMAS_TABLE        = aws_dynamodb_table.form_schemas.name
      EVENT_REGISTRATIONS_TABLE = aws_dynamodb_table.event_registrations.name
      EVENT_COUNTERS_TABLE      = aws_dynamodb_table.event_counters.name
      AUDIT_LOG_TABLE           = aws_dynamodb_table.audit_log.name
      ASSETS_BUCKET             = aws_s3_bucket.assets.id
      ASSETS_BASE_URL           = local.assets_base_url
    })
  }

  depends_on = [aws_cloudwatch_log_group.manage_event_platform]
}

resource "aws_lambda_permission" "manage_event_platform" {
  statement_id  = "AllowAPIGatewayInvokeEventPlatform"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.manage_event_platform.function_name
  principal     = "apigateway.amazonaws.com"
  source_arn    = "${aws_api_gateway_rest_api.main.execution_arn}/*/*"
}

resource "aws_api_gateway_resource" "admin_event_platform" {
  rest_api_id = aws_api_gateway_rest_api.main.id
  parent_id   = aws_api_gateway_resource.admin.id
  path_part   = "event-platform"
}

resource "aws_api_gateway_resource" "admin_event_platform_proxy" {
  rest_api_id = aws_api_gateway_rest_api.main.id
  parent_id   = aws_api_gateway_resource.admin_event_platform.id
  path_part   = "{proxy+}"
}

resource "aws_api_gateway_method" "admin_event_platform_any" {
  rest_api_id   = aws_api_gateway_rest_api.main.id
  resource_id   = aws_api_gateway_resource.admin_event_platform_proxy.id
  http_method   = "ANY"
  authorization = "COGNITO_USER_POOLS"
  authorizer_id = aws_api_gateway_authorizer.cognito.id
  request_parameters = {
    "method.request.path.proxy" = true
  }
}

resource "aws_api_gateway_integration" "admin_event_platform_any" {
  rest_api_id             = aws_api_gateway_rest_api.main.id
  resource_id             = aws_api_gateway_resource.admin_event_platform_proxy.id
  http_method             = aws_api_gateway_method.admin_event_platform_any.http_method
  integration_http_method = "POST"
  type                    = "AWS_PROXY"
  uri                     = aws_lambda_function.manage_event_platform.invoke_arn
}

resource "aws_api_gateway_method" "admin_event_platform_options" {
  rest_api_id   = aws_api_gateway_rest_api.main.id
  resource_id   = aws_api_gateway_resource.admin_event_platform_proxy.id
  http_method   = "OPTIONS"
  authorization = "NONE"
}

resource "aws_api_gateway_integration" "admin_event_platform_options" {
  rest_api_id       = aws_api_gateway_rest_api.main.id
  resource_id       = aws_api_gateway_resource.admin_event_platform_proxy.id
  http_method       = aws_api_gateway_method.admin_event_platform_options.http_method
  type              = "MOCK"
  request_templates = { "application/json" = "{\"statusCode\": 200}" }
}

resource "aws_api_gateway_method_response" "admin_event_platform_options_200" {
  rest_api_id = aws_api_gateway_rest_api.main.id
  resource_id = aws_api_gateway_resource.admin_event_platform_proxy.id
  http_method = aws_api_gateway_method.admin_event_platform_options.http_method
  status_code = "200"
  response_parameters = {
    "method.response.header.Access-Control-Allow-Headers" = true
    "method.response.header.Access-Control-Allow-Methods" = true
    "method.response.header.Access-Control-Allow-Origin"  = true
  }
}

resource "aws_api_gateway_integration_response" "admin_event_platform_options" {
  rest_api_id         = aws_api_gateway_rest_api.main.id
  resource_id         = aws_api_gateway_resource.admin_event_platform_proxy.id
  http_method         = aws_api_gateway_method.admin_event_platform_options.http_method
  status_code         = aws_api_gateway_method_response.admin_event_platform_options_200.status_code
  response_parameters = local.cors_headers
}
