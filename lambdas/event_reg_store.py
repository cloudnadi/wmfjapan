"""DynamoDB store for event-platform registrations (public flow, webhook and expiry Lambdas)."""

import json
import os
from decimal import Decimal
from typing import Optional

import boto3
from boto3.dynamodb.conditions import Attr, Key
from boto3.dynamodb.types import TypeDeserializer, TypeSerializer
from botocore.exceptions import ClientError

import event_registrations as logic

REGION = os.environ.get("REGION", "ap-northeast-1")
dynamodb = boto3.resource("dynamodb", region_name=REGION)
# Low-level client: the TransactWriteItems payloads below are pre-serialised (the resource client would serialise them twice).
client = boto3.client("dynamodb", region_name=REGION)
serializer = TypeSerializer()
deserializer = TypeDeserializer()

EVENTS_TABLE = os.environ.get("EVENTS_V2_TABLE", "")
TICKETS_TABLE = os.environ.get("EVENT_TICKET_TYPES_TABLE", "")
FORMS_TABLE = os.environ.get("FORM_SCHEMAS_TABLE", "")
REGISTRATIONS_TABLE = os.environ.get("EVENT_REGISTRATIONS_TABLE", "")
COUNTERS_TABLE = os.environ.get("EVENT_COUNTERS_TABLE", "")
SITE_CONFIG_TABLE = os.environ.get("SITE_CONFIG_TABLE", "")
SLUG_PREFIX = "SLUG#"


def _t(name):
    return dynamodb.Table(name)


def _ddb(value):
    """JSON-roundtrip so floats become Decimals (DynamoDB rejects float)."""
    return json.loads(json.dumps(value, default=str), parse_float=Decimal)


def _ser(item: dict) -> dict:
    return {k: serializer.serialize(v) for k, v in _ddb(item).items()}


def _query(table, **kwargs):
    items = []
    while True:
        response = table.query(**kwargs)
        items.extend(response.get("Items", []))
        if "LastEvaluatedKey" not in response:
            return items
        kwargs["ExclusiveStartKey"] = response["LastEvaluatedKey"]


# ── catalogue (read-only) ─────────────────────────────────────────────────────

def get_event(event_id: str) -> Optional[dict]:
    return _t(EVENTS_TABLE).get_item(Key={"eventId": event_id}).get("Item")


def get_event_by_slug(slug: str) -> Optional[dict]:
    lock = _t(EVENTS_TABLE).get_item(Key={"eventId": SLUG_PREFIX + slug}).get("Item")
    return get_event(lock["lockedBy"]) if lock else None


def list_listed_events() -> list:
    items = []
    for status in ("published", "closed"):
        items += _query(_t(EVENTS_TABLE), IndexName="StatusStartIndex", KeyConditionExpression=Key("status").eq(status))
    return items


def get_tickets(event_id: str) -> list:
    items = _query(_t(TICKETS_TABLE), KeyConditionExpression=Key("eventId").eq(event_id))
    return sorted(items, key=lambda t: (int(t.get("sortOrder", 0) or 0), t.get("createdAt", "")))


def latest_form(form_id: str) -> Optional[dict]:
    items = _t(FORMS_TABLE).query(KeyConditionExpression=Key("formId").eq(form_id), ScanIndexForward=False, Limit=1)["Items"]
    return items[0] if items else None


def get_form_version(form_id: str, version: int) -> Optional[dict]:
    return _t(FORMS_TABLE).get_item(Key={"formId": form_id, "version": version}).get("Item")


def get_counters(keys: list) -> dict:
    if not keys:
        return {}
    keys = list(dict.fromkeys(keys))
    out: dict = {}
    for i in range(0, len(keys), 100):
        chunk = keys[i:i + 100]
        response = dynamodb.batch_get_item(RequestItems={COUNTERS_TABLE: {"Keys": [{"counterKey": k} for k in chunk]}})
        for item in response["Responses"].get(COUNTERS_TABLE, []):
            out[item["counterKey"]] = int(item.get("sold", 0))
    return out


# ── site config (payment methods) ─────────────────────────────────────────────

def get_site_config(key: str) -> dict:
    if not SITE_CONFIG_TABLE:
        return {}
    try:
        item = _t(SITE_CONFIG_TABLE).get_item(Key={"configKey": key}).get("Item")
        parsed = json.loads((item or {}).get("value") or "{}")
        return parsed if isinstance(parsed, dict) else {}
    except (ClientError, TypeError, ValueError):
        return {}


