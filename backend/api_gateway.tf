# ── REST API ─────────────────────────────────────────────────────────────────

resource "aws_api_gateway_rest_api" "main" {
  name        = "${local.name_prefix}-api"
  description = "API Gateway for ${var.project_name} registration system"

  endpoint_configuration {
    types = ["REGIONAL"]
  }
}

# ── Cognito Authorizer ───────────────────────────────────────────────────────

resource "aws_api_gateway_authorizer" "cognito" {
  name                             = "CognitoAuthorizer"
  rest_api_id                      = aws_api_gateway_rest_api.main.id
  type                             = "COGNITO_USER_POOLS"
  provider_arns                    = [aws_cognito_user_pool.admin.arn]
  identity_source                  = "method.request.header.Authorization"
  authorizer_result_ttl_in_seconds = 300
}

# ── CORS helper module ───────────────────────────────────────────────────────
# We define CORS options inline for each resource below.

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

# ─────────────────────────────────────────────────────────────────────────────
# /webhooks  — shared mount point for every plugin's payment-provider webhook
#
# Owned by the host, not any one plugin: a plugin mounts its own
# {plugin-name}/{provider} routes as a child of this resource (passed in as
# `webhooks_resource_id`, same convention as admin_event_item_resource_id /
# my_resource_id in plugin.tf) instead of creating its own top-level
# "webhooks" resource. Two plugins each independently declaring
# `path_part = "webhooks"` under the API root is a genuine conflict, not
# just a preview-environment timing issue — AWS rejects a second sibling
# resource with the same name under the same parent regardless of which
# Terraform module "owns" it, so only one resource may exist here.
# ─────────────────────────────────────────────────────────────────────────────

resource "aws_api_gateway_resource" "webhooks" {
  rest_api_id = aws_api_gateway_rest_api.main.id
  parent_id   = aws_api_gateway_rest_api.main.root_resource_id
  path_part   = "webhooks"
}

# Re-addresses the existing state object instead of destroy+create: without
# this, Terraform has no dependency edge telling it the plugin's old
# `webhooks` resource must be destroyed before this one is created, so it can
# try to create this one first and hit the same "duplicate name under the
# same parent" conflict this whole file exists to avoid.
moved {
  from = module.seat_upgrade.aws_api_gateway_resource.webhooks
  to   = aws_api_gateway_resource.webhooks
}

# ─────────────────────────────────────────────────────────────────────────────
# /webhooks/celebrity-dinner/{provider}  (public) — payment-provider callback
# for the standalone Celebrity Dinner sign-up (celebrity_dinner_webhook
# Lambda). Mounted under the shared /webhooks resource above, a sibling of
# the seat-upgrade plugin's own /webhooks/seat-upgrade/{provider} — same
# convention, host-owned rather than each feature creating its own
# top-level "webhooks" resource.
# ─────────────────────────────────────────────────────────────────────────────

resource "aws_api_gateway_resource" "webhooks_celebrity_dinner" {
  rest_api_id = aws_api_gateway_rest_api.main.id
  parent_id   = aws_api_gateway_resource.webhooks.id
  path_part   = "celebrity-dinner"
}

resource "aws_api_gateway_resource" "webhooks_celebrity_dinner_provider" {
  rest_api_id = aws_api_gateway_rest_api.main.id
  parent_id   = aws_api_gateway_resource.webhooks_celebrity_dinner.id
  path_part   = "{provider}"
}

resource "aws_api_gateway_method" "webhooks_celebrity_dinner_post" {
  rest_api_id   = aws_api_gateway_rest_api.main.id
  resource_id   = aws_api_gateway_resource.webhooks_celebrity_dinner_provider.id
  http_method   = "POST"
  authorization = "NONE"
  request_parameters = {
    "method.request.path.provider" = true
  }
}

resource "aws_api_gateway_integration" "webhooks_celebrity_dinner_post" {
  rest_api_id             = aws_api_gateway_rest_api.main.id
  resource_id             = aws_api_gateway_resource.webhooks_celebrity_dinner_provider.id
  http_method             = aws_api_gateway_method.webhooks_celebrity_dinner_post.http_method
  integration_http_method = "POST"
  type                    = "AWS_PROXY"
  uri                     = aws_lambda_function.celebrity_dinner_webhook.invoke_arn
}

resource "aws_lambda_permission" "celebrity_dinner_webhook" {
  statement_id  = "AllowAPIGatewayInvokeCelebrityDinnerWebhook"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.celebrity_dinner_webhook.function_name
  principal     = "apigateway.amazonaws.com"
  source_arn    = "${aws_api_gateway_rest_api.main.execution_arn}/*/*"
}

# /webhooks/npo-membership/{provider}  (public) — Square callback for NPO
# membership payments made from My Page.
resource "aws_api_gateway_resource" "webhooks_npo_membership" {
  rest_api_id = aws_api_gateway_rest_api.main.id
  parent_id   = aws_api_gateway_resource.webhooks.id
  path_part   = "npo-membership"
}

resource "aws_api_gateway_resource" "webhooks_npo_membership_provider" {
  rest_api_id = aws_api_gateway_rest_api.main.id
  parent_id   = aws_api_gateway_resource.webhooks_npo_membership.id
  path_part   = "{provider}"
}

resource "aws_api_gateway_method" "webhooks_npo_membership_post" {
  rest_api_id   = aws_api_gateway_rest_api.main.id
  resource_id   = aws_api_gateway_resource.webhooks_npo_membership_provider.id
  http_method   = "POST"
  authorization = "NONE"
  request_parameters = {
    "method.request.path.provider" = true
  }
}

resource "aws_api_gateway_integration" "webhooks_npo_membership_post" {
  rest_api_id             = aws_api_gateway_rest_api.main.id
  resource_id             = aws_api_gateway_resource.webhooks_npo_membership_provider.id
  http_method             = aws_api_gateway_method.webhooks_npo_membership_post.http_method
  integration_http_method = "POST"
  type                    = "AWS_PROXY"
  uri                     = aws_lambda_function.npo_membership_webhook.invoke_arn
}

resource "aws_lambda_permission" "npo_membership_webhook" {
  statement_id  = "AllowAPIGatewayInvokeNpoMembershipWebhook"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.npo_membership_webhook.function_name
  principal     = "apigateway.amazonaws.com"
  source_arn    = "${aws_api_gateway_rest_api.main.execution_arn}/*/*"
}

# ─────────────────────────────────────────────────────────────────────────────
# /register  (public)
# ─────────────────────────────────────────────────────────────────────────────

resource "aws_api_gateway_resource" "register" {
  rest_api_id = aws_api_gateway_rest_api.main.id
  parent_id   = aws_api_gateway_rest_api.main.root_resource_id
  path_part   = "register"
}

# POST /register
resource "aws_api_gateway_method" "register_post" {
  rest_api_id   = aws_api_gateway_rest_api.main.id
  resource_id   = aws_api_gateway_resource.register.id
  http_method   = "POST"
  authorization = "NONE"
}

resource "aws_api_gateway_integration" "register_post" {
  rest_api_id             = aws_api_gateway_rest_api.main.id
  resource_id             = aws_api_gateway_resource.register.id
  http_method             = aws_api_gateway_method.register_post.http_method
  integration_http_method = "POST"
  type                    = "AWS_PROXY"
  uri                     = aws_lambda_function.register.invoke_arn
}

resource "aws_lambda_permission" "register" {
  statement_id  = "AllowAPIGatewayInvoke"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.register.function_name
  principal     = "apigateway.amazonaws.com"
  source_arn    = "${aws_api_gateway_rest_api.main.execution_arn}/*/*"
}

# OPTIONS /register (CORS preflight)
resource "aws_api_gateway_method" "register_options" {
  rest_api_id   = aws_api_gateway_rest_api.main.id
  resource_id   = aws_api_gateway_resource.register.id
  http_method   = "OPTIONS"
  authorization = "NONE"
}

resource "aws_api_gateway_integration" "register_options" {
  rest_api_id       = aws_api_gateway_rest_api.main.id
  resource_id       = aws_api_gateway_resource.register.id
  http_method       = aws_api_gateway_method.register_options.http_method
  type              = "MOCK"
  request_templates = { "application/json" = "{\"statusCode\": 200}" }
}

resource "aws_api_gateway_method_response" "register_options_200" {
  rest_api_id = aws_api_gateway_rest_api.main.id
  resource_id = aws_api_gateway_resource.register.id
  http_method = aws_api_gateway_method.register_options.http_method
  status_code = "200"
  response_parameters = {
    "method.response.header.Access-Control-Allow-Headers" = true
    "method.response.header.Access-Control-Allow-Methods" = true
    "method.response.header.Access-Control-Allow-Origin"  = true
  }
}

resource "aws_api_gateway_integration_response" "register_options" {
  rest_api_id         = aws_api_gateway_rest_api.main.id
  resource_id         = aws_api_gateway_resource.register.id
  http_method         = aws_api_gateway_method.register_options.http_method
  status_code         = aws_api_gateway_method_response.register_options_200.status_code
  response_parameters = local.cors_headers
}

# ─────────────────────────────────────────────────────────────────────────────
# /registrations/available-slots  (public)
resource "aws_api_gateway_resource" "registrations" {
  rest_api_id = aws_api_gateway_rest_api.main.id
  parent_id   = aws_api_gateway_rest_api.main.root_resource_id
  path_part   = "registrations"
}

# ─────────────────────────────────────────────────────────────────────────────

resource "aws_api_gateway_resource" "available_slots" {
  rest_api_id = aws_api_gateway_rest_api.main.id
  parent_id   = aws_api_gateway_resource.registrations.id
  path_part   = "available-slots"
}

resource "aws_lambda_permission" "get_available_slots" {
  statement_id  = "AllowAPIGatewayInvokeGetAvailableSlots"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.get_available_slots.function_name
  principal     = "apigateway.amazonaws.com"
  source_arn    = "${aws_api_gateway_rest_api.main.execution_arn}/*/*"
}

# GET /registrations/available-slots
resource "aws_api_gateway_method" "available_slots_get" {
  rest_api_id   = aws_api_gateway_rest_api.main.id
  resource_id   = aws_api_gateway_resource.available_slots.id
  http_method   = "GET"
  authorization = "NONE"
  request_parameters = {
    "method.request.querystring.eventType"     = true
    "method.request.querystring.numberOfSeats" = false
  }
}

resource "aws_api_gateway_integration" "available_slots_get" {
  rest_api_id             = aws_api_gateway_rest_api.main.id
  resource_id             = aws_api_gateway_resource.available_slots.id
  http_method             = aws_api_gateway_method.available_slots_get.http_method
  integration_http_method = "POST"
  type                    = "AWS_PROXY"
  uri                     = aws_lambda_function.get_available_slots.invoke_arn
}

# OPTIONS /registrations/available-slots
resource "aws_api_gateway_method" "available_slots_options" {
  rest_api_id   = aws_api_gateway_rest_api.main.id
  resource_id   = aws_api_gateway_resource.available_slots.id
  http_method   = "OPTIONS"
  authorization = "NONE"
}

resource "aws_api_gateway_integration" "available_slots_options" {
  rest_api_id       = aws_api_gateway_rest_api.main.id
  resource_id       = aws_api_gateway_resource.available_slots.id
  http_method       = aws_api_gateway_method.available_slots_options.http_method
  type              = "MOCK"
  request_templates = { "application/json" = "{\"statusCode\": 200}" }
}

resource "aws_api_gateway_method_response" "available_slots_options_200" {
  rest_api_id = aws_api_gateway_rest_api.main.id
  resource_id = aws_api_gateway_resource.available_slots.id
  http_method = aws_api_gateway_method.available_slots_options.http_method
  status_code = "200"
  response_parameters = {
    "method.response.header.Access-Control-Allow-Headers" = true
    "method.response.header.Access-Control-Allow-Methods" = true
    "method.response.header.Access-Control-Allow-Origin"  = true
  }
}

resource "aws_api_gateway_integration_response" "available_slots_options" {
  rest_api_id         = aws_api_gateway_rest_api.main.id
  resource_id         = aws_api_gateway_resource.available_slots.id
  http_method         = aws_api_gateway_method.available_slots_options.http_method
  status_code         = aws_api_gateway_method_response.available_slots_options_200.status_code
  response_parameters = local.cors_headers
}

# ─────────────────────────────────────────────────────────────────────────────
# /confirm  (public — email confirmation click)
# ─────────────────────────────────────────────────────────────────────────────

resource "aws_api_gateway_resource" "confirm" {
  rest_api_id = aws_api_gateway_rest_api.main.id
  parent_id   = aws_api_gateway_rest_api.main.root_resource_id
  path_part   = "confirm"
}

resource "aws_api_gateway_method" "confirm_get" {
  rest_api_id   = aws_api_gateway_rest_api.main.id
  resource_id   = aws_api_gateway_resource.confirm.id
  http_method   = "GET"
  authorization = "NONE"
  request_parameters = {
    "method.request.querystring.token" = true
  }
}

resource "aws_api_gateway_integration" "confirm_get" {
  rest_api_id             = aws_api_gateway_rest_api.main.id
  resource_id             = aws_api_gateway_resource.confirm.id
  http_method             = aws_api_gateway_method.confirm_get.http_method
  integration_http_method = "POST"
  type                    = "AWS_PROXY"
  uri                     = aws_lambda_function.confirm_email.invoke_arn
}

resource "aws_lambda_permission" "confirm" {
  statement_id  = "AllowAPIGatewayInvoke"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.confirm_email.function_name
  principal     = "apigateway.amazonaws.com"
  source_arn    = "${aws_api_gateway_rest_api.main.execution_arn}/*/*"
}

# ─────────────────────────────────────────────────────────────────────────────
# /visits  (public — SPA page-view logging)
# ─────────────────────────────────────────────────────────────────────────────

resource "aws_api_gateway_resource" "visits" {
  rest_api_id = aws_api_gateway_rest_api.main.id
  parent_id   = aws_api_gateway_rest_api.main.root_resource_id
  path_part   = "visits"
}

resource "aws_api_gateway_method" "visits_post" {
  rest_api_id   = aws_api_gateway_rest_api.main.id
  resource_id   = aws_api_gateway_resource.visits.id
  http_method   = "POST"
  authorization = "NONE"
}

resource "aws_api_gateway_integration" "visits_post" {
  rest_api_id             = aws_api_gateway_rest_api.main.id
  resource_id             = aws_api_gateway_resource.visits.id
  http_method             = aws_api_gateway_method.visits_post.http_method
  integration_http_method = "POST"
  type                    = "AWS_PROXY"
  uri                     = aws_lambda_function.visit_logger.invoke_arn
}

resource "aws_lambda_permission" "visit_logger" {
  statement_id  = "AllowAPIGatewayInvokeVisits"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.visit_logger.function_name
  principal     = "apigateway.amazonaws.com"
  source_arn    = "${aws_api_gateway_rest_api.main.execution_arn}/*/*"
}

