#!/usr/bin/env python3
"""Repair malformed entry-allocation checkedInCount values from audit history."""

import argparse
import sys
from collections import defaultdict
from decimal import Decimal

import boto3
from botocore.exceptions import ClientError


def parse_args():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--allocations-table", required=True)
    parser.add_argument("--check-ins-table", required=True)
    parser.add_argument("--region", required=True)
    parser.add_argument("--apply", action="store_true", help="Write repairs; otherwise run a dry run")
    return parser.parse_args()


def scan_all(table):
    items = []
    params = {}
    while True:
        response = table.scan(**params)
        items.extend(response.get("Items", []))
        if "LastEvaluatedKey" not in response:
            return items
        params["ExclusiveStartKey"] = response["LastEvaluatedKey"]


def as_int(value):
    if isinstance(value, dict):
        for key in ("value", "N", "checkedInCount"):
            if key in value:
                return as_int(value[key])
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def main():
    args = parse_args()
    dynamodb = boto3.resource("dynamodb", region_name=args.region)
    allocations_table = dynamodb.Table(args.allocations_table)
    check_ins_table = dynamodb.Table(args.check_ins_table)

    audit_totals = defaultdict(int)
    for check_in in scan_all(check_ins_table):
        quantity = as_int(check_in.get("quantity", 0))
        if quantity is not None:
            audit_totals[check_in.get("allocationId", "")] += quantity

    malformed = 0
    repaired = 0
    for allocation in scan_all(allocations_table):
        current = allocation.get("checkedInCount", 0)
        if not isinstance(current, dict):
            continue
        allocation_id = allocation.get("allocationId", "")
        audit_count = audit_totals.get(allocation_id, 0)
        seat_count = as_int(allocation.get("seatCount", 0)) or 0
        if audit_count > seat_count:
            print(f"ERROR {allocation_id}: audit total {audit_count} exceeds seatCount {seat_count}", file=sys.stderr)
            return 1
        malformed += 1
        print(f"{('Repairing' if args.apply else 'Would repair')} {allocation_id}: {audit_count}")
        if not args.apply:
            continue
        try:
            allocations_table.update_item(
                Key={"eventType": allocation["eventType"], "allocationId": allocation_id},
                UpdateExpression="SET checkedInCount = :count",
                ConditionExpression="attribute_type(checkedInCount, :map_type)",
                ExpressionAttributeValues={":count": audit_count, ":map_type": "M"},
            )
            repaired += 1
        except ClientError as exc:
            if exc.response.get("Error", {}).get("Code") != "ConditionalCheckFailedException":
                raise

    print(f"Malformed counters: {malformed}; repaired: {repaired if args.apply else 0}")
    if not args.apply:
        print("Dry run only. Re-run with --apply to write repairs.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
