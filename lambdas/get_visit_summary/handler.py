"""
Lambda: GET /admin/visits-summary
Returns aggregated visit analytics from the visit logs table.
"""

import json
import os
from collections import defaultdict
from datetime import datetime, timedelta, timezone

import boto3
from boto3.dynamodb.conditions import Key
from botocore.exceptions import ClientError

VISITS_TABLE = os.environ["VISITS_TABLE"]
REGION = os.environ["REGION"]
MAX_DAYS = 90
DEFAULT_DAYS = 30


dynamodb = boto3.resource("dynamodb", region_name=REGION)


def cors_headers():
    return {
        "Access-Control-Allow-Origin": "*",
        "Access-Control-Allow-Headers": "Content-Type,Authorization",
        "Access-Control-Allow-Methods": "OPTIONS,GET",
        "Content-Type": "application/json",
    }


def respond(status_code, body):
    return {
        "statusCode": status_code,
        "headers": cors_headers(),
        "body": json.dumps(body, default=str),
    }


def parse_days(event):
    raw = ((event.get("queryStringParameters") or {}).get("days") or "").strip()
    if not raw:
        return DEFAULT_DAYS
    try:
        value = int(raw)
    except ValueError:
        return DEFAULT_DAYS
    return min(MAX_DAYS, max(1, value))


def dates_for_window(days):
    today = datetime.now(timezone.utc).date()
    return [(today - timedelta(days=offset)).isoformat() for offset in range(days)]


def read_items_for_date(table, visit_date):
    items = []
    next_key = None

    while True:
        query_kwargs = {
            "KeyConditionExpression": Key("visitDate").eq(visit_date),
        }
        if next_key:
            query_kwargs["ExclusiveStartKey"] = next_key

        response = table.query(**query_kwargs)
        items.extend(response.get("Items") or [])
        next_key = response.get("LastEvaluatedKey")
        if not next_key:
            break

    return items


def lambda_handler(event, _context):
    method = (event.get("httpMethod") or "GET").upper()
    if method == "OPTIONS":
        return respond(200, {"ok": True})
    if method != "GET":
        return respond(405, {"error": "Method not allowed"})

    days = parse_days(event)
    dates = dates_for_window(days)
    table = dynamodb.Table(VISITS_TABLE)

    daily_counts = defaultdict(int)
    daily_visitors = defaultdict(set)
    path_counts = defaultdict(int)
    path_visitors = defaultdict(set)
    visitor_type_counts = defaultdict(int)
    visitor_type_visitors = defaultdict(set)
    overall_visitors = set()

    try:
        for date_key in dates:
            for item in read_items_for_date(table, date_key):
                path = (item.get("path") or "/").strip() or "/"
                visitor_id = (item.get("visitorId") or "").strip()
                visitor_type = (item.get("visitorType") or "anonymous").strip() or "anonymous"

                daily_counts[date_key] += 1
                path_counts[path] += 1
                visitor_type_counts[visitor_type] += 1

                if visitor_id:
                    daily_visitors[date_key].add(visitor_id)
                    path_visitors[path].add(visitor_id)
                    visitor_type_visitors[visitor_type].add(visitor_id)
                    overall_visitors.add(visitor_id)
    except ClientError as exc:
        return respond(500, {"error": f"Could not load visit summary: {exc}"})

    daily = [
        {
            "date": date_key,
            "visits": daily_counts[date_key],
            "uniqueVisitors": len(daily_visitors[date_key]),
        }
        for date_key in sorted(dates)
    ]

    by_path = [
        {
            "path": path,
            "visits": count,
            "uniqueVisitors": len(path_visitors[path]),
        }
        for path, count in path_counts.items()
    ]
    by_path.sort(key=lambda row: row["visits"], reverse=True)

    by_visitor_type = [
        {
            "visitorType": visitor_type,
            "visits": count,
            "uniqueVisitors": len(visitor_type_visitors[visitor_type]),
        }
        for visitor_type, count in visitor_type_counts.items()
    ]
    by_visitor_type.sort(key=lambda row: row["visits"], reverse=True)

    total_visits = sum(daily_counts.values())
    from_date = min(dates) if dates else None
    to_date = max(dates) if dates else None

    return respond(
        200,
        {
            "days": days,
            "range": {"from": from_date, "to": to_date},
            "totalVisits": total_visits,
            "uniqueVisitors": len(overall_visitors),
            "daily": daily,
            "byPath": by_path,
            "byVisitorType": by_visitor_type,
        },
    )
