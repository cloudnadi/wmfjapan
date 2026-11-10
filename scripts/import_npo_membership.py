#!/usr/bin/env python3
"""
Import NPO membership transactions from the legacy site CSV export into the
DynamoDB npo_membership table.

Why a separate "legacy transaction id"
--------------------------------------
Legacy transaction numbers are only unique within the old system. The new site
numbers its own transactions per member (1, 2, 3, ...), and a later legacy file
can reuse numbers, so using the legacy number as the table key could overwrite
a real transaction. Instead each imported row keeps the legacy number in its own
attribute, `legacyTransactionId`, and gets a fresh `transactionNo` from the new
site's numbering:

  * a row whose (memberid, legacy id) was imported before is UPDATED in place
    (re-running the same or an overlapping file never creates duplicates);
  * an older import that stored the legacy number as `transactionNo` is ADOPTED
    (stamped with `legacyTransactionId`) when it is clearly the same record
    (same number, dates, paid date, method and amount) — never when it looks like a different one;
  * anything else is INSERTED with the member's next free transactionNo.

Records created on the site (or by an admin) are never modified.

Expected CSV columns (header row required; the transaction column may also be
named legacy_transaction_id or transaction_id):

    memberid, transaction_no, membership_amount_paid, paid_date,
    memb_datefrom, memb_dateto, paidvia

Usage
-----
  TABLE=$(terraform -chdir=backend output -raw npo_membership_table_name)
  python3 scripts/import_npo_membership.py --csv npo_membership.csv --table "$TABLE" --dry-run
  python3 scripts/import_npo_membership.py --csv npo_membership.csv --table "$TABLE"

--dry-run reads the table (read-only) and prints exactly what would be inserted,
updated or adopted; it writes nothing.
"""

import argparse
import csv
import sys
from collections import OrderedDict
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation

LEGACY_ID_COLUMNS = ("transaction_no", "legacy_transaction_id", "transaction_id")
STRING_COLUMNS = {
    "paid_date": "paidDate",
    "memb_datefrom": "membDateFrom",
    "memb_dateto": "membDateTo",
    "paidvia": "paidVia",
}
NUMBER_COLUMNS = {"membership_amount_paid": "membershipAmountPaid"}
IMPORT_SOURCE = "legacy-import"


def to_number(value):
    text = str(value or "").strip()
    if not text:
        return Decimal(0)
    try:
        return Decimal(text)
    except (InvalidOperation, ValueError):
        return Decimal(0)


def build_item(row):
    """CSV row -> import item (without a transactionNo yet), or None if a key is missing."""
    memberid = str(row.get("memberid") or "").strip()
    legacy_id = next((str(row[c]).strip() for c in LEGACY_ID_COLUMNS if str(row.get(c) or "").strip()), "")
    if not memberid or not legacy_id:
        return None
    item = {"memberid": memberid, "legacyTransactionId": legacy_id}
    for csv_col, attr in STRING_COLUMNS.items():
        value = str(row.get(csv_col) or "").strip()
        if value:
            item[attr] = value
    for csv_col, attr in NUMBER_COLUMNS.items():
        item[attr] = to_number(row.get(csv_col))
    return item


def read_items(csv_path):
    """Parse the CSV into items, one per (memberid, legacy id) — the last row wins."""
    seen = OrderedDict()
    skipped = 0
    with open(csv_path, newline="", encoding="utf-8-sig") as fh:
        reader = csv.DictReader(fh)
        fields = reader.fieldnames or []
        if "memberid" not in fields or not any(c in fields for c in LEGACY_ID_COLUMNS):
            sys.exit(f"error: CSV needs a memberid column and one of {LEGACY_ID_COLUMNS} (found: {fields})")
        for row in reader:
            item = build_item(row)
            if item is None:
                skipped += 1
                continue
            seen[(item["memberid"], item["legacyTransactionId"])] = item
    if skipped:
        print(f"  skipped {skipped} row(s) missing memberid / transaction id")
    return list(seen.values())


def _num(value):
    try:
        return int(Decimal(str(value)))
    except (InvalidOperation, ValueError, TypeError):
        return None


def _same_record(existing, item):
    """True when an older import's record is clearly this row (so it can be adopted)."""
    return (
        not existing.get("legacyTransactionId")
        and str(existing.get("transactionNo")) == item["legacyTransactionId"]
        and str(existing.get("membDateFrom", "")) == str(item.get("membDateFrom", ""))
        and str(existing.get("membDateTo", "")) == str(item.get("membDateTo", ""))
        and str(existing.get("paidDate", "")) == str(item.get("paidDate", ""))
        and str(existing.get("paidVia", "")) == str(item.get("paidVia", ""))
        and _num(existing.get("membershipAmountPaid")) == _num(item.get("membershipAmountPaid"))
    )


