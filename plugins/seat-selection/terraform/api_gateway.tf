# ─────────────────────────────────────────────────────────────────────────────
# Seat map routes, mounted under the host's existing REST API:
#
#   {public_parent}/seats                GET         (public — seat map)
#   {admin_parent}/seats                 GET POST    (admin view + block/set-category)
#   {admin_parent}/seats/{seatId}        DELETE      (admin reset-to-default)
#   {admin_parent}/seat-pricing                    GET       (admin — all priced zones)
#   {admin_parent}/seat-pricing/{category}         PUT DELETE (admin — set/unprice one zone)
#   {admin_parent}/seat-purchases[/{purchaseId}]   GET PATCH (admin — list/get, mark paid/cancelled)
#   /webhooks/seat-selection/{provider}            POST      (public — payment provider webhook)
#
# AWS_PROXY integrations for the real methods, MOCK integrations for OPTIONS —
# same convention the host app itself uses. The host's own
# aws_api_gateway_deployment resource must depend on this module (and include
# `deployment_trigger_inputs`, below, in its trigger hash) so an apply
# actually redeploys the stage when these routes change.
# ─────────────────────────────────────────────────────────────────────────────

locals {
  cors_headers = {
    "method.response.header.Access-Control-Allow-Headers" = "'Content-Type,X-Amz-Date,Authorization,X-Api-Key'"
    "method.response.header.Access-Control-Allow-Methods" = "'OPTIONS,POST,PUT,PATCH,GET,DELETE'"
    "method.response.header.Access-Control-Allow-Origin"  = "'${var.cors_allowed_origin}'"
  }
}

# ── Lambda invoke permissions ────────────────────────────────────────────────

resource "aws_lambda_permission" "get_available_seats" {
  statement_id  = "AllowAPIGatewayInvokeGetAvailableSeats"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.get_available_seats.function_name
  principal     = "apigateway.amazonaws.com"
  source_arn    = "${var.rest_api_execution_arn}/*/*"
}

resource "aws_lambda_permission" "manage_seats" {
  statement_id  = "AllowAPIGatewayInvokeManageSeats"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.manage_seats.function_name
  principal     = "apigateway.amazonaws.com"
  source_arn    = "${var.rest_api_execution_arn}/*/*"
}

# ─────────────────────────────────────────────────────────────────────────────
# {public_parent}/seats  (public — GET the seat map)
# ─────────────────────────────────────────────────────────────────────────────

resource "aws_api_gateway_resource" "seats" {
  rest_api_id = var.rest_api_id
  parent_id   = var.public_parent_resource_id
  path_part   = "seats"
}

resource "aws_api_gateway_method" "seats_get" {
  rest_api_id   = var.rest_api_id
  resource_id   = aws_api_gateway_resource.seats.id
  http_method   = "GET"
  authorization = "NONE"
}

resource "aws_api_gateway_integration" "seats_get" {
  rest_api_id             = var.rest_api_id
  resource_id             = aws_api_gateway_resource.seats.id
  http_method             = aws_api_gateway_method.seats_get.http_method
  integration_http_method = "POST"
  type                    = "AWS_PROXY"
  uri                     = aws_lambda_function.get_available_seats.invoke_arn
}

resource "aws_api_gateway_method" "seats_options" {
  rest_api_id   = var.rest_api_id
  resource_id   = aws_api_gateway_resource.seats.id
  http_method   = "OPTIONS"
  authorization = "NONE"
}

resource "aws_api_gateway_integration" "seats_options" {
  rest_api_id       = var.rest_api_id
  resource_id       = aws_api_gateway_resource.seats.id
  http_method       = aws_api_gateway_method.seats_options.http_method
  type              = "MOCK"
  request_templates = { "application/json" = "{\"statusCode\": 200}" }
}

resource "aws_api_gateway_method_response" "seats_options_200" {
  rest_api_id = var.rest_api_id
  resource_id = aws_api_gateway_resource.seats.id
  http_method = aws_api_gateway_method.seats_options.http_method
  status_code = "200"
  response_parameters = {
    "method.response.header.Access-Control-Allow-Headers" = true
    "method.response.header.Access-Control-Allow-Methods" = true
    "method.response.header.Access-Control-Allow-Origin"  = true
  }
}

