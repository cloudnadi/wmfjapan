# ── Site configuration table ─────────────────────────────────────────────────
# Small key/value store for runtime settings edited from the admin console
# (currently the payment-method settings, key "payment_methods_config").
# PK: configKey

resource "aws_dynamodb_table" "site_config" {
  name         = "${local.name_prefix}-site-config-${local.suffix}"
  billing_mode = var.dynamodb_billing_mode
  hash_key     = "configKey"

  attribute {
    name = "configKey"
    type = "S"
  }

  point_in_time_recovery {
    enabled = true
  }

  server_side_encryption {
    enabled = true
  }

  tags = {
    Name        = "${local.name_prefix}-site-config"
    Environment = var.environment
  }
}
