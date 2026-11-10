aws_region   = "ap-northeast-1"
project_name = "wmfjapan"
environment  = "prod"

# Email — must be verified in AWS SES
ses_from_email                      = "office@wmfjapan.com"
admin_email                         = "overall.committee@wmfjapan.com"
admin_email_additional              = ""

# Created by the preview Terraform state; do not create second account-wide resources.
# FIXME: Not implemented
create_github_actions_oidc_provider = false
create_github_actions_deploy_role   = false

# Cognito admin user (created post-deploy via CLI — see README)
cognito_admin_email = "hello@cloudnadi.com"

# Event details (appear in confirmation emails)
event_name  = "Aavani 2026 & 40th Anniversary Celebration"
event_date  = "September 12, 2026"
event_venue = "Tokyo, Japan"

# CORS — set to your exact domain in production
allowed_origins = "*"

# Optional tuning
lambda_timeout        = 30
lambda_memory         = 256
log_retention_days    = 30
dynamodb_billing_mode = "PAY_PER_REQUEST"

# Frontend SPA Configuration
frontend_domain          = "wmfjapan.cloudnadi.com"
route53_zone_id          = "Z03539103R2QBIEBYMN1T"
acm_certificate_arn      = ""
enable_s3_frontend       = true
enable_domain_management = true

# PayPay
paypay_api_key     = "a_pZJxFmbors_RcB7"
paypay_api_secret  = "zSGempiVzsclI6j9uD8n5T4riOIklAUehh+SWGA9DfE="
paypay_merchant_id = "467660228713914368"
paypay_env         = "production"

# Square
square_application_id   = "sq0idp-6sjgYVnd7ecmuqVjhjS49g"
square_access_token     = "EAAAl_jetHPwv58COR5MYO81gH-N7-KGmtIWKMmqd25c5dWRVhE4EbMtTR208WcT"
square_location_id      = "LSBW890BAZXGZ"
square_env              = "production"

# Seat Selection
square_seat_selection_webhook_signature_key  = ""    # set after step 3 below
square_seat_selection_webhook_url            = "" 

# Seat Upgrade
square_seat_upgrade_webhook_signature_key  = "f8YZ_Xq3lIMLCLbaUUwjEw"    # set after step 3 below
square_seat_upgrade_webhook_url            = "https://1xadef8emg.execute-api.ap-northeast-1.amazonaws.com/prod/webhooks/seat-upgrade/square" 

# Celebrity Dinner
square_celebrity_dinner_webhook_signature_key   = "IMlX1-9V2IFNfOrlG94I-w"
square_celebrity_dinner_webhook_url             = "https://1xadef8emg.execute-api.ap-northeast-1.amazonaws.com/prod/webhooks/celebrity-dinner/square"

# NPO Payment
square_npo_webhook_signature_key    = "UxI-YY_ka5InxdqSrr00yQ"
square_npo_webhook_url              = "https://1xadef8emg.execute-api.ap-northeast-1.amazonaws.com/prod/webhooks/npo-membership/square"

# Events Platform
square_events_webhook_signature_key = "TGKM2OQZtFqqAi99NH1shQ"
square_events_webhook_url           = "https://1xadef8emg.execute-api.ap-northeast-1.amazonaws.com/preview/webhooks/event-registrations/square"
