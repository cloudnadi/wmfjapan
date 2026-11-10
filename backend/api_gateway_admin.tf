# ─────────────────────────────────────────────────────────────────────────────
# Admin management routes (protected by the Cognito authorizer):
#   /admin/events                 GET (list)  POST (create)  PUT (update)
#   /admin/events/{eventType}     GET (view)  DELETE
#   /admin/events/{eventType}/entry-slots           GET (list)  POST (create)
#   /admin/events/{eventType}/entry-slots/{slotId}  GET PATCH DELETE
#   /admin/registration           POST (create/update)
#   /admin/registration/{registrationId}  DELETE
#   /admin/users                  GET (members + NPO status)
#   /admin/visits-summary         GET (visit analytics)
#   /admin/members                GET (list)  POST (upsert)
#   /admin/members/{email}        GET  PUT  DELETE
#   /admin/npo-membership/{memberid}             GET  POST
#   /admin/npo-membership/{memberid}/{txn}       PUT  DELETE
#
# Follows the same conventions as api_gateway.tf: AWS_PROXY integrations for the
# real methods, MOCK integrations for OPTIONS (CORS preflight). New methods and
# integrations are registered in the deployment triggers/depends_on at the
# bottom of this file's companion locals so a single apply redeploys the stage.
# ─────────────────────────────────────────────────────────────────────────────

# ── Lambda invoke permissions ────────────────────────────────────────────────

resource "aws_lambda_permission" "manage_events" {
  statement_id  = "AllowAPIGatewayInvokeEvents"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.manage_events.function_name
  principal     = "apigateway.amazonaws.com"
  source_arn    = "${aws_api_gateway_rest_api.main.execution_arn}/*/*"
}

resource "aws_lambda_permission" "manage_registrations" {
  statement_id  = "AllowAPIGatewayInvokeManageRegs"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.manage_registrations.function_name
  principal     = "apigateway.amazonaws.com"
  source_arn    = "${aws_api_gateway_rest_api.main.execution_arn}/*/*"
}

resource "aws_lambda_permission" "get_visit_summary" {
  statement_id  = "AllowAPIGatewayInvokeVisitSummary"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.get_visit_summary.function_name
  principal     = "apigateway.amazonaws.com"
  source_arn    = "${aws_api_gateway_rest_api.main.execution_arn}/*/*"
}

# ─────────────────────────────────────────────────────────────────────────────
# /admin/events  (collection)
# ─────────────────────────────────────────────────────────────────────────────

resource "aws_api_gateway_resource" "admin_events" {
  rest_api_id = aws_api_gateway_rest_api.main.id
  parent_id   = aws_api_gateway_resource.admin.id
  path_part   = "events"
}

resource "aws_api_gateway_method" "admin_events_get" {
  rest_api_id   = aws_api_gateway_rest_api.main.id
  resource_id   = aws_api_gateway_resource.admin_events.id
  http_method   = "GET"
  authorization = "COGNITO_USER_POOLS"
  authorizer_id = aws_api_gateway_authorizer.cognito.id
}

resource "aws_api_gateway_method" "admin_events_post" {
  rest_api_id   = aws_api_gateway_rest_api.main.id
  resource_id   = aws_api_gateway_resource.admin_events.id
  http_method   = "POST"
  authorization = "COGNITO_USER_POOLS"
  authorizer_id = aws_api_gateway_authorizer.cognito.id
}

resource "aws_api_gateway_method" "admin_events_put" {
  rest_api_id   = aws_api_gateway_rest_api.main.id
  resource_id   = aws_api_gateway_resource.admin_events.id
  http_method   = "PUT"
  authorization = "COGNITO_USER_POOLS"
  authorizer_id = aws_api_gateway_authorizer.cognito.id
}

resource "aws_api_gateway_integration" "admin_events_get" {
  rest_api_id             = aws_api_gateway_rest_api.main.id
  resource_id             = aws_api_gateway_resource.admin_events.id
  http_method             = aws_api_gateway_method.admin_events_get.http_method
  integration_http_method = "POST"
  type                    = "AWS_PROXY"
  uri                     = aws_lambda_function.manage_events.invoke_arn
}

resource "aws_api_gateway_integration" "admin_events_post" {
  rest_api_id             = aws_api_gateway_rest_api.main.id
  resource_id             = aws_api_gateway_resource.admin_events.id
  http_method             = aws_api_gateway_method.admin_events_post.http_method
  integration_http_method = "POST"
  type                    = "AWS_PROXY"
  uri                     = aws_lambda_function.manage_events.invoke_arn
}

resource "aws_api_gateway_integration" "admin_events_put" {
  rest_api_id             = aws_api_gateway_rest_api.main.id
  resource_id             = aws_api_gateway_resource.admin_events.id
  http_method             = aws_api_gateway_method.admin_events_put.http_method
  integration_http_method = "POST"
  type                    = "AWS_PROXY"
  uri                     = aws_lambda_function.manage_events.invoke_arn
}

resource "aws_api_gateway_method" "admin_events_options" {
  rest_api_id   = aws_api_gateway_rest_api.main.id
  resource_id   = aws_api_gateway_resource.admin_events.id
  http_method   = "OPTIONS"
  authorization = "NONE"
}

resource "aws_api_gateway_integration" "admin_events_options" {
  rest_api_id       = aws_api_gateway_rest_api.main.id
  resource_id       = aws_api_gateway_resource.admin_events.id
  http_method       = aws_api_gateway_method.admin_events_options.http_method
  type              = "MOCK"
  request_templates = { "application/json" = "{\"statusCode\": 200}" }
}

resource "aws_api_gateway_method_response" "admin_events_options_200" {
  rest_api_id = aws_api_gateway_rest_api.main.id
  resource_id = aws_api_gateway_resource.admin_events.id
  http_method = aws_api_gateway_method.admin_events_options.http_method
  status_code = "200"
  response_parameters = {
    "method.response.header.Access-Control-Allow-Headers" = true
    "method.response.header.Access-Control-Allow-Methods" = true
    "method.response.header.Access-Control-Allow-Origin"  = true
  }
}

resource "aws_api_gateway_integration_response" "admin_events_options" {
  rest_api_id         = aws_api_gateway_rest_api.main.id
  resource_id         = aws_api_gateway_resource.admin_events.id
  http_method         = aws_api_gateway_method.admin_events_options.http_method
  status_code         = aws_api_gateway_method_response.admin_events_options_200.status_code
  response_parameters = local.cors_headers
}

# ─────────────────────────────────────────────────────────────────────────────
# /admin/events/{eventType}  (item)
# ─────────────────────────────────────────────────────────────────────────────

resource "aws_api_gateway_resource" "admin_event_item" {
  rest_api_id = aws_api_gateway_rest_api.main.id
  parent_id   = aws_api_gateway_resource.admin_events.id
  path_part   = "{eventType}"
}

resource "aws_api_gateway_method" "admin_event_get" {
  rest_api_id   = aws_api_gateway_rest_api.main.id
  resource_id   = aws_api_gateway_resource.admin_event_item.id
  http_method   = "GET"
  authorization = "COGNITO_USER_POOLS"
  authorizer_id = aws_api_gateway_authorizer.cognito.id
  request_parameters = {
    "method.request.path.eventType" = true
  }
}

resource "aws_api_gateway_method" "admin_event_delete" {
  rest_api_id   = aws_api_gateway_rest_api.main.id
  resource_id   = aws_api_gateway_resource.admin_event_item.id
  http_method   = "DELETE"
  authorization = "COGNITO_USER_POOLS"
  authorizer_id = aws_api_gateway_authorizer.cognito.id
  request_parameters = {
    "method.request.path.eventType" = true
  }
}

resource "aws_api_gateway_integration" "admin_event_get" {
  rest_api_id             = aws_api_gateway_rest_api.main.id
  resource_id             = aws_api_gateway_resource.admin_event_item.id
  http_method             = aws_api_gateway_method.admin_event_get.http_method
  integration_http_method = "POST"
  type                    = "AWS_PROXY"
  uri                     = aws_lambda_function.manage_events.invoke_arn
}

resource "aws_api_gateway_integration" "admin_event_delete" {
  rest_api_id             = aws_api_gateway_rest_api.main.id
  resource_id             = aws_api_gateway_resource.admin_event_item.id
  http_method             = aws_api_gateway_method.admin_event_delete.http_method
  integration_http_method = "POST"
  type                    = "AWS_PROXY"
  uri                     = aws_lambda_function.manage_events.invoke_arn
}

resource "aws_api_gateway_method" "admin_event_options" {
  rest_api_id   = aws_api_gateway_rest_api.main.id
  resource_id   = aws_api_gateway_resource.admin_event_item.id
  http_method   = "OPTIONS"
  authorization = "NONE"
}

resource "aws_api_gateway_integration" "admin_event_options" {
  rest_api_id       = aws_api_gateway_rest_api.main.id
  resource_id       = aws_api_gateway_resource.admin_event_item.id
  http_method       = aws_api_gateway_method.admin_event_options.http_method
  type              = "MOCK"
  request_templates = { "application/json" = "{\"statusCode\": 200}" }
}

resource "aws_api_gateway_method_response" "admin_event_options_200" {
  rest_api_id = aws_api_gateway_rest_api.main.id
  resource_id = aws_api_gateway_resource.admin_event_item.id
  http_method = aws_api_gateway_method.admin_event_options.http_method
  status_code = "200"
  response_parameters = {
    "method.response.header.Access-Control-Allow-Headers" = true
    "method.response.header.Access-Control-Allow-Methods" = true
    "method.response.header.Access-Control-Allow-Origin"  = true
  }
}

resource "aws_api_gateway_integration_response" "admin_event_options" {
  rest_api_id         = aws_api_gateway_rest_api.main.id
  resource_id         = aws_api_gateway_resource.admin_event_item.id
  http_method         = aws_api_gateway_method.admin_event_options.http_method
  status_code         = aws_api_gateway_method_response.admin_event_options_200.status_code
  response_parameters = local.cors_headers
}

# ─────────────────────────────────────────────────────────────────────────────
# /admin/registrations/bulk  (POST — bulk payment/email actions)
# ─────────────────────────────────────────────────────────────────────────────

resource "aws_api_gateway_resource" "admin_registrations_bulk" {
  rest_api_id = aws_api_gateway_rest_api.main.id
  parent_id   = aws_api_gateway_resource.admin_registrations.id
  path_part   = "bulk"
}

resource "aws_api_gateway_method" "admin_registrations_bulk_post" {
  rest_api_id   = aws_api_gateway_rest_api.main.id
  resource_id   = aws_api_gateway_resource.admin_registrations_bulk.id
  http_method   = "POST"
  authorization = "COGNITO_USER_POOLS"
  authorizer_id = aws_api_gateway_authorizer.cognito.id
}

resource "aws_api_gateway_integration" "admin_registrations_bulk_post" {
  rest_api_id             = aws_api_gateway_rest_api.main.id
  resource_id             = aws_api_gateway_resource.admin_registrations_bulk.id
  http_method             = aws_api_gateway_method.admin_registrations_bulk_post.http_method
  integration_http_method = "POST"
  type                    = "AWS_PROXY"
  uri                     = aws_lambda_function.manage_registrations.invoke_arn
}

resource "aws_api_gateway_method" "admin_registrations_bulk_options" {
  rest_api_id   = aws_api_gateway_rest_api.main.id
  resource_id   = aws_api_gateway_resource.admin_registrations_bulk.id
  http_method   = "OPTIONS"
  authorization = "NONE"
}

resource "aws_api_gateway_integration" "admin_registrations_bulk_options" {
  rest_api_id       = aws_api_gateway_rest_api.main.id
  resource_id       = aws_api_gateway_resource.admin_registrations_bulk.id
  http_method       = aws_api_gateway_method.admin_registrations_bulk_options.http_method
  type              = "MOCK"
  request_templates = { "application/json" = "{\"statusCode\": 200}" }
}

resource "aws_api_gateway_method_response" "admin_registrations_bulk_options_200" {
  rest_api_id = aws_api_gateway_rest_api.main.id
  resource_id = aws_api_gateway_resource.admin_registrations_bulk.id
  http_method = aws_api_gateway_method.admin_registrations_bulk_options.http_method
  status_code = "200"
  response_parameters = {
    "method.response.header.Access-Control-Allow-Headers" = true
    "method.response.header.Access-Control-Allow-Methods" = true
    "method.response.header.Access-Control-Allow-Origin"  = true
  }
}

resource "aws_api_gateway_integration_response" "admin_registrations_bulk_options" {
  rest_api_id         = aws_api_gateway_rest_api.main.id
  resource_id         = aws_api_gateway_resource.admin_registrations_bulk.id
  http_method         = aws_api_gateway_method.admin_registrations_bulk_options.http_method
  status_code         = aws_api_gateway_method_response.admin_registrations_bulk_options_200.status_code
  response_parameters = local.cors_headers
}

# ─────────────────────────────────────────────────────────────────────────────
# /admin/registrations/reassign  (POST — move a registration to a new entry slot)
# ─────────────────────────────────────────────────────────────────────────────