resource "aws_api_gateway_method" "visits_options" {
  rest_api_id   = aws_api_gateway_rest_api.main.id
  resource_id   = aws_api_gateway_resource.visits.id
  http_method   = "OPTIONS"
  authorization = "NONE"
}

resource "aws_api_gateway_integration" "visits_options" {
  rest_api_id       = aws_api_gateway_rest_api.main.id
  resource_id       = aws_api_gateway_resource.visits.id
  http_method       = aws_api_gateway_method.visits_options.http_method
  type              = "MOCK"
  request_templates = { "application/json" = "{\"statusCode\": 200}" }
}

resource "aws_api_gateway_method_response" "visits_options_200" {
  rest_api_id = aws_api_gateway_rest_api.main.id
  resource_id = aws_api_gateway_resource.visits.id
  http_method = aws_api_gateway_method.visits_options.http_method
  status_code = "200"
  response_parameters = {
    "method.response.header.Access-Control-Allow-Headers" = true
    "method.response.header.Access-Control-Allow-Methods" = true
    "method.response.header.Access-Control-Allow-Origin"  = true
  }
}

resource "aws_api_gateway_integration_response" "visits_options" {
  rest_api_id         = aws_api_gateway_rest_api.main.id
  resource_id         = aws_api_gateway_resource.visits.id
  http_method         = aws_api_gateway_method.visits_options.http_method
  status_code         = aws_api_gateway_method_response.visits_options_200.status_code
  response_parameters = local.cors_headers
}

# ─────────────────────────────────────────────────────────────────────────────
# /admin  (protected)
# ─────────────────────────────────────────────────────────────────────────────

resource "aws_api_gateway_resource" "admin" {
  rest_api_id = aws_api_gateway_rest_api.main.id
  parent_id   = aws_api_gateway_rest_api.main.root_resource_id
  path_part   = "admin"
}

# /admin/registrations
resource "aws_api_gateway_resource" "admin_registrations" {
  rest_api_id = aws_api_gateway_rest_api.main.id
  parent_id   = aws_api_gateway_resource.admin.id
  path_part   = "registrations"
}

resource "aws_api_gateway_method" "admin_registrations_get" {
  rest_api_id   = aws_api_gateway_rest_api.main.id
  resource_id   = aws_api_gateway_resource.admin_registrations.id
  http_method   = "GET"
  authorization = "COGNITO_USER_POOLS"
  authorizer_id = aws_api_gateway_authorizer.cognito.id
  request_parameters = {
    "method.request.querystring.limit"            = false
    "method.request.querystring.lastEvaluatedKey" = false
    "method.request.querystring.search"           = false
  }
}

resource "aws_api_gateway_integration" "admin_registrations_get" {
  rest_api_id             = aws_api_gateway_rest_api.main.id
  resource_id             = aws_api_gateway_resource.admin_registrations.id
  http_method             = aws_api_gateway_method.admin_registrations_get.http_method
  integration_http_method = "POST"
  type                    = "AWS_PROXY"
  uri                     = aws_lambda_function.get_registrations.invoke_arn
}

resource "aws_lambda_permission" "get_registrations" {
  statement_id  = "AllowAPIGatewayInvoke"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.get_registrations.function_name
  principal     = "apigateway.amazonaws.com"
  source_arn    = "${aws_api_gateway_rest_api.main.execution_arn}/*/*"
}

# OPTIONS /admin/registrations
resource "aws_api_gateway_method" "admin_registrations_options" {
  rest_api_id   = aws_api_gateway_rest_api.main.id
  resource_id   = aws_api_gateway_resource.admin_registrations.id
  http_method   = "OPTIONS"
  authorization = "NONE"
}

resource "aws_api_gateway_integration" "admin_registrations_options" {
  rest_api_id       = aws_api_gateway_rest_api.main.id
  resource_id       = aws_api_gateway_resource.admin_registrations.id
  http_method       = aws_api_gateway_method.admin_registrations_options.http_method
  type              = "MOCK"
  request_templates = { "application/json" = "{\"statusCode\": 200}" }
}

resource "aws_api_gateway_method_response" "admin_registrations_options_200" {
  rest_api_id = aws_api_gateway_rest_api.main.id
  resource_id = aws_api_gateway_resource.admin_registrations.id
  http_method = aws_api_gateway_method.admin_registrations_options.http_method
  status_code = "200"
  response_parameters = {
    "method.response.header.Access-Control-Allow-Headers" = true
    "method.response.header.Access-Control-Allow-Methods" = true
    "method.response.header.Access-Control-Allow-Origin"  = true
  }
}

resource "aws_api_gateway_integration_response" "admin_registrations_options" {
  rest_api_id         = aws_api_gateway_rest_api.main.id
  resource_id         = aws_api_gateway_resource.admin_registrations.id
  http_method         = aws_api_gateway_method.admin_registrations_options.http_method
  status_code         = aws_api_gateway_method_response.admin_registrations_options_200.status_code
  response_parameters = local.cors_headers
}

# /admin/export
resource "aws_api_gateway_resource" "admin_export" {
  rest_api_id = aws_api_gateway_rest_api.main.id
  parent_id   = aws_api_gateway_resource.admin.id
  path_part   = "export"
}

resource "aws_api_gateway_method" "admin_export_get" {
  rest_api_id   = aws_api_gateway_rest_api.main.id
  resource_id   = aws_api_gateway_resource.admin_export.id
  http_method   = "GET"
  authorization = "COGNITO_USER_POOLS"
  authorizer_id = aws_api_gateway_authorizer.cognito.id
}

resource "aws_api_gateway_integration" "admin_export_get" {
  rest_api_id             = aws_api_gateway_rest_api.main.id
  resource_id             = aws_api_gateway_resource.admin_export.id
  http_method             = aws_api_gateway_method.admin_export_get.http_method
  integration_http_method = "POST"
  type                    = "AWS_PROXY"
  uri                     = aws_lambda_function.export_csv.invoke_arn
}

resource "aws_lambda_permission" "export_csv" {
  statement_id  = "AllowAPIGatewayInvoke"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.export_csv.function_name
  principal     = "apigateway.amazonaws.com"
  source_arn    = "${aws_api_gateway_rest_api.main.execution_arn}/*/*"
}

# OPTIONS /admin/export (CORS preflight)
resource "aws_api_gateway_method" "admin_export_options" {
  rest_api_id   = aws_api_gateway_rest_api.main.id
  resource_id   = aws_api_gateway_resource.admin_export.id
  http_method   = "OPTIONS"
  authorization = "NONE"
}

resource "aws_api_gateway_integration" "admin_export_options" {
  rest_api_id       = aws_api_gateway_rest_api.main.id
  resource_id       = aws_api_gateway_resource.admin_export.id
  http_method       = aws_api_gateway_method.admin_export_options.http_method
  type              = "MOCK"
  request_templates = { "application/json" = "{\"statusCode\": 200}" }
}

resource "aws_api_gateway_method_response" "admin_export_options_200" {
  rest_api_id = aws_api_gateway_rest_api.main.id
  resource_id = aws_api_gateway_resource.admin_export.id
  http_method = aws_api_gateway_method.admin_export_options.http_method
  status_code = "200"
  response_parameters = {
    "method.response.header.Access-Control-Allow-Headers" = true
    "method.response.header.Access-Control-Allow-Methods" = true
    "method.response.header.Access-Control-Allow-Origin"  = true
  }
}

resource "aws_api_gateway_integration_response" "admin_export_options" {
  rest_api_id         = aws_api_gateway_rest_api.main.id
  resource_id         = aws_api_gateway_resource.admin_export.id
  http_method         = aws_api_gateway_method.admin_export_options.http_method
  status_code         = aws_api_gateway_method_response.admin_export_options_200.status_code
  response_parameters = local.cors_headers
}

# /admin/partners
resource "aws_api_gateway_resource" "admin_partners" {
  rest_api_id = aws_api_gateway_rest_api.main.id
  parent_id   = aws_api_gateway_resource.admin.id
  path_part   = "partners"
}

resource "aws_api_gateway_method" "admin_partners_get" {
  rest_api_id   = aws_api_gateway_rest_api.main.id
  resource_id   = aws_api_gateway_resource.admin_partners.id
  http_method   = "GET"
  authorization = "COGNITO_USER_POOLS"
  authorizer_id = aws_api_gateway_authorizer.cognito.id
}

resource "aws_api_gateway_method" "admin_partners_post" {
  rest_api_id   = aws_api_gateway_rest_api.main.id
  resource_id   = aws_api_gateway_resource.admin_partners.id
  http_method   = "POST"
  authorization = "COGNITO_USER_POOLS"
  authorizer_id = aws_api_gateway_authorizer.cognito.id
}

resource "aws_api_gateway_integration" "admin_partners_get" {
  rest_api_id             = aws_api_gateway_rest_api.main.id
  resource_id             = aws_api_gateway_resource.admin_partners.id
  http_method             = aws_api_gateway_method.admin_partners_get.http_method
  integration_http_method = "POST"
  type                    = "AWS_PROXY"
  uri                     = aws_lambda_function.manage_partners.invoke_arn
}

resource "aws_api_gateway_integration" "admin_partners_post" {
  rest_api_id             = aws_api_gateway_rest_api.main.id
  resource_id             = aws_api_gateway_resource.admin_partners.id
  http_method             = aws_api_gateway_method.admin_partners_post.http_method
  integration_http_method = "POST"
  type                    = "AWS_PROXY"
  uri                     = aws_lambda_function.manage_partners.invoke_arn
}

resource "aws_lambda_permission" "manage_partners" {
  statement_id  = "AllowAPIGatewayInvokePartners"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.manage_partners.function_name
  principal     = "apigateway.amazonaws.com"
  source_arn    = "${aws_api_gateway_rest_api.main.execution_arn}/*/*"
}

resource "aws_api_gateway_method" "admin_partners_options" {
  rest_api_id   = aws_api_gateway_rest_api.main.id
  resource_id   = aws_api_gateway_resource.admin_partners.id
  http_method   = "OPTIONS"
  authorization = "NONE"
}

resource "aws_api_gateway_integration" "admin_partners_options" {
  rest_api_id       = aws_api_gateway_rest_api.main.id
  resource_id       = aws_api_gateway_resource.admin_partners.id
  http_method       = aws_api_gateway_method.admin_partners_options.http_method
  type              = "MOCK"
  request_templates = { "application/json" = "{\"statusCode\": 200}" }
}

resource "aws_api_gateway_method_response" "admin_partners_options_200" {
  rest_api_id = aws_api_gateway_rest_api.main.id
  resource_id = aws_api_gateway_resource.admin_partners.id
  http_method = aws_api_gateway_method.admin_partners_options.http_method
  status_code = "200"
  response_parameters = {
    "method.response.header.Access-Control-Allow-Headers" = true
    "method.response.header.Access-Control-Allow-Methods" = true
    "method.response.header.Access-Control-Allow-Origin"  = true
  }
}

resource "aws_api_gateway_integration_response" "admin_partners_options" {
  rest_api_id         = aws_api_gateway_rest_api.main.id
  resource_id         = aws_api_gateway_resource.admin_partners.id
  http_method         = aws_api_gateway_method.admin_partners_options.http_method
  status_code         = aws_api_gateway_method_response.admin_partners_options_200.status_code
  response_parameters = local.cors_headers
}

# /admin/partners/{partnerId}
resource "aws_api_gateway_resource" "admin_partner_item" {
  rest_api_id = aws_api_gateway_rest_api.main.id
  parent_id   = aws_api_gateway_resource.admin_partners.id
  path_part   = "{partnerId}"
}

resource "aws_api_gateway_method" "admin_partner_delete" {
  rest_api_id   = aws_api_gateway_rest_api.main.id
  resource_id   = aws_api_gateway_resource.admin_partner_item.id
  http_method   = "DELETE"
  authorization = "COGNITO_USER_POOLS"
  authorizer_id = aws_api_gateway_authorizer.cognito.id
  request_parameters = {
    "method.request.path.partnerId" = true
  }
}

resource "aws_api_gateway_integration" "admin_partner_delete" {
  rest_api_id             = aws_api_gateway_rest_api.main.id
  resource_id             = aws_api_gateway_resource.admin_partner_item.id
  http_method             = aws_api_gateway_method.admin_partner_delete.http_method
  integration_http_method = "POST"
  type                    = "AWS_PROXY"
  uri                     = aws_lambda_function.manage_partners.invoke_arn
}

resource "aws_api_gateway_method" "admin_partner_options" {
  rest_api_id   = aws_api_gateway_rest_api.main.id
  resource_id   = aws_api_gateway_resource.admin_partner_item.id
  http_method   = "OPTIONS"
  authorization = "NONE"
}

resource "aws_api_gateway_integration" "admin_partner_options" {
  rest_api_id       = aws_api_gateway_rest_api.main.id
  resource_id       = aws_api_gateway_resource.admin_partner_item.id
  http_method       = aws_api_gateway_method.admin_partner_options.http_method
  type              = "MOCK"
  request_templates = { "application/json" = "{\"statusCode\": 200}" }
}

resource "aws_api_gateway_method_response" "admin_partner_options_200" {
  rest_api_id = aws_api_gateway_rest_api.main.id
  resource_id = aws_api_gateway_resource.admin_partner_item.id
  http_method = aws_api_gateway_method.admin_partner_options.http_method
  status_code = "200"
  response_parameters = {
    "method.response.header.Access-Control-Allow-Headers" = true
    "method.response.header.Access-Control-Allow-Methods" = true
    "method.response.header.Access-Control-Allow-Origin"  = true
  }
}

resource "aws_api_gateway_integration_response" "admin_partner_options" {
  rest_api_id         = aws_api_gateway_rest_api.main.id
  resource_id         = aws_api_gateway_resource.admin_partner_item.id
  http_method         = aws_api_gateway_method.admin_partner_options.http_method
  status_code         = aws_api_gateway_method_response.admin_partner_options_200.status_code
  response_parameters = local.cors_headers
}

# ─────────────────────────────────────────────────────────────────────────────
# /partners  (public — read-only list of active partners for the site)
# ─────────────────────────────────────────────────────────────────────────────

resource "aws_api_gateway_resource" "partners" {
  rest_api_id = aws_api_gateway_rest_api.main.id
  parent_id   = aws_api_gateway_rest_api.main.root_resource_id
  path_part   = "partners"
}

resource "aws_api_gateway_method" "partners_get" {
  rest_api_id   = aws_api_gateway_rest_api.main.id
  resource_id   = aws_api_gateway_resource.partners.id
  http_method   = "GET"
  authorization = "NONE"
}

resource "aws_api_gateway_integration" "partners_get" {
  rest_api_id             = aws_api_gateway_rest_api.main.id
  resource_id             = aws_api_gateway_resource.partners.id
  http_method             = aws_api_gateway_method.partners_get.http_method
  integration_http_method = "POST"
  type                    = "AWS_PROXY"
  uri                     = aws_lambda_function.manage_partners.invoke_arn
}

