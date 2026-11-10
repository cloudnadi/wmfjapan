# ── Cognito User Pool ────────────────────────────────────────────────────────

resource "aws_cognito_user_pool" "admin" {
  name = "${local.name_prefix}-admin-pool"

  # Password policy
  password_policy {
    minimum_length                   = 12
    require_lowercase                = true
    require_numbers                  = true
    require_symbols                  = true
    require_uppercase                = true
    temporary_password_validity_days = 7
  }

  # MFA (optional — OPTIONAL lets admins enable it per-user)
  mfa_configuration = "OPTIONAL"

  software_token_mfa_configuration {
    enabled = true
  }

  # Account recovery
  account_recovery_setting {
    recovery_mechanism {
      name     = "verified_email"
      priority = 1
    }
  }

  # Auto-verify email
  auto_verified_attributes = ["email"]

  # Schema
  schema {
    attribute_data_type = "String"
    name                = "email"
    required            = true
    mutable             = true

    string_attribute_constraints {
      min_length = 5
      max_length = 254
    }
  }

  # Email sender (uses SES if configured, otherwise Cognito default)
  email_configuration {
    email_sending_account = "COGNITO_DEFAULT"
  }

  # Prevent users from signing themselves up
  admin_create_user_config {
    allow_admin_create_user_only = true

    invite_message_template {
      email_subject = "Welcome to the ${var.project_name} Admin Portal"
      email_message = <<-EOT
        Hello,

        You have been granted admin access to the ${var.project_name} registration portal.

        Username: {username}
        Temporary Password: {####}

        Please log in and change your password immediately.

        Regards,
        ${upper(var.project_name)} Tech Team
      EOT
      sms_message   = "${upper(var.project_name)} Admin: username={username} temp_password={####}"
    }
  }

  user_pool_add_ons {
    advanced_security_mode = "ENFORCED"
  }

  tags = {
    Name = "${local.name_prefix}-admin-pool"
  }
}

# ── App Client ───────────────────────────────────────────────────────────────

resource "aws_cognito_user_pool_client" "admin_client" {
  name         = "${local.name_prefix}-admin-client"
  user_pool_id = aws_cognito_user_pool.admin.id

  # No client secret (SPA / JS dashboard)
  generate_secret = false

  # Auth flows
  explicit_auth_flows = [
    "ALLOW_USER_SRP_AUTH",
    "ALLOW_REFRESH_TOKEN_AUTH",
    "ALLOW_USER_PASSWORD_AUTH",
    "ALLOW_ADMIN_USER_PASSWORD_AUTH"
  ]

  # Token validity
  access_token_validity  = 60 # minutes
  id_token_validity      = 60 # minutes
  refresh_token_validity = 7  # days

  token_validity_units {
    access_token  = "minutes"
    id_token      = "minutes"
    refresh_token = "days"
  }

  # Prevent OAuth flows (direct auth only)
  allowed_oauth_flows_user_pool_client = false

  read_attributes  = ["email", "email_verified"]
  write_attributes = ["email"]
}

# ── Admin Group ──────────────────────────────────────────────────────────────

resource "aws_cognito_user_group" "admins" {
  name         = "Admins"
  user_pool_id = aws_cognito_user_pool.admin.id
  description  = "Administrators for the ${var.project_name} portal"
}