resource "aws_api_gateway_resource" "admin_registrations_reassign" {
  rest_api_id = aws_api_gateway_rest_api.main.id
  parent_id   = aws_api_gateway_resource.admin_registrations.id
  path_part   = "reassign"
}

resource "aws_api_gateway_method" "admin_registrations_reassign_post" {
  rest_api_id   = aws_api_gateway_rest_api.main.id
  resource_id   = aws_api_gateway_resource.admin_registrations_reassign.id
  http_method   = "POST"
  authorization = "COGNITO_USER_POOLS"
  authorizer_id = aws_api_gateway_authorizer.cognito.id
}

resource "aws_api_gateway_integration" "admin_registrations_reassign_post" {
  rest_api_id             = aws_api_gateway_rest_api.main.id
  resource_id             = aws_api_gateway_resource.admin_registrations_reassign.id
  http_method             = aws_api_gateway_method.admin_registrations_reassign_post.http_method
  integration_http_method = "POST"
  type                    = "AWS_PROXY"
  uri                     = aws_lambda_function.manage_registrations.invoke_arn
}

resource "aws_api_gateway_method" "admin_registrations_reassign_options" {
  rest_api_id   = aws_api_gateway_rest_api.main.id
  resource_id   = aws_api_gateway_resource.admin_registrations_reassign.id
  http_method   = "OPTIONS"
  authorization = "NONE"
}

resource "aws_api_gateway_integration" "admin_registrations_reassign_options" {
  rest_api_id       = aws_api_gateway_rest_api.main.id
  resource_id       = aws_api_gateway_resource.admin_registrations_reassign.id
  http_method       = aws_api_gateway_method.admin_registrations_reassign_options.http_method
  type              = "MOCK"
  request_templates = { "application/json" = "{\"statusCode\": 200}" }
}

resource "aws_api_gateway_method_response" "admin_registrations_reassign_options_200" {
  rest_api_id = aws_api_gateway_rest_api.main.id
  resource_id = aws_api_gateway_resource.admin_registrations_reassign.id
  http_method = aws_api_gateway_method.admin_registrations_reassign_options.http_method
  status_code = "200"
  response_parameters = {
    "method.response.header.Access-Control-Allow-Headers" = true
    "method.response.header.Access-Control-Allow-Methods" = true
    "method.response.header.Access-Control-Allow-Origin"  = true
  }
}

resource "aws_api_gateway_integration_response" "admin_registrations_reassign_options" {
  rest_api_id         = aws_api_gateway_rest_api.main.id
  resource_id         = aws_api_gateway_resource.admin_registrations_reassign.id
  http_method         = aws_api_gateway_method.admin_registrations_reassign_options.http_method
  status_code         = aws_api_gateway_method_response.admin_registrations_reassign_options_200.status_code
  response_parameters = local.cors_headers
}

# ─────────────────────────────────────────────────────────────────────────────
# /admin/registrations/allocate-seats  (GET — this registration's editable/
# locked seats + seat allowance, for the "Allocate Seats" modal to open with;
# POST — admin directly assigns Celebrity Event hall seats to a registration,
# bypassing self-service payment/window)
# ─────────────────────────────────────────────────────────────────────────────

resource "aws_api_gateway_resource" "admin_registrations_allocate_seats" {
  rest_api_id = aws_api_gateway_rest_api.main.id
  parent_id   = aws_api_gateway_resource.admin_registrations.id
  path_part   = "allocate-seats"
}

resource "aws_api_gateway_method" "admin_registrations_allocate_seats_get" {
  rest_api_id   = aws_api_gateway_rest_api.main.id
  resource_id   = aws_api_gateway_resource.admin_registrations_allocate_seats.id
  http_method   = "GET"
  authorization = "COGNITO_USER_POOLS"
  authorizer_id = aws_api_gateway_authorizer.cognito.id
  request_parameters = {
    "method.request.querystring.registrationId" = true
  }
}

resource "aws_api_gateway_integration" "admin_registrations_allocate_seats_get" {
  rest_api_id             = aws_api_gateway_rest_api.main.id
  resource_id             = aws_api_gateway_resource.admin_registrations_allocate_seats.id
  http_method             = aws_api_gateway_method.admin_registrations_allocate_seats_get.http_method
  integration_http_method = "POST"
  type                    = "AWS_PROXY"
  uri                     = aws_lambda_function.manage_registrations.invoke_arn
}

resource "aws_api_gateway_method" "admin_registrations_allocate_seats_post" {
  rest_api_id   = aws_api_gateway_rest_api.main.id
  resource_id   = aws_api_gateway_resource.admin_registrations_allocate_seats.id
  http_method   = "POST"
  authorization = "COGNITO_USER_POOLS"
  authorizer_id = aws_api_gateway_authorizer.cognito.id
}

resource "aws_api_gateway_integration" "admin_registrations_allocate_seats_post" {
  rest_api_id             = aws_api_gateway_rest_api.main.id
  resource_id             = aws_api_gateway_resource.admin_registrations_allocate_seats.id
  http_method             = aws_api_gateway_method.admin_registrations_allocate_seats_post.http_method
  integration_http_method = "POST"
  type                    = "AWS_PROXY"
  uri                     = aws_lambda_function.manage_registrations.invoke_arn
}

resource "aws_api_gateway_method" "admin_registrations_allocate_seats_options" {
  rest_api_id   = aws_api_gateway_rest_api.main.id
  resource_id   = aws_api_gateway_resource.admin_registrations_allocate_seats.id
  http_method   = "OPTIONS"
  authorization = "NONE"
}

resource "aws_api_gateway_integration" "admin_registrations_allocate_seats_options" {
  rest_api_id       = aws_api_gateway_rest_api.main.id
  resource_id       = aws_api_gateway_resource.admin_registrations_allocate_seats.id
  http_method       = aws_api_gateway_method.admin_registrations_allocate_seats_options.http_method
  type              = "MOCK"
  request_templates = { "application/json" = "{\"statusCode\": 200}" }
}

resource "aws_api_gateway_method_response" "admin_registrations_allocate_seats_options_200" {
  rest_api_id = aws_api_gateway_rest_api.main.id
  resource_id = aws_api_gateway_resource.admin_registrations_allocate_seats.id
  http_method = aws_api_gateway_method.admin_registrations_allocate_seats_options.http_method
  status_code = "200"
  response_parameters = {
    "method.response.header.Access-Control-Allow-Headers" = true
    "method.response.header.Access-Control-Allow-Methods" = true
    "method.response.header.Access-Control-Allow-Origin"  = true
  }
}

resource "aws_api_gateway_integration_response" "admin_registrations_allocate_seats_options" {
  rest_api_id         = aws_api_gateway_rest_api.main.id
  resource_id         = aws_api_gateway_resource.admin_registrations_allocate_seats.id
  http_method         = aws_api_gateway_method.admin_registrations_allocate_seats_options.http_method
  status_code         = aws_api_gateway_method_response.admin_registrations_allocate_seats_options_200.status_code
  response_parameters = local.cors_headers
}

# ─────────────────────────────────────────────────────────────────────────────
# /admin/registrations/seat-selection-announcement  (POST — body {eventType};
# emails every paid event/combo registrant that seat selection has opened)
# ─────────────────────────────────────────────────────────────────────────────

resource "aws_api_gateway_resource" "admin_registrations_seat_selection_announcement" {
  rest_api_id = aws_api_gateway_rest_api.main.id
  parent_id   = aws_api_gateway_resource.admin_registrations.id
  path_part   = "seat-selection-announcement"
}

resource "aws_api_gateway_method" "admin_registrations_seat_selection_announcement_post" {
  rest_api_id   = aws_api_gateway_rest_api.main.id
  resource_id   = aws_api_gateway_resource.admin_registrations_seat_selection_announcement.id
  http_method   = "POST"
  authorization = "COGNITO_USER_POOLS"
  authorizer_id = aws_api_gateway_authorizer.cognito.id
}

resource "aws_api_gateway_integration" "admin_registrations_seat_selection_announcement_post" {
  rest_api_id             = aws_api_gateway_rest_api.main.id
  resource_id             = aws_api_gateway_resource.admin_registrations_seat_selection_announcement.id
  http_method             = aws_api_gateway_method.admin_registrations_seat_selection_announcement_post.http_method
  integration_http_method = "POST"
  type                    = "AWS_PROXY"
  uri                     = aws_lambda_function.manage_registrations.invoke_arn
}

resource "aws_api_gateway_method" "admin_registrations_seat_selection_announcement_options" {
  rest_api_id   = aws_api_gateway_rest_api.main.id
  resource_id   = aws_api_gateway_resource.admin_registrations_seat_selection_announcement.id
  http_method   = "OPTIONS"
  authorization = "NONE"
}

resource "aws_api_gateway_integration" "admin_registrations_seat_selection_announcement_options" {
  rest_api_id       = aws_api_gateway_rest_api.main.id
  resource_id       = aws_api_gateway_resource.admin_registrations_seat_selection_announcement.id
  http_method       = aws_api_gateway_method.admin_registrations_seat_selection_announcement_options.http_method
  type              = "MOCK"
  request_templates = { "application/json" = "{\"statusCode\": 200}" }
}

resource "aws_api_gateway_method_response" "admin_registrations_seat_selection_announcement_options_200" {
  rest_api_id = aws_api_gateway_rest_api.main.id
  resource_id = aws_api_gateway_resource.admin_registrations_seat_selection_announcement.id
  http_method = aws_api_gateway_method.admin_registrations_seat_selection_announcement_options.http_method
  status_code = "200"
  response_parameters = {
    "method.response.header.Access-Control-Allow-Headers" = true
    "method.response.header.Access-Control-Allow-Methods" = true
    "method.response.header.Access-Control-Allow-Origin"  = true
  }
}

resource "aws_api_gateway_integration_response" "admin_registrations_seat_selection_announcement_options" {
  rest_api_id         = aws_api_gateway_rest_api.main.id
  resource_id         = aws_api_gateway_resource.admin_registrations_seat_selection_announcement.id
  http_method         = aws_api_gateway_method.admin_registrations_seat_selection_announcement_options.http_method
  status_code         = aws_api_gateway_method_response.admin_registrations_seat_selection_announcement_options_200.status_code
  response_parameters = local.cors_headers
}

# /admin/check-in (POST — resolve a pass or consume check-in quantity)
resource "aws_api_gateway_resource" "admin_check_in" {
  rest_api_id = aws_api_gateway_rest_api.main.id
  parent_id   = aws_api_gateway_resource.admin.id
  path_part   = "check-in"
}

resource "aws_api_gateway_method" "admin_check_in_post" {
  rest_api_id   = aws_api_gateway_rest_api.main.id
  resource_id   = aws_api_gateway_resource.admin_check_in.id
  http_method   = "POST"
  authorization = "COGNITO_USER_POOLS"
  authorizer_id = aws_api_gateway_authorizer.cognito.id
}

resource "aws_api_gateway_integration" "admin_check_in_post" {
  rest_api_id             = aws_api_gateway_rest_api.main.id
  resource_id             = aws_api_gateway_resource.admin_check_in.id
  http_method             = aws_api_gateway_method.admin_check_in_post.http_method
  integration_http_method = "POST"
  type                    = "AWS_PROXY"
  uri                     = aws_lambda_function.manage_registrations.invoke_arn
}

resource "aws_api_gateway_method" "admin_check_in_options" {
  rest_api_id   = aws_api_gateway_rest_api.main.id
  resource_id   = aws_api_gateway_resource.admin_check_in.id
  http_method   = "OPTIONS"
  authorization = "NONE"
}

resource "aws_api_gateway_integration" "admin_check_in_options" {
  rest_api_id       = aws_api_gateway_rest_api.main.id
  resource_id       = aws_api_gateway_resource.admin_check_in.id
  http_method       = aws_api_gateway_method.admin_check_in_options.http_method
  type              = "MOCK"
  request_templates = { "application/json" = "{\"statusCode\": 200}" }
}

resource "aws_api_gateway_method_response" "admin_check_in_options_200" {
  rest_api_id = aws_api_gateway_rest_api.main.id
  resource_id = aws_api_gateway_resource.admin_check_in.id
  http_method = aws_api_gateway_method.admin_check_in_options.http_method
  status_code = "200"
  response_parameters = {
    "method.response.header.Access-Control-Allow-Headers" = true
    "method.response.header.Access-Control-Allow-Methods" = true
    "method.response.header.Access-Control-Allow-Origin"  = true
  }
}

resource "aws_api_gateway_integration_response" "admin_check_in_options" {
  rest_api_id         = aws_api_gateway_rest_api.main.id
  resource_id         = aws_api_gateway_resource.admin_check_in.id
  http_method         = aws_api_gateway_method.admin_check_in_options.http_method
  status_code         = aws_api_gateway_method_response.admin_check_in_options_200.status_code
  response_parameters = local.cors_headers
}

# ─────────────────────────────────────────────────────────────────────────────
# /admin/registration  (collection — create/update)
# ─────────────────────────────────────────────────────────────────────────────

resource "aws_api_gateway_resource" "admin_registration" {
  rest_api_id = aws_api_gateway_rest_api.main.id
  parent_id   = aws_api_gateway_resource.admin.id
  path_part   = "registration"
}