resource "aws_api_gateway_method" "partners_options" {
  rest_api_id   = aws_api_gateway_rest_api.main.id
  resource_id   = aws_api_gateway_resource.partners.id
  http_method   = "OPTIONS"
  authorization = "NONE"
}

resource "aws_api_gateway_integration" "partners_options" {
  rest_api_id       = aws_api_gateway_rest_api.main.id
  resource_id       = aws_api_gateway_resource.partners.id
  http_method       = aws_api_gateway_method.partners_options.http_method
  type              = "MOCK"
  request_templates = { "application/json" = "{\"statusCode\": 200}" }
}

resource "aws_api_gateway_method_response" "partners_options_200" {
  rest_api_id = aws_api_gateway_rest_api.main.id
  resource_id = aws_api_gateway_resource.partners.id
  http_method = aws_api_gateway_method.partners_options.http_method
  status_code = "200"
  response_parameters = {
    "method.response.header.Access-Control-Allow-Headers" = true
    "method.response.header.Access-Control-Allow-Methods" = true
    "method.response.header.Access-Control-Allow-Origin"  = true
  }
}

resource "aws_api_gateway_integration_response" "partners_options" {
  rest_api_id         = aws_api_gateway_rest_api.main.id
  resource_id         = aws_api_gateway_resource.partners.id
  http_method         = aws_api_gateway_method.partners_options.http_method
  status_code         = aws_api_gateway_method_response.partners_options_200.status_code
  response_parameters = local.cors_headers
}

# ── Deployment & Stage ───────────────────────────────────────────────────────

