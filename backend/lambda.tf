# ── IAM Role shared by all Lambda functions ──────────────────────────────────

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
      # CloudWatch Logs
      {
        Effect = "Allow"
        Action = [
          "logs:CreateLogGroup",
          "logs:CreateLogStream",
          "logs:PutLogEvents"
        ]
        Resource = "arn:aws:logs:${var.aws_region}:*:*"
      },
      # DynamoDB — scoped to the registrations table
      {
        Effect = "Allow"
        Action = [
          "dynamodb:PutItem",
          "dynamodb:GetItem",
          "dynamodb:UpdateItem",
          "dynamodb:DeleteItem",
          "dynamodb:Scan",
          "dynamodb:Query"
        ]
        Resource = [
          aws_dynamodb_table.registrations.arn,
          "${aws_dynamodb_table.registrations.arn}/index/*"
        ]
      },
      # DynamoDB — scoped to the visit logs table.
      {
        Effect = "Allow"
        Action = [
          "dynamodb:PutItem",
          "dynamodb:Query"
        ]
        Resource = [aws_dynamodb_table.visits.arn]
      },
      # DynamoDB — scoped to the events table.
      # register/get_registrations read; manage_events writes admin CRUD.
      {
        Effect = "Allow"
        Action = [
          "dynamodb:GetItem",
          "dynamodb:Query",
          "dynamodb:Scan",
          "dynamodb:PutItem",
          "dynamodb:UpdateItem",
          "dynamodb:DeleteItem"
        ]
        Resource = [
          aws_dynamodb_table.events.arn,
          "${aws_dynamodb_table.events.arn}/index/*"
        ]
      },
      # DynamoDB — scoped to the partners table
      {
        Effect = "Allow"
        Action = [
          "dynamodb:PutItem",
          "dynamodb:GetItem",
          "dynamodb:UpdateItem",
          "dynamodb:DeleteItem",
          "dynamodb:Scan",
          "dynamodb:Query"
        ]
        Resource = [
          aws_dynamodb_table.partners.arn,
          "${aws_dynamodb_table.partners.arn}/index/*"
        ]
      },
      # DynamoDB — scoped to the login OTP table
      {
        Effect = "Allow"
        Action = [
          "dynamodb:PutItem",
          "dynamodb:GetItem",
          "dynamodb:UpdateItem",
          "dynamodb:DeleteItem"
        ]
        Resource = [aws_dynamodb_table.login_otp.arn]
      },
      # DynamoDB — scoped to the members master table.
      # auth_otp reads it to gate login; register/manage_registrations read the
      # memberid and flip npoMembershipActive on join/renew;
      # manage_registrations scans it for the admin Members list.
      {
        Effect = "Allow"
        Action = [
          "dynamodb:GetItem",
          "dynamodb:BatchGetItem",
          "dynamodb:Query",
          "dynamodb:Scan",
          "dynamodb:PutItem",
          "dynamodb:UpdateItem",
          "dynamodb:DeleteItem"
        ]
        Resource = [aws_dynamodb_table.members.arn]
      },
      # DynamoDB — scoped to the NPO membership table.
      # membership Lambda reads status; register Lambda writes joins/renewals;
      # manage_registrations scans it for the admin Members list.
      {
        Effect = "Allow"
        Action = [
          "dynamodb:GetItem",
          "dynamodb:Query",
          "dynamodb:Scan",
          "dynamodb:PutItem",
          "dynamodb:UpdateItem",
          "dynamodb:DeleteItem"
        ]
        Resource = [
          aws_dynamodb_table.npo_membership.arn,
          "${aws_dynamodb_table.npo_membership.arn}/index/*"
        ]
      },
      # DynamoDB — scoped to the AGM registrations table.
      # membership Lambda saves/reads registrations; manage_agms lists them for admins.
      {
        Effect = "Allow"
        Action = [
          "dynamodb:GetItem",
          "dynamodb:Query",
          "dynamodb:PutItem",
          "dynamodb:DeleteItem"
        ]
        Resource = [aws_dynamodb_table.agm_registrations.arn]
      },
      # DynamoDB — scoped to the member announcements (campaign records) table.
      {
        Effect = "Allow"
        Action = [
          "dynamodb:GetItem",
          "dynamodb:Scan",
          "dynamodb:PutItem",
          "dynamodb:UpdateItem"
        ]
        Resource = [aws_dynamodb_table.member_announcements.arn]
      },
      # DynamoDB — scoped to the AGMs table (membership reads, manage_agms writes).
      {
        Effect = "Allow"
        Action = [
          "dynamodb:GetItem",
          "dynamodb:Scan",
          "dynamodb:PutItem"
        ]
        Resource = [aws_dynamodb_table.agms.arn]
      },
      # DynamoDB — scoped to the entry slots table.
      # register Lambda reads available slots; manage_entry_slots writes admin CRUD.
      {
        Effect = "Allow"
        Action = [
          "dynamodb:GetItem",
          "dynamodb:Query",
          "dynamodb:Scan",
          "dynamodb:PutItem",
          "dynamodb:UpdateItem",
          "dynamodb:DeleteItem"
        ]
        Resource = [
          aws_dynamodb_table.entry_slots.arn,
          "${aws_dynamodb_table.entry_slots.arn}/index/*"
        ]
      },
      # DynamoDB — scoped to the slot reservations table.
      # register Lambda reserves seats; manage_registrations updates reservations;
      # get_available_slots queries capacity.
      {
        Effect = "Allow"
        Action = [
          "dynamodb:GetItem",
          "dynamodb:Query",
          "dynamodb:Scan",
          "dynamodb:PutItem",
          "dynamodb:UpdateItem",
          "dynamodb:DeleteItem"
        ]
        Resource = [
          aws_dynamodb_table.slot_reservations.arn,
          "${aws_dynamodb_table.slot_reservations.arn}/index/*"
        ]
      },
      # DynamoDB — allocation authorization and append-only check-in history.
      {
        Effect = "Allow"
        Action = [
          "dynamodb:GetItem",
          "dynamodb:Query",
          "dynamodb:PutItem",
          "dynamodb:UpdateItem",
          "dynamodb:TransactWriteItems"
        ]
        Resource = [
          aws_dynamodb_table.entry_allocations.arn,
          "${aws_dynamodb_table.entry_allocations.arn}/index/*",
          aws_dynamodb_table.entry_check_ins.arn
        ]
      },
      # DynamoDB — seat-selection plugin's seats table. my_registrations
      # claims/releases a registrant's own seats; manage_registrations
      # releases seats on delete. (The plugin's own Lambdas — manage_seats/
      # get_available_seats — use their own dedicated IAM role instead; see
      # plugins/seat-selection/terraform.)
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
          module.seat_selection.seats_table_arn,
          "${module.seat_selection.seats_table_arn}/index/*"
        ]
      },
      # DynamoDB — seat-selection plugin's purchases table. my_registrations
      # owns purchase creation/checkout/cancel; manage_registrations
      # cancels active purchases when a registration is deleted. (The
      # module's own seat_purchase_webhook Lambda uses its dedicated IAM
      # role instead, same as above.)
      {
        Effect = "Allow"
        Action = [
          "dynamodb:GetItem",
          "dynamodb:Query",
          "dynamodb:PutItem",
          "dynamodb:UpdateItem"
        ]
        Resource = [
          module.seat_selection.seat_purchases_table_arn,
          "${module.seat_selection.seat_purchases_table_arn}/index/*"
        ]
      },
      # DynamoDB — seat-selection plugin's zone-pricing table, read-only.
      # my_registrations reads it to price a purchase at creation/checkout;
      # manage_registrations reads it to preview pricing in the seat
      # selection announcement email.
      {
        Effect = "Allow"
        Action = [
          "dynamodb:GetItem",
          "dynamodb:Query"
        ]
        Resource = [
          module.seat_selection.zone_pricing_table_arn,
          "${module.seat_selection.zone_pricing_table_arn}/index/*"
        ]
      },
      # Seat-upgrade tables: IAM lives on the plugin's own
      # aws_iam_role_policy.seat_upgrade (plugin.tf), attached to this same
      # role as a separate inline policy rather than added here.
      # DynamoDB — scoped to the payment transactions table (reconciliation uploads).
      {
        Effect = "Allow"
        Action = [
          "dynamodb:GetItem",
          "dynamodb:Query",
          "dynamodb:Scan",
          "dynamodb:PutItem",
          "dynamodb:DeleteItem",
          "dynamodb:BatchWriteItem"
        ]
        Resource = [
          aws_dynamodb_table.payment_transactions.arn,
          "${aws_dynamodb_table.payment_transactions.arn}/index/*"
        ]
      },
      # DynamoDB — scoped to the reconciliation runs table.
      {
        Effect = "Allow"
        Action = [
          "dynamodb:GetItem",
          "dynamodb:Query",
          "dynamodb:PutItem"
        ]
        Resource = [aws_dynamodb_table.reconciliation_runs.arn]
      },
      # DynamoDB — scoped to the Celebrity Dinner config table.
      # manage_celebrity_dinner writes admin config; my_registrations reads it.
      {
        Effect = "Allow"
        Action = [
          "dynamodb:GetItem",
          "dynamodb:PutItem"
        ]
        Resource = [aws_dynamodb_table.celebrity_dinner_config.arn]
      },
      # DynamoDB — scoped to the Celebrity Dinner registrations table.
      # my_registrations writes registrant sign-ups; manage_celebrity_dinner
      # lists them for admin visibility. Never touches the registrations table.
      {
        Effect = "Allow"
        Action = [
          "dynamodb:GetItem",
          "dynamodb:Query",
          "dynamodb:PutItem",
          "dynamodb:UpdateItem"
        ]
        Resource = [
          aws_dynamodb_table.celebrity_dinner_registrations.arn,
          "${aws_dynamodb_table.celebrity_dinner_registrations.arn}/index/*"
        ]
      },
      # SES — send email
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