def put_site_config(key: str, value: dict) -> None:
    _t(SITE_CONFIG_TABLE).put_item(Item={"configKey": key, "value": json.dumps(value)})


# ── registrations ─────────────────────────────────────────────────────────────

def get_registration(reg_id: str) -> Optional[dict]:
    return _t(REGISTRATIONS_TABLE).get_item(Key={"registrationId": reg_id}).get("Item")


def find_by_provider_id(provider_id: str) -> Optional[dict]:
    items = _t(REGISTRATIONS_TABLE).query(IndexName="ProviderPaymentIndex",
                                          KeyConditionExpression=Key("providerPaymentId").eq(provider_id)).get("Items", [])
    return items[0] if items else None


def list_for_event(event_id: str) -> list:
    """Every registration of an event (admin views, exports, e-mailing)."""
    return _query(_t(REGISTRATIONS_TABLE), IndexName="EventIndex", KeyConditionExpression=Key("eventId").eq(event_id))


def list_by_email(email: str) -> list:
    """Every registration made with this address (any event)."""
    return _query(_t(REGISTRATIONS_TABLE), IndexName="EmailIndex", KeyConditionExpression=Key("email").eq(email))


def list_for_account(email: str) -> list:
    """Registrations made while signed in with this account's address."""
    return _query(_t(REGISTRATIONS_TABLE), IndexName="EmailIndex", KeyConditionExpression=Key("email").eq(email),
                  FilterExpression=Attr("accountEmail").eq(email), ScanIndexForward=False)


def list_expired_holds(iso_now: str) -> list:
    return _query(_t(REGISTRATIONS_TABLE), IndexName="StatusHoldIndex",
                  KeyConditionExpression=Key("status").eq("pending_payment") & Key("holdUntil").lte(iso_now))


def _counter_update(key: str, amount: int, limit, *, release: bool = False) -> dict:
    name = {"TableName": COUNTERS_TABLE, "Key": _ser({"counterKey": key})}
    if release:
        return {"Update": {**name, "UpdateExpression": "SET sold = sold - :n",
                           "ConditionExpression": "sold >= :n", "ExpressionAttributeValues": {":n": serializer.serialize(amount)}}}
    update = {**name, "UpdateExpression": "SET sold = if_not_exists(sold, :zero) + :n",
              "ExpressionAttributeValues": {":n": serializer.serialize(amount), ":zero": serializer.serialize(0)}}
    if limit is not None:
        update["ConditionExpression"] = "attribute_not_exists(sold) OR sold <= :max"
        update["ExpressionAttributeValues"][":max"] = serializer.serialize(limit - amount)
    return {"Update": update}


def _check_fits(ops: list) -> None:
    for key, amount, limit in ops:
        if limit is not None and amount > limit:
            raise logic.CapacityFull(logic.counter_scope(key), key)


def _transact(ops: list, *, counters: list, first: str) -> None:
    """Run a transaction whose first item is the registration; map cancellations to domain errors.

    `first` is "create" (a failed condition means the registration exists) or "update" (it changed state).
    """
    try:
        client.transact_write_items(TransactItems=ops)
    except ClientError as exc:
        if exc.response["Error"]["Code"] != "TransactionCanceledException":
            raise
        reasons = exc.response.get("CancellationReasons") or []
        if reasons and reasons[0].get("Code") == "ConditionalCheckFailed":
            raise (logic.AlreadyExists() if first == "create" else logic.StateConflict()) from exc
        for (key, _, _), reason in zip(counters, reasons[1:]):
            if reason.get("Code") == "ConditionalCheckFailed":
                raise logic.CapacityFull(logic.counter_scope(key), key) from exc
        raise


def reserve_and_create(reg: dict, ops: list) -> None:
    """Take seats and create the registration in one transaction."""
    _check_fits(ops)
    items = [{"Put": {"TableName": REGISTRATIONS_TABLE, "Item": _ser(reg),
                      "ConditionExpression": "attribute_not_exists(registrationId)"}}]
    items += [_counter_update(k, n, lim) for k, n, lim in ops]
    _transact(items, counters=ops, first="create")


def create_waitlisted(reg: dict) -> None:
    try:
        _t(REGISTRATIONS_TABLE).put_item(Item=_ddb(reg), ConditionExpression="attribute_not_exists(registrationId)")
    except ClientError as exc:
        if exc.response["Error"]["Code"] == "ConditionalCheckFailedException":
            raise logic.AlreadyExists() from exc
        raise


