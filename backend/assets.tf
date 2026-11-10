# ── S3 Bucket for Public Assets (carousel images / media) ────────────────────

resource "aws_s3_bucket" "assets" {
  bucket              = "${local.name_prefix}-assets-${local.suffix}"
  object_lock_enabled = false

  tags = {
    Name = "${local.name_prefix}-assets"
  }
}

resource "aws_s3_bucket_public_access_block" "assets" {
  bucket = aws_s3_bucket.assets.id

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

# Required so browsers can PUT directly via presigned URLs (no Lambda proxy).
resource "aws_s3_bucket_cors_configuration" "assets" {
  bucket = aws_s3_bucket.assets.id

  cors_rule {
    allowed_headers = ["*"]
    allowed_methods = ["PUT", "GET", "HEAD"]
    allowed_origins = ["*"]
    max_age_seconds = 3000
  }
}

# Safety net: expire orphaned media objects after 90 days.
resource "aws_s3_bucket_lifecycle_configuration" "assets" {
  bucket = aws_s3_bucket.assets.id

  # Scoped to the prefixes that were previously covered by a blanket "media/"
  # rule. Event images (media/events/) must not expire.
  rule {
    id     = "expire-old-media"
    status = "Enabled"

    filter {
      prefix = "media/carousel/"
    }

    expiration {
      days = 90
    }
  }

  # Event-registration uploads that were never attached to a registration. Attached files are copied to
  # event-uploads/ (no expiry) when the registration is created.
  rule {
    id     = "expire-unattached-event-uploads"
    status = "Enabled"

    filter {
      prefix = "event-uploads-pending/"
    }

    expiration {
      days = 2
    }
  }

  rule {
    id     = "expire-old-seat-upgrade-media"
    status = "Enabled"

    filter {
      prefix = "media/seat-upgrade/"
    }

    expiration {
      days = 90
    }
  }
}

# CloudFront OAI reads from the assets bucket (reuses the frontend OAI).
data "aws_iam_policy_document" "s3_assets_cloudfront" {
  statement {
    principals {
      type        = "AWS"
      identifiers = [aws_cloudfront_origin_access_identity.frontend.iam_arn]
    }
    actions   = ["s3:GetObject"]
    resources = ["${aws_s3_bucket.assets.arn}/*"]
  }
}

resource "aws_s3_bucket_policy" "assets" {
  bucket = aws_s3_bucket.assets.id
  policy = data.aws_iam_policy_document.s3_assets_cloudfront.json
}

# ── Additional IAM permissions for the shared Lambda role ────────────────────
# Kept separate from the main lambda_policy so assets.tf is self-contained.

resource "aws_iam_role_policy" "lambda_assets_policy" {
  name = "${local.name_prefix}-lambda-assets-policy"
  role = aws_iam_role.lambda_exec.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = [
          "dynamodb:PutItem",
          "dynamodb:GetItem",
          "dynamodb:UpdateItem",
          "dynamodb:DeleteItem",
          "dynamodb:Scan",
          "dynamodb:Query",
        ]
        Resource = [aws_dynamodb_table.site_config.arn]
      },
      {
        Effect = "Allow"
        Action = [
          "s3:PutObject",
          "s3:GetObject",
          "s3:DeleteObject",
          "s3:HeadObject",
        ]
        Resource = "${aws_s3_bucket.assets.arn}/*"
      },
    ]
  })
}

# ── Locals ───────────────────────────────────────────────────────────────────

locals {
  # Base URL used to build the public image URL stored in DynamoDB.
  # Prefers the custom domain; falls back to the CloudFront domain name.
  assets_base_url = (
    var.frontend_domain != ""
    ? "https://${var.frontend_domain}"
    : (var.enable_s3_frontend ? "https://${aws_cloudfront_distribution.frontend[0].domain_name}" : "")
  )
}