# ── Signing secret for OTP access tokens ─────────────────────────────────────

resource "random_password" "otp_token_secret" {
  length  = 48
  special = false
}

# ── Lambda: ZIP packaging ────────────────────────────────────────────────────

data "archive_file" "register" {
  type        = "zip"
  output_path = "${path.module}/../lambdas/dist/register.zip"

  source {
    content  = file("${path.module}/../lambdas/register/handler.py")
    filename = "handler.py"
  }

  source {
    content  = file("${path.module}/../lambdas/register/npo_membership.py")
    filename = "npo_membership.py"
  }

  source {
    content  = file("${path.module}/../lambdas/register/onam_pricing.py")
    filename = "onam_pricing.py"
  }

  source {
    content  = file("${path.module}/../lambdas/entry_slots_logic.py")
    filename = "entry_slots_logic.py"
  }

  source {
    content  = file("${path.module}/../lambdas/entry_allocations.py")
    filename = "entry_allocations.py"
  }

  source {
    content  = file("${path.module}/../lambdas/qr_email.py")
    filename = "qr_email.py"
  }

  source {
    content  = file("${path.module}/../lambdas/checkin_pdf.py")
    filename = "checkin_pdf.py"
  }
}

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

data "archive_file" "get_registrations" {
  type        = "zip"
  source_dir  = "${path.module}/../lambdas/get_registrations"
  output_path = "${path.module}/../lambdas/dist/get_registrations.zip"
}

data "archive_file" "export_csv" {
  type        = "zip"
  source_dir  = "${path.module}/../lambdas/export_csv"
  output_path = "${path.module}/../lambdas/dist/export_csv.zip"
}

data "archive_file" "confirm_email" {
  type        = "zip"
  source_dir  = "${path.module}/../lambdas/confirm_email"
  output_path = "${path.module}/../lambdas/dist/confirm_email.zip"
}

data "archive_file" "manage_partners" {
  type        = "zip"
  source_dir  = "${path.module}/../lambdas/manage_partners"
  output_path = "${path.module}/../lambdas/dist/manage_partners.zip"
}

data "archive_file" "auth_otp" {
  type        = "zip"
  source_dir  = "${path.module}/../lambdas/auth_otp"
  output_path = "${path.module}/../lambdas/dist/auth_otp.zip"
}

data "archive_file" "membership" {
  type        = "zip"
  output_path = "${path.module}/../lambdas/dist/membership.zip"

  source {
    content  = file("${path.module}/../lambdas/membership/handler.py")
    filename = "handler.py"
  }

  source {
    content  = file("${path.module}/../lambdas/membership/npo_membership.py")
    filename = "npo_membership.py"
  }

  source {
    content  = file("${path.module}/../lambdas/npo_payment_logic.py")
    filename = "npo_payment_logic.py"
  }

  # Branded email template (payment-failed email), shared with the announcements.
  source {
    content  = file("${path.module}/../lambdas/member_announcements/member_emailer.py")
    filename = "member_emailer.py"
  }

  source {
    content  = file("${path.module}/../lambdas/agm_logic.py")
    filename = "agm_logic.py"
  }

  source {
    content  = file("${path.module}/../plugins/seat-upgrade/backend/lambdas/square.py")
    filename = "square.py"
  }
}

data "archive_file" "npo_membership_webhook" {
  type        = "zip"
  output_path = "${path.module}/../lambdas/dist/npo_membership_webhook.zip"

  source {
    content  = file("${path.module}/../lambdas/npo_membership_webhook/handler.py")
    filename = "handler.py"
  }

  source {
    content  = file("${path.module}/../lambdas/membership/npo_membership.py")
    filename = "npo_membership.py"
  }

  source {
    content  = file("${path.module}/../lambdas/npo_payment_logic.py")
    filename = "npo_payment_logic.py"
  }

  # Branded email template (payment-failed email), shared with the announcements.
  source {
    content  = file("${path.module}/../lambdas/member_announcements/member_emailer.py")
    filename = "member_emailer.py"
  }

  source {
    content  = file("${path.module}/../plugins/seat-upgrade/backend/lambdas/square.py")
    filename = "square.py"
  }
}

