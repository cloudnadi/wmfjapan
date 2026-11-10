# ── REST API ─────────────────────────────────────────────────────────────────
# Three route groups, each backed by one Lambda (see event_platform_*.tf):
#   /public/{proxy+}                          attendees and guests (auth checked in the Lambda)
#   /admin/event-platform/{proxy+}            admins / event managers (Cognito authorizer)
#   /webhooks/event-registrations/{provider}  Square payment webhook

resource "aws_api_gateway_rest_api" "main" {
  name        = "${local.name_prefix}-api"
  description = "API Gateway for the ${var.project_name} event platform"

  endpoint_configuration {
    types = ["REGIONAL"]
  }
}

# ── Admin Cognito authorizer ─────────────────────────────────────────────────

resource "aws_api_gateway_authorizer" "cognito" {
  name                             = "CognitoAuthorizer"
  rest_api_id                      = aws_api_gateway_rest_api.main.id
  type                             = "COGNITO_USER_POOLS"
  provider_arns                    = [aws_cognito_user_pool.admin.arn]
  identity_source                  = "method.request.header.Authorization"
  authorizer_result_ttl_in_seconds = 300
}

# ── CORS ─────────────────────────────────────────────────────────────────────

locals {
  cors_headers = {
    "method.response.header.Access-Control-Allow-Headers" = "'Content-Type,X-Amz-Date,Authorization,X-Api-Key'"
    "method.response.header.Access-Control-Allow-Methods" = "'OPTIONS,POST,PUT,PATCH,GET,DELETE'"
    "method.response.header.Access-Control-Allow-Origin"  = "'${var.allowed_origins}'"
  }

  gateway_cors_headers = {
    "gatewayresponse.header.Access-Control-Allow-Headers" = "'Content-Type,X-Amz-Date,Authorization,X-Api-Key'"
    "gatewayresponse.header.Access-Control-Allow-Methods" = "'OPTIONS,POST,PUT,PATCH,GET,DELETE'"
    "gatewayresponse.header.Access-Control-Allow-Origin"  = "'${var.allowed_origins}'"
  }
}

resource "aws_api_gateway_gateway_response" "default_4xx" {
  rest_api_id   = aws_api_gateway_rest_api.main.id
  response_type = "DEFAULT_4XX"

  response_parameters = local.gateway_cors_headers
}

resource "aws_api_gateway_gateway_response" "default_5xx" {
  rest_api_id   = aws_api_gateway_rest_api.main.id
  response_type = "DEFAULT_5XX"

  response_parameters = local.gateway_cors_headers
}

resource "aws_api_gateway_gateway_response" "unauthorized" {
  rest_api_id   = aws_api_gateway_rest_api.main.id
  response_type = "UNAUTHORIZED"

  response_parameters = local.gateway_cors_headers
}

resource "aws_api_gateway_gateway_response" "access_denied" {
  rest_api_id   = aws_api_gateway_rest_api.main.id
  response_type = "ACCESS_DENIED"

  response_parameters = local.gateway_cors_headers
}

# ── Shared mount points ──────────────────────────────────────────────────────

resource "aws_api_gateway_resource" "webhooks" {
  rest_api_id = aws_api_gateway_rest_api.main.id
  parent_id   = aws_api_gateway_rest_api.main.root_resource_id
  path_part   = "webhooks"
}

resource "aws_api_gateway_resource" "admin" {
  rest_api_id = aws_api_gateway_rest_api.main.id
  parent_id   = aws_api_gateway_rest_api.main.root_resource_id
  path_part   = "admin"
}

# ── Deployment & stage ───────────────────────────────────────────────────────

resource "aws_api_gateway_deployment" "main" {
  rest_api_id = aws_api_gateway_rest_api.main.id

  triggers = {
    # Redeploy whenever the API definition changes. Hashing the source files (known at plan time) instead of
    # resource objects avoids "inconsistent final plan": object attributes such as ids are only known at apply.
    redeployment = sha1(join("", [
      filesha1("${path.module}/api_gateway.tf"),
      filesha1("${path.module}/event_platform_public.tf"),
      filesha1("${path.module}/event_platform_admin.tf"),
    ]))
  }

  lifecycle {
    create_before_destroy = true
  }

  depends_on = [
    aws_api_gateway_integration.admin_event_platform_any,
    aws_api_gateway_integration.admin_event_platform_options,
    aws_api_gateway_integration.public_any,
    aws_api_gateway_integration.public_options,
    aws_api_gateway_integration.webhooks_event_registrations_post,
  ]
}

resource "aws_api_gateway_stage" "main" {
  rest_api_id   = aws_api_gateway_rest_api.main.id
  deployment_id = aws_api_gateway_deployment.main.id
  stage_name    = var.environment

  access_log_settings {
    destination_arn = aws_cloudwatch_log_group.api_gateway.arn
    format = jsonencode({
      requestId      = "$context.requestId"
      ip             = "$context.identity.sourceIp"
      requestTime    = "$context.requestTime"
      httpMethod     = "$context.httpMethod"
      resourcePath   = "$context.resourcePath"
      status         = "$context.status"
      protocol       = "$context.protocol"
      responseLength = "$context.responseLength"
      errorMessage   = "$context.error.message"
    })
  }

  depends_on = [aws_cloudwatch_log_group.api_gateway]
}

# ── Usage plan (basic throttling) ────────────────────────────────────────────

resource "aws_api_gateway_usage_plan" "main" {
  name = "${local.name_prefix}-usage-plan"

  api_stages {
    api_id = aws_api_gateway_rest_api.main.id
    stage  = aws_api_gateway_stage.main.stage_name
  }

  throttle_settings {
    burst_limit = 100
    rate_limit  = 50
  }

  quota_settings {
    limit  = 50000
    period = "DAY"
  }
}
