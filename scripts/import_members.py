#!/usr/bin/env python3
"""
Import the members master list from the legacy site CSV export into the
DynamoDB members table. Login is gated against this table (see the auth_otp
Lambda): only emails present here receive an OTP.

Only a lean subset of the CSV's columns is stored — enough to gate login,
personalize the OTP email, and reflect NPO status:

    email (key), memberid, fullname, phone, mobile, statusid,
    npo_membship_active, npo_memb_interested

Usage
-----
  # Resolve the table name from terraform, then import:
  TABLE=$(terraform -chdir=backend output -raw members_table_name)
  python3 scripts/import_members.py --csv members.csv --table "$TABLE" --region ap-northeast-1

  # Preview without writing:
  python3 scripts/import_members.py --csv members.csv --table "$TABLE" --dry-run

Notes
-----
* Emails are lowercased/trimmed and used as the hash key. Blank-email rows and
  duplicate emails (last one wins) are skipped/collapsed with a log line.
* Boolean-ish flags (npo_membship_active, npo_memb_interested) are coerced to
  Python bools so the Lambda can test them directly.
* Re-running is safe: rows are upserted by email (put overwrites).
"""

import argparse
import csv
import sys

# CSV column -> DynamoDB attribute. Only these are imported.
STRING_COLUMNS = {
    "memberid": "memberid",
    "fullname": "fullname",
    "phone": "phone",
    "mobile": "mobile",
    "statusid": "statusid",
}
BOOL_COLUMNS = {
    "npo_membship_active": "npoMembershipActive",
    "npo_memb_interested": "npoMembInterested",
}

TRUTHY = {"1", "true", "t", "yes", "y", "active"}


def to_bool(value):
    return str(value or "").strip().lower() in TRUTHY


def build_item(row):
    """Map a CSV row (dict) to a DynamoDB item, or None if it has no email."""
    email = (row.get("email") or "").strip().lower()
    if not email:
        return None

    item = {"email": email}

    for csv_col, attr in STRING_COLUMNS.items():
        value = (row.get(csv_col) or "").strip()
        if value:
            item[attr] = value

    for csv_col, attr in BOOL_COLUMNS.items():
        item[attr] = to_bool(row.get(csv_col))

    return item


def read_items(csv_path):
    """Parse the CSV into deduped items keyed by email (last row wins)."""
    seen = {}
    skipped = 0
    with open(csv_path, newline="", encoding="utf-8-sig") as fh:
        reader = csv.DictReader(fh)
        if "email" not in (reader.fieldnames or []):
            sys.exit(f"error: CSV has no 'email' column (found: {reader.fieldnames})")
        for row in reader:
            item = build_item(row)
            if item is None:
                skipped += 1
                continue
            seen[item["email"]] = item
    if skipped:
        print(f"  skipped {skipped} row(s) with a blank email")
    return list(seen.values())


def main():
    parser = argparse.ArgumentParser(description="Import members CSV into DynamoDB.")
    parser.add_argument("--csv", required=True, help="Path to the members CSV export")
    parser.add_argument("--table", required=True, help="DynamoDB members table name")
    parser.add_argument("--region", default="ap-northeast-1", help="AWS region")
    parser.add_argument("--dry-run", action="store_true", help="Parse and report, but do not write")
    args = parser.parse_args()

    print(f"Reading {args.csv} ...")
    items = read_items(args.csv)
    print(f"  {len(items)} unique member(s) to import")

    if args.dry_run:
        for sample in items[:5]:
            print(f"    {sample}")
        print("Dry run — nothing written.")
        return

    # Imported lazily so --dry-run works without boto3 installed.
    import boto3
    from botocore.exceptions import ClientError

    table = boto3.resource("dynamodb", region_name=args.region).Table(args.table)

    written = 0
    try:
        with table.batch_writer(overwrite_by_pkeys=["email"]) as batch:
            for item in items:
                batch.put_item(Item=item)
                written += 1
                if written % 500 == 0:
                    print(f"  wrote {written} ...")
    except ClientError as exc:
        sys.exit(f"error: DynamoDB write failed after {written} item(s): {exc}")

    print(f"Done. Imported {written} member(s) into {args.table}.")


if __name__ == "__main__":
    main()