data "archive_file" "manage_events" {
  type        = "zip"
  source_dir  = "${path.module}/../lambdas/manage_events"
  output_path = "${path.module}/../lambdas/dist/manage_events.zip"
}

data "archive_file" "manage_registrations" {
  type        = "zip"
  output_path = "${path.module}/../lambdas/dist/manage_registrations.zip"

  source {
    content  = file("${path.module}/../lambdas/manage_registrations/handler.py")
    filename = "handler.py"
  }

  source {
    content  = file("${path.module}/../lambdas/manage_registrations/emailer.py")
    filename = "emailer.py"
  }

  source {
    content  = file("${path.module}/../lambdas/manage_registrations/npo_membership.py")
    filename = "npo_membership.py"
  }

  source {
    content  = file("${path.module}/../lambdas/manage_registrations/onam_pricing.py")
    filename = "onam_pricing.py"
  }

  source {
    content  = file("${path.module}/../lambdas/entry_slots_logic.py")
    filename = "entry_slots_logic.py"
  }
  source {
    content  = file("${path.module}/../lambdas/entry_allocations.py")
    filename = "entry_allocations.py"
  }

  source {
    content  = file("${path.module}/../plugins/seat-selection/backend/seat_selection_logic.py")
    filename = "seat_selection_logic.py"
  }

  source {
    content  = file("${path.module}/../plugins/seat-selection/backend/seat_purchase_logic.py")
    filename = "seat_purchase_logic.py"
  }

  source {
    content  = file("${path.module}/../plugins/seat-selection/backend/seat_purchase_emailer.py")
    filename = "seat_purchase_emailer.py"
  }

  source {
    content  = file("${path.module}/../plugins/seat-selection/backend/seat_purchase_qr.py")
    filename = "seat_purchase_qr.py"
  }

  source {
    content  = file("${path.module}/../lambdas/qr_email.py")
    filename = "qr_email.py"
  }

  source {
    content  = file("${path.module}/../lambdas/checkin_pdf.py")
    filename = "checkin_pdf.py"
  }

  source {
    content  = file("${path.module}/../lambdas/celebrity_dinner_logic.py")
    filename = "celebrity_dinner_logic.py"
  }

  source {
    content  = file("${path.module}/../lambdas/celebrity_dinner_emailer.py")
    filename = "celebrity_dinner_emailer.py"
  }
}

data "archive_file" "my_registrations" {
  type        = "zip"
  output_path = "${path.module}/../lambdas/dist/my_registrations.zip"

  source {
    content  = file("${path.module}/../lambdas/my_registrations/handler.py")
    filename = "handler.py"
  }

  source {
    content  = file("${path.module}/../lambdas/my_registrations/emailer.py")
    filename = "emailer.py"
  }

  source {
    content  = file("${path.module}/../lambdas/my_registrations/npo_membership.py")
    filename = "npo_membership.py"
  }

  source {
    content  = file("${path.module}/../lambdas/my_registrations/onam_pricing.py")
    filename = "onam_pricing.py"
  }

  source {
    content  = file("${path.module}/../lambdas/entry_slots_logic.py")
    filename = "entry_slots_logic.py"
  }

  source {
    content  = file("${path.module}/../lambdas/entry_allocations.py")
    filename = "entry_allocations.py"
  }

  source {
    content  = file("${path.module}/../plugins/seat-selection/backend/seat_selection_logic.py")
    filename = "seat_selection_logic.py"
  }

  source {
    content  = file("${path.module}/../plugins/seat-selection/backend/seat_purchase_logic.py")
    filename = "seat_purchase_logic.py"
  }

  source {
    content  = file("${path.module}/../plugins/seat-selection/backend/seat_purchase_emailer.py")
    filename = "seat_purchase_emailer.py"
  }

  source {
    content  = file("${path.module}/../plugins/seat-selection/backend/seat_purchase_qr.py")
    filename = "seat_purchase_qr.py"
  }

  source {
    content  = file("${path.module}/../lambdas/celebrity_dinner_logic.py")
    filename = "celebrity_dinner_logic.py"
  }

  source {
    content  = file("${path.module}/../lambdas/celebrity_dinner_emailer.py")
    filename = "celebrity_dinner_emailer.py"
  }

  source {
    content  = file("${path.module}/../lambdas/qr_email.py")
    filename = "qr_email.py"
  }

  source {
    content  = file("${path.module}/../lambdas/checkin_pdf.py")
    filename = "checkin_pdf.py"
  }

  # Both the seat-selection purchase flow and the standalone Celebrity
  # Dinner flow need a paypay.py/square.py in this zip (PAYMENT_ADAPTERS
  # and DINNER_PAYMENT_ADAPTERS both just `import paypay`/`import square`
  # once — one shared module, not two), so there's exactly one `source`
  # for each filename here rather than seat-selection's own copy
  # (plugins/seat-selection/backend/{paypay,square}.py) duplicated
  # alongside it — that would silently collide on the same zip entry name.
  # Referenced directly from the seat-upgrade plugin's copy rather than a
  # third copy of the same files — see its paypay.py's own module
  # docstring for the trust model each implements.
  source {
    content  = file("${path.module}/../plugins/seat-upgrade/backend/lambdas/paypay.py")
    filename = "paypay.py"
  }

  source {
    content  = file("${path.module}/../plugins/seat-upgrade/backend/lambdas/square.py")
    filename = "square.py"
  }
}

data "archive_file" "manage_celebrity_dinner" {
  type        = "zip"
  output_path = "${path.module}/../lambdas/dist/manage_celebrity_dinner.zip"

  source {
    content  = file("${path.module}/../lambdas/manage_celebrity_dinner/handler.py")
    filename = "handler.py"
  }

  source {
    content  = file("${path.module}/../lambdas/celebrity_dinner_logic.py")
    filename = "celebrity_dinner_logic.py"
  }

  source {
    content  = file("${path.module}/../lambdas/celebrity_dinner_emailer.py")
    filename = "celebrity_dinner_emailer.py"
  }
}

