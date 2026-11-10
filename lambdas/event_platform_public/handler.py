"""
Lambda: /public/{proxy+}  — public event pages, registration, and attendee self-service.

Public (no sign-in; guests allowed where the event allows it):
  GET  /events?filter=upcoming|past|all&category=        listed events
  GET  /events/{slug}                                    event page data (+ viewer pricing if signed in)
  POST /events/{slug}/quote                              server-side price for a ticket selection
  POST /events/{slug}/upload                             upload a file for a file field
  POST /events/{slug}/register                           create a registration
  GET  /registrations/{id}?token=                        the registrant's own registration (check-in token)
  POST /registrations/{id}/pay?token=                    (re)start payment

Signed-in account (Authorization: a Cognito attendee-pool ID token):
  GET    /my/registrations
  GET    /my/registrations/{id}
  PUT    /my/registrations/{id}       edit answers (until registration closes)
  DELETE /my/registrations/{id}       cancel (until registration closes)
"""

import base64
import hashlib
import hmac
import json
import logging
import os
import time
import uuid
from datetime import datetime
from decimal import Decimal
from typing import Optional

import cognito_jwt
import event_addons
import event_capabilities
import event_mailer
import event_model
import event_pricing
import event_registrations as logic
import event_reg_store as store

try:
    import square
except Exception:  # pragma: no cover - the zip always ships square.py; keeps unit tests importable
    square = None

import boto3
from botocore.exceptions import ClientError

logger = logging.getLogger(__name__)
logger.setLevel(os.environ.get("LOG_LEVEL", "INFO").upper())

FRONTEND_URL = os.environ.get("FRONTEND_URL", "").rstrip("/")
UPLOADS_BUCKET = os.environ.get("ASSETS_BUCKET", "")
# Attendee accounts live in their own Cognito user pool (never the admin pool: the admin API
# treats every user of its pool as an administrator).
ATTENDEE_POOL_ID = os.environ.get("ATTENDEE_USER_POOL_ID", "")
ATTENDEE_CLIENT_ID = os.environ.get("ATTENDEE_APP_CLIENT_ID", "")
ATTENDEE_REGION = os.environ.get("ATTENDEE_COGNITO_REGION") or os.environ.get("REGION", "ap-northeast-1")

UPLOAD_TYPES = {"application/pdf": b"%PDF-", "image/jpeg": b"\xff\xd8\xff", "image/png": b"\x89PNG\r\n\x1a\n",
                "image/webp": b"RIFF"}
MAX_BODY_BYTES = 6_000_000

# (bucket name, limit, window seconds). Fixed windows keyed on IP or e-mail.
LIMITS = {"register-ip": (10, 600), "register-email": (5, 3600), "upload-ip": (20, 600), "quote-ip": (60, 60),
          "pay-ip": (20, 600), "lookup-ip": (60, 60)}

_s3 = None
_verifier = None


class ApiError(Exception):
    def __init__(self, status, message, fields=None, code="", headers=None):
        super().__init__(message)
        self.status, self.message, self.fields, self.code, self.headers = status, message, fields or {}, code, headers or {}


def now():
    return datetime.now(event_model.JST)


def s3():
    global _s3
    _s3 = _s3 or boto3.client("s3", region_name=os.environ.get("REGION", "ap-northeast-1"))
    return _s3


def _default(obj):
    if isinstance(obj, Decimal):
        return int(obj) if obj == obj.to_integral_value() else float(obj)
    raise TypeError(type(obj))


def respond(status, body, headers=None):
    return {"statusCode": status, "body": json.dumps(body, default=_default), "headers": {
        "Access-Control-Allow-Origin": "*", "Access-Control-Allow-Headers": "Content-Type,Authorization",
        "Access-Control-Allow-Methods": "OPTIONS,GET,POST,PUT,DELETE", "Content-Type": "application/json",
        "Cache-Control": "no-store", **(headers or {})}}