resource "aws_api_gateway_deployment" "main" {
  rest_api_id = aws_api_gateway_rest_api.main.id

  triggers = {
    # Redeploy when any method/integration changes
    redeployment = sha1(jsonencode([
      aws_api_gateway_resource.admin_event_platform_proxy,
      aws_api_gateway_resource.public_proxy,
      aws_api_gateway_method.public_any,
      aws_api_gateway_integration.public_any,
      aws_api_gateway_method.public_options,
      aws_api_gateway_integration.public_options,
      aws_api_gateway_resource.webhooks_event_registrations_provider,
      aws_api_gateway_method.webhooks_event_registrations_post,
      aws_api_gateway_integration.webhooks_event_registrations_post,
      aws_api_gateway_method.admin_event_platform_any,
      aws_api_gateway_integration.admin_event_platform_any,
      aws_api_gateway_method.admin_event_platform_options,
      aws_api_gateway_integration.admin_event_platform_options,
      aws_api_gateway_resource.register,
      aws_api_gateway_method.register_post,
      aws_api_gateway_integration.register_post,
      aws_api_gateway_resource.event_register,
      aws_api_gateway_resource.event_availability,
      aws_api_gateway_method.event_availability_get,
      aws_api_gateway_integration.event_availability_get,
      aws_api_gateway_method.event_availability_options,
      aws_api_gateway_integration.event_availability_options,
      aws_api_gateway_integration_response.event_availability_options,
      aws_api_gateway_method.event_register_post,
      aws_api_gateway_integration.event_register_post,
      aws_api_gateway_resource.admin_registrations,
      aws_api_gateway_method.admin_registrations_get,
      aws_api_gateway_integration.admin_registrations_get,
      aws_api_gateway_resource.admin_export,
      aws_api_gateway_method.admin_export_get,
      aws_api_gateway_integration.admin_export_get,
      aws_api_gateway_method.admin_export_options,
      aws_api_gateway_integration.admin_export_options,
      aws_api_gateway_method_response.admin_export_options_200,
      aws_api_gateway_integration_response.admin_export_options,
      aws_api_gateway_resource.admin_partners,
      aws_api_gateway_method.admin_partners_get,
      aws_api_gateway_method.admin_partners_post,
      aws_api_gateway_integration.admin_partners_get,
      aws_api_gateway_integration.admin_partners_post,
      aws_api_gateway_method.admin_partners_options,
      aws_api_gateway_integration.admin_partners_options,
      aws_api_gateway_method_response.admin_partners_options_200,
      aws_api_gateway_integration_response.admin_partners_options,
      aws_api_gateway_resource.admin_partner_item,
      aws_api_gateway_method.admin_partner_delete,
      aws_api_gateway_integration.admin_partner_delete,
      aws_api_gateway_method.admin_partner_options,
      aws_api_gateway_integration.admin_partner_options,
      aws_api_gateway_method_response.admin_partner_options_200,
      aws_api_gateway_integration_response.admin_partner_options,
      # Public partners list
      aws_api_gateway_resource.partners,
      aws_api_gateway_method.partners_get,
      aws_api_gateway_integration.partners_get,
      aws_api_gateway_method.partners_options,
      aws_api_gateway_integration.partners_options,
      aws_api_gateway_method_response.partners_options_200,
      aws_api_gateway_integration_response.partners_options,
      aws_api_gateway_method.admin_registrations_options,
      aws_api_gateway_integration.admin_registrations_options,
      aws_api_gateway_method_response.admin_registrations_options_200,
      aws_api_gateway_integration_response.admin_registrations_options,
      aws_api_gateway_resource.admin_registrations_bulk,
      aws_api_gateway_method.admin_registrations_bulk_post,
      aws_api_gateway_integration.admin_registrations_bulk_post,
      aws_api_gateway_method.admin_registrations_bulk_options,
      aws_api_gateway_integration.admin_registrations_bulk_options,
      aws_api_gateway_method_response.admin_registrations_bulk_options_200,
      aws_api_gateway_integration_response.admin_registrations_bulk_options,
      aws_api_gateway_resource.admin_registrations_reassign,
      aws_api_gateway_method.admin_registrations_reassign_post,
      aws_api_gateway_integration.admin_registrations_reassign_post,
      aws_api_gateway_method.admin_registrations_reassign_options,
      aws_api_gateway_integration.admin_registrations_reassign_options,
      aws_api_gateway_method_response.admin_registrations_reassign_options_200,
      aws_api_gateway_integration_response.admin_registrations_reassign_options,
      aws_api_gateway_resource.admin_registrations_allocate_seats,
      aws_api_gateway_method.admin_registrations_allocate_seats_get,
      aws_api_gateway_integration.admin_registrations_allocate_seats_get,
      aws_api_gateway_method.admin_registrations_allocate_seats_post,
      aws_api_gateway_integration.admin_registrations_allocate_seats_post,
      aws_api_gateway_method.admin_registrations_allocate_seats_options,
      aws_api_gateway_integration.admin_registrations_allocate_seats_options,
      aws_api_gateway_method_response.admin_registrations_allocate_seats_options_200,
      aws_api_gateway_integration_response.admin_registrations_allocate_seats_options,
      aws_api_gateway_resource.admin_registrations_seat_selection_announcement,
      aws_api_gateway_method.admin_registrations_seat_selection_announcement_post,
      aws_api_gateway_integration.admin_registrations_seat_selection_announcement_post,
      aws_api_gateway_method.admin_registrations_seat_selection_announcement_options,
      aws_api_gateway_integration.admin_registrations_seat_selection_announcement_options,
      aws_api_gateway_method_response.admin_registrations_seat_selection_announcement_options_200,
      aws_api_gateway_integration_response.admin_registrations_seat_selection_announcement_options,
      aws_api_gateway_resource.admin_check_in,
      aws_api_gateway_method.admin_check_in_post,
      aws_api_gateway_integration.admin_check_in_post,
      aws_api_gateway_method.admin_check_in_options,
      aws_api_gateway_integration.admin_check_in_options,
      aws_api_gateway_method_response.admin_check_in_options_200,
      aws_api_gateway_integration_response.admin_check_in_options,
      aws_api_gateway_method.confirm_get,
      aws_api_gateway_integration.confirm_get,
      aws_api_gateway_resource.visits,
      aws_api_gateway_method.visits_post,
      aws_api_gateway_integration.visits_post,
      aws_api_gateway_method.visits_options,
      aws_api_gateway_integration.visits_options,
      aws_api_gateway_method_response.visits_options_200,
      aws_api_gateway_integration_response.visits_options,
      aws_api_gateway_resource.auth_request_otp,
      aws_api_gateway_method.auth_request_otp_post,
      aws_api_gateway_integration.auth_request_otp_post,
      aws_api_gateway_method.auth_request_otp_options,
      aws_api_gateway_integration.auth_request_otp_options,
      aws_api_gateway_method_response.auth_request_otp_options_200,
      aws_api_gateway_integration_response.auth_request_otp_options,
      aws_api_gateway_resource.auth_verify_otp,
      aws_api_gateway_method.auth_verify_otp_post,
      aws_api_gateway_integration.auth_verify_otp_post,
      aws_api_gateway_method.auth_verify_otp_options,
      aws_api_gateway_integration.auth_verify_otp_options,
      aws_api_gateway_method_response.auth_verify_otp_options_200,
      aws_api_gateway_integration_response.auth_verify_otp_options,
      aws_api_gateway_resource.membership,
      aws_api_gateway_resource.membership_status,
      aws_api_gateway_method.membership_status_get,
      aws_api_gateway_integration.membership_status_get,
      aws_api_gateway_method.membership_status_options,
      aws_api_gateway_integration.membership_status_options,
      aws_api_gateway_method_response.membership_status_options_200,
      aws_api_gateway_integration_response.membership_status_options,
      aws_api_gateway_resource.membership_check,
      aws_api_gateway_method.membership_check_post,
      aws_api_gateway_integration.membership_check_post,
      aws_api_gateway_method.membership_check_options,
      aws_api_gateway_integration.membership_check_options,
    aws_api_gateway_integration.membership_apply_post,
    aws_api_gateway_integration.membership_apply_options,
    aws_api_gateway_integration.membership_agm_post,
    aws_api_gateway_integration.membership_agm_options,
      aws_api_gateway_method_response.membership_check_options_200,
      aws_api_gateway_integration_response.membership_check_options,
      aws_api_gateway_resource.membership_apply,
      aws_api_gateway_method.membership_apply_post,
      aws_api_gateway_integration.membership_apply_post,
      aws_api_gateway_method.membership_apply_options,
      aws_api_gateway_integration.membership_apply_options,
      aws_api_gateway_method_response.membership_apply_options_200,
      aws_api_gateway_integration_response.membership_apply_options,
      aws_api_gateway_resource.membership_agm,
      aws_api_gateway_method.membership_agm_post,
      aws_api_gateway_integration.membership_agm_post,
      aws_api_gateway_method.membership_agm_options,
      aws_api_gateway_integration.membership_agm_options,
      aws_api_gateway_method_response.membership_agm_options_200,
      aws_api_gateway_integration_response.membership_agm_options,
      # Registrant self-service: /my/registrations (+ item)
      aws_api_gateway_resource.my,
      aws_api_gateway_resource.my_registrations,
      aws_api_gateway_method.my_registrations_get,
      aws_api_gateway_integration.my_registrations_get,
      aws_api_gateway_method.my_registrations_options,
      aws_api_gateway_integration.my_registrations_options,
      aws_api_gateway_method_response.my_registrations_options_200,
      aws_api_gateway_integration_response.my_registrations_options,
      aws_api_gateway_resource.my_registration_item,
      aws_api_gateway_method.my_registration_put,
      aws_api_gateway_integration.my_registration_put,
      aws_api_gateway_method.my_registration_delete,
      aws_api_gateway_integration.my_registration_delete,
      aws_api_gateway_method.my_registration_item_options,
      aws_api_gateway_integration.my_registration_item_options,
      aws_api_gateway_method_response.my_registration_item_options_200,
      aws_api_gateway_integration_response.my_registration_item_options,
      aws_api_gateway_resource.my_registration_item_slot,
      aws_api_gateway_method.my_registration_slot_put,
      aws_api_gateway_integration.my_registration_slot_put,
      aws_api_gateway_method.my_registration_slot_options,
      aws_api_gateway_integration.my_registration_slot_options,
      aws_api_gateway_method_response.my_registration_slot_options_200,
      aws_api_gateway_integration_response.my_registration_slot_options,
      aws_api_gateway_resource.my_registration_item_celebrity_dinner,
      aws_api_gateway_method.my_registration_celebrity_dinner_get,
      aws_api_gateway_integration.my_registration_celebrity_dinner_get,
      aws_api_gateway_method.my_registration_celebrity_dinner_put,
      aws_api_gateway_integration.my_registration_celebrity_dinner_put,
      aws_api_gateway_method.my_registration_celebrity_dinner_delete,
      aws_api_gateway_integration.my_registration_celebrity_dinner_delete,
      aws_api_gateway_method.my_registration_celebrity_dinner_options,
      aws_api_gateway_integration.my_registration_celebrity_dinner_options,
      aws_api_gateway_method_response.my_registration_celebrity_dinner_options_200,
      aws_api_gateway_integration_response.my_registration_celebrity_dinner_options,
      aws_api_gateway_resource.my_registration_celebrity_dinner_checkout,
      aws_api_gateway_method.my_registration_celebrity_dinner_checkout_post,
      aws_api_gateway_integration.my_registration_celebrity_dinner_checkout_post,
      aws_api_gateway_method.my_registration_celebrity_dinner_checkout_options,
      aws_api_gateway_integration.my_registration_celebrity_dinner_checkout_options,
      aws_api_gateway_method_response.my_registration_celebrity_dinner_checkout_options_200,
      aws_api_gateway_integration_response.my_registration_celebrity_dinner_checkout_options,
      # Guest (no registration) Celebrity Dinner: /my/celebrity-dinner/{eventType} (+ checkout)
      aws_api_gateway_resource.my_celebrity_dinner,
      aws_api_gateway_resource.my_celebrity_dinner_item,
      aws_api_gateway_method.my_celebrity_dinner_get,
      aws_api_gateway_integration.my_celebrity_dinner_get,
      aws_api_gateway_method.my_celebrity_dinner_put,
      aws_api_gateway_integration.my_celebrity_dinner_put,
      aws_api_gateway_method.my_celebrity_dinner_delete,
      aws_api_gateway_integration.my_celebrity_dinner_delete,
      aws_api_gateway_method.my_celebrity_dinner_options,
      aws_api_gateway_integration.my_celebrity_dinner_options,
      aws_api_gateway_method_response.my_celebrity_dinner_options_200,
      aws_api_gateway_integration_response.my_celebrity_dinner_options,
      aws_api_gateway_resource.my_celebrity_dinner_checkout,
      aws_api_gateway_method.my_celebrity_dinner_checkout_post,
      aws_api_gateway_integration.my_celebrity_dinner_checkout_post,
      aws_api_gateway_method.my_celebrity_dinner_checkout_options,
      aws_api_gateway_integration.my_celebrity_dinner_checkout_options,
      aws_api_gateway_method_response.my_celebrity_dinner_checkout_options_200,
      aws_api_gateway_integration_response.my_celebrity_dinner_checkout_options,
      # Admin events CRUD
      aws_api_gateway_resource.admin_events,
      aws_api_gateway_method.admin_events_get,
      aws_api_gateway_method.admin_events_post,
      aws_api_gateway_method.admin_events_put,
      aws_api_gateway_integration.admin_events_get,
      aws_api_gateway_integration.admin_events_post,
      aws_api_gateway_integration.admin_events_put,
      aws_api_gateway_method.admin_events_options,
      aws_api_gateway_integration.admin_events_options,
      aws_api_gateway_method_response.admin_events_options_200,
      aws_api_gateway_integration_response.admin_events_options,
      aws_api_gateway_resource.admin_event_item,
      aws_api_gateway_method.admin_event_get,
      aws_api_gateway_method.admin_event_delete,
      aws_api_gateway_integration.admin_event_get,
      aws_api_gateway_integration.admin_event_delete,
      aws_api_gateway_method.admin_event_options,
      aws_api_gateway_integration.admin_event_options,
      aws_api_gateway_method_response.admin_event_options_200,
      aws_api_gateway_integration_response.admin_event_options,
      # Admin Celebrity Dinner config + registrations list
      aws_api_gateway_resource.admin_celebrity_dinner,
      aws_api_gateway_method.admin_celebrity_dinner_get,
      aws_api_gateway_method.admin_celebrity_dinner_put,
      aws_api_gateway_integration.admin_celebrity_dinner_get,
      aws_api_gateway_integration.admin_celebrity_dinner_put,
      aws_api_gateway_method.admin_celebrity_dinner_options,
      aws_api_gateway_integration.admin_celebrity_dinner_options,
      aws_api_gateway_method_response.admin_celebrity_dinner_options_200,
      aws_api_gateway_integration_response.admin_celebrity_dinner_options,
      aws_api_gateway_resource.admin_celebrity_dinner_registrations,
      aws_api_gateway_method.admin_celebrity_dinner_registrations_get,
      aws_api_gateway_integration.admin_celebrity_dinner_registrations_get,
      aws_api_gateway_method.admin_celebrity_dinner_registrations_options,
      aws_api_gateway_integration.admin_celebrity_dinner_registrations_options,
      aws_api_gateway_method_response.admin_celebrity_dinner_registrations_options_200,
      aws_api_gateway_integration_response.admin_celebrity_dinner_registrations_options,
      aws_api_gateway_resource.admin_celebrity_dinner_registration_item,
      aws_api_gateway_method.admin_celebrity_dinner_registration_patch,
      aws_api_gateway_integration.admin_celebrity_dinner_registration_patch,
      aws_api_gateway_method.admin_celebrity_dinner_registration_options,
      aws_api_gateway_integration.admin_celebrity_dinner_registration_options,
      aws_api_gateway_method_response.admin_celebrity_dinner_registration_options_200,
      aws_api_gateway_integration_response.admin_celebrity_dinner_registration_options,
      # Admin entry slots
      aws_api_gateway_resource.admin_entry_slots,
      aws_api_gateway_method.admin_entry_slots_get,
      aws_api_gateway_method.admin_entry_slots_post,
      aws_api_gateway_integration.admin_entry_slots_get,
      aws_api_gateway_integration.admin_entry_slots_post,
      aws_api_gateway_method.admin_entry_slots_options,
      aws_api_gateway_integration.admin_entry_slots_options,
      aws_api_gateway_method_response.admin_entry_slots_options_200,
      aws_api_gateway_integration_response.admin_entry_slots_options,
      aws_api_gateway_resource.admin_entry_slot_item,
      aws_api_gateway_method.admin_entry_slot_item_get,
      aws_api_gateway_method.admin_entry_slot_item_patch,
      aws_api_gateway_method.admin_entry_slot_item_delete,
      aws_api_gateway_integration.admin_entry_slot_item_get,
      aws_api_gateway_integration.admin_entry_slot_item_patch,
      aws_api_gateway_integration.admin_entry_slot_item_delete,
      aws_api_gateway_method.admin_entry_slot_item_options,
      aws_api_gateway_integration.admin_entry_slot_item_options,
      aws_api_gateway_method_response.admin_entry_slot_item_options_200,
      aws_api_gateway_integration_response.admin_entry_slot_item_options,
      aws_api_gateway_resource.admin_entry_slot_release_buffer,
      aws_api_gateway_method.admin_entry_slot_release_buffer_post,
      aws_api_gateway_integration.admin_entry_slot_release_buffer_post,
      aws_api_gateway_method.admin_entry_slot_release_buffer_options,
      aws_api_gateway_integration.admin_entry_slot_release_buffer_options,
      aws_api_gateway_method_response.admin_entry_slot_release_buffer_options_200,
      aws_api_gateway_integration_response.admin_entry_slot_release_buffer_options,
      # Admin registration CRUD
      aws_api_gateway_resource.admin_registration,
      aws_api_gateway_method.admin_registration_post,
      aws_api_gateway_integration.admin_registration_post,
      aws_api_gateway_method.admin_registration_options,
      aws_api_gateway_integration.admin_registration_options,
      aws_api_gateway_method_response.admin_registration_options_200,
      aws_api_gateway_integration_response.admin_registration_options,
      aws_api_gateway_resource.admin_registration_item,
      aws_api_gateway_method.admin_registration_delete,
      aws_api_gateway_integration.admin_registration_delete,
      aws_api_gateway_method.admin_registration_item_options,
      aws_api_gateway_integration.admin_registration_item_options,
      aws_api_gateway_method_response.admin_registration_item_options_200,
      aws_api_gateway_integration_response.admin_registration_item_options,
      # Admin users
      aws_api_gateway_resource.admin_users,
      aws_api_gateway_method.admin_users_get,
      aws_api_gateway_integration.admin_users_get,
      aws_api_gateway_method.admin_users_options,
      aws_api_gateway_integration.admin_users_options,
      aws_api_gateway_method_response.admin_users_options_200,
      aws_api_gateway_integration_response.admin_users_options,
      # Admin visits summary
      aws_api_gateway_resource.admin_visits_summary,
      aws_api_gateway_method.admin_visits_summary_get,
      aws_api_gateway_integration.admin_visits_summary_get,
      aws_api_gateway_method.admin_visits_summary_options,
      aws_api_gateway_integration.admin_visits_summary_options,
      aws_api_gateway_method_response.admin_visits_summary_options_200,
      aws_api_gateway_integration_response.admin_visits_summary_options,
      # Admin members CRUD
      aws_api_gateway_resource.admin_members,
      aws_api_gateway_method.admin_members_get,
      aws_api_gateway_method.admin_members_post,
      aws_api_gateway_integration.admin_members_get,
      aws_api_gateway_integration.admin_members_post,
      aws_api_gateway_method.admin_members_options,
      aws_api_gateway_integration.admin_members_options,
      aws_api_gateway_method_response.admin_members_options_200,
      aws_api_gateway_integration_response.admin_members_options,
      aws_api_gateway_resource.admin_member_item,
      aws_api_gateway_method.admin_member_get,
      aws_api_gateway_method.admin_member_put,
      aws_api_gateway_method.admin_member_delete,
      aws_api_gateway_integration.admin_member_get,
      aws_api_gateway_integration.admin_member_put,
      aws_api_gateway_integration.admin_member_delete,
      aws_api_gateway_method.admin_member_item_options,
      aws_api_gateway_integration.admin_member_item_options,
      aws_api_gateway_method_response.admin_member_item_options_200,
      aws_api_gateway_integration_response.admin_member_item_options,
      # Admin NPO membership CRUD
      aws_api_gateway_resource.admin_npo_membership,
      aws_api_gateway_resource.admin_npo_membership_member,
      aws_api_gateway_method.admin_npo_membership_get,
      aws_api_gateway_method.admin_npo_membership_post,
      aws_api_gateway_integration.admin_npo_membership_get,
      aws_api_gateway_integration.admin_npo_membership_post,
      aws_api_gateway_method.admin_npo_membership_member_options,
      aws_api_gateway_integration.admin_npo_membership_member_options,
      aws_api_gateway_method_response.admin_npo_membership_member_options_200,
      aws_api_gateway_integration_response.admin_npo_membership_member_options,
      aws_api_gateway_resource.admin_npo_membership_item,
      aws_api_gateway_method.admin_npo_membership_put,
      aws_api_gateway_method.admin_npo_membership_delete,
      aws_api_gateway_integration.admin_npo_membership_put,
      aws_api_gateway_integration.admin_npo_membership_delete,
      aws_api_gateway_method.admin_npo_membership_item_options,
      aws_api_gateway_integration.admin_npo_membership_item_options,
      aws_api_gateway_method_response.admin_npo_membership_item_options_200,
      aws_api_gateway_integration_response.admin_npo_membership_item_options,
      aws_api_gateway_resource.admin_agms,
      aws_api_gateway_resource.admin_agm_item,
      aws_api_gateway_method.admin_agms_get,
      aws_api_gateway_method.admin_agms_post,
      aws_api_gateway_integration.admin_agms_get,
      aws_api_gateway_integration.admin_agms_post,
      aws_api_gateway_method.admin_agms_options,
      aws_api_gateway_integration.admin_agms_options,
      aws_api_gateway_method_response.admin_agms_options_200,
      aws_api_gateway_integration_response.admin_agms_options,
      aws_api_gateway_method.admin_agm_item_get,
      aws_api_gateway_method.admin_agm_item_put,
      aws_api_gateway_integration.admin_agm_item_get,
      aws_api_gateway_integration.admin_agm_item_put,
      aws_api_gateway_method.admin_agm_item_options,
      aws_api_gateway_integration.admin_agm_item_options,
      aws_api_gateway_method_response.admin_agm_item_options_200,
      aws_api_gateway_integration_response.admin_agm_item_options,
      aws_api_gateway_resource.admin_member_announcements,
      aws_api_gateway_method.admin_member_announcements_get,
      aws_api_gateway_method.admin_member_announcements_post,
      aws_api_gateway_integration.admin_member_announcements_get,
      aws_api_gateway_integration.admin_member_announcements_post,
      aws_api_gateway_method.admin_member_announcements_options,
      aws_api_gateway_integration.admin_member_announcements_options,
      aws_api_gateway_method_response.admin_member_announcements_options_200,
      aws_api_gateway_integration_response.admin_member_announcements_options,
      # Site config (public + admin)
      aws_api_gateway_resource.site_config,
      aws_api_gateway_method.site_config_get,
    aws_api_gateway_integration.event_availability_get,
    aws_api_gateway_integration.event_availability_options,
    aws_api_gateway_integration_response.event_availability_options,
    aws_api_gateway_integration.site_config_get,
      aws_api_gateway_method.site_config_options,
      aws_api_gateway_integration.site_config_options,
      aws_api_gateway_method_response.site_config_options_200,
      aws_api_gateway_integration_response.site_config_options,
      aws_api_gateway_resource.admin_site_config,
      aws_api_gateway_method.admin_site_config_get,
      aws_api_gateway_method.admin_site_config_put,
      aws_api_gateway_integration.admin_site_config_get,
      aws_api_gateway_integration.admin_site_config_put,
      aws_api_gateway_method.admin_site_config_options,
      aws_api_gateway_integration.admin_site_config_options,
      aws_api_gateway_method_response.admin_site_config_options_200,
      aws_api_gateway_integration_response.admin_site_config_options,
      aws_api_gateway_resource.admin_site_config_upload,
      aws_api_gateway_method.admin_site_config_upload_post,
      aws_api_gateway_integration.admin_site_config_upload_post,
      aws_api_gateway_method.admin_site_config_upload_options,
      aws_api_gateway_integration.admin_site_config_upload_options,
      aws_api_gateway_method_response.admin_site_config_upload_options_200,
      aws_api_gateway_integration_response.admin_site_config_upload_options,
      # Available slots for registrants
      aws_api_gateway_resource.registrations,
      aws_api_gateway_resource.available_slots,
      aws_api_gateway_method.available_slots_get,
      aws_api_gateway_integration.available_slots_get,
      aws_api_gateway_method.available_slots_options,
      aws_api_gateway_integration.available_slots_options,
      aws_api_gateway_method_response.available_slots_options_200,
      aws_api_gateway_integration_response.available_slots_options,
      # Celebrity Event seat purchases: registrant self-service (host-owned)
      aws_api_gateway_resource.my_registration_item_seat_purchases,
      aws_api_gateway_method.my_registration_seat_purchases_get,
      aws_api_gateway_integration.my_registration_seat_purchases_get,
      aws_api_gateway_method.my_registration_seat_purchases_post,
      aws_api_gateway_integration.my_registration_seat_purchases_post,
      aws_api_gateway_method.my_registration_seat_purchases_options,
      aws_api_gateway_integration.my_registration_seat_purchases_options,
      aws_api_gateway_method_response.my_registration_seat_purchases_options_200,
      aws_api_gateway_integration_response.my_registration_seat_purchases_options,
      aws_api_gateway_resource.my_registration_seat_purchase_item,
      aws_api_gateway_method.my_registration_seat_purchase_item_get,
      aws_api_gateway_integration.my_registration_seat_purchase_item_get,
      aws_api_gateway_method.my_registration_seat_purchase_item_delete,
      aws_api_gateway_integration.my_registration_seat_purchase_item_delete,
      aws_api_gateway_method.my_registration_seat_purchase_item_options,
      aws_api_gateway_integration.my_registration_seat_purchase_item_options,
      aws_api_gateway_method_response.my_registration_seat_purchase_item_options_200,
      aws_api_gateway_integration_response.my_registration_seat_purchase_item_options,
      aws_api_gateway_resource.my_registration_seat_purchase_checkout,
      aws_api_gateway_method.my_registration_seat_purchase_checkout_post,
      aws_api_gateway_integration.my_registration_seat_purchase_checkout_post,
      aws_api_gateway_method.my_registration_seat_purchase_checkout_options,
      aws_api_gateway_integration.my_registration_seat_purchase_checkout_options,
      aws_api_gateway_method_response.my_registration_seat_purchase_checkout_options_200,
      aws_api_gateway_integration_response.my_registration_seat_purchase_checkout_options,
      # Seat-selection plugin module: public seat map + admin view/block/unblock/
      # pricing/purchases + payment-provider webhook
      module.seat_selection.deployment_trigger_inputs,
      # Shared /webhooks mount point — see its own comment above.
      aws_api_gateway_resource.webhooks,
      # Standalone Celebrity Dinner payment webhook
      aws_api_gateway_resource.webhooks_celebrity_dinner,
      aws_api_gateway_resource.webhooks_celebrity_dinner_provider,
      aws_api_gateway_method.webhooks_celebrity_dinner_post,
      aws_api_gateway_integration.webhooks_celebrity_dinner_post,
      aws_api_gateway_resource.webhooks_npo_membership,
      aws_api_gateway_resource.webhooks_npo_membership_provider,
      aws_api_gateway_method.webhooks_npo_membership_post,
      aws_api_gateway_integration.webhooks_npo_membership_post,
      # Seat-upgrade plugin: its own hash captures every route it owns
      # (admin tiers/requests, self-service, checkout, webhook) — see
      # module "seat_upgrade" in plugin.tf and that module's outputs.tf.
      module.seat_upgrade.api_trigger_hash,
      # Admin payment reconciliation
      aws_api_gateway_resource.admin_reconciliation,
      aws_api_gateway_resource.admin_reconciliation_transactions,
      aws_api_gateway_method.admin_reconciliation_transactions_get,
      aws_api_gateway_method.admin_reconciliation_transactions_post,
      aws_api_gateway_integration.admin_reconciliation_transactions_get,
      aws_api_gateway_integration.admin_reconciliation_transactions_post,
      aws_api_gateway_method.admin_reconciliation_transactions_options,
      aws_api_gateway_integration.admin_reconciliation_transactions_options,
      aws_api_gateway_method_response.admin_reconciliation_transactions_options_200,
      aws_api_gateway_integration_response.admin_reconciliation_transactions_options,
      aws_api_gateway_resource.admin_reconciliation_transactions_preview,
      aws_api_gateway_method.admin_reconciliation_transactions_preview_post,
      aws_api_gateway_integration.admin_reconciliation_transactions_preview_post,
      aws_api_gateway_method.admin_reconciliation_transactions_preview_options,
      aws_api_gateway_integration.admin_reconciliation_transactions_preview_options,
      aws_api_gateway_method_response.admin_reconciliation_transactions_preview_options_200,
      aws_api_gateway_integration_response.admin_reconciliation_transactions_preview_options,
      aws_api_gateway_resource.admin_reconciliation_transaction_batch,
      aws_api_gateway_method.admin_reconciliation_transaction_batch_delete,
      aws_api_gateway_integration.admin_reconciliation_transaction_batch_delete,
      aws_api_gateway_method.admin_reconciliation_transaction_batch_options,
      aws_api_gateway_integration.admin_reconciliation_transaction_batch_options,
      aws_api_gateway_method_response.admin_reconciliation_transaction_batch_options_200,
      aws_api_gateway_integration_response.admin_reconciliation_transaction_batch_options,
      aws_api_gateway_resource.admin_reconciliation_runs,
      aws_api_gateway_method.admin_reconciliation_runs_get,
      aws_api_gateway_method.admin_reconciliation_runs_post,
      aws_api_gateway_integration.admin_reconciliation_runs_get,
      aws_api_gateway_integration.admin_reconciliation_runs_post,
      aws_api_gateway_method.admin_reconciliation_runs_options,
      aws_api_gateway_integration.admin_reconciliation_runs_options,
      aws_api_gateway_method_response.admin_reconciliation_runs_options_200,
      aws_api_gateway_integration_response.admin_reconciliation_runs_options,
      aws_api_gateway_resource.admin_reconciliation_run_item,
      aws_api_gateway_method.admin_reconciliation_run_item_get,
      aws_api_gateway_integration.admin_reconciliation_run_item_get,
      aws_api_gateway_method.admin_reconciliation_run_item_options,
      aws_api_gateway_integration.admin_reconciliation_run_item_options,
      aws_api_gateway_method_response.admin_reconciliation_run_item_options_200,
      aws_api_gateway_integration_response.admin_reconciliation_run_item_options,
    ]))
  }

  lifecycle {
    create_before_destroy = true
  }

  depends_on = [
    aws_api_gateway_integration.register_post,
    aws_api_gateway_integration.register_options,
    aws_api_gateway_integration.event_register_post,
    aws_api_gateway_integration.event_register_options,
    aws_api_gateway_integration.admin_registrations_get,
    aws_api_gateway_integration.admin_registrations_options,
    aws_api_gateway_integration.admin_export_get,
    aws_api_gateway_integration.admin_export_options,
    aws_api_gateway_integration.admin_partners_get,
    aws_api_gateway_integration.admin_partners_post,
    aws_api_gateway_integration.admin_partners_options,
    aws_api_gateway_integration.admin_partner_delete,
    aws_api_gateway_integration.admin_partner_options,
    aws_api_gateway_integration.partners_get,
    aws_api_gateway_integration.partners_options,
    aws_api_gateway_integration.confirm_get,
    aws_api_gateway_integration.visits_post,
    aws_api_gateway_integration.visits_options,
    aws_api_gateway_integration.auth_request_otp_post,
    aws_api_gateway_integration.auth_request_otp_options,
    aws_api_gateway_integration.auth_verify_otp_post,
    aws_api_gateway_integration.auth_verify_otp_options,
    aws_api_gateway_integration.membership_status_get,
    aws_api_gateway_integration.membership_status_options,
    aws_api_gateway_integration.membership_check_post,
    aws_api_gateway_integration.membership_check_options,
    aws_api_gateway_integration.my_registrations_get,
    aws_api_gateway_integration.my_registrations_options,
    aws_api_gateway_integration.my_registration_put,
    aws_api_gateway_integration.my_registration_delete,
    aws_api_gateway_integration.my_registration_item_options,
    aws_api_gateway_integration.my_registration_slot_put,
    aws_api_gateway_integration.my_registration_slot_options,
    aws_api_gateway_integration.my_registration_celebrity_dinner_get,
    aws_api_gateway_integration.my_registration_celebrity_dinner_put,
    aws_api_gateway_integration.my_registration_celebrity_dinner_delete,
    aws_api_gateway_integration.my_registration_celebrity_dinner_options,
    aws_api_gateway_integration.my_registration_celebrity_dinner_checkout_post,
    aws_api_gateway_integration.my_registration_celebrity_dinner_checkout_options,
    aws_api_gateway_integration.webhooks_celebrity_dinner_post,
    aws_api_gateway_integration.webhooks_npo_membership_post,
    aws_api_gateway_integration.admin_events_get,
    aws_api_gateway_integration.admin_events_post,
    aws_api_gateway_integration.admin_events_put,
    aws_api_gateway_integration.admin_events_options,
    aws_api_gateway_integration.admin_event_get,
    aws_api_gateway_integration.admin_event_delete,
    aws_api_gateway_integration.admin_event_options,
    aws_api_gateway_integration.admin_celebrity_dinner_get,
    aws_api_gateway_integration.admin_celebrity_dinner_put,
    aws_api_gateway_integration.admin_celebrity_dinner_options,
    aws_api_gateway_integration.admin_celebrity_dinner_registrations_get,
    aws_api_gateway_integration.admin_celebrity_dinner_registrations_options,
    aws_api_gateway_integration.admin_celebrity_dinner_registration_patch,
    aws_api_gateway_integration.admin_celebrity_dinner_registration_options,
    aws_api_gateway_integration.admin_entry_slots_get,
    aws_api_gateway_integration.admin_entry_slots_post,
    aws_api_gateway_integration.admin_entry_slots_options,
    aws_api_gateway_integration.admin_entry_slot_item_get,
    aws_api_gateway_integration.admin_entry_slot_item_patch,
    aws_api_gateway_integration.admin_entry_slot_item_delete,
    aws_api_gateway_integration.admin_entry_slot_item_options,
    aws_api_gateway_integration.admin_entry_slot_release_buffer_post,
    aws_api_gateway_integration.admin_entry_slot_release_buffer_options,
    aws_api_gateway_integration.admin_registration_post,
    aws_api_gateway_integration.admin_registration_options,
    aws_api_gateway_integration.admin_registration_delete,
    aws_api_gateway_integration.admin_registration_item_options,
    aws_api_gateway_integration.admin_users_get,
    aws_api_gateway_integration.admin_users_options,
    aws_api_gateway_integration.admin_visits_summary_get,
    aws_api_gateway_integration.admin_visits_summary_options,
    aws_api_gateway_integration.admin_members_get,
    aws_api_gateway_integration.admin_members_post,
    aws_api_gateway_integration.admin_members_options,
    aws_api_gateway_integration.admin_member_get,
    aws_api_gateway_integration.admin_member_put,
    aws_api_gateway_integration.admin_member_delete,
    aws_api_gateway_integration.admin_member_item_options,
    aws_api_gateway_integration.admin_npo_membership_get,
    aws_api_gateway_integration.admin_npo_membership_post,
    aws_api_gateway_integration.admin_npo_membership_member_options,
    aws_api_gateway_integration.admin_npo_membership_put,
    aws_api_gateway_integration.admin_npo_membership_delete,
    aws_api_gateway_integration.admin_npo_membership_item_options,
    aws_api_gateway_integration.admin_agms_get,
    aws_api_gateway_integration.admin_agms_post,
    aws_api_gateway_integration.admin_agms_options,
    aws_api_gateway_integration.admin_agm_item_get,
    aws_api_gateway_integration.admin_agm_item_put,
    aws_api_gateway_integration.admin_agm_item_options,
    aws_api_gateway_integration.admin_member_announcements_get,
    aws_api_gateway_integration.admin_member_announcements_post,
    aws_api_gateway_integration.admin_member_announcements_options,
    aws_api_gateway_integration.site_config_get,
    aws_api_gateway_integration.site_config_options,
    aws_api_gateway_integration.admin_site_config_get,
    aws_api_gateway_integration.admin_site_config_put,
    aws_api_gateway_integration.admin_site_config_options,
    aws_api_gateway_integration.admin_site_config_upload_post,
    aws_api_gateway_integration.admin_site_config_upload_options,
    aws_api_gateway_integration.my_registration_seat_purchases_get,
    aws_api_gateway_integration.my_registration_seat_purchases_post,
    aws_api_gateway_integration.my_registration_seat_purchases_options,
    aws_api_gateway_integration.my_registration_seat_purchase_item_get,
    aws_api_gateway_integration.my_registration_seat_purchase_item_delete,
    aws_api_gateway_integration.my_registration_seat_purchase_item_options,
    aws_api_gateway_integration.my_registration_seat_purchase_checkout_post,
    aws_api_gateway_integration.my_registration_seat_purchase_checkout_options,
    module.seat_selection,
    # Seat-upgrade plugin: depending on the whole module waits for every
    # integration it creates, without hand-listing each one here too.
    module.seat_upgrade,
    aws_api_gateway_integration.admin_reconciliation_transactions_get,
    aws_api_gateway_integration.admin_reconciliation_transactions_post,
    aws_api_gateway_integration.admin_reconciliation_transactions_options,
    aws_api_gateway_integration.admin_reconciliation_transactions_preview_post,
    aws_api_gateway_integration.admin_reconciliation_transactions_preview_options,
    aws_api_gateway_integration.admin_reconciliation_transaction_batch_delete,
    aws_api_gateway_integration.admin_reconciliation_transaction_batch_options,
    aws_api_gateway_integration.admin_reconciliation_runs_get,
    aws_api_gateway_integration.admin_reconciliation_runs_post,
    aws_api_gateway_integration.admin_reconciliation_runs_options,
    aws_api_gateway_integration.admin_reconciliation_run_item_get,
    aws_api_gateway_integration.admin_reconciliation_run_item_options,
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
      caller         = "$context.identity.caller"
      user           = "$context.identity.user"
      requestTime    = "$context.requestTime"
      httpMethod     = "$context.httpMethod"
      resourcePath   = "$context.resourcePath"
      status         = "$context.status"
      protocol       = "$context.protocol"
      responseLength = "$context.responseLength"
      errorMessage   = "$context.error.message"
    })
  }

  variables = {
    resource_path = "/*"
    http_method   = "*"
  }

  depends_on = [aws_cloudwatch_log_group.api_gateway]
}

