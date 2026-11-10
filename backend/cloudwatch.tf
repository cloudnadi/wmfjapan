resource "aws_cloudwatch_log_group" "api_gateway" {
  name              = "/aws/apigateway/${local.name_prefix}"
  retention_in_days = var.log_retention_days
}

# ── CloudWatch alarms: event platform Lambdas ────────────────────────────────
# No notification action is attached yet; add alarm_actions with an SNS topic to be told by email.

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