def parse_body(event):
    raw = event.get("body") or "{}"
    if event.get("isBase64Encoded") or len(raw) > MAX_BODY_BYTES:
        raise ApiError(400, "Unsupported or oversized request body")
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        raise ApiError(400, "Body must be valid JSON")
    if not isinstance(parsed, dict):
        raise ApiError(400, "Body must be a JSON object")
    return parsed


# ── identity ──────────────────────────────────────────────────────────────────

def verifier() -> "cognito_jwt.CognitoVerifier":
    global _verifier
    if _verifier is None:
        _verifier = cognito_jwt.CognitoVerifier(ATTENDEE_REGION, ATTENDEE_POOL_ID, ATTENDEE_CLIENT_ID)
    return _verifier


def _identity_from_token(token: str, strict: bool) -> Optional[dict]:
    """{email, sub, name} for a valid Cognito ID token with a verified e-mail; None when it is not valid."""
    try:
        claims = verifier().verify_id_token(token)
    except cognito_jwt.TokenError as exc:
        logger.info("rejected attendee token: %s", exc.code)          # reason code only, never the token
        return None
    identity = cognito_jwt.identity_from_claims(claims)
    if identity is None and strict:
        raise ApiError(401, "Please verify your email address before registering.", code="email_unverified")
    return identity


def resolve_account(event, *, strict: bool) -> Optional[dict]:
    """The signed-in attendee ({email, sub, name}), or None for guests.

    A token that is present but invalid is an error when `strict` (so nobody is silently registered
    as a guest because their session expired); otherwise it is ignored.
    """
    headers = {str(k).lower(): v for k, v in (event.get("headers") or {}).items()}
    token = headers.get("authorization", "").strip()
    if token.lower().startswith("bearer "):
        token = token[7:].strip()
    if not token:
        return None
    identity = _identity_from_token(token, strict)
    if not identity and strict:
        raise ApiError(401, "Your session has expired. Please sign in again.", code="session_expired")
    return identity


def require_account(event) -> dict:
    account = resolve_account(event, strict=True)
    if not account:
        raise ApiError(401, "Please sign in", code="login_required")
    return account


def client_ip(event) -> str:
    return str(((event.get("requestContext") or {}).get("identity") or {}).get("sourceIp") or "unknown")


def throttle(name: str, subject: str):
    limit, window = LIMITS[name]
    digest = hashlib.sha256(subject.encode()).hexdigest()[:24]            # never store raw IPs / e-mail addresses
    try:
        allowed = store.rate_limit_allows(f"{name}#{digest}", limit, window, int(time.time()))
    except Exception:
        logger.exception("rate limiter unavailable; allowing request")
        return
    if not allowed:
        raise ApiError(429, "Too many requests. Please wait a few minutes and try again.", code="rate_limited",
                       headers={"Retry-After": str(window)})


# ── payment methods ───────────────────────────────────────────────────────────

def payment_methods() -> dict:
    return logic.available_payment_methods(store, square)


def payment_info(reg: dict, methods: dict) -> dict:
    method = reg.get("paymentMethod")
    info = {"method": method, "status": reg.get("paymentStatus"), "holdUntil": reg.get("holdUntil"),
            "checkoutUrl": reg.get("checkoutUrl")}
    if reg["status"] == "pending_payment" and method in ("bank", "paypay"):
        info.update(instructions=(methods.get(method) or {}).get("instructions", ""),
                    url=(methods.get(method) or {}).get("url", ""))
    return info


def _notify(kind, reg, event):
    event_mailer.send(kind, reg, event, payment_methods())


# ── views ─────────────────────────────────────────────────────────────────────

def lite_event(event: dict, *, attendee: bool = False) -> dict:
    location = dict(event.get("location") or {})
    online = location.pop("onlineUrl", "")
    if attendee and online:
        location["onlineUrl"] = online
    return {"eventId": event["eventId"], "slug": event["slug"], "title": event["title"], "status": event["status"],
            "startsAt": event.get("startsAt"), "endsAt": event.get("endsAt"), "location": location,
            "registrationClosesAt": event.get("registrationClosesAt"), "hasOnlineLink": bool(online)}


