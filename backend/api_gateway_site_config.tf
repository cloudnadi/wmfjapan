# ─────────────────────────────────────────────────────────────────────────────
# Site config API:
#   GET  /site-config                         (public — current carousel URLs)
#   GET  /admin/site-config                   (Cognito — read current config)
#   PUT  /admin/site-config                   (Cognito — confirm upload)
#   POST /admin/site-config/upload-url        (Cognito — get presigned PUT URL)
# ─────────────────────────────────────────────────────────────────────────────

resource "aws_lambda_permission" "get_site_config" {
  statement_id  = "AllowAPIGatewayInvokeGetSiteConfig"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.get_site_config.function_name
  principal     = "apigateway.amazonaws.com"
  source_arn    = "${aws_api_gateway_rest_api.main.execution_arn}/*/*"
}

resource "aws_lambda_permission" "manage_site_config" {
  statement_id  = "AllowAPIGatewayInvokeManageSiteConfig"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.manage_site_config.function_name
  principal     = "apigateway.amazonaws.com"
  source_arn    = "${aws_api_gateway_rest_api.main.execution_arn}/*/*"
}

# ── GET /site-config (public) ─────────────────────────────────────────────────

resource "aws_api_gateway_resource" "site_config" {
  rest_api_id = aws_api_gateway_rest_api.main.id
  parent_id   = aws_api_gateway_rest_api.main.root_resource_id
  path_part   = "site-config"
}

resource "aws_api_gateway_method" "site_config_get" {
  rest_api_id   = aws_api_gateway_rest_api.main.id
  resource_id   = aws_api_gateway_resource.site_config.id
  http_method   = "GET"
  authorization = "NONE"
}

resource "aws_api_gateway_integration" "site_config_get" {
  rest_api_id             = aws_api_gateway_rest_api.main.id
  resource_id             = aws_api_gateway_resource.site_config.id
  http_method             = aws_api_gateway_method.site_config_get.http_method
  integration_http_method = "POST"
  type                    = "AWS_PROXY"
  uri                     = aws_lambda_function.get_site_config.invoke_arn
}

resource "aws_api_gateway_method" "site_config_options" {
  rest_api_id   = aws_api_gateway_rest_api.main.id
  resource_id   = aws_api_gateway_resource.site_config.id
  http_method   = "OPTIONS"
  authorization = "NONE"
}

resource "aws_api_gateway_integration" "site_config_options" {
  rest_api_id       = aws_api_gateway_rest_api.main.id
  resource_id       = aws_api_gateway_resource.site_config.id
  http_method       = aws_api_gateway_method.site_config_options.http_method
  type              = "MOCK"
  request_templates = { "application/json" = "{\"statusCode\": 200}" }
}

resource "aws_api_gateway_method_response" "site_config_options_200" {
  rest_api_id = aws_api_gateway_rest_api.main.id
  resource_id = aws_api_gateway_resource.site_config.id
  http_method = aws_api_gateway_method.site_config_options.http_method
  status_code = "200"
  response_parameters = {
    "method.response.header.Access-Control-Allow-Headers" = true
    "method.response.header.Access-Control-Allow-Methods" = true
    "method.response.header.Access-Control-Allow-Origin"  = true
  }
}

resource "aws_api_gateway_integration_response" "site_config_options" {
  rest_api_id         = aws_api_gateway_rest_api.main.id
  resource_id         = aws_api_gateway_resource.site_config.id
  http_method         = aws_api_gateway_method.site_config_options.http_method
  status_code         = aws_api_gateway_method_response.site_config_options_200.status_code
  response_parameters = local.cors_headers
}

# ── GET + PUT /admin/site-config (Cognito protected) ──────────────────────────

resource "aws_api_gateway_resource" "admin_site_config" {
  rest_api_id = aws_api_gateway_rest_api.main.id
  parent_id   = aws_api_gateway_resource.admin.id
  path_part   = "site-config"
}

resource "aws_api_gateway_method" "admin_site_config_get" {
  rest_api_id   = aws_api_gateway_rest_api.main.id
  resource_id   = aws_api_gateway_resource.admin_site_config.id
  http_method   = "GET"
  authorization = "COGNITO_USER_POOLS"
  authorizer_id = aws_api_gateway_authorizer.cognito.id
}

resource "aws_api_gateway_integration" "admin_site_config_get" {
  rest_api_id             = aws_api_gateway_rest_api.main.id
  resource_id             = aws_api_gateway_resource.admin_site_config.id
  http_method             = aws_api_gateway_method.admin_site_config_get.http_method
  integration_http_method = "POST"
  type                    = "AWS_PROXY"
  uri                     = aws_lambda_function.manage_site_config.invoke_arn
}

resource "aws_api_gateway_method" "admin_site_config_put" {
  rest_api_id   = aws_api_gateway_rest_api.main.id
  resource_id   = aws_api_gateway_resource.admin_site_config.id
  http_method   = "PUT"
  authorization = "COGNITO_USER_POOLS"
  authorizer_id = aws_api_gateway_authorizer.cognito.id
}

