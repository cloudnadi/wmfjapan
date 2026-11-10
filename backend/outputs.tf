output "api_base_url" {
  description = "Base URL for the API — paste into frontend-integration/api.js"
  value       = "https://${aws_api_gateway_rest_api.main.id}.execute-api.${var.aws_region}.amazonaws.com/${var.environment}"
}

output "register_endpoint" {
  description = "POST endpoint for registration form"
  value       = "https://${aws_api_gateway_rest_api.main.id}.execute-api.${var.aws_region}.amazonaws.com/${var.environment}/register"
}

output "auth_request_otp_endpoint" {
  description = "POST endpoint to request a login OTP by email"
  value       = "https://${aws_api_gateway_rest_api.main.id}.execute-api.${var.aws_region}.amazonaws.com/${var.environment}/auth/request-otp"
}

output "auth_verify_otp_endpoint" {
  description = "POST endpoint to verify a login OTP and receive an access token"
  value       = "https://${aws_api_gateway_rest_api.main.id}.execute-api.${var.aws_region}.amazonaws.com/${var.environment}/auth/verify-otp"
}

output "visits_endpoint" {
  description = "POST endpoint for SPA page-visit logging"
  value       = "https://${aws_api_gateway_rest_api.main.id}.execute-api.${var.aws_region}.amazonaws.com/${var.environment}/visits"
}

output "membership_status_endpoint" {
  description = "GET endpoint for NPO membership status (requires reg access token)"
  value       = "https://${aws_api_gateway_rest_api.main.id}.execute-api.${var.aws_region}.amazonaws.com/${var.environment}/membership/status"
}

output "npo_membership_table_name" {
  description = "Name of the DynamoDB table storing NPO membership records"
  value       = aws_dynamodb_table.npo_membership.name
}

output "members_table_name" {
  description = "Name of the DynamoDB members master table (login gate list)"
  value       = aws_dynamodb_table.members.name
}

output "admin_registrations_endpoint" {
  description = "GET endpoint for admin — list all registrations"
  value       = "https://${aws_api_gateway_rest_api.main.id}.execute-api.${var.aws_region}.amazonaws.com/${var.environment}/admin/registrations"
}

output "admin_export_endpoint" {
  description = "GET endpoint for admin — CSV export"
  value       = "https://${aws_api_gateway_rest_api.main.id}.execute-api.${var.aws_region}.amazonaws.com/${var.environment}/admin/export"
}

output "admin_partners_endpoint" {
  description = "GET/POST endpoint for admin — manage partners"
  value       = "https://${aws_api_gateway_rest_api.main.id}.execute-api.${var.aws_region}.amazonaws.com/${var.environment}/admin/partners"
}

output "admin_events_endpoint" {
  description = "GET/POST endpoint for admin — manage events"
  value       = "https://${aws_api_gateway_rest_api.main.id}.execute-api.${var.aws_region}.amazonaws.com/${var.environment}/admin/events"
}

output "admin_registration_endpoint" {
  description = "POST/DELETE endpoint for admin — manage individual registrations"
  value       = "https://${aws_api_gateway_rest_api.main.id}.execute-api.${var.aws_region}.amazonaws.com/${var.environment}/admin/registration"
}

output "admin_users_endpoint" {
  description = "GET endpoint for admin — registrants with NPO status"
  value       = "https://${aws_api_gateway_rest_api.main.id}.execute-api.${var.aws_region}.amazonaws.com/${var.environment}/admin/users"
}

output "admin_visits_summary_endpoint" {
  description = "GET endpoint for admin — visits analytics summary"
  value       = "https://${aws_api_gateway_rest_api.main.id}.execute-api.${var.aws_region}.amazonaws.com/${var.environment}/admin/visits-summary"
}

output "site_config_endpoint" {
  description = "GET endpoint (public) — current carousel image URLs"
  value       = "https://${aws_api_gateway_rest_api.main.id}.execute-api.${var.aws_region}.amazonaws.com/${var.environment}/site-config"
}

output "admin_site_config_endpoint" {
  description = "GET/PUT endpoint (admin) — manage carousel images"
  value       = "https://${aws_api_gateway_rest_api.main.id}.execute-api.${var.aws_region}.amazonaws.com/${var.environment}/admin/site-config"
}

output "assets_bucket_name" {
  description = "S3 bucket for carousel images and media assets"
  value       = aws_s3_bucket.assets.id
}

output "assets_base_url" {
  description = "Base URL for media assets served via CloudFront"
  value       = local.assets_base_url
}

output "cognito_user_pool_id" {
  description = "Cognito User Pool ID — needed to create admin users"
  value       = aws_cognito_user_pool.admin.id
}

output "cognito_client_id" {
  description = "Cognito App Client ID — used in admin dashboard login"
  value       = aws_cognito_user_pool_client.admin_client.id
}

output "cognito_region" {
  description = "Region where Cognito pool is deployed"
  value       = var.aws_region
}

output "registration_table_name" {
  description = "Name of the DynamoDB table storing registrations"
  value       = aws_dynamodb_table.registrations.name
}

output "registration_table_arn" {
  description = "ARN of the DynamoDB table storing registrations"
  value       = aws_dynamodb_table.registrations.arn
}

output "events_table_name" {
  value       = aws_dynamodb_table.events.name
  description = "Name of the DynamoDB table storing events metadata"
}

output "events_table_arn" {
  value       = aws_dynamodb_table.events.arn
  description = "ARN of the DynamoDB table storing events metadata"
}

output "entry_slots_table_name" {
  value       = aws_dynamodb_table.entry_slots.name
  description = "Name of the DynamoDB table storing entry slot definitions"
}

output "entry_slots_table_arn" {
  value       = aws_dynamodb_table.entry_slots.arn
  description = "ARN of the DynamoDB table storing entry slots"
}