# ── Usage Plan & API Key (optional rate limiting) ────────────────────────────

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
    limit  = 5000
    period = "DAY"
  }
}

# ─────────────────────────────────────────────────────────────────────────────
# /events/{eventType}/register  (dynamic)
# ─────────────────────────────────────────────────────────────────────────────

resource "aws_api_gateway_resource" "events" {
  rest_api_id = aws_api_gateway_rest_api.main.id
  parent_id   = aws_api_gateway_rest_api.main.root_resource_id
  path_part   = "events"
}

resource "aws_api_gateway_resource" "event_type" {
  rest_api_id = aws_api_gateway_rest_api.main.id
  parent_id   = aws_api_gateway_resource.events.id
  path_part   = "{eventType}"
}

resource "aws_api_gateway_resource" "event_register" {
  rest_api_id = aws_api_gateway_rest_api.main.id
  parent_id   = aws_api_gateway_resource.event_type.id
  path_part   = "register"
}

# POST /events/{eventType}/register
resource "aws_api_gateway_method" "event_register_post" {
  rest_api_id   = aws_api_gateway_rest_api.main.id
  resource_id   = aws_api_gateway_resource.event_register.id
  http_method   = "POST"
  authorization = "NONE"

  request_parameters = {
    "method.request.path.eventType" = true
  }
}

resource "aws_api_gateway_integration" "event_register_post" {
  rest_api_id             = aws_api_gateway_rest_api.main.id
  resource_id             = aws_api_gateway_resource.event_register.id
  http_method             = aws_api_gateway_method.event_register_post.http_method
  integration_http_method = "POST"
  type                    = "AWS_PROXY"
  uri                     = aws_lambda_function.register.invoke_arn

  request_parameters = {
    "integration.request.path.eventType" = "method.request.path.eventType"
  }
}

# OPTIONS /events/{eventType}/register (CORS preflight)
resource "aws_api_gateway_method" "event_register_options" {
  rest_api_id   = aws_api_gateway_rest_api.main.id
  resource_id   = aws_api_gateway_resource.event_register.id
  http_method   = "OPTIONS"
  authorization = "NONE"
}

resource "aws_api_gateway_integration" "event_register_options" {
  rest_api_id       = aws_api_gateway_rest_api.main.id
  resource_id       = aws_api_gateway_resource.event_register.id
  http_method       = aws_api_gateway_method.event_register_options.http_method
  type              = "MOCK"
  request_templates = { "application/json" = "{\"statusCode\": 200}" }
}

resource "aws_api_gateway_method_response" "event_register_options_200" {
  rest_api_id = aws_api_gateway_rest_api.main.id
  resource_id = aws_api_gateway_resource.event_register.id
  http_method = aws_api_gateway_method.event_register_options.http_method
  status_code = "200"
  response_parameters = {
    "method.response.header.Access-Control-Allow-Headers" = true
    "method.response.header.Access-Control-Allow-Methods" = true
    "method.response.header.Access-Control-Allow-Origin"  = true
  }
}

resource "aws_api_gateway_integration_response" "event_register_options" {
  rest_api_id         = aws_api_gateway_rest_api.main.id
  resource_id         = aws_api_gateway_resource.event_register.id
  http_method         = aws_api_gateway_method.event_register_options.http_method
  status_code         = aws_api_gateway_method_response.event_register_options_200.status_code
  response_parameters = local.cors_headers
}