resource "aws_api_gateway_integration" "admin_site_config_put" {
  rest_api_id             = aws_api_gateway_rest_api.main.id
  resource_id             = aws_api_gateway_resource.admin_site_config.id
  http_method             = aws_api_gateway_method.admin_site_config_put.http_method
  integration_http_method = "POST"
  type                    = "AWS_PROXY"
  uri                     = aws_lambda_function.manage_site_config.invoke_arn
}

resource "aws_api_gateway_method" "admin_site_config_options" {
  rest_api_id   = aws_api_gateway_rest_api.main.id
  resource_id   = aws_api_gateway_resource.admin_site_config.id
  http_method   = "OPTIONS"
  authorization = "NONE"
}

resource "aws_api_gateway_integration" "admin_site_config_options" {
  rest_api_id       = aws_api_gateway_rest_api.main.id
  resource_id       = aws_api_gateway_resource.admin_site_config.id
  http_method       = aws_api_gateway_method.admin_site_config_options.http_method
  type              = "MOCK"
  request_templates = { "application/json" = "{\"statusCode\": 200}" }
}

resource "aws_api_gateway_method_response" "admin_site_config_options_200" {
  rest_api_id = aws_api_gateway_rest_api.main.id
  resource_id = aws_api_gateway_resource.admin_site_config.id
  http_method = aws_api_gateway_method.admin_site_config_options.http_method
  status_code = "200"
  response_parameters = {
    "method.response.header.Access-Control-Allow-Headers" = true
    "method.response.header.Access-Control-Allow-Methods" = true
    "method.response.header.Access-Control-Allow-Origin"  = true
  }
}

resource "aws_api_gateway_integration_response" "admin_site_config_options" {
  rest_api_id         = aws_api_gateway_rest_api.main.id
  resource_id         = aws_api_gateway_resource.admin_site_config.id
  http_method         = aws_api_gateway_method.admin_site_config_options.http_method
  status_code         = aws_api_gateway_method_response.admin_site_config_options_200.status_code
  response_parameters = local.cors_headers
}

# ── POST /admin/site-config/upload-url (Cognito protected) ────────────────────

resource "aws_api_gateway_resource" "admin_site_config_upload" {
  rest_api_id = aws_api_gateway_rest_api.main.id
  parent_id   = aws_api_gateway_resource.admin_site_config.id
  path_part   = "upload-url"
}

resource "aws_api_gateway_method" "admin_site_config_upload_post" {
  rest_api_id   = aws_api_gateway_rest_api.main.id
  resource_id   = aws_api_gateway_resource.admin_site_config_upload.id
  http_method   = "POST"
  authorization = "COGNITO_USER_POOLS"
  authorizer_id = aws_api_gateway_authorizer.cognito.id
  request_parameters = {
    "method.request.querystring.slot" = true
  }
}

resource "aws_api_gateway_integration" "admin_site_config_upload_post" {
  rest_api_id             = aws_api_gateway_rest_api.main.id
  resource_id             = aws_api_gateway_resource.admin_site_config_upload.id
  http_method             = aws_api_gateway_method.admin_site_config_upload_post.http_method
  integration_http_method = "POST"
  type                    = "AWS_PROXY"
  uri                     = aws_lambda_function.manage_site_config.invoke_arn
}

resource "aws_api_gateway_method" "admin_site_config_upload_options" {
  rest_api_id   = aws_api_gateway_rest_api.main.id
  resource_id   = aws_api_gateway_resource.admin_site_config_upload.id
  http_method   = "OPTIONS"
  authorization = "NONE"
}

resource "aws_api_gateway_integration" "admin_site_config_upload_options" {
  rest_api_id       = aws_api_gateway_rest_api.main.id
  resource_id       = aws_api_gateway_resource.admin_site_config_upload.id
  http_method       = aws_api_gateway_method.admin_site_config_upload_options.http_method
  type              = "MOCK"
  request_templates = { "application/json" = "{\"statusCode\": 200}" }
}

resource "aws_api_gateway_method_response" "admin_site_config_upload_options_200" {
  rest_api_id = aws_api_gateway_rest_api.main.id
  resource_id = aws_api_gateway_resource.admin_site_config_upload.id
  http_method = aws_api_gateway_method.admin_site_config_upload_options.http_method
  status_code = "200"
  response_parameters = {
    "method.response.header.Access-Control-Allow-Headers" = true
    "method.response.header.Access-Control-Allow-Methods" = true
    "method.response.header.Access-Control-Allow-Origin"  = true
  }
}

resource "aws_api_gateway_integration_response" "admin_site_config_upload_options" {
  rest_api_id         = aws_api_gateway_rest_api.main.id
  resource_id         = aws_api_gateway_resource.admin_site_config_upload.id
  http_method         = aws_api_gateway_method.admin_site_config_upload_options.http_method
  status_code         = aws_api_gateway_method_response.admin_site_config_upload_options_200.status_code
  response_parameters = local.cors_headers
}