resource "aws_api_gateway_method" "admin_registration_post" {
  rest_api_id   = aws_api_gateway_rest_api.main.id
  resource_id   = aws_api_gateway_resource.admin_registration.id
  http_method   = "POST"
  authorization = "COGNITO_USER_POOLS"
  authorizer_id = aws_api_gateway_authorizer.cognito.id
}

resource "aws_api_gateway_integration" "admin_registration_post" {
  rest_api_id             = aws_api_gateway_rest_api.main.id
  resource_id             = aws_api_gateway_resource.admin_registration.id
  http_method             = aws_api_gateway_method.admin_registration_post.http_method
  integration_http_method = "POST"
  type                    = "AWS_PROXY"
  uri                     = aws_lambda_function.manage_registrations.invoke_arn
}

resource "aws_api_gateway_method" "admin_registration_options" {
  rest_api_id   = aws_api_gateway_rest_api.main.id
  resource_id   = aws_api_gateway_resource.admin_registration.id
  http_method   = "OPTIONS"
  authorization = "NONE"
}

resource "aws_api_gateway_integration" "admin_registration_options" {
  rest_api_id       = aws_api_gateway_rest_api.main.id
  resource_id       = aws_api_gateway_resource.admin_registration.id
  http_method       = aws_api_gateway_method.admin_registration_options.http_method
  type              = "MOCK"
  request_templates = { "application/json" = "{\"statusCode\": 200}" }
}

resource "aws_api_gateway_method_response" "admin_registration_options_200" {
  rest_api_id = aws_api_gateway_rest_api.main.id
  resource_id = aws_api_gateway_resource.admin_registration.id
  http_method = aws_api_gateway_method.admin_registration_options.http_method
  status_code = "200"
  response_parameters = {
    "method.response.header.Access-Control-Allow-Headers" = true
    "method.response.header.Access-Control-Allow-Methods" = true
    "method.response.header.Access-Control-Allow-Origin"  = true
  }
}

resource "aws_api_gateway_integration_response" "admin_registration_options" {
  rest_api_id         = aws_api_gateway_rest_api.main.id
  resource_id         = aws_api_gateway_resource.admin_registration.id
  http_method         = aws_api_gateway_method.admin_registration_options.http_method
  status_code         = aws_api_gateway_method_response.admin_registration_options_200.status_code
  response_parameters = local.cors_headers
}

# ─────────────────────────────────────────────────────────────────────────────
# /admin/registration/{registrationId}  (item — delete)
# ─────────────────────────────────────────────────────────────────────────────

resource "aws_api_gateway_resource" "admin_registration_item" {
  rest_api_id = aws_api_gateway_rest_api.main.id
  parent_id   = aws_api_gateway_resource.admin_registration.id
  path_part   = "{registrationId}"
}

resource "aws_api_gateway_method" "admin_registration_delete" {
  rest_api_id   = aws_api_gateway_rest_api.main.id
  resource_id   = aws_api_gateway_resource.admin_registration_item.id
  http_method   = "DELETE"
  authorization = "COGNITO_USER_POOLS"
  authorizer_id = aws_api_gateway_authorizer.cognito.id
  request_parameters = {
    "method.request.path.registrationId" = true
  }
}

resource "aws_api_gateway_integration" "admin_registration_delete" {
  rest_api_id             = aws_api_gateway_rest_api.main.id
  resource_id             = aws_api_gateway_resource.admin_registration_item.id
  http_method             = aws_api_gateway_method.admin_registration_delete.http_method
  integration_http_method = "POST"
  type                    = "AWS_PROXY"
  uri                     = aws_lambda_function.manage_registrations.invoke_arn
}

resource "aws_api_gateway_method" "admin_registration_item_options" {
  rest_api_id   = aws_api_gateway_rest_api.main.id
  resource_id   = aws_api_gateway_resource.admin_registration_item.id
  http_method   = "OPTIONS"
  authorization = "NONE"
}

resource "aws_api_gateway_integration" "admin_registration_item_options" {
  rest_api_id       = aws_api_gateway_rest_api.main.id
  resource_id       = aws_api_gateway_resource.admin_registration_item.id
  http_method       = aws_api_gateway_method.admin_registration_item_options.http_method
  type              = "MOCK"
  request_templates = { "application/json" = "{\"statusCode\": 200}" }
}

resource "aws_api_gateway_method_response" "admin_registration_item_options_200" {
  rest_api_id = aws_api_gateway_rest_api.main.id
  resource_id = aws_api_gateway_resource.admin_registration_item.id
  http_method = aws_api_gateway_method.admin_registration_item_options.http_method
  status_code = "200"
  response_parameters = {
    "method.response.header.Access-Control-Allow-Headers" = true
    "method.response.header.Access-Control-Allow-Methods" = true
    "method.response.header.Access-Control-Allow-Origin"  = true
  }
}

resource "aws_api_gateway_integration_response" "admin_registration_item_options" {
  rest_api_id         = aws_api_gateway_rest_api.main.id
  resource_id         = aws_api_gateway_resource.admin_registration_item.id
  http_method         = aws_api_gateway_method.admin_registration_item_options.http_method
  status_code         = aws_api_gateway_method_response.admin_registration_item_options_200.status_code
  response_parameters = local.cors_headers
}

# ─────────────────────────────────────────────────────────────────────────────
# /admin/users  (GET — registrants + NPO status)
# ─────────────────────────────────────────────────────────────────────────────

resource "aws_api_gateway_resource" "admin_users" {
  rest_api_id = aws_api_gateway_rest_api.main.id
  parent_id   = aws_api_gateway_resource.admin.id
  path_part   = "users"
}

resource "aws_api_gateway_method" "admin_users_get" {
  rest_api_id   = aws_api_gateway_rest_api.main.id
  resource_id   = aws_api_gateway_resource.admin_users.id
  http_method   = "GET"
  authorization = "COGNITO_USER_POOLS"
  authorizer_id = aws_api_gateway_authorizer.cognito.id
}

resource "aws_api_gateway_integration" "admin_users_get" {
  rest_api_id             = aws_api_gateway_rest_api.main.id
  resource_id             = aws_api_gateway_resource.admin_users.id
  http_method             = aws_api_gateway_method.admin_users_get.http_method
  integration_http_method = "POST"
  type                    = "AWS_PROXY"
  uri                     = aws_lambda_function.manage_registrations.invoke_arn
}

resource "aws_api_gateway_method" "admin_users_options" {
  rest_api_id   = aws_api_gateway_rest_api.main.id
  resource_id   = aws_api_gateway_resource.admin_users.id
  http_method   = "OPTIONS"
  authorization = "NONE"
}

resource "aws_api_gateway_integration" "admin_users_options" {
  rest_api_id       = aws_api_gateway_rest_api.main.id
  resource_id       = aws_api_gateway_resource.admin_users.id
  http_method       = aws_api_gateway_method.admin_users_options.http_method
  type              = "MOCK"
  request_templates = { "application/json" = "{\"statusCode\": 200}" }
}

resource "aws_api_gateway_method_response" "admin_users_options_200" {
  rest_api_id = aws_api_gateway_rest_api.main.id
  resource_id = aws_api_gateway_resource.admin_users.id
  http_method = aws_api_gateway_method.admin_users_options.http_method
  status_code = "200"
  response_parameters = {
    "method.response.header.Access-Control-Allow-Headers" = true
    "method.response.header.Access-Control-Allow-Methods" = true
    "method.response.header.Access-Control-Allow-Origin"  = true
  }
}

resource "aws_api_gateway_integration_response" "admin_users_options" {
  rest_api_id         = aws_api_gateway_rest_api.main.id
  resource_id         = aws_api_gateway_resource.admin_users.id
  http_method         = aws_api_gateway_method.admin_users_options.http_method
  status_code         = aws_api_gateway_method_response.admin_users_options_200.status_code
  response_parameters = local.cors_headers
}

# ─────────────────────────────────────────────────────────────────────────────
# /admin/visits-summary  (GET — aggregated visit analytics)
# ─────────────────────────────────────────────────────────────────────────────

resource "aws_api_gateway_resource" "admin_visits_summary" {
  rest_api_id = aws_api_gateway_rest_api.main.id
  parent_id   = aws_api_gateway_resource.admin.id
  path_part   = "visits-summary"
}

resource "aws_api_gateway_method" "admin_visits_summary_get" {
  rest_api_id   = aws_api_gateway_rest_api.main.id
  resource_id   = aws_api_gateway_resource.admin_visits_summary.id
  http_method   = "GET"
  authorization = "COGNITO_USER_POOLS"
  authorizer_id = aws_api_gateway_authorizer.cognito.id
  request_parameters = {
    "method.request.querystring.days" = false
  }
}

resource "aws_api_gateway_integration" "admin_visits_summary_get" {
  rest_api_id             = aws_api_gateway_rest_api.main.id
  resource_id             = aws_api_gateway_resource.admin_visits_summary.id
  http_method             = aws_api_gateway_method.admin_visits_summary_get.http_method
  integration_http_method = "POST"
  type                    = "AWS_PROXY"
  uri                     = aws_lambda_function.get_visit_summary.invoke_arn
}

resource "aws_api_gateway_method" "admin_visits_summary_options" {
  rest_api_id   = aws_api_gateway_rest_api.main.id
  resource_id   = aws_api_gateway_resource.admin_visits_summary.id
  http_method   = "OPTIONS"
  authorization = "NONE"
}

resource "aws_api_gateway_integration" "admin_visits_summary_options" {
  rest_api_id       = aws_api_gateway_rest_api.main.id
  resource_id       = aws_api_gateway_resource.admin_visits_summary.id
  http_method       = aws_api_gateway_method.admin_visits_summary_options.http_method
  type              = "MOCK"
  request_templates = { "application/json" = "{\"statusCode\": 200}" }
}

resource "aws_api_gateway_method_response" "admin_visits_summary_options_200" {
  rest_api_id = aws_api_gateway_rest_api.main.id
  resource_id = aws_api_gateway_resource.admin_visits_summary.id
  http_method = aws_api_gateway_method.admin_visits_summary_options.http_method
  status_code = "200"
  response_parameters = {
    "method.response.header.Access-Control-Allow-Headers" = true
    "method.response.header.Access-Control-Allow-Methods" = true
    "method.response.header.Access-Control-Allow-Origin"  = true
  }
}

resource "aws_api_gateway_integration_response" "admin_visits_summary_options" {
  rest_api_id         = aws_api_gateway_rest_api.main.id
  resource_id         = aws_api_gateway_resource.admin_visits_summary.id
  http_method         = aws_api_gateway_method.admin_visits_summary_options.http_method
  status_code         = aws_api_gateway_method_response.admin_visits_summary_options_200.status_code
  response_parameters = local.cors_headers
}

# ─────────────────────────────────────────────────────────────────────────────
# Lambda invoke permission for manage_members
# ─────────────────────────────────────────────────────────────────────────────

resource "aws_lambda_permission" "manage_members" {
  statement_id  = "AllowAPIGatewayInvokeManageMembers"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.manage_members.function_name
  principal     = "apigateway.amazonaws.com"
  source_arn    = "${aws_api_gateway_rest_api.main.execution_arn}/*/*"
}

resource "aws_lambda_permission" "manage_celebrity_dinner" {
  statement_id  = "AllowAPIGatewayInvokeManageCelebrityDinner"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.manage_celebrity_dinner.function_name
  principal     = "apigateway.amazonaws.com"
  source_arn    = "${aws_api_gateway_rest_api.main.execution_arn}/*/*"
}

resource "aws_lambda_permission" "manage_entry_slots" {
  statement_id  = "AllowAPIGatewayInvokeManageEntrySlots"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.manage_entry_slots.function_name
  principal     = "apigateway.amazonaws.com"
  source_arn    = "${aws_api_gateway_rest_api.main.execution_arn}/*/*"
}

resource "aws_lambda_permission" "release_buffer" {
  statement_id  = "AllowAPIGatewayInvokeReleaseBuffer"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.release_buffer.function_name
  principal     = "apigateway.amazonaws.com"
  source_arn    = "${aws_api_gateway_rest_api.main.execution_arn}/*/*"
}

resource "aws_lambda_permission" "manage_reconciliation" {
  statement_id  = "AllowAPIGatewayInvokeManageReconciliation"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.manage_reconciliation.function_name
  principal     = "apigateway.amazonaws.com"
  source_arn    = "${aws_api_gateway_rest_api.main.execution_arn}/*/*"
}

# ─────────────────────────────────────────────────────────────────────────────
# /admin/members  (collection — list + upsert)
# ─────────────────────────────────────────────────────────────────────────────

resource "aws_api_gateway_resource" "admin_members" {
  rest_api_id = aws_api_gateway_rest_api.main.id
  parent_id   = aws_api_gateway_resource.admin.id
  path_part   = "members"
}

resource "aws_api_gateway_method" "admin_members_get" {
  rest_api_id   = aws_api_gateway_rest_api.main.id
  resource_id   = aws_api_gateway_resource.admin_members.id
  http_method   = "GET"
  authorization = "COGNITO_USER_POOLS"
  authorizer_id = aws_api_gateway_authorizer.cognito.id
}

