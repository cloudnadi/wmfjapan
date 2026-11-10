aws_region   = "ap-northeast-1"
project_name = "wmfjapan"
environment  = "preview"

# Email — must be verified in AWS SES
ses_from_email                      = "office@wmfjapan.com"
admin_email                         = "prajilal.kp@gmail.com"
admin_email_additional              = ""

# The GitHub Actions OIDC provider and deployment role are account-wide. This is
# the bootstrap state; keep both true here and false in terraform.production.tfvars.
# FIXME: Not implemented
create_github_actions_oidc_provider = true
create_github_actions_deploy_role   = true

# Cognito admin user (created post-deploy via CLI — see README)
cognito_admin_email = "hello@cloudnadi.com"

# Event details (appear in confirmation emails)
event_name  = "Aavani 2026 & 40th Anniversary Celebration (Preview)"
event_date  = "September 12, 2026"
event_venue = "Tokyo, Japan"

# CORS — use preview frontend URL once created
allowed_origins = "*"

# Optional tuning
lambda_timeout        = 30
lambda_memory         = 256
log_retention_days    = 14
dynamodb_billing_mode = "PAY_PER_REQUEST"

# Frontend SPA Configuration
frontend_domain          = "preview-wmfjapan.cloudnadi.com"
route53_zone_id          = "Z03539103R2QBIEBYMN1T"
acm_certificate_arn      = ""
enable_s3_frontend       = true
enable_domain_management = true

# PayPay
paypay_api_key     = "a_pZJxFmbors_RcB7"
paypay_api_secret  = "zSGempiVzsclI6j9uD8n5T4riOIklAUehh+SWGA9DfE="
paypay_merchant_id = "467660228713914368"
paypay_env         = "sandbox"

# Square
square_application_id   = "sandbox-sq0idb-1DX25NN_GUzmR-KEoUI1Hg"
square_access_token     = "EAAAl8EaorOrk2aMEeVIbzutDr8o4vT9DYzmrajTHboasnlhxq6GuZ1ZzMdeXUcX"
square_location_id      = "L3BRXHFMZZ7B4"
square_env              = "sandbox"

# Seat Selection
square_seat_selection_webhook_signature_key  = ""
square_seat_selection_webhook_url            = "" 

# Seat Upgrade
square_seat_upgrade_webhook_signature_key  = "7pyF0I5QdWSxPqtXOE-0_g"
square_seat_upgrade_webhook_url            = "https://3kng1pqoyg.execute-api.ap-northeast-1.amazonaws.com/preview/webhooks/seat-upgrade/square"

# Celebrity Dinner
square_celebrity_dinner_webhook_signature_key   = "mRvEfpgamHhT2NsjZNpsPA"
square_celebrity_dinner_webhook_url             = "https://3kng1pqoyg.execute-api.ap-northeast-1.amazonaws.com/preview/webhooks/celebrity-dinner/square"

# NPO Payment
square_npo_webhook_signature_key    = "-VGGxjN3YTQ1gEe4Fohv0g"
square_npo_webhook_url              = "https://3kng1pqoyg.execute-api.ap-northeast-1.amazonaws.com/preview/webhooks/npo-membership/square"

# Events Platform
square_events_webhook_signature_key = "6KJ1Ck77SiGFSmVvk7EXkA"
square_events_webhook_url           = "https://3kng1pqoyg.execute-api.ap-northeast-1.amazonaws.com/preview/webhooks/event-registrations/square"