def listing_view(event: dict) -> dict:
    base = lite_event(event)
    base.update({"shortDescription": event.get("shortDescription", ""), "category": event.get("category", ""),
                 "tags": event.get("tags", []), "coverImage": (event.get("design") or {}).get("coverImage", ""),
                 "accentColor": (event.get("design") or {}).get("accentColor", ""),
                 "guestsAllowed": event_capabilities.guest_checkout_enabled(event),
                 "capabilities": event_capabilities.normalize(event)})
    return base


def event_page(event, tickets, form, counters, account, when) -> dict:
    availability = event_pricing.availability(event, tickets, counters)
    decision = event_pricing.access_decision(event, bool(account))
    window = event_pricing.registration_window(event, when)
    shown = []
    for t in tickets:
        if t.get("active") is False:
            continue
        price, early = event_pricing.unit_price(t, when)
        cutoff = event_model.parse_iso(t.get("earlyBirdCutoff"))
        shown.append({k: t.get(k) for k in ("ticketTypeId", "name", "description", "priceJpy", "earlyBirdPriceJpy",
                                            "earlyBirdCutoff", "perPersonCap")} | {
            "earlyBirdOpen": bool(t.get("earlyBirdPriceJpy") is not None and cutoff and when < cutoff),
            "yourPriceJpy": price, "yourPriceIsEarlyBird": early,
            "remaining": availability["tickets"].get(t["ticketTypeId"])})
    page = listing_view(event)
    page.update({
        "longDescription": event.get("longDescription", ""), "design": event.get("design") or {},
        "waitlistEnabled": bool(event.get("waitlistEnabled")), "access": event.get("access") or {},
        "registrationOpensAt": event.get("registrationOpensAt"), "tickets": shown,
        "availability": {"full": availability["full"], "eventRemaining": availability["eventRemaining"]},
        "registration": {"window": window, "allowed": decision["allowed"], "reason": decision["reason"],
                         "waitlist": bool(availability["full"] and event.get("waitlistEnabled"))},
        "viewer": {"loggedIn": bool(account), "email": (account or {}).get("email", ""), "name": (account or {}).get("name", "")},
        "addons": event_addons.public_view(event, counters),
        "paymentMethods": [{"key": m["key"], "label": m["label"]} for m in payment_methods().values()],
        "form": {"formId": form["formId"], "version": form["version"], "fields": event_model.ensure_core_fields(form["fields"])} if form else None,
    })
    return page


def load_public_event(slug: str) -> dict:
    event = store.get_event_by_slug(slug)
    if not event or not event_pricing.is_publicly_visible(event):
        raise ApiError(404, "Event not found")
    return event


def registrant_view(reg: dict, event: Optional[dict], methods: dict) -> dict:
    out = {"registration": logic.public_registration(reg), "payment": payment_info(reg, methods)}
    if event:
        out["event"] = lite_event(event, attendee=reg["status"] == "confirmed")
    if reg["status"] == "confirmed":
        out["qr"] = event_mailer.qr_payload(reg)
    return out


# ── handlers ──────────────────────────────────────────────────────────────────

def list_events(query):
    when = now()
    filter_ = query.get("filter", "upcoming")
    category = query.get("category", "")
    listed = store.list_listed_events()
    events = [e for e in listed if event_pricing.is_listed(e)]
    if category:
        events = [e for e in events if e.get("category", "").lower() == category.lower()]

    def end(e):
        return event_model.parse_iso(e.get("endsAt")) or event_model.parse_iso(e.get("startsAt"))

    def is_past(e):
        finish = end(e)
        return bool(finish and finish < when)

    upcoming = sorted((e for e in events if not is_past(e)), key=lambda e: e.get("startsAt") or "9999")
    past = sorted((e for e in events if is_past(e)), key=lambda e: e.get("startsAt") or "", reverse=True)
    chosen = {"upcoming": upcoming, "past": past}.get(filter_, upcoming + past)
    categories = sorted({e.get("category") for e in listed if e.get("category")})
    return 200, {"events": [listing_view(e) for e in chosen], "categories": categories}