resource "aws_api_gateway_integration_response" "seats_options" {
  rest_api_id = var.rest_api_id
  resource_id = aws_api_gateway_resource.seats.id
  http_method = aws_api_gateway_method.seats_options.http_method
  # Terraform has no attribute-level edge to the integration below (this
  # resource only references the method/method_response), so without an
  # explicit depends_on it can race ahead of the integration's own PUT and
  # AWS returns 404 NotFoundException: Invalid Integration identifier —
  # exactly what happened deploying this module the first time.
  depends_on          = [aws_api_gateway_integration.seats_options]
  status_code         = aws_api_gateway_method_response.seats_options_200.status_code
  response_parameters = local.cors_headers
}

# ─────────────────────────────────────────────────────────────────────────────
# {admin_parent}/seats  (collection — GET view, POST block/set-category)
# ─────────────────────────────────────────────────────────────────────────────

resource "aws_api_gateway_resource" "admin_seats" {
  rest_api_id = var.rest_api_id
  parent_id   = var.admin_parent_resource_id
  path_part   = "seats"
}

resource "aws_api_gateway_method" "admin_seats_get" {
  rest_api_id   = var.rest_api_id
  resource_id   = aws_api_gateway_resource.admin_seats.id
  http_method   = "GET"
  authorization = "COGNITO_USER_POOLS"
  authorizer_id = var.cognito_authorizer_id
}

resource "aws_api_gateway_method" "admin_seats_post" {
  rest_api_id   = var.rest_api_id
  resource_id   = aws_api_gateway_resource.admin_seats.id
  http_method   = "POST"
  authorization = "COGNITO_USER_POOLS"
  authorizer_id = var.cognito_authorizer_id
}

resource "aws_api_gateway_integration" "admin_seats_get" {
  rest_api_id             = var.rest_api_id
  resource_id             = aws_api_gateway_resource.admin_seats.id
  http_method             = aws_api_gateway_method.admin_seats_get.http_method
  integration_http_method = "POST"
  type                    = "AWS_PROXY"
  uri                     = aws_lambda_function.manage_seats.invoke_arn
}

resource "aws_api_gateway_integration" "admin_seats_post" {
  rest_api_id             = var.rest_api_id
  resource_id             = aws_api_gateway_resource.admin_seats.id
  http_method             = aws_api_gateway_method.admin_seats_post.http_method
  integration_http_method = "POST"
  type                    = "AWS_PROXY"
  uri                     = aws_lambda_function.manage_seats.invoke_arn
}

resource "aws_api_gateway_method" "admin_seats_options" {
  rest_api_id   = var.rest_api_id
  resource_id   = aws_api_gateway_resource.admin_seats.id
  http_method   = "OPTIONS"
  authorization = "NONE"
}

resource "aws_api_gateway_integration" "admin_seats_options" {
  rest_api_id       = var.rest_api_id
  resource_id       = aws_api_gateway_resource.admin_seats.id
  http_method       = aws_api_gateway_method.admin_seats_options.http_method
  type              = "MOCK"
  request_templates = { "application/json" = "{\"statusCode\": 200}" }
}

resource "aws_api_gateway_method_response" "admin_seats_options_200" {
  rest_api_id = var.rest_api_id
  resource_id = aws_api_gateway_resource.admin_seats.id
  http_method = aws_api_gateway_method.admin_seats_options.http_method
  status_code = "200"
  response_parameters = {
    "method.response.header.Access-Control-Allow-Headers" = true
    "method.response.header.Access-Control-Allow-Methods" = true
    "method.response.header.Access-Control-Allow-Origin"  = true
  }
}

resource "aws_api_gateway_integration_response" "admin_seats_options" {
  depends_on          = [aws_api_gateway_integration.admin_seats_options] # see seats_options above for why
  rest_api_id         = var.rest_api_id
  resource_id         = aws_api_gateway_resource.admin_seats.id
  http_method         = aws_api_gateway_method.admin_seats_options.http_method
  status_code         = aws_api_gateway_method_response.admin_seats_options_200.status_code
  response_parameters = local.cors_headers
}

# ── {admin_parent}/seats/{seatId}  (item — DELETE reset-to-default) ────────

resource "aws_api_gateway_resource" "admin_seat_item" {
  rest_api_id = var.rest_api_id
  parent_id   = aws_api_gateway_resource.admin_seats.id
  path_part   = "{seatId}"
}

