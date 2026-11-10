"""DynamoDB access for the event-platform admin Lambda (kept apart so the handler is testable)."""

import os
from typing import Optional

import boto3
from boto3.dynamodb.conditions import Attr, Key
from boto3.dynamodb.types import TypeSerializer
from botocore.exceptions import ClientError

REGION = os.environ.get("REGION", "ap-northeast-1")
dynamodb = boto3.resource("dynamodb", region_name=REGION)
# Low-level client: the TransactWriteItems payloads below are pre-serialised (the resource client would serialise them twice).
client = boto3.client("dynamodb", region_name=REGION)
serializer = TypeSerializer()

EVENTS_TABLE = os.environ.get("EVENTS_V2_TABLE", "")
TICKETS_TABLE = os.environ.get("EVENT_TICKET_TYPES_TABLE", "")
FORMS_TABLE = os.environ.get("FORM_SCHEMAS_TABLE", "")
REGISTRATIONS_TABLE = os.environ.get("EVENT_REGISTRATIONS_TABLE", "")
AUDIT_TABLE = os.environ.get("AUDIT_LOG_TABLE", "")

SLUG_PREFIX = "SLUG#"


class RevisionConflict(Exception):
    pass


class SlugTaken(Exception):
    pass


def _table(name):
    return dynamodb.Table(name)


def _scan_all(table, **kwargs):
    items = []
    while True:
        response = table.scan(**kwargs)
        items.extend(response.get("Items", []))
        if "LastEvaluatedKey" not in response:
            return items
        kwargs["ExclusiveStartKey"] = response["LastEvaluatedKey"]


def _query_all(table, **kwargs):
    items = []
    while True:
        response = table.query(**kwargs)
        items.extend(response.get("Items", []))
        if "LastEvaluatedKey" not in response:
            return items
        kwargs["ExclusiveStartKey"] = response["LastEvaluatedKey"]


# ── events ────────────────────────────────────────────────────────────────────

def list_events() -> list:
    return _scan_all(_table(EVENTS_TABLE), FilterExpression=~Attr("eventId").begins_with(SLUG_PREFIX))


def get_event(event_id: str) -> Optional[dict]:
    return _table(EVENTS_TABLE).get_item(Key={"eventId": event_id}).get("Item")


def find_by_slug(slug: str) -> Optional[dict]:
    lock = _table(EVENTS_TABLE).get_item(Key={"eventId": SLUG_PREFIX + slug}).get("Item")
    return get_event(lock["lockedBy"]) if lock else None


def slug_taken(slug: str) -> bool:
    return bool(_table(EVENTS_TABLE).get_item(Key={"eventId": SLUG_PREFIX + slug}).get("Item"))


def save_event(item: dict, *, expected_revision: Optional[int], old_slug: str = "") -> None:
    """Write the event and its slug lock atomically.

    expected_revision None  -> create (the event must not exist)
    expected_revision N     -> update only if the stored revision is still N
    """
    event_id, slug = item["eventId"], item["slug"]
    put = {"TableName": EVENTS_TABLE, "Item": _ser(item)}
    if expected_revision is None:
        put["ConditionExpression"] = "attribute_not_exists(eventId)"
    else:
        put["ConditionExpression"] = "revision = :rev"
        put["ExpressionAttributeValues"] = {":rev": serializer.serialize(expected_revision)}
    ops = [{"Put": put}]
    if expected_revision is None or slug != old_slug:
        ops.append({"Put": {
            "TableName": EVENTS_TABLE,
            "Item": _ser({"eventId": SLUG_PREFIX + slug, "lockedBy": event_id}),
            "ConditionExpression": "attribute_not_exists(eventId) OR lockedBy = :id",
            "ExpressionAttributeValues": {":id": serializer.serialize(event_id)},
        }})
    if old_slug and old_slug != slug:
        ops.append({"Delete": {"TableName": EVENTS_TABLE, "Key": _ser({"eventId": SLUG_PREFIX + old_slug})}})
    _transact(ops)


def delete_event(item: dict) -> None:
    _transact([
        {"Delete": {"TableName": EVENTS_TABLE, "Key": _ser({"eventId": item["eventId"]})}},
        {"Delete": {"TableName": EVENTS_TABLE, "Key": _ser({"eventId": SLUG_PREFIX + item["slug"]})}},
    ])