data "archive_file" "celebrity_dinner_webhook" {
  type        = "zip"
  output_path = "${path.module}/../lambdas/dist/celebrity_dinner_webhook.zip"

  source {
    content  = file("${path.module}/../lambdas/celebrity_dinner_webhook/handler.py")
    filename = "handler.py"
  }

  source {
    content  = file("${path.module}/../lambdas/celebrity_dinner_logic.py")
    filename = "celebrity_dinner_logic.py"
  }

  source {
    content  = file("${path.module}/../lambdas/celebrity_dinner_emailer.py")
    filename = "celebrity_dinner_emailer.py"
  }

  source {
    content  = file("${path.module}/../plugins/seat-upgrade/backend/lambdas/paypay.py")
    filename = "paypay.py"
  }

  source {
    content  = file("${path.module}/../plugins/seat-upgrade/backend/lambdas/square.py")
    filename = "square.py"
  }
}

data "archive_file" "manage_site_config" {
  type        = "zip"
  source_dir  = "${path.module}/../lambdas/manage_site_config"
  output_path = "${path.module}/../lambdas/dist/manage_site_config.zip"
}

data "archive_file" "get_site_config" {
  type        = "zip"
  source_dir  = "${path.module}/../lambdas/get_site_config"
  output_path = "${path.module}/../lambdas/dist/get_site_config.zip"
}

data "archive_file" "visit_logger" {
  type        = "zip"
  source_dir  = "${path.module}/../lambdas/visit_logger"
  output_path = "${path.module}/../lambdas/dist/visit_logger.zip"
}

data "archive_file" "get_visit_summary" {
  type        = "zip"
  source_dir  = "${path.module}/../lambdas/get_visit_summary"
  output_path = "${path.module}/../lambdas/dist/get_visit_summary.zip"
}

data "archive_file" "manage_agms" {
  type        = "zip"
  output_path = "${path.module}/../lambdas/dist/manage_agms.zip"

  source {
    content  = file("${path.module}/../lambdas/manage_agms/handler.py")
    filename = "handler.py"
  }

  source {
    content  = file("${path.module}/../lambdas/manage_agms/npo_membership.py")
    filename = "npo_membership.py"
  }

  # AGM eligibility rules (NPO members only) shared with the membership Lambda.
  source {
    content  = file("${path.module}/../lambdas/agm_logic.py")
    filename = "agm_logic.py"
  }
}

data "archive_file" "manage_members" {
  type        = "zip"
  output_path = "${path.module}/../lambdas/dist/manage_members.zip"

  source {
    content  = file("${path.module}/../lambdas/manage_members/handler.py")
    filename = "handler.py"
  }

  source {
    content  = file("${path.module}/../lambdas/manage_members/npo_membership.py")
    filename = "npo_membership.py"
  }

  # Audience + campaign logic shared with the member_announcer worker.
  source {
    content  = file("${path.module}/../lambdas/member_announcements/announcement_logic.py")
    filename = "announcement_logic.py"
  }

  # "Mark paid" sends the same branded payment-received email as a Square payment.
  source {
    content  = file("${path.module}/../lambdas/npo_payment_logic.py")
    filename = "npo_payment_logic.py"
  }

  source {
    content  = file("${path.module}/../lambdas/member_announcements/member_emailer.py")
    filename = "member_emailer.py"
  }

  # Cancel & refund: refunds card payments through Square.
  source {
    content  = file("${path.module}/../plugins/seat-upgrade/backend/lambdas/square.py")
    filename = "square.py"
  }
}

data "archive_file" "member_announcer" {
  type        = "zip"
  source_dir  = "${path.module}/../lambdas/member_announcements"
  output_path = "${path.module}/../lambdas/dist/member_announcer.zip"
}

data "archive_file" "manage_entry_slots" {
  type        = "zip"
  output_path = "${path.module}/../lambdas/dist/manage_entry_slots.zip"

  source {
    content  = file("${path.module}/../lambdas/manage_entry_slots/handler.py")
    filename = "handler.py"
  }

  source {
    content  = file("${path.module}/../lambdas/entry_slots_logic.py")
    filename = "entry_slots_logic.py"
  }
}

data "archive_file" "release_buffer" {
  type        = "zip"
  output_path = "${path.module}/../lambdas/dist/release_buffer.zip"

  source {
    content  = file("${path.module}/../lambdas/release_buffer/handler.py")
    filename = "handler.py"
  }

  source {
    content  = file("${path.module}/../lambdas/entry_slots_logic.py")
    filename = "entry_slots_logic.py"
  }
}

data "archive_file" "manage_reconciliation" {
  type        = "zip"
  source_dir  = "${path.module}/../lambdas/manage_reconciliation"
  output_path = "${path.module}/../lambdas/dist/manage_reconciliation.zip"
}

data "archive_file" "get_available_slots" {
  type        = "zip"
  output_path = "${path.module}/../lambdas/dist/get_available_slots.zip"

  source {
    content  = file("${path.module}/../lambdas/get_available_slots/handler.py")
    filename = "handler.py"
  }

  source {
    content  = file("${path.module}/../lambdas/entry_slots_logic.py")
    filename = "entry_slots_logic.py"
  }
}

data "archive_file" "get_event_availability" {
  type        = "zip"
  output_path = "${path.module}/../lambdas/dist/get_event_availability.zip"

  source {
    content  = file("${path.module}/../lambdas/get_event_availability/handler.py")
    filename = "handler.py"
  }

  source {
    content  = file("${path.module}/../lambdas/celebrity_dinner_logic.py")
    filename = "celebrity_dinner_logic.py"
  }

  source {
    content  = file("${path.module}/../plugins/seat-selection/backend/seat_selection_logic.py")
    filename = "seat_selection_logic.py"
  }
}

# get_available_seats/manage_seats now live in the seat-selection plugin
# module (see module "seat_selection" in main.tf) — their archive_file/
# aws_lambda_function resources moved to plugins/seat-selection/terraform.

# ── Common environment variables ─────────────────────────────────────────────

locals {
  lambda_env = {
    DYNAMODB_TABLE                       = aws_dynamodb_table.registrations.name
    EVENTS_TABLE                         = aws_dynamodb_table.events.name
    ONAM_CAPACITY                        = "350"
    PARTNERS_TABLE                       = aws_dynamodb_table.partners.name
    VISITS_TABLE                         = aws_dynamodb_table.visits.name
    MEMBERSHIP_TABLE                     = aws_dynamodb_table.npo_membership.name
    MEMBERS_TABLE                        = aws_dynamodb_table.members.name
    AGM_TABLE                            = aws_dynamodb_table.agm_registrations.name
    AGMS_TABLE                           = aws_dynamodb_table.agms.name
    MEMBER_ANNOUNCEMENTS_TABLE           = aws_dynamodb_table.member_announcements.name
    SITE_CONFIG_TABLE                    = aws_dynamodb_table.site_config.name
    ENTRY_SLOTS_TABLE                    = aws_dynamodb_table.entry_slots.name
    SLOT_RESERVATIONS_TABLE              = aws_dynamodb_table.slot_reservations.name
    ENTRY_ALLOCATIONS_TABLE              = aws_dynamodb_table.entry_allocations.name
    ENTRY_CHECK_INS_TABLE                = aws_dynamodb_table.entry_check_ins.name
    SEATS_TABLE                          = module.seat_selection.seats_table_name
    ZONE_PRICING_TABLE                   = module.seat_selection.zone_pricing_table_name
    PAYMENT_TRANSACTIONS_TABLE           = aws_dynamodb_table.payment_transactions.name
    RECONCILIATION_RUNS_TABLE            = aws_dynamodb_table.reconciliation_runs.name
    CELEBRITY_DINNER_CONFIG_TABLE        = aws_dynamodb_table.celebrity_dinner_config.name
    CELEBRITY_DINNER_REGISTRATIONS_TABLE = aws_dynamodb_table.celebrity_dinner_registrations.name
    FRONTEND_URL                         = var.frontend_domain != "" ? "https://${var.frontend_domain}" : try("https://${aws_cloudfront_distribution.frontend[0].domain_name}", "")
    SES_FROM_EMAIL                       = var.ses_from_email
    ADMIN_EMAIL                          = var.admin_email
    ADMIN_EMAIL_CC                       = var.admin_email_additional
    REGION                               = var.aws_region
    ENVIRONMENT                          = var.environment
    RENEWAL_MEMBERSHIP_YEAR              = "2026-27"
  }
}

