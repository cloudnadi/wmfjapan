# ─────────────────────────────────────────────────────────────────────────────
# Seat Upgrade plugin — API Gateway routes
#
#   /admin/events/{eventType}/seat-upgrade-tiers            GET POST
#   /admin/events/{eventType}/seat-upgrade-tiers/{tierId}   GET PATCH DELETE
#   /admin/events/{eventType}/seat-upgrade-seat-map                            POST
#   /admin/events/{eventType}/seat-upgrade-announcement                        POST
#   /admin/events/{eventType}/seat-upgrade-email-templates                     GET
#   /admin/events/{eventType}/seat-upgrade-email-templates/{templateKey}       PATCH DELETE
#   /admin/events/{eventType}/seat-upgrade-email-templates/{templateKey}/preview  POST
#   /admin/events/{eventType}/seat-upgrades                 GET
#   /admin/events/{eventType}/seat-upgrades/{upgradeId}     PATCH
#   /my/seat-upgrades                                       GET POST
#   /my/seat-upgrades/{upgradeId}                           GET DELETE
#   /my/seat-upgrades/{upgradeId}/checkout                  POST
#   /webhooks/seat-upgrade/{provider}                       POST   (public)
#
# Follows the same AWS_PROXY-for-methods / MOCK-for-OPTIONS convention as the
# host's own api_gateway.tf and api_gateway_admin.tf.
# ─────────────────────────────────────────────────────────────────────────────

# ═══════════════════════════════════════════════════════════════════════════
# Admin: seat-upgrade-tiers (collection + item)
# ═══════════════════════════════════════════════════════════════════════════

resource "aws_api_gateway_resource" "seat_upgrade_tiers" {
  rest_api_id = var.rest_api_id
  parent_id   = var.admin_event_item_resource_id
  path_part   = "seat-upgrade-tiers"
}

resource "aws_api_gateway_method" "seat_upgrade_tiers_get" {
  rest_api_id   = var.rest_api_id
  resource_id   = aws_api_gateway_resource.seat_upgrade_tiers.id
  http_method   = "GET"
  authorization = "COGNITO_USER_POOLS"
  authorizer_id = var.cognito_authorizer_id
}

resource "aws_api_gateway_method" "seat_upgrade_tiers_post" {
  rest_api_id   = var.rest_api_id
  resource_id   = aws_api_gateway_resource.seat_upgrade_tiers.id
  http_method   = "POST"
  authorization = "COGNITO_USER_POOLS"
  authorizer_id = var.cognito_authorizer_id
}

resource "aws_api_gateway_method" "seat_upgrade_tiers_options" {
  rest_api_id   = var.rest_api_id
  resource_id   = aws_api_gateway_resource.seat_upgrade_tiers.id
  http_method   = "OPTIONS"
  authorization = "NONE"
}

resource "aws_api_gateway_integration" "seat_upgrade_tiers_get" {
  rest_api_id             = var.rest_api_id
  resource_id             = aws_api_gateway_resource.seat_upgrade_tiers.id
  http_method             = aws_api_gateway_method.seat_upgrade_tiers_get.http_method
  integration_http_method = "POST"
  type                    = "AWS_PROXY"
  uri                     = aws_lambda_function.manage_seat_upgrades.invoke_arn
}

resource "aws_api_gateway_integration" "seat_upgrade_tiers_post" {
  rest_api_id             = var.rest_api_id
  resource_id             = aws_api_gateway_resource.seat_upgrade_tiers.id
  http_method             = aws_api_gateway_method.seat_upgrade_tiers_post.http_method
  integration_http_method = "POST"
  type                    = "AWS_PROXY"
  uri                     = aws_lambda_function.manage_seat_upgrades.invoke_arn
}

resource "aws_api_gateway_integration" "seat_upgrade_tiers_options" {
  rest_api_id       = var.rest_api_id
  resource_id       = aws_api_gateway_resource.seat_upgrade_tiers.id
  http_method       = aws_api_gateway_method.seat_upgrade_tiers_options.http_method
  type              = "MOCK"
  request_templates = { "application/json" = "{\"statusCode\": 200}" }
}

resource "aws_api_gateway_method_response" "seat_upgrade_tiers_options_200" {
  rest_api_id = var.rest_api_id
  resource_id = aws_api_gateway_resource.seat_upgrade_tiers.id
  http_method = aws_api_gateway_method.seat_upgrade_tiers_options.http_method
  status_code = "200"
  response_parameters = {
    "method.response.header.Access-Control-Allow-Headers" = true
    "method.response.header.Access-Control-Allow-Methods" = true
    "method.response.header.Access-Control-Allow-Origin"  = true
  }
}

resource "aws_api_gateway_integration_response" "seat_upgrade_tiers_options" {
  rest_api_id         = var.rest_api_id
  resource_id         = aws_api_gateway_resource.seat_upgrade_tiers.id
  http_method         = aws_api_gateway_method.seat_upgrade_tiers_options.http_method
  status_code         = aws_api_gateway_method_response.seat_upgrade_tiers_options_200.status_code
  response_parameters = var.cors_response_parameters
}

# ═══════════════════════════════════════════════════════════════════════════
# Admin: seat-upgrade-seat-map (image upload)
# ═══════════════════════════════════════════════════════════════════════════

