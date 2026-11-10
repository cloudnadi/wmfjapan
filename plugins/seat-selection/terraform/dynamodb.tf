# ── Seats table ──────────────────────────────────────────────────────────────
# Sparse: a row exists only for a booked or admin-blocked seat; an unlisted
# seatId is available. Composite PK: eventType (hash) + seatId (range, e.g.
# "R04C12"). GSI lets a registration's seats be released or reassigned
# without scanning the whole table.
locals {
  suffix_part = var.suffix != "" ? "-${var.suffix}" : ""
}

resource "aws_dynamodb_table" "seats" {
  name         = "${var.name_prefix}-seat-selection-seats${local.suffix_part}"
  billing_mode = var.dynamodb_billing_mode
  hash_key     = "eventType"
  range_key    = "seatId"

  attribute {
    name = "eventType"
    type = "S"
  }

  attribute {
    name = "seatId"
    type = "S"
  }

  attribute {
    name = "registrationId"
    type = "S"
  }

  global_secondary_index {
    name            = "RegistrationIdIndex"
    hash_key        = "registrationId"
    projection_type = "ALL"
  }

  point_in_time_recovery {
    enabled = true
  }

  server_side_encryption {
    enabled = true
  }

  tags = merge(var.tags, { Name = "${var.name_prefix}-seat-selection-seats${local.suffix_part}" })
}

# ── Category overrides table ────────────────────────────────────────────────
# One item per eventType/hall: {eventType, overrides: {seatId: category}}.
# Only the cosmetic, still-bookable categories (zone1/zone2/zone3/regular)
# ever appear here — camera/projector are stored as BLOCKED rows in the
# seats table instead (see seat_selection_logic.block_seat), since those are
# not bookable. Dedicated table rather than piggybacking on some other
# app-specific config table, so this module has no dependency beyond itself.
resource "aws_dynamodb_table" "category_overrides" {
  name         = "${var.name_prefix}-seat-selection-category-overrides${local.suffix_part}"
  billing_mode = var.dynamodb_billing_mode
  hash_key     = "eventType"

  attribute {
    name = "eventType"
    type = "S"
  }

  point_in_time_recovery {
    enabled = true
  }

  server_side_encryption {
    enabled = true
  }

  tags = merge(var.tags, { Name = "${var.name_prefix}-seat-selection-category-overrides${local.suffix_part}" })
}

# ── Zone pricing table ───────────────────────────────────────────────────────
# One item per eventType+category (zone1/zone2/zone3/regular only — camera/
# projector are never for sale). Composite PK: eventType (hash) +
# category (range). A category with no row here is unpriced/not for sale;
# seat_selection_logic.price_seats() refuses to price seats in an unpriced
# or INACTIVE category rather than silently charging 0.
resource "aws_dynamodb_table" "zone_pricing" {
  name         = "${var.name_prefix}-seat-selection-zone-pricing${local.suffix_part}"
  billing_mode = var.dynamodb_billing_mode
  hash_key     = "eventType"
  range_key    = "category"

  attribute {
    name = "eventType"
    type = "S"
  }

  attribute {
    name = "category"
    type = "S"
  }

  point_in_time_recovery {
    enabled = true
  }

  server_side_encryption {
    enabled = true
  }

  tags = merge(var.tags, { Name = "${var.name_prefix}-seat-selection-zone-pricing${local.suffix_part}" })
}

# ── Seat purchases table ─────────────────────────────────────────────────────
# One item per purchase request (hash key purchaseId only — no range key).
# A purchase claims specific seatIds (via seat_selection_logic.
# claim_seats_atomic) rather than holding a capacity counter, so there's
# nothing else to reserve here — this table is purely the purchase record's
# own lifecycle (pending -> paid -> ..., or -> cancelled) and online-payment
# bookkeeping. See seat_purchase_logic.py.
#
# Both this module's own webhook Lambda AND the host app's registrant-facing
# Lambda (which owns creation/checkout/cancel — see the README) read/write
# this table, so its ARN is exported for the host to grant its own Lambda
# role access to, the same way seats_table_arn already is.
resource "aws_dynamodb_table" "seat_purchases" {
  name         = "${var.name_prefix}-seat-selection-purchases${local.suffix_part}"
  billing_mode = var.dynamodb_billing_mode
  hash_key     = "purchaseId"

  attribute {
    name = "purchaseId"
    type = "S"
  }

  attribute {
    name = "registrationId"
    type = "S"
  }

  attribute {
    name = "eventType"
    type = "S"
  }

  attribute {
    name = "providerPaymentId"
    type = "S"
  }

  attribute {
    name = "requestedAt"
    type = "S"
  }

  attribute {
    name = "checkInToken"
    type = "S"
  }

  global_secondary_index {
    name            = "RegistrationIdIndex"
    hash_key        = "registrationId"
    range_key       = "requestedAt"
    projection_type = "ALL"
  }

  global_secondary_index {
    name            = "EventTypeIndex"
    hash_key        = "eventType"
    range_key       = "requestedAt"
    projection_type = "ALL"
  }

  # Sparse — only populated once an online checkout starts (record_checkout),
  # so the webhook/return-page can find a purchase from nothing but the
  # provider's own reference, without a separate lookup table.
  global_secondary_index {
    name            = "ProviderPaymentIdIndex"
    hash_key        = "providerPaymentId"
    projection_type = "ALL"
  }

  # Sparse — only populated once a purchase is marked paid (see
  # update_purchase_status), so the door check-in Lambda can resolve a
  # scanned QR without a separate lookup table, and a pending/cancelled
  # purchase is never even reachable this way.
  global_secondary_index {
    name            = "CheckInTokenIndex"
    hash_key        = "checkInToken"
    projection_type = "ALL"
  }

  point_in_time_recovery {
    enabled = true
  }

  server_side_encryption {
    enabled = true
  }

  tags = merge(var.tags, { Name = "${var.name_prefix}-seat-selection-purchases${local.suffix_part}" })
}