def get_event(slug, event):
    item = load_public_event(slug)
    account = resolve_account(event, strict=False)
    tickets = store.get_tickets(item["eventId"])
    form = store.latest_form(item["formId"]) if item.get("formId") else None
    keys = [f"EVENT#{item['eventId']}"] + [f"TICKET#{item['eventId']}#{t['ticketTypeId']}" for t in tickets]
    keys += event_addons.counter_keys(item)
    page = event_page(item, tickets, form, store.get_counters(keys), account, now())
    return 200, {"event": page}


def quote(slug, event):
    throttle("quote-ip", client_ip(event))
    item = load_public_event(slug)
    body = parse_body(event)
    lines, total, errors = event_pricing.quote(store.get_tickets(item["eventId"]), body.get("tickets"), now())
    if errors:
        raise ApiError(400, "Please check your tickets", errors)
    unavailable = event_capabilities.reject_unavailable_addons(item, body)
    if unavailable:
        raise ApiError(400, "Some options are not available for this event", unavailable, code="feature_disabled")
    _, addon_jpy, _, addon_errors = event_addons.resolve(item, body, sum(l["qty"] for l in lines))
    if addon_errors:
        raise ApiError(400, "Please check your add-ons", addon_errors, code="addons_invalid")
    return 200, {"lines": lines, "ticketsJpy": total, "addonJpy": addon_jpy, "totalJpy": total + addon_jpy}


def _walk_files(fields, answers, found, prefix=""):
    for field in fields:
        key = field["key"]
        if field["type"] == "file" and answers.get(key) is not None:
            found.append((f"{prefix}{key}", field, answers, key))
        elif field["type"] == "group" and isinstance(answers.get(key), list):
            for i, entry in enumerate(answers[key]):
                if isinstance(entry, dict):
                    _walk_files(field["fields"], entry, found, f"{prefix}{key}[{i}].")


def _promote_upload(event_id: str, file_id: str) -> dict:
    """Return the stored object's metadata, moving it out of the self-expiring pending area on first use."""
    final = f"event-uploads/{event_id}/{file_id}"
    try:
        return s3().head_object(Bucket=UPLOADS_BUCKET, Key=final)
    except ClientError:
        pass
    pending = f"event-uploads-pending/{event_id}/{file_id}"
    s3().copy_object(Bucket=UPLOADS_BUCKET, Key=final, CopySource={"Bucket": UPLOADS_BUCKET, "Key": pending},
                     MetadataDirective="COPY", ServerSideEncryption="AES256")
    return s3().head_object(Bucket=UPLOADS_BUCKET, Key=final)


def verify_uploads(item, form, answers):
    """Replace client-supplied file metadata with what is actually stored (and refuse foreign files).

    Uploads land in a pending prefix that S3 empties after two days; a file that is attached to a
    registration is copied to the permanent prefix here, so unused uploads never accumulate.
    """
    found: list = []
    if isinstance(answers, dict):
        _walk_files(form["fields"], answers, found)
    problems = {}
    for path, field, holder, key in found:
        ref = holder[key]
        file_id = ref.get("fileId") if isinstance(ref, dict) else None
        if not isinstance(file_id, str) or not file_id.isalnum():
            problems[path] = "Upload the file again"
            continue
        try:
            head = _promote_upload(item["eventId"], file_id)
        except ClientError:
            problems[path] = "Upload the file again"
            continue
        holder[key] = {"fileId": file_id, "size": int(head["ContentLength"]), "contentType": head["ContentType"],
                       "name": (head.get("Metadata") or {}).get("name", "file")}
    if problems:
        raise ApiError(400, "Please check the highlighted answers", problems)