resource "aws_api_gateway_resource" "seat_upgrade_seat_map" {
  rest_api_id = var.rest_api_id
  parent_id   = var.admin_event_item_resource_id
  path_part   = "seat-upgrade-seat-map"
}

resource "aws_api_gateway_method" "seat_upgrade_seat_map_post" {
  rest_api_id   = var.rest_api_id
  resource_id   = aws_api_gateway_resource.seat_upgrade_seat_map.id
  http_method   = "POST"
  authorization = "COGNITO_USER_POOLS"
  authorizer_id = var.cognito_authorizer_id
}

resource "aws_api_gateway_method" "seat_upgrade_seat_map_options" {
  rest_api_id   = var.rest_api_id
  resource_id   = aws_api_gateway_resource.seat_upgrade_seat_map.id
  http_method   = "OPTIONS"
  authorization = "NONE"
}

resource "aws_api_gateway_integration" "seat_upgrade_seat_map_post" {
  rest_api_id             = var.rest_api_id
  resource_id             = aws_api_gateway_resource.seat_upgrade_seat_map.id
  http_method             = aws_api_gateway_method.seat_upgrade_seat_map_post.http_method
  integration_http_method = "POST"
  type                    = "AWS_PROXY"
  uri                     = aws_lambda_function.manage_seat_upgrades.invoke_arn
}

resource "aws_api_gateway_integration" "seat_upgrade_seat_map_options" {
  rest_api_id       = var.rest_api_id
  resource_id       = aws_api_gateway_resource.seat_upgrade_seat_map.id
  http_method       = aws_api_gateway_method.seat_upgrade_seat_map_options.http_method
  type              = "MOCK"
  request_templates = { "application/json" = "{\"statusCode\": 200}" }
}

resource "aws_api_gateway_method_response" "seat_upgrade_seat_map_options_200" {
  rest_api_id = var.rest_api_id
  resource_id = aws_api_gateway_resource.seat_upgrade_seat_map.id
  http_method = aws_api_gateway_method.seat_upgrade_seat_map_options.http_method
  status_code = "200"
  response_parameters = {
    "method.response.header.Access-Control-Allow-Headers" = true
    "method.response.header.Access-Control-Allow-Methods" = true
    "method.response.header.Access-Control-Allow-Origin"  = true
  }
}

resource "aws_api_gateway_integration_response" "seat_upgrade_seat_map_options" {
  rest_api_id         = var.rest_api_id
  resource_id         = aws_api_gateway_resource.seat_upgrade_seat_map.id
  http_method         = aws_api_gateway_method.seat_upgrade_seat_map_options.http_method
  status_code         = aws_api_gateway_method_response.seat_upgrade_seat_map_options_200.status_code
  response_parameters = var.cors_response_parameters
}

# ═══════════════════════════════════════════════════════════════════════════
# Admin: seat-upgrade-announcement (broadcast email to paid registrants)
# ═══════════════════════════════════════════════════════════════════════════

resource "aws_api_gateway_resource" "seat_upgrade_announcement" {
  rest_api_id = var.rest_api_id
  parent_id   = var.admin_event_item_resource_id
  path_part   = "seat-upgrade-announcement"
}

resource "aws_api_gateway_method" "seat_upgrade_announcement_post" {
  rest_api_id   = var.rest_api_id
  resource_id   = aws_api_gateway_resource.seat_upgrade_announcement.id
  http_method   = "POST"
  authorization = "COGNITO_USER_POOLS"
  authorizer_id = var.cognito_authorizer_id
}

resource "aws_api_gateway_method" "seat_upgrade_announcement_options" {
  rest_api_id   = var.rest_api_id
  resource_id   = aws_api_gateway_resource.seat_upgrade_announcement.id
  http_method   = "OPTIONS"
  authorization = "NONE"
}

resource "aws_api_gateway_integration" "seat_upgrade_announcement_post" {
  rest_api_id             = var.rest_api_id
  resource_id             = aws_api_gateway_resource.seat_upgrade_announcement.id
  http_method             = aws_api_gateway_method.seat_upgrade_announcement_post.http_method
  integration_http_method = "POST"
  type                    = "AWS_PROXY"
  uri                     = aws_lambda_function.manage_seat_upgrades.invoke_arn
}

resource "aws_api_gateway_integration" "seat_upgrade_announcement_options" {
  rest_api_id       = var.rest_api_id
  resource_id       = aws_api_gateway_resource.seat_upgrade_announcement.id
  http_method       = aws_api_gateway_method.seat_upgrade_announcement_options.http_method
  type              = "MOCK"
  request_templates = { "application/json" = "{\"statusCode\": 200}" }
}

resource "aws_api_gateway_method_response" "seat_upgrade_announcement_options_200" {
  rest_api_id = var.rest_api_id
  resource_id = aws_api_gateway_resource.seat_upgrade_announcement.id
  http_method = aws_api_gateway_method.seat_upgrade_announcement_options.http_method
  status_code = "200"
  response_parameters = {
    "method.response.header.Access-Control-Allow-Headers" = true
    "method.response.header.Access-Control-Allow-Methods" = true
    "method.response.header.Access-Control-Allow-Origin"  = true
  }
}

resource "aws_api_gateway_integration_response" "seat_upgrade_announcement_options" {
  rest_api_id         = var.rest_api_id
  resource_id         = aws_api_gateway_resource.seat_upgrade_announcement.id
  http_method         = aws_api_gateway_method.seat_upgrade_announcement_options.http_method
  status_code         = aws_api_gateway_method_response.seat_upgrade_announcement_options_200.status_code
  response_parameters = var.cors_response_parameters
}

