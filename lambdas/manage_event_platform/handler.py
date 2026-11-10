"""
Lambda: /admin/event-platform/{proxy+}   (Cognito-authorized; roles checked here)

  GET    /me                                   caller's roles
  GET    /events                               list (optional ?status=)
  POST   /events                               create (draft)
  GET    /events/{id}                          event + tickets + form summary
  PUT    /events/{id}                          update (needs the current `revision`)
  DELETE /events/{id}                          super admin only, only without registrations
  POST   /events/{id}/status                   {status}  draft|published|unlisted|closed|archived
  POST   /events/{id}/duplicate                copy event, tickets and latest form as a draft
  GET    /events/{id}/tickets                  list ticket types
  POST   /events/{id}/tickets                  create ticket type
  PUT    /events/{id}/tickets/{ticketId}       update
  DELETE /events/{id}/tickets/{ticketId}       delete (deactivate instead once registrations exist)
  POST   /events/{id}/images                   {contentType, data(base64)} -> {url}
  GET    /events/{id}/audit                    change history
  GET    /events/{id}/form                     latest registration form + version list
  PUT    /events/{id}/form                     save a new immutable form version {fields, baseVersion}
  GET    /events/{id}/form/versions/{v}        one stored version
  POST   /events/{id}/form/validate            run the server-side validator on sample answers
  POST   /events/{id}/form/apply               replace the form from {templateId} or {copyFromEventId}
  GET    /form-templates                       reusable form templates
  POST   /form-templates                       save {name, fields} or {name, fromEventId} as a template
  GET    /form-templates/{id}                  one template
  DELETE /form-templates/{id}                  super admin only

"""

import base64
import json
import os
import time
import uuid
from decimal import Decimal

import admin_roles
import event_exports
import event_mailer
import event_model
import event_registration_admin as radm
import event_registrations as logic
import event_reg_store as regstore
import form_engine
import store

try:
    import square
except Exception:  # pragma: no cover - the deployed zip always ships square.py
    square = None

import boto3
from botocore.exceptions import ClientError

ASSETS_BUCKET = os.environ.get("ASSETS_BUCKET", "")
ASSETS_BASE_URL = os.environ.get("ASSETS_BASE_URL", "").rstrip("/")
_s3 = None

IMAGE_TYPES = {"image/jpeg": (".jpg", b"\xff\xd8\xff"), "image/png": (".png", b"\x89PNG\r\n\x1a\n"),
               "image/webp": (".webp", b"RIFF")}
MAX_IMAGE_BYTES = 3 * 1024 * 1024
MAX_GALLERY = 12
BULK_BATCH = 100
MAX_EXPORT_ROWS = 20000


class ApiError(Exception):
    def __init__(self, status, message, fields=None):
        super().__init__(message)
        self.status, self.message, self.fields = status, message, fields or {}


def s3():
    global _s3
    if _s3 is None:
        _s3 = boto3.client("s3", region_name=os.environ.get("REGION", "ap-northeast-1"))
    return _s3


def _default(obj):
    if isinstance(obj, Decimal):
        return int(obj) if obj == obj.to_integral_value() else float(obj)
    raise TypeError(type(obj))


def respond(status, body):
    return {
        "statusCode": status,
        "headers": {
            "Access-Control-Allow-Origin": "*",
            "Access-Control-Allow-Headers": "Content-Type,Authorization",
            "Access-Control-Allow-Methods": "OPTIONS,GET,POST,PUT,DELETE",
            "Content-Type": "application/json",
            "Cache-Control": "no-store",
        },
        "body": json.dumps(body, default=_default),
    }


def parse_body(event):
    if event.get("isBase64Encoded"):
        raise ApiError(400, "Unsupported body encoding")
    try:
        parsed = json.loads(event.get("body") or "{}", parse_float=Decimal)
    except json.JSONDecodeError:
        raise ApiError(400, "Body must be valid JSON")
    if not isinstance(parsed, dict):
        raise ApiError(400, "Body must be a JSON object")
    return _plain(parsed)