# ── Lambda: auth_otp (public — email OTP login gate) ─────────────────────────

resource "aws_lambda_function" "auth_otp" {
  function_name    = "${local.name_prefix}-auth-otp"
  role             = aws_iam_role.lambda_exec.arn
  handler          = "handler.lambda_handler"
  runtime          = "python3.12"
  filename         = data.archive_file.auth_otp.output_path
  source_code_hash = data.archive_file.auth_otp.output_base64sha256
  timeout          = var.lambda_timeout
  memory_size      = var.lambda_memory

  environment {
    variables = merge(local.lambda_env, {
      OTP_TABLE           = aws_dynamodb_table.login_otp.name
      OTP_TOKEN_SECRET    = random_password.otp_token_secret.result
      MEMBERS_TABLE       = aws_dynamodb_table.members.name
      MEMBER_REGISTER_URL = var.member_register_url
    })
  }

  depends_on = [aws_cloudwatch_log_group.auth_otp]
}

# ── Lambda: membership (public — reg-token gated NPO status lookup) ──────────

resource "aws_lambda_function" "membership" {
  function_name    = "${local.name_prefix}-membership"
  role             = aws_iam_role.lambda_exec.arn
  handler          = "handler.lambda_handler"
  runtime          = "python3.12"
  filename         = data.archive_file.membership.output_path
  source_code_hash = data.archive_file.membership.output_base64sha256
  timeout          = var.lambda_timeout
  memory_size      = var.lambda_memory

  environment {
    variables = merge(local.lambda_env, {
      MEMBERSHIP_TABLE        = aws_dynamodb_table.npo_membership.name
      OTP_TOKEN_SECRET        = random_password.otp_token_secret.result
      RENEWAL_MEMBERSHIP_YEAR = "2026-27"
      MEMBER_REGISTER_URL     = var.member_register_url
      # Square hosted checkout for NPO membership payments (My Page).
      SQUARE_ACCESS_TOKEN = var.square_access_token
      SQUARE_LOCATION_ID  = var.square_location_id
      SQUARE_ENV          = var.square_env
    })
  }

  depends_on = [aws_cloudwatch_log_group.membership]
}

# ── Lambda: npo_membership_webhook (public — Square payment callback) ────────

resource "aws_lambda_function" "npo_membership_webhook" {
  function_name    = "${local.name_prefix}-npo-membership-webhook"
  role             = aws_iam_role.lambda_exec.arn
  handler          = "handler.lambda_handler"
  runtime          = "python3.12"
  filename         = data.archive_file.npo_membership_webhook.output_path
  source_code_hash = data.archive_file.npo_membership_webhook.output_base64sha256
  timeout          = var.lambda_timeout
  memory_size      = var.lambda_memory

  environment {
    variables = {
      REGION                       = var.aws_region
      MEMBERSHIP_TABLE             = aws_dynamodb_table.npo_membership.name
      MEMBERS_TABLE                = aws_dynamodb_table.members.name
      SITE_CONFIG_TABLE            = aws_dynamodb_table.site_config.name # WhatsApp group link in the confirmation email
      RENEWAL_MEMBERSHIP_YEAR      = "2026-27"
      SES_FROM_EMAIL               = var.ses_from_email
      ADMIN_EMAIL                  = var.admin_email
      SQUARE_ACCESS_TOKEN          = var.square_access_token
      SQUARE_LOCATION_ID           = var.square_location_id
      SQUARE_WEBHOOK_SIGNATURE_KEY = var.square_npo_webhook_signature_key
      SQUARE_WEBHOOK_URL           = var.square_npo_webhook_url
      SQUARE_ENV                   = var.square_env
      # For the "payment didn't go through" email's Retry Payment link.
      FRONTEND_URL = var.frontend_domain != "" ? "https://${var.frontend_domain}" : try("https://${aws_cloudfront_distribution.frontend[0].domain_name}", "")
    }
  }

  depends_on = [aws_cloudwatch_log_group.npo_membership_webhook]
}

# ── Lambda: register (public) ────────────────────────────────────────────────

resource "aws_lambda_function" "register" {
  function_name    = "${local.name_prefix}-register"
  role             = aws_iam_role.lambda_exec.arn
  handler          = "handler.lambda_handler"
  runtime          = "python3.12"
  filename         = data.archive_file.register.output_path
  source_code_hash = data.archive_file.register.output_base64sha256
  timeout          = var.lambda_timeout
  memory_size      = var.lambda_memory
  layers           = [aws_lambda_layer_version.qr_code.arn]

  environment {
    variables = merge(local.lambda_env, {
      # For the consolidated guest check-in ticket PDF (qr_email.py ->
      # checkin_pdf.py), which pulls in a registrant's zone upgrade / seat
      # selection add-ons by registrationId alongside the entry pass.
      SEAT_UPGRADES_TABLE  = module.seat_upgrade.seat_upgrades_table_name
      SEAT_PURCHASES_TABLE = module.seat_selection.seat_purchases_table_name
    })
  }

  depends_on = [aws_cloudwatch_log_group.register]
}

# ── Lambda: get_registrations (admin) ───────────────────────────────────────

resource "aws_lambda_function" "get_registrations" {
  function_name    = "${local.name_prefix}-get-registrations"
  role             = aws_iam_role.lambda_exec.arn
  handler          = "handler.lambda_handler"
  runtime          = "python3.12"
  filename         = data.archive_file.get_registrations.output_path
  source_code_hash = data.archive_file.get_registrations.output_base64sha256
  timeout          = var.lambda_timeout
  memory_size      = var.lambda_memory

  environment {
    variables = local.lambda_env
  }

  depends_on = [aws_cloudwatch_log_group.get_registrations]
}

