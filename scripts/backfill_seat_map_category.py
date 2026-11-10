#!/usr/bin/env python3
"""One-off backfill for the zone1/zone2/zone3 seat-selection bug.

Root cause: a seat-upgrade tier's "Seat Map Zone" field was left on "None"
when people bought it, so seat_upgrade_logic.price_seat_picks snapshotted an
empty seatMapCategory onto their purchase's zoneBreakdown at that time
(deliberately — a later tier fix never retroactively changes what an
already-paid purchase entitles its holder to, see that function's own
comment). Fixing the tier in the admin UI only unlocks *future* purchases.
This script finds every EXISTING purchase whose zoneBreakdown still has an
empty seatMapCategory for a tier that NOW has one set, and backfills it to
match — so people who already paid get their entitlement without having to
re-purchase.

Run this AFTER fixing the tier(s) in Admin Dashboard -> Seat Upgrades, so it
backfills with whatever you just set.

Dry-run by default. Nothing is written until you pass --apply.

Usage:
  python3 backfill_seat_map_category.py --event-type onam_2026
  python3 backfill_seat_map_category.py --event-type onam_2026 --apply

Requires AWS credentials (for the production account) with read access on
the seat-upgrade tiers table and read+write on the seat-upgrades (purchases)
table. Get the exact table names from the my_registrations Lambda's
environment variables (SEAT_UPGRADES_TABLE) in the AWS console, or from
`terraform output` if the stack exposes them, and pass them via
--upgrades-table/--tiers-table or the env vars below.
"""
import argparse
import os

import boto3
from boto3.dynamodb.conditions import Key


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--event-type", required=True, help="e.g. onam_2026")
    parser.add_argument("--upgrades-table", default=os.environ.get("SEAT_UPGRADES_TABLE", ""))
    parser.add_argument("--tiers-table", default=os.environ.get("SEAT_UPGRADE_TIERS_TABLE", ""))
    parser.add_argument("--region", default=os.environ.get("AWS_REGION", "ap-northeast-1"))
    parser.add_argument("--apply", action="store_true", help="Actually write changes (default: dry-run only)")
    args = parser.parse_args()

    if not args.upgrades_table or not args.tiers_table:
        raise SystemExit(
            "Pass --upgrades-table and --tiers-table, or set SEAT_UPGRADES_TABLE / "
            "SEAT_UPGRADE_TIERS_TABLE env vars to the real DynamoDB table names."
        )

    dynamodb = boto3.resource("dynamodb", region_name=args.region)
    tiers_table = dynamodb.Table(args.tiers_table)
    upgrades_table = dynamodb.Table(args.upgrades_table)

    # Current (just-fixed) seatMapCategory per tierId for this event.
    tier_categories = {}
    resp = tiers_table.query(KeyConditionExpression=Key("eventType").eq(args.event_type))
    for tier in resp.get("Items", []):
        cat = str(tier.get("seatMapCategory", "") or "").strip()
        if cat:
            tier_categories[tier["tierId"]] = cat

    print(f"Tiers with a Seat Map Zone set for {args.event_type}: {tier_categories}")
    if not tier_categories:
        print("No tiers have a seatMapCategory set yet — fix the tier(s) in the admin UI first, then re-run this.")
        return

    changed = 0
    scanned = 0
    last_key = None
    while True:
        kwargs = {
            "IndexName": "EventTypeIndex",
            "KeyConditionExpression": Key("eventType").eq(args.event_type),
        }
        if last_key:
            kwargs["ExclusiveStartKey"] = last_key
        resp = upgrades_table.query(**kwargs)
        for item in resp.get("Items", []):
            scanned += 1
            breakdown = item.get("zoneBreakdown", []) or []
            updated_breakdown = []
            item_changed = False
            for zone in breakdown:
                tier_id = zone.get("tierId", "")
                current_cat = str(zone.get("seatMapCategory", "") or "").strip()
                if not current_cat and tier_id in tier_categories:
                    new_zone = dict(zone)
                    new_zone["seatMapCategory"] = tier_categories[tier_id]
                    updated_breakdown.append(new_zone)
                    item_changed = True
                else:
                    updated_breakdown.append(zone)
            if item_changed:
                changed += 1
                tag = "[APPLY]" if args.apply else "[DRY-RUN]"
                print(
                    f"{tag} upgradeId={item['upgradeId']} registrationId={item.get('registrationId')} "
                    f"status={item.get('status')} -> "
                    f"{[{'tierId': z['tierId'], 'seatMapCategory': z['seatMapCategory']} for z in updated_breakdown]}"
                )
                if args.apply:
                    upgrades_table.update_item(
                        Key={"upgradeId": item["upgradeId"]},
                        UpdateExpression="SET zoneBreakdown = :zb",
                        ExpressionAttributeValues={":zb": updated_breakdown},
                    )
        last_key = resp.get("LastEvaluatedKey")
        if not last_key:
            break

    print(f"\nScanned {scanned} purchase(s) for {args.event_type}; "
          f"{'updated' if args.apply else 'would update'} {changed}.")
    if not args.apply:
        print("Dry run only — re-run with --apply to actually write these changes.")


if __name__ == "__main__":
    main()