def upload(slug, event):
    throttle("upload-ip", client_ip(event))
    item = load_public_event(slug)
    body = parse_body(event)
    form = store.latest_form(item.get("formId", ""))
    flat = event_model.flatten_fields(form["fields"]) if form else {}
    field = next((f for path, f in flat.items() if path.split(".")[-1] == body.get("fieldKey") and f["type"] == "file"), None)
    if field is None:
        raise ApiError(400, "This form has no such file field")
    rules = field.get("validation") or {}
    content_type = body.get("contentType", "")
    if content_type not in UPLOAD_TYPES or content_type not in rules.get("allowedTypes", []):
        raise ApiError(400, "That file type is not allowed")
    try:
        data = base64.b64decode(body.get("data", ""), validate=True)
    except Exception:
        raise ApiError(400, "Invalid file data")
    limit = int(float(rules.get("maxSizeMb", 0)) * 1024 * 1024)
    if not data or len(data) > min(limit, event_model.MAX_UPLOAD_MB * 1024 * 1024):
        raise ApiError(400, f"File must be {rules.get('maxSizeMb')} MB or smaller")
    magic = UPLOAD_TYPES[content_type]
    if not data.startswith(magic) or (content_type == "image/webp" and data[8:12] != b"WEBP"):
        raise ApiError(400, "The file content does not match its type")
    name = "".join(c if c.isalnum() or c in " .-_()" else "_" for c in str(body.get("name", "file")))[:200] or "file"
    file_id = uuid.uuid4().hex
    # Private bucket prefix: CloudFront only serves /media/*, so uploads are never publicly reachable.
    s3().put_object(Bucket=UPLOADS_BUCKET, Key=f"event-uploads-pending/{item['eventId']}/{file_id}", Body=data,
                    ContentType=content_type, ContentDisposition="attachment", Metadata={"name": name},
                    ServerSideEncryption="AES256")
    return 201, {"fileId": file_id, "name": name, "size": len(data), "contentType": content_type}


def register(slug, event):
    ip = client_ip(event)
    throttle("register-ip", ip)
    item = load_public_event(slug)
    body = parse_body(event)
    account = resolve_account(event, strict=True)
    email = str(((body.get("answers") or {}).get("email") if isinstance(body.get("answers"), dict) else "") or "").strip().lower()
    throttle("register-email", (account or {}).get("email") or email or ip)
    tickets = store.get_tickets(item["eventId"])
    form = store.latest_form(item.get("formId", ""))
    if not form:
        raise ApiError(409, "This event is not ready for registration")
    verify_uploads(item, form, body.get("answers"))
    methods = payment_methods()
    reg, created = logic.register(event=item, tickets=tickets, form=form, body=body, account=account, methods=methods,
                                  now=now(), redirect_base=FRONTEND_URL)
    return (201 if created else 200), registrant_view(reg, item, methods)


def _load_by_token(reg_id, event):
    reg = store.get_registration(reg_id)
    token = (event.get("queryStringParameters") or {}).get("token", "")
    if not reg or not token or not hmac.compare_digest(reg["checkInToken"], token):
        raise ApiError(404, "Registration not found")
    return reg


def reconcile_square(reg, item):
    """The card redirect can beat the webhook: ask Square once whether the order is paid."""
    if reg["status"] != "pending_payment" or reg.get("paymentMethod") != "square" or not reg.get("providerPaymentId"):
        return reg
    try:
        result = square.get_payment_status(reg["providerPaymentId"])
    except Exception:
        return reg
    if result.get("status") == "COMPLETED":
        paid = ((result.get("raw") or {}).get("order") or {}).get("total_money", {}).get("amount")
        if not logic.payment_amount_ok(reg, paid):
            return logic.flag_payment_mismatch(reg, paid, now())
        return logic.mark_paid(reg, via="square", reference=result.get("paymentId", ""), now=now(), event=item)
    return reg


def get_registration_by_token(reg_id, event):
    throttle("lookup-ip", client_ip(event))
    reg = _load_by_token(reg_id, event)
    item = store.get_event(reg["eventId"])
    reg = reconcile_square(reg, item)
    return 200, registrant_view(reg, item, payment_methods())


def pay(reg_id, event):
    throttle("pay-ip", client_ip(event))
    reg = _load_by_token(reg_id, event)
    item = store.get_event(reg["eventId"])
    body = parse_body(event)
    methods = payment_methods()
    reg = logic.begin_payment(reg, item, body.get("paymentMethod"), methods, now(), FRONTEND_URL)
    return 200, registrant_view(reg, item, methods)