output "slot_reservations_table_name" {
  value       = aws_dynamodb_table.slot_reservations.name
  description = "Name of the DynamoDB table storing slot reservations"
}

output "slot_reservations_table_arn" {
  value       = aws_dynamodb_table.slot_reservations.arn
  description = "ARN of the DynamoDB table storing slot reservations"
}

output "entry_allocations_table_name" {
  value       = aws_dynamodb_table.entry_allocations.name
  description = "Name of the DynamoDB table storing entry allocations"
}

output "entry_check_ins_table_name" {
  value       = aws_dynamodb_table.entry_check_ins.name
  description = "Name of the DynamoDB table storing entry check-ins"
}


output "ses_from_email" {
  description = "SES sender email (must be verified in SES before emails work)"
  value       = var.ses_from_email
}

output "create_admin_user_command" {
  description = "Run this command after deploy to create the first admin user"
  value       = <<-EOT
    aws cognito-idp admin-create-user \
      --user-pool-id ${aws_cognito_user_pool.admin.id} \
      --username ${var.cognito_admin_email} \
      --user-attributes Name=email,Value=${var.cognito_admin_email} Name=email_verified,Value=true \
      --temporary-password "Test12345678@" \
      --region ${var.aws_region}

    # Then add user to Admins group:
    aws cognito-idp admin-add-user-to-group \
      --user-pool-id ${aws_cognito_user_pool.admin.id} \
      --username ${var.cognito_admin_email} \
      --group-name Admins \
      --region ${var.aws_region}
  EOT
}


# ── Frontend (SPA) Configuration ──────────────────────────────────────────────

output "s3_frontend_bucket_name" {
  description = "S3 bucket name for frontend SPA assets"
  value       = aws_s3_bucket.frontend.id
  sensitive   = false
}

output "s3_frontend_bucket_arn" {
  description = "S3 bucket ARN for frontend SPA"
  value       = aws_s3_bucket.frontend.arn
  sensitive   = false
}

output "cloudfront_distribution_id" {
  description = "CloudFront distribution ID (for invalidations)"
  value       = try(aws_cloudfront_distribution.frontend[0].id, null)
  sensitive   = false
}

output "cloudfront_domain_name" {
  description = "CloudFront distribution domain name (use this as your SPA URL)"
  value       = try(aws_cloudfront_distribution.frontend[0].domain_name, null)
  sensitive   = false
}

output "frontend_url" {
  description = "Frontend SPA URL (HTTP/HTTPS depending on domain configuration)"
  value = var.frontend_domain != "" ? "https://${var.frontend_domain}" : try(
    "https://${aws_cloudfront_distribution.frontend[0].domain_name}",
    null
  )
  sensitive = false
}

output "deploy_frontend_command" {
  description = "Command to deploy built frontend to S3 and invalidate CloudFront"
  value = var.enable_s3_frontend ? (<<-EOT
# Build the frontend (from event-registration/wmfjapan/frontend):
${contains(["dev", "staging", "preview"], var.environment) ? "npm run build:preview" : "npm run build"}
# Sync dist/ to S3:
../scripts/deploy_frontend.sh ${aws_s3_bucket.frontend.id} dist
# Invalidate CloudFront cache (if using CloudFront):
aws cloudfront create-invalidation \
  --distribution-id ${try(aws_cloudfront_distribution.frontend[0].id, "DISTRIBUTION_ID")} \
  --paths "/*"
EOT
  ) : null
  sensitive = false
}

# ── Domain & TLS Configuration ────────────────────────────────────────────────

output "acm_certificate_arn" {
  description = "ACM certificate ARN for the custom domain (auto-generated if enable_domain_management is true)"
  value       = try(aws_acm_certificate.frontend[0].arn, "Not created - enable_domain_management or provide acm_certificate_arn")
  sensitive   = false
}

output "domain_status" {
  description = "Domain management status and configuration"
  value = (var.enable_domain_management && var.frontend_domain != "") ? {
    domain          = var.frontend_domain
    certificate_arn = try(aws_acm_certificate.frontend[0].arn, "Pending...")
    validated       = try(aws_acm_certificate_validation.frontend[0].certificate_arn, "") != "" ? "Yes" : "Validating..."
    route53_records = {
      apex = try(aws_route53_record.frontend_apex[0].fqdn, "Not created")
      www  = try(aws_route53_record.frontend_www[0].fqdn, "Not created")
    }
  } : null
  sensitive = false
}

output "custom_domain_url" {
  description = "Your custom domain URL (once DNS propagation completes, usually 5-30 minutes)"
  value = var.enable_domain_management && var.frontend_domain != "" ? (
    "https://${var.frontend_domain}"
    ) : (
    var.frontend_domain != "" ? "https://${var.frontend_domain}" : null
  )
  sensitive = false
}

# ── Event platform (v2) ──────────────────────────────────────────────────────
output "events_v2_table_name" {
  value       = aws_dynamodb_table.events_v2.name
  description = "Event platform: events"
}

output "event_ticket_types_table_name" {
  value       = aws_dynamodb_table.event_ticket_types.name
  description = "Event platform: ticket types"
}

output "form_schemas_table_name" {
  value       = aws_dynamodb_table.form_schemas.name
  description = "Event platform: versioned registration form schemas"
}

output "event_registrations_table_name" {
  value       = aws_dynamodb_table.event_registrations.name
  description = "Event platform: registrations with answers"
}

output "event_counters_table_name" {
  value       = aws_dynamodb_table.event_counters.name
  description = "Event platform: atomic capacity counters"
}

output "audit_log_table_name" {
  value       = aws_dynamodb_table.audit_log.name
  description = "Event platform: admin audit log"
}