def _plain(value):
    """Decimals that are whole numbers become ints so validators see JSON-like values."""
    if isinstance(value, Decimal):
        return int(value) if value == value.to_integral_value() else value
    if isinstance(value, dict):
        return {k: _plain(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_plain(v) for v in value]
    return value


# ── helpers ───────────────────────────────────────────────────────────────────

def new_id(prefix, length=12):
    return f"{prefix}_{uuid.uuid4().hex[:length]}"


def load_event(event_id):
    item = store.get_event(event_id)
    if not item:
        raise ApiError(404, "Event not found")
    return item


def audit(entity_key, action, actor, summary, changes=None):
    now = event_model.now_iso()
    record = {"entityKey": entity_key, "sortKey": f"{now}#{uuid.uuid4().hex[:6]}",
              "action": action, "actor": actor, "summary": summary, "at": now}
    if changes:
        record["changes"] = changes
    try:
        store.put_audit(record)
    except ClientError:
        # An audit failure must not hide a change that already happened; surface it in the logs.
        print(f"AUDIT WRITE FAILED: {record!r}")


def diff(old, new):
    out = {}
    for key in event_model.EDITABLE_EVENT_KEYS:
        if old.get(key) != new.get(key):
            out[key] = {"from": _clip(old.get(key)), "to": _clip(new.get(key))}
    return out


def _clip(value):
    text = json.dumps(value, default=_default, sort_keys=True)
    return text if len(text) <= 300 else text[:300] + "…"


def parse_expected_revision(body):
    revision = body.get("revision")
    if not isinstance(revision, int) or isinstance(revision, bool):
        raise ApiError(400, "revision is required (reload the event and try again)")
    return revision


def save(item, expected_revision, old_slug=""):
    try:
        store.save_event(item, expected_revision=expected_revision, old_slug=old_slug)
    except store.SlugTaken:
        raise ApiError(409, "That URL slug is already used by another event", {"slug": "Already in use"})
    except store.RevisionConflict:
        raise ApiError(409, "This event was changed by someone else. Reload it and apply your edits again.")


def event_view(item):
    return {k: v for k, v in item.items()}


# ── handlers ──────────────────────────────────────────────────────────────────

def list_events(event, query):
    items = store.list_events()
    status = query.get("status")
    if status:
        items = [i for i in items if i.get("status") == status]
    items.sort(key=lambda i: (i.get("startsAt") or "9999", i.get("title", "").lower()))
    return 200, {"events": [event_view(i) for i in items], "count": len(items)}


def create_event(event, body):
    actor = admin_roles.actor_of(event)
    errors = event_model.validate_event({**body, "status": "draft"})
    if errors:
        raise ApiError(400, "Invalid event details", errors)
    event_id = new_id("evt")
    item = event_model.new_event(event_id, body, created_by=actor)
    form = event_model.new_form_version(
        new_id("form"), None, event_model.default_form_fields(), event_id=event_id, name=item["title"], created_by=actor)
    item["formId"] = form["formId"]
    save(item, None)
    store.put_form(form)
    audit(f"EVENT#{event_id}", "create", actor, f"Created event '{item['title']}'")
    return 201, {"event": event_view(item)}


def get_event(event_id):
    item = load_event(event_id)
    return 200, {"event": event_view(item), "tickets": store.list_tickets(event_id),
                 "hasRegistrations": store.has_registrations(event_id)}


def update_event(event, event_id, body):
    actor = admin_roles.actor_of(event)
    existing = load_event(event_id)
    expected = parse_expected_revision(body)
    updated = event_model.apply_event_edit(existing, body)
    errors = event_model.validate_event(updated)
    if errors:
        raise ApiError(400, "Invalid event details", errors)
    updated["revision"] = expected + 1
    save(updated, expected, old_slug=existing.get("slug", ""))
    changes = diff(existing, updated)
    audit(f"EVENT#{event_id}", "update", actor, f"Updated {', '.join(changes) or 'event'}", changes)
    return 200, {"event": event_view(updated)}


def set_status(event, event_id, body):
    actor = admin_roles.actor_of(event)
    existing = load_event(event_id)
    target = body.get("status")
    if target not in event_model.EVENT_STATUSES:
        raise ApiError(400, "Invalid status", {"status": "Must be one of " + ", ".join(event_model.EVENT_STATUSES)})
    if target in ("published", "unlisted"):
        errors = event_model.validate_event({**existing, "status": target})
        if not any(t.get("active", True) for t in store.list_tickets(event_id)):
            errors["tickets"] = "Add at least one active ticket type before publishing"
        if errors:
            raise ApiError(400, "This event is not ready to publish", errors)
    expected = existing.get("revision", 1)
    updated = {**existing, "status": target, "revision": expected + 1, "updatedAt": event_model.now_iso()}
    save(updated, expected, old_slug=existing.get("slug", ""))
    audit(f"EVENT#{event_id}", "status", actor, f"Status {existing.get('status')} -> {target}")
    return 200, {"event": event_view(updated)}


def delete_event(event, event_id):
    if not admin_roles.has_role(event, admin_roles.SUPER_ADMIN):
        raise ApiError(403, "Only a super admin can delete events. Archive it instead.")
    existing = load_event(event_id)
    if store.has_registrations(event_id):
        raise ApiError(409, "This event has registrations and cannot be deleted. Archive it instead.")
    for ticket in store.list_tickets(event_id):
        store.delete_ticket(event_id, ticket["ticketTypeId"])
    store.delete_event(existing)
    audit(f"EVENT#{event_id}", "delete", admin_roles.actor_of(event), f"Deleted event '{existing.get('title')}'")
    return 200, {"deleted": True, "eventId": event_id}


def duplicate_event(event, event_id):
    actor = admin_roles.actor_of(event)
    source = load_event(event_id)
    new_event_id = new_id("evt")
    slug = base = f"{source['slug']}-copy"
    n = 2
    while store.slug_taken(slug):
        slug, n = f"{base}-{n}", n + 1
    now = event_model.now_iso()
    item = {k: v for k, v in source.items() if k != "createdBy"}
    item.update({"eventId": new_event_id, "slug": slug, "title": f"{source['title']} (copy)", "status": "draft",
                 "revision": 1, "createdAt": now, "updatedAt": now, "createdBy": actor})
    item.pop("formId", None)
    previous_form = store.latest_form(source["formId"]) if source.get("formId") else None
    fields = previous_form["fields"] if previous_form else event_model.default_form_fields()
    form = event_model.new_form_version(
        new_id("form"), None, fields, event_id=new_event_id, name=item["title"], created_by=actor)
    item["formId"] = form["formId"]
    save(item, None)
    store.put_form(form)
    for ticket in store.list_tickets(event_id):
        store.put_ticket({**ticket, "eventId": new_event_id, "ticketTypeId": new_id("tkt", 10),
                          "createdAt": now, "updatedAt": now})
    audit(f"EVENT#{new_event_id}", "duplicate", actor, f"Duplicated from '{source.get('title')}' ({event_id})")
    return 201, {"event": event_view(item)}


def ticket_item(event_id, ticket_id, body, existing=None):
    now = event_model.now_iso()
    errors = event_model.validate_ticket_type(body)
    if errors:
        raise ApiError(400, "Invalid ticket type", errors)
    item = {
        "eventId": event_id, "ticketTypeId": ticket_id,
        "name": body["name"].strip(), "description": str(body.get("description", "")).strip(),
        "priceJpy": body["priceJpy"],
        "sortOrder": int(body.get("sortOrder", 0) or 0), "active": bool(body.get("active", True)),
        "createdAt": (existing or {}).get("createdAt", now), "updatedAt": now,
    }
    for key in ("earlyBirdPriceJpy", "earlyBirdCutoff", "quantityLimit", "perPersonCap"):
        if body.get(key) not in (None, ""):
            item[key] = body[key]
    return item


def save_ticket(event, event_id, ticket_id, body):
    actor = admin_roles.actor_of(event)
    existing_event = load_event(event_id)
    existing = store.get_ticket(event_id, ticket_id) if ticket_id else None
    if ticket_id and not existing:
        raise ApiError(404, "Ticket type not found")
    item = ticket_item(event_id, ticket_id or new_id("tkt", 10), body, existing)
    store.put_ticket(item)
    audit(f"EVENT#{event_id}", "ticket", actor,
          f"{'Updated' if existing else 'Added'} ticket type '{item['name']}'")
    return (200 if existing else 201), {"ticket": item}


def delete_ticket(event, event_id, ticket_id):
    existing_event = load_event(event_id)
    ticket = store.get_ticket(event_id, ticket_id)
    if not ticket:
        raise ApiError(404, "Ticket type not found")
    if store.has_registrations(event_id):
        raise ApiError(409, "This event has registrations. Deactivate the ticket type instead of deleting it.")
    store.delete_ticket(event_id, ticket_id)
    audit(f"EVENT#{event_id}", "ticket", admin_roles.actor_of(event), f"Deleted ticket type '{ticket.get('name')}'")
    return 200, {"deleted": True}


def upload_image(event, event_id, body):
    existing = load_event(event_id)
    content_type = body.get("contentType", "")
    if content_type not in IMAGE_TYPES:
        raise ApiError(400, "Unsupported image type. Allowed: " + ", ".join(sorted(IMAGE_TYPES)))
    try:
        data = base64.b64decode(body.get("data", ""), validate=True)
    except Exception:
        raise ApiError(400, "Invalid image data")
    if not data or len(data) > MAX_IMAGE_BYTES:
        raise ApiError(400, f"Image must be 1 byte to {MAX_IMAGE_BYTES // (1024 * 1024)} MB")
    ext, magic = IMAGE_TYPES[content_type]
    if not data.startswith(magic) or (content_type == "image/webp" and data[8:12] != b"WEBP"):
        raise ApiError(400, "The file content does not match its image type")
    if not ASSETS_BUCKET or not ASSETS_BASE_URL:
        raise ApiError(500, "Image storage is not configured")
    # Served by CloudFront under /media/*; keys are random so they cannot be guessed or overwritten.
    key = f"media/events/{event_id}/{int(time.time())}-{uuid.uuid4().hex[:8]}{ext}"
    s3().put_object(Bucket=ASSETS_BUCKET, Key=key, Body=data, ContentType=content_type,
                    CacheControl="public, max-age=31536000, immutable")
    audit(f"EVENT#{event_id}", "image", admin_roles.actor_of(event), f"Uploaded {key.rsplit('/', 1)[-1]}")
    return 201, {"url": f"{ASSETS_BASE_URL}/{key}"}



# ── registration form ─────────────────────────────────────────────────────────

MAX_FORM_VERSIONS = 200
MAX_TEMPLATES = 200


def form_problems(problems):
    return {str(i + 1): text for i, text in enumerate(problems[:30])}


def load_form(item):
    form = store.latest_form(item["formId"]) if item.get("formId") else None
    if not form:
        raise ApiError(404, "This event has no registration form")
    return form


def save_form_version(event, event_id, item, raw_fields, base_version, summary):
    """Validate and store raw_fields as the next version of the event's form."""
    actor = admin_roles.actor_of(event)
    previous = load_form(item)
    if base_version != previous["version"]:
        raise ApiError(409, "The form was changed by someone else. Reload it and apply your edits again.")
    if previous["version"] >= MAX_FORM_VERSIONS:
        raise ApiError(409, "This form has reached the maximum number of versions")
    if not isinstance(raw_fields, list):
        raise ApiError(400, "fields must be a list")
    fields = event_model.ensure_core_fields(raw_fields)
    problems = event_model.validate_form_schema({"fields": fields})
    if store.has_registrations(event_id):
        problems += event_model.check_schema_evolution(previous["fields"], fields)
    if problems:
        raise ApiError(400, "The form has problems", form_problems(problems))
    version = event_model.new_form_version(item["formId"], previous, fields, event_id=event_id, created_by=actor)
    if version is None:
        return previous, False
    try:
        store.put_form(version)
    except store.RevisionConflict:
        raise ApiError(409, "The form was changed by someone else. Reload it and apply your edits again.")
    audit(f"EVENT#{event_id}", "form", actor, f"{summary} (version {version['version']})",
          event_model.diff_forms(previous["fields"], fields))
    return version, True


def form_response(item, form):
    return {"form": form, "versions": [{k: v for k, v in x.items() if k != "fields"}
                                       for x in store.list_form_versions(item["formId"])],
            "hasRegistrations": store.has_registrations(item["eventId"])}


def get_form(event_id):
    item = load_event(event_id)
    return 200, form_response(item, load_form(item))


def put_form(event, event_id, body):
    item = load_event(event_id)
    base = body.get("baseVersion")
    if not isinstance(base, int) or isinstance(base, bool):
        raise ApiError(400, "baseVersion is required (reload the form and try again)")
    form, changed = save_form_version(event, event_id, item, body.get("fields"), base, "Saved form")
    return 200, {**form_response(item, form), "changed": changed}


def get_form_version(event_id, version):
    item = load_event(event_id)
    try:
        number = int(version)
    except ValueError:
        raise ApiError(404, "Version not found")
    form = store.get_form_version(item.get("formId", ""), number) if item.get("formId") else None
    if not form:
        raise ApiError(404, "Version not found")
    return 200, {"form": form}


def validate_sample(event_id, body):
    item = load_event(event_id)
    form = load_form(item)
    clean, errors = form_engine.validate_submission(form, body.get("answers"), {})
    return 200, {"clean": clean, "errors": errors, "version": form["version"]}


def template_summary(t):
    return {"templateId": t["formId"], "name": t.get("name", ""), "version": t["version"],
            "updatedAt": t.get("updatedAt", ""), "fieldCount": len(t.get("fields", []))}


def source_fields(body):
    """Fields from {templateId} or {copyFromEventId}."""
    if body.get("templateId"):
        template = store.latest_form(str(body["templateId"]))
        if not template or template.get("kind") != "template":
            raise ApiError(404, "Template not found")
        return template["fields"], f"template '{template.get('name', '')}'"
    if body.get("copyFromEventId"):
        source = load_event(str(body["copyFromEventId"]))
        return load_form(source)["fields"], f"the form of '{source.get('title', '')}'"
    raise ApiError(400, "Provide templateId or copyFromEventId")


def apply_form_source(event, event_id, body):
    item = load_event(event_id)
    base = body.get("baseVersion")
    if not isinstance(base, int) or isinstance(base, bool):
        raise ApiError(400, "baseVersion is required (reload the form and try again)")
    fields, origin = source_fields(body)
    form, changed = save_form_version(event, event_id, item, fields, base, f"Replaced form with {origin}")
    return 200, {**form_response(item, form), "changed": changed}


def list_templates():
    return 200, {"templates": [template_summary(t) for t in store.list_templates()]}


def create_template(event, body):
    actor = admin_roles.actor_of(event)
    name = str(body.get("name", "")).strip()
    if not name or len(name) > 100:
        raise ApiError(400, "Template name is required (up to 100 characters)", {"name": "Required"})
    if len(store.list_templates()) >= MAX_TEMPLATES:
        raise ApiError(409, "Too many templates. Delete some first.")
    raw = body.get("fields")
    if body.get("fromEventId"):
        source = load_event(str(body["fromEventId"]))
        raw = load_form(source)["fields"]
    if not isinstance(raw, list):
        raise ApiError(400, "Provide fields or fromEventId")
    fields = event_model.ensure_core_fields(raw)
    problems = event_model.validate_form_schema({"fields": fields})
    if problems:
        raise ApiError(400, "The form has problems", form_problems(problems))
    template = event_model.new_form_version(new_id("tpl"), None, fields, kind="template", name=name, created_by=actor)
    store.put_form(template)
    audit(f"TEMPLATE#{template['formId']}", "create", actor, f"Saved form template '{name}'")
    return 201, {"template": template_summary(template)}


def get_template(template_id):
    template = store.latest_form(template_id)
    if not template or template.get("kind") != "template":
        raise ApiError(404, "Template not found")
    return 200, {"template": {**template_summary(template), "fields": template["fields"]}}


def delete_template(event, template_id):
    if not admin_roles.has_role(event, admin_roles.SUPER_ADMIN):
        raise ApiError(403, "Only a super admin can delete form templates")
    template = store.latest_form(template_id)
    if not template or template.get("kind") != "template":
        raise ApiError(404, "Template not found")
    store.delete_form(template_id)
    audit(f"TEMPLATE#{template_id}", "delete", admin_roles.actor_of(event), f"Deleted form template '{template.get('name', '')}'")
    return 200, {"deleted": True}


# ── registration management ───────────────────────────────────────────────────

def _wire(event_id=None):
    """Point the shared registration logic at the registrations store and the e-mail sender."""
    logic.store, logic.payments = regstore, square
    methods = {}

    def notify(kind, reg, ev):
        nonlocal methods
        methods = methods or logic.available_payment_methods(regstore, square)
        event_mailer.send(kind, reg, ev, methods)

    logic.notify = notify


def now_jst():
    from datetime import datetime
    return datetime.now(event_model.JST)


def load_native_event(event_id):
    item = load_event(event_id)
    return item


def load_registration(event_id, reg_id):
    reg = regstore.get_registration(reg_id)
    if not reg or reg.get("eventId") != event_id:
        raise ApiError(404, "Registration not found")
    return reg


def reg_audit(reg_id, event_id, event, action, summary, changes=None):
    actor = admin_roles.actor_of(event)
    audit(f"REG#{reg_id}", action, actor, summary, changes)


def event_form(item, reg=None):
    form = None
    if reg:
        form = regstore.get_form_version(reg["formId"], int(reg["formVersion"]))
    return form or load_form(item)


def reg_dashboard(event_id):
    item = load_native_event(event_id)
    regs = regstore.list_for_event(event_id)
    tickets = store.list_tickets(event_id)
    counters = regstore.get_counters([f"EVENT#{event_id}"])
    return 200, {"dashboard": radm.dashboard(item, tickets, regs, counters)}


def reg_columns(item, regs):
    forms = {}
    for r in regs:
        key = (r["formId"], int(r["formVersion"]))
        if key not in forms:
            forms[key] = regstore.get_form_version(*key)
    latest = load_form(item) if item.get("formId") else None
    versions = [f for f in forms.values() if f] + ([latest] if latest else [])
    unique = {f["version"]: f for f in versions}
    return event_exports.answer_columns(list(unique.values()), regs)


def reg_list(event_id, query):
    item = load_native_event(event_id)
    regs = regstore.list_for_event(event_id)
    result = radm.query_registrations(regs, query)
    result["columns"] = reg_columns(item, regs)
    result["tickets"] = [{"ticketTypeId": t["ticketTypeId"], "name": t["name"]} for t in store.list_tickets(event_id)]
    return 200, result


def reg_get(event_id, reg_id):
    item = load_native_event(event_id)
    reg = load_registration(event_id, reg_id)
    form = event_form(item, reg)
    return 200, {"registration": radm.admin_view(reg), "form": {"version": form["version"], "fields": form["fields"]},
                 "history": store.list_audit(f"REG#{reg_id}"), "qr": event_mailer.qr_payload(reg)}


def reg_add(event, event_id, body):
    _wire()
    item = load_native_event(event_id)
    actor = admin_roles.actor_of(event)
    reg, created = radm.manual_add(event=item, tickets=store.list_tickets(event_id), form=load_form(item), body=body,
                                   account=None, now=now_jst(), actor=actor)
    if created:
        reg_audit(reg["registrationId"], event_id, event, "create", f"Added by {actor}: {reg['seats']} ticket(s), {reg['status']}")
        audit(f"EVENT#{event_id}", "registration", actor, f"Added registration {reg['registrationId']} ({reg['fullName']})")
    return (201 if created else 200), {"registration": radm.admin_view(reg)}


def reg_edit(event, event_id, reg_id, body):
    item = load_native_event(event_id)
    reg = load_registration(event_id, reg_id)
    updated = radm.admin_edit(reg, event_form(item, reg), body, admin_roles.actor_of(event), now_jst())
    changed = [k for k in ("answers", "adminNote") if k in body]
    reg_audit(reg_id, event_id, event, "update", f"Edited {', '.join(changed) or 'registration'}")
    return 200, {"registration": radm.admin_view(updated)}


def reg_action(event, event_id, reg_id, action, body):
    _wire()
    item = load_native_event(event_id)
    reg = load_registration(event_id, reg_id)
    actor = admin_roles.actor_of(event)
    when = now_jst()
    try:
        if action == "cancel":
            reason = str(body.get("reason", "")).strip()[:200] or "admin_cancelled"
            updated = logic.cancel(reg, by=actor, reason=reason, now=when, event=item, quiet=not body.get("notify", True))
            summary = f"Cancelled ({reason})" + ("; refund pending" if updated.get("paymentStatus") == "refund_pending" else "")
        elif action == "promote":
            updated = logic.promote(reg, item, store.list_tickets(event_id), now=when, actor=actor)
            summary = f"Promoted from the waiting list to {updated['status']}"
        elif action == "mark-paid":
            via = body.get("via", "bank")
            if via not in radm.PAID_VIA:
                raise ApiError(400, "Unknown payment method", {"via": "Invalid"})
            updated = logic.mark_paid(reg, via=via, reference=str(body.get("reference", ""))[:100], now=when, event=item, actor=actor)
            summary = f"Marked paid ({via})"
        elif action == "refund":
            updated = radm.refund(reg, body, actor, when, square)
            summary = f"Refund recorded ({updated.get('refundMode')})"
        elif action == "anonymize":
            if not admin_roles.has_role(event, admin_roles.SUPER_ADMIN):
                raise ApiError(403, "Only a super admin can erase personal data")
            ends = event_model.parse_iso(item.get("endsAt") or item.get("startsAt"))
            updated = radm.anonymize(reg, actor, when, event_over=bool(ends and ends < when), delete_file=delete_upload)
            summary = "Personal data erased"
        elif action == "check-in":
            result = radm.check_in(reg, None, event_id, actor, when, undo=bool(body.get("undo")))
            if result["result"] in ("ok", "undone"):
                reg_audit(reg_id, event_id, event, "check-in", "Checked in" if result["result"] == "ok" else "Check-in undone")
            return 200, result
        else:
            raise ApiError(404, "Not found")
    except logic.StateConflict:
        raise ApiError(409, "This registration changed in the meantime. Reload it and try again.")
    reg_audit(reg_id, event_id, event, action, summary)
    return 200, {"registration": radm.admin_view(updated)}


def delete_upload(event_id, file_id):
    if not ASSETS_BUCKET or not file_id or not str(file_id).isalnum():
        return
    for prefix in ("event-uploads", "event-uploads-pending"):
        try:
            s3().delete_object(Bucket=ASSETS_BUCKET, Key=f"{prefix}/{event_id}/{file_id}")
        except ClientError:
            print(f"could not delete upload {file_id}")


def reg_file_link(event_id, reg_id, file_id):
    load_native_event(event_id)
    reg = load_registration(event_id, reg_id)
    ref = next((r for r in radm.file_refs(reg.get("answers", {})) if r["fileId"] == file_id), None)
    if not ref or not ASSETS_BUCKET:
        raise ApiError(404, "File not found")
    name = "".join(c if c.isalnum() or c in " .-_()" else "_" for c in str(ref.get("name", "file")))[:100] or "file"
    url = s3().generate_presigned_url("get_object", ExpiresIn=300, Params={
        "Bucket": ASSETS_BUCKET, "Key": f"event-uploads/{event_id}/{file_id}",
        "ResponseContentDisposition": f'attachment; filename="{name}"', "ResponseContentType": ref.get("contentType", "application/octet-stream")})
    return 200, {"url": url, "name": name, "expiresInSeconds": 300}


def reg_scan(event, event_id, body):
    load_native_event(event_id)
    parsed = radm.parse_scan(body.get("code"))
    if not parsed:
        return 200, {"result": "not_found"}
    reg_id, token = parsed
    reg = regstore.get_registration(reg_id)
    undo = bool(body.get("undo"))
    result = radm.check_in(reg, token, event_id, admin_roles.actor_of(event), now_jst(), undo=undo)
    if result["result"] in ("ok", "undone"):
        reg_audit(reg_id, event_id, event, "check-in", "Checked in" if result["result"] == "ok" else "Check-in undone")
    return 200, result


def reg_export(event, event_id, query):
    import base64
    item = load_native_event(event_id)
    fmt = query.get("format", "csv")
    if fmt not in ("csv", "xlsx"):
        raise ApiError(400, "format must be csv or xlsx")
    regs = regstore.list_for_event(event_id)
    rows = radm.filter_registrations(regs, query)
    if len(rows) > MAX_EXPORT_ROWS:
        raise ApiError(400, "Too many rows to export at once. Narrow the filters.")
    table = event_exports.export_table(rows, reg_columns(item, regs), store.list_tickets(event_id))
    data = event_exports.to_csv(table) if fmt == "csv" else event_exports.to_xlsx(table)
    content_type = "text/csv; charset=utf-8" if fmt == "csv" else "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    actor = admin_roles.actor_of(event)
    audit(f"EVENT#{event_id}", "export", actor, f"Exported {len(rows)} registration(s) as {fmt}")
    return 200, {"filename": event_exports.export_filename(item, fmt, now_jst()), "contentType": content_type,
                 "base64": base64.b64encode(data).decode(), "rows": len(rows)}


def reg_email(event, event_id, body):
    item = load_native_event(event_id)
    actor = admin_roles.actor_of(event)
    subject, text = str(body.get("subject", "")), str(body.get("body", ""))
    template = body.get("useTemplate")
    if template:
        saved = (item.get("emails") or {}).get(template)
        if template not in event_model.EMAIL_KINDS or not saved or not saved.get("body"):
            raise ApiError(400, "That email template has not been written yet", {"useTemplate": "Not set"})
        subject, text = saved.get("subject") or subject, saved["body"]
    problems = radm.validate_message(subject, text)
    if problems:
        raise ApiError(400, "Please check the message", problems)
    regs = regstore.list_for_event(event_id)
    if body.get("test"):
        own = actor if "@" in actor else ""
        if not own:
            raise ApiError(400, "Your account has no email address to send the test to")
        sample = (regs or [{"registrationId": "NK-TEST-0000", "fullName": "Test Person", "tickets": [], "email": own}])[0]
        result = radm.send_bulk(item, [{**sample, "email": own}], subject, text, event_mailer.send_message, pause=0)
        return 200, {**result, "total": 1, "nextOffset": None, "test": True}
    recipients = radm.bulk_recipients(regs, str(body.get("audience", "")))
    if not recipients:
        raise ApiError(400, "No one matches that audience")
    offset = body.get("offset", 0)
    if not isinstance(offset, int) or isinstance(offset, bool) or offset < 0:
        raise ApiError(400, "Invalid offset")
    batch = recipients[offset:offset + BULK_BATCH]
    result = radm.send_bulk(item, batch, subject, text, event_mailer.send_message)
    next_offset = offset + BULK_BATCH if offset + BULK_BATCH < len(recipients) else None
    audit(f"EVENT#{event_id}", "email", actor,
          f"Emailed '{subject[:80]}' to {body.get('audience')} ({result['sent']} sent, {len(result['failed'])} failed, "
          f"recipients {offset + 1}-{offset + len(batch)} of {len(recipients)})")
    return 200, {**result, "total": len(recipients), "nextOffset": next_offset}


# ── routing ───────────────────────────────────────────────────────────────────

def route(event):
    method = event.get("httpMethod", "")
    parts = [p for p in str((event.get("pathParameters") or {}).get("proxy", "")).split("/") if p]
    query = event.get("queryStringParameters") or {}

    if not admin_roles.roles_of(event):
        raise ApiError(403, "Your account does not have access to event management")

    if parts == ["me"] and method == "GET":
        return 200, {"roles": sorted(admin_roles.roles_of(event)), "actor": admin_roles.actor_of(event)}
    if parts and parts[0] == "form-templates":
        if len(parts) == 1 and method == "GET":
            return list_templates()
        if len(parts) == 1 and method == "POST":
            return create_template(event, parse_body(event))
        if len(parts) == 2 and method == "GET":
            return get_template(parts[1])
        if len(parts) == 2 and method == "DELETE":
            return delete_template(event, parts[1])
        raise ApiError(405, "Method not allowed")
    if not parts or parts[0] != "events":
        raise ApiError(404, "Not found")

    if len(parts) == 1:
        if method == "GET":
            return list_events(event, query)
        if method == "POST":
            return create_event(event, parse_body(event))
    elif len(parts) == 2:
        if method == "GET":
            return get_event(parts[1])
        if method == "PUT":
            return update_event(event, parts[1], parse_body(event))
        if method == "DELETE":
            return delete_event(event, parts[1])
    elif len(parts) == 3:
        eid, sub = parts[1], parts[2]
        if sub == "status" and method == "POST":
            return set_status(event, eid, parse_body(event))
        if sub == "duplicate" and method == "POST":
            return duplicate_event(event, eid)
        if sub == "tickets" and method == "GET":
            load_event(eid)
            return 200, {"tickets": store.list_tickets(eid)}
        if sub == "tickets" and method == "POST":
            return save_ticket(event, eid, None, parse_body(event))
        if sub == "images" and method == "POST":
            return upload_image(event, eid, parse_body(event))
        if sub == "audit" and method == "GET":
            load_event(eid)
            return 200, {"entries": store.list_audit(f"EVENT#{eid}")}
        if sub == "form" and method == "GET":
            return get_form(eid)
        if sub == "form" and method == "PUT":
            return put_form(event, eid, parse_body(event))
        if sub == "dashboard" and method == "GET":
            return reg_dashboard(eid)
        if sub == "registrations" and method == "GET":
            return reg_list(eid, query)
        if sub == "registrations" and method == "POST":
            return reg_add(event, eid, parse_body(event))
        if sub == "check-in" and method == "POST":
            return reg_scan(event, eid, parse_body(event))
        if sub == "export" and method == "GET":
            return reg_export(event, eid, query)
        if sub == "email" and method == "POST":
            return reg_email(event, eid, parse_body(event))
    elif len(parts) == 4 and parts[2] == "registrations":
        if method == "GET":
            return reg_get(parts[1], parts[3])
        if method == "PUT":
            return reg_edit(event, parts[1], parts[3], parse_body(event))
    elif len(parts) == 5 and parts[2] == "registrations" and method == "POST":
        return reg_action(event, parts[1], parts[3], parts[4], parse_body(event))
    elif len(parts) == 6 and parts[2] == "registrations" and parts[4] == "files" and method == "GET":
        result = reg_file_link(parts[1], parts[3], parts[5])
        reg_audit(parts[3], parts[1], event, "file", "Downloaded an uploaded file")
        return result
    elif len(parts) == 4 and parts[2] == "form":
        if parts[3] == "validate" and method == "POST":
            return validate_sample(parts[1], parse_body(event))
        if parts[3] == "apply" and method == "POST":
            return apply_form_source(event, parts[1], parse_body(event))
    elif len(parts) == 5 and parts[2] == "form" and parts[3] == "versions" and method == "GET":
        return get_form_version(parts[1], parts[4])
    elif len(parts) == 4 and parts[2] == "tickets":
        if method == "PUT":
            return save_ticket(event, parts[1], parts[3], parse_body(event))
        if method == "DELETE":
            return delete_ticket(event, parts[1], parts[3])
    raise ApiError(405, "Method not allowed")


def lambda_handler(event, context):
    if event.get("httpMethod") == "OPTIONS":
        return respond(200, {})
    try:
        status, body = route(event)
        return respond(status, body)
    except ApiError as exc:
        return respond(exc.status, {"error": exc.message, "fields": exc.fields})
    except logic.RegError as exc:
        return respond(exc.status, {"error": exc.message, "fields": exc.fields, "code": exc.code})
    except ClientError as exc:
        print(f"DynamoDB/S3 error: {exc}")
        return respond(500, {"error": "Request failed"})