resource "aws_api_gateway_method" "admin_seat_item_delete" {
  rest_api_id   = var.rest_api_id
  resource_id   = aws_api_gateway_resource.admin_seat_item.id
  http_method   = "DELETE"
  authorization = "COGNITO_USER_POOLS"
  authorizer_id = var.cognito_authorizer_id
  request_parameters = {
    "method.request.path.seatId" = true
  }
}

resource "aws_api_gateway_integration" "admin_seat_item_delete" {
  rest_api_id             = var.rest_api_id
  resource_id             = aws_api_gateway_resource.admin_seat_item.id
  http_method             = aws_api_gateway_method.admin_seat_item_delete.http_method
  integration_http_method = "POST"
  type                    = "AWS_PROXY"
  uri                     = aws_lambda_function.manage_seats.invoke_arn
}

resource "aws_api_gateway_method" "admin_seat_item_options" {
  rest_api_id   = var.rest_api_id
  resource_id   = aws_api_gateway_resource.admin_seat_item.id
  http_method   = "OPTIONS"
  authorization = "NONE"
}

resource "aws_api_gateway_integration" "admin_seat_item_options" {
  rest_api_id       = var.rest_api_id
  resource_id       = aws_api_gateway_resource.admin_seat_item.id
  http_method       = aws_api_gateway_method.admin_seat_item_options.http_method
  type              = "MOCK"
  request_templates = { "application/json" = "{\"statusCode\": 200}" }
}

resource "aws_api_gateway_method_response" "admin_seat_item_options_200" {
  rest_api_id = var.rest_api_id
  resource_id = aws_api_gateway_resource.admin_seat_item.id
  http_method = aws_api_gateway_method.admin_seat_item_options.http_method
  status_code = "200"
  response_parameters = {
    "method.response.header.Access-Control-Allow-Headers" = true
    "method.response.header.Access-Control-Allow-Methods" = true
    "method.response.header.Access-Control-Allow-Origin"  = true
  }
}

resource "aws_api_gateway_integration_response" "admin_seat_item_options" {
  depends_on          = [aws_api_gateway_integration.admin_seat_item_options] # see seats_options above for why
  rest_api_id         = var.rest_api_id
  resource_id         = aws_api_gateway_resource.admin_seat_item.id
  http_method         = aws_api_gateway_method.admin_seat_item_options.http_method
  status_code         = aws_api_gateway_method_response.admin_seat_item_options_200.status_code
  response_parameters = local.cors_headers
}

# ─────────────────────────────────────────────────────────────────────────────
# {admin_parent}/seat-pricing  (collection — GET all priced zones)
# ─────────────────────────────────────────────────────────────────────────────

resource "aws_api_gateway_resource" "admin_seat_pricing" {
  rest_api_id = var.rest_api_id
  parent_id   = var.admin_parent_resource_id
  path_part   = "seat-pricing"
}

resource "aws_api_gateway_method" "admin_seat_pricing_get" {
  rest_api_id   = var.rest_api_id
  resource_id   = aws_api_gateway_resource.admin_seat_pricing.id
  http_method   = "GET"
  authorization = "COGNITO_USER_POOLS"
  authorizer_id = var.cognito_authorizer_id
}

resource "aws_api_gateway_integration" "admin_seat_pricing_get" {
  rest_api_id             = var.rest_api_id
  resource_id             = aws_api_gateway_resource.admin_seat_pricing.id
  http_method             = aws_api_gateway_method.admin_seat_pricing_get.http_method
  integration_http_method = "POST"
  type                    = "AWS_PROXY"
  uri                     = aws_lambda_function.manage_seats.invoke_arn
}

resource "aws_api_gateway_method" "admin_seat_pricing_options" {
  rest_api_id   = var.rest_api_id
  resource_id   = aws_api_gateway_resource.admin_seat_pricing.id
  http_method   = "OPTIONS"
  authorization = "NONE"
}

resource "aws_api_gateway_integration" "admin_seat_pricing_options" {
  rest_api_id       = var.rest_api_id
  resource_id       = aws_api_gateway_resource.admin_seat_pricing.id
  http_method       = aws_api_gateway_method.admin_seat_pricing_options.http_method
  type              = "MOCK"
  request_templates = { "application/json" = "{\"statusCode\": 200}" }
}

