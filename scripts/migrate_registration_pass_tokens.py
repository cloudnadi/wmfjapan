#!/usr/bin/env python3
"""Backfill one opaque registration pass token per registration.
TABLE=$(terraform output -raw registration_table_name)
cd ..

python3 scripts/migrate_registration_pass_tokens.py \
  --table "$TABLE" \
  --region ap-northeast-1
  
  npm --prefix frontend run build:preview

aws s3 sync frontend/dist/ s3://YOUR_PREVIEW_BUCKET/ --delete

aws cloudfront create-invalidation \
  --distribution-id YOUR_DISTRIBUTION_ID \
  --paths "/*"
"""

import argparse
import secrets
import sys

import boto3
from botocore.exceptions import ClientError


def parse_args():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--table", required=True, help="DynamoDB registrations table name")
    parser.add_argument("--region", required=True, help="AWS region")
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Write tokens. Without this flag, only report what would change.",
    )
    return parser.parse_args()


def migrate(table, apply):
    scanned = 0
    missing = 0
    updated = 0
    last_key = None

    while True:
        params = {"ProjectionExpression": "registrationId, registrationPassToken"}
        if last_key:
            params["ExclusiveStartKey"] = last_key
        response = table.scan(**params)
        for item in response.get("Items", []):
            scanned += 1
            registration_id = str(item.get("registrationId", "")).strip()
            if not registration_id or item.get("registrationPassToken"):
                continue
            missing += 1
            if not apply:
                print(f"Would migrate {registration_id}")
                continue

            token = secrets.token_urlsafe(32)
            try:
                table.update_item(
                    Key={"registrationId": registration_id},
                    UpdateExpression="SET registrationPassToken = :token",
                    ConditionExpression="attribute_not_exists(registrationPassToken)",
                    ExpressionAttributeValues={":token": token},
                )
                updated += 1
                print(f"Migrated {registration_id}")
            except ClientError as exc:
                code = exc.response.get("Error", {}).get("Code")
                if code != "ConditionalCheckFailedException":
                    raise

        last_key = response.get("LastEvaluatedKey")
        if not last_key:
            break

    return scanned, missing, updated


def main():
    args = parse_args()
    dynamodb = boto3.resource("dynamodb", region_name=args.region)
    try:
        scanned, missing, updated = migrate(dynamodb.Table(args.table), args.apply)
    except ClientError as exc:
        print(f"Migration failed: {exc}", file=sys.stderr)
        return 1

    mode = "Updated" if args.apply else "Would update"
    print(f"Scanned: {scanned}; missing tokens: {missing}; {mode}: {updated if args.apply else missing}")
    if not args.apply:
        print("Dry run only. Re-run with --apply to write tokens.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