def _update_expression(updates: dict, remove: list, bump: bool) -> tuple:
    names, values, sets = {}, {}, []
    for i, (k, v) in enumerate(updates.items()):
        names[f"#k{i}"], values[f":v{i}"] = k, serializer.serialize(_ddb(v))
        sets.append(f"#k{i} = :v{i}")
    if bump:
        names["#rev"], values[":one"] = "revision", serializer.serialize(1)
        sets.append("#rev = #rev + :one")
    expression = "SET " + ", ".join(sets)
    if remove:
        for j, k in enumerate(remove):
            names[f"#r{j}"] = k
        expression += " REMOVE " + ", ".join(f"#r{j}" for j in range(len(remove)))
    return expression, names, values


def update_registration(reg_id: str, updates: dict, *, expect_status=None, expect_revision=None, bump=False,
                        remove=None, expect_absent=None) -> dict:
    expression, names, values = _update_expression(updates, remove or [], bump)
    conditions = ["attribute_exists(registrationId)"]
    if expect_status:
        names["#st"], values[":es"] = "status", serializer.serialize(expect_status)
        conditions.append("#st = :es")
    if expect_revision is not None:
        names["#erev"], values[":er"] = "revision", serializer.serialize(expect_revision)
        conditions.append("#erev = :er")
    if expect_absent:
        names["#ea"] = expect_absent
        conditions.append("attribute_not_exists(#ea)")
    plain = {k: deserializer.deserialize(v) if k.startswith(":") else v for k, v in values.items()}
    try:
        return _t(REGISTRATIONS_TABLE).update_item(
            Key={"registrationId": reg_id}, UpdateExpression=expression, ConditionExpression=" AND ".join(conditions),
            ExpressionAttributeNames=names, ExpressionAttributeValues=plain, ReturnValues="ALL_NEW")["Attributes"]
    except ClientError as exc:
        if exc.response["Error"]["Code"] == "ConditionalCheckFailedException":
            raise logic.StateConflict() from exc
        raise


def _reg_update_item(reg_id: str, expected_status: str, updates: dict, remove: list) -> dict:
    expression, names, values = _update_expression(updates, remove, False)
    names["#st"], values[":es"] = "status", serializer.serialize(expected_status)
    return {"Update": {"TableName": REGISTRATIONS_TABLE, "Key": _ser({"registrationId": reg_id}),
                       "UpdateExpression": expression, "ConditionExpression": "#st = :es",
                       "ExpressionAttributeNames": names, "ExpressionAttributeValues": values}}


def release(reg_id: str, expected_status: str, updates: dict, ops: list, remove=None) -> dict:
    """End a registration and return its seats, atomically. `ops` is [(counter_key, amount)]."""
    remove = remove or []
    if not ops:
        return update_registration(reg_id, updates, expect_status=expected_status, remove=remove)
    items = [_reg_update_item(reg_id, expected_status, updates, remove)]
    items += [_counter_update(k, n, None, release=True) for k, n in ops]
    _transact(items, counters=[(k, n, None) for k, n in ops], first="update")
    return get_registration(reg_id)


def reserve_existing(reg_id: str, expected_status: str, updates: dict, ops: list, remove=None) -> dict:
    """Take seats for a registration that exists but holds none (waitlist promotion, late payment)."""
    _check_fits(ops)
    items = [_reg_update_item(reg_id, expected_status, updates, remove or [])]
    items += [_counter_update(k, n, lim) for k, n, lim in ops]
    _transact(items, counters=ops, first="update")
    return get_registration(reg_id)


# ── rate limiting ─────────────────────────────────────────────────────────────

def rate_limit_allows(bucket: str, limit: int, window_seconds: int, now_epoch: int) -> bool:
    """Fixed-window counter. Expired windows are removed by the table's TTL on `expiresAt`."""
    window = now_epoch // window_seconds
    response = _t(COUNTERS_TABLE).update_item(
        Key={"counterKey": f"RL#{bucket}#{window}"},
        UpdateExpression="ADD hits :one SET expiresAt = :exp",
        ExpressionAttributeValues={":one": 1, ":exp": (window + 2) * window_seconds},
        ReturnValues="UPDATED_NEW")
    return int(response["Attributes"]["hits"]) <= limit
