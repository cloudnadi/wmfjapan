# ── IAM role shared by the event-platform Lambda functions ───────────────────
# Table access lives next to the tables (dynamodb_event_platform.tf), asset
# access in assets.tf. This file holds logging and email.

resource "aws_iam_role" "lambda_exec" {
  name = "${local.name_prefix}-lambda-role"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Action    = "sts:AssumeRole"
      Effect    = "Allow"
      Principal = { Service = "lambda.amazonaws.com" }
    }]
  })
}

resource "aws_iam_role_policy" "lambda_policy" {
  name = "${local.name_prefix}-lambda-policy"
  role = aws_iam_role.lambda_exec.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = [
          "logs:CreateLogGroup",
          "logs:CreateLogStream",
          "logs:PutLogEvents"
        ]
        Resource = "arn:aws:logs:${var.aws_region}:*:*"
      },
      {
        Effect   = "Allow"
        Action   = ["ses:SendEmail", "ses:SendRawEmail"]
        Resource = "*"
        Condition = {
          StringEquals = {
            "ses:FromAddress" = var.ses_from_email
          }
        }
      }
    ]
  })
}

# ── QR code layer (vendored, used for e-ticket emails) ───────────────────────

data "archive_file" "qr_layer" {
  type        = "zip"
  source_dir  = "${path.module}/../lambdas/qr_layer"
  output_path = "${path.module}/../lambdas/dist/qr_layer.zip"
}

resource "aws_lambda_layer_version" "qr_code" {
  layer_name          = "${local.name_prefix}-qr-code"
  filename            = data.archive_file.qr_layer.output_path
  source_code_hash    = data.archive_file.qr_layer.output_base64sha256
  compatible_runtimes = ["python3.12"]
}

# ── Common environment variables ─────────────────────────────────────────────

locals {
  lambda_env = {
    SITE_CONFIG_TABLE = aws_dynamodb_table.site_config.name
    FRONTEND_URL      = var.frontend_domain != "" ? "https://${var.frontend_domain}" : try("https://${aws_cloudfront_distribution.frontend[0].domain_name}", "")
    SES_FROM_EMAIL    = var.ses_from_email
    ADMIN_EMAIL       = var.admin_email
    ADMIN_EMAIL_CC    = var.admin_email_additional
    REGION            = var.aws_region
    ENVIRONMENT       = var.environment
  }
}
