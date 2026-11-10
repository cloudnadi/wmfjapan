# ─────────────────────────────────────────────────────────────────────────────
# Celebrity Event seat selection — the registrant-facing "buy specific
# seats" routes. This is the host app's own integration glue
# (my_registrations already owns the registration record and gates
# eligibility), so it stays here rather than in the seat-selection plugin
# module — see the plugin README's "the one route this module doesn't own".
#
# The seat map's own routes (GET /events/{eventType}/seats, the admin
# GET/POST/DELETE .../seats[/{seatId}] and .../seat-pricing[...] routes, the
# admin .../seat-purchases[...] routes, and the payment-provider webhook)
# are provisioned by that module instead — see module "seat_selection" in
# seat_selection.tf and plugins/seat-selection/terraform/api_gateway.tf.
#
#   /my/registrations/{registrationId}/seat-purchases                     GET POST
#   /my/registrations/{registrationId}/seat-purchases/{purchaseId}        GET DELETE
#   /my/registrations/{registrationId}/seat-purchases/{purchaseId}/checkout   POST
#
# Follows the same conventions as api_gateway.tf / api_gateway_admin.tf:
# AWS_PROXY integrations for the real methods, MOCK integrations for OPTIONS.
# New methods/integrations are added to aws_api_gateway_deployment.main's
# triggers/depends_on in api_gateway.tf so an apply redeploys the stage.
# ─────────────────────────────────────────────────────────────────────────────

# ─────────────────────────────────────────────────────────────────────────────
# /my/registrations/{registrationId}/seat-purchases  (collection — GET list, POST create)
# ─────────────────────────────────────────────────────────────────────────────

resource "aws_api_gateway_resource" "my_registration_item_seat_purchases" {
  rest_api_id = aws_api_gateway_rest_api.main.id
  parent_id   = aws_api_gateway_resource.my_registration_item.id
  path_part   = "seat-purchases"
}

resource "aws_api_gateway_method" "my_registration_seat_purchases_get" {
  rest_api_id   = aws_api_gateway_rest_api.main.id
  resource_id   = aws_api_gateway_resource.my_registration_item_seat_purchases.id
  http_method   = "GET"
  authorization = "NONE"
}

resource "aws_api_gateway_integration" "my_registration_seat_purchases_get" {
  rest_api_id             = aws_api_gateway_rest_api.main.id
  resource_id             = aws_api_gateway_resource.my_registration_item_seat_purchases.id
  http_method             = aws_api_gateway_method.my_registration_seat_purchases_get.http_method
  integration_http_method = "POST"
  type                    = "AWS_PROXY"
  uri                     = aws_lambda_function.my_registrations.invoke_arn
}

resource "aws_api_gateway_method" "my_registration_seat_purchases_post" {
  rest_api_id   = aws_api_gateway_rest_api.main.id
  resource_id   = aws_api_gateway_resource.my_registration_item_seat_purchases.id
  http_method   = "POST"
  authorization = "NONE"
}

resource "aws_api_gateway_integration" "my_registration_seat_purchases_post" {
  rest_api_id             = aws_api_gateway_rest_api.main.id
  resource_id             = aws_api_gateway_resource.my_registration_item_seat_purchases.id
  http_method             = aws_api_gateway_method.my_registration_seat_purchases_post.http_method
  integration_http_method = "POST"
  type                    = "AWS_PROXY"
  uri                     = aws_lambda_function.my_registrations.invoke_arn
}

resource "aws_api_gateway_method" "my_registration_seat_purchases_options" {
  rest_api_id   = aws_api_gateway_rest_api.main.id
  resource_id   = aws_api_gateway_resource.my_registration_item_seat_purchases.id
  http_method   = "OPTIONS"
  authorization = "NONE"
}

resource "aws_api_gateway_integration" "my_registration_seat_purchases_options" {
  rest_api_id       = aws_api_gateway_rest_api.main.id
  resource_id       = aws_api_gateway_resource.my_registration_item_seat_purchases.id
  http_method       = aws_api_gateway_method.my_registration_seat_purchases_options.http_method
  type              = "MOCK"
  request_templates = { "application/json" = "{\"statusCode\": 200}" }
}