# ═══════════════════════════════════════════════════════════════════════════
# Admin: seat-upgrade-email-templates (collection + item + preview)
#
# Mounted under /admin/events/{eventType}/... like every other admin route
# in this module, even though templates are global to the plugin, not
# per-event — reusing this mount point needs no new API Gateway wiring
# (no new resource-id variable), and {eventType} is simply ignored by
# handle_email_templates. See docs/SEAT_UPGRADE_FEATURE.md.
# ═══════════════════════════════════════════════════════════════════════════

resource "aws_api_gateway_resource" "seat_upgrade_email_templates" {
  rest_api_id = var.rest_api_id
  parent_id   = var.admin_event_item_resource_id
  path_part   = "seat-upgrade-email-templates"
}

resource "aws_api_gateway_method" "seat_upgrade_email_templates_get" {
  rest_api_id   = var.rest_api_id
  resource_id   = aws_api_gateway_resource.seat_upgrade_email_templates.id
  http_method   = "GET"
  authorization = "COGNITO_USER_POOLS"
  authorizer_id = var.cognito_authorizer_id
}

resource "aws_api_gateway_method" "seat_upgrade_email_templates_options" {
  rest_api_id   = var.rest_api_id
  resource_id   = aws_api_gateway_resource.seat_upgrade_email_templates.id
  http_method   = "OPTIONS"
  authorization = "NONE"
}

resource "aws_api_gateway_integration" "seat_upgrade_email_templates_get" {
  rest_api_id             = var.rest_api_id
  resource_id             = aws_api_gateway_resource.seat_upgrade_email_templates.id
  http_method             = aws_api_gateway_method.seat_upgrade_email_templates_get.http_method
  integration_http_method = "POST"
  type                    = "AWS_PROXY"
  uri                     = aws_lambda_function.manage_seat_upgrades.invoke_arn
}

resource "aws_api_gateway_integration" "seat_upgrade_email_templates_options" {
  rest_api_id       = var.rest_api_id
  resource_id       = aws_api_gateway_resource.seat_upgrade_email_templates.id
  http_method       = aws_api_gateway_method.seat_upgrade_email_templates_options.http_method
  type              = "MOCK"
  request_templates = { "application/json" = "{\"statusCode\": 200}" }
}

resource "aws_api_gateway_method_response" "seat_upgrade_email_templates_options_200" {
  rest_api_id = var.rest_api_id
  resource_id = aws_api_gateway_resource.seat_upgrade_email_templates.id
  http_method = aws_api_gateway_method.seat_upgrade_email_templates_options.http_method
  status_code = "200"
  response_parameters = {
    "method.response.header.Access-Control-Allow-Headers" = true
    "method.response.header.Access-Control-Allow-Methods" = true
    "method.response.header.Access-Control-Allow-Origin"  = true
  }
}

resource "aws_api_gateway_integration_response" "seat_upgrade_email_templates_options" {
  rest_api_id         = var.rest_api_id
  resource_id         = aws_api_gateway_resource.seat_upgrade_email_templates.id
  http_method         = aws_api_gateway_method.seat_upgrade_email_templates_options.http_method
  status_code         = aws_api_gateway_method_response.seat_upgrade_email_templates_options_200.status_code
  response_parameters = var.cors_response_parameters
}

# ── /admin/events/{eventType}/seat-upgrade-email-templates/{templateKey} ───

resource "aws_api_gateway_resource" "seat_upgrade_email_template_item" {
  rest_api_id = var.rest_api_id
  parent_id   = aws_api_gateway_resource.seat_upgrade_email_templates.id
  path_part   = "{templateKey}"
}

resource "aws_api_gateway_method" "seat_upgrade_email_template_item_patch" {
  rest_api_id   = var.rest_api_id
  resource_id   = aws_api_gateway_resource.seat_upgrade_email_template_item.id
  http_method   = "PATCH"
  authorization = "COGNITO_USER_POOLS"
  authorizer_id = var.cognito_authorizer_id
}

resource "aws_api_gateway_method" "seat_upgrade_email_template_item_delete" {
  rest_api_id   = var.rest_api_id
  resource_id   = aws_api_gateway_resource.seat_upgrade_email_template_item.id
  http_method   = "DELETE"
  authorization = "COGNITO_USER_POOLS"
  authorizer_id = var.cognito_authorizer_id
}

resource "aws_api_gateway_method" "seat_upgrade_email_template_item_options" {
  rest_api_id   = var.rest_api_id
  resource_id   = aws_api_gateway_resource.seat_upgrade_email_template_item.id
  http_method   = "OPTIONS"
  authorization = "NONE"
}

resource "aws_api_gateway_integration" "seat_upgrade_email_template_item_patch" {
  rest_api_id             = var.rest_api_id
  resource_id             = aws_api_gateway_resource.seat_upgrade_email_template_item.id
  http_method             = aws_api_gateway_method.seat_upgrade_email_template_item_patch.http_method
  integration_http_method = "POST"
  type                    = "AWS_PROXY"
  uri                     = aws_lambda_function.manage_seat_upgrades.invoke_arn
}

