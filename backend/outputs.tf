output "api_base_url" {
  description = "Base URL for the API — paste into frontend-integration/api.js"
  value       = "https://${aws_api_gateway_rest_api.main.id}.execute-api.${var.aws_region}.amazonaws.com/${var.environment}"
}

output "assets_bucket_name" {
  description = "S3 bucket for event images and registration uploads"
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

output "ses_from_email" {
  description = "SES sender email (must be verified in SES before emails work)"
  value       = var.ses_from_email
}

output "create_admin_user_command" {
  description = "Run after deploy to create the first admin (set a real temporary password; the user must change it at first sign-in)"
  value       = <<-EOT
    aws cognito-idp admin-create-user \
      --user-pool-id ${aws_cognito_user_pool.admin.id} \
      --username ${var.cognito_admin_email} \
      --user-attributes Name=email,Value=${var.cognito_admin_email} Name=email_verified,Value=true \
      --temporary-password '<choose-a-temporary-password>' \
      --region ${var.aws_region}

    # Super admin (everything, including payment settings):
    aws cognito-idp admin-add-user-to-group \
      --user-pool-id ${aws_cognito_user_pool.admin.id} \
      --username ${var.cognito_admin_email} \
      --group-name Admins \
      --region ${var.aws_region}

    # Event manager (events, forms, registrations) instead of Admins:
    #   --group-name EventManagers
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
# Build the frontend (from the frontend/ directory):
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

# ── Attendee accounts (Cognito) — public values, used to build the frontend ──

output "attendee_user_pool_id" {
  description = "Attendee Cognito user pool ID (frontend VITE_AUTH_USER_POOL_ID)"
  value       = aws_cognito_user_pool.attendees.id
}

output "attendee_app_client_id" {
  description = "Attendee Cognito app client ID (frontend VITE_AUTH_CLIENT_ID)"
  value       = aws_cognito_user_pool_client.attendees.id
}

output "attendee_hosted_ui_domain" {
  description = "Hosted UI / OAuth domain without scheme (frontend VITE_AUTH_COGNITO_DOMAIN)"
  value       = "${aws_cognito_user_pool_domain.attendees.domain}.auth.${var.aws_region}.amazoncognito.com"
}

output "attendee_social_providers" {
  description = "Comma-separated social provider names enabled on the attendee client (frontend VITE_AUTH_SOCIAL_PROVIDERS)"
  value       = join(",", local.attendee_social_provider_names)
}