resource "aws_api_gateway_method" "admin_members_post" {
  rest_api_id   = aws_api_gateway_rest_api.main.id
  resource_id   = aws_api_gateway_resource.admin_members.id
  http_method   = "POST"
  authorization = "COGNITO_USER_POOLS"
  authorizer_id = aws_api_gateway_authorizer.cognito.id
}

resource "aws_api_gateway_integration" "admin_members_get" {
  rest_api_id             = aws_api_gateway_rest_api.main.id
  resource_id             = aws_api_gateway_resource.admin_members.id
  http_method             = aws_api_gateway_method.admin_members_get.http_method
  integration_http_method = "POST"
  type                    = "AWS_PROXY"
  uri                     = aws_lambda_function.manage_members.invoke_arn
}

resource "aws_api_gateway_integration" "admin_members_post" {
  rest_api_id             = aws_api_gateway_rest_api.main.id
  resource_id             = aws_api_gateway_resource.admin_members.id
  http_method             = aws_api_gateway_method.admin_members_post.http_method
  integration_http_method = "POST"
  type                    = "AWS_PROXY"
  uri                     = aws_lambda_function.manage_members.invoke_arn
}

resource "aws_api_gateway_method" "admin_members_options" {
  rest_api_id   = aws_api_gateway_rest_api.main.id
  resource_id   = aws_api_gateway_resource.admin_members.id
  http_method   = "OPTIONS"
  authorization = "NONE"
}

resource "aws_api_gateway_integration" "admin_members_options" {
  rest_api_id       = aws_api_gateway_rest_api.main.id
  resource_id       = aws_api_gateway_resource.admin_members.id
  http_method       = aws_api_gateway_method.admin_members_options.http_method
  type              = "MOCK"
  request_templates = { "application/json" = "{\"statusCode\": 200}" }
}

resource "aws_api_gateway_method_response" "admin_members_options_200" {
  rest_api_id = aws_api_gateway_rest_api.main.id
  resource_id = aws_api_gateway_resource.admin_members.id
  http_method = aws_api_gateway_method.admin_members_options.http_method
  status_code = "200"
  response_parameters = {
    "method.response.header.Access-Control-Allow-Headers" = true
    "method.response.header.Access-Control-Allow-Methods" = true
    "method.response.header.Access-Control-Allow-Origin"  = true
  }
}

resource "aws_api_gateway_integration_response" "admin_members_options" {
  rest_api_id         = aws_api_gateway_rest_api.main.id
  resource_id         = aws_api_gateway_resource.admin_members.id
  http_method         = aws_api_gateway_method.admin_members_options.http_method
  status_code         = aws_api_gateway_method_response.admin_members_options_200.status_code
  response_parameters = local.cors_headers
}

# ─────────────────────────────────────────────────────────────────────────────
# /admin/members/{email}  (item — get, update, delete)
# ─────────────────────────────────────────────────────────────────────────────

resource "aws_api_gateway_resource" "admin_member_item" {
  rest_api_id = aws_api_gateway_rest_api.main.id
  parent_id   = aws_api_gateway_resource.admin_members.id
  path_part   = "{email}"
}

resource "aws_api_gateway_method" "admin_member_get" {
  rest_api_id        = aws_api_gateway_rest_api.main.id
  resource_id        = aws_api_gateway_resource.admin_member_item.id
  http_method        = "GET"
  authorization      = "COGNITO_USER_POOLS"
  authorizer_id      = aws_api_gateway_authorizer.cognito.id
  request_parameters = { "method.request.path.email" = true }
}

resource "aws_api_gateway_method" "admin_member_put" {
  rest_api_id        = aws_api_gateway_rest_api.main.id
  resource_id        = aws_api_gateway_resource.admin_member_item.id
  http_method        = "PUT"
  authorization      = "COGNITO_USER_POOLS"
  authorizer_id      = aws_api_gateway_authorizer.cognito.id
  request_parameters = { "method.request.path.email" = true }
}

resource "aws_api_gateway_method" "admin_member_delete" {
  rest_api_id        = aws_api_gateway_rest_api.main.id
  resource_id        = aws_api_gateway_resource.admin_member_item.id
  http_method        = "DELETE"
  authorization      = "COGNITO_USER_POOLS"
  authorizer_id      = aws_api_gateway_authorizer.cognito.id
  request_parameters = { "method.request.path.email" = true }
}

resource "aws_api_gateway_integration" "admin_member_get" {
  rest_api_id             = aws_api_gateway_rest_api.main.id
  resource_id             = aws_api_gateway_resource.admin_member_item.id
  http_method             = aws_api_gateway_method.admin_member_get.http_method
  integration_http_method = "POST"
  type                    = "AWS_PROXY"
  uri                     = aws_lambda_function.manage_members.invoke_arn
}

resource "aws_api_gateway_integration" "admin_member_put" {
  rest_api_id             = aws_api_gateway_rest_api.main.id
  resource_id             = aws_api_gateway_resource.admin_member_item.id
  http_method             = aws_api_gateway_method.admin_member_put.http_method
  integration_http_method = "POST"
  type                    = "AWS_PROXY"
  uri                     = aws_lambda_function.manage_members.invoke_arn
}

resource "aws_api_gateway_integration" "admin_member_delete" {
  rest_api_id             = aws_api_gateway_rest_api.main.id
  resource_id             = aws_api_gateway_resource.admin_member_item.id
  http_method             = aws_api_gateway_method.admin_member_delete.http_method
  integration_http_method = "POST"
  type                    = "AWS_PROXY"
  uri                     = aws_lambda_function.manage_members.invoke_arn
}

resource "aws_api_gateway_method" "admin_member_item_options" {
  rest_api_id   = aws_api_gateway_rest_api.main.id
  resource_id   = aws_api_gateway_resource.admin_member_item.id
  http_method   = "OPTIONS"
  authorization = "NONE"
}

resource "aws_api_gateway_integration" "admin_member_item_options" {
  rest_api_id       = aws_api_gateway_rest_api.main.id
  resource_id       = aws_api_gateway_resource.admin_member_item.id
  http_method       = aws_api_gateway_method.admin_member_item_options.http_method
  type              = "MOCK"
  request_templates = { "application/json" = "{\"statusCode\": 200}" }
}

resource "aws_api_gateway_method_response" "admin_member_item_options_200" {
  rest_api_id = aws_api_gateway_rest_api.main.id
  resource_id = aws_api_gateway_resource.admin_member_item.id
  http_method = aws_api_gateway_method.admin_member_item_options.http_method
  status_code = "200"
  response_parameters = {
    "method.response.header.Access-Control-Allow-Headers" = true
    "method.response.header.Access-Control-Allow-Methods" = true
    "method.response.header.Access-Control-Allow-Origin"  = true
  }
}

resource "aws_api_gateway_integration_response" "admin_member_item_options" {
  rest_api_id         = aws_api_gateway_rest_api.main.id
  resource_id         = aws_api_gateway_resource.admin_member_item.id
  http_method         = aws_api_gateway_method.admin_member_item_options.http_method
  status_code         = aws_api_gateway_method_response.admin_member_item_options_200.status_code
  response_parameters = local.cors_headers
}

# ─────────────────────────────────────────────────────────────────────────────
# /admin/npo-membership  (root — no direct methods, just a path segment)
# ─────────────────────────────────────────────────────────────────────────────

resource "aws_api_gateway_resource" "admin_npo_membership" {
  rest_api_id = aws_api_gateway_rest_api.main.id
  parent_id   = aws_api_gateway_resource.admin.id
  path_part   = "npo-membership"
}

# ─────────────────────────────────────────────────────────────────────────────
# /admin/npo-membership/{memberid}  (list + create transactions for a member)
# ─────────────────────────────────────────────────────────────────────────────

resource "aws_api_gateway_resource" "admin_npo_membership_member" {
  rest_api_id = aws_api_gateway_rest_api.main.id
  parent_id   = aws_api_gateway_resource.admin_npo_membership.id
  path_part   = "{memberid}"
}

resource "aws_api_gateway_method" "admin_npo_membership_get" {
  rest_api_id        = aws_api_gateway_rest_api.main.id
  resource_id        = aws_api_gateway_resource.admin_npo_membership_member.id
  http_method        = "GET"
  authorization      = "COGNITO_USER_POOLS"
  authorizer_id      = aws_api_gateway_authorizer.cognito.id
  request_parameters = { "method.request.path.memberid" = true }
}

resource "aws_api_gateway_method" "admin_npo_membership_post" {
  rest_api_id        = aws_api_gateway_rest_api.main.id
  resource_id        = aws_api_gateway_resource.admin_npo_membership_member.id
  http_method        = "POST"
  authorization      = "COGNITO_USER_POOLS"
  authorizer_id      = aws_api_gateway_authorizer.cognito.id
  request_parameters = { "method.request.path.memberid" = true }
}

resource "aws_api_gateway_integration" "admin_npo_membership_get" {
  rest_api_id             = aws_api_gateway_rest_api.main.id
  resource_id             = aws_api_gateway_resource.admin_npo_membership_member.id
  http_method             = aws_api_gateway_method.admin_npo_membership_get.http_method
  integration_http_method = "POST"
  type                    = "AWS_PROXY"
  uri                     = aws_lambda_function.manage_members.invoke_arn
}

resource "aws_api_gateway_integration" "admin_npo_membership_post" {
  rest_api_id             = aws_api_gateway_rest_api.main.id
  resource_id             = aws_api_gateway_resource.admin_npo_membership_member.id
  http_method             = aws_api_gateway_method.admin_npo_membership_post.http_method
  integration_http_method = "POST"
  type                    = "AWS_PROXY"
  uri                     = aws_lambda_function.manage_members.invoke_arn
}

resource "aws_api_gateway_method" "admin_npo_membership_member_options" {
  rest_api_id   = aws_api_gateway_rest_api.main.id
  resource_id   = aws_api_gateway_resource.admin_npo_membership_member.id
  http_method   = "OPTIONS"
  authorization = "NONE"
}

resource "aws_api_gateway_integration" "admin_npo_membership_member_options" {
  rest_api_id       = aws_api_gateway_rest_api.main.id
  resource_id       = aws_api_gateway_resource.admin_npo_membership_member.id
  http_method       = aws_api_gateway_method.admin_npo_membership_member_options.http_method
  type              = "MOCK"
  request_templates = { "application/json" = "{\"statusCode\": 200}" }
}

resource "aws_api_gateway_method_response" "admin_npo_membership_member_options_200" {
  rest_api_id = aws_api_gateway_rest_api.main.id
  resource_id = aws_api_gateway_resource.admin_npo_membership_member.id
  http_method = aws_api_gateway_method.admin_npo_membership_member_options.http_method
  status_code = "200"
  response_parameters = {
    "method.response.header.Access-Control-Allow-Headers" = true
    "method.response.header.Access-Control-Allow-Methods" = true
    "method.response.header.Access-Control-Allow-Origin"  = true
  }
}

resource "aws_api_gateway_integration_response" "admin_npo_membership_member_options" {
  rest_api_id         = aws_api_gateway_rest_api.main.id
  resource_id         = aws_api_gateway_resource.admin_npo_membership_member.id
  http_method         = aws_api_gateway_method.admin_npo_membership_member_options.http_method
  status_code         = aws_api_gateway_method_response.admin_npo_membership_member_options_200.status_code
  response_parameters = local.cors_headers
}

# ─────────────────────────────────────────────────────────────────────────────
# /admin/npo-membership/{memberid}/{transactionNo}  (update + delete)
# ─────────────────────────────────────────────────────────────────────────────

resource "aws_api_gateway_resource" "admin_npo_membership_item" {
  rest_api_id = aws_api_gateway_rest_api.main.id
  parent_id   = aws_api_gateway_resource.admin_npo_membership_member.id
  path_part   = "{transactionNo}"
}

resource "aws_api_gateway_method" "admin_npo_membership_put" {
  rest_api_id   = aws_api_gateway_rest_api.main.id
  resource_id   = aws_api_gateway_resource.admin_npo_membership_item.id
  http_method   = "PUT"
  authorization = "COGNITO_USER_POOLS"
  authorizer_id = aws_api_gateway_authorizer.cognito.id
  request_parameters = {
    "method.request.path.memberid"      = true
    "method.request.path.transactionNo" = true
  }
}

resource "aws_api_gateway_method" "admin_npo_membership_delete" {
  rest_api_id   = aws_api_gateway_rest_api.main.id
  resource_id   = aws_api_gateway_resource.admin_npo_membership_item.id
  http_method   = "DELETE"
  authorization = "COGNITO_USER_POOLS"
  authorizer_id = aws_api_gateway_authorizer.cognito.id
  request_parameters = {
    "method.request.path.memberid"      = true
    "method.request.path.transactionNo" = true
  }
}

resource "aws_api_gateway_integration" "admin_npo_membership_put" {
  rest_api_id             = aws_api_gateway_rest_api.main.id
  resource_id             = aws_api_gateway_resource.admin_npo_membership_item.id
  http_method             = aws_api_gateway_method.admin_npo_membership_put.http_method
  integration_http_method = "POST"
  type                    = "AWS_PROXY"
  uri                     = aws_lambda_function.manage_members.invoke_arn
}