resource "aws_api_gateway_method_response" "admin_seat_pricing_options_200" {
  rest_api_id = var.rest_api_id
  resource_id = aws_api_gateway_resource.admin_seat_pricing.id
  http_method = aws_api_gateway_method.admin_seat_pricing_options.http_method
  status_code = "200"
  response_parameters = {
    "method.response.header.Access-Control-Allow-Headers" = true
    "method.response.header.Access-Control-Allow-Methods" = true
    "method.response.header.Access-Control-Allow-Origin"  = true
  }
}

resource "aws_api_gateway_integration_response" "admin_seat_pricing_options" {
  depends_on          = [aws_api_gateway_integration.admin_seat_pricing_options] # see seats_options above for why
  rest_api_id         = var.rest_api_id
  resource_id         = aws_api_gateway_resource.admin_seat_pricing.id
  http_method         = aws_api_gateway_method.admin_seat_pricing_options.http_method
  status_code         = aws_api_gateway_method_response.admin_seat_pricing_options_200.status_code
  response_parameters = local.cors_headers
}

# ── {admin_parent}/seat-pricing/{category}  (item — PUT set, DELETE unprice) ──

resource "aws_api_gateway_resource" "admin_seat_pricing_item" {
  rest_api_id = var.rest_api_id
  parent_id   = aws_api_gateway_resource.admin_seat_pricing.id
  path_part   = "{category}"
}

resource "aws_api_gateway_method" "admin_seat_pricing_item_put" {
  rest_api_id   = var.rest_api_id
  resource_id   = aws_api_gateway_resource.admin_seat_pricing_item.id
  http_method   = "PUT"
  authorization = "COGNITO_USER_POOLS"
  authorizer_id = var.cognito_authorizer_id
  request_parameters = {
    "method.request.path.category" = true
  }
}

resource "aws_api_gateway_integration" "admin_seat_pricing_item_put" {
  rest_api_id             = var.rest_api_id
  resource_id             = aws_api_gateway_resource.admin_seat_pricing_item.id
  http_method             = aws_api_gateway_method.admin_seat_pricing_item_put.http_method
  integration_http_method = "POST"
  type                    = "AWS_PROXY"
  uri                     = aws_lambda_function.manage_seats.invoke_arn
}

resource "aws_api_gateway_method" "admin_seat_pricing_item_delete" {
  rest_api_id   = var.rest_api_id
  resource_id   = aws_api_gateway_resource.admin_seat_pricing_item.id
  http_method   = "DELETE"
  authorization = "COGNITO_USER_POOLS"
  authorizer_id = var.cognito_authorizer_id
  request_parameters = {
    "method.request.path.category" = true
  }
}

resource "aws_api_gateway_integration" "admin_seat_pricing_item_delete" {
  rest_api_id             = var.rest_api_id
  resource_id             = aws_api_gateway_resource.admin_seat_pricing_item.id
  http_method             = aws_api_gateway_method.admin_seat_pricing_item_delete.http_method
  integration_http_method = "POST"
  type                    = "AWS_PROXY"
  uri                     = aws_lambda_function.manage_seats.invoke_arn
}

resource "aws_api_gateway_method" "admin_seat_pricing_item_options" {
  rest_api_id   = var.rest_api_id
  resource_id   = aws_api_gateway_resource.admin_seat_pricing_item.id
  http_method   = "OPTIONS"
  authorization = "NONE"
}

resource "aws_api_gateway_integration" "admin_seat_pricing_item_options" {
  rest_api_id       = var.rest_api_id
  resource_id       = aws_api_gateway_resource.admin_seat_pricing_item.id
  http_method       = aws_api_gateway_method.admin_seat_pricing_item_options.http_method
  type              = "MOCK"
  request_templates = { "application/json" = "{\"statusCode\": 200}" }
}

resource "aws_api_gateway_method_response" "admin_seat_pricing_item_options_200" {
  rest_api_id = var.rest_api_id
  resource_id = aws_api_gateway_resource.admin_seat_pricing_item.id
  http_method = aws_api_gateway_method.admin_seat_pricing_item_options.http_method
  status_code = "200"
  response_parameters = {
    "method.response.header.Access-Control-Allow-Headers" = true
    "method.response.header.Access-Control-Allow-Methods" = true
    "method.response.header.Access-Control-Allow-Origin"  = true
  }
}

