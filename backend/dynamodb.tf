resource "aws_dynamodb_table" "registrations" {
  name         = "${local.name_prefix}-registrations-${local.suffix}"
  billing_mode = var.dynamodb_billing_mode
  hash_key     = "registrationId"

  attribute {
    name = "registrationId"
    type = "S"
  }

  # GSI: query by email
  attribute {
    name = "email"
    type = "S"
  }

  # GSI: query/sort by event host
  attribute {
    name = "eventHost"
    type = "S"
  }

  # GSI: query/sort by submission timestamp
  attribute {
    name = "submittedAt"
    type = "S"
  }

  # GSI: query by event type
  attribute {
    name = "eventType"
    type = "S"
  }

  # GSI: query by event date (for efficient scans of recent registrations)
  attribute {
    name = "eventDate"
    type = "S"
  }

  attribute {
    name = "registrationPassToken"
    type = "S"
  }

  global_secondary_index {
    name            = "EmailIndex"
    hash_key        = "email"
    range_key       = "submittedAt"
    projection_type = "ALL"
  }

  global_secondary_index {
    name            = "EventHostIndex"
    hash_key        = "eventHost"
    range_key       = "submittedAt"
    projection_type = "ALL"
  }

  global_secondary_index {
    name            = "SubmittedAtIndex"
    hash_key        = "submittedAt" # partition by date prefix (YYYY-MM-DD) for efficient scans
    projection_type = "ALL"
  }

  global_secondary_index {
    name            = "EventTypeIndex"
    hash_key        = "eventType"
    range_key       = "submittedAt"
    projection_type = "ALL"
  }

  global_secondary_index {
    name            = "EventDateIndex"
    hash_key        = "eventDate" # partition by date prefix (YYYY-MM-DD) for efficient scans
    range_key       = "submittedAt"
    projection_type = "ALL"
  }

  global_secondary_index {
    name            = "RegistrationPassTokenIndex"
    hash_key        = "registrationPassToken"
    projection_type = "ALL"
  }

  point_in_time_recovery {
    enabled = true
  }

  server_side_encryption {
    enabled = true
  }

  tags = {
    Name = "${local.name_prefix}-registrations"
  }
}

# ── Visit Logs Table ────────────────────────────────────────────────────────
# Records SPA page views with a date bucket + timestamp sort key.

resource "aws_dynamodb_table" "visits" {
  name         = "${local.name_prefix}-visits-${local.suffix}"
  billing_mode = var.dynamodb_billing_mode
  hash_key     = "visitDate"
  range_key    = "visitedAtKey"

  attribute {
    name = "visitDate"
    type = "S"
  }

  attribute {
    name = "visitedAtKey"
    type = "S"
  }

  point_in_time_recovery {
    enabled = true
  }

  server_side_encryption {
    enabled = true
  }

  tags = {
    Name        = "${local.name_prefix}-visits"
    Environment = var.environment
  }
}

# ── Events Metadata Table ────────────────────────────────────────────────────
# Stores event configuration, metadata, and tenant/host information
# Allows dynamic management of multiple events without code changes

resource "aws_dynamodb_table" "events" {
  name         = "${local.name_prefix}-events-${local.suffix}"
  billing_mode = var.dynamodb_billing_mode
  hash_key     = "eventType"

  attribute {
    name = "eventType"
    type = "S"
  }

  # GSI: query by host/tenant
  attribute {
    name = "hostId"
    type = "S"
  }

  # GSI: query by event status
  attribute {
    name = "status"
    type = "S"
  }

  global_secondary_index {
    name            = "HostIdIndex"
    hash_key        = "hostId"
    projection_type = "ALL"
  }

  global_secondary_index {
    name            = "StatusIndex"
    hash_key        = "status"
    projection_type = "ALL"
  }

  point_in_time_recovery {
    enabled = true
  }

  ttl {
    enabled        = true
    attribute_name = "expiresAt"
  }

  tags = {
    Name        = "${local.name_prefix}-events"
    Environment = var.environment
  }
}