resource "aws_api_gateway_method_response" "my_registration_seat_purchases_options_200" {
  rest_api_id = aws_api_gateway_rest_api.main.id
  resource_id = aws_api_gateway_resource.my_registration_item_seat_purchases.id
  http_method = aws_api_gateway_method.my_registration_seat_purchases_options.http_method
  status_code = "200"
  response_parameters = {
    "method.response.header.Access-Control-Allow-Headers" = true
    "method.response.header.Access-Control-Allow-Methods" = true
    "method.response.header.Access-Control-Allow-Origin"  = true
  }
}

resource "aws_api_gateway_integration_response" "my_registration_seat_purchases_options" {
  rest_api_id         = aws_api_gateway_rest_api.main.id
  resource_id         = aws_api_gateway_resource.my_registration_item_seat_purchases.id
  http_method         = aws_api_gateway_method.my_registration_seat_purchases_options.http_method
  status_code         = aws_api_gateway_method_response.my_registration_seat_purchases_options_200.status_code
  response_parameters = local.cors_headers

  depends_on = [aws_api_gateway_integration.my_registration_seat_purchases_options]
}

# ── /my/registrations/{registrationId}/seat-purchases/{purchaseId}  (item) ──

resource "aws_api_gateway_resource" "my_registration_seat_purchase_item" {
  rest_api_id = aws_api_gateway_rest_api.main.id
  parent_id   = aws_api_gateway_resource.my_registration_item_seat_purchases.id
  path_part   = "{purchaseId}"
}

resource "aws_api_gateway_method" "my_registration_seat_purchase_item_get" {
  rest_api_id   = aws_api_gateway_rest_api.main.id
  resource_id   = aws_api_gateway_resource.my_registration_seat_purchase_item.id
  http_method   = "GET"
  authorization = "NONE"
  request_parameters = {
    "method.request.path.purchaseId" = true
  }
}

resource "aws_api_gateway_integration" "my_registration_seat_purchase_item_get" {
  rest_api_id             = aws_api_gateway_rest_api.main.id
  resource_id             = aws_api_gateway_resource.my_registration_seat_purchase_item.id
  http_method             = aws_api_gateway_method.my_registration_seat_purchase_item_get.http_method
  integration_http_method = "POST"
  type                    = "AWS_PROXY"
  uri                     = aws_lambda_function.my_registrations.invoke_arn
}

resource "aws_api_gateway_method" "my_registration_seat_purchase_item_delete" {
  rest_api_id   = aws_api_gateway_rest_api.main.id
  resource_id   = aws_api_gateway_resource.my_registration_seat_purchase_item.id
  http_method   = "DELETE"
  authorization = "NONE"
  request_parameters = {
    "method.request.path.purchaseId" = true
  }
}

resource "aws_api_gateway_integration" "my_registration_seat_purchase_item_delete" {
  rest_api_id             = aws_api_gateway_rest_api.main.id
  resource_id             = aws_api_gateway_resource.my_registration_seat_purchase_item.id
  http_method             = aws_api_gateway_method.my_registration_seat_purchase_item_delete.http_method
  integration_http_method = "POST"
  type                    = "AWS_PROXY"
  uri                     = aws_lambda_function.my_registrations.invoke_arn
}

resource "aws_api_gateway_method" "my_registration_seat_purchase_item_options" {
  rest_api_id   = aws_api_gateway_rest_api.main.id
  resource_id   = aws_api_gateway_resource.my_registration_seat_purchase_item.id
  http_method   = "OPTIONS"
  authorization = "NONE"
}

resource "aws_api_gateway_integration" "my_registration_seat_purchase_item_options" {
  rest_api_id       = aws_api_gateway_rest_api.main.id
  resource_id       = aws_api_gateway_resource.my_registration_seat_purchase_item.id
  http_method       = aws_api_gateway_method.my_registration_seat_purchase_item_options.http_method
  type              = "MOCK"
  request_templates = { "application/json" = "{\"statusCode\": 200}" }
}

