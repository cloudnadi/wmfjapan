"""
Lambda: /admin/reconciliation/*
Protected by Cognito JWT authorizer.

Payment reconciliation: the admin uploads a CSV export from a payment source
(credit card processor, bank statement, PayPay) for a single event, the rows
are normalized and stored, and "Run Reconciliation" compares the uploaded
transactions against that event's registrations to surface matches,
mismatched amounts, and anything that needs a human look.

Routes (dispatched on event["resource"], following manage_registrations.py's
substring-match convention):
  POST   /admin/reconciliation/transactions/preview   parse headers + sample rows, suggest a column mapping
  POST   /admin/reconciliation/transactions           import a CSV using a confirmed column mapping
  GET    /admin/reconciliation/transactions           list uploaded transactions + batches for an event
  DELETE /admin/reconciliation/transactions/{batchId}  undo one upload batch
  POST   /admin/reconciliation/runs                   run reconciliation for an event, persist + return the report
  GET    /admin/reconciliation/runs                   list past runs (summaries) for an event
  GET    /admin/reconciliation/runs/{runId}            fetch one run's full report (add &format=csv to export)
"""

import csv
import io
import json
import os
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

import boto3
from boto3.dynamodb.conditions import Key
from botocore.exceptions import ClientError

import matching

dynamodb = boto3.resource("dynamodb", region_name=os.environ["REGION"])

REGISTRATIONS_TABLE = os.environ["DYNAMODB_TABLE"]
TRANSACTIONS_TABLE = os.environ["PAYMENT_TRANSACTIONS_TABLE"]
RUNS_TABLE = os.environ["RECONCILIATION_RUNS_TABLE"]

VALID_SOURCE_TYPES = {"credit_card", "bank_transfer", "paypay"}
MAX_IMPORT_ROWS = 5000
PREVIEW_SAMPLE_ROWS = 5


def decimal_to_python(obj):
    if isinstance(obj, Decimal):
        return int(obj) if obj == obj.to_integral_value() else float(obj)
    raise TypeError(f"Object of type {type(obj)} is not JSON serializable")


def to_int(value) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def cors_headers():
    return {
        "Access-Control-Allow-Origin": "*",
        "Access-Control-Allow-Headers": "Content-Type,Authorization",
        "Access-Control-Allow-Methods": "OPTIONS,GET,POST,DELETE",
        "Content-Type": "application/json",
    }


def respond(status: int, body: dict):
    return {
        "statusCode": status,
        "headers": cors_headers(),
        "body": json.dumps(body, default=decimal_to_python),
    }


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def parse_json_body(event: dict[str, Any]) -> dict[str, Any]:
    if event.get("isBase64Encoded"):
        return {}
    raw_body = event.get("body") or "{}"
    try:
        parsed = json.loads(raw_body)
        return parsed if isinstance(parsed, dict) else {}
    except json.JSONDecodeError:
        return {}


def admin_identity(event: dict[str, Any]) -> str:
    claims = ((event.get("requestContext") or {}).get("authorizer") or {}).get("claims") or {}
    return str(claims.get("email") or claims.get("username") or "admin").strip()


def parse_csv_text(csv_text: str) -> tuple[list[str], list[dict]]:
    """csv.DictReader over a text blob; tolerates a leading BOM."""
    reader = csv.DictReader(io.StringIO(csv_text.lstrip("﻿")))
    headers = reader.fieldnames or []
    rows = [row for row in reader]
    return list(headers), rows


# ── /admin/reconciliation/transactions/preview ──────────────────────────────

def preview_transactions(body: dict) -> dict:
    csv_text = str(body.get("csvText", ""))
    if not csv_text.strip():
        return respond(400, {"error": "csvText is required"})

    try:
        headers, rows = parse_csv_text(csv_text)
    except Exception as exc:
        print(f"CSV preview parse error: {exc}")
        return respond(400, {"error": "Could not parse CSV"})

    if not headers:
        return respond(400, {"error": "CSV has no header row"})

    return respond(200, {
        "headers": headers,
        "sampleRows": rows[:PREVIEW_SAMPLE_ROWS],
        "totalRows": len(rows),
        "suggestedMapping": matching.suggest_column_mapping(headers),
    })


# ── /admin/reconciliation/transactions  (POST import, GET list) ─────────────