# ── Partners Table ──────────────────────────────────────────────────────────
# Stores event partner profiles, their website links, and display metadata.

resource "aws_dynamodb_table" "partners" {
  name         = "${local.name_prefix}-partners-${local.suffix}"
  billing_mode = var.dynamodb_billing_mode
  hash_key     = "partnerId"

  attribute {
    name = "partnerId"
    type = "S"
  }

  point_in_time_recovery {
    enabled = true
  }

  server_side_encryption {
    enabled = true
  }

  tags = {
    Name        = "${local.name_prefix}-partners"
    Environment = var.environment
  }
}

# ── Login OTP Table ──────────────────────────────────────────────────────────
# Stores short-lived email verification codes for the public registration gate.
# Rows auto-expire via the `expiresAt` TTL attribute.

resource "aws_dynamodb_table" "login_otp" {
  name         = "${local.name_prefix}-login-otp-${local.suffix}"
  billing_mode = var.dynamodb_billing_mode
  hash_key     = "email"

  attribute {
    name = "email"
    type = "S"
  }

  ttl {
    enabled        = true
    attribute_name = "expiresAt"
  }

  server_side_encryption {
    enabled = true
  }

  tags = {
    Name        = "${local.name_prefix}-login-otp"
    Environment = var.environment
  }
}

# ── Site Config Table ────────────────────────────────────────────────────────
# Stores runtime site configuration (e.g., carousel image keys).
# PK: configKey ("carousel_onam", "carousel_anniversary", …)

resource "aws_dynamodb_table" "site_config" {
  name         = "${local.name_prefix}-site-config-${local.suffix}"
  billing_mode = var.dynamodb_billing_mode
  hash_key     = "configKey"

  attribute {
    name = "configKey"
    type = "S"
  }

  server_side_encryption {
    enabled = true
  }

  tags = {
    Name        = "${local.name_prefix}-site-config"
    Environment = var.environment
  }
}

# ── Members Master Table ─────────────────────────────────────────────────────
# The authoritative list of known members, imported from the legacy site's CSV
# export. Login is gated against this table: an email must exist here before an
# OTP is issued. Anyone not present is directed to the public registration flow.
# Keyed by lowercased email; a small lean subset of the CSV columns is stored.

resource "aws_dynamodb_table" "members" {
  name         = "${local.name_prefix}-members-${local.suffix}"
  billing_mode = var.dynamodb_billing_mode
  hash_key     = "email"

  attribute {
    name = "email"
    type = "S"
  }

  point_in_time_recovery {
    enabled = true
  }

  server_side_encryption {
    enabled = true
  }

  tags = {
    Name        = "${local.name_prefix}-members"
    Environment = var.environment
  }
}

# ── NPO Membership Table ─────────────────────────────────────────────────────
# Stores NPO membership as dated transactions, keyed by (memberid,
# transactionNo). A member is "valid" when any transaction's
# [membDateFrom, membDateTo] range covers the reference date. Members are linked
# to a login email via the members master table (email → memberid). Populated
# from the legacy CSV export and appended to on join/renew.

resource "aws_dynamodb_table" "npo_membership" {
  name         = "${local.name_prefix}-npo-membership-${local.suffix}"
  billing_mode = var.dynamodb_billing_mode
  hash_key     = "memberid"
  range_key    = "transactionNo"

  attribute {
    name = "memberid"
    type = "S"
  }

  attribute {
    name = "transactionNo"
    type = "S"
  }

  # Sparse index: only My Page Square checkouts carry providerPaymentId. The
  # webhook uses it to find the pending transaction from a Square order id.
  attribute {
    name = "providerPaymentId"
    type = "S"
  }

  global_secondary_index {
    name            = "providerPaymentId-index"
    hash_key        = "providerPaymentId"
    projection_type = "ALL"
  }

  point_in_time_recovery {
    enabled = true
  }

  server_side_encryption {
    enabled = true
  }

  tags = {
    Name        = "${local.name_prefix}-npo-membership"
    Environment = var.environment
  }
}

