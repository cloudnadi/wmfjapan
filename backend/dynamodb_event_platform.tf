# ── Event platform tables ────────────────────────────────────────────────────
# Events, per-event ticket types, versioned registration form schemas,
# registrations with answers, atomic capacity counters (event, ticket and
# add-on units) and an admin audit log.

resource "aws_dynamodb_table" "events_v2" {
  name         = "${local.name_prefix}-events-v2-${local.suffix}"
  billing_mode = var.dynamodb_billing_mode
  hash_key     = "eventId"

  attribute {
    name = "eventId"
    type = "S"
  }

  attribute {
    name = "slug"
    type = "S"
  }

  attribute {
    name = "status"
    type = "S"
  }

  attribute {
    name = "startsAt"
    type = "S"
  }

  # Public pages resolve /events/<slug>.
  global_secondary_index {
    name            = "SlugIndex"
    hash_key        = "slug"
    projection_type = "ALL"
  }

  # Listing: published events by start time (upcoming / past).
  global_secondary_index {
    name            = "StatusStartIndex"
    hash_key        = "status"
    range_key       = "startsAt"
    projection_type = "ALL"
  }

  point_in_time_recovery {
    enabled = true
  }

  server_side_encryption {
    enabled = true
  }

  tags = {
    Name        = "${local.name_prefix}-events-v2"
    Environment = var.environment
  }
}

resource "aws_dynamodb_table" "event_ticket_types" {
  name         = "${local.name_prefix}-event-ticket-types-${local.suffix}"
  billing_mode = var.dynamodb_billing_mode
  hash_key     = "eventId"
  range_key    = "ticketTypeId"

  attribute {
    name = "eventId"
    type = "S"
  }

  attribute {
    name = "ticketTypeId"
    type = "S"
  }

  point_in_time_recovery {
    enabled = true
  }

  server_side_encryption {
    enabled = true
  }

  tags = {
    Name        = "${local.name_prefix}-event-ticket-types"
    Environment = var.environment
  }
}

# One item per (formId, version). Versions are immutable once a registration
# references them; editing a form writes version N+1.
resource "aws_dynamodb_table" "form_schemas" {
  name         = "${local.name_prefix}-form-schemas-${local.suffix}"
  billing_mode = var.dynamodb_billing_mode
  hash_key     = "formId"
  range_key    = "version"

  attribute {
    name = "formId"
    type = "S"
  }

  attribute {
    name = "version"
    type = "N"
  }

  attribute {
    name = "kind"
    type = "S"
  }

  attribute {
    name = "updatedAt"
    type = "S"
  }

  # Reusable form templates (kind = "template").
  global_secondary_index {
    name            = "KindIndex"
    hash_key        = "kind"
    range_key       = "updatedAt"
    projection_type = "ALL"
  }

  point_in_time_recovery {
    enabled = true
  }

  server_side_encryption {
    enabled = true
  }

  tags = {
    Name        = "${local.name_prefix}-form-schemas"
    Environment = var.environment
  }
}

resource "aws_dynamodb_table" "event_registrations" {
  name         = "${local.name_prefix}-event-registrations-${local.suffix}"
  billing_mode = var.dynamodb_billing_mode
  hash_key     = "registrationId"

  attribute {
    name = "registrationId"
    type = "S"
  }

  attribute {
    name = "eventId"
    type = "S"
  }

  attribute {
    name = "email"
    type = "S"
  }

  attribute {
    name = "createdAt"
    type = "S"
  }

  attribute {
    name = "checkInToken"
    type = "S"
  }

  attribute {
    name = "providerPaymentId"
    type = "S"
  }

  attribute {
    name = "status"
    type = "S"
  }

  attribute {
    name = "holdUntil"
    type = "S"
  }

  global_secondary_index {
    name            = "EventIndex"
    hash_key        = "eventId"
    range_key       = "createdAt"
    projection_type = "ALL"
  }

  global_secondary_index {
    name            = "EmailIndex"
    hash_key        = "email"
    range_key       = "createdAt"
    projection_type = "ALL"
  }

  # QR check-in lookup.
  global_secondary_index {
    name            = "CheckInTokenIndex"
    hash_key        = "checkInToken"
    projection_type = "ALL"
  }

  # Square webhook: order id -> registration.
  global_secondary_index {
    name            = "ProviderPaymentIndex"
    hash_key        = "providerPaymentId"
    projection_type = "ALL"
  }

  # Hold expiry job: unpaid registrations whose hold has run out.
  global_secondary_index {
    name            = "StatusHoldIndex"
    hash_key        = "status"
    range_key       = "holdUntil"
    projection_type = "ALL"
  }

  point_in_time_recovery {
    enabled = true
  }

  server_side_encryption {
    enabled = true
  }

  tags = {
    Name        = "${local.name_prefix}-event-registrations"
    Environment = var.environment
  }
}

# Atomic capacity counters (conditional UpdateItem / TransactWriteItems), keyed
# "EVENT#<eventId>" and "TICKET#<eventId>#<ticketTypeId>".
resource "aws_dynamodb_table" "event_counters" {
  name         = "${local.name_prefix}-event-counters-${local.suffix}"
  billing_mode = var.dynamodb_billing_mode
  hash_key     = "counterKey"

  attribute {
    name = "counterKey"
    type = "S"
  }

  # Rate-limit windows ("RL#...") carry an expiresAt epoch and are removed by TTL.
  ttl {
    attribute_name = "expiresAt"
    enabled        = true
  }

  point_in_time_recovery {
    enabled = true
  }

  server_side_encryption {
    enabled = true
  }

  tags = {
    Name        = "${local.name_prefix}-event-counters"
    Environment = var.environment
  }
}

resource "aws_dynamodb_table" "audit_log" {
  name         = "${local.name_prefix}-audit-log-${local.suffix}"
  billing_mode = var.dynamodb_billing_mode
  hash_key     = "entityKey"
  range_key    = "sortKey"

  attribute {
    name = "entityKey"
    type = "S"
  }

  attribute {
    name = "sortKey"
    type = "S"
  }

  point_in_time_recovery {
    enabled = true
  }

  server_side_encryption {
    enabled = true
  }

  tags = {
    Name        = "${local.name_prefix}-audit-log"
    Environment = var.environment
  }
}

# Shared Lambda role access to the event-platform tables.
resource "aws_iam_role_policy" "lambda_event_platform_policy" {
  name = "${local.name_prefix}-lambda-event-platform-policy"
  role = aws_iam_role.lambda_exec.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = [
          "dynamodb:GetItem",
          "dynamodb:PutItem",
          "dynamodb:UpdateItem",
          "dynamodb:DeleteItem",
          "dynamodb:Query",
          "dynamodb:Scan",
          "dynamodb:ConditionCheckItem",
          "dynamodb:BatchGetItem",
        ]
        Resource = flatten([
          for t in [
            aws_dynamodb_table.events_v2,
            aws_dynamodb_table.event_ticket_types,
            aws_dynamodb_table.form_schemas,
            aws_dynamodb_table.event_registrations,
            aws_dynamodb_table.event_counters,
            aws_dynamodb_table.audit_log,
          ] : [t.arn, "${t.arn}/index/*"]
        ])
      },
    ]
  })
}