resource "aws_api_gateway_integration" "admin_npo_membership_delete" {
  rest_api_id             = aws_api_gateway_rest_api.main.id
  resource_id             = aws_api_gateway_resource.admin_npo_membership_item.id
  http_method             = aws_api_gateway_method.admin_npo_membership_delete.http_method
  integration_http_method = "POST"
  type                    = "AWS_PROXY"
  uri                     = aws_lambda_function.manage_members.invoke_arn
}

resource "aws_api_gateway_method" "admin_npo_membership_item_options" {
  rest_api_id   = aws_api_gateway_rest_api.main.id
  resource_id   = aws_api_gateway_resource.admin_npo_membership_item.id
  http_method   = "OPTIONS"
  authorization = "NONE"
}

resource "aws_api_gateway_integration" "admin_npo_membership_item_options" {
  rest_api_id       = aws_api_gateway_rest_api.main.id
  resource_id       = aws_api_gateway_resource.admin_npo_membership_item.id
  http_method       = aws_api_gateway_method.admin_npo_membership_item_options.http_method
  type              = "MOCK"
  request_templates = { "application/json" = "{\"statusCode\": 200}" }
}

resource "aws_api_gateway_method_response" "admin_npo_membership_item_options_200" {
  rest_api_id = aws_api_gateway_rest_api.main.id
  resource_id = aws_api_gateway_resource.admin_npo_membership_item.id
  http_method = aws_api_gateway_method.admin_npo_membership_item_options.http_method
  status_code = "200"
  response_parameters = {
    "method.response.header.Access-Control-Allow-Headers" = true
    "method.response.header.Access-Control-Allow-Methods" = true
    "method.response.header.Access-Control-Allow-Origin"  = true
  }
}

resource "aws_api_gateway_integration_response" "admin_npo_membership_item_options" {
  rest_api_id         = aws_api_gateway_rest_api.main.id
  resource_id         = aws_api_gateway_resource.admin_npo_membership_item.id
  http_method         = aws_api_gateway_method.admin_npo_membership_item_options.http_method
  status_code         = aws_api_gateway_method_response.admin_npo_membership_item_options_200.status_code
  response_parameters = local.cors_headers
}

# ─────────────────────────────────────────────────────────────────────────────
# /admin/member-announcements  (GET history, POST preview/send) → manage_members
# ─────────────────────────────────────────────────────────────────────────────

resource "aws_api_gateway_resource" "admin_member_announcements" {
  rest_api_id = aws_api_gateway_rest_api.main.id
  parent_id   = aws_api_gateway_resource.admin.id
  path_part   = "member-announcements"
}

resource "aws_api_gateway_method" "admin_member_announcements_get" {
  rest_api_id   = aws_api_gateway_rest_api.main.id
  resource_id   = aws_api_gateway_resource.admin_member_announcements.id
  http_method   = "GET"
  authorization = "COGNITO_USER_POOLS"
  authorizer_id = aws_api_gateway_authorizer.cognito.id
}

resource "aws_api_gateway_integration" "admin_member_announcements_get" {
  rest_api_id             = aws_api_gateway_rest_api.main.id
  resource_id             = aws_api_gateway_resource.admin_member_announcements.id
  http_method             = aws_api_gateway_method.admin_member_announcements_get.http_method
  integration_http_method = "POST"
  type                    = "AWS_PROXY"
  uri                     = aws_lambda_function.manage_members.invoke_arn
}

resource "aws_api_gateway_method" "admin_member_announcements_post" {
  rest_api_id   = aws_api_gateway_rest_api.main.id
  resource_id   = aws_api_gateway_resource.admin_member_announcements.id
  http_method   = "POST"
  authorization = "COGNITO_USER_POOLS"
  authorizer_id = aws_api_gateway_authorizer.cognito.id
}

resource "aws_api_gateway_integration" "admin_member_announcements_post" {
  rest_api_id             = aws_api_gateway_rest_api.main.id
  resource_id             = aws_api_gateway_resource.admin_member_announcements.id
  http_method             = aws_api_gateway_method.admin_member_announcements_post.http_method
  integration_http_method = "POST"
  type                    = "AWS_PROXY"
  uri                     = aws_lambda_function.manage_members.invoke_arn
}

resource "aws_api_gateway_method" "admin_member_announcements_options" {
  rest_api_id   = aws_api_gateway_rest_api.main.id
  resource_id   = aws_api_gateway_resource.admin_member_announcements.id
  http_method   = "OPTIONS"
  authorization = "NONE"
}

resource "aws_api_gateway_integration" "admin_member_announcements_options" {
  rest_api_id       = aws_api_gateway_rest_api.main.id
  resource_id       = aws_api_gateway_resource.admin_member_announcements.id
  http_method       = aws_api_gateway_method.admin_member_announcements_options.http_method
  type              = "MOCK"
  request_templates = { "application/json" = "{\"statusCode\": 200}" }
}

resource "aws_api_gateway_method_response" "admin_member_announcements_options_200" {
  rest_api_id = aws_api_gateway_rest_api.main.id
  resource_id = aws_api_gateway_resource.admin_member_announcements.id
  http_method = aws_api_gateway_method.admin_member_announcements_options.http_method
  status_code = "200"
  response_parameters = {
    "method.response.header.Access-Control-Allow-Headers" = true
    "method.response.header.Access-Control-Allow-Methods" = true
    "method.response.header.Access-Control-Allow-Origin"  = true
  }
}

resource "aws_api_gateway_integration_response" "admin_member_announcements_options" {
  rest_api_id         = aws_api_gateway_rest_api.main.id
  resource_id         = aws_api_gateway_resource.admin_member_announcements.id
  http_method         = aws_api_gateway_method.admin_member_announcements_options.http_method
  status_code         = aws_api_gateway_method_response.admin_member_announcements_options_200.status_code
  response_parameters = local.cors_headers
}

# ─────────────────────────────────────────────────────────────────────────────
# /admin/agms (list + create) and /admin/agms/{agmId} (get with registrations + update)
# ─────────────────────────────────────────────────────────────────────────────

resource "aws_api_gateway_resource" "admin_agms" {
  rest_api_id = aws_api_gateway_rest_api.main.id
  parent_id   = aws_api_gateway_resource.admin.id
  path_part   = "agms"
}

resource "aws_api_gateway_resource" "admin_agm_item" {
  rest_api_id = aws_api_gateway_rest_api.main.id
  parent_id   = aws_api_gateway_resource.admin_agms.id
  path_part   = "{agmId}"
}

resource "aws_api_gateway_method" "admin_agms_get" {
  rest_api_id   = aws_api_gateway_rest_api.main.id
  resource_id   = aws_api_gateway_resource.admin_agms.id
  http_method   = "GET"
  authorization = "COGNITO_USER_POOLS"
  authorizer_id = aws_api_gateway_authorizer.cognito.id
}

resource "aws_api_gateway_integration" "admin_agms_get" {
  rest_api_id             = aws_api_gateway_rest_api.main.id
  resource_id             = aws_api_gateway_resource.admin_agms.id
  http_method             = aws_api_gateway_method.admin_agms_get.http_method
  integration_http_method = "POST"
  type                    = "AWS_PROXY"
  uri                     = aws_lambda_function.manage_agms.invoke_arn
}

resource "aws_api_gateway_method" "admin_agms_post" {
  rest_api_id   = aws_api_gateway_rest_api.main.id
  resource_id   = aws_api_gateway_resource.admin_agms.id
  http_method   = "POST"
  authorization = "COGNITO_USER_POOLS"
  authorizer_id = aws_api_gateway_authorizer.cognito.id
}

resource "aws_api_gateway_integration" "admin_agms_post" {
  rest_api_id             = aws_api_gateway_rest_api.main.id
  resource_id             = aws_api_gateway_resource.admin_agms.id
  http_method             = aws_api_gateway_method.admin_agms_post.http_method
  integration_http_method = "POST"
  type                    = "AWS_PROXY"
  uri                     = aws_lambda_function.manage_agms.invoke_arn
}

resource "aws_api_gateway_method" "admin_agms_options" {
  rest_api_id   = aws_api_gateway_rest_api.main.id
  resource_id   = aws_api_gateway_resource.admin_agms.id
  http_method   = "OPTIONS"
  authorization = "NONE"
}

resource "aws_api_gateway_integration" "admin_agms_options" {
  rest_api_id       = aws_api_gateway_rest_api.main.id
  resource_id       = aws_api_gateway_resource.admin_agms.id
  http_method       = aws_api_gateway_method.admin_agms_options.http_method
  type              = "MOCK"
  request_templates = { "application/json" = "{\"statusCode\": 200}" }
}

resource "aws_api_gateway_method_response" "admin_agms_options_200" {
  rest_api_id = aws_api_gateway_rest_api.main.id
  resource_id = aws_api_gateway_resource.admin_agms.id
  http_method = aws_api_gateway_method.admin_agms_options.http_method
  status_code = "200"
  response_parameters = {
    "method.response.header.Access-Control-Allow-Headers" = true
    "method.response.header.Access-Control-Allow-Methods" = true
    "method.response.header.Access-Control-Allow-Origin"  = true
  }
}

resource "aws_api_gateway_integration_response" "admin_agms_options" {
  rest_api_id         = aws_api_gateway_rest_api.main.id
  resource_id         = aws_api_gateway_resource.admin_agms.id
  http_method         = aws_api_gateway_method.admin_agms_options.http_method
  status_code         = aws_api_gateway_method_response.admin_agms_options_200.status_code
  response_parameters = local.cors_headers
}

resource "aws_api_gateway_method" "admin_agm_item_get" {
  rest_api_id   = aws_api_gateway_rest_api.main.id
  resource_id   = aws_api_gateway_resource.admin_agm_item.id
  http_method   = "GET"
  authorization = "COGNITO_USER_POOLS"
  authorizer_id = aws_api_gateway_authorizer.cognito.id
  request_parameters = { "method.request.path.agmId" = true }
}

resource "aws_api_gateway_integration" "admin_agm_item_get" {
  rest_api_id             = aws_api_gateway_rest_api.main.id
  resource_id             = aws_api_gateway_resource.admin_agm_item.id
  http_method             = aws_api_gateway_method.admin_agm_item_get.http_method
  integration_http_method = "POST"
  type                    = "AWS_PROXY"
  uri                     = aws_lambda_function.manage_agms.invoke_arn
}

resource "aws_api_gateway_method" "admin_agm_item_put" {
  rest_api_id   = aws_api_gateway_rest_api.main.id
  resource_id   = aws_api_gateway_resource.admin_agm_item.id
  http_method   = "PUT"
  authorization = "COGNITO_USER_POOLS"
  authorizer_id = aws_api_gateway_authorizer.cognito.id
  request_parameters = { "method.request.path.agmId" = true }
}

resource "aws_api_gateway_integration" "admin_agm_item_put" {
  rest_api_id             = aws_api_gateway_rest_api.main.id
  resource_id             = aws_api_gateway_resource.admin_agm_item.id
  http_method             = aws_api_gateway_method.admin_agm_item_put.http_method
  integration_http_method = "POST"
  type                    = "AWS_PROXY"
  uri                     = aws_lambda_function.manage_agms.invoke_arn
}

resource "aws_api_gateway_method" "admin_agm_item_options" {
  rest_api_id   = aws_api_gateway_rest_api.main.id
  resource_id   = aws_api_gateway_resource.admin_agm_item.id
  http_method   = "OPTIONS"
  authorization = "NONE"
}

resource "aws_api_gateway_integration" "admin_agm_item_options" {
  rest_api_id       = aws_api_gateway_rest_api.main.id
  resource_id       = aws_api_gateway_resource.admin_agm_item.id
  http_method       = aws_api_gateway_method.admin_agm_item_options.http_method
  type              = "MOCK"
  request_templates = { "application/json" = "{\"statusCode\": 200}" }
}

resource "aws_api_gateway_method_response" "admin_agm_item_options_200" {
  rest_api_id = aws_api_gateway_rest_api.main.id
  resource_id = aws_api_gateway_resource.admin_agm_item.id
  http_method = aws_api_gateway_method.admin_agm_item_options.http_method
  status_code = "200"
  response_parameters = {
    "method.response.header.Access-Control-Allow-Headers" = true
    "method.response.header.Access-Control-Allow-Methods" = true
    "method.response.header.Access-Control-Allow-Origin"  = true
  }
}

resource "aws_api_gateway_integration_response" "admin_agm_item_options" {
  rest_api_id         = aws_api_gateway_rest_api.main.id
  resource_id         = aws_api_gateway_resource.admin_agm_item.id
  http_method         = aws_api_gateway_method.admin_agm_item_options.http_method
  status_code         = aws_api_gateway_method_response.admin_agm_item_options_200.status_code
  response_parameters = local.cors_headers
}

resource "aws_lambda_permission" "manage_agms" {
  statement_id  = "AllowAPIGatewayInvokeManageAgms"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.manage_agms.function_name
  principal     = "apigateway.amazonaws.com"
  source_arn    = "${aws_api_gateway_rest_api.main.execution_arn}/*/*"
}

# ─────────────────────────────────────────────────────────────────────────────
# /admin/events/{eventType}/celebrity-dinner (config) + /registrations (list)
# ─────────────────────────────────────────────────────────────────────────────