def import_transactions(body: dict, admin_email: str) -> dict:
    event_type = str(body.get("eventType", "")).strip()
    source_type = str(body.get("sourceType", "")).strip()
    csv_text = str(body.get("csvText", ""))
    column_mapping = body.get("columnMapping") or {}

    if not event_type:
        return respond(400, {"error": "eventType is required"})
    if source_type not in VALID_SOURCE_TYPES:
        return respond(400, {"error": f"sourceType must be one of {sorted(VALID_SOURCE_TYPES)}"})
    if not csv_text.strip():
        return respond(400, {"error": "csvText is required"})
    if not isinstance(column_mapping, dict) or not column_mapping.get("amount"):
        return respond(400, {"error": "columnMapping.amount is required"})

    try:
        _headers, rows = parse_csv_text(csv_text)
    except Exception as exc:
        print(f"CSV import parse error: {exc}")
        return respond(400, {"error": "Could not parse CSV"})

    if len(rows) > MAX_IMPORT_ROWS:
        return respond(400, {"error": f"CSV has {len(rows)} rows; the limit per upload is {MAX_IMPORT_ROWS}"})

    batch_id = uuid.uuid4().hex
    uploaded_at = now_iso()
    table = dynamodb.Table(TRANSACTIONS_TABLE)

    imported = 0
    skipped: list[dict] = []

    try:
        with table.batch_writer() as writer:
            for index, row in enumerate(rows, start=2):  # row 1 is the header
                normalized = matching.normalize_transaction_row(row, column_mapping)
                if normalized is None:
                    if len(skipped) < 20:
                        skipped.append({"row": index, "reason": "Missing or unparsable amount"})
                    continue

                writer.put_item(Item={
                    "eventType": event_type,
                    "transactionId": str(uuid.uuid4()),
                    "sourceType": source_type,
                    "batchId": batch_id,
                    "transactionDate": normalized["transactionDate"],
                    "amount": normalized["amount"],
                    "description": normalized["description"],
                    "payerName": normalized["payerName"],
                    "payerEmail": normalized["payerEmail"],
                    "referenceId": normalized["referenceId"],
                    "uploadedAt": uploaded_at,
                    "uploadedBy": admin_email,
                })
                imported += 1
    except ClientError as exc:
        print(f"Transaction import write error: {exc}")
        return respond(500, {"error": "Failed to save transactions"})

    return respond(200, {
        "batchId": batch_id,
        "eventType": event_type,
        "sourceType": source_type,
        "totalRows": len(rows),
        "imported": imported,
        "skipped": len(skipped) if len(skipped) < 20 else f"{len(skipped)}+",
        "skippedDetails": skipped,
    })


def list_transactions(query: dict) -> dict:
    event_type = str(query.get("eventType", "")).strip()
    if not event_type:
        return respond(400, {"error": "eventType is required"})

    source_type_filter = str(query.get("sourceType", "")).strip()

    table = dynamodb.Table(TRANSACTIONS_TABLE)
    items: list[dict] = []
    query_kwargs: dict[str, Any] = {"KeyConditionExpression": Key("eventType").eq(event_type)}

    try:
        while True:
            response = table.query(**query_kwargs)
            items.extend(response.get("Items", []))
            if "LastEvaluatedKey" not in response:
                break
            query_kwargs["ExclusiveStartKey"] = response["LastEvaluatedKey"]
    except ClientError as exc:
        print(f"Transaction list error: {exc}")
        return respond(500, {"error": "Failed to load transactions"})

    if source_type_filter:
        items = [i for i in items if i.get("sourceType") == source_type_filter]

    batches: dict[str, dict] = {}
    for item in items:
        batch_id = item.get("batchId", "")
        bucket = batches.setdefault(batch_id, {
            "batchId": batch_id,
            "sourceType": item.get("sourceType", ""),
            "uploadedAt": item.get("uploadedAt", ""),
            "uploadedBy": item.get("uploadedBy", ""),
            "count": 0,
            "totalAmount": 0,
        })
        bucket["count"] += 1
        bucket["totalAmount"] += to_int(item.get("amount", 0))

    items.sort(key=lambda i: i.get("transactionDate", ""), reverse=True)
    batch_list = sorted(batches.values(), key=lambda b: b["uploadedAt"], reverse=True)

    return respond(200, {"transactions": items, "count": len(items), "batches": batch_list})


