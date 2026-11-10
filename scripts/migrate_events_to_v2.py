#!/usr/bin/env python3
"""
Mirror the legacy events and registrations into the event-platform (v2) tables.

The legacy tables are only ever read. Mirrored items carry `source = "legacy"`
and a deterministic id (`legacy-<eventType>` / `legacy-<registrationId>`), so:

  * re-running is safe: existing mirrors are skipped (use --refresh to
    overwrite them from the legacy data again);
  * --rollback deletes exactly the `source = "legacy"` mirrors and nothing else.

Usage
-----
  cd backend
  export LEGACY_EVENTS=$(terraform output -raw events_table_name)
  export LEGACY_REGS=$(terraform output -raw registration_table_name)
  export V2_EVENTS=$(terraform output -raw events_v2_table_name)
  export V2_REGS=$(terraform output -raw event_registrations_table_name)

  python3 scripts/migrate_events_to_v2.py --dry-run          # report only
  python3 scripts/migrate_events_to_v2.py                    # write mirrors
  python3 scripts/migrate_events_to_v2.py --rollback         # remove mirrors

Table names can also be passed as --legacy-events/--legacy-registrations/
--v2-events/--v2-registrations.
"""

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lambdas"))
import event_model  # noqa: E402


def scan_all(table):
    kwargs = {}
    while True:
        response = table.scan(**kwargs)
        yield from response.get("Items", [])
        if "LastEvaluatedKey" not in response:
            return
        kwargs["ExclusiveStartKey"] = response["LastEvaluatedKey"]


def build_plan(legacy_events, legacy_registrations):
    """Pure mapping step. Returns (event_items, registration_items, problems)."""
    events, registrations, problems = [], [], []
    known = set()
    for item in legacy_events:
        if not item.get("eventType"):
            problems.append(f"event without eventType skipped: {item!r:.120}")
            continue
        events.append(event_model.legacy_event_to_v2(item))
        known.add(str(item["eventType"]))
    slugs = {}
    for ev in events:
        slugs.setdefault(ev["slug"], []).append(ev["eventId"])
    for slug, ids in slugs.items():
        if len(ids) > 1:
            problems.append(f"slug collision '{slug}': {', '.join(ids)}")
    for item in legacy_registrations:
        if not item.get("registrationId"):
            problems.append(f"registration without registrationId skipped: {item!r:.120}")
            continue
        if str(item.get("eventType", "")) not in known:
            problems.append(
                f"registration {item['registrationId']} references unknown event "
                f"'{item.get('eventType', '')}' (mirrored with a placeholder event id)"
            )
        registrations.append(event_model.legacy_registration_to_v2(item))
    return events, registrations, problems


def put_missing(table, items, key, refresh):
    """Write items; skip ones that exist unless refresh. Returns (written, skipped)."""
    from botocore.exceptions import ClientError

    written = skipped = 0
    for item in items:
        kwargs = {"Item": item}
        if not refresh:
            kwargs["ConditionExpression"] = f"attribute_not_exists({key})"
        try:
            table.put_item(**kwargs)
            written += 1
        except ClientError as exc:
            if exc.response["Error"]["Code"] == "ConditionalCheckFailedException":
                skipped += 1
            else:
                raise
    return written, skipped


def rollback(table, key):
    removed = 0
    for item in scan_all(table):
        if item.get("source") == "legacy":
            table.delete_item(Key={key: item[key]})
            removed += 1
    return removed


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--legacy-events", default=os.environ.get("LEGACY_EVENTS"))
    parser.add_argument("--legacy-registrations", default=os.environ.get("LEGACY_REGS"))
    parser.add_argument("--v2-events", default=os.environ.get("V2_EVENTS"))
    parser.add_argument("--v2-registrations", default=os.environ.get("V2_REGS"))
    parser.add_argument("--region", default=os.environ.get("AWS_REGION", "ap-northeast-1"))
    parser.add_argument("--dry-run", action="store_true", help="map and report; write nothing")
    parser.add_argument("--refresh", action="store_true", help="overwrite existing mirrors")
    parser.add_argument("--rollback", action="store_true", help="delete legacy mirrors from the v2 tables")
    args = parser.parse_args(argv)

    import boto3

    db = boto3.resource("dynamodb", region_name=args.region)
    need = ["v2_events", "v2_registrations"] if args.rollback else \
        ["legacy_events", "legacy_registrations", "v2_events", "v2_registrations"]
    missing = [n for n in need if not getattr(args, n)]
    if missing:
        parser.error("missing table names: " + ", ".join(missing))

    v2_events, v2_regs = db.Table(args.v2_events), db.Table(args.v2_registrations)

    if args.rollback:
        if args.dry_run:
            n_e = sum(1 for i in scan_all(v2_events) if i.get("source") == "legacy")
            n_r = sum(1 for i in scan_all(v2_regs) if i.get("source") == "legacy")
            print(f"[dry-run] would remove {n_e} event mirrors and {n_r} registration mirrors")
            return 0
        print(f"removed {rollback(v2_regs, 'registrationId')} registration mirrors, "
              f"{rollback(v2_events, 'eventId')} event mirrors")
        return 0

    legacy_events = list(scan_all(db.Table(args.legacy_events)))
    legacy_regs = list(scan_all(db.Table(args.legacy_registrations)))
    events, registrations, problems = build_plan(legacy_events, legacy_regs)

    print(f"legacy events: {len(legacy_events)} -> {len(events)} mirrors")
    print(f"legacy registrations: {len(legacy_regs)} -> {len(registrations)} mirrors")
    for problem in problems:
        print(f"  warning: {problem}")

    if args.dry_run:
        print("[dry-run] nothing written")
        return 0

    e_w, e_s = put_missing(v2_events, events, "eventId", args.refresh)
    r_w, r_s = put_missing(v2_regs, registrations, "registrationId", args.refresh)
    print(f"events: {e_w} written, {e_s} already present")
    print(f"registrations: {r_w} written, {r_s} already present")

    # Verify: every legacy item must now exist as a mirror.
    mirrored_events = {i["eventId"] for i in scan_all(v2_events)}
    mirrored_regs = {i["registrationId"] for i in scan_all(v2_regs)}
    lost = [e["eventId"] for e in events if e["eventId"] not in mirrored_events]
    lost += [r["registrationId"] for r in registrations if r["registrationId"] not in mirrored_regs]
    if lost:
        print(f"VERIFY FAILED: {len(lost)} items missing, e.g. {lost[:5]}")
        return 1
    print("verify OK: all legacy events and registrations are present in v2")
    return 0


if __name__ == "__main__":
    sys.exit(main())
