# Operational scripts

One-off scripts run by hand against a real AWS account (not part of any
deployed Lambda). Each is dry-run-by-default where it writes anything —
review the dry-run output before passing `--apply`.

## backfill_seat_map_category.py

**What it fixes:** a seat-upgrade tier's "Seat Map Zone" field (Admin
Dashboard → Seat Upgrades → edit tier) controls which seat-selection zone
(zone1/zone2/zone3) that tier unlocks for free seat picking once
`eventInfo.seatSelection.pricingEnabled` is off for the event. That value is
snapshotted onto `zoneBreakdown[].seatMapCategory` at the moment someone
buys the tier — deliberately, so a later change to the tier never
retroactively changes what an already-paid purchase entitles its holder to
(see `seat_upgrade_logic.price_seat_picks`'s own comment).

If a tier was left on "None" when people bought it, their purchases
snapshotted an empty `seatMapCategory`, so they hold a paid upgrade that
doesn't unlock any zone in the seat picker (zone1/2/3 render as locked for
them, identically to someone who never upgraded at all). Fixing the tier in
the admin UI only unlocks *future* purchases of it — everyone who already
paid needs their existing purchase record backfilled to match.

**Run this after fixing the tier(s) in the admin UI**, not before — it
derives the correct `seatMapCategory` per tier live from the tiers table, so
it always backfills with whatever you just set.

```bash
# Dry run first — prints every purchase it would change, writes nothing.
python3 scripts/backfill_seat_map_category.py \
  --event-type onam_2026 \
  --upgrades-table <SEAT_UPGRADES_TABLE value> \
  --tiers-table <seat-upgrade-tiers table name>

# Review the output, then actually write the fix:
python3 scripts/backfill_seat_map_category.py \
  --event-type onam_2026 \
  --upgrades-table <...> \
  --tiers-table <...> \
  --apply
```

Finding the two table names:
- Easiest: AWS Lambda console → `my_registrations` function → Configuration
  → Environment variables → `SEAT_UPGRADES_TABLE`. The tiers table isn't
  injected into any Lambda's env, but shares the same prefix/suffix.
- Or from Terraform's naming convention directly (`plugins/seat-upgrade/backend/terraform/main.tf`):
  `<name_prefix>-seat-upgrades-<suffix>` and `<name_prefix>-seat-upgrade-tiers-<suffix>`.
  `name_prefix`/`suffix` are whatever this deploy's `terraform.production.tfvars` sets them to.
- Neither is exposed via `terraform output` today — `backend/outputs.tf`
  doesn't pass the seat-upgrade module's own outputs through.

Requires AWS credentials for the production account with read on the tiers
table and read+write on the seat-upgrades (purchases) table, in the same
region as the deployed stack. Safe to re-run: it only ever touches an entry
whose `seatMapCategory` is still empty for a tier that now has one set, so
running it twice is a no-op the second time.

## Other scripts here

- `import_members.py` / `import_npo_membership.py` — one-time CSV imports
  from the legacy site into the members/npo_membership tables.
- `migrate_registration_pass_tokens.py` — backfills one opaque pass token
  per registration.
- `repair_entry_allocation_counters.py` — repairs malformed entry-allocation
  `checkedInCount` values from audit history.
- `configure_github_actions.sh` / `package_lambdas.sh` — setup/build helpers,
  not data migrations.

See each script's own docstring/`--help` for its exact usage.
