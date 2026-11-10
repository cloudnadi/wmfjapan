output "seat_upgrade_tiers_table_name" {
  value = aws_dynamodb_table.seat_upgrade_tiers.name
}

output "seat_upgrades_table_name" {
  value = aws_dynamodb_table.seat_upgrades.name
}

output "seat_upgrades_table_arn" {
  value = aws_dynamodb_table.seat_upgrades.arn
}

output "manage_seat_upgrades_function_name" {
  value = aws_lambda_function.manage_seat_upgrades.function_name
}

output "seat_upgrade_function_name" {
  value = aws_lambda_function.seat_upgrade.function_name
}

output "seat_upgrade_webhook_function_name" {
  value = aws_lambda_function.seat_upgrade_webhook.function_name
}

output "webhook_path_template" {
  description = "For filling in each provider's dashboard: {api invoke url}/webhooks/seat-upgrade/{paypay|square}"
  value       = "/webhooks/seat-upgrade/{provider}"
}

# Folded into the host's own aws_api_gateway_deployment trigger hash so a
# route/integration change in this module forces a redeploy of the shared
# stage. jsonencode()-ing a resource object (not just its .id) captures every
# exported attribute, so an in-place update (e.g. a changed integration URI)
# changes this hash even when the resource's id itself doesn't.
output "api_trigger_hash" {
  value = sha1(jsonencode([
    aws_api_gateway_resource.seat_upgrade_tiers,
    aws_api_gateway_method.seat_upgrade_tiers_get,
    aws_api_gateway_method.seat_upgrade_tiers_post,
    aws_api_gateway_integration.seat_upgrade_tiers_get,
    aws_api_gateway_integration.seat_upgrade_tiers_post,
    aws_api_gateway_method.seat_upgrade_tiers_options,
    aws_api_gateway_integration.seat_upgrade_tiers_options,
    aws_api_gateway_method_response.seat_upgrade_tiers_options_200,
    aws_api_gateway_integration_response.seat_upgrade_tiers_options,
    aws_api_gateway_resource.seat_upgrade_seat_map,
    aws_api_gateway_method.seat_upgrade_seat_map_post,
    aws_api_gateway_integration.seat_upgrade_seat_map_post,
    aws_api_gateway_method.seat_upgrade_seat_map_options,
    aws_api_gateway_integration.seat_upgrade_seat_map_options,
    aws_api_gateway_method_response.seat_upgrade_seat_map_options_200,
    aws_api_gateway_integration_response.seat_upgrade_seat_map_options,
    aws_api_gateway_resource.seat_upgrade_announcement,
    aws_api_gateway_method.seat_upgrade_announcement_post,
    aws_api_gateway_integration.seat_upgrade_announcement_post,
    aws_api_gateway_method.seat_upgrade_announcement_options,
    aws_api_gateway_integration.seat_upgrade_announcement_options,
    aws_api_gateway_method_response.seat_upgrade_announcement_options_200,
    aws_api_gateway_integration_response.seat_upgrade_announcement_options,
    aws_api_gateway_resource.seat_upgrade_email_templates,
    aws_api_gateway_method.seat_upgrade_email_templates_get,
    aws_api_gateway_integration.seat_upgrade_email_templates_get,
    aws_api_gateway_method.seat_upgrade_email_templates_options,
    aws_api_gateway_integration.seat_upgrade_email_templates_options,
    aws_api_gateway_method_response.seat_upgrade_email_templates_options_200,
    aws_api_gateway_integration_response.seat_upgrade_email_templates_options,
    aws_api_gateway_resource.seat_upgrade_email_template_item,
    aws_api_gateway_method.seat_upgrade_email_template_item_patch,
    aws_api_gateway_integration.seat_upgrade_email_template_item_patch,
    aws_api_gateway_method.seat_upgrade_email_template_item_delete,
    aws_api_gateway_integration.seat_upgrade_email_template_item_delete,
    aws_api_gateway_method.seat_upgrade_email_template_item_options,
    aws_api_gateway_integration.seat_upgrade_email_template_item_options,
    aws_api_gateway_method_response.seat_upgrade_email_template_item_options_200,
    aws_api_gateway_integration_response.seat_upgrade_email_template_item_options,
    aws_api_gateway_resource.seat_upgrade_email_template_preview,
    aws_api_gateway_method.seat_upgrade_email_template_preview_post,
    aws_api_gateway_integration.seat_upgrade_email_template_preview_post,
    aws_api_gateway_method.seat_upgrade_email_template_preview_options,
    aws_api_gateway_integration.seat_upgrade_email_template_preview_options,
    aws_api_gateway_method_response.seat_upgrade_email_template_preview_options_200,
    aws_api_gateway_integration_response.seat_upgrade_email_template_preview_options,
    aws_api_gateway_resource.seat_upgrade_tier_item,
    aws_api_gateway_method.seat_upgrade_tier_item_get,
    aws_api_gateway_method.seat_upgrade_tier_item_patch,
    aws_api_gateway_method.seat_upgrade_tier_item_delete,
    aws_api_gateway_integration.seat_upgrade_tier_item_get,
    aws_api_gateway_integration.seat_upgrade_tier_item_patch,
    aws_api_gateway_integration.seat_upgrade_tier_item_delete,
    aws_api_gateway_method.seat_upgrade_tier_item_options,
    aws_api_gateway_integration.seat_upgrade_tier_item_options,
    aws_api_gateway_method_response.seat_upgrade_tier_item_options_200,
    aws_api_gateway_integration_response.seat_upgrade_tier_item_options,
    aws_api_gateway_resource.admin_seat_upgrades,
    aws_api_gateway_method.admin_seat_upgrades_get,
    aws_api_gateway_integration.admin_seat_upgrades_get,
    aws_api_gateway_method.admin_seat_upgrades_options,
    aws_api_gateway_integration.admin_seat_upgrades_options,
    aws_api_gateway_method_response.admin_seat_upgrades_options_200,
    aws_api_gateway_integration_response.admin_seat_upgrades_options,
    aws_api_gateway_resource.admin_seat_upgrade_item,
    aws_api_gateway_method.admin_seat_upgrade_item_patch,
    aws_api_gateway_integration.admin_seat_upgrade_item_patch,
    aws_api_gateway_method.admin_seat_upgrade_item_options,
    aws_api_gateway_integration.admin_seat_upgrade_item_options,
    aws_api_gateway_method_response.admin_seat_upgrade_item_options_200,
    aws_api_gateway_integration_response.admin_seat_upgrade_item_options,
    aws_api_gateway_resource.my_seat_upgrades,
    aws_api_gateway_method.my_seat_upgrades_get,
    aws_api_gateway_integration.my_seat_upgrades_get,
    aws_api_gateway_method.my_seat_upgrades_post,
    aws_api_gateway_integration.my_seat_upgrades_post,
    aws_api_gateway_method.my_seat_upgrades_options,
    aws_api_gateway_integration.my_seat_upgrades_options,
    aws_api_gateway_method_response.my_seat_upgrades_options_200,
    aws_api_gateway_integration_response.my_seat_upgrades_options,
    aws_api_gateway_resource.my_seat_upgrade_item,
    aws_api_gateway_method.my_seat_upgrade_get,
    aws_api_gateway_integration.my_seat_upgrade_get,
    aws_api_gateway_method.my_seat_upgrade_delete,
    aws_api_gateway_integration.my_seat_upgrade_delete,
    aws_api_gateway_method.my_seat_upgrade_item_options,
    aws_api_gateway_integration.my_seat_upgrade_item_options,
    aws_api_gateway_method_response.my_seat_upgrade_item_options_200,
    aws_api_gateway_integration_response.my_seat_upgrade_item_options,
    aws_api_gateway_resource.my_seat_upgrade_checkout,
    aws_api_gateway_method.my_seat_upgrade_checkout_post,
    aws_api_gateway_integration.my_seat_upgrade_checkout_post,
    aws_api_gateway_method.my_seat_upgrade_checkout_options,
    aws_api_gateway_integration.my_seat_upgrade_checkout_options,
    aws_api_gateway_method_response.my_seat_upgrade_checkout_options_200,
    aws_api_gateway_integration_response.my_seat_upgrade_checkout_options,
    aws_api_gateway_resource.webhooks_seat_upgrade,
    aws_api_gateway_resource.webhooks_seat_upgrade_provider,
    aws_api_gateway_method.webhooks_seat_upgrade_post,
    aws_api_gateway_integration.webhooks_seat_upgrade_post,
  ]))
}