def _ser(item: dict) -> dict:
    return {k: serializer.serialize(v) for k, v in item.items()}


def _transact(ops: list) -> None:
    try:
        client.transact_write_items(TransactItems=ops)
    except ClientError as exc:
        if exc.response["Error"]["Code"] != "TransactionCanceledException":
            raise
        reasons = exc.response.get("CancellationReasons") or []
        for op, reason in zip(ops, reasons):
            if reason.get("Code") != "ConditionalCheckFailed":
                continue
            if "Put" in op and str(op["Put"]["Item"]["eventId"]["S"]).startswith(SLUG_PREFIX):
                raise SlugTaken() from exc
            raise RevisionConflict() from exc
        raise


# ── tickets ───────────────────────────────────────────────────────────────────

def list_tickets(event_id: str) -> list:
    items = _query_all(_table(TICKETS_TABLE), KeyConditionExpression=Key("eventId").eq(event_id))
    return sorted(items, key=lambda t: (int(t.get("sortOrder", 0) or 0), t.get("createdAt", "")))


def get_ticket(event_id: str, ticket_id: str) -> Optional[dict]:
    return _table(TICKETS_TABLE).get_item(Key={"eventId": event_id, "ticketTypeId": ticket_id}).get("Item")


def put_ticket(item: dict) -> None:
    _table(TICKETS_TABLE).put_item(Item=item)


def delete_ticket(event_id: str, ticket_id: str) -> None:
    _table(TICKETS_TABLE).delete_item(Key={"eventId": event_id, "ticketTypeId": ticket_id})


# ── registrations (read-only here) ────────────────────────────────────────────

def has_registrations(event_id: str) -> bool:
    response = _table(REGISTRATIONS_TABLE).query(
        IndexName="EventIndex", KeyConditionExpression=Key("eventId").eq(event_id), Limit=1)
    return bool(response.get("Items"))


# ── forms ─────────────────────────────────────────────────────────────────────

def latest_form(form_id: str) -> Optional[dict]:
    response = _table(FORMS_TABLE).query(
        KeyConditionExpression=Key("formId").eq(form_id), ScanIndexForward=False, Limit=1)
    items = response.get("Items") or []
    return items[0] if items else None


def put_form(item: dict) -> None:
    try:
        _table(FORMS_TABLE).put_item(
            Item=item, ConditionExpression="attribute_not_exists(formId) AND attribute_not_exists(version)")
    except ClientError as exc:
        if exc.response["Error"]["Code"] == "ConditionalCheckFailedException":
            raise RevisionConflict() from exc
        raise


def list_form_versions(form_id: str) -> list:
    return _query_all(_table(FORMS_TABLE), KeyConditionExpression=Key("formId").eq(form_id),
                      ProjectionExpression="#f, #v, #c, #cb, #h, #n",
                      ExpressionAttributeNames={"#f": "formId", "#v": "version", "#c": "createdAt",
                                                "#cb": "createdBy", "#h": "schemaHash", "#n": "name"})


def get_form_version(form_id: str, version: int) -> Optional[dict]:
    return _table(FORMS_TABLE).get_item(Key={"formId": form_id, "version": version}).get("Item")


def list_templates() -> list:
    """Latest version of every template."""
    items = _query_all(_table(FORMS_TABLE), IndexName="KindIndex", KeyConditionExpression=Key("kind").eq("template"))
    latest: dict = {}
    for item in items:
        cur = latest.get(item["formId"])
        if cur is None or item["version"] > cur["version"]:
            latest[item["formId"]] = item
    return sorted(latest.values(), key=lambda i: i.get("updatedAt", ""), reverse=True)


def delete_form(form_id: str) -> None:
    for item in list_form_versions(form_id):
        _table(FORMS_TABLE).delete_item(Key={"formId": form_id, "version": item["version"]})


# ── audit ─────────────────────────────────────────────────────────────────────

def put_audit(record: dict) -> None:
    _table(AUDIT_TABLE).put_item(Item=record)


def list_audit(entity_key: str, limit: int = 100) -> list:
    response = _table(AUDIT_TABLE).query(
        KeyConditionExpression=Key("entityKey").eq(entity_key), ScanIndexForward=False, Limit=limit)
    return response.get("Items", [])