def plan_import(items, existing_by_member):
    """Decide what to do with each item. Pure function (no AWS), so it is unit-tested.

    existing_by_member: {memberid: [existing transaction dicts]}
    Returns a list of actions: {"action": "insert"|"update"|"adopt", "key": (memberid, transactionNo), "item": {...}}
    """
    actions = []
    for item in items:
        memberid = item["memberid"]
        existing = existing_by_member.setdefault(memberid, [])
        match = next((t for t in existing if t.get("legacyTransactionId") == item["legacyTransactionId"]), None)
        kind = "update"
        if match is None:
            match = next((t for t in existing if _same_record(t, item)), None)
            kind = "adopt"
        if match is not None:
            key = (memberid, str(match["transactionNo"]))
            match.update({k: v for k, v in item.items() if k != "memberid"})  # keep the in-memory view current
            actions.append({"action": kind, "key": key, "item": item})
            continue
        highest = max((n for n in (_num(t.get("transactionNo")) for t in existing) if n is not None), default=0)
        new_no = str(highest + 1)
        record = {**item, "transactionNo": new_no, "source": IMPORT_SOURCE}
        existing.append(record)
        actions.append({"action": "insert", "key": (memberid, new_no), "item": record})
    return actions


def load_existing(table):
    existing = {}
    kwargs = {}
    while True:
        page = table.scan(**kwargs)
        for t in page.get("Items", []):
            existing.setdefault(str(t["memberid"]), []).append(t)
        if "LastEvaluatedKey" not in page:
            return existing
        kwargs["ExclusiveStartKey"] = page["LastEvaluatedKey"]


def apply_action(table, action, now):
    from botocore.exceptions import ClientError  # noqa: F401  (imported lazily with boto3)

    memberid, txn_no = action["key"]
    item = action["item"]
    if action["action"] == "insert":
        table.put_item(
            Item={**item, "importedAt": now},
            ConditionExpression="attribute_not_exists(memberid) AND attribute_not_exists(transactionNo)",
        )
        return
    # update / adopt: only touch the imported attributes; never replace the record.
    attrs = {k: v for k, v in item.items() if k not in ("memberid", "transactionNo", "source")}
    names = {f"#a{i}": k for i, k in enumerate(attrs)}
    values = {f":v{i}": v for i, v in enumerate(attrs.values())}
    values[":now"] = now
    expr = "SET " + ", ".join(f"#a{i} = :v{i}" for i in range(len(attrs))) + ", importedAt = :now"
    table.update_item(
        Key={"memberid": memberid, "transactionNo": txn_no},
        UpdateExpression=expr,
        ExpressionAttributeNames=names,
        ExpressionAttributeValues=values,
        ConditionExpression="attribute_exists(memberid)",
    )


def main():
    parser = argparse.ArgumentParser(description="Import legacy NPO membership transactions into DynamoDB.")
    parser.add_argument("--csv", required=True, help="Path to the legacy NPO membership CSV export")
    parser.add_argument("--table", required=True, help="DynamoDB npo_membership table name")
    parser.add_argument("--region", default="ap-northeast-1", help="AWS region")
    parser.add_argument("--dry-run", action="store_true", help="Read the table and report, but do not write")
    args = parser.parse_args()

    print(f"Reading {args.csv} ...")
    items = read_items(args.csv)
    print(f"  {len(items)} unique legacy transaction(s) in the file")

    import boto3  # lazily, so --help works without it

    table = boto3.resource("dynamodb", region_name=args.region).Table(args.table)
    existing = load_existing(table)
    print(f"  {sum(len(v) for v in existing.values())} existing transaction(s) in {args.table}")
    actions = plan_import(items, existing)

    counts = {k: sum(1 for a in actions if a["action"] == k) for k in ("insert", "update", "adopt")}
    print(f"Plan: {counts['insert']} new, {counts['update']} update (already imported), {counts['adopt']} adopt (older import)")
    for a in actions[:5]:
        print(f"    {a['action']:6} {a['key']}  legacy id {a['item']['legacyTransactionId']}")
    if args.dry_run:
        print("Dry run — nothing written.")
        return

    from botocore.exceptions import ClientError

    now = datetime.now(timezone.utc).isoformat()
    done = 0
    for a in actions:
        try:
            apply_action(table, a, now)
        except ClientError as exc:
            sys.exit(f"error: write failed at {a['key']} after {done} item(s): {exc}")
        done += 1
        if done % 500 == 0:
            print(f"  wrote {done} ...")
    print(f"Done. {counts['insert']} inserted, {counts['update']} updated, {counts['adopt']} adopted in {args.table}.")


if __name__ == "__main__":
    main()