resource "aws_api_gateway_method_response" "my_registration_seat_purchase_item_options_200" {
  rest_api_id = aws_api_gateway_rest_api.main.id
  resource_id = aws_api_gateway_resource.my_registration_seat_purchase_item.id
  http_method = aws_api_gateway_method.my_registration_seat_purchase_item_options.http_method
  status_code = "200"
  response_parameters = {
    "method.response.header.Access-Control-Allow-Headers" = true
    "method.response.header.Access-Control-Allow-Methods" = true
    "method.response.header.Access-Control-Allow-Origin"  = true
  }
}

resource "aws_api_gateway_integration_response" "my_registration_seat_purchase_item_options" {
  rest_api_id         = aws_api_gateway_rest_api.main.id
  resource_id         = aws_api_gateway_resource.my_registration_seat_purchase_item.id
  http_method         = aws_api_gateway_method.my_registration_seat_purchase_item_options.http_method
  status_code         = aws_api_gateway_method_response.my_registration_seat_purchase_item_options_200.status_code
  response_parameters = local.cors_headers

  depends_on = [aws_api_gateway_integration.my_registration_seat_purchase_item_options]
}

# ── .../seat-purchases/{purchaseId}/checkout  (POST — start online checkout) ──

resource "aws_api_gateway_resource" "my_registration_seat_purchase_checkout" {
  rest_api_id = aws_api_gateway_rest_api.main.id
  parent_id   = aws_api_gateway_resource.my_registration_seat_purchase_item.id
  path_part   = "checkout"
}

resource "aws_api_gateway_method" "my_registration_seat_purchase_checkout_post" {
  rest_api_id   = aws_api_gateway_rest_api.main.id
  resource_id   = aws_api_gateway_resource.my_registration_seat_purchase_checkout.id
  http_method   = "POST"
  authorization = "NONE"
  request_parameters = {
    "method.request.path.purchaseId" = true
  }
}

resource "aws_api_gateway_integration" "my_registration_seat_purchase_checkout_post" {
  rest_api_id             = aws_api_gateway_rest_api.main.id
  resource_id             = aws_api_gateway_resource.my_registration_seat_purchase_checkout.id
  http_method             = aws_api_gateway_method.my_registration_seat_purchase_checkout_post.http_method
  integration_http_method = "POST"
  type                    = "AWS_PROXY"
  uri                     = aws_lambda_function.my_registrations.invoke_arn
}

resource "aws_api_gateway_method" "my_registration_seat_purchase_checkout_options" {
  rest_api_id   = aws_api_gateway_rest_api.main.id
  resource_id   = aws_api_gateway_resource.my_registration_seat_purchase_checkout.id
  http_method   = "OPTIONS"
  authorization = "NONE"
}

resource "aws_api_gateway_integration" "my_registration_seat_purchase_checkout_options" {
  rest_api_id       = aws_api_gateway_rest_api.main.id
  resource_id       = aws_api_gateway_resource.my_registration_seat_purchase_checkout.id
  http_method       = aws_api_gateway_method.my_registration_seat_purchase_checkout_options.http_method
  type              = "MOCK"
  request_templates = { "application/json" = "{\"statusCode\": 200}" }
}

resource "aws_api_gateway_method_response" "my_registration_seat_purchase_checkout_options_200" {
  rest_api_id = aws_api_gateway_rest_api.main.id
  resource_id = aws_api_gateway_resource.my_registration_seat_purchase_checkout.id
  http_method = aws_api_gateway_method.my_registration_seat_purchase_checkout_options.http_method
  status_code = "200"
  response_parameters = {
    "method.response.header.Access-Control-Allow-Headers" = true
    "method.response.header.Access-Control-Allow-Methods" = true
    "method.response.header.Access-Control-Allow-Origin"  = true
  }
}

resource "aws_api_gateway_integration_response" "my_registration_seat_purchase_checkout_options" {
  rest_api_id         = aws_api_gateway_rest_api.main.id
  resource_id         = aws_api_gateway_resource.my_registration_seat_purchase_checkout.id
  http_method         = aws_api_gateway_method.my_registration_seat_purchase_checkout_options.http_method
  status_code         = aws_api_gateway_method_response.my_registration_seat_purchase_checkout_options_200.status_code
  response_parameters = local.cors_headers

  depends_on = [aws_api_gateway_integration.my_registration_seat_purchase_checkout_options]
}