def my_list(event):
    account = require_account(event)
    rows = []
    cache = {}
    for reg in sorted(store.list_for_account(account["email"]), key=lambda r: r.get("createdAt", ""), reverse=True):
        item = cache.setdefault(reg["eventId"], store.get_event(reg["eventId"]))
        view = registrant_view(reg, item, {})
        view["canChange"] = _can_change(reg, item)
        rows.append(view)
    return 200, {"registrations": rows}


def _can_change(reg, item):
    return bool(item and reg["status"] in logic.HOLDING + ("waitlisted",)
                and event_pricing.registration_window(item, now()) == event_pricing.WINDOW_OPEN)


def _my_registration(reg_id, account):
    reg = store.get_registration(reg_id)
    if not reg or reg.get("accountEmail") != account["email"]:
        raise ApiError(404, "Registration not found")
    return reg, store.get_event(reg["eventId"])


def my_get(reg_id, event):
    account = require_account(event)
    reg, item = _my_registration(reg_id, account)
    methods = payment_methods()
    view = registrant_view(reg, item, methods)
    view["canChange"] = _can_change(reg, item)
    version = store.get_form_version(reg["formId"], int(reg["formVersion"]))
    view["form"] = {"version": reg["formVersion"], "fields": event_model.ensure_core_fields(version["fields"])} if version else None
    return 200, view


def my_edit(reg_id, event):
    account = require_account(event)
    reg, item = _my_registration(reg_id, account)
    body = parse_body(event)
    version = store.get_form_version(reg["formId"], int(reg["formVersion"]))
    if not version:
        raise ApiError(409, "The form this registration used is no longer available")
    verify_uploads(item, version, body.get("answers"))
    updated = logic.account_edit(reg, item, version, body.get("answers"), account, now())
    return 200, registrant_view(updated, item, payment_methods())


def my_cancel(reg_id, event):
    account = require_account(event)
    reg, item = _my_registration(reg_id, account)
    updated = logic.account_cancel(reg, item, account, now())
    return 200, registrant_view(updated, item, payment_methods())


# ── routing ───────────────────────────────────────────────────────────────────

def route(event):
    method = event.get("httpMethod", "")
    parts = [p for p in str((event.get("pathParameters") or {}).get("proxy", "")).split("/") if p]
    query = event.get("queryStringParameters") or {}
    if parts[:1] == ["events"]:
        if len(parts) == 1 and method == "GET":
            return list_events(query)
        if len(parts) == 2 and method == "GET":
            return get_event(parts[1], event)
        if len(parts) == 3 and method == "POST":
            action = {"quote": quote, "upload": upload, "register": register}.get(parts[2])
            if action:
                return action(parts[1], event)
    elif parts[:1] == ["registrations"]:
        if len(parts) == 2 and method == "GET":
            return get_registration_by_token(parts[1], event)
        if len(parts) == 3 and parts[2] == "pay" and method == "POST":
            return pay(parts[1], event)
    elif parts[:2] == ["my", "registrations"]:
        if len(parts) == 2 and method == "GET":
            return my_list(event)
        if len(parts) == 3:
            action = {"GET": my_get, "PUT": my_edit, "DELETE": my_cancel}.get(method)
            if action:
                return action(parts[2], event)
    raise ApiError(404, "Not found")


def lambda_handler(event, context):
    logic.store, logic.payments, logic.notify = store, square, _notify
    if event.get("httpMethod") == "OPTIONS":
        return respond(200, {})
    try:
        status, body = route(event)
        return respond(status, body)
    except ApiError as exc:
        return respond(exc.status, {"error": exc.message, "fields": exc.fields, "code": exc.code}, exc.headers)
    except logic.RegError as exc:
        return respond(exc.status, {"error": exc.message, "fields": exc.fields, "code": exc.code})
    except ClientError:
        logger.exception("AWS error")
        return respond(500, {"error": "Request failed"})