def delete_batch(batch_id: str, query: dict) -> dict:
    event_type = str(query.get("eventType", "")).strip()
    if not batch_id:
        return respond(400, {"error": "Missing batchId"})
    if not event_type:
        return respond(400, {"error": "eventType is required"})

    table = dynamodb.Table(TRANSACTIONS_TABLE)
    try:
        response = table.query(
            IndexName="BatchIdIndex",
            KeyConditionExpression=Key("batchId").eq(batch_id),
        )
    except ClientError as exc:
        print(f"Batch lookup error: {exc}")
        return respond(500, {"error": "Failed to load batch"})

    items = [i for i in response.get("Items", []) if i.get("eventType") == event_type]
    if not items:
        return respond(404, {"error": "Batch not found for this event"})

    try:
        with table.batch_writer() as writer:
            for item in items:
                writer.delete_item(Key={"eventType": item["eventType"], "transactionId": item["transactionId"]})
    except ClientError as exc:
        print(f"Batch delete error: {exc}")
        return respond(500, {"error": "Failed to delete batch"})

    return respond(200, {"deleted": True, "batchId": batch_id, "count": len(items)})


# ── /admin/reconciliation/runs  (POST run, GET list/detail) ─────────────────

def load_registrations_for_event(event_type: str) -> list[dict]:
    table = dynamodb.Table(REGISTRATIONS_TABLE)
    items: list[dict] = []
    query_kwargs: dict[str, Any] = {
        "IndexName": "EventTypeIndex",
        "KeyConditionExpression": Key("eventType").eq(event_type),
    }
    while True:
        response = table.query(**query_kwargs)
        items.extend(response.get("Items", []))
        if "LastEvaluatedKey" not in response:
            break
        query_kwargs["ExclusiveStartKey"] = response["LastEvaluatedKey"]

    for item in items:
        item["totalAmount"] = to_int(item.get("totalAmount", 0))
        item["paidAmount"] = to_int(item.get("paidAmount", 0))
    return items


def load_transactions_for_event(event_type: str) -> list[dict]:
    table = dynamodb.Table(TRANSACTIONS_TABLE)
    items: list[dict] = []
    query_kwargs: dict[str, Any] = {"KeyConditionExpression": Key("eventType").eq(event_type)}
    while True:
        response = table.query(**query_kwargs)
        items.extend(response.get("Items", []))
        if "LastEvaluatedKey" not in response:
            break
        query_kwargs["ExclusiveStartKey"] = response["LastEvaluatedKey"]

    for item in items:
        item["amount"] = to_int(item.get("amount", 0))
    return items


def run_reconciliation(body: dict, admin_email: str) -> dict:
    event_type = str(body.get("eventType", "")).strip()
    if not event_type:
        return respond(400, {"error": "eventType is required"})

    try:
        registrations = load_registrations_for_event(event_type)
        transactions = load_transactions_for_event(event_type)
    except ClientError as exc:
        print(f"Reconciliation data load error: {exc}")
        return respond(500, {"error": "Failed to load reconciliation data"})

    report = matching.match_transactions(registrations, transactions)

    run_id = f"{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')}-{uuid.uuid4().hex[:8]}"
    record = {
        "eventType": event_type,
        "runId": run_id,
        "ranAt": now_iso(),
        "ranBy": admin_email,
        **report,
    }

    try:
        dynamodb.Table(RUNS_TABLE).put_item(Item=record)
    except ClientError as exc:
        print(f"Reconciliation run save error: {exc}")
        return respond(500, {"error": "Reconciliation ran but the report could not be saved"})

    return respond(200, record)


def list_runs(query: dict) -> dict:
    event_type = str(query.get("eventType", "")).strip()
    if not event_type:
        return respond(400, {"error": "eventType is required"})

    table = dynamodb.Table(RUNS_TABLE)
    try:
        response = table.query(
            KeyConditionExpression=Key("eventType").eq(event_type),
            ScanIndexForward=False,
        )
    except ClientError as exc:
        print(f"Run list error: {exc}")
        return respond(500, {"error": "Failed to load reconciliation runs"})

    runs = [
        {
            "eventType": item.get("eventType", ""),
            "runId": item.get("runId", ""),
            "ranAt": item.get("ranAt", ""),
            "ranBy": item.get("ranBy", ""),
            "summary": item.get("summary", {}),
        }
        for item in response.get("Items", [])
    ]
    return respond(200, {"runs": runs, "count": len(runs)})


