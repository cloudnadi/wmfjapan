# ── S3 Bucket for Frontend SPA ────────────────────────────────────────────────

resource "aws_s3_bucket" "frontend" {
  bucket              = "${local.name_prefix}-spa-${local.suffix}"
  object_lock_enabled = false

  tags = {
    Name = "${local.name_prefix}-frontend-spa"
  }
}

# Block public access (CloudFront will access via OAI)
resource "aws_s3_bucket_public_access_block" "frontend" {
  bucket = aws_s3_bucket.frontend.id

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

# Enable versioning for rollback capability
resource "aws_s3_bucket_versioning" "frontend" {
  bucket = aws_s3_bucket.frontend.id

  versioning_configuration {
    status = "Enabled"
  }
}

# ── CloudFront Origin Access Identity (OAI) ──────────────────────────────────

resource "aws_cloudfront_origin_access_identity" "frontend" {
  comment = "OAI for ${local.name_prefix} frontend SPA"
}

# ── S3 Bucket Policy for CloudFront OAI ──────────────────────────────────────

data "aws_iam_policy_document" "s3_frontend_cloudfront" {
  statement {
    principals {
      type        = "AWS"
      identifiers = [aws_cloudfront_origin_access_identity.frontend.iam_arn]
    }
    actions   = ["s3:GetObject"]
    resources = ["${aws_s3_bucket.frontend.arn}/*"]
  }

  statement {
    principals {
      type        = "AWS"
      identifiers = [aws_cloudfront_origin_access_identity.frontend.iam_arn]
    }
    actions   = ["s3:ListBucket"]
    resources = [aws_s3_bucket.frontend.arn]
  }
}

resource "aws_s3_bucket_policy" "frontend" {
  bucket = aws_s3_bucket.frontend.id
  policy = data.aws_iam_policy_document.s3_frontend_cloudfront.json
}

# ── CloudFront Distribution ───────────────────────────────────────────────────

resource "aws_cloudfront_distribution" "frontend" {
  count = var.enable_s3_frontend ? 1 : 0

  origin {
    domain_name = aws_s3_bucket.frontend.bucket_regional_domain_name
    origin_id   = "S3-${local.name_prefix}-frontend"

    s3_origin_config {
      origin_access_identity = aws_cloudfront_origin_access_identity.frontend.cloudfront_access_identity_path
    }
  }

  # ── Assets origin (carousel images, media) ──────────────────────────────

  origin {
    domain_name = aws_s3_bucket.assets.bucket_regional_domain_name
    origin_id   = "S3-${local.name_prefix}-assets"

    s3_origin_config {
      origin_access_identity = aws_cloudfront_origin_access_identity.frontend.cloudfront_access_identity_path
    }
  }

  enabled             = true
  is_ipv6_enabled     = true
  default_root_object = "index.html"
  comment             = "${local.name_prefix} - Frontend SPA"

  # ── Custom Domain (optional) ──────────────────────────────────────────────

  aliases = var.frontend_domain != "" ? [var.frontend_domain] : []

  # ── Default Cache Behavior ────────────────────────────────────────────────

  default_cache_behavior {
    allowed_methods  = ["GET", "HEAD", "OPTIONS"]
    cached_methods   = ["GET", "HEAD"]
    target_origin_id = "S3-${local.name_prefix}-frontend"

    # Viewer protocol policy
    viewer_protocol_policy = var.frontend_domain != "" ? "https-only" : "allow-all"

    # Cache policy: 1 hour for HTML, longer for assets
    cache_policy_id = aws_cloudfront_cache_policy.frontend.id

    # Compress assets automatically
    compress = true
  }

  # ── Cache Behavior for Media Assets (carousel images — long TTL, versioned keys) ──

  ordered_cache_behavior {
    path_pattern     = "/media/*"
    allowed_methods  = ["GET", "HEAD", "OPTIONS"]
    cached_methods   = ["GET", "HEAD"]
    target_origin_id = "S3-${local.name_prefix}-assets"

    viewer_protocol_policy = var.frontend_domain != "" ? "https-only" : "allow-all"
    cache_policy_id        = aws_cloudfront_cache_policy.assets.id
    compress               = true
  }

  # ── Cache Behavior for Static HTML Pages (parking guide, etc.) ──────────────

  ordered_cache_behavior {
    path_pattern     = "*.html"
    allowed_methods  = ["GET", "HEAD", "OPTIONS"]
    cached_methods   = ["GET", "HEAD"]
    target_origin_id = "S3-${local.name_prefix}-frontend"

    viewer_protocol_policy = var.frontend_domain != "" ? "https-only" : "allow-all"
    cache_policy_id        = aws_cloudfront_cache_policy.frontend.id
    compress               = true
  }

  # ── Cache Behavior for Static Assets (longer TTL) ──────────────────────────

  ordered_cache_behavior {
    path_pattern     = "/assets/*"
    allowed_methods  = ["GET", "HEAD", "OPTIONS"]
    cached_methods   = ["GET", "HEAD"]
    target_origin_id = "S3-${local.name_prefix}-frontend"

    viewer_protocol_policy = var.frontend_domain != "" ? "https-only" : "allow-all"
    cache_policy_id        = aws_cloudfront_cache_policy.assets.id
    compress               = true
  }

  # ── Error Responses (SPA routing) ─────────────────────────────────────────

  # Route 404s to index.html for client-side routing (only for non-file paths)
  custom_error_response {
    error_code            = 404
    response_code         = 200
    response_page_path    = "/index.html"
    error_caching_min_ttl = 300
  }

  custom_error_response {
    error_code            = 403
    response_code         = 200
    response_page_path    = "/index.html"
    error_caching_min_ttl = 300
  }

  # ── SSL/TLS Certificate (if custom domain provided) ──────────────────────

  viewer_certificate {
    cloudfront_default_certificate = var.frontend_domain == "" ? true : false
    # Use auto-generated certificate from domain.tf if available, otherwise fall back to var.acm_certificate_arn
    acm_certificate_arn = (
      var.enable_domain_management && try(aws_acm_certificate.frontend[0].arn, "") != "" ?
      aws_acm_certificate.frontend[0].arn :
      (var.frontend_domain != "" ? var.acm_certificate_arn : null)
    )
    ssl_support_method       = var.frontend_domain != "" ? "sni-only" : null
    minimum_protocol_version = var.frontend_domain != "" ? "TLSv1.2_2021" : null
  }

  restrictions {
    geo_restriction {
      restriction_type = "none"
    }
  }

  logging_config {
    include_cookies = false
    bucket          = aws_s3_bucket.cloudfront_logs.bucket_regional_domain_name
    prefix          = "cloudfront-logs"
  }

  tags = {
    Name = "${local.name_prefix}-frontend-cdn"
  }

  depends_on = [
    aws_s3_bucket_policy.frontend,
    aws_s3_bucket_acl.cloudfront_logs,
  ]
}

# ── CloudFront Cache Policies ─────────────────────────────────────────────────

resource "aws_cloudfront_cache_policy" "frontend" {
  name        = "${local.name_prefix}-frontend-policy"
  comment     = "Cache policy for frontend HTML files (1 hour)"
  default_ttl = 3600
  max_ttl     = 86400
  min_ttl     = 0

  parameters_in_cache_key_and_forwarded_to_origin {
    query_strings_config {
      query_string_behavior = "none"
    }

    headers_config {
      header_behavior = "none"
    }

    cookies_config {
      cookie_behavior = "none"
    }

    enable_accept_encoding_gzip   = true
    enable_accept_encoding_brotli = true
  }
}

resource "aws_cloudfront_cache_policy" "assets" {
  name        = "${local.name_prefix}-assets-policy"
  comment     = "Cache policy for static assets (1 year)"
  default_ttl = 31536000
  max_ttl     = 31536000
  min_ttl     = 0

  parameters_in_cache_key_and_forwarded_to_origin {
    query_strings_config {
      query_string_behavior = "none"
    }

    headers_config {
      header_behavior = "none"
    }

    cookies_config {
      cookie_behavior = "none"
    }

    enable_accept_encoding_gzip   = true
    enable_accept_encoding_brotli = true
  }
}

# ── CloudFront Logs Bucket ────────────────────────────────────────────────────

resource "aws_s3_bucket" "cloudfront_logs" {
  bucket              = "${local.name_prefix}-cf-logs-${local.suffix}"
  object_lock_enabled = false

  tags = {
    Name = "${local.name_prefix}-cloudfront-logs"
  }
}

resource "aws_s3_bucket_public_access_block" "cloudfront_logs" {
  bucket = aws_s3_bucket.cloudfront_logs.id

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_ownership_controls" "cloudfront_logs" {
  bucket = aws_s3_bucket.cloudfront_logs.id

  rule {
    # CloudFront standard logging needs ACL support on the target bucket.
    object_ownership = "BucketOwnerPreferred"
  }
}

# CloudFront can write logs with this ACL
resource "aws_s3_bucket_acl" "cloudfront_logs" {
  bucket = aws_s3_bucket.cloudfront_logs.id
  acl    = "log-delivery-write"

  depends_on = [aws_s3_bucket_ownership_controls.cloudfront_logs]
}

# ── Route53 Records (optional, requires manual setup) ───────────────────────

# To enable custom domain routing, uncomment and configure:
# 1. Create a Route53 hosted zone for your domain
# 2. Add route53_zone_id variable
# 3. Uncomment the resources below

# resource "aws_route53_record" "frontend" {
#   count   = var.frontend_domain != "" ? 1 : 0
#   zone_id = var.route53_zone_id
#   name    = var.frontend_domain
#   type    = "A"
#
#   alias {
#     name                   = aws_cloudfront_distribution.frontend[0].domain_name
#     zone_id                = aws_cloudfront_distribution.frontend[0].hosted_zone_id
#     evaluate_target_health = false
#   }
# }
#