resource "aws_api_gateway_integration_response" "admin_seat_pricing_item_options" {
  depends_on          = [aws_api_gateway_integration.admin_seat_pricing_item_options] # see seats_options above for why
  rest_api_id         = var.rest_api_id
  resource_id         = aws_api_gateway_resource.admin_seat_pricing_item.id
  http_method         = aws_api_gateway_method.admin_seat_pricing_item_options.http_method
  status_code         = aws_api_gateway_method_response.admin_seat_pricing_item_options_200.status_code
  response_parameters = local.cors_headers
}

# ─────────────────────────────────────────────────────────────────────────────
# {admin_parent}/seat-purchases[/{purchaseId}]  (admin purchase management)
# ─────────────────────────────────────────────────────────────────────────────

resource "aws_api_gateway_resource" "admin_seat_purchases" {
  rest_api_id = var.rest_api_id
  parent_id   = var.admin_parent_resource_id
  path_part   = "seat-purchases"
}

resource "aws_api_gateway_method" "admin_seat_purchases_get" {
  rest_api_id   = var.rest_api_id
  resource_id   = aws_api_gateway_resource.admin_seat_purchases.id
  http_method   = "GET"
  authorization = "COGNITO_USER_POOLS"
  authorizer_id = var.cognito_authorizer_id
}

resource "aws_api_gateway_integration" "admin_seat_purchases_get" {
  rest_api_id             = var.rest_api_id
  resource_id             = aws_api_gateway_resource.admin_seat_purchases.id
  http_method             = aws_api_gateway_method.admin_seat_purchases_get.http_method
  integration_http_method = "POST"
  type                    = "AWS_PROXY"
  uri                     = aws_lambda_function.manage_seats.invoke_arn
}

resource "aws_api_gateway_method" "admin_seat_purchases_options" {
  rest_api_id   = var.rest_api_id
  resource_id   = aws_api_gateway_resource.admin_seat_purchases.id
  http_method   = "OPTIONS"
  authorization = "NONE"
}

resource "aws_api_gateway_integration" "admin_seat_purchases_options" {
  rest_api_id       = var.rest_api_id
  resource_id       = aws_api_gateway_resource.admin_seat_purchases.id
  http_method       = aws_api_gateway_method.admin_seat_purchases_options.http_method
  type              = "MOCK"
  request_templates = { "application/json" = "{\"statusCode\": 200}" }
}

resource "aws_api_gateway_method_response" "admin_seat_purchases_options_200" {
  rest_api_id = var.rest_api_id
  resource_id = aws_api_gateway_resource.admin_seat_purchases.id
  http_method = aws_api_gateway_method.admin_seat_purchases_options.http_method
  status_code = "200"
  response_parameters = {
    "method.response.header.Access-Control-Allow-Headers" = true
    "method.response.header.Access-Control-Allow-Methods" = true
    "method.response.header.Access-Control-Allow-Origin"  = true
  }
}

resource "aws_api_gateway_integration_response" "admin_seat_purchases_options" {
  depends_on          = [aws_api_gateway_integration.admin_seat_purchases_options] # see seats_options above for why
  rest_api_id         = var.rest_api_id
  resource_id         = aws_api_gateway_resource.admin_seat_purchases.id
  http_method         = aws_api_gateway_method.admin_seat_purchases_options.http_method
  status_code         = aws_api_gateway_method_response.admin_seat_purchases_options_200.status_code
  response_parameters = local.cors_headers
}

# ── {admin_parent}/seat-purchases/{purchaseId}  (item — GET, PATCH status) ──

resource "aws_api_gateway_resource" "admin_seat_purchase_item" {
  rest_api_id = var.rest_api_id
  parent_id   = aws_api_gateway_resource.admin_seat_purchases.id
  path_part   = "{purchaseId}"
}

resource "aws_api_gateway_method" "admin_seat_purchase_item_get" {
  rest_api_id   = var.rest_api_id
  resource_id   = aws_api_gateway_resource.admin_seat_purchase_item.id
  http_method   = "GET"
  authorization = "COGNITO_USER_POOLS"
  authorizer_id = var.cognito_authorizer_id
  request_parameters = {
    "method.request.path.purchaseId" = true
  }
}