CSV_SECTION_COLUMNS = {
    "matchedPairs": ["registrationId", "name", "email", "registrationAmount", "matchedAmount", "difference", "matchConfidence", "paymentStatus", "transactionIds", "sourceTypes"],
    "unmatchedRegistrations": ["registrationId", "name", "email", "totalAmount", "paidAmount", "paymentMethod", "paymentStatus"],
    "unmatchedTransactions": ["transactionId", "sourceType", "transactionDate", "amount", "payerName", "payerEmail", "description", "referenceId"],
}


def render_run_csv(record: dict) -> str:
    summary = record.get("summary", {})
    lines = [
        f"# Reconciliation Report",
        f"# Event: {record.get('eventType', '')}",
        f"# Run: {record.get('runId', '')} (by {record.get('ranBy', '')} at {record.get('ranAt', '')})",
        f"# Matched: {summary.get('totalMatched', 0)} | Unmatched registrations: {summary.get('totalUnmatchedRegistrations', 0)} "
        f"| Unmatched transactions: {summary.get('totalUnmatchedTransactions', 0)} | Amount mismatches: {summary.get('totalAmountMismatches', 0)}",
        f"# Net discrepancy: ¥{summary.get('netDiscrepancyAmount', 0):,}",
        "",
    ]

    for section, columns in CSV_SECTION_COLUMNS.items():
        rows = record.get(section, [])
        output = io.StringIO()
        writer = csv.DictWriter(output, fieldnames=columns, extrasaction="ignore")
        lines.append(f"## {section} ({len(rows)})")
        writer.writeheader()
        for row in rows:
            row = {k: (";".join(v) if isinstance(v, list) else v) for k, v in row.items()}
            writer.writerow(row)
        lines.append(output.getvalue().rstrip("\n"))
        lines.append("")

    return "\n".join(lines)


def get_run(run_id: str, query: dict) -> dict:
    event_type = str(query.get("eventType", "")).strip()
    if not run_id or not event_type:
        return respond(400, {"error": "eventType and runId are required"})

    try:
        item = dynamodb.Table(RUNS_TABLE).get_item(Key={"eventType": event_type, "runId": run_id}).get("Item")
    except ClientError as exc:
        print(f"Run get error: {exc}")
        return respond(500, {"error": "Failed to load reconciliation run"})

    if not item:
        return respond(404, {"error": "Reconciliation run not found"})

    if str(query.get("format", "")).strip().lower() == "csv":
        filename = f"reconciliation_{event_type}_{run_id}.csv"
        return {
            "statusCode": 200,
            "headers": {
                **cors_headers(),
                "Content-Type": "text/csv; charset=utf-8",
                "Content-Disposition": f'attachment; filename="{filename}"',
            },
            "body": render_run_csv(item),
            "isBase64Encoded": False,
        }

    return respond(200, item)


def lambda_handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    http_method = event.get("httpMethod")
    if http_method == "OPTIONS":
        return respond(200, {})

    resource = event.get("resource", "") or ""
    path_params = event.get("pathParameters") or {}
    query = event.get("queryStringParameters") or {}

    if "transactions/preview" in resource:
        if http_method != "POST":
            return respond(405, {"error": "Method not allowed"})
        return preview_transactions(parse_json_body(event))

    if "transactions/{batchId}" in resource:
        if http_method != "DELETE":
            return respond(405, {"error": "Method not allowed"})
        return delete_batch(str(path_params.get("batchId", "")).strip(), query)

    if "transactions" in resource:
        if http_method == "POST":
            return import_transactions(parse_json_body(event), admin_identity(event))
        if http_method == "GET":
            return list_transactions(query)
        return respond(405, {"error": "Method not allowed"})

    if "runs/{runId}" in resource:
        if http_method != "GET":
            return respond(405, {"error": "Method not allowed"})
        return get_run(str(path_params.get("runId", "")).strip(), query)

    if "runs" in resource:
        if http_method == "POST":
            return run_reconciliation(parse_json_body(event), admin_identity(event))
        if http_method == "GET":
            return list_runs(query)
        return respond(405, {"error": "Method not allowed"})

    return respond(404, {"error": "Not found"})