# ── Lambda: export_csv (admin) ───────────────────────────────────────────────

resource "aws_lambda_function" "export_csv" {
  function_name    = "${local.name_prefix}-export-csv"
  role             = aws_iam_role.lambda_exec.arn
  handler          = "handler.lambda_handler"
  runtime          = "python3.12"
  filename         = data.archive_file.export_csv.output_path
  source_code_hash = data.archive_file.export_csv.output_base64sha256
  timeout          = 60 # longer for large exports
  memory_size      = 512

  environment {
    variables = local.lambda_env
  }

  depends_on = [aws_cloudwatch_log_group.export_csv]
}

# ── Lambda: confirm_email (public click link) ────────────────────────────────

resource "aws_lambda_function" "confirm_email" {
  function_name    = "${local.name_prefix}-confirm-email"
  role             = aws_iam_role.lambda_exec.arn
  handler          = "handler.lambda_handler"
  runtime          = "python3.12"
  filename         = data.archive_file.confirm_email.output_path
  source_code_hash = data.archive_file.confirm_email.output_base64sha256
  timeout          = var.lambda_timeout
  memory_size      = var.lambda_memory

  environment {
    variables = local.lambda_env
  }

  depends_on = [aws_cloudwatch_log_group.confirm_email]
}

# ── Lambda: manage_partners (admin) ─────────────────────────────────────────

resource "aws_lambda_function" "manage_partners" {
  function_name    = "${local.name_prefix}-manage-partners"
  role             = aws_iam_role.lambda_exec.arn
  handler          = "handler.lambda_handler"
  runtime          = "python3.12"
  filename         = data.archive_file.manage_partners.output_path
  source_code_hash = data.archive_file.manage_partners.output_base64sha256
  timeout          = var.lambda_timeout
  memory_size      = var.lambda_memory

  environment {
    variables = local.lambda_env
  }

  depends_on = [aws_cloudwatch_log_group.manage_partners]
}

# ── Lambda: manage_events (admin — events CRUD) ──────────────────────────────

resource "aws_lambda_function" "manage_events" {
  function_name    = "${local.name_prefix}-manage-events"
  role             = aws_iam_role.lambda_exec.arn
  handler          = "handler.lambda_handler"
  runtime          = "python3.12"
  filename         = data.archive_file.manage_events.output_path
  source_code_hash = data.archive_file.manage_events.output_base64sha256
  timeout          = var.lambda_timeout
  memory_size      = var.lambda_memory

  environment {
    variables = local.lambda_env
  }

  depends_on = [aws_cloudwatch_log_group.manage_events]
}

# ── Lambda: manage_registrations (admin — registration CRUD + users) ─────────

resource "aws_lambda_function" "manage_registrations" {
  function_name    = "${local.name_prefix}-manage-registrations"
  role             = aws_iam_role.lambda_exec.arn
  handler          = "handler.lambda_handler"
  runtime          = "python3.12"
  filename         = data.archive_file.manage_registrations.output_path
  source_code_hash = data.archive_file.manage_registrations.output_base64sha256
  timeout          = var.lambda_timeout
  memory_size      = var.lambda_memory
  layers           = [aws_lambda_layer_version.qr_code.arn]

  environment {
    variables = merge(local.lambda_env, {
      MEMBERS_TABLE           = aws_dynamodb_table.members.name
      RENEWAL_MEMBERSHIP_YEAR = "2026-27"
      SEAT_PURCHASES_TABLE    = module.seat_selection.seat_purchases_table_name
      # For the consolidated guest check-in ticket PDF (qr_email.py ->
      # checkin_pdf.py) re-sent when an admin edits a registration.
      SEAT_UPGRADES_TABLE = module.seat_upgrade.seat_upgrades_table_name
    })
  }

  depends_on = [aws_cloudwatch_log_group.manage_registrations]
}

# ── Lambda: my_registrations (public — reg-token gated self-service) ─────────

resource "aws_lambda_function" "my_registrations" {
  function_name    = "${local.name_prefix}-my-registrations"
  role             = aws_iam_role.lambda_exec.arn
  handler          = "handler.lambda_handler"
  runtime          = "python3.12"
  filename         = data.archive_file.my_registrations.output_path
  source_code_hash = data.archive_file.my_registrations.output_base64sha256
  timeout          = var.lambda_timeout
  memory_size      = var.lambda_memory
  layers           = [aws_lambda_layer_version.qr_code.arn]

  environment {
    variables = merge(local.lambda_env, {
      MEMBERS_TABLE           = aws_dynamodb_table.members.name
      OTP_TOKEN_SECRET        = random_password.otp_token_secret.result
      RENEWAL_MEMBERSHIP_YEAR = "2026-27"
      # Checkout creation for the seat-selection plugin's paid purchase flow
      # — the webhook's own copy of these same credentials lives in
      # module.seat_selection (see backend/seat_selection.tf).
      SEAT_PURCHASES_TABLE = module.seat_selection.seat_purchases_table_name
      # Lets the standalone Celebrity Dinner sign-up (upsert_my_celebrity_dinner)
      # refuse a registration that already has the seat-upgrade plugin's
      # bundled dinner add-on — the two paths are mutually exclusive.
      SEAT_UPGRADES_TABLE = module.seat_upgrade.seat_upgrades_table_name
      # Standalone Celebrity Dinner checkout (checkout_my_celebrity_dinner) —
      # same PayPay/Square merchant credentials the seat-upgrade plugin uses,
      # a separate Square webhook subscription/signing key (own endpoint URL).
      PAYPAY_API_KEY      = var.paypay_api_key
      PAYPAY_API_SECRET   = var.paypay_api_secret
      PAYPAY_MERCHANT_ID  = var.paypay_merchant_id
      PAYPAY_ENV          = var.paypay_env
      SQUARE_ACCESS_TOKEN = var.square_access_token
      SQUARE_LOCATION_ID  = var.square_location_id
      # Only actually consumed by verify_webhook_signature, which this
      # Lambda never calls (it only creates checkouts/refunds) — inert here
      # either way, so this is whichever webhook's subscription happens to
      # be configured; kept as the Celebrity Dinner one since that's the
      # newer addition sharing this same square.py adapter.
      SQUARE_WEBHOOK_SIGNATURE_KEY = var.square_celebrity_dinner_webhook_signature_key
      SQUARE_WEBHOOK_URL           = var.square_celebrity_dinner_webhook_url
      SQUARE_ENV                   = var.square_env
    })
  }

  depends_on = [aws_cloudwatch_log_group.my_registrations]
}

# ── Lambda: celebrity_dinner_webhook (public — payment-provider callback) ────