# ── Member Announcements Table ───────────────────────────────────────────────
# One item per announcement send (AGM / join / renew) from the admin Members
# tab: audience size, progress, result. Written by manage_members (queued) and
# the member_announcer worker (sending / done).

resource "aws_dynamodb_table" "member_announcements" {
  name         = "${local.name_prefix}-member-announcements-${local.suffix}"
  billing_mode = var.dynamodb_billing_mode
  hash_key     = "campaignId"

  attribute {
    name = "campaignId"
    type = "S"
  }

  point_in_time_recovery {
    enabled = true
  }

  server_side_encryption {
    enabled = true
  }

  tags = {
    Name        = "${local.name_prefix}-member-announcements"
    Environment = var.environment
  }
}

# ── AGMs Table ───────────────────────────────────────────────────────────────
# One item per AGM (title, date, venue, whether registration is open, ...),
# managed from the admin AGM tab. Past AGMs stay as a read-only record; their
# registrations live in agm_registrations below.

resource "aws_dynamodb_table" "agms" {
  name         = "${local.name_prefix}-agms-${local.suffix}"
  billing_mode = var.dynamodb_billing_mode
  hash_key     = "agmId"

  attribute {
    name = "agmId"
    type = "S"
  }

  point_in_time_recovery {
    enabled = true
  }

  server_side_encryption {
    enabled = true
  }

  tags = {
    Name        = "${local.name_prefix}-agms"
    Environment = var.environment
  }
}

# ── AGM Registrations Table ──────────────────────────────────────────────────
# One record per (AGM, member email): attendee counts by age group and whether
# they attend in person or remotely. agmId comes from the admin AGM settings,
# so each year's AGM keeps its own records.

resource "aws_dynamodb_table" "agm_registrations" {
  name         = "${local.name_prefix}-agm-registrations-${local.suffix}"
  billing_mode = var.dynamodb_billing_mode
  hash_key     = "agmId"
  range_key    = "email"

  attribute {
    name = "agmId"
    type = "S"
  }

  attribute {
    name = "email"
    type = "S"
  }

  point_in_time_recovery {
    enabled = true
  }

  server_side_encryption {
    enabled = true
  }

  tags = {
    Name        = "${local.name_prefix}-agm-registrations"
    Environment = var.environment
  }
}

# ── Entry Slots Table ────────────────────────────────────────────────────────
# Stores event entry slot definitions per event: capacity, booking time window,
# status (ACTIVE/INACTIVE). Admin-managed via /admin/events/{eventType}/entry-slots.
# Composite PK: eventType (hash) + slotId (range).

resource "aws_dynamodb_table" "entry_slots" {
  name         = "${local.name_prefix}-entry-slots-${local.suffix}"
  billing_mode = var.dynamodb_billing_mode
  hash_key     = "eventType"
  range_key    = "slotId"

  attribute {
    name = "eventType"
    type = "S"
  }

  attribute {
    name = "slotId"
    type = "S"
  }

  attribute {
    name = "status"
    type = "S"
  }

  # GSI: query by status (ACTIVE, INACTIVE) within an event
  global_secondary_index {
    name            = "EventTypeStatusIndex"
    hash_key        = "eventType"
    range_key       = "status"
    projection_type = "ALL"
  }

  point_in_time_recovery {
    enabled = true
  }

  server_side_encryption {
    enabled = true
  }

  tags = {
    Name        = "${local.name_prefix}-entry-slots"
    Environment = var.environment
  }
}

# ── Slot Reservations Table ─────────────────────────────────────────────────
# Tracks seat reservations per entry slot. A registration can have one
# reservation (HELD → CONFIRMED on successful submission, or CANCELLED on
# refund). Composite PK: eventType (hash) + reservationId (range).
# GSI for querying by registrationId to find a registration's slot.