resource "aws_api_gateway_integration" "seat_upgrade_email_template_item_delete" {
  rest_api_id             = var.rest_api_id
  resource_id             = aws_api_gateway_resource.seat_upgrade_email_template_item.id
  http_method             = aws_api_gateway_method.seat_upgrade_email_template_item_delete.http_method
  integration_http_method = "POST"
  type                    = "AWS_PROXY"
  uri                     = aws_lambda_function.manage_seat_upgrades.invoke_arn
}

resource "aws_api_gateway_integration" "seat_upgrade_email_template_item_options" {
  rest_api_id       = var.rest_api_id
  resource_id       = aws_api_gateway_resource.seat_upgrade_email_template_item.id
  http_method       = aws_api_gateway_method.seat_upgrade_email_template_item_options.http_method
  type              = "MOCK"
  request_templates = { "application/json" = "{\"statusCode\": 200}" }
}

resource "aws_api_gateway_method_response" "seat_upgrade_email_template_item_options_200" {
  rest_api_id = var.rest_api_id
  resource_id = aws_api_gateway_resource.seat_upgrade_email_template_item.id
  http_method = aws_api_gateway_method.seat_upgrade_email_template_item_options.http_method
  status_code = "200"
  response_parameters = {
    "method.response.header.Access-Control-Allow-Headers" = true
    "method.response.header.Access-Control-Allow-Methods" = true
    "method.response.header.Access-Control-Allow-Origin"  = true
  }
}

resource "aws_api_gateway_integration_response" "seat_upgrade_email_template_item_options" {
  rest_api_id         = var.rest_api_id
  resource_id         = aws_api_gateway_resource.seat_upgrade_email_template_item.id
  http_method         = aws_api_gateway_method.seat_upgrade_email_template_item_options.http_method
  status_code         = aws_api_gateway_method_response.seat_upgrade_email_template_item_options_200.status_code
  response_parameters = var.cors_response_parameters
}

# ── .../seat-upgrade-email-templates/{templateKey}/preview ─────────────────
# Renders a template against canned sample data using DRAFT (not-yet-saved)
# field values from the request body — the admin editor's live preview.

resource "aws_api_gateway_resource" "seat_upgrade_email_template_preview" {
  rest_api_id = var.rest_api_id
  parent_id   = aws_api_gateway_resource.seat_upgrade_email_template_item.id
  path_part   = "preview"
}

resource "aws_api_gateway_method" "seat_upgrade_email_template_preview_post" {
  rest_api_id   = var.rest_api_id
  resource_id   = aws_api_gateway_resource.seat_upgrade_email_template_preview.id
  http_method   = "POST"
  authorization = "COGNITO_USER_POOLS"
  authorizer_id = var.cognito_authorizer_id
}

resource "aws_api_gateway_method" "seat_upgrade_email_template_preview_options" {
  rest_api_id   = var.rest_api_id
  resource_id   = aws_api_gateway_resource.seat_upgrade_email_template_preview.id
  http_method   = "OPTIONS"
  authorization = "NONE"
}

resource "aws_api_gateway_integration" "seat_upgrade_email_template_preview_post" {
  rest_api_id             = var.rest_api_id
  resource_id             = aws_api_gateway_resource.seat_upgrade_email_template_preview.id
  http_method             = aws_api_gateway_method.seat_upgrade_email_template_preview_post.http_method
  integration_http_method = "POST"
  type                    = "AWS_PROXY"
  uri                     = aws_lambda_function.manage_seat_upgrades.invoke_arn
}

resource "aws_api_gateway_integration" "seat_upgrade_email_template_preview_options" {
  rest_api_id       = var.rest_api_id
  resource_id       = aws_api_gateway_resource.seat_upgrade_email_template_preview.id
  http_method       = aws_api_gateway_method.seat_upgrade_email_template_preview_options.http_method
  type              = "MOCK"
  request_templates = { "application/json" = "{\"statusCode\": 200}" }
}

resource "aws_api_gateway_method_response" "seat_upgrade_email_template_preview_options_200" {
  rest_api_id = var.rest_api_id
  resource_id = aws_api_gateway_resource.seat_upgrade_email_template_preview.id
  http_method = aws_api_gateway_method.seat_upgrade_email_template_preview_options.http_method
  status_code = "200"
  response_parameters = {
    "method.response.header.Access-Control-Allow-Headers" = true
    "method.response.header.Access-Control-Allow-Methods" = true
    "method.response.header.Access-Control-Allow-Origin"  = true
  }
}

resource "aws_api_gateway_integration_response" "seat_upgrade_email_template_preview_options" {
  rest_api_id         = var.rest_api_id
  resource_id         = aws_api_gateway_resource.seat_upgrade_email_template_preview.id
  http_method         = aws_api_gateway_method.seat_upgrade_email_template_preview_options.http_method
  status_code         = aws_api_gateway_method_response.seat_upgrade_email_template_preview_options_200.status_code
  response_parameters = var.cors_response_parameters
}

# ── /admin/events/{eventType}/seat-upgrade-tiers/{tierId} (item) ────────────

resource "aws_api_gateway_resource" "seat_upgrade_tier_item" {
  rest_api_id = var.rest_api_id
  parent_id   = aws_api_gateway_resource.seat_upgrade_tiers.id
  path_part   = "{tierId}"
}

resource "aws_api_gateway_method" "seat_upgrade_tier_item_get" {
  rest_api_id   = var.rest_api_id
  resource_id   = aws_api_gateway_resource.seat_upgrade_tier_item.id
  http_method   = "GET"
  authorization = "COGNITO_USER_POOLS"
  authorizer_id = var.cognito_authorizer_id
  request_parameters = {
    "method.request.path.tierId" = true
  }
}