resource "aws_lambda_function" "celebrity_dinner_webhook" {
  function_name    = "${local.name_prefix}-celebrity-dinner-webhook"
  role             = aws_iam_role.lambda_exec.arn
  handler          = "handler.lambda_handler"
  runtime          = "python3.12"
  filename         = data.archive_file.celebrity_dinner_webhook.output_path
  source_code_hash = data.archive_file.celebrity_dinner_webhook.output_base64sha256
  timeout          = var.lambda_timeout
  memory_size      = var.lambda_memory

  environment {
    variables = {
      REGION                               = var.aws_region
      CELEBRITY_DINNER_REGISTRATIONS_TABLE = aws_dynamodb_table.celebrity_dinner_registrations.name
      PAYPAY_API_KEY                       = var.paypay_api_key
      PAYPAY_API_SECRET                    = var.paypay_api_secret
      PAYPAY_MERCHANT_ID                   = var.paypay_merchant_id
      PAYPAY_ENV                           = var.paypay_env
      SQUARE_ACCESS_TOKEN                  = var.square_access_token
      SQUARE_LOCATION_ID                   = var.square_location_id
      SQUARE_WEBHOOK_SIGNATURE_KEY         = var.square_celebrity_dinner_webhook_signature_key
      SQUARE_WEBHOOK_URL                   = var.square_celebrity_dinner_webhook_url
      SQUARE_ENV                           = var.square_env
      # For the "payment received" confirmation email celebrity_dinner_emailer.py
      # sends once a webhook confirms payment — the only three inputs it needs
      # beyond the table above (see that module's own docstring).
      EVENTS_TABLE   = aws_dynamodb_table.events.name
      FRONTEND_URL   = var.frontend_domain != "" ? "https://${var.frontend_domain}" : try("https://${aws_cloudfront_distribution.frontend[0].domain_name}", "")
      SES_FROM_EMAIL = var.ses_from_email
      ADMIN_EMAIL    = var.admin_email
    }
  }

  depends_on = [aws_cloudwatch_log_group.celebrity_dinner_webhook]
}

# ── Lambda: manage_site_config (admin — carousel image upload + config) ───────

resource "aws_lambda_function" "manage_site_config" {
  function_name    = "${local.name_prefix}-manage-site-config"
  role             = aws_iam_role.lambda_exec.arn
  handler          = "handler.lambda_handler"
  runtime          = "python3.12"
  filename         = data.archive_file.manage_site_config.output_path
  source_code_hash = data.archive_file.manage_site_config.output_base64sha256
  timeout          = var.lambda_timeout
  memory_size      = var.lambda_memory

  environment {
    variables = merge(local.lambda_env, {
      SITE_CONFIG_TABLE = aws_dynamodb_table.site_config.name
      ASSETS_BUCKET     = aws_s3_bucket.assets.id
      ASSETS_BASE_URL   = local.assets_base_url
    })
  }

  depends_on = [aws_cloudwatch_log_group.manage_site_config]
}

# ── Lambda: get_site_config (public — carousel image URLs for homepage) ───────

resource "aws_lambda_function" "get_site_config" {
  function_name    = "${local.name_prefix}-get-site-config"
  role             = aws_iam_role.lambda_exec.arn
  handler          = "handler.lambda_handler"
  runtime          = "python3.12"
  filename         = data.archive_file.get_site_config.output_path
  source_code_hash = data.archive_file.get_site_config.output_base64sha256
  timeout          = 10
  memory_size      = 128

  environment {
    variables = {
      SITE_CONFIG_TABLE = aws_dynamodb_table.site_config.name
      DYNAMODB_TABLE    = aws_dynamodb_table.registrations.name
      EVENTS_TABLE      = aws_dynamodb_table.events.name
      AGMS_TABLE        = aws_dynamodb_table.agms.name
      ONAM_CAPACITY     = "350"
      REGION            = var.aws_region
    }
  }

  depends_on = [aws_cloudwatch_log_group.get_site_config]
}

# ── Lambda: get_event_availability (public — Celebrity Event / dinner / seat availability) ──

resource "aws_lambda_function" "get_event_availability" {
  function_name    = "${local.name_prefix}-get-event-availability"
  role             = aws_iam_role.lambda_exec.arn
  handler          = "handler.lambda_handler"
  runtime          = "python3.12"
  filename         = data.archive_file.get_event_availability.output_path
  source_code_hash = data.archive_file.get_event_availability.output_base64sha256
  timeout          = var.lambda_timeout
  memory_size      = var.lambda_memory

  environment {
    variables = merge(local.lambda_env, {
      # Counts bundled seat-upgrade dinner guests toward dinner capacity.
      SEAT_UPGRADES_TABLE = module.seat_upgrade.seat_upgrades_table_name
    })
  }

  depends_on = [aws_cloudwatch_log_group.get_event_availability]
}

# ── Lambda: visit_logger (public — SPA page view logging) ────────────────────

resource "aws_lambda_function" "visit_logger" {
  function_name    = "${local.name_prefix}-visit-logger"
  role             = aws_iam_role.lambda_exec.arn
  handler          = "handler.lambda_handler"
  runtime          = "python3.12"
  filename         = data.archive_file.visit_logger.output_path
  source_code_hash = data.archive_file.visit_logger.output_base64sha256
  timeout          = 10
  memory_size      = 128

  environment {
    variables = {
      VISITS_TABLE = aws_dynamodb_table.visits.name
      REGION       = var.aws_region
    }
  }

  depends_on = [aws_cloudwatch_log_group.visit_logger]
}

# ── Lambda: get_visit_summary (admin — visits analytics) ─────────────────────

resource "aws_lambda_function" "get_visit_summary" {
  function_name    = "${local.name_prefix}-get-visit-summary"
  role             = aws_iam_role.lambda_exec.arn
  handler          = "handler.lambda_handler"
  runtime          = "python3.12"
  filename         = data.archive_file.get_visit_summary.output_path
  source_code_hash = data.archive_file.get_visit_summary.output_base64sha256
  timeout          = 20
  memory_size      = 256

  environment {
    variables = {
      VISITS_TABLE = aws_dynamodb_table.visits.name
      REGION       = var.aws_region
    }
  }

  depends_on = [aws_cloudwatch_log_group.get_visit_summary]
}

# ── Lambda: manage_members (admin — members + NPO membership CRUD) ────────────

resource "aws_lambda_function" "manage_agms" {
  function_name    = "${local.name_prefix}-manage-agms"
  role             = aws_iam_role.lambda_exec.arn
  handler          = "handler.lambda_handler"
  runtime          = "python3.12"
  filename         = data.archive_file.manage_agms.output_path
  source_code_hash = data.archive_file.manage_agms.output_base64sha256
  timeout          = var.lambda_timeout
  memory_size      = var.lambda_memory

  environment {
    variables = local.lambda_env
  }

  depends_on = [aws_cloudwatch_log_group.manage_agms]
}