resource "aws_dynamodb_table" "slot_reservations" {
  name         = "${local.name_prefix}-slot-reservations-${local.suffix}"
  billing_mode = var.dynamodb_billing_mode
  hash_key     = "eventType"
  range_key    = "reservationId"

  attribute {
    name = "eventType"
    type = "S"
  }

  attribute {
    name = "reservationId"
    type = "S"
  }

  attribute {
    name = "registrationId"
    type = "S"
  }

  attribute {
    name = "entrySlotId"
    type = "S"
  }

  attribute {
    name = "status"
    type = "S"
  }

  # GSI: query by registrationId to find which slot a registration occupies
  global_secondary_index {
    name            = "RegistrationIdIndex"
    hash_key        = "registrationId"
    projection_type = "ALL"
  }

  # GSI: query by (eventType, entrySlotId) to sum occupancy of a slot
  global_secondary_index {
    name            = "SlotOccupancyIndex"
    hash_key        = "entrySlotId"
    range_key       = "status"
    projection_type = "ALL"
  }

  # TTL: auto-expire HELD reservations after expiresAt timestamp
  ttl {
    enabled        = true
    attribute_name = "expiresAt"
  }

  point_in_time_recovery {
    enabled = true
  }

  server_side_encryption {
    enabled = true
  }

  tags = {
    Name        = "${local.name_prefix}-slot-reservations"
    Environment = var.environment
  }
}

# ── Entry Allocations Table ─────────────────────────────────────────────────
# One authorization pass per registration/slot allocation. The publicToken is
# opaque and is never derived from a registration or database identifier.