resource "aws_api_gateway_method" "seat_upgrade_tier_item_patch" {
  rest_api_id   = var.rest_api_id
  resource_id   = aws_api_gateway_resource.seat_upgrade_tier_item.id
  http_method   = "PATCH"
  authorization = "COGNITO_USER_POOLS"
  authorizer_id = var.cognito_authorizer_id
  request_parameters = {
    "method.request.path.tierId" = true
  }
}

resource "aws_api_gateway_method" "seat_upgrade_tier_item_delete" {
  rest_api_id   = var.rest_api_id
  resource_id   = aws_api_gateway_resource.seat_upgrade_tier_item.id
  http_method   = "DELETE"
  authorization = "COGNITO_USER_POOLS"
  authorizer_id = var.cognito_authorizer_id
  request_parameters = {
    "method.request.path.tierId" = true
  }
}

resource "aws_api_gateway_method" "seat_upgrade_tier_item_options" {
  rest_api_id   = var.rest_api_id
  resource_id   = aws_api_gateway_resource.seat_upgrade_tier_item.id
  http_method   = "OPTIONS"
  authorization = "NONE"
}

resource "aws_api_gateway_integration" "seat_upgrade_tier_item_get" {
  rest_api_id             = var.rest_api_id
  resource_id             = aws_api_gateway_resource.seat_upgrade_tier_item.id
  http_method             = aws_api_gateway_method.seat_upgrade_tier_item_get.http_method
  integration_http_method = "POST"
  type                    = "AWS_PROXY"
  uri                     = aws_lambda_function.manage_seat_upgrades.invoke_arn
}

resource "aws_api_gateway_integration" "seat_upgrade_tier_item_patch" {
  rest_api_id             = var.rest_api_id
  resource_id             = aws_api_gateway_resource.seat_upgrade_tier_item.id
  http_method             = aws_api_gateway_method.seat_upgrade_tier_item_patch.http_method
  integration_http_method = "POST"
  type                    = "AWS_PROXY"
  uri                     = aws_lambda_function.manage_seat_upgrades.invoke_arn
}

resource "aws_api_gateway_integration" "seat_upgrade_tier_item_delete" {
  rest_api_id             = var.rest_api_id
  resource_id             = aws_api_gateway_resource.seat_upgrade_tier_item.id
  http_method             = aws_api_gateway_method.seat_upgrade_tier_item_delete.http_method
  integration_http_method = "POST"
  type                    = "AWS_PROXY"
  uri                     = aws_lambda_function.manage_seat_upgrades.invoke_arn
}

resource "aws_api_gateway_integration" "seat_upgrade_tier_item_options" {
  rest_api_id       = var.rest_api_id
  resource_id       = aws_api_gateway_resource.seat_upgrade_tier_item.id
  http_method       = aws_api_gateway_method.seat_upgrade_tier_item_options.http_method
  type              = "MOCK"
  request_templates = { "application/json" = "{\"statusCode\": 200}" }
}

resource "aws_api_gateway_method_response" "seat_upgrade_tier_item_options_200" {
  rest_api_id = var.rest_api_id
  resource_id = aws_api_gateway_resource.seat_upgrade_tier_item.id
  http_method = aws_api_gateway_method.seat_upgrade_tier_item_options.http_method
  status_code = "200"
  response_parameters = {
    "method.response.header.Access-Control-Allow-Headers" = true
    "method.response.header.Access-Control-Allow-Methods" = true
    "method.response.header.Access-Control-Allow-Origin"  = true
  }
}

resource "aws_api_gateway_integration_response" "seat_upgrade_tier_item_options" {
  rest_api_id         = var.rest_api_id
  resource_id         = aws_api_gateway_resource.seat_upgrade_tier_item.id
  http_method         = aws_api_gateway_method.seat_upgrade_tier_item_options.http_method
  status_code         = aws_api_gateway_method_response.seat_upgrade_tier_item_options_200.status_code
  response_parameters = var.cors_response_parameters
}

# ═══════════════════════════════════════════════════════════════════════════
# Admin: seat-upgrades — purchase requests (collection + item)
# ═══════════════════════════════════════════════════════════════════════════

resource "aws_api_gateway_resource" "admin_seat_upgrades" {
  rest_api_id = var.rest_api_id
  parent_id   = var.admin_event_item_resource_id
  path_part   = "seat-upgrades"
}

resource "aws_api_gateway_method" "admin_seat_upgrades_get" {
  rest_api_id   = var.rest_api_id
  resource_id   = aws_api_gateway_resource.admin_seat_upgrades.id
  http_method   = "GET"
  authorization = "COGNITO_USER_POOLS"
  authorizer_id = var.cognito_authorizer_id
}

resource "aws_api_gateway_method" "admin_seat_upgrades_options" {
  rest_api_id   = var.rest_api_id
  resource_id   = aws_api_gateway_resource.admin_seat_upgrades.id
  http_method   = "OPTIONS"
  authorization = "NONE"
}

resource "aws_api_gateway_integration" "admin_seat_upgrades_get" {
  rest_api_id             = var.rest_api_id
  resource_id             = aws_api_gateway_resource.admin_seat_upgrades.id
  http_method             = aws_api_gateway_method.admin_seat_upgrades_get.http_method
  integration_http_method = "POST"
  type                    = "AWS_PROXY"
  uri                     = aws_lambda_function.manage_seat_upgrades.invoke_arn
}

