# ── Attendee accounts (Amazon Cognito) ───────────────────────────────────────
# Event attendees sign up / sign in here. This is a SEPARATE user pool from the admin pool in
# cognito.tf on purpose: the /admin/* API Gateway authorizer accepts any user of the admin pool and
# treats them as an administrator, so public sign-ups must never share it.
#
# The app client is a public (secret-less) SPA client using the OAuth 2.0 authorization-code grant
# (PKCE) for the Hosted UI / social sign-in, plus USER_PASSWORD_AUTH for the in-app e-mail forms.
# Only the event-platform public Lambda reads these tokens (it verifies the ID token signature).

locals {
  attendee_frontend_origin = var.frontend_domain != "" ? "https://${var.frontend_domain}" : ""

  # Callback / sign-out URLs: the deployed site plus any extras (e.g. http://localhost:5173 for local development).
  attendee_callback_urls = distinct(concat(
    local.attendee_frontend_origin != "" ? ["${local.attendee_frontend_origin}/auth/callback"] : [],
    [for origin in var.attendee_extra_origins : "${origin}/auth/callback"]
  ))
  attendee_logout_urls = distinct(concat(
    local.attendee_frontend_origin != "" ? ["${local.attendee_frontend_origin}/"] : [],
    [for origin in var.attendee_extra_origins : "${origin}/"]
  ))

  attendee_social_provider_names = keys(var.attendee_social_providers)

  attendee_default_scopes = {
    Google          = "openid email profile"
    Facebook        = "public_profile,email"
    LoginWithAmazon = "profile"
  }
}

resource "aws_cognito_user_pool" "attendees" {
  name = "${local.name_prefix}-attendees-pool"

  # Sign in with the e-mail address; Cognito sends a verification code before the account is usable.
  username_attributes      = ["email"]
  auto_verified_attributes = ["email"]

  password_policy {
    minimum_length                   = 10
    require_lowercase                = true
    require_uppercase                = true
    require_numbers                  = true
    require_symbols                  = false
    temporary_password_validity_days = 7
  }

  mfa_configuration = "OFF"

  account_recovery_setting {
    recovery_mechanism {
      name     = "verified_email"
      priority = 1
    }
  }

  verification_message_template {
    default_email_option = "CONFIRM_WITH_CODE"
    email_subject        = "Your ${var.project_name} verification code"
    email_message        = "Your verification code is {####}. If you did not request it, you can ignore this message."
  }

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

  admin_create_user_config {
    allow_admin_create_user_only = false
  }

  email_configuration {
    email_sending_account = "COGNITO_DEFAULT"
  }

  deletion_protection = "ACTIVE"

  tags = {
    Name = "${local.name_prefix}-attendees-pool"
  }
}

# Hosted UI domain (needed for the social-login redirect and the /oauth2/token endpoint).
resource "aws_cognito_user_pool_domain" "attendees" {
  domain       = var.attendee_cognito_domain_prefix != "" ? var.attendee_cognito_domain_prefix : "${local.name_prefix}-attendees"
  user_pool_id = aws_cognito_user_pool.attendees.id
}

# Social identity providers are configuration-driven: add an entry to var.attendee_social_providers
# (and its secret to var.attendee_social_provider_secrets) to enable one; nothing is enabled by default.
resource "aws_cognito_identity_provider" "attendee_social" {
  for_each = toset(local.attendee_social_provider_names)

  user_pool_id  = aws_cognito_user_pool.attendees.id
  provider_name = each.value
  provider_type = var.attendee_social_providers[each.value].provider_type

  provider_details = {
    client_id        = var.attendee_social_providers[each.value].client_id
    client_secret    = var.attendee_social_provider_secrets[each.value]
    authorize_scopes = coalesce(var.attendee_social_providers[each.value].authorize_scopes, lookup(local.attendee_default_scopes, var.attendee_social_providers[each.value].provider_type, "openid email profile"))
  }

  attribute_mapping = {
    email    = "email"
    name     = "name"
    username = "sub"
  }
}

resource "aws_cognito_user_pool_client" "attendees" {
  name         = "${local.name_prefix}-attendees-client"
  user_pool_id = aws_cognito_user_pool.attendees.id

  # A browser app cannot keep a secret: no client secret, PKCE protects the code exchange.
  generate_secret = false

  explicit_auth_flows = [
    "ALLOW_USER_PASSWORD_AUTH",
    "ALLOW_REFRESH_TOKEN_AUTH",
  ]

  allowed_oauth_flows_user_pool_client = true
  allowed_oauth_flows                  = ["code"]
  allowed_oauth_scopes                 = ["openid", "email", "profile"]
  callback_urls                        = local.attendee_callback_urls
  logout_urls                          = local.attendee_logout_urls
  supported_identity_providers         = concat(["COGNITO"], [for p in aws_cognito_identity_provider.attendee_social : p.provider_name])

  # Sign-in and password-reset answers do not reveal whether an address has an account.
  prevent_user_existence_errors = "ENABLED"
  enable_token_revocation       = true

  access_token_validity  = 60 # minutes
  id_token_validity      = 60 # minutes
  refresh_token_validity = 30 # days

  token_validity_units {
    access_token  = "minutes"
    id_token      = "minutes"
    refresh_token = "days"
  }

  read_attributes  = ["email", "email_verified", "name"]
  write_attributes = ["email", "name"]
}
