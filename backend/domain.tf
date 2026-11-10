# ── ACM Certificate & Route53 Domain Configuration ────────────────────────────

# NOTE: ACM certificates for CloudFront MUST be in us-east-1 region.
# This requires a separate provider configuration.

provider "aws" {
  alias  = "us_east_1"
  region = "us-east-1"

  default_tags {
    tags = {
      Project     = upper(var.project_name)
      Environment = upper(var.environment)
      ManagedBy   = "Terraform"
    }
  }
}

# ── ACM Certificate (us-east-1 for CloudFront) ────────────────────────────────

resource "aws_acm_certificate" "frontend" {
  count             = (var.enable_domain_management && var.frontend_domain != "") ? 1 : 0
  provider          = aws.us_east_1
  domain_name       = var.frontend_domain
  validation_method = "DNS"

  subject_alternative_names = [
    "www.${var.frontend_domain}",
  ]

  lifecycle {
    create_before_destroy = true
  }

  tags = {
    Name = "${local.name_prefix}-cert"
  }
}

# ── Route53 DNS Validation Records ──────────────────────────────────────────

resource "aws_route53_record" "cert_validation" {
  for_each = (var.enable_domain_management && var.frontend_domain != "" && var.route53_zone_id != "") ? {
    for dvo in aws_acm_certificate.frontend[0].domain_validation_options : dvo.domain_name => {
      name   = dvo.resource_record_name
      record = dvo.resource_record_value
      type   = dvo.resource_record_type
    }
  } : {}

  allow_overwrite = true
  name            = each.value.name
  records         = [each.value.record]
  ttl             = 60
  type            = each.value.type
  zone_id         = var.route53_zone_id
}

# ── Wait for Certificate Validation ────────────────────────────────────────

resource "aws_acm_certificate_validation" "frontend" {
  count           = (var.enable_domain_management && var.frontend_domain != "") ? 1 : 0
  provider        = aws.us_east_1
  certificate_arn = aws_acm_certificate.frontend[0].arn

  timeouts {
    create = "5m"
  }

  depends_on = [aws_route53_record.cert_validation]
}

# ── Route53 A Records for CloudFront Distribution ──────────────────────────

# Main domain (e.g., wmfjapan.cloudnadi.com)
resource "aws_route53_record" "frontend_apex" {
  count   = (var.enable_domain_management && var.frontend_domain != "" && var.route53_zone_id != "") ? 1 : 0
  zone_id = var.route53_zone_id
  name    = var.frontend_domain
  type    = "A"

  alias {
    name                   = aws_cloudfront_distribution.frontend[0].domain_name
    zone_id                = aws_cloudfront_distribution.frontend[0].hosted_zone_id
    evaluate_target_health = false
  }

  depends_on = [aws_acm_certificate_validation.frontend]
}

# WWW subdomain (e.g., www.wmfjapan.cloudnadi.com)
resource "aws_route53_record" "frontend_www" {
  count   = (var.enable_domain_management && var.frontend_domain != "" && var.route53_zone_id != "") ? 1 : 0
  zone_id = var.route53_zone_id
  name    = "www.${var.frontend_domain}"
  type    = "A"

  alias {
    name                   = aws_cloudfront_distribution.frontend[0].domain_name
    zone_id                = aws_cloudfront_distribution.frontend[0].hosted_zone_id
    evaluate_target_health = false
  }

  depends_on = [aws_acm_certificate_validation.frontend]
}