resource "aws_api_gateway_integration" "admin_seat_upgrades_options" {
  rest_api_id       = var.rest_api_id
  resource_id       = aws_api_gateway_resource.admin_seat_upgrades.id
  http_method       = aws_api_gateway_method.admin_seat_upgrades_options.http_method
  type              = "MOCK"
  request_templates = { "application/json" = "{\"statusCode\": 200}" }
}

resource "aws_api_gateway_method_response" "admin_seat_upgrades_options_200" {
  rest_api_id = var.rest_api_id
  resource_id = aws_api_gateway_resource.admin_seat_upgrades.id
  http_method = aws_api_gateway_method.admin_seat_upgrades_options.http_method
  status_code = "200"
  response_parameters = {
    "method.response.header.Access-Control-Allow-Headers" = true
    "method.response.header.Access-Control-Allow-Methods" = true
    "method.response.header.Access-Control-Allow-Origin"  = true
  }
}

resource "aws_api_gateway_integration_response" "admin_seat_upgrades_options" {
  rest_api_id         = var.rest_api_id
  resource_id         = aws_api_gateway_resource.admin_seat_upgrades.id
  http_method         = aws_api_gateway_method.admin_seat_upgrades_options.http_method
  status_code         = aws_api_gateway_method_response.admin_seat_upgrades_options_200.status_code
  response_parameters = var.cors_response_parameters
}

# ── /admin/events/{eventType}/seat-upgrades/{upgradeId} (item) ──────────────

resource "aws_api_gateway_resource" "admin_seat_upgrade_item" {
  rest_api_id = var.rest_api_id
  parent_id   = aws_api_gateway_resource.admin_seat_upgrades.id
  path_part   = "{upgradeId}"
}

resource "aws_api_gateway_method" "admin_seat_upgrade_item_patch" {
  rest_api_id   = var.rest_api_id
  resource_id   = aws_api_gateway_resource.admin_seat_upgrade_item.id
  http_method   = "PATCH"
  authorization = "COGNITO_USER_POOLS"
  authorizer_id = var.cognito_authorizer_id
  request_parameters = {
    "method.request.path.upgradeId" = true
  }
}

resource "aws_api_gateway_method" "admin_seat_upgrade_item_options" {
  rest_api_id   = var.rest_api_id
  resource_id   = aws_api_gateway_resource.admin_seat_upgrade_item.id
  http_method   = "OPTIONS"
  authorization = "NONE"
}

resource "aws_api_gateway_integration" "admin_seat_upgrade_item_patch" {
  rest_api_id             = var.rest_api_id
  resource_id             = aws_api_gateway_resource.admin_seat_upgrade_item.id
  http_method             = aws_api_gateway_method.admin_seat_upgrade_item_patch.http_method
  integration_http_method = "POST"
  type                    = "AWS_PROXY"
  uri                     = aws_lambda_function.manage_seat_upgrades.invoke_arn
}

resource "aws_api_gateway_integration" "admin_seat_upgrade_item_options" {
  rest_api_id       = var.rest_api_id
  resource_id       = aws_api_gateway_resource.admin_seat_upgrade_item.id
  http_method       = aws_api_gateway_method.admin_seat_upgrade_item_options.http_method
  type              = "MOCK"
  request_templates = { "application/json" = "{\"statusCode\": 200}" }
}

resource "aws_api_gateway_method_response" "admin_seat_upgrade_item_options_200" {
  rest_api_id = var.rest_api_id
  resource_id = aws_api_gateway_resource.admin_seat_upgrade_item.id
  http_method = aws_api_gateway_method.admin_seat_upgrade_item_options.http_method
  status_code = "200"
  response_parameters = {
    "method.response.header.Access-Control-Allow-Headers" = true
    "method.response.header.Access-Control-Allow-Methods" = true
    "method.response.header.Access-Control-Allow-Origin"  = true
  }
}

resource "aws_api_gateway_integration_response" "admin_seat_upgrade_item_options" {
  rest_api_id         = var.rest_api_id
  resource_id         = aws_api_gateway_resource.admin_seat_upgrade_item.id
  http_method         = aws_api_gateway_method.admin_seat_upgrade_item_options.http_method
  status_code         = aws_api_gateway_method_response.admin_seat_upgrade_item_options_200.status_code
  response_parameters = var.cors_response_parameters
}

# ═══════════════════════════════════════════════════════════════════════════
# Self-service: /my/seat-upgrades (+ item, + checkout)
# API Gateway authorization stays NONE throughout — the reg token isn't a
# Cognito credential, seat_upgrade's Lambda verifies it itself, same
# convention as the host's /my/registrations.
# ═══════════════════════════════════════════════════════════════════════════

resource "aws_api_gateway_resource" "my_seat_upgrades" {
  rest_api_id = var.rest_api_id
  parent_id   = var.my_resource_id
  path_part   = "seat-upgrades"
}

resource "aws_api_gateway_method" "my_seat_upgrades_get" {
  rest_api_id   = var.rest_api_id
  resource_id   = aws_api_gateway_resource.my_seat_upgrades.id
  http_method   = "GET"
  authorization = "NONE"
}