# ─────────────────────────────────────────────────────────────────────────────
# GET /events/{eventType}/availability  (public, no auth)
# Celebrity Event registration / dinner / seat-selection availability summary
# for external consumers (e.g. Kaira AI). Aggregate counts only.
# ─────────────────────────────────────────────────────────────────────────────

resource "aws_lambda_permission" "get_event_availability" {
  statement_id  = "AllowAPIGatewayInvokeGetEventAvailability"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.get_event_availability.function_name
  principal     = "apigateway.amazonaws.com"
  source_arn    = "${aws_api_gateway_rest_api.main.execution_arn}/*/*"
}

resource "aws_api_gateway_resource" "event_availability" {
  rest_api_id = aws_api_gateway_rest_api.main.id
  parent_id   = aws_api_gateway_resource.event_type.id
  path_part   = "availability"
}

resource "aws_api_gateway_method" "event_availability_get" {
  rest_api_id   = aws_api_gateway_rest_api.main.id
  resource_id   = aws_api_gateway_resource.event_availability.id
  http_method   = "GET"
  authorization = "NONE"
}

resource "aws_api_gateway_integration" "event_availability_get" {
  rest_api_id             = aws_api_gateway_rest_api.main.id
  resource_id             = aws_api_gateway_resource.event_availability.id
  http_method             = aws_api_gateway_method.event_availability_get.http_method
  integration_http_method = "POST"
  type                    = "AWS_PROXY"
  uri                     = aws_lambda_function.get_event_availability.invoke_arn
}

resource "aws_api_gateway_method" "event_availability_options" {
  rest_api_id   = aws_api_gateway_rest_api.main.id
  resource_id   = aws_api_gateway_resource.event_availability.id
  http_method   = "OPTIONS"
  authorization = "NONE"
}

resource "aws_api_gateway_integration" "event_availability_options" {
  rest_api_id       = aws_api_gateway_rest_api.main.id
  resource_id       = aws_api_gateway_resource.event_availability.id
  http_method       = aws_api_gateway_method.event_availability_options.http_method
  type              = "MOCK"
  request_templates = { "application/json" = "{\"statusCode\": 200}" }
}

resource "aws_api_gateway_method_response" "event_availability_options_200" {
  rest_api_id = aws_api_gateway_rest_api.main.id
  resource_id = aws_api_gateway_resource.event_availability.id
  http_method = aws_api_gateway_method.event_availability_options.http_method
  status_code = "200"
  response_parameters = {
    "method.response.header.Access-Control-Allow-Headers" = true
    "method.response.header.Access-Control-Allow-Methods" = true
    "method.response.header.Access-Control-Allow-Origin"  = true
  }
}

resource "aws_api_gateway_integration_response" "event_availability_options" {
  rest_api_id         = aws_api_gateway_rest_api.main.id
  resource_id         = aws_api_gateway_resource.event_availability.id
  http_method         = aws_api_gateway_method.event_availability_options.http_method
  status_code         = aws_api_gateway_method_response.event_availability_options_200.status_code
  response_parameters = local.cors_headers
  depends_on          = [aws_api_gateway_integration.event_availability_options]
}

# ─────────────────────────────────────────────────────────────────────────────
# /auth/request-otp  &  /auth/verify-otp  (public — email OTP login gate)
# ─────────────────────────────────────────────────────────────────────────────

resource "aws_api_gateway_resource" "auth" {
  rest_api_id = aws_api_gateway_rest_api.main.id
  parent_id   = aws_api_gateway_rest_api.main.root_resource_id
  path_part   = "auth"
}

# One Lambda serves both auth routes; permit API Gateway to invoke it once.
resource "aws_lambda_permission" "auth_otp" {
  statement_id  = "AllowAPIGatewayInvokeAuthOtp"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.auth_otp.function_name
  principal     = "apigateway.amazonaws.com"
  source_arn    = "${aws_api_gateway_rest_api.main.execution_arn}/*/*"
}

# --- /auth/request-otp ---
resource "aws_api_gateway_resource" "auth_request_otp" {
  rest_api_id = aws_api_gateway_rest_api.main.id
  parent_id   = aws_api_gateway_resource.auth.id
  path_part   = "request-otp"
}

resource "aws_api_gateway_method" "auth_request_otp_post" {
  rest_api_id   = aws_api_gateway_rest_api.main.id
  resource_id   = aws_api_gateway_resource.auth_request_otp.id
  http_method   = "POST"
  authorization = "NONE"
}

resource "aws_api_gateway_integration" "auth_request_otp_post" {
  rest_api_id             = aws_api_gateway_rest_api.main.id
  resource_id             = aws_api_gateway_resource.auth_request_otp.id
  http_method             = aws_api_gateway_method.auth_request_otp_post.http_method
  integration_http_method = "POST"
  type                    = "AWS_PROXY"
  uri                     = aws_lambda_function.auth_otp.invoke_arn
}

resource "aws_api_gateway_method" "auth_request_otp_options" {
  rest_api_id   = aws_api_gateway_rest_api.main.id
  resource_id   = aws_api_gateway_resource.auth_request_otp.id
  http_method   = "OPTIONS"
  authorization = "NONE"
}

resource "aws_api_gateway_integration" "auth_request_otp_options" {
  rest_api_id       = aws_api_gateway_rest_api.main.id
  resource_id       = aws_api_gateway_resource.auth_request_otp.id
  http_method       = aws_api_gateway_method.auth_request_otp_options.http_method
  type              = "MOCK"
  request_templates = { "application/json" = "{\"statusCode\": 200}" }
}

resource "aws_api_gateway_method_response" "auth_request_otp_options_200" {
  rest_api_id = aws_api_gateway_rest_api.main.id
  resource_id = aws_api_gateway_resource.auth_request_otp.id
  http_method = aws_api_gateway_method.auth_request_otp_options.http_method
  status_code = "200"
  response_parameters = {
    "method.response.header.Access-Control-Allow-Headers" = true
    "method.response.header.Access-Control-Allow-Methods" = true
    "method.response.header.Access-Control-Allow-Origin"  = true
  }
}

resource "aws_api_gateway_integration_response" "auth_request_otp_options" {
  rest_api_id         = aws_api_gateway_rest_api.main.id
  resource_id         = aws_api_gateway_resource.auth_request_otp.id
  http_method         = aws_api_gateway_method.auth_request_otp_options.http_method
  status_code         = aws_api_gateway_method_response.auth_request_otp_options_200.status_code
  response_parameters = local.cors_headers
}

# --- /auth/verify-otp ---
resource "aws_api_gateway_resource" "auth_verify_otp" {
  rest_api_id = aws_api_gateway_rest_api.main.id
  parent_id   = aws_api_gateway_resource.auth.id
  path_part   = "verify-otp"
}

resource "aws_api_gateway_method" "auth_verify_otp_post" {
  rest_api_id   = aws_api_gateway_rest_api.main.id
  resource_id   = aws_api_gateway_resource.auth_verify_otp.id
  http_method   = "POST"
  authorization = "NONE"
}

resource "aws_api_gateway_integration" "auth_verify_otp_post" {
  rest_api_id             = aws_api_gateway_rest_api.main.id
  resource_id             = aws_api_gateway_resource.auth_verify_otp.id
  http_method             = aws_api_gateway_method.auth_verify_otp_post.http_method
  integration_http_method = "POST"
  type                    = "AWS_PROXY"
  uri                     = aws_lambda_function.auth_otp.invoke_arn
}

resource "aws_api_gateway_method" "auth_verify_otp_options" {
  rest_api_id   = aws_api_gateway_rest_api.main.id
  resource_id   = aws_api_gateway_resource.auth_verify_otp.id
  http_method   = "OPTIONS"
  authorization = "NONE"
}

resource "aws_api_gateway_integration" "auth_verify_otp_options" {
  rest_api_id       = aws_api_gateway_rest_api.main.id
  resource_id       = aws_api_gateway_resource.auth_verify_otp.id
  http_method       = aws_api_gateway_method.auth_verify_otp_options.http_method
  type              = "MOCK"
  request_templates = { "application/json" = "{\"statusCode\": 200}" }
}

resource "aws_api_gateway_method_response" "auth_verify_otp_options_200" {
  rest_api_id = aws_api_gateway_rest_api.main.id
  resource_id = aws_api_gateway_resource.auth_verify_otp.id
  http_method = aws_api_gateway_method.auth_verify_otp_options.http_method
  status_code = "200"
  response_parameters = {
    "method.response.header.Access-Control-Allow-Headers" = true
    "method.response.header.Access-Control-Allow-Methods" = true
    "method.response.header.Access-Control-Allow-Origin"  = true
  }
}

resource "aws_api_gateway_integration_response" "auth_verify_otp_options" {
  rest_api_id         = aws_api_gateway_rest_api.main.id
  resource_id         = aws_api_gateway_resource.auth_verify_otp.id
  http_method         = aws_api_gateway_method.auth_verify_otp_options.http_method
  status_code         = aws_api_gateway_method_response.auth_verify_otp_options_200.status_code
  response_parameters = local.cors_headers
}

# ─────────────────────────────────────────────────────────────────────────────
# /membership/status  (public route, but gated by the reg token in-Lambda)
# ─────────────────────────────────────────────────────────────────────────────

resource "aws_api_gateway_resource" "membership" {
  rest_api_id = aws_api_gateway_rest_api.main.id
  parent_id   = aws_api_gateway_rest_api.main.root_resource_id
  path_part   = "membership"
}

resource "aws_api_gateway_resource" "membership_status" {
  rest_api_id = aws_api_gateway_rest_api.main.id
  parent_id   = aws_api_gateway_resource.membership.id
  path_part   = "status"
}

# The reg token rides in the Authorization header; the Lambda verifies it, so
# API Gateway authorization stays NONE (it isn't a Cognito credential).
resource "aws_api_gateway_method" "membership_status_get" {
  rest_api_id   = aws_api_gateway_rest_api.main.id
  resource_id   = aws_api_gateway_resource.membership_status.id
  http_method   = "GET"
  authorization = "NONE"
}

resource "aws_api_gateway_integration" "membership_status_get" {
  rest_api_id             = aws_api_gateway_rest_api.main.id
  resource_id             = aws_api_gateway_resource.membership_status.id
  http_method             = aws_api_gateway_method.membership_status_get.http_method
  integration_http_method = "POST"
  type                    = "AWS_PROXY"
  uri                     = aws_lambda_function.membership.invoke_arn
}

resource "aws_lambda_permission" "membership" {
  statement_id  = "AllowAPIGatewayInvokeMembership"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.membership.function_name
  principal     = "apigateway.amazonaws.com"
  source_arn    = "${aws_api_gateway_rest_api.main.execution_arn}/*/*"
}

resource "aws_api_gateway_method" "membership_status_options" {
  rest_api_id   = aws_api_gateway_rest_api.main.id
  resource_id   = aws_api_gateway_resource.membership_status.id
  http_method   = "OPTIONS"
  authorization = "NONE"
}

resource "aws_api_gateway_integration" "membership_status_options" {
  rest_api_id       = aws_api_gateway_rest_api.main.id
  resource_id       = aws_api_gateway_resource.membership_status.id
  http_method       = aws_api_gateway_method.membership_status_options.http_method
  type              = "MOCK"
  request_templates = { "application/json" = "{\"statusCode\": 200}" }
}

resource "aws_api_gateway_method_response" "membership_status_options_200" {
  rest_api_id = aws_api_gateway_rest_api.main.id
  resource_id = aws_api_gateway_resource.membership_status.id
  http_method = aws_api_gateway_method.membership_status_options.http_method
  status_code = "200"
  response_parameters = {
    "method.response.header.Access-Control-Allow-Headers" = true
    "method.response.header.Access-Control-Allow-Methods" = true
    "method.response.header.Access-Control-Allow-Origin"  = true
  }
}

resource "aws_api_gateway_integration_response" "membership_status_options" {
  rest_api_id         = aws_api_gateway_rest_api.main.id
  resource_id         = aws_api_gateway_resource.membership_status.id
  http_method         = aws_api_gateway_method.membership_status_options.http_method
  status_code         = aws_api_gateway_method_response.membership_status_options_200.status_code
  response_parameters = local.cors_headers
}

# /membership/apply — My Page: the signed-in member joins or renews NPO
# membership (reg token verified in-Lambda, like /membership/status).
resource "aws_api_gateway_resource" "membership_apply" {
  rest_api_id = aws_api_gateway_rest_api.main.id
  parent_id   = aws_api_gateway_resource.membership.id
  path_part   = "apply"
}

resource "aws_api_gateway_method" "membership_apply_post" {
  rest_api_id   = aws_api_gateway_rest_api.main.id
  resource_id   = aws_api_gateway_resource.membership_apply.id
  http_method   = "POST"
  authorization = "NONE"
}

resource "aws_api_gateway_integration" "membership_apply_post" {
  rest_api_id             = aws_api_gateway_rest_api.main.id
  resource_id             = aws_api_gateway_resource.membership_apply.id
  http_method             = aws_api_gateway_method.membership_apply_post.http_method
  integration_http_method = "POST"
  type                    = "AWS_PROXY"
  uri                     = aws_lambda_function.membership.invoke_arn
}

resource "aws_api_gateway_method" "membership_apply_options" {
  rest_api_id   = aws_api_gateway_rest_api.main.id
  resource_id   = aws_api_gateway_resource.membership_apply.id
  http_method   = "OPTIONS"
  authorization = "NONE"
}

resource "aws_api_gateway_integration" "membership_apply_options" {
  rest_api_id       = aws_api_gateway_rest_api.main.id
  resource_id       = aws_api_gateway_resource.membership_apply.id
  http_method       = aws_api_gateway_method.membership_apply_options.http_method
  type              = "MOCK"
  request_templates = { "application/json" = "{\"statusCode\": 200}" }
}

resource "aws_api_gateway_method_response" "membership_apply_options_200" {
  rest_api_id = aws_api_gateway_rest_api.main.id
  resource_id = aws_api_gateway_resource.membership_apply.id
  http_method = aws_api_gateway_method.membership_apply_options.http_method
  status_code = "200"
  response_parameters = {
    "method.response.header.Access-Control-Allow-Headers" = true
    "method.response.header.Access-Control-Allow-Methods" = true
    "method.response.header.Access-Control-Allow-Origin"  = true
  }
}

resource "aws_api_gateway_integration_response" "membership_apply_options" {
  rest_api_id         = aws_api_gateway_rest_api.main.id
  resource_id         = aws_api_gateway_resource.membership_apply.id
  http_method         = aws_api_gateway_method.membership_apply_options.http_method
  status_code         = aws_api_gateway_method_response.membership_apply_options_200.status_code
  response_parameters = local.cors_headers
}

# /membership/agm — My Page: register for / update / cancel AGM attendance
# (reg token verified in-Lambda).
resource "aws_api_gateway_resource" "membership_agm" {
  rest_api_id = aws_api_gateway_rest_api.main.id
  parent_id   = aws_api_gateway_resource.membership.id
  path_part   = "agm"
}