resource "aws_dynamodb_table" "entry_allocations" {
  name         = "${local.name_prefix}-entry-allocations-${local.suffix}"
  billing_mode = var.dynamodb_billing_mode
  hash_key     = "eventType"
  range_key    = "allocationId"

  attribute {
    name = "eventType"
    type = "S"
  }

  attribute {
    name = "allocationId"
    type = "S"
  }

  attribute {
    name = "publicToken"
    type = "S"
  }

  attribute {
    name = "registrationId"
    type = "S"
  }

  global_secondary_index {
    name            = "PublicTokenIndex"
    hash_key        = "publicToken"
    projection_type = "ALL"
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

  tags = {
    Name        = "${local.name_prefix}-entry-allocations"
    Environment = var.environment
  }
}

# The Celebrity Event seat map's own tables (seats + category overrides) are
# provisioned by the seat-selection plugin module instead of here — see
# module "seat_selection" in main.tf and plugins/seat-selection/terraform.

# ── Payment Transactions Table ──────────────────────────────────────────────
# Normalized rows imported from admin-uploaded CSV statements (credit card,
# bank transfer, PayPay). Each import batch gets a batchId so a mistaken
# upload can be undone in one delete. Composite PK: eventType (hash) +
# transactionId (range) — reconciliation always runs against a single event.
resource "aws_dynamodb_table" "payment_transactions" {
  name         = "${local.name_prefix}-payment-transactions-${local.suffix}"
  billing_mode = var.dynamodb_billing_mode
  hash_key     = "eventType"
  range_key    = "transactionId"

  attribute {
    name = "eventType"
    type = "S"
  }

  attribute {
    name = "transactionId"
    type = "S"
  }

  attribute {
    name = "batchId"
    type = "S"
  }

  # GSI: find/delete every row from one upload batch.
  global_secondary_index {
    name            = "BatchIdIndex"
    hash_key        = "batchId"
    projection_type = "ALL"
  }

  point_in_time_recovery {
    enabled = true
  }

  server_side_encryption {
    enabled = true
  }

  tags = {
    Name        = "${local.name_prefix}-payment-transactions"
    Environment = var.environment
  }
}

# ── Reconciliation Runs Table ───────────────────────────────────────────────
# One record per "Run Reconciliation" click: the full matched/unmatched
# report snapshot for a single event, kept for history and CSV re-export.
# Composite PK: eventType (hash) + runId (range, sortable by time).
resource "aws_dynamodb_table" "reconciliation_runs" {
  name         = "${local.name_prefix}-reconciliation-runs-${local.suffix}"
  billing_mode = var.dynamodb_billing_mode
  hash_key     = "eventType"
  range_key    = "runId"

  attribute {
    name = "eventType"
    type = "S"
  }

  attribute {
    name = "runId"
    type = "S"
  }

  point_in_time_recovery {
    enabled = true
  }

  server_side_encryption {
    enabled = true
  }

  tags = {
    Name        = "${local.name_prefix}-reconciliation-runs"
    Environment = var.environment
  }
}

# Append-only check-in events. The allocation counter is updated atomically in
# the allocation table; these records preserve who checked in what quantity.
resource "aws_dynamodb_table" "entry_check_ins" {
  name         = "${local.name_prefix}-entry-check-ins-${local.suffix}"
  billing_mode = var.dynamodb_billing_mode
  hash_key     = "allocationId"
  range_key    = "checkInId"

  attribute {
    name = "allocationId"
    type = "S"
  }

  attribute {
    name = "checkInId"
    type = "S"
  }

  point_in_time_recovery {
    enabled = true
  }

  server_side_encryption {
    enabled = true
  }

  tags = {
    Name        = "${local.name_prefix}-entry-check-ins"
    Environment = var.environment
  }
}

# ── Celebrity Dinner Config Table ───────────────────────────────────────────
# One item per event: whether the Celebrity Dinner add-on is enabled, its
# registration window, and the admin-set dinner details (venue, price,
# conditions). Managed via /admin/events/{eventType}/celebrity-dinner.

resource "aws_dynamodb_table" "celebrity_dinner_config" {
  name         = "${local.name_prefix}-celebrity-dinner-config-${local.suffix}"
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

  tags = {
    Name        = "${local.name_prefix}-celebrity-dinner-config"
    Environment = var.environment
  }
}

# ── Celebrity Dinner Registrations Table ────────────────────────────────────
# A Celebrity Dinner sign-up made against an existing registration. Never
# writes to the registrations table itself. One active row per registrationId
# per event, enforced in application code via the RegistrationIdIndex GSI.

resource "aws_dynamodb_table" "celebrity_dinner_registrations" {
  name         = "${local.name_prefix}-celebrity-dinner-registrations-${local.suffix}"
  billing_mode = var.dynamodb_billing_mode
  hash_key     = "eventType"
  range_key    = "dinnerRegistrationId"

  attribute {
    name = "eventType"
    type = "S"
  }

  attribute {
    name = "dinnerRegistrationId"
    type = "S"
  }

  attribute {
    name = "registrationId"
    type = "S"
  }

  attribute {
    name = "providerPaymentId"
    type = "S"
  }

  # GSI: find the (at most one active) dinner sign-up for a base registration.
  global_secondary_index {
    name            = "RegistrationIdIndex"
    hash_key        = "registrationId"
    projection_type = "ALL"
  }

  # GSI: look up a booking by the payment provider's own reference (a PayPay
  # merchantPaymentId or a Square Order id) when a webhook or the return-page
  # reconcile call only has that to go on — mirrors the seat-upgrade plugin's
  # own seat_upgrades table ProviderPaymentIdIndex. Sparse — only populated
  # once a checkout has actually started.
  global_secondary_index {
    name            = "ProviderPaymentIdIndex"
    hash_key        = "providerPaymentId"
    projection_type = "ALL"
  }

  point_in_time_recovery {
    enabled = true
  }

  server_side_encryption {
    enabled = true
  }

  tags = {
    Name        = "${local.name_prefix}-celebrity-dinner-registrations"
    Environment = var.environment
  }
}