resource "aws_api_gateway_method" "my_seat_upgrades_post" {
  rest_api_id   = var.rest_api_id
  resource_id   = aws_api_gateway_resource.my_seat_upgrades.id
  http_method   = "POST"
  authorization = "NONE"
}

resource "aws_api_gateway_integration" "my_seat_upgrades_get" {
  rest_api_id             = var.rest_api_id
  resource_id             = aws_api_gateway_resource.my_seat_upgrades.id
  http_method             = aws_api_gateway_method.my_seat_upgrades_get.http_method
  integration_http_method = "POST"
  type                    = "AWS_PROXY"
  uri                     = aws_lambda_function.seat_upgrade.invoke_arn
}

resource "aws_api_gateway_integration" "my_seat_upgrades_post" {
  rest_api_id             = var.rest_api_id
  resource_id             = aws_api_gateway_resource.my_seat_upgrades.id
  http_method             = aws_api_gateway_method.my_seat_upgrades_post.http_method
  integration_http_method = "POST"
  type                    = "AWS_PROXY"
  uri                     = aws_lambda_function.seat_upgrade.invoke_arn
}

resource "aws_api_gateway_method" "my_seat_upgrades_options" {
  rest_api_id   = var.rest_api_id
  resource_id   = aws_api_gateway_resource.my_seat_upgrades.id
  http_method   = "OPTIONS"
  authorization = "NONE"
}

resource "aws_api_gateway_integration" "my_seat_upgrades_options" {
  rest_api_id       = var.rest_api_id
  resource_id       = aws_api_gateway_resource.my_seat_upgrades.id
  http_method       = aws_api_gateway_method.my_seat_upgrades_options.http_method
  type              = "MOCK"
  request_templates = { "application/json" = "{\"statusCode\": 200}" }
}

resource "aws_api_gateway_method_response" "my_seat_upgrades_options_200" {
  rest_api_id = var.rest_api_id
  resource_id = aws_api_gateway_resource.my_seat_upgrades.id
  http_method = aws_api_gateway_method.my_seat_upgrades_options.http_method
  status_code = "200"
  response_parameters = {
    "method.response.header.Access-Control-Allow-Headers" = true
    "method.response.header.Access-Control-Allow-Methods" = true
    "method.response.header.Access-Control-Allow-Origin"  = true
  }
}

resource "aws_api_gateway_integration_response" "my_seat_upgrades_options" {
  rest_api_id         = var.rest_api_id
  resource_id         = aws_api_gateway_resource.my_seat_upgrades.id
  http_method         = aws_api_gateway_method.my_seat_upgrades_options.http_method
  status_code         = aws_api_gateway_method_response.my_seat_upgrades_options_200.status_code
  response_parameters = var.cors_response_parameters
}

# ── /my/seat-upgrades/{upgradeId} ────────────────────────────────────────────

resource "aws_api_gateway_resource" "my_seat_upgrade_item" {
  rest_api_id = var.rest_api_id
  parent_id   = aws_api_gateway_resource.my_seat_upgrades.id
  path_part   = "{upgradeId}"
}

resource "aws_api_gateway_method" "my_seat_upgrade_get" {
  rest_api_id   = var.rest_api_id
  resource_id   = aws_api_gateway_resource.my_seat_upgrade_item.id
  http_method   = "GET"
  authorization = "NONE"
}

resource "aws_api_gateway_method" "my_seat_upgrade_delete" {
  rest_api_id   = var.rest_api_id
  resource_id   = aws_api_gateway_resource.my_seat_upgrade_item.id
  http_method   = "DELETE"
  authorization = "NONE"
}

resource "aws_api_gateway_integration" "my_seat_upgrade_get" {
  rest_api_id             = var.rest_api_id
  resource_id             = aws_api_gateway_resource.my_seat_upgrade_item.id
  http_method             = aws_api_gateway_method.my_seat_upgrade_get.http_method
  integration_http_method = "POST"
  type                    = "AWS_PROXY"
  uri                     = aws_lambda_function.seat_upgrade.invoke_arn
}

resource "aws_api_gateway_integration" "my_seat_upgrade_delete" {
  rest_api_id             = var.rest_api_id
  resource_id             = aws_api_gateway_resource.my_seat_upgrade_item.id
  http_method             = aws_api_gateway_method.my_seat_upgrade_delete.http_method
  integration_http_method = "POST"
  type                    = "AWS_PROXY"
  uri                     = aws_lambda_function.seat_upgrade.invoke_arn
}

resource "aws_api_gateway_method" "my_seat_upgrade_item_options" {
  rest_api_id   = var.rest_api_id
  resource_id   = aws_api_gateway_resource.my_seat_upgrade_item.id
  http_method   = "OPTIONS"
  authorization = "NONE"
}

resource "aws_api_gateway_integration" "my_seat_upgrade_item_options" {
  rest_api_id       = var.rest_api_id
  resource_id       = aws_api_gateway_resource.my_seat_upgrade_item.id
  http_method       = aws_api_gateway_method.my_seat_upgrade_item_options.http_method
  type              = "MOCK"
  request_templates = { "application/json" = "{\"statusCode\": 200}" }
}

resource "aws_api_gateway_method_response" "my_seat_upgrade_item_options_200" {
  rest_api_id = var.rest_api_id
  resource_id = aws_api_gateway_resource.my_seat_upgrade_item.id
  http_method = aws_api_gateway_method.my_seat_upgrade_item_options.http_method
  status_code = "200"
  response_parameters = {
    "method.response.header.Access-Control-Allow-Headers" = true
    "method.response.header.Access-Control-Allow-Methods" = true
    "method.response.header.Access-Control-Allow-Origin"  = true
  }
}