resource "aws_api_gateway_integration" "admin_seat_purchase_item_get" {
  rest_api_id             = var.rest_api_id
  resource_id             = aws_api_gateway_resource.admin_seat_purchase_item.id
  http_method             = aws_api_gateway_method.admin_seat_purchase_item_get.http_method
  integration_http_method = "POST"
  type                    = "AWS_PROXY"
  uri                     = aws_lambda_function.manage_seats.invoke_arn
}

resource "aws_api_gateway_method" "admin_seat_purchase_item_patch" {
  rest_api_id   = var.rest_api_id
  resource_id   = aws_api_gateway_resource.admin_seat_purchase_item.id
  http_method   = "PATCH"
  authorization = "COGNITO_USER_POOLS"
  authorizer_id = var.cognito_authorizer_id
  request_parameters = {
    "method.request.path.purchaseId" = true
  }
}

resource "aws_api_gateway_integration" "admin_seat_purchase_item_patch" {
  rest_api_id             = var.rest_api_id
  resource_id             = aws_api_gateway_resource.admin_seat_purchase_item.id
  http_method             = aws_api_gateway_method.admin_seat_purchase_item_patch.http_method
  integration_http_method = "POST"
  type                    = "AWS_PROXY"
  uri                     = aws_lambda_function.manage_seats.invoke_arn
}

resource "aws_api_gateway_method" "admin_seat_purchase_item_options" {
  rest_api_id   = var.rest_api_id
  resource_id   = aws_api_gateway_resource.admin_seat_purchase_item.id
  http_method   = "OPTIONS"
  authorization = "NONE"
}

resource "aws_api_gateway_integration" "admin_seat_purchase_item_options" {
  rest_api_id       = var.rest_api_id
  resource_id       = aws_api_gateway_resource.admin_seat_purchase_item.id
  http_method       = aws_api_gateway_method.admin_seat_purchase_item_options.http_method
  type              = "MOCK"
  request_templates = { "application/json" = "{\"statusCode\": 200}" }
}

resource "aws_api_gateway_method_response" "admin_seat_purchase_item_options_200" {
  rest_api_id = var.rest_api_id
  resource_id = aws_api_gateway_resource.admin_seat_purchase_item.id
  http_method = aws_api_gateway_method.admin_seat_purchase_item_options.http_method
  status_code = "200"
  response_parameters = {
    "method.response.header.Access-Control-Allow-Headers" = true
    "method.response.header.Access-Control-Allow-Methods" = true
    "method.response.header.Access-Control-Allow-Origin"  = true
  }
}

resource "aws_api_gateway_integration_response" "admin_seat_purchase_item_options" {
  depends_on          = [aws_api_gateway_integration.admin_seat_purchase_item_options] # see seats_options above for why
  rest_api_id         = var.rest_api_id
  resource_id         = aws_api_gateway_resource.admin_seat_purchase_item.id
  http_method         = aws_api_gateway_method.admin_seat_purchase_item_options.http_method
  status_code         = aws_api_gateway_method_response.admin_seat_purchase_item_options_200.status_code
  response_parameters = local.cors_headers
}

# ─────────────────────────────────────────────────────────────────────────────
# /admin/seat-check-in  (POST — resolve or consume a scanned check-in QR)
#
# Deliberately NOT nested under admin_parent_resource_id: that path requires
# an eventType, but a scanned QR carries only its own token — the purchase
# it resolves to already knows its own eventType. Body: {token, action}
# where action is "resolve" (peek, no write) or "check_in" (atomic consume),
# same convention as this host's own /admin/check-in (entry-pass) route.
# ─────────────────────────────────────────────────────────────────────────────

resource "aws_api_gateway_resource" "admin_seat_check_in" {
  rest_api_id = var.rest_api_id
  parent_id   = var.admin_root_resource_id
  path_part   = "seat-check-in"
}

resource "aws_api_gateway_method" "admin_seat_check_in_post" {
  rest_api_id   = var.rest_api_id
  resource_id   = aws_api_gateway_resource.admin_seat_check_in.id
  http_method   = "POST"
  authorization = "COGNITO_USER_POOLS"
  authorizer_id = var.cognito_authorizer_id
}