resource "aws_api_gateway_method" "membership_agm_post" {
  rest_api_id   = aws_api_gateway_rest_api.main.id
  resource_id   = aws_api_gateway_resource.membership_agm.id
  http_method   = "POST"
  authorization = "NONE"
}

resource "aws_api_gateway_integration" "membership_agm_post" {
  rest_api_id             = aws_api_gateway_rest_api.main.id
  resource_id             = aws_api_gateway_resource.membership_agm.id
  http_method             = aws_api_gateway_method.membership_agm_post.http_method
  integration_http_method = "POST"
  type                    = "AWS_PROXY"
  uri                     = aws_lambda_function.membership.invoke_arn
}

resource "aws_api_gateway_method" "membership_agm_options" {
  rest_api_id   = aws_api_gateway_rest_api.main.id
  resource_id   = aws_api_gateway_resource.membership_agm.id
  http_method   = "OPTIONS"
  authorization = "NONE"
}

resource "aws_api_gateway_integration" "membership_agm_options" {
  rest_api_id       = aws_api_gateway_rest_api.main.id
  resource_id       = aws_api_gateway_resource.membership_agm.id
  http_method       = aws_api_gateway_method.membership_agm_options.http_method
  type              = "MOCK"
  request_templates = { "application/json" = "{\"statusCode\": 200}" }
}

resource "aws_api_gateway_method_response" "membership_agm_options_200" {
  rest_api_id = aws_api_gateway_rest_api.main.id
  resource_id = aws_api_gateway_resource.membership_agm.id
  http_method = aws_api_gateway_method.membership_agm_options.http_method
  status_code = "200"
  response_parameters = {
    "method.response.header.Access-Control-Allow-Headers" = true
    "method.response.header.Access-Control-Allow-Methods" = true
    "method.response.header.Access-Control-Allow-Origin"  = true
  }
}

resource "aws_api_gateway_integration_response" "membership_agm_options" {
  rest_api_id         = aws_api_gateway_rest_api.main.id
  resource_id         = aws_api_gateway_resource.membership_agm.id
  http_method         = aws_api_gateway_method.membership_agm_options.http_method
  status_code         = aws_api_gateway_method_response.membership_agm_options_200.status_code
  response_parameters = local.cors_headers
}

# /membership/check — public, unauthenticated boolean membership lookup used to
# preview the per-adult NPO discount for additional adults during registration.
resource "aws_api_gateway_resource" "membership_check" {
  rest_api_id = aws_api_gateway_rest_api.main.id
  parent_id   = aws_api_gateway_resource.membership.id
  path_part   = "check"
}

resource "aws_api_gateway_method" "membership_check_post" {
  rest_api_id   = aws_api_gateway_rest_api.main.id
  resource_id   = aws_api_gateway_resource.membership_check.id
  http_method   = "POST"
  authorization = "NONE"
}

resource "aws_api_gateway_integration" "membership_check_post" {
  rest_api_id             = aws_api_gateway_rest_api.main.id
  resource_id             = aws_api_gateway_resource.membership_check.id
  http_method             = aws_api_gateway_method.membership_check_post.http_method
  integration_http_method = "POST"
  type                    = "AWS_PROXY"
  uri                     = aws_lambda_function.membership.invoke_arn
}

resource "aws_api_gateway_method" "membership_check_options" {
  rest_api_id   = aws_api_gateway_rest_api.main.id
  resource_id   = aws_api_gateway_resource.membership_check.id
  http_method   = "OPTIONS"
  authorization = "NONE"
}

resource "aws_api_gateway_integration" "membership_check_options" {
  rest_api_id       = aws_api_gateway_rest_api.main.id
  resource_id       = aws_api_gateway_resource.membership_check.id
  http_method       = aws_api_gateway_method.membership_check_options.http_method
  type              = "MOCK"
  request_templates = { "application/json" = "{\"statusCode\": 200}" }
}

resource "aws_api_gateway_method_response" "membership_check_options_200" {
  rest_api_id = aws_api_gateway_rest_api.main.id
  resource_id = aws_api_gateway_resource.membership_check.id
  http_method = aws_api_gateway_method.membership_check_options.http_method
  status_code = "200"
  response_parameters = {
    "method.response.header.Access-Control-Allow-Headers" = true
    "method.response.header.Access-Control-Allow-Methods" = true
    "method.response.header.Access-Control-Allow-Origin"  = true
  }
}

resource "aws_api_gateway_integration_response" "membership_check_options" {
  rest_api_id         = aws_api_gateway_rest_api.main.id
  resource_id         = aws_api_gateway_resource.membership_check.id
  http_method         = aws_api_gateway_method.membership_check_options.http_method
  status_code         = aws_api_gateway_method_response.membership_check_options_200.status_code
  response_parameters = local.cors_headers
}

# ─────────────────────────────────────────────────────────────────────────────
# /my/registrations (+ item)  — registrant self-service, gated by reg token
# in-Lambda. API Gateway authorization stays NONE (the reg token isn't a
# Cognito credential); the Lambda verifies it and scopes results to the caller.
# ─────────────────────────────────────────────────────────────────────────────

resource "aws_api_gateway_resource" "my" {
  rest_api_id = aws_api_gateway_rest_api.main.id
  parent_id   = aws_api_gateway_rest_api.main.root_resource_id
  path_part   = "my"
}

resource "aws_api_gateway_resource" "my_registrations" {
  rest_api_id = aws_api_gateway_rest_api.main.id
  parent_id   = aws_api_gateway_resource.my.id
  path_part   = "registrations"
}

resource "aws_lambda_permission" "my_registrations" {
  statement_id  = "AllowAPIGatewayInvokeMyRegistrations"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.my_registrations.function_name
  principal     = "apigateway.amazonaws.com"
  source_arn    = "${aws_api_gateway_rest_api.main.execution_arn}/*/*"
}

# GET /my/registrations
resource "aws_api_gateway_method" "my_registrations_get" {
  rest_api_id   = aws_api_gateway_rest_api.main.id
  resource_id   = aws_api_gateway_resource.my_registrations.id
  http_method   = "GET"
  authorization = "NONE"
}

resource "aws_api_gateway_integration" "my_registrations_get" {
  rest_api_id             = aws_api_gateway_rest_api.main.id
  resource_id             = aws_api_gateway_resource.my_registrations.id
  http_method             = aws_api_gateway_method.my_registrations_get.http_method
  integration_http_method = "POST"
  type                    = "AWS_PROXY"
  uri                     = aws_lambda_function.my_registrations.invoke_arn
}

# OPTIONS /my/registrations
resource "aws_api_gateway_method" "my_registrations_options" {
  rest_api_id   = aws_api_gateway_rest_api.main.id
  resource_id   = aws_api_gateway_resource.my_registrations.id
  http_method   = "OPTIONS"
  authorization = "NONE"
}

resource "aws_api_gateway_integration" "my_registrations_options" {
  rest_api_id       = aws_api_gateway_rest_api.main.id
  resource_id       = aws_api_gateway_resource.my_registrations.id
  http_method       = aws_api_gateway_method.my_registrations_options.http_method
  type              = "MOCK"
  request_templates = { "application/json" = "{\"statusCode\": 200}" }
}

resource "aws_api_gateway_method_response" "my_registrations_options_200" {
  rest_api_id = aws_api_gateway_rest_api.main.id
  resource_id = aws_api_gateway_resource.my_registrations.id
  http_method = aws_api_gateway_method.my_registrations_options.http_method
  status_code = "200"
  response_parameters = {
    "method.response.header.Access-Control-Allow-Headers" = true
    "method.response.header.Access-Control-Allow-Methods" = true
    "method.response.header.Access-Control-Allow-Origin"  = true
  }
}

resource "aws_api_gateway_integration_response" "my_registrations_options" {
  rest_api_id         = aws_api_gateway_rest_api.main.id
  resource_id         = aws_api_gateway_resource.my_registrations.id
  http_method         = aws_api_gateway_method.my_registrations_options.http_method
  status_code         = aws_api_gateway_method_response.my_registrations_options_200.status_code
  response_parameters = local.cors_headers
}

# /my/registrations/{registrationId}
resource "aws_api_gateway_resource" "my_registration_item" {
  rest_api_id = aws_api_gateway_rest_api.main.id
  parent_id   = aws_api_gateway_resource.my_registrations.id
  path_part   = "{registrationId}"
}

# PUT /my/registrations/{registrationId}
resource "aws_api_gateway_method" "my_registration_put" {
  rest_api_id   = aws_api_gateway_rest_api.main.id
  resource_id   = aws_api_gateway_resource.my_registration_item.id
  http_method   = "PUT"
  authorization = "NONE"
}

resource "aws_api_gateway_integration" "my_registration_put" {
  rest_api_id             = aws_api_gateway_rest_api.main.id
  resource_id             = aws_api_gateway_resource.my_registration_item.id
  http_method             = aws_api_gateway_method.my_registration_put.http_method
  integration_http_method = "POST"
  type                    = "AWS_PROXY"
  uri                     = aws_lambda_function.my_registrations.invoke_arn
}

# DELETE /my/registrations/{registrationId}
resource "aws_api_gateway_method" "my_registration_delete" {
  rest_api_id   = aws_api_gateway_rest_api.main.id
  resource_id   = aws_api_gateway_resource.my_registration_item.id
  http_method   = "DELETE"
  authorization = "NONE"
}

resource "aws_api_gateway_integration" "my_registration_delete" {
  rest_api_id             = aws_api_gateway_rest_api.main.id
  resource_id             = aws_api_gateway_resource.my_registration_item.id
  http_method             = aws_api_gateway_method.my_registration_delete.http_method
  integration_http_method = "POST"
  type                    = "AWS_PROXY"
  uri                     = aws_lambda_function.my_registrations.invoke_arn
}

# OPTIONS /my/registrations/{registrationId}
resource "aws_api_gateway_method" "my_registration_item_options" {
  rest_api_id   = aws_api_gateway_rest_api.main.id
  resource_id   = aws_api_gateway_resource.my_registration_item.id
  http_method   = "OPTIONS"
  authorization = "NONE"
}

resource "aws_api_gateway_integration" "my_registration_item_options" {
  rest_api_id       = aws_api_gateway_rest_api.main.id
  resource_id       = aws_api_gateway_resource.my_registration_item.id
  http_method       = aws_api_gateway_method.my_registration_item_options.http_method
  type              = "MOCK"
  request_templates = { "application/json" = "{\"statusCode\": 200}" }
}

resource "aws_api_gateway_method_response" "my_registration_item_options_200" {
  rest_api_id = aws_api_gateway_rest_api.main.id
  resource_id = aws_api_gateway_resource.my_registration_item.id
  http_method = aws_api_gateway_method.my_registration_item_options.http_method
  status_code = "200"
  response_parameters = {
    "method.response.header.Access-Control-Allow-Headers" = true
    "method.response.header.Access-Control-Allow-Methods" = true
    "method.response.header.Access-Control-Allow-Origin"  = true
  }
}

resource "aws_api_gateway_integration_response" "my_registration_item_options" {
  rest_api_id         = aws_api_gateway_rest_api.main.id
  resource_id         = aws_api_gateway_resource.my_registration_item.id
  http_method         = aws_api_gateway_method.my_registration_item_options.http_method
  status_code         = aws_api_gateway_method_response.my_registration_item_options_200.status_code
  response_parameters = local.cors_headers
}

# /my/registrations/{registrationId}/slot — change entry slot regardless of
# payment status (verified against the reg token in-Lambda, same as the item route).
resource "aws_api_gateway_resource" "my_registration_item_slot" {
  rest_api_id = aws_api_gateway_rest_api.main.id
  parent_id   = aws_api_gateway_resource.my_registration_item.id
  path_part   = "slot"
}

resource "aws_api_gateway_method" "my_registration_slot_put" {
  rest_api_id   = aws_api_gateway_rest_api.main.id
  resource_id   = aws_api_gateway_resource.my_registration_item_slot.id
  http_method   = "PUT"
  authorization = "NONE"
}

resource "aws_api_gateway_integration" "my_registration_slot_put" {
  rest_api_id             = aws_api_gateway_rest_api.main.id
  resource_id             = aws_api_gateway_resource.my_registration_item_slot.id
  http_method             = aws_api_gateway_method.my_registration_slot_put.http_method
  integration_http_method = "POST"
  type                    = "AWS_PROXY"
  uri                     = aws_lambda_function.my_registrations.invoke_arn
}

resource "aws_api_gateway_method" "my_registration_slot_options" {
  rest_api_id   = aws_api_gateway_rest_api.main.id
  resource_id   = aws_api_gateway_resource.my_registration_item_slot.id
  http_method   = "OPTIONS"
  authorization = "NONE"
}

resource "aws_api_gateway_integration" "my_registration_slot_options" {
  rest_api_id       = aws_api_gateway_rest_api.main.id
  resource_id       = aws_api_gateway_resource.my_registration_item_slot.id
  http_method       = aws_api_gateway_method.my_registration_slot_options.http_method
  type              = "MOCK"
  request_templates = { "application/json" = "{\"statusCode\": 200}" }
}

resource "aws_api_gateway_method_response" "my_registration_slot_options_200" {
  rest_api_id = aws_api_gateway_rest_api.main.id
  resource_id = aws_api_gateway_resource.my_registration_item_slot.id
  http_method = aws_api_gateway_method.my_registration_slot_options.http_method
  status_code = "200"
  response_parameters = {
    "method.response.header.Access-Control-Allow-Headers" = true
    "method.response.header.Access-Control-Allow-Methods" = true
    "method.response.header.Access-Control-Allow-Origin"  = true
  }
}

resource "aws_api_gateway_integration_response" "my_registration_slot_options" {
  rest_api_id         = aws_api_gateway_rest_api.main.id
  resource_id         = aws_api_gateway_resource.my_registration_item_slot.id
  http_method         = aws_api_gateway_method.my_registration_slot_options.http_method
  status_code         = aws_api_gateway_method_response.my_registration_slot_options_200.status_code
  response_parameters = local.cors_headers
}

# /my/registrations/{registrationId}/celebrity-dinner — view/set/cancel the
# Celebrity Dinner sign-up tied to this registration (verified against the reg
# token in-Lambda, same as the item route). Never touches the registrations
# table itself.
resource "aws_api_gateway_resource" "my_registration_item_celebrity_dinner" {
  rest_api_id = aws_api_gateway_rest_api.main.id
  parent_id   = aws_api_gateway_resource.my_registration_item.id
  path_part   = "celebrity-dinner"
}

resource "aws_api_gateway_method" "my_registration_celebrity_dinner_get" {
  rest_api_id   = aws_api_gateway_rest_api.main.id
  resource_id   = aws_api_gateway_resource.my_registration_item_celebrity_dinner.id
  http_method   = "GET"
  authorization = "NONE"
}

