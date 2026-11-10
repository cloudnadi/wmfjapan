resource "aws_cloudwatch_log_group" "api_gateway" {
  name              = "/aws/apigateway/${local.name_prefix}"
  retention_in_days = var.log_retention_days
}

resource "aws_cloudwatch_log_group" "register" {
  name              = "/aws/lambda/${local.name_prefix}-register"
  retention_in_days = var.log_retention_days
}

resource "aws_cloudwatch_log_group" "get_registrations" {
  name              = "/aws/lambda/${local.name_prefix}-get-registrations"
  retention_in_days = var.log_retention_days
}

resource "aws_cloudwatch_log_group" "export_csv" {
  name              = "/aws/lambda/${local.name_prefix}-export-csv"
  retention_in_days = var.log_retention_days
}

resource "aws_cloudwatch_log_group" "confirm_email" {
  name              = "/aws/lambda/${local.name_prefix}-confirm-email"
  retention_in_days = var.log_retention_days
}

resource "aws_cloudwatch_log_group" "manage_partners" {
  name              = "/aws/lambda/${local.name_prefix}-manage-partners"
  retention_in_days = var.log_retention_days
}

resource "aws_cloudwatch_log_group" "auth_otp" {
  name              = "/aws/lambda/${local.name_prefix}-auth-otp"
  retention_in_days = var.log_retention_days
}

resource "aws_cloudwatch_log_group" "membership" {
  name              = "/aws/lambda/${local.name_prefix}-membership"
  retention_in_days = var.log_retention_days
}

resource "aws_cloudwatch_log_group" "npo_membership_webhook" {
  name              = "/aws/lambda/${local.name_prefix}-npo-membership-webhook"
  retention_in_days = var.log_retention_days
}

resource "aws_cloudwatch_log_group" "manage_events" {
  name              = "/aws/lambda/${local.name_prefix}-manage-events"
  retention_in_days = var.log_retention_days
}

resource "aws_cloudwatch_log_group" "manage_registrations" {
  name              = "/aws/lambda/${local.name_prefix}-manage-registrations"
  retention_in_days = var.log_retention_days
}

resource "aws_cloudwatch_log_group" "my_registrations" {
  name              = "/aws/lambda/${local.name_prefix}-my-registrations"
  retention_in_days = var.log_retention_days
}

resource "aws_cloudwatch_log_group" "manage_site_config" {
  name              = "/aws/lambda/${local.name_prefix}-manage-site-config"
  retention_in_days = var.log_retention_days
}

resource "aws_cloudwatch_log_group" "get_site_config" {
  name              = "/aws/lambda/${local.name_prefix}-get-site-config"
  retention_in_days = var.log_retention_days
}

resource "aws_cloudwatch_log_group" "get_event_availability" {
  name              = "/aws/lambda/${local.name_prefix}-get-event-availability"
  retention_in_days = var.log_retention_days
}

resource "aws_cloudwatch_log_group" "visit_logger" {
  name              = "/aws/lambda/${local.name_prefix}-visit-logger"
  retention_in_days = var.log_retention_days
}

resource "aws_cloudwatch_log_group" "get_visit_summary" {
  name              = "/aws/lambda/${local.name_prefix}-get-visit-summary"
  retention_in_days = var.log_retention_days
}

resource "aws_cloudwatch_log_group" "manage_agms" {
  name              = "/aws/lambda/${local.name_prefix}-manage-agms"
  retention_in_days = var.log_retention_days
}

resource "aws_cloudwatch_log_group" "member_announcer" {
  name              = "/aws/lambda/${local.name_prefix}-member-announcer"
  retention_in_days = var.log_retention_days
}

resource "aws_cloudwatch_log_group" "manage_members" {
  name              = "/aws/lambda/${local.name_prefix}-manage-members"
  retention_in_days = var.log_retention_days
}

resource "aws_cloudwatch_log_group" "manage_entry_slots" {
  name              = "/aws/lambda/${local.name_prefix}-manage-entry-slots"
  retention_in_days = var.log_retention_days
}

resource "aws_cloudwatch_log_group" "release_buffer" {
  name              = "/aws/lambda/${local.name_prefix}-release-buffer"
  retention_in_days = var.log_retention_days
}

resource "aws_cloudwatch_log_group" "get_available_slots" {
  name              = "/aws/lambda/${local.name_prefix}-get-available-slots"
  retention_in_days = var.log_retention_days
}

# get_available_seats/manage_seats log groups are provisioned by the
# seat-selection plugin module — see plugins/seat-selection/terraform.

resource "aws_cloudwatch_log_group" "manage_reconciliation" {
  name              = "/aws/lambda/${local.name_prefix}-manage-reconciliation"
  retention_in_days = var.log_retention_days
}

resource "aws_cloudwatch_log_group" "manage_celebrity_dinner" {
  name              = "/aws/lambda/${local.name_prefix}-manage-celebrity-dinner"
  retention_in_days = var.log_retention_days
}

resource "aws_cloudwatch_log_group" "celebrity_dinner_webhook" {
  name              = "/aws/lambda/${local.name_prefix}-celebrity-dinner-webhook"
  retention_in_days = var.log_retention_days
}

# ── CloudWatch Alarm: registration errors ────────────────────────────────────

resource "aws_cloudwatch_metric_alarm" "lambda_errors" {
  alarm_name          = "${local.name_prefix}-lambda-errors"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 1
  metric_name         = "Errors"
  namespace           = "AWS/Lambda"
  period              = 60
  statistic           = "Sum"
  threshold           = 5
  alarm_description   = "Alarm if Lambda function has more than 5 errors in a minute"

  dimensions = {
    FunctionName = aws_lambda_function.register.function_name
  }

  treat_missing_data = "notBreaching"
}

# ── CloudWatch alarms: event platform Lambdas ────────────────────────────────
# Same pattern as the registration alarm above (no notification action is attached yet;
# add alarm_actions with an SNS topic to be told by email).

resource "aws_cloudwatch_metric_alarm" "event_platform_errors" {
  for_each = {
    public  = aws_lambda_function.event_platform_public.function_name
    webhook = aws_lambda_function.event_platform_webhook.function_name
    expiry  = aws_lambda_function.event_platform_expiry.function_name
    admin   = aws_lambda_function.manage_event_platform.function_name
  }

  alarm_name          = "${local.name_prefix}-event-platform-${each.key}-errors"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 1
  metric_name         = "Errors"
  namespace           = "AWS/Lambda"
  period              = 300
  statistic           = "Sum"
  threshold           = 3
  alarm_description   = "Event platform (${each.key}) Lambda had more than 3 errors in 5 minutes"

  dimensions = {
    FunctionName = each.value
  }

  treat_missing_data = "notBreaching"
}

# The hold-expiry job runs every 5 minutes; no invocations for 30 minutes means the schedule is broken.
resource "aws_cloudwatch_metric_alarm" "event_platform_expiry_stalled" {
  alarm_name          = "${local.name_prefix}-event-platform-expiry-stalled"
  comparison_operator = "LessThanThreshold"
  evaluation_periods  = 1
  metric_name         = "Invocations"
  namespace           = "AWS/Lambda"
  period              = 1800
  statistic           = "Sum"
  threshold           = 1
  alarm_description   = "Unpaid event registrations are not being released: the expiry job has not run in 30 minutes"

  dimensions = {
    FunctionName = aws_lambda_function.event_platform_expiry.function_name
  }

  treat_missing_data = "breaching"
}
