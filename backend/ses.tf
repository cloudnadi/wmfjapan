# ── SES Email Identity ───────────────────────────────────────────────────────
# Terraform creates the identity; you must verify it in SES before emails work.
# For a domain identity, add the DKIM records shown in outputs to your DNS.

resource "aws_ses_email_identity" "from" {
  email = var.ses_from_email
}

resource "aws_ses_email_identity" "admin" {
  # Also verify the admin inbox so SES can receive bounce/complaint notifications
  email = var.ses_from_email
}

# ── SES Configuration Set (for bounce/complaint tracking) ───────────────────

resource "aws_ses_configuration_set" "main" {
  name = "${local.name_prefix}-config-set"

  delivery_options {
    tls_policy = "Require"
  }

  reputation_metrics_enabled = true
  sending_enabled            = true
}

# ── SES Domain Identity (optional — uncomment if using a full domain) ────────
# resource "aws_ses_domain_identity" "domain" {
#   domain = "cloudnadi.com"
# }
#
# resource "aws_ses_domain_dkim" "domain" {
#   domain = aws_ses_domain_identity.domain.domain
# }
#
# output "ses_dkim_tokens" {
#   value = aws_ses_domain_dkim.domain.dkim_tokens
#   description = "Add these as CNAME records in your DNS (_domainkey.cloudnadi.com)"
# }