resource "aws_api_gateway_integration_response" "my_seat_upgrade_item_options" {
  rest_api_id         = var.rest_api_id
  resource_id         = aws_api_gateway_resource.my_seat_upgrade_item.id
  http_method         = aws_api_gateway_method.my_seat_upgrade_item_options.http_method
  status_code         = aws_api_gateway_method_response.my_seat_upgrade_item_options_200.status_code
  response_parameters = var.cors_response_parameters
}

# ── /my/seat-upgrades/{upgradeId}/checkout ───────────────────────────────────

resource "aws_api_gateway_resource" "my_seat_upgrade_checkout" {
  rest_api_id = var.rest_api_id
  parent_id   = aws_api_gateway_resource.my_seat_upgrade_item.id
  path_part   = "checkout"
}

resource "aws_api_gateway_method" "my_seat_upgrade_checkout_post" {
  rest_api_id   = var.rest_api_id
  resource_id   = aws_api_gateway_resource.my_seat_upgrade_checkout.id
  http_method   = "POST"
  authorization = "NONE"
}

resource "aws_api_gateway_integration" "my_seat_upgrade_checkout_post" {
  rest_api_id             = var.rest_api_id
  resource_id             = aws_api_gateway_resource.my_seat_upgrade_checkout.id
  http_method             = aws_api_gateway_method.my_seat_upgrade_checkout_post.http_method
  integration_http_method = "POST"
  type                    = "AWS_PROXY"
  uri                     = aws_lambda_function.seat_upgrade.invoke_arn
}

resource "aws_api_gateway_method" "my_seat_upgrade_checkout_options" {
  rest_api_id   = var.rest_api_id
  resource_id   = aws_api_gateway_resource.my_seat_upgrade_checkout.id
  http_method   = "OPTIONS"
  authorization = "NONE"
}

resource "aws_api_gateway_integration" "my_seat_upgrade_checkout_options" {
  rest_api_id       = var.rest_api_id
  resource_id       = aws_api_gateway_resource.my_seat_upgrade_checkout.id
  http_method       = aws_api_gateway_method.my_seat_upgrade_checkout_options.http_method
  type              = "MOCK"
  request_templates = { "application/json" = "{\"statusCode\": 200}" }
}

resource "aws_api_gateway_method_response" "my_seat_upgrade_checkout_options_200" {
  rest_api_id = var.rest_api_id
  resource_id = aws_api_gateway_resource.my_seat_upgrade_checkout.id
  http_method = aws_api_gateway_method.my_seat_upgrade_checkout_options.http_method
  status_code = "200"
  response_parameters = {
    "method.response.header.Access-Control-Allow-Headers" = true
    "method.response.header.Access-Control-Allow-Methods" = true
    "method.response.header.Access-Control-Allow-Origin"  = true
  }
}

resource "aws_api_gateway_integration_response" "my_seat_upgrade_checkout_options" {
  rest_api_id         = var.rest_api_id
  resource_id         = aws_api_gateway_resource.my_seat_upgrade_checkout.id
  http_method         = aws_api_gateway_method.my_seat_upgrade_checkout_options.http_method
  status_code         = aws_api_gateway_method_response.my_seat_upgrade_checkout_options_200.status_code
  response_parameters = var.cors_response_parameters
}

# ═══════════════════════════════════════════════════════════════════════════
# Public webhook: /webhooks/seat-upgrade/{provider}
# Mounted under the host-owned, shared /webhooks resource (var.webhooks_
# resource_id) as a sibling of every other plugin's own webhook route —
# not a "webhooks" resource this module creates itself, which would
# conflict with any other plugin doing the same under the API root.
# Independent of /admin and /my either way — a payment provider's servers
# call this directly, with no reg token or Cognito credential (see
# seat_upgrade_webhook/handler.py for how trust is established per
# provider instead).
# ═══════════════════════════════════════════════════════════════════════════

resource "aws_api_gateway_resource" "webhooks_seat_upgrade" {
  rest_api_id = var.rest_api_id
  parent_id   = var.webhooks_resource_id
  path_part   = "seat-upgrade"
}

resource "aws_api_gateway_resource" "webhooks_seat_upgrade_provider" {
  rest_api_id = var.rest_api_id
  parent_id   = aws_api_gateway_resource.webhooks_seat_upgrade.id
  path_part   = "{provider}"
}

resource "aws_api_gateway_method" "webhooks_seat_upgrade_post" {
  rest_api_id   = var.rest_api_id
  resource_id   = aws_api_gateway_resource.webhooks_seat_upgrade_provider.id
  http_method   = "POST"
  authorization = "NONE"
  request_parameters = {
    "method.request.path.provider" = true
  }
}

resource "aws_api_gateway_integration" "webhooks_seat_upgrade_post" {
  rest_api_id             = var.rest_api_id
  resource_id             = aws_api_gateway_resource.webhooks_seat_upgrade_provider.id
  http_method             = aws_api_gateway_method.webhooks_seat_upgrade_post.http_method
  integration_http_method = "POST"
  type                    = "AWS_PROXY"
  uri                     = aws_lambda_function.seat_upgrade_webhook.invoke_arn
}
