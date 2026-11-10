output "seats_table_name" {
  description = "Name of the seats table — pass this to any host Lambda (via a SEATS_TABLE env var) that imports seat_selection_logic.py directly to claim/release seats as part of its own request handling."
  value       = aws_dynamodb_table.seats.name
}

output "seats_table_arn" {
  value = aws_dynamodb_table.seats.arn
}

output "category_overrides_table_name" {
  value = aws_dynamodb_table.category_overrides.name
}

output "category_overrides_table_arn" {
  value = aws_dynamodb_table.category_overrides.arn
}

output "zone_pricing_table_name" {
  value = aws_dynamodb_table.zone_pricing.name
}

output "zone_pricing_table_arn" {
  value = aws_dynamodb_table.zone_pricing.arn
}

output "seat_purchases_table_name" {
  description = "Name of the seat purchases table — pass this to your own registrant-facing Lambda (via a SEAT_PURCHASES_TABLE env var) that owns purchase creation/checkout/cancel — see the README."
  value       = aws_dynamodb_table.seat_purchases.name
}

output "seat_purchases_table_arn" {
  value = aws_dynamodb_table.seat_purchases.arn
}

output "seat_purchase_webhook_function_name" {
  value = aws_lambda_function.seat_purchase_webhook.function_name
}

output "seat_purchase_webhook_function_arn" {
  value = aws_lambda_function.seat_purchase_webhook.arn
}

output "seat_purchase_webhook_path" {
  description = "The webhook's own path template, for constructing the exact URL to register with each payment provider — append it to your API Gateway stage's invoke URL, e.g. .../webhooks/seat-selection/square."
  value       = "/webhooks/seat-selection/{provider}"
}

output "get_available_seats_function_name" {
  value = aws_lambda_function.get_available_seats.function_name
}

output "get_available_seats_function_arn" {
  value = aws_lambda_function.get_available_seats.arn
}

output "manage_seats_function_name" {
  value = aws_lambda_function.manage_seats.function_name
}

output "manage_seats_function_arn" {
  value = aws_lambda_function.manage_seats.arn
}

output "public_seats_resource_id" {
  value = aws_api_gateway_resource.seats.id
}

output "admin_seats_resource_id" {
  value = aws_api_gateway_resource.admin_seats.id
}

output "admin_seat_item_resource_id" {
  value = aws_api_gateway_resource.admin_seat_item.id
}

output "admin_seat_pricing_resource_id" {
  value = aws_api_gateway_resource.admin_seat_pricing.id
}

output "admin_seat_pricing_item_resource_id" {
  value = aws_api_gateway_resource.admin_seat_pricing_item.id
}

# Feed this into the host's aws_api_gateway_deployment trigger hash
# (sha1(jsonencode([...]))) so an apply redeploys the stage whenever any of
# this module's routes or Lambda code change.
output "deployment_trigger_inputs" {
  value = [
    aws_api_gateway_integration.seats_get.id,
    aws_api_gateway_integration.seats_options.id,
    aws_api_gateway_integration.admin_seats_get.id,
    aws_api_gateway_integration.admin_seats_post.id,
    aws_api_gateway_integration.admin_seats_options.id,
    aws_api_gateway_integration.admin_seat_item_delete.id,
    aws_api_gateway_integration.admin_seat_item_options.id,
    aws_api_gateway_integration.admin_seat_pricing_get.id,
    aws_api_gateway_integration.admin_seat_pricing_options.id,
    aws_api_gateway_integration.admin_seat_pricing_item_put.id,
    aws_api_gateway_integration.admin_seat_pricing_item_delete.id,
    aws_api_gateway_integration.admin_seat_pricing_item_options.id,
    aws_api_gateway_integration.admin_seat_purchases_get.id,
    aws_api_gateway_integration.admin_seat_purchases_options.id,
    aws_api_gateway_integration.admin_seat_purchase_item_get.id,
    aws_api_gateway_integration.admin_seat_purchase_item_patch.id,
    aws_api_gateway_integration.admin_seat_purchase_item_options.id,
    aws_api_gateway_integration.admin_seat_check_in_post.id,
    aws_api_gateway_integration.admin_seat_check_in_options.id,
    aws_api_gateway_integration.seat_selection_webhook_post.id,
    data.archive_file.get_available_seats.output_base64sha256,
    data.archive_file.manage_seats.output_base64sha256,
    data.archive_file.seat_purchase_webhook.output_base64sha256,
  ]
}