# Every method/integration resource this module owns, for the host's
# aws_api_gateway_deployment `depends_on` (depending on the whole module
# object waits for all of it without hand-listing each one twice).
output "api_resources" {
  value = [
    aws_api_gateway_integration.seat_upgrade_tiers_get,
    aws_api_gateway_integration.seat_upgrade_tiers_post,
    aws_api_gateway_integration.seat_upgrade_tiers_options,
    aws_api_gateway_integration.seat_upgrade_seat_map_post,
    aws_api_gateway_integration.seat_upgrade_seat_map_options,
    aws_api_gateway_integration.seat_upgrade_announcement_post,
    aws_api_gateway_integration.seat_upgrade_announcement_options,
    aws_api_gateway_integration.seat_upgrade_email_templates_get,
    aws_api_gateway_integration.seat_upgrade_email_templates_options,
    aws_api_gateway_integration.seat_upgrade_email_template_item_patch,
    aws_api_gateway_integration.seat_upgrade_email_template_item_delete,
    aws_api_gateway_integration.seat_upgrade_email_template_item_options,
    aws_api_gateway_integration.seat_upgrade_email_template_preview_post,
    aws_api_gateway_integration.seat_upgrade_email_template_preview_options,
    aws_api_gateway_integration.seat_upgrade_tier_item_get,
    aws_api_gateway_integration.seat_upgrade_tier_item_patch,
    aws_api_gateway_integration.seat_upgrade_tier_item_delete,
    aws_api_gateway_integration.seat_upgrade_tier_item_options,
    aws_api_gateway_integration.admin_seat_upgrades_get,
    aws_api_gateway_integration.admin_seat_upgrades_options,
    aws_api_gateway_integration.admin_seat_upgrade_item_patch,
    aws_api_gateway_integration.admin_seat_upgrade_item_options,
    aws_api_gateway_integration.my_seat_upgrades_get,
    aws_api_gateway_integration.my_seat_upgrades_post,
    aws_api_gateway_integration.my_seat_upgrades_options,
    aws_api_gateway_integration.my_seat_upgrade_get,
    aws_api_gateway_integration.my_seat_upgrade_delete,
    aws_api_gateway_integration.my_seat_upgrade_item_options,
    aws_api_gateway_integration.my_seat_upgrade_checkout_post,
    aws_api_gateway_integration.my_seat_upgrade_checkout_options,
    aws_api_gateway_integration.webhooks_seat_upgrade_post,
  ]
}