resource "aws_api_gateway_integration" "admin_seat_check_in_post" {
  rest_api_id             = var.rest_api_id
  resource_id             = aws_api_gateway_resource.admin_seat_check_in.id
  http_method             = aws_api_gateway_method.admin_seat_check_in_post.http_method
  integration_http_method = "POST"
  type                    = "AWS_PROXY"
  uri                     = aws_lambda_function.manage_seats.invoke_arn
}

resource "aws_api_gateway_method" "admin_seat_check_in_options" {
  rest_api_id   = var.rest_api_id
  resource_id   = aws_api_gateway_resource.admin_seat_check_in.id
  http_method   = "OPTIONS"
  authorization = "NONE"
}

resource "aws_api_gateway_integration" "admin_seat_check_in_options" {
  rest_api_id       = var.rest_api_id
  resource_id       = aws_api_gateway_resource.admin_seat_check_in.id
  http_method       = aws_api_gateway_method.admin_seat_check_in_options.http_method
  type              = "MOCK"
  request_templates = { "application/json" = "{\"statusCode\": 200}" }
}

resource "aws_api_gateway_method_response" "admin_seat_check_in_options_200" {
  rest_api_id = var.rest_api_id
  resource_id = aws_api_gateway_resource.admin_seat_check_in.id
  http_method = aws_api_gateway_method.admin_seat_check_in_options.http_method
  status_code = "200"
  response_parameters = {
    "method.response.header.Access-Control-Allow-Headers" = true
    "method.response.header.Access-Control-Allow-Methods" = true
    "method.response.header.Access-Control-Allow-Origin"  = true
  }
}

resource "aws_api_gateway_integration_response" "admin_seat_check_in_options" {
  depends_on          = [aws_api_gateway_integration.admin_seat_check_in_options] # see seats_options above for why
  rest_api_id         = var.rest_api_id
  resource_id         = aws_api_gateway_resource.admin_seat_check_in.id
  http_method         = aws_api_gateway_method.admin_seat_check_in_options.http_method
  status_code         = aws_api_gateway_method_response.admin_seat_check_in_options_200.status_code
  response_parameters = local.cors_headers
}

# ─────────────────────────────────────────────────────────────────────────────
# /webhooks/seat-selection/{provider}  (public — payment provider webhook)
# ─────────────────────────────────────────────────────────────────────────────

resource "aws_lambda_permission" "seat_purchase_webhook" {
  statement_id  = "AllowAPIGatewayInvokeSeatPurchaseWebhook"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.seat_purchase_webhook.function_name
  principal     = "apigateway.amazonaws.com"
  source_arn    = "${var.rest_api_execution_arn}/*/*"
}

# Mounted under the host-owned, shared /webhooks resource
# (var.webhooks_resource_id) as a sibling of every other plugin's own
# webhook route — not a "webhooks" resource this module creates itself,
# which would conflict with any other plugin doing the same under the API
# root (see plugins/seat-upgrade's own webhook mount for the same
# convention, and this repo's backend/api_gateway.tf for the shared
# resource itself).
resource "aws_api_gateway_resource" "seat_selection_webhook" {
  rest_api_id = var.rest_api_id
  parent_id   = var.webhooks_resource_id
  path_part   = "seat-selection"
}

resource "aws_api_gateway_resource" "seat_selection_webhook_provider" {
  rest_api_id = var.rest_api_id
  parent_id   = aws_api_gateway_resource.seat_selection_webhook.id
  path_part   = "{provider}"
}

resource "aws_api_gateway_method" "seat_selection_webhook_post" {
  rest_api_id   = var.rest_api_id
  resource_id   = aws_api_gateway_resource.seat_selection_webhook_provider.id
  http_method   = "POST"
  authorization = "NONE"
  request_parameters = {
    "method.request.path.provider" = true
  }
}

resource "aws_api_gateway_integration" "seat_selection_webhook_post" {
  rest_api_id             = var.rest_api_id
  resource_id             = aws_api_gateway_resource.seat_selection_webhook_provider.id
  http_method             = aws_api_gateway_method.seat_selection_webhook_post.http_method
  integration_http_method = "POST"
  type                    = "AWS_PROXY"
  uri                     = aws_lambda_function.seat_purchase_webhook.invoke_arn
}