resource "aws_api_gateway_resource" "admin_celebrity_dinner" {
  rest_api_id = aws_api_gateway_rest_api.main.id
  parent_id   = aws_api_gateway_resource.admin_event_item.id
  path_part   = "celebrity-dinner"
}

resource "aws_api_gateway_method" "admin_celebrity_dinner_get" {
  rest_api_id   = aws_api_gateway_rest_api.main.id
  resource_id   = aws_api_gateway_resource.admin_celebrity_dinner.id
  http_method   = "GET"
  authorization = "COGNITO_USER_POOLS"
  authorizer_id = aws_api_gateway_authorizer.cognito.id
}

resource "aws_api_gateway_method" "admin_celebrity_dinner_put" {
  rest_api_id   = aws_api_gateway_rest_api.main.id
  resource_id   = aws_api_gateway_resource.admin_celebrity_dinner.id
  http_method   = "PUT"
  authorization = "COGNITO_USER_POOLS"
  authorizer_id = aws_api_gateway_authorizer.cognito.id
}

resource "aws_api_gateway_method" "admin_celebrity_dinner_options" {
  rest_api_id   = aws_api_gateway_rest_api.main.id
  resource_id   = aws_api_gateway_resource.admin_celebrity_dinner.id
  http_method   = "OPTIONS"
  authorization = "NONE"
}

resource "aws_api_gateway_integration" "admin_celebrity_dinner_get" {
  rest_api_id             = aws_api_gateway_rest_api.main.id
  resource_id             = aws_api_gateway_resource.admin_celebrity_dinner.id
  http_method             = aws_api_gateway_method.admin_celebrity_dinner_get.http_method
  integration_http_method = "POST"
  type                    = "AWS_PROXY"
  uri                     = aws_lambda_function.manage_celebrity_dinner.invoke_arn
}

resource "aws_api_gateway_integration" "admin_celebrity_dinner_put" {
  rest_api_id             = aws_api_gateway_rest_api.main.id
  resource_id             = aws_api_gateway_resource.admin_celebrity_dinner.id
  http_method             = aws_api_gateway_method.admin_celebrity_dinner_put.http_method
  integration_http_method = "POST"
  type                    = "AWS_PROXY"
  uri                     = aws_lambda_function.manage_celebrity_dinner.invoke_arn
}

resource "aws_api_gateway_integration" "admin_celebrity_dinner_options" {
  rest_api_id       = aws_api_gateway_rest_api.main.id
  resource_id       = aws_api_gateway_resource.admin_celebrity_dinner.id
  http_method       = aws_api_gateway_method.admin_celebrity_dinner_options.http_method
  type              = "MOCK"
  request_templates = { "application/json" = "{\"statusCode\": 200}" }
}

resource "aws_api_gateway_method_response" "admin_celebrity_dinner_options_200" {
  rest_api_id = aws_api_gateway_rest_api.main.id
  resource_id = aws_api_gateway_resource.admin_celebrity_dinner.id
  http_method = aws_api_gateway_method.admin_celebrity_dinner_options.http_method
  status_code = "200"
  response_parameters = {
    "method.response.header.Access-Control-Allow-Headers" = true
    "method.response.header.Access-Control-Allow-Methods" = true
    "method.response.header.Access-Control-Allow-Origin"  = true
  }
}

resource "aws_api_gateway_integration_response" "admin_celebrity_dinner_options" {
  rest_api_id         = aws_api_gateway_rest_api.main.id
  resource_id         = aws_api_gateway_resource.admin_celebrity_dinner.id
  http_method         = aws_api_gateway_method.admin_celebrity_dinner_options.http_method
  status_code         = aws_api_gateway_method_response.admin_celebrity_dinner_options_200.status_code
  response_parameters = local.cors_headers
}

# ── /admin/events/{eventType}/celebrity-dinner/registrations (list, read-only) ──

resource "aws_api_gateway_resource" "admin_celebrity_dinner_registrations" {
  rest_api_id = aws_api_gateway_rest_api.main.id
  parent_id   = aws_api_gateway_resource.admin_celebrity_dinner.id
  path_part   = "registrations"
}

resource "aws_api_gateway_method" "admin_celebrity_dinner_registrations_get" {
  rest_api_id   = aws_api_gateway_rest_api.main.id
  resource_id   = aws_api_gateway_resource.admin_celebrity_dinner_registrations.id
  http_method   = "GET"
  authorization = "COGNITO_USER_POOLS"
  authorizer_id = aws_api_gateway_authorizer.cognito.id
}

resource "aws_api_gateway_method" "admin_celebrity_dinner_registrations_options" {
  rest_api_id   = aws_api_gateway_rest_api.main.id
  resource_id   = aws_api_gateway_resource.admin_celebrity_dinner_registrations.id
  http_method   = "OPTIONS"
  authorization = "NONE"
}

resource "aws_api_gateway_integration" "admin_celebrity_dinner_registrations_get" {
  rest_api_id             = aws_api_gateway_rest_api.main.id
  resource_id             = aws_api_gateway_resource.admin_celebrity_dinner_registrations.id
  http_method             = aws_api_gateway_method.admin_celebrity_dinner_registrations_get.http_method
  integration_http_method = "POST"
  type                    = "AWS_PROXY"
  uri                     = aws_lambda_function.manage_celebrity_dinner.invoke_arn
}

resource "aws_api_gateway_integration" "admin_celebrity_dinner_registrations_options" {
  rest_api_id       = aws_api_gateway_rest_api.main.id
  resource_id       = aws_api_gateway_resource.admin_celebrity_dinner_registrations.id
  http_method       = aws_api_gateway_method.admin_celebrity_dinner_registrations_options.http_method
  type              = "MOCK"
  request_templates = { "application/json" = "{\"statusCode\": 200}" }
}

resource "aws_api_gateway_method_response" "admin_celebrity_dinner_registrations_options_200" {
  rest_api_id = aws_api_gateway_rest_api.main.id
  resource_id = aws_api_gateway_resource.admin_celebrity_dinner_registrations.id
  http_method = aws_api_gateway_method.admin_celebrity_dinner_registrations_options.http_method
  status_code = "200"
  response_parameters = {
    "method.response.header.Access-Control-Allow-Headers" = true
    "method.response.header.Access-Control-Allow-Methods" = true
    "method.response.header.Access-Control-Allow-Origin"  = true
  }
}

resource "aws_api_gateway_integration_response" "admin_celebrity_dinner_registrations_options" {
  rest_api_id         = aws_api_gateway_rest_api.main.id
  resource_id         = aws_api_gateway_resource.admin_celebrity_dinner_registrations.id
  http_method         = aws_api_gateway_method.admin_celebrity_dinner_registrations_options.http_method
  status_code         = aws_api_gateway_method_response.admin_celebrity_dinner_registrations_options_200.status_code
  response_parameters = local.cors_headers
}

# ── /admin/events/{eventType}/celebrity-dinner/registrations/{registrationId}
# — PATCH to mark paid / cancel / finalize a refund on a standalone booking,
# mirroring the seat-upgrade plugin's own PATCH .../seat-upgrades/{upgradeId} ──

resource "aws_api_gateway_resource" "admin_celebrity_dinner_registration_item" {
  rest_api_id = aws_api_gateway_rest_api.main.id
  parent_id   = aws_api_gateway_resource.admin_celebrity_dinner_registrations.id
  path_part   = "{registrationId}"
}

resource "aws_api_gateway_method" "admin_celebrity_dinner_registration_patch" {
  rest_api_id   = aws_api_gateway_rest_api.main.id
  resource_id   = aws_api_gateway_resource.admin_celebrity_dinner_registration_item.id
  http_method   = "PATCH"
  authorization = "COGNITO_USER_POOLS"
  authorizer_id = aws_api_gateway_authorizer.cognito.id
}

resource "aws_api_gateway_method" "admin_celebrity_dinner_registration_options" {
  rest_api_id   = aws_api_gateway_rest_api.main.id
  resource_id   = aws_api_gateway_resource.admin_celebrity_dinner_registration_item.id
  http_method   = "OPTIONS"
  authorization = "NONE"
}

resource "aws_api_gateway_integration" "admin_celebrity_dinner_registration_patch" {
  rest_api_id             = aws_api_gateway_rest_api.main.id
  resource_id             = aws_api_gateway_resource.admin_celebrity_dinner_registration_item.id
  http_method             = aws_api_gateway_method.admin_celebrity_dinner_registration_patch.http_method
  integration_http_method = "POST"
  type                    = "AWS_PROXY"
  uri                     = aws_lambda_function.manage_celebrity_dinner.invoke_arn
}

resource "aws_api_gateway_integration" "admin_celebrity_dinner_registration_options" {
  rest_api_id       = aws_api_gateway_rest_api.main.id
  resource_id       = aws_api_gateway_resource.admin_celebrity_dinner_registration_item.id
  http_method       = aws_api_gateway_method.admin_celebrity_dinner_registration_options.http_method
  type              = "MOCK"
  request_templates = { "application/json" = "{\"statusCode\": 200}" }
}

resource "aws_api_gateway_method_response" "admin_celebrity_dinner_registration_options_200" {
  rest_api_id = aws_api_gateway_rest_api.main.id
  resource_id = aws_api_gateway_resource.admin_celebrity_dinner_registration_item.id
  http_method = aws_api_gateway_method.admin_celebrity_dinner_registration_options.http_method
  status_code = "200"
  response_parameters = {
    "method.response.header.Access-Control-Allow-Headers" = true
    "method.response.header.Access-Control-Allow-Methods" = true
    "method.response.header.Access-Control-Allow-Origin"  = true
  }
}

resource "aws_api_gateway_integration_response" "admin_celebrity_dinner_registration_options" {
  rest_api_id         = aws_api_gateway_rest_api.main.id
  resource_id         = aws_api_gateway_resource.admin_celebrity_dinner_registration_item.id
  http_method         = aws_api_gateway_method.admin_celebrity_dinner_registration_options.http_method
  status_code         = aws_api_gateway_method_response.admin_celebrity_dinner_registration_options_200.status_code
  response_parameters = local.cors_headers
}

# ─────────────────────────────────────────────────────────────────────────────
# /admin/events/{eventType}/entry-slots (collection + item)
# ─────────────────────────────────────────────────────────────────────────────

resource "aws_api_gateway_resource" "admin_entry_slots" {
  rest_api_id = aws_api_gateway_rest_api.main.id
  parent_id   = aws_api_gateway_resource.admin_event_item.id
  path_part   = "entry-slots"
}

resource "aws_api_gateway_method" "admin_entry_slots_get" {
  rest_api_id   = aws_api_gateway_rest_api.main.id
  resource_id   = aws_api_gateway_resource.admin_entry_slots.id
  http_method   = "GET"
  authorization = "COGNITO_USER_POOLS"
  authorizer_id = aws_api_gateway_authorizer.cognito.id
}

resource "aws_api_gateway_method" "admin_entry_slots_post" {
  rest_api_id   = aws_api_gateway_rest_api.main.id
  resource_id   = aws_api_gateway_resource.admin_entry_slots.id
  http_method   = "POST"
  authorization = "COGNITO_USER_POOLS"
  authorizer_id = aws_api_gateway_authorizer.cognito.id
}

resource "aws_api_gateway_method" "admin_entry_slots_options" {
  rest_api_id   = aws_api_gateway_rest_api.main.id
  resource_id   = aws_api_gateway_resource.admin_entry_slots.id
  http_method   = "OPTIONS"
  authorization = "NONE"
}

resource "aws_api_gateway_integration" "admin_entry_slots_get" {
  rest_api_id             = aws_api_gateway_rest_api.main.id
  resource_id             = aws_api_gateway_resource.admin_entry_slots.id
  http_method             = aws_api_gateway_method.admin_entry_slots_get.http_method
  integration_http_method = "POST"
  type                    = "AWS_PROXY"
  uri                     = aws_lambda_function.manage_entry_slots.invoke_arn
}

resource "aws_api_gateway_integration" "admin_entry_slots_post" {
  rest_api_id             = aws_api_gateway_rest_api.main.id
  resource_id             = aws_api_gateway_resource.admin_entry_slots.id
  http_method             = aws_api_gateway_method.admin_entry_slots_post.http_method
  integration_http_method = "POST"
  type                    = "AWS_PROXY"
  uri                     = aws_lambda_function.manage_entry_slots.invoke_arn
}

resource "aws_api_gateway_integration" "admin_entry_slots_options" {
  rest_api_id       = aws_api_gateway_rest_api.main.id
  resource_id       = aws_api_gateway_resource.admin_entry_slots.id
  http_method       = aws_api_gateway_method.admin_entry_slots_options.http_method
  type              = "MOCK"
  request_templates = { "application/json" = "{\"statusCode\": 200}" }
}

resource "aws_api_gateway_method_response" "admin_entry_slots_options_200" {
  rest_api_id = aws_api_gateway_rest_api.main.id
  resource_id = aws_api_gateway_resource.admin_entry_slots.id
  http_method = aws_api_gateway_method.admin_entry_slots_options.http_method
  status_code = "200"
  response_parameters = {
    "method.response.header.Access-Control-Allow-Headers" = true
    "method.response.header.Access-Control-Allow-Methods" = true
    "method.response.header.Access-Control-Allow-Origin"  = true
  }
}

