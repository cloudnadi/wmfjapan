# A dedicated role for this module's own Lambdas (get_available_seats,
# manage_seats, seat_purchase_webhook), scoped to only what they need —
# logging plus its own four tables — so mounting this module never requires
# threading in a host app's existing shared Lambda role.

resource "aws_iam_role" "lambda_exec" {
  name = "${var.name_prefix}-seat-selection-lambda-role"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Action    = "sts:AssumeRole"
      Effect    = "Allow"
      Principal = { Service = "lambda.amazonaws.com" }
    }]
  })

  tags = var.tags
}

resource "aws_iam_role_policy" "lambda_policy" {
  name = "${var.name_prefix}-seat-selection-lambda-policy"
  role = aws_iam_role.lambda_exec.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = concat([
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
        Effect = "Allow"
        Action = [
          "dynamodb:GetItem",
          "dynamodb:Query",
          "dynamodb:PutItem",
          "dynamodb:DeleteItem",
          "dynamodb:TransactWriteItems"
        ]
        Resource = [
          aws_dynamodb_table.seats.arn,
          "${aws_dynamodb_table.seats.arn}/index/*"
        ]
      },
      {
        Effect   = "Allow"
        Action   = ["dynamodb:GetItem", "dynamodb:PutItem"]
        Resource = [aws_dynamodb_table.category_overrides.arn]
      },
      {
        Effect = "Allow"
        Action = [
          "dynamodb:GetItem",
          "dynamodb:Query",
          "dynamodb:PutItem",
          "dynamodb:DeleteItem"
        ]
        Resource = [aws_dynamodb_table.zone_pricing.arn]
      },
      {
        Effect = "Allow"
        Action = [
          "dynamodb:GetItem",
          "dynamodb:Query",
          "dynamodb:PutItem",
          "dynamodb:UpdateItem"
        ]
        Resource = [
          aws_dynamodb_table.seat_purchases.arn,
          "${aws_dynamodb_table.seat_purchases.arn}/index/*"
        ]
      },
      # manage_seats (admin mark-paid/refund) and seat_purchase_webhook
      # (PayPay/Square) both send the registrant paid/cancelled/refund
      # emails and the admin notification via seat_purchase_emailer —
      # same SES grant as the host's own shared Lambda role.
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
      ],
      # Optional, read-only cross-plugin grants for the guest check-in
      # ticket PDF's Zone Upgrade / Celebrity Dinner sections — see the
      # matching variables' descriptions in variables.tf for why these are
      # each their own conditional statement rather than always-present.
      var.seat_upgrades_table_arn != "" ? [{
        Effect = "Allow"
        Action = ["dynamodb:GetItem", "dynamodb:Query"]
        Resource = [
          var.seat_upgrades_table_arn,
          "${var.seat_upgrades_table_arn}/index/*"
        ]
      }] : [],
      var.celebrity_dinner_registrations_table_arn != "" ? [{
        Effect = "Allow"
        Action = ["dynamodb:GetItem", "dynamodb:Query"]
        Resource = [
          var.celebrity_dinner_registrations_table_arn,
          "${var.celebrity_dinner_registrations_table_arn}/index/*"
        ]
      }] : []
    )
  })
}