resource "aws_api_gateway_integration" "my_registration_celebrity_dinner_get" {
  rest_api_id             = aws_api_gateway_rest_api.main.id
  resource_id             = aws_api_gateway_resource.my_registration_item_celebrity_dinner.id
  http_method             = aws_api_gateway_method.my_registration_celebrity_dinner_get.http_method
  integration_http_method = "POST"
  type                    = "AWS_PROXY"
  uri                     = aws_lambda_function.my_registrations.invoke_arn
}

resource "aws_api_gateway_method" "my_registration_celebrity_dinner_put" {
  rest_api_id   = aws_api_gateway_rest_api.main.id
  resource_id   = aws_api_gateway_resource.my_registration_item_celebrity_dinner.id
  http_method   = "PUT"
  authorization = "NONE"
}

resource "aws_api_gateway_integration" "my_registration_celebrity_dinner_put" {
  rest_api_id             = aws_api_gateway_rest_api.main.id
  resource_id             = aws_api_gateway_resource.my_registration_item_celebrity_dinner.id
  http_method             = aws_api_gateway_method.my_registration_celebrity_dinner_put.http_method
  integration_http_method = "POST"
  type                    = "AWS_PROXY"
  uri                     = aws_lambda_function.my_registrations.invoke_arn
}

resource "aws_api_gateway_method" "my_registration_celebrity_dinner_delete" {
  rest_api_id   = aws_api_gateway_rest_api.main.id
  resource_id   = aws_api_gateway_resource.my_registration_item_celebrity_dinner.id
  http_method   = "DELETE"
  authorization = "NONE"
}

resource "aws_api_gateway_integration" "my_registration_celebrity_dinner_delete" {
  rest_api_id             = aws_api_gateway_rest_api.main.id
  resource_id             = aws_api_gateway_resource.my_registration_item_celebrity_dinner.id
  http_method             = aws_api_gateway_method.my_registration_celebrity_dinner_delete.http_method
  integration_http_method = "POST"
  type                    = "AWS_PROXY"
  uri                     = aws_lambda_function.my_registrations.invoke_arn
}

resource "aws_api_gateway_method" "my_registration_celebrity_dinner_options" {
  rest_api_id   = aws_api_gateway_rest_api.main.id
  resource_id   = aws_api_gateway_resource.my_registration_item_celebrity_dinner.id
  http_method   = "OPTIONS"
  authorization = "NONE"
}

resource "aws_api_gateway_integration" "my_registration_celebrity_dinner_options" {
  rest_api_id       = aws_api_gateway_rest_api.main.id
  resource_id       = aws_api_gateway_resource.my_registration_item_celebrity_dinner.id
  http_method       = aws_api_gateway_method.my_registration_celebrity_dinner_options.http_method
  type              = "MOCK"
  request_templates = { "application/json" = "{\"statusCode\": 200}" }
}

resource "aws_api_gateway_method_response" "my_registration_celebrity_dinner_options_200" {
  rest_api_id = aws_api_gateway_rest_api.main.id
  resource_id = aws_api_gateway_resource.my_registration_item_celebrity_dinner.id
  http_method = aws_api_gateway_method.my_registration_celebrity_dinner_options.http_method
  status_code = "200"
  response_parameters = {
    "method.response.header.Access-Control-Allow-Headers" = true
    "method.response.header.Access-Control-Allow-Methods" = true
    "method.response.header.Access-Control-Allow-Origin"  = true
  }
}

resource "aws_api_gateway_integration_response" "my_registration_celebrity_dinner_options" {
  rest_api_id         = aws_api_gateway_rest_api.main.id
  resource_id         = aws_api_gateway_resource.my_registration_item_celebrity_dinner.id
  http_method         = aws_api_gateway_method.my_registration_celebrity_dinner_options.http_method
  status_code         = aws_api_gateway_method_response.my_registration_celebrity_dinner_options_200.status_code
  response_parameters = local.cors_headers
}

# /my/registrations/{registrationId}/celebrity-dinner/checkout — starts a
# hosted PayPay/Square checkout for a standalone (non add-on) dinner sign-up.
resource "aws_api_gateway_resource" "my_registration_celebrity_dinner_checkout" {
  rest_api_id = aws_api_gateway_rest_api.main.id
  parent_id   = aws_api_gateway_resource.my_registration_item_celebrity_dinner.id
  path_part   = "checkout"
}

resource "aws_api_gateway_method" "my_registration_celebrity_dinner_checkout_post" {
  rest_api_id   = aws_api_gateway_rest_api.main.id
  resource_id   = aws_api_gateway_resource.my_registration_celebrity_dinner_checkout.id
  http_method   = "POST"
  authorization = "NONE"
}

resource "aws_api_gateway_integration" "my_registration_celebrity_dinner_checkout_post" {
  rest_api_id             = aws_api_gateway_rest_api.main.id
  resource_id             = aws_api_gateway_resource.my_registration_celebrity_dinner_checkout.id
  http_method             = aws_api_gateway_method.my_registration_celebrity_dinner_checkout_post.http_method
  integration_http_method = "POST"
  type                    = "AWS_PROXY"
  uri                     = aws_lambda_function.my_registrations.invoke_arn
}

resource "aws_api_gateway_method" "my_registration_celebrity_dinner_checkout_options" {
  rest_api_id   = aws_api_gateway_rest_api.main.id
  resource_id   = aws_api_gateway_resource.my_registration_celebrity_dinner_checkout.id
  http_method   = "OPTIONS"
  authorization = "NONE"
}

resource "aws_api_gateway_integration" "my_registration_celebrity_dinner_checkout_options" {
  rest_api_id       = aws_api_gateway_rest_api.main.id
  resource_id       = aws_api_gateway_resource.my_registration_celebrity_dinner_checkout.id
  http_method       = aws_api_gateway_method.my_registration_celebrity_dinner_checkout_options.http_method
  type              = "MOCK"
  request_templates = { "application/json" = "{\"statusCode\": 200}" }
}

resource "aws_api_gateway_method_response" "my_registration_celebrity_dinner_checkout_options_200" {
  rest_api_id = aws_api_gateway_rest_api.main.id
  resource_id = aws_api_gateway_resource.my_registration_celebrity_dinner_checkout.id
  http_method = aws_api_gateway_method.my_registration_celebrity_dinner_checkout_options.http_method
  status_code = "200"
  response_parameters = {
    "method.response.header.Access-Control-Allow-Headers" = true
    "method.response.header.Access-Control-Allow-Methods" = true
    "method.response.header.Access-Control-Allow-Origin"  = true
  }
}

resource "aws_api_gateway_integration_response" "my_registration_celebrity_dinner_checkout_options" {
  rest_api_id         = aws_api_gateway_rest_api.main.id
  resource_id         = aws_api_gateway_resource.my_registration_celebrity_dinner_checkout.id
  http_method         = aws_api_gateway_method.my_registration_celebrity_dinner_checkout_options.http_method
  status_code         = aws_api_gateway_method_response.my_registration_celebrity_dinner_checkout_options_200.status_code
  response_parameters = local.cors_headers
}

# /my/celebrity-dinner/{eventType} (+ checkout) — a Celebrity Dinner sign-up
# for someone with NO underlying event registration at all (a "guest"
# booking), gated by the same reg token as everything else under /my. Reuses
# the my_registrations Lambda (same handler dispatches on resource path) since
# celebrity_dinner_logic.py already treats registrationId as just an
# attribute — a guest booking simply leaves it blank. See
# get_active_guest_dinner_registration in celebrity_dinner_logic.py.
resource "aws_api_gateway_resource" "my_celebrity_dinner" {
  rest_api_id = aws_api_gateway_rest_api.main.id
  parent_id   = aws_api_gateway_resource.my.id
  path_part   = "celebrity-dinner"
}

resource "aws_api_gateway_resource" "my_celebrity_dinner_item" {
  rest_api_id = aws_api_gateway_rest_api.main.id
  parent_id   = aws_api_gateway_resource.my_celebrity_dinner.id
  path_part   = "{eventType}"
}

resource "aws_api_gateway_method" "my_celebrity_dinner_get" {
  rest_api_id   = aws_api_gateway_rest_api.main.id
  resource_id   = aws_api_gateway_resource.my_celebrity_dinner_item.id
  http_method   = "GET"
  authorization = "NONE"
}

resource "aws_api_gateway_integration" "my_celebrity_dinner_get" {
  rest_api_id             = aws_api_gateway_rest_api.main.id
  resource_id             = aws_api_gateway_resource.my_celebrity_dinner_item.id
  http_method             = aws_api_gateway_method.my_celebrity_dinner_get.http_method
  integration_http_method = "POST"
  type                    = "AWS_PROXY"
  uri                     = aws_lambda_function.my_registrations.invoke_arn
}

resource "aws_api_gateway_method" "my_celebrity_dinner_put" {
  rest_api_id   = aws_api_gateway_rest_api.main.id
  resource_id   = aws_api_gateway_resource.my_celebrity_dinner_item.id
  http_method   = "PUT"
  authorization = "NONE"
}

resource "aws_api_gateway_integration" "my_celebrity_dinner_put" {
  rest_api_id             = aws_api_gateway_rest_api.main.id
  resource_id             = aws_api_gateway_resource.my_celebrity_dinner_item.id
  http_method             = aws_api_gateway_method.my_celebrity_dinner_put.http_method
  integration_http_method = "POST"
  type                    = "AWS_PROXY"
  uri                     = aws_lambda_function.my_registrations.invoke_arn
}

resource "aws_api_gateway_method" "my_celebrity_dinner_delete" {
  rest_api_id   = aws_api_gateway_rest_api.main.id
  resource_id   = aws_api_gateway_resource.my_celebrity_dinner_item.id
  http_method   = "DELETE"
  authorization = "NONE"
}

resource "aws_api_gateway_integration" "my_celebrity_dinner_delete" {
  rest_api_id             = aws_api_gateway_rest_api.main.id
  resource_id             = aws_api_gateway_resource.my_celebrity_dinner_item.id
  http_method             = aws_api_gateway_method.my_celebrity_dinner_delete.http_method
  integration_http_method = "POST"
  type                    = "AWS_PROXY"
  uri                     = aws_lambda_function.my_registrations.invoke_arn
}

resource "aws_api_gateway_method" "my_celebrity_dinner_options" {
  rest_api_id   = aws_api_gateway_rest_api.main.id
  resource_id   = aws_api_gateway_resource.my_celebrity_dinner_item.id
  http_method   = "OPTIONS"
  authorization = "NONE"
}

resource "aws_api_gateway_integration" "my_celebrity_dinner_options" {
  rest_api_id       = aws_api_gateway_rest_api.main.id
  resource_id       = aws_api_gateway_resource.my_celebrity_dinner_item.id
  http_method       = aws_api_gateway_method.my_celebrity_dinner_options.http_method
  type              = "MOCK"
  request_templates = { "application/json" = "{\"statusCode\": 200}" }
}

resource "aws_api_gateway_method_response" "my_celebrity_dinner_options_200" {
  rest_api_id = aws_api_gateway_rest_api.main.id
  resource_id = aws_api_gateway_resource.my_celebrity_dinner_item.id
  http_method = aws_api_gateway_method.my_celebrity_dinner_options.http_method
  status_code = "200"
  response_parameters = {
    "method.response.header.Access-Control-Allow-Headers" = true
    "method.response.header.Access-Control-Allow-Methods" = true
    "method.response.header.Access-Control-Allow-Origin"  = true
  }
}

resource "aws_api_gateway_integration_response" "my_celebrity_dinner_options" {
  rest_api_id         = aws_api_gateway_rest_api.main.id
  resource_id         = aws_api_gateway_resource.my_celebrity_dinner_item.id
  http_method         = aws_api_gateway_method.my_celebrity_dinner_options.http_method
  status_code         = aws_api_gateway_method_response.my_celebrity_dinner_options_200.status_code
  response_parameters = local.cors_headers
}

# /my/celebrity-dinner/{eventType}/checkout
resource "aws_api_gateway_resource" "my_celebrity_dinner_checkout" {
  rest_api_id = aws_api_gateway_rest_api.main.id
  parent_id   = aws_api_gateway_resource.my_celebrity_dinner_item.id
  path_part   = "checkout"
}

resource "aws_api_gateway_method" "my_celebrity_dinner_checkout_post" {
  rest_api_id   = aws_api_gateway_rest_api.main.id
  resource_id   = aws_api_gateway_resource.my_celebrity_dinner_checkout.id
  http_method   = "POST"
  authorization = "NONE"
}

resource "aws_api_gateway_integration" "my_celebrity_dinner_checkout_post" {
  rest_api_id             = aws_api_gateway_rest_api.main.id
  resource_id             = aws_api_gateway_resource.my_celebrity_dinner_checkout.id
  http_method             = aws_api_gateway_method.my_celebrity_dinner_checkout_post.http_method
  integration_http_method = "POST"
  type                    = "AWS_PROXY"
  uri                     = aws_lambda_function.my_registrations.invoke_arn
}

resource "aws_api_gateway_method" "my_celebrity_dinner_checkout_options" {
  rest_api_id   = aws_api_gateway_rest_api.main.id
  resource_id   = aws_api_gateway_resource.my_celebrity_dinner_checkout.id
  http_method   = "OPTIONS"
  authorization = "NONE"
}

resource "aws_api_gateway_integration" "my_celebrity_dinner_checkout_options" {
  rest_api_id       = aws_api_gateway_rest_api.main.id
  resource_id       = aws_api_gateway_resource.my_celebrity_dinner_checkout.id
  http_method       = aws_api_gateway_method.my_celebrity_dinner_checkout_options.http_method
  type              = "MOCK"
  request_templates = { "application/json" = "{\"statusCode\": 200}" }
}

resource "aws_api_gateway_method_response" "my_celebrity_dinner_checkout_options_200" {
  rest_api_id = aws_api_gateway_rest_api.main.id
  resource_id = aws_api_gateway_resource.my_celebrity_dinner_checkout.id
  http_method = aws_api_gateway_method.my_celebrity_dinner_checkout_options.http_method
  status_code = "200"
  response_parameters = {
    "method.response.header.Access-Control-Allow-Headers" = true
    "method.response.header.Access-Control-Allow-Methods" = true
    "method.response.header.Access-Control-Allow-Origin"  = true
  }
}

resource "aws_api_gateway_integration_response" "my_celebrity_dinner_checkout_options" {
  rest_api_id         = aws_api_gateway_rest_api.main.id
  resource_id         = aws_api_gateway_resource.my_celebrity_dinner_checkout.id
  http_method         = aws_api_gateway_method.my_celebrity_dinner_checkout_options.http_method
  status_code         = aws_api_gateway_method_response.my_celebrity_dinner_checkout_options_200.status_code
  response_parameters = local.cors_headers
}

# Seat-upgrade self-service routes (/my/seat-upgrades, incl. checkout) moved
# to the plugin — see module "seat_upgrade" in plugin.tf.