resource "aws_api_gateway_integration_response" "admin_entry_slots_options" {
  rest_api_id         = aws_api_gateway_rest_api.main.id
  resource_id         = aws_api_gateway_resource.admin_entry_slots.id
  http_method         = aws_api_gateway_method.admin_entry_slots_options.http_method
  status_code         = aws_api_gateway_method_response.admin_entry_slots_options_200.status_code
  response_parameters = local.cors_headers
}

# ── /admin/events/{eventType}/entry-slots/{slotId} (item) ───────────────────

resource "aws_api_gateway_resource" "admin_entry_slot_item" {
  rest_api_id = aws_api_gateway_rest_api.main.id
  parent_id   = aws_api_gateway_resource.admin_entry_slots.id
  path_part   = "{slotId}"
}

resource "aws_api_gateway_method" "admin_entry_slot_item_get" {
  rest_api_id   = aws_api_gateway_rest_api.main.id
  resource_id   = aws_api_gateway_resource.admin_entry_slot_item.id
  http_method   = "GET"
  authorization = "COGNITO_USER_POOLS"
  authorizer_id = aws_api_gateway_authorizer.cognito.id
  request_parameters = {
    "method.request.path.slotId" = true
  }
}

resource "aws_api_gateway_method" "admin_entry_slot_item_patch" {
  rest_api_id   = aws_api_gateway_rest_api.main.id
  resource_id   = aws_api_gateway_resource.admin_entry_slot_item.id
  http_method   = "PATCH"
  authorization = "COGNITO_USER_POOLS"
  authorizer_id = aws_api_gateway_authorizer.cognito.id
  request_parameters = {
    "method.request.path.slotId" = true
  }
}

resource "aws_api_gateway_method" "admin_entry_slot_item_delete" {
  rest_api_id   = aws_api_gateway_rest_api.main.id
  resource_id   = aws_api_gateway_resource.admin_entry_slot_item.id
  http_method   = "DELETE"
  authorization = "COGNITO_USER_POOLS"
  authorizer_id = aws_api_gateway_authorizer.cognito.id
  request_parameters = {
    "method.request.path.slotId" = true
  }
}

resource "aws_api_gateway_method" "admin_entry_slot_item_options" {
  rest_api_id   = aws_api_gateway_rest_api.main.id
  resource_id   = aws_api_gateway_resource.admin_entry_slot_item.id
  http_method   = "OPTIONS"
  authorization = "NONE"
}

resource "aws_api_gateway_integration" "admin_entry_slot_item_get" {
  rest_api_id             = aws_api_gateway_rest_api.main.id
  resource_id             = aws_api_gateway_resource.admin_entry_slot_item.id
  http_method             = aws_api_gateway_method.admin_entry_slot_item_get.http_method
  integration_http_method = "POST"
  type                    = "AWS_PROXY"
  uri                     = aws_lambda_function.manage_entry_slots.invoke_arn
}

resource "aws_api_gateway_integration" "admin_entry_slot_item_patch" {
  rest_api_id             = aws_api_gateway_rest_api.main.id
  resource_id             = aws_api_gateway_resource.admin_entry_slot_item.id
  http_method             = aws_api_gateway_method.admin_entry_slot_item_patch.http_method
  integration_http_method = "POST"
  type                    = "AWS_PROXY"
  uri                     = aws_lambda_function.manage_entry_slots.invoke_arn
}

resource "aws_api_gateway_integration" "admin_entry_slot_item_delete" {
  rest_api_id             = aws_api_gateway_rest_api.main.id
  resource_id             = aws_api_gateway_resource.admin_entry_slot_item.id
  http_method             = aws_api_gateway_method.admin_entry_slot_item_delete.http_method
  integration_http_method = "POST"
  type                    = "AWS_PROXY"
  uri                     = aws_lambda_function.manage_entry_slots.invoke_arn
}

resource "aws_api_gateway_integration" "admin_entry_slot_item_options" {
  rest_api_id       = aws_api_gateway_rest_api.main.id
  resource_id       = aws_api_gateway_resource.admin_entry_slot_item.id
  http_method       = aws_api_gateway_method.admin_entry_slot_item_options.http_method
  type              = "MOCK"
  request_templates = { "application/json" = "{\"statusCode\": 200}" }
}

resource "aws_api_gateway_method_response" "admin_entry_slot_item_options_200" {
  rest_api_id = aws_api_gateway_rest_api.main.id
  resource_id = aws_api_gateway_resource.admin_entry_slot_item.id
  http_method = aws_api_gateway_method.admin_entry_slot_item_options.http_method
  status_code = "200"
  response_parameters = {
    "method.response.header.Access-Control-Allow-Headers" = true
    "method.response.header.Access-Control-Allow-Methods" = true
    "method.response.header.Access-Control-Allow-Origin"  = true
  }
}

resource "aws_api_gateway_integration_response" "admin_entry_slot_item_options" {
  rest_api_id         = aws_api_gateway_rest_api.main.id
  resource_id         = aws_api_gateway_resource.admin_entry_slot_item.id
  http_method         = aws_api_gateway_method.admin_entry_slot_item_options.http_method
  status_code         = aws_api_gateway_method_response.admin_entry_slot_item_options_200.status_code
  response_parameters = local.cors_headers
}

# ── /admin/events/{eventType}/entry-slots/{slotId}/release-buffer ───────────

resource "aws_api_gateway_resource" "admin_entry_slot_release_buffer" {
  rest_api_id = aws_api_gateway_rest_api.main.id
  parent_id   = aws_api_gateway_resource.admin_entry_slot_item.id
  path_part   = "release-buffer"
}

resource "aws_api_gateway_method" "admin_entry_slot_release_buffer_post" {
  rest_api_id   = aws_api_gateway_rest_api.main.id
  resource_id   = aws_api_gateway_resource.admin_entry_slot_release_buffer.id
  http_method   = "POST"
  authorization = "COGNITO_USER_POOLS"
  authorizer_id = aws_api_gateway_authorizer.cognito.id
  request_parameters = {
    "method.request.path.slotId" = true
  }
}

resource "aws_api_gateway_method" "admin_entry_slot_release_buffer_options" {
  rest_api_id   = aws_api_gateway_rest_api.main.id
  resource_id   = aws_api_gateway_resource.admin_entry_slot_release_buffer.id
  http_method   = "OPTIONS"
  authorization = "NONE"
}

resource "aws_api_gateway_integration" "admin_entry_slot_release_buffer_post" {
  rest_api_id             = aws_api_gateway_rest_api.main.id
  resource_id             = aws_api_gateway_resource.admin_entry_slot_release_buffer.id
  http_method             = aws_api_gateway_method.admin_entry_slot_release_buffer_post.http_method
  integration_http_method = "POST"
  type                    = "AWS_PROXY"
  uri                     = aws_lambda_function.release_buffer.invoke_arn
}

resource "aws_api_gateway_integration" "admin_entry_slot_release_buffer_options" {
  rest_api_id       = aws_api_gateway_rest_api.main.id
  resource_id       = aws_api_gateway_resource.admin_entry_slot_release_buffer.id
  http_method       = aws_api_gateway_method.admin_entry_slot_release_buffer_options.http_method
  type              = "MOCK"
  request_templates = { "application/json" = "{\"statusCode\": 200}" }
}

resource "aws_api_gateway_method_response" "admin_entry_slot_release_buffer_options_200" {
  rest_api_id = aws_api_gateway_rest_api.main.id
  resource_id = aws_api_gateway_resource.admin_entry_slot_release_buffer.id
  http_method = aws_api_gateway_method.admin_entry_slot_release_buffer_options.http_method
  status_code = "200"
  response_parameters = {
    "method.response.header.Access-Control-Allow-Headers" = true
    "method.response.header.Access-Control-Allow-Methods" = true
    "method.response.header.Access-Control-Allow-Origin"  = true
  }
}

resource "aws_api_gateway_integration_response" "admin_entry_slot_release_buffer_options" {
  rest_api_id         = aws_api_gateway_rest_api.main.id
  resource_id         = aws_api_gateway_resource.admin_entry_slot_release_buffer.id
  http_method         = aws_api_gateway_method.admin_entry_slot_release_buffer_options.http_method
  status_code         = aws_api_gateway_method_response.admin_entry_slot_release_buffer_options_200.status_code
  response_parameters = local.cors_headers
}

# ─────────────────────────────────────────────────────────────────────────────
# /admin/reconciliation/*  (payment reconciliation)
#   /admin/reconciliation/transactions            GET (list)  POST (import)
#   /admin/reconciliation/transactions/preview     POST (parse + suggest mapping, no persist)
#   /admin/reconciliation/transactions/{batchId}   DELETE (undo an upload)
#   /admin/reconciliation/runs                     GET (list)  POST (run + persist)
#   /admin/reconciliation/runs/{runId}             GET (detail; ?format=csv to export)
# All backed by the manage_reconciliation Lambda; routes are dispatched inside
# the Lambda on event["resource"], same convention as manage_registrations.
# ─────────────────────────────────────────────────────────────────────────────

resource "aws_api_gateway_resource" "admin_reconciliation" {
  rest_api_id = aws_api_gateway_rest_api.main.id
  parent_id   = aws_api_gateway_resource.admin.id
  path_part   = "reconciliation"
}

# ── /admin/reconciliation/transactions  (collection) ────────────────────────

resource "aws_api_gateway_resource" "admin_reconciliation_transactions" {
  rest_api_id = aws_api_gateway_rest_api.main.id
  parent_id   = aws_api_gateway_resource.admin_reconciliation.id
  path_part   = "transactions"
}

resource "aws_api_gateway_method" "admin_reconciliation_transactions_get" {
  rest_api_id   = aws_api_gateway_rest_api.main.id
  resource_id   = aws_api_gateway_resource.admin_reconciliation_transactions.id
  http_method   = "GET"
  authorization = "COGNITO_USER_POOLS"
  authorizer_id = aws_api_gateway_authorizer.cognito.id
  request_parameters = {
    "method.request.querystring.eventType"  = true
    "method.request.querystring.sourceType" = false
  }
}

resource "aws_api_gateway_method" "admin_reconciliation_transactions_post" {
  rest_api_id   = aws_api_gateway_rest_api.main.id
  resource_id   = aws_api_gateway_resource.admin_reconciliation_transactions.id
  http_method   = "POST"
  authorization = "COGNITO_USER_POOLS"
  authorizer_id = aws_api_gateway_authorizer.cognito.id
}

resource "aws_api_gateway_integration" "admin_reconciliation_transactions_get" {
  rest_api_id             = aws_api_gateway_rest_api.main.id
  resource_id             = aws_api_gateway_resource.admin_reconciliation_transactions.id
  http_method             = aws_api_gateway_method.admin_reconciliation_transactions_get.http_method
  integration_http_method = "POST"
  type                    = "AWS_PROXY"
  uri                     = aws_lambda_function.manage_reconciliation.invoke_arn
}

resource "aws_api_gateway_integration" "admin_reconciliation_transactions_post" {
  rest_api_id             = aws_api_gateway_rest_api.main.id
  resource_id             = aws_api_gateway_resource.admin_reconciliation_transactions.id
  http_method             = aws_api_gateway_method.admin_reconciliation_transactions_post.http_method
  integration_http_method = "POST"
  type                    = "AWS_PROXY"
  uri                     = aws_lambda_function.manage_reconciliation.invoke_arn
}

resource "aws_api_gateway_method" "admin_reconciliation_transactions_options" {
  rest_api_id   = aws_api_gateway_rest_api.main.id
  resource_id   = aws_api_gateway_resource.admin_reconciliation_transactions.id
  http_method   = "OPTIONS"
  authorization = "NONE"
}

resource "aws_api_gateway_integration" "admin_reconciliation_transactions_options" {
  rest_api_id       = aws_api_gateway_rest_api.main.id
  resource_id       = aws_api_gateway_resource.admin_reconciliation_transactions.id
  http_method       = aws_api_gateway_method.admin_reconciliation_transactions_options.http_method
  type              = "MOCK"
  request_templates = { "application/json" = "{\"statusCode\": 200}" }
}

resource "aws_api_gateway_method_response" "admin_reconciliation_transactions_options_200" {
  rest_api_id = aws_api_gateway_rest_api.main.id
  resource_id = aws_api_gateway_resource.admin_reconciliation_transactions.id
  http_method = aws_api_gateway_method.admin_reconciliation_transactions_options.http_method
  status_code = "200"
  response_parameters = {
    "method.response.header.Access-Control-Allow-Headers" = true
    "method.response.header.Access-Control-Allow-Methods" = true
    "method.response.header.Access-Control-Allow-Origin"  = true
  }
}

resource "aws_api_gateway_integration_response" "admin_reconciliation_transactions_options" {
  rest_api_id         = aws_api_gateway_rest_api.main.id
  resource_id         = aws_api_gateway_resource.admin_reconciliation_transactions.id
  http_method         = aws_api_gateway_method.admin_reconciliation_transactions_options.http_method
  status_code         = aws_api_gateway_method_response.admin_reconciliation_transactions_options_200.status_code
  response_parameters = local.cors_headers
}