resource "aws_lambda_function" "manage_members" {
  function_name    = "${local.name_prefix}-manage-members"
  role             = aws_iam_role.lambda_exec.arn
  handler          = "handler.lambda_handler"
  runtime          = "python3.12"
  filename         = data.archive_file.manage_members.output_path
  source_code_hash = data.archive_file.manage_members.output_base64sha256
  timeout          = var.lambda_timeout
  memory_size      = var.lambda_memory

  environment {
    variables = merge(local.lambda_env, {
      MEMBERS_TABLE             = aws_dynamodb_table.members.name
      MEMBERSHIP_TABLE          = aws_dynamodb_table.npo_membership.name
      RENEWAL_MEMBERSHIP_YEAR   = "2026-27"
      MEMBER_ANNOUNCER_FUNCTION = aws_lambda_function.member_announcer.function_name
      SQUARE_ACCESS_TOKEN       = var.square_access_token
      SQUARE_LOCATION_ID        = var.square_location_id
      SQUARE_ENV                = var.square_env
    })
  }

  depends_on = [aws_cloudwatch_log_group.manage_members]
}

# ── Lambda: member_announcer (background worker for member announcements) ─────
# Invoked asynchronously by manage_members; sends thousands of emails, so it
# gets the maximum timeout instead of the API default.

resource "aws_lambda_function" "member_announcer" {
  function_name    = "${local.name_prefix}-member-announcer"
  role             = aws_iam_role.lambda_exec.arn
  handler          = "handler.lambda_handler"
  runtime          = "python3.12"
  filename         = data.archive_file.member_announcer.output_path
  source_code_hash = data.archive_file.member_announcer.output_base64sha256
  timeout          = 900
  memory_size      = var.lambda_memory

  environment {
    variables = merge(local.lambda_env, {
      MEMBERSHIP_TABLE        = aws_dynamodb_table.npo_membership.name
      MEMBERS_TABLE           = aws_dynamodb_table.members.name
      RENEWAL_MEMBERSHIP_YEAR = "2026-27"
      CURRENT_MEMBERSHIP_YEAR = "2025-26"
    })
  }

  depends_on = [aws_cloudwatch_log_group.member_announcer]
}

# manage_members (same shared role) may start the worker asynchronously.
resource "aws_iam_role_policy" "invoke_member_announcer" {
  name = "${local.name_prefix}-invoke-member-announcer"
  role = aws_iam_role.lambda_exec.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = ["lambda:InvokeFunction"]
      Resource = [aws_lambda_function.member_announcer.arn]
    }]
  })
}

# ── Lambda: manage_celebrity_dinner (admin — dinner config CRUD + listing) ───

resource "aws_lambda_function" "manage_celebrity_dinner" {
  function_name    = "${local.name_prefix}-manage-celebrity-dinner"
  role             = aws_iam_role.lambda_exec.arn
  handler          = "handler.lambda_handler"
  runtime          = "python3.12"
  filename         = data.archive_file.manage_celebrity_dinner.output_path
  source_code_hash = data.archive_file.manage_celebrity_dinner.output_base64sha256
  timeout          = var.lambda_timeout
  memory_size      = var.lambda_memory

  environment {
    variables = merge(local.lambda_env, {
      # So its Celebrity Dinner sign-ups list and "seats left" capacity math
      # (celebrity_dinner_logic.total_active_dinner_guest_count) can see the
      # seat-upgrade plugin's bundled add-on guests too, not just this
      # module's own standalone sign-ups — mirrors my_registrations' own
      # SEAT_UPGRADES_TABLE env var below.
      SEAT_UPGRADES_TABLE = module.seat_upgrade.seat_upgrades_table_name
    })
  }

  depends_on = [
    aws_cloudwatch_log_group.manage_celebrity_dinner,
    aws_iam_role_policy.lambda_policy
  ]
}

# ── Lambda: manage_entry_slots (admin — entry slot CRUD) ─────────────────────

resource "aws_lambda_function" "manage_entry_slots" {
  function_name    = "${local.name_prefix}-manage-entry-slots"
  role             = aws_iam_role.lambda_exec.arn
  handler          = "handler.lambda_handler"
  runtime          = "python3.12"
  filename         = data.archive_file.manage_entry_slots.output_path
  source_code_hash = data.archive_file.manage_entry_slots.output_base64sha256
  timeout          = var.lambda_timeout
  memory_size      = var.lambda_memory

  environment {
    variables = local.lambda_env
  }

  depends_on = [
    aws_cloudwatch_log_group.manage_entry_slots,
    aws_iam_role_policy.lambda_policy
  ]
}

# ── Lambda: release_buffer (admin — release buffer capacity) ────────────────

resource "aws_lambda_function" "release_buffer" {
  function_name    = "${local.name_prefix}-release-buffer"
  role             = aws_iam_role.lambda_exec.arn
  handler          = "handler.lambda_handler"
  runtime          = "python3.12"
  filename         = data.archive_file.release_buffer.output_path
  source_code_hash = data.archive_file.release_buffer.output_base64sha256
  timeout          = var.lambda_timeout
  memory_size      = var.lambda_memory

  environment {
    variables = local.lambda_env
  }

  depends_on = [
    aws_cloudwatch_log_group.release_buffer,
    aws_iam_role_policy.lambda_policy
  ]
}

# ── Lambda: get_available_slots (public — list available slots for registrant) ──

resource "aws_lambda_function" "get_available_slots" {
  function_name    = "${local.name_prefix}-get-available-slots"
  role             = aws_iam_role.lambda_exec.arn
  handler          = "handler.lambda_handler"
  runtime          = "python3.12"
  filename         = data.archive_file.get_available_slots.output_path
  source_code_hash = data.archive_file.get_available_slots.output_base64sha256
  timeout          = var.lambda_timeout
  memory_size      = var.lambda_memory

  environment {
    variables = local.lambda_env
  }

  depends_on = [
    aws_cloudwatch_log_group.get_available_slots,
    aws_iam_role_policy.lambda_policy
  ]
}

# get_available_seats and manage_seats are provisioned by the seat-selection
# plugin module (module "seat_selection" in main.tf), with their own
# dedicated IAM role/tables — see plugins/seat-selection/terraform.

# ── Lambda: manage_reconciliation (admin — payment reconciliation) ──────────

resource "aws_lambda_function" "manage_reconciliation" {
  function_name    = "${local.name_prefix}-manage-reconciliation"
  role             = aws_iam_role.lambda_exec.arn
  handler          = "handler.lambda_handler"
  runtime          = "python3.12"
  filename         = data.archive_file.manage_reconciliation.output_path
  source_code_hash = data.archive_file.manage_reconciliation.output_base64sha256
  timeout          = 60 # CSV import + matching over a full event can take longer than the default
  memory_size      = 512

  environment {
    variables = local.lambda_env
  }

  depends_on = [
    aws_cloudwatch_log_group.manage_reconciliation,
    aws_iam_role_policy.lambda_policy
  ]
}