# ── /admin/reconciliation/transactions/preview ───────────────────────────────

resource "aws_api_gateway_resource" "admin_reconciliation_transactions_preview" {
  rest_api_id = aws_api_gateway_rest_api.main.id
  parent_id   = aws_api_gateway_resource.admin_reconciliation_transactions.id
  path_part   = "preview"
}

resource "aws_api_gateway_method" "admin_reconciliation_transactions_preview_post" {
  rest_api_id   = aws_api_gateway_rest_api.main.id
  resource_id   = aws_api_gateway_resource.admin_reconciliation_transactions_preview.id
  http_method   = "POST"
  authorization = "COGNITO_USER_POOLS"
  authorizer_id = aws_api_gateway_authorizer.cognito.id
}

resource "aws_api_gateway_integration" "admin_reconciliation_transactions_preview_post" {
  rest_api_id             = aws_api_gateway_rest_api.main.id
  resource_id             = aws_api_gateway_resource.admin_reconciliation_transactions_preview.id
  http_method             = aws_api_gateway_method.admin_reconciliation_transactions_preview_post.http_method
  integration_http_method = "POST"
  type                    = "AWS_PROXY"
  uri                     = aws_lambda_function.manage_reconciliation.invoke_arn
}

resource "aws_api_gateway_method" "admin_reconciliation_transactions_preview_options" {
  rest_api_id   = aws_api_gateway_rest_api.main.id
  resource_id   = aws_api_gateway_resource.admin_reconciliation_transactions_preview.id
  http_method   = "OPTIONS"
  authorization = "NONE"
}

resource "aws_api_gateway_integration" "admin_reconciliation_transactions_preview_options" {
  rest_api_id       = aws_api_gateway_rest_api.main.id
  resource_id       = aws_api_gateway_resource.admin_reconciliation_transactions_preview.id
  http_method       = aws_api_gateway_method.admin_reconciliation_transactions_preview_options.http_method
  type              = "MOCK"
  request_templates = { "application/json" = "{\"statusCode\": 200}" }
}

resource "aws_api_gateway_method_response" "admin_reconciliation_transactions_preview_options_200" {
  rest_api_id = aws_api_gateway_rest_api.main.id
  resource_id = aws_api_gateway_resource.admin_reconciliation_transactions_preview.id
  http_method = aws_api_gateway_method.admin_reconciliation_transactions_preview_options.http_method
  status_code = "200"
  response_parameters = {
    "method.response.header.Access-Control-Allow-Headers" = true
    "method.response.header.Access-Control-Allow-Methods" = true
    "method.response.header.Access-Control-Allow-Origin"  = true
  }
}

resource "aws_api_gateway_integration_response" "admin_reconciliation_transactions_preview_options" {
  rest_api_id         = aws_api_gateway_rest_api.main.id
  resource_id         = aws_api_gateway_resource.admin_reconciliation_transactions_preview.id
  http_method         = aws_api_gateway_method.admin_reconciliation_transactions_preview_options.http_method
  status_code         = aws_api_gateway_method_response.admin_reconciliation_transactions_preview_options_200.status_code
  response_parameters = local.cors_headers
}

# ── /admin/reconciliation/transactions/{batchId}  (item — delete a batch) ───

resource "aws_api_gateway_resource" "admin_reconciliation_transaction_batch" {
  rest_api_id = aws_api_gateway_rest_api.main.id
  parent_id   = aws_api_gateway_resource.admin_reconciliation_transactions.id
  path_part   = "{batchId}"
}

resource "aws_api_gateway_method" "admin_reconciliation_transaction_batch_delete" {
  rest_api_id   = aws_api_gateway_rest_api.main.id
  resource_id   = aws_api_gateway_resource.admin_reconciliation_transaction_batch.id
  http_method   = "DELETE"
  authorization = "COGNITO_USER_POOLS"
  authorizer_id = aws_api_gateway_authorizer.cognito.id
  request_parameters = {
    "method.request.path.batchId"          = true
    "method.request.querystring.eventType" = true
  }
}

resource "aws_api_gateway_integration" "admin_reconciliation_transaction_batch_delete" {
  rest_api_id             = aws_api_gateway_rest_api.main.id
  resource_id             = aws_api_gateway_resource.admin_reconciliation_transaction_batch.id
  http_method             = aws_api_gateway_method.admin_reconciliation_transaction_batch_delete.http_method
  integration_http_method = "POST"
  type                    = "AWS_PROXY"
  uri                     = aws_lambda_function.manage_reconciliation.invoke_arn
}

resource "aws_api_gateway_method" "admin_reconciliation_transaction_batch_options" {
  rest_api_id   = aws_api_gateway_rest_api.main.id
  resource_id   = aws_api_gateway_resource.admin_reconciliation_transaction_batch.id
  http_method   = "OPTIONS"
  authorization = "NONE"
}

resource "aws_api_gateway_integration" "admin_reconciliation_transaction_batch_options" {
  rest_api_id       = aws_api_gateway_rest_api.main.id
  resource_id       = aws_api_gateway_resource.admin_reconciliation_transaction_batch.id
  http_method       = aws_api_gateway_method.admin_reconciliation_transaction_batch_options.http_method
  type              = "MOCK"
  request_templates = { "application/json" = "{\"statusCode\": 200}" }
}

resource "aws_api_gateway_method_response" "admin_reconciliation_transaction_batch_options_200" {
  rest_api_id = aws_api_gateway_rest_api.main.id
  resource_id = aws_api_gateway_resource.admin_reconciliation_transaction_batch.id
  http_method = aws_api_gateway_method.admin_reconciliation_transaction_batch_options.http_method
  status_code = "200"
  response_parameters = {
    "method.response.header.Access-Control-Allow-Headers" = true
    "method.response.header.Access-Control-Allow-Methods" = true
    "method.response.header.Access-Control-Allow-Origin"  = true
  }
}

resource "aws_api_gateway_integration_response" "admin_reconciliation_transaction_batch_options" {
  rest_api_id         = aws_api_gateway_rest_api.main.id
  resource_id         = aws_api_gateway_resource.admin_reconciliation_transaction_batch.id
  http_method         = aws_api_gateway_method.admin_reconciliation_transaction_batch_options.http_method
  status_code         = aws_api_gateway_method_response.admin_reconciliation_transaction_batch_options_200.status_code
  response_parameters = local.cors_headers
}

# ── /admin/reconciliation/runs  (collection) ─────────────────────────────────

resource "aws_api_gateway_resource" "admin_reconciliation_runs" {
  rest_api_id = aws_api_gateway_rest_api.main.id
  parent_id   = aws_api_gateway_resource.admin_reconciliation.id
  path_part   = "runs"
}

resource "aws_api_gateway_method" "admin_reconciliation_runs_get" {
  rest_api_id   = aws_api_gateway_rest_api.main.id
  resource_id   = aws_api_gateway_resource.admin_reconciliation_runs.id
  http_method   = "GET"
  authorization = "COGNITO_USER_POOLS"
  authorizer_id = aws_api_gateway_authorizer.cognito.id
  request_parameters = {
    "method.request.querystring.eventType" = true
  }
}

resource "aws_api_gateway_method" "admin_reconciliation_runs_post" {
  rest_api_id   = aws_api_gateway_rest_api.main.id
  resource_id   = aws_api_gateway_resource.admin_reconciliation_runs.id
  http_method   = "POST"
  authorization = "COGNITO_USER_POOLS"
  authorizer_id = aws_api_gateway_authorizer.cognito.id
}

resource "aws_api_gateway_integration" "admin_reconciliation_runs_get" {
  rest_api_id             = aws_api_gateway_rest_api.main.id
  resource_id             = aws_api_gateway_resource.admin_reconciliation_runs.id
  http_method             = aws_api_gateway_method.admin_reconciliation_runs_get.http_method
  integration_http_method = "POST"
  type                    = "AWS_PROXY"
  uri                     = aws_lambda_function.manage_reconciliation.invoke_arn
}

resource "aws_api_gateway_integration" "admin_reconciliation_runs_post" {
  rest_api_id             = aws_api_gateway_rest_api.main.id
  resource_id             = aws_api_gateway_resource.admin_reconciliation_runs.id
  http_method             = aws_api_gateway_method.admin_reconciliation_runs_post.http_method
  integration_http_method = "POST"
  type                    = "AWS_PROXY"
  uri                     = aws_lambda_function.manage_reconciliation.invoke_arn
}

resource "aws_api_gateway_method" "admin_reconciliation_runs_options" {
  rest_api_id   = aws_api_gateway_rest_api.main.id
  resource_id   = aws_api_gateway_resource.admin_reconciliation_runs.id
  http_method   = "OPTIONS"
  authorization = "NONE"
}

resource "aws_api_gateway_integration" "admin_reconciliation_runs_options" {
  rest_api_id       = aws_api_gateway_rest_api.main.id
  resource_id       = aws_api_gateway_resource.admin_reconciliation_runs.id
  http_method       = aws_api_gateway_method.admin_reconciliation_runs_options.http_method
  type              = "MOCK"
  request_templates = { "application/json" = "{\"statusCode\": 200}" }
}

resource "aws_api_gateway_method_response" "admin_reconciliation_runs_options_200" {
  rest_api_id = aws_api_gateway_rest_api.main.id
  resource_id = aws_api_gateway_resource.admin_reconciliation_runs.id
  http_method = aws_api_gateway_method.admin_reconciliation_runs_options.http_method
  status_code = "200"
  response_parameters = {
    "method.response.header.Access-Control-Allow-Headers" = true
    "method.response.header.Access-Control-Allow-Methods" = true
    "method.response.header.Access-Control-Allow-Origin"  = true
  }
}

resource "aws_api_gateway_integration_response" "admin_reconciliation_runs_options" {
  rest_api_id         = aws_api_gateway_rest_api.main.id
  resource_id         = aws_api_gateway_resource.admin_reconciliation_runs.id
  http_method         = aws_api_gateway_method.admin_reconciliation_runs_options.http_method
  status_code         = aws_api_gateway_method_response.admin_reconciliation_runs_options_200.status_code
  response_parameters = local.cors_headers
}

# ── /admin/reconciliation/runs/{runId}  (item — detail or ?format=csv export) ─

resource "aws_api_gateway_resource" "admin_reconciliation_run_item" {
  rest_api_id = aws_api_gateway_rest_api.main.id
  parent_id   = aws_api_gateway_resource.admin_reconciliation_runs.id
  path_part   = "{runId}"
}

resource "aws_api_gateway_method" "admin_reconciliation_run_item_get" {
  rest_api_id   = aws_api_gateway_rest_api.main.id
  resource_id   = aws_api_gateway_resource.admin_reconciliation_run_item.id
  http_method   = "GET"
  authorization = "COGNITO_USER_POOLS"
  authorizer_id = aws_api_gateway_authorizer.cognito.id
  request_parameters = {
    "method.request.path.runId"            = true
    "method.request.querystring.eventType" = true
    "method.request.querystring.format"    = false
  }
}

resource "aws_api_gateway_integration" "admin_reconciliation_run_item_get" {
  rest_api_id             = aws_api_gateway_rest_api.main.id
  resource_id             = aws_api_gateway_resource.admin_reconciliation_run_item.id
  http_method             = aws_api_gateway_method.admin_reconciliation_run_item_get.http_method
  integration_http_method = "POST"
  type                    = "AWS_PROXY"
  uri                     = aws_lambda_function.manage_reconciliation.invoke_arn
}

resource "aws_api_gateway_method" "admin_reconciliation_run_item_options" {
  rest_api_id   = aws_api_gateway_rest_api.main.id
  resource_id   = aws_api_gateway_resource.admin_reconciliation_run_item.id
  http_method   = "OPTIONS"
  authorization = "NONE"
}

resource "aws_api_gateway_integration" "admin_reconciliation_run_item_options" {
  rest_api_id       = aws_api_gateway_rest_api.main.id
  resource_id       = aws_api_gateway_resource.admin_reconciliation_run_item.id
  http_method       = aws_api_gateway_method.admin_reconciliation_run_item_options.http_method
  type              = "MOCK"
  request_templates = { "application/json" = "{\"statusCode\": 200}" }
}

resource "aws_api_gateway_method_response" "admin_reconciliation_run_item_options_200" {
  rest_api_id = aws_api_gateway_rest_api.main.id
  resource_id = aws_api_gateway_resource.admin_reconciliation_run_item.id
  http_method = aws_api_gateway_method.admin_reconciliation_run_item_options.http_method
  status_code = "200"
  response_parameters = {
    "method.response.header.Access-Control-Allow-Headers" = true
    "method.response.header.Access-Control-Allow-Methods" = true
    "method.response.header.Access-Control-Allow-Origin"  = true
  }
}

resource "aws_api_gateway_integration_response" "admin_reconciliation_run_item_options" {
  rest_api_id         = aws_api_gateway_rest_api.main.id
  resource_id         = aws_api_gateway_resource.admin_reconciliation_run_item.id
  http_method         = aws_api_gateway_method.admin_reconciliation_run_item_options.http_method
  status_code         = aws_api_gateway_method_response.admin_reconciliation_run_item_options_200.status_code
  response_parameters = local.cors_headers
}
