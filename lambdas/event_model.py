"""
Event platform data model (pure Python, no AWS calls).

Shared by the event-platform Lambdas:
  * constants and validation for events, ticket types and form schemas
  * the locked core registration fields and their backend enforcement
  * form-schema versioning (immutable versions, content hash)

Times are ISO-8601 with an explicit +09:00 offset (JST); money is whole JPY.
"""

import hashlib
import json
import re
from html import escape
from html.parser import HTMLParser
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, Optional

import event_addons
import event_capabilities

JST = timezone(timedelta(hours=9))
TIMEZONE_NAME = "Asia/Tokyo"

EVENT_STATUSES = ("draft", "published", "unlisted", "closed", "archived")
REGISTRATION_STATUSES = ("pending_payment", "confirmed", "waitlisted", "cancelled")
PAYMENT_STATUSES = ("none", "pending", "paid", "refund_pending", "refunding", "refunded")
LAYOUTS = ("classic", "hero", "minimal")
PAGE_SECTIONS = ("agenda", "speakers", "sponsors", "faq", "contact")

FIELD_TYPES = (
    "text", "textarea", "email", "phone", "number", "dropdown", "radio",
    "checkbox", "multiselect", "date", "file", "consent", "heading", "info",
    "group",
)
CHOICE_TYPES = ("dropdown", "radio", "multiselect")
DISPLAY_ONLY_TYPES = ("heading", "info")
CONDITION_OPS = ("eq", "ne", "in", "notEmpty", "empty", "gt", "lt")

SLUG_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
FIELD_KEY_RE = re.compile(r"^[a-z][a-zA-Z0-9_]{0,63}$")
COLOR_RE = re.compile(r"^#[0-9a-fA-F]{6}$")
MAX_UPLOAD_MB = 4   # uploads travel base64-encoded inside one Lambda request (6 MB limit)
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
# A group that is itself repeated: (a+)+, (a*)*, (a|b+){2,} ... catastrophic backtracking.
NESTED_QUANTIFIER_RE = re.compile(r"\([^)]*[+*][^)]*\)[+*{]")

# ── Locked core fields ────────────────────────────────────────────────────────
# Every form contains these. The admin may relabel and reorder them (label,
# helpText, placeholder, position) but never delete them or change what makes
# them system fields. `source` says where the value comes from:
#   profile  - prefilled from the signed-in attendee's account
#   system   - read-only, resolved server-side (none are defined by default)
#   user     - entered by the registrant
CORE_FIELDS = (
    {"key": "fullName", "type": "text", "label": "Full name", "section": "member",
     "source": "profile", "profileKey": "fullname", "required": True, "readOnly": False},
    {"key": "email", "type": "email", "label": "Email", "section": "member",
     "source": "profile", "profileKey": "email", "required": True, "readOnly": False},
    {"key": "phone", "type": "phone", "label": "WhatsApp number (with country code)", "section": "member",
     "helpText": "We use WhatsApp to share event updates. Include the country code, e.g. +81 90 1234 5678. "
                 "A Japanese number starting with 0 is saved as +81.",
     "placeholder": "+81 90 1234 5678",
     "source": "profile", "profileKey": "phone", "required": True, "readOnly": False},
    {"key": "consentPrivacy", "type": "consent", "label": "I agree to the Privacy Policy", "section": "consent",
     "source": "user", "required": True, "readOnly": False},
    {"key": "consentTerms", "type": "consent", "label": "I agree to the Terms and Conditions", "section": "consent",
     "source": "user", "required": True, "readOnly": False},
)
CORE_KEYS = tuple(f["key"] for f in CORE_FIELDS)
# Properties the admin may change on a core field.
CORE_EDITABLE = ("label", "helpText", "placeholder")
# Everything else on a core field is restored from CORE_FIELDS on every save.
CORE_LOCKED_PROPS = ("type", "section", "source", "profileKey", "required", "readOnly")


def _core_field(definition: dict) -> dict:
    field = dict(definition)
    field["locked"] = True
    return field


def now_iso() -> str:
    return datetime.now(JST).isoformat(timespec="seconds")


def parse_iso(value: Any) -> Optional[datetime]:
    """Parse an ISO-8601 string; naive values are taken as JST."""
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=JST)


# ── Form schemas ──────────────────────────────────────────────────────────────

def default_form_fields() -> list:
    """Core fields in their default order (contact details, consent)."""
    return [_core_field(f) for f in CORE_FIELDS]


def ensure_core_fields(fields: list) -> list:
    """Return `fields` with every core field present and its locked props restored.

    Missing core fields are re-inserted (contact details at the top, consent at
    the bottom); the admin's label/help/placeholder and the position of core
    fields that are present are kept. Called on every save, so a crafted
    payload cannot drop or weaken a core field.
    """
    by_key = {f.get("key"): f for f in fields if isinstance(f, dict)}
    result = []
    for field in fields:
        if not isinstance(field, dict):
            continue
        definition = next((d for d in CORE_FIELDS if d["key"] == field.get("key")), None)
        if definition is None:
            result.append(field)
            continue
        merged = _core_field(definition)
        for prop in CORE_EDITABLE:
            if prop in field:
                merged[prop] = field[prop]
        # Forms saved before the phone field became a required WhatsApp number carry the old default label.
        if field.get("key") == "phone" and str(field.get("label", "")).strip() in ("", "Phone"):
            merged["label"] = definition["label"]
        result.append(merged)
    head = [_core_field(d) for d in CORE_FIELDS if d["key"] not in by_key and d["section"] != "consent"]
    tail = [_core_field(d) for d in CORE_FIELDS if d["key"] not in by_key and d["section"] == "consent"]
    return head + result + tail


def _validate_condition(cond: Any, own_key: str, earlier_keys: set, path: str, errors: list) -> None:
    if cond is None:
        return
    if not isinstance(cond, dict) or not ({"all", "any"} & set(cond)) or len(cond) != 1:
        errors.append(f"{path}: showIf must be an object with a single 'all' or 'any' list")
        return
    rules = cond.get("all") if "all" in cond else cond.get("any")
    if not isinstance(rules, list) or not rules:
        errors.append(f"{path}: showIf needs at least one rule")
        return
    for rule in rules:
        if not isinstance(rule, dict) or rule.get("op") not in CONDITION_OPS:
            errors.append(f"{path}: rule operator must be one of {', '.join(CONDITION_OPS)}")
            continue
        ref = rule.get("field")
        if ref == own_key:
            errors.append(f"{path}: a field cannot depend on itself")
        elif ref not in earlier_keys:
            errors.append(f"{path}: rule refers to '{ref}', which must be an earlier field")
        if rule["op"] in ("eq", "ne", "in", "gt", "lt") and "value" not in rule:
            errors.append(f"{path}: rule on '{ref}' needs a value")


def _validate_field(field: Any, index: int, seen: set, earlier: set, errors: list, in_group: bool) -> None:
    path = f"fields[{index}]"
    if not isinstance(field, dict):
        errors.append(f"{path}: must be an object")
        return
    key = field.get("key")
    ftype = field.get("type")
    if not isinstance(key, str) or not FIELD_KEY_RE.match(key):
        errors.append(f"{path}: key must start with a letter and use letters, digits or _ (max 64)")
        return
    path = f"field '{key}'"
    if key in seen:
        errors.append(f"{path}: duplicate key")
    if ftype not in FIELD_TYPES:
        errors.append(f"{path}: unknown type '{ftype}'")
        return
    if not str(field.get("label", "")).strip():
        errors.append(f"{path}: label is required")
    if in_group and (ftype == "group" or key in CORE_KEYS):
        errors.append(f"{path}: groups cannot nest, and core fields cannot be placed in a group")
    if ftype in CHOICE_TYPES:
        options = field.get("options")
        if not isinstance(options, list) or not options or not all(
            isinstance(o, dict) and str(o.get("value", "")).strip() and str(o.get("label", "")).strip()
            for o in options
        ):
            errors.append(f"{path}: needs a non-empty options list of {{value, label}}")
        elif len({o["value"] for o in options}) != len(options):
            errors.append(f"{path}: option values must be unique")
    validation = field.get("validation") or {}
    if not isinstance(validation, dict):
        errors.append(f"{path}: validation must be an object")
        validation = {}
    pattern = validation.get("regex")
    if pattern:
        if len(str(pattern)) > 200:
            errors.append(f"{path}: regex is too long")
        else:
            try:
                re.compile(pattern)
            except re.error:
                errors.append(f"{path}: regex is not valid")
            else:
                if NESTED_QUANTIFIER_RE.search(pattern):
                    errors.append(f"{path}: regex has a nested repetition like (a+)+, which can hang; simplify it")
    for lo, hi in (("minLength", "maxLength"), ("min", "max")):
        a, b = validation.get(lo), validation.get(hi)
        if a is None and b is None:
            continue
        numeric = all(isinstance(v, (int, float, Decimal)) and not isinstance(v, bool) for v in (a, b) if v is not None)
        dates = all(isinstance(v, str) and DATE_RE.match(v) for v in (a, b) if v is not None)
        if not (numeric or dates):
            errors.append(f"{path}: {lo}/{hi} must be numbers (or YYYY-MM-DD dates for date fields)")
        elif a is not None and b is not None and a > b:
            errors.append(f"{path}: {lo} cannot exceed {hi}")
    if ftype == "file":
        size = validation.get("maxSizeMb")
        if not isinstance(size, (int, float, Decimal)) or isinstance(size, bool) or not 0 < size <= MAX_UPLOAD_MB:
            errors.append(f"{path}: file fields need maxSizeMb between 0 and {MAX_UPLOAD_MB}")
        types = validation.get("allowedTypes")
        if not isinstance(types, list) or not types:
            errors.append(f"{path}: file fields need allowedTypes (e.g. ['image/png', 'application/pdf'])")
    if ftype == "group":
        children = field.get("fields")
        if not isinstance(children, list) or not children:
            errors.append(f"{path}: a repeatable group needs fields")
        else:
            child_seen: set = set()
            for i, child in enumerate(children):
                _validate_field(child, i, child_seen | seen, earlier | child_seen, errors, True)
                if isinstance(child, dict) and isinstance(child.get("key"), str):
                    child_seen.add(child["key"])
        mx = field.get("maxRepeat")
        if not _is_int(mx) or not 1 <= mx <= 50:
            errors.append(f"{path}: maxRepeat must be a whole number from 1 to 50")
    _validate_condition(field.get("showIf"), key, earlier, path, errors)
    if key in CORE_KEYS and field.get("showIf"):
        errors.append(f"{path}: core fields cannot be conditional")
    seen.add(key)


def validate_form_schema(schema: Any) -> list:
    """Return a list of human-readable problems (empty when valid)."""
    errors: list = []
    if not isinstance(schema, dict) or not isinstance(schema.get("fields"), list):
        return ["schema must be an object with a 'fields' list"]
    fields = schema["fields"]
    if len(fields) > 200:
        errors.append("a form can have at most 200 fields")
    seen: set = set()
    for i, field in enumerate(fields):
        earlier = set(seen)
        _validate_field(field, i, seen, earlier, errors, False)
    by_key = {f.get("key"): f for f in fields if isinstance(f, dict)}
    for definition in CORE_FIELDS:
        field = by_key.get(definition["key"])
        if field is None:
            errors.append(f"core field '{definition['key']}' cannot be removed")
            continue
        if not field.get("locked"):
            errors.append(f"core field '{definition['key']}' must stay locked")
        for prop in CORE_LOCKED_PROPS:
            if prop in definition and field.get(prop) != definition[prop]:
                errors.append(f"core field '{definition['key']}': {prop} cannot be changed")
    return errors


def schema_hash(fields: list) -> str:
    canonical = json.dumps(fields, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def new_form_version(form_id: str, previous: Optional[dict], fields: list, *, kind: str = "event",
                     event_id: str = "", name: str = "", created_by: str = "") -> Optional[dict]:
    """Build the next immutable form-schema item, or None if nothing changed.

    Core fields are enforced first. A published version is never modified: a
    changed form becomes version N+1, and registrations keep pointing at the
    (formId, version) they were submitted against.
    """
    fields = ensure_core_fields(fields)
    digest = schema_hash(fields)
    if previous and previous.get("schemaHash") == digest and previous.get("name", "") == (name or previous.get("name", "")):
        return None
    now = now_iso()
    item = {
        "formId": form_id,
        "version": int(previous["version"]) + 1 if previous else 1,
        "kind": kind,
        "name": name or (previous or {}).get("name", ""),
        "fields": fields,
        "schemaHash": digest,
        "createdAt": now,
        "updatedAt": now,
    }
    if event_id:
        item["eventId"] = event_id
    if created_by:
        item["createdBy"] = created_by
    return item


def flatten_fields(fields: list, prefix: str = "") -> dict:
    """{path: field} for every field, with group children as "group.child"."""
    out: dict = {}
    for field in fields:
        if not isinstance(field, dict) or "key" not in field:
            continue
        path = f"{prefix}{field['key']}"
        out[path] = field
        if field.get("type") == "group":
            out.update(flatten_fields(field.get("fields") or [], path + "."))
    return out


def check_schema_evolution(previous: list, new: list) -> list:
    """Problems with changing a form that registrations already use.

    Old registrations keep the version they were submitted against, so nothing is
    rewritten. But exports and dashboards line columns up by key, so a key must keep
    its type (and group membership) across versions; to change a type, add a new field.
    """
    old_map, new_map = flatten_fields(previous), flatten_fields(new)
    errors = []
    for path, field in new_map.items():
        before = old_map.get(path)
        if before and before.get("type") != field.get("type"):
            errors.append(f"field '{path}' was a {before.get('type')} in an earlier version and cannot become a "
                          f"{field.get('type')}; add a new field instead")
    return errors


_DIFF_PROPS = ("type", "label", "required", "options", "validation", "showIf", "placeholder", "helpText",
               "defaultValue", "maxRepeat")


def diff_forms(old: list, new: list) -> dict:
    old_map, new_map = flatten_fields(old), flatten_fields(new)
    changed = [path for path, field in new_map.items() if path in old_map and any(
        field.get(p) != old_map[path].get(p) for p in _DIFF_PROPS)]
    result = {
        "added": sorted(set(new_map) - set(old_map)),
        "removed": sorted(set(old_map) - set(new_map)),
        "changed": sorted(changed),
    }
    old_order, new_order = [f.get("key") for f in old], [f.get("key") for f in new]
    common = [k for k in new_order if k in old_order]
    if common != [k for k in old_order if k in new_order]:
        result["reordered"] = True
    return {k: v for k, v in result.items() if v}


# ── Events and ticket types ───────────────────────────────────────────────────

def slugify(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", str(text).lower()).strip("-")


def _is_int(value: Any) -> bool:
    """A whole number. DynamoDB hands numbers back as Decimal, so accept whole-valued Decimals too."""
    if isinstance(value, Decimal):
        return value.is_finite() and value == value.to_integral_value()
    return isinstance(value, int) and not isinstance(value, bool)


def _is_https_url(value: Any) -> bool:
    return isinstance(value, str) and len(value) <= 2048 and bool(re.match(r"^https://[^\s/]+\S*$", value))


# ── Email templates ───────────────────────────────────────────────────────────

TEMPLATE_PLACEHOLDERS = ("name", "event_title", "ticket_type", "registration_id", "event_date", "venue", "event_url")
EMAIL_KINDS = ("confirmation", "reminder")
_PLACEHOLDER_RE = re.compile(r"\{([a-zA-Z_]+)\}")


def validate_email_templates(emails: Any) -> dict:
    errors: dict = {}
    if not isinstance(emails, dict):
        return {"": "must be an object"}
    for kind, template in emails.items():
        if kind not in EMAIL_KINDS:
            errors[str(kind)] = "Unknown email template"
            continue
        if not isinstance(template, dict):
            errors[kind] = "Must be an object with subject and body"
            continue
        subject, body = str(template.get("subject", "")), str(template.get("body", ""))
        if len(subject) > 200 or len(body) > 10000:
            errors[kind] = "Subject (200) or body (10000) is too long"
        unknown = sorted(set(_PLACEHOLDER_RE.findall(subject + " " + body)) - set(TEMPLATE_PLACEHOLDERS))
        if unknown:
            errors[kind] = "Unknown placeholder(s): " + ", ".join("{" + u + "}" for u in unknown)
    return errors


def render_template(text: str, values: dict, *, html_escape: bool = False) -> str:
    """Fill {placeholders}; unknown names are left as typed. Values are escaped for HTML bodies."""
    def sub(match):
        name = match.group(1)
        if name not in TEMPLATE_PLACEHOLDERS or name not in values:
            return match.group(0)
        value = str(values[name])
        return escape(value) if html_escape else value
    return _PLACEHOLDER_RE.sub(sub, text or "")


# ── Rich text ─────────────────────────────────────────────────────────────────

_ALLOWED_TAGS = {"p", "br", "strong", "b", "em", "i", "u", "ul", "ol", "li", "h2", "h3", "h4", "blockquote", "a"}
_VOID_TAGS = {"br"}
_DROP_CONTENT = {"script", "style", "iframe", "object", "embed", "template", "noscript"}


class _Sanitizer(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.out: list = []
        self.stack: list = []
        self.skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in _DROP_CONTENT:
            self.skip += 1
            return
        if self.skip or tag not in _ALLOWED_TAGS:
            return
        if tag == "a":
            href = dict(attrs).get("href") or ""
            if re.match(r"^(https?://|mailto:)", href.strip(), re.I):
                self.out.append(f'<a href="{escape(href.strip(), quote=True)}" rel="noopener noreferrer" target="_blank">')
                self.stack.append("a")
            else:
                self.stack.append(None)          # keep text, drop the link
            return
        self.out.append(f"<{tag}>")
        if tag not in _VOID_TAGS:
            self.stack.append(tag)

    def handle_startendtag(self, tag, attrs):
        if tag == "br" and not self.skip:
            self.out.append("<br>")

    def handle_endtag(self, tag):
        if tag in _DROP_CONTENT:
            self.skip = max(0, self.skip - 1)
            return
        if self.skip or tag not in _ALLOWED_TAGS or tag in _VOID_TAGS:
            return
        if tag in self.stack:
            while self.stack:
                top = self.stack.pop()
                if top:
                    self.out.append(f"</{top}>")
                if top == tag or (top is None and tag == "a"):
                    break

    def handle_data(self, data):
        if not self.skip:
            self.out.append(escape(data))

    def close(self):
        super().close()
        while self.stack:
            top = self.stack.pop()
            if top:
                self.out.append(f"</{top}>")


def sanitize_html(html: Any) -> str:
    """Allow-list sanitizer for admin-authored rich text (no attributes except safe links)."""
    parser = _Sanitizer()
    parser.feed(str(html or ""))
    parser.close()
    return "".join(parser.out)


def validate_event(payload: dict) -> dict:
    """Validate an admin event payload; returns {field: message}."""
    errors: dict = {}
    if not str(payload.get("title", "")).strip():
        errors["title"] = "Title is required"
    slug = str(payload.get("slug", "")).strip()
    if not SLUG_RE.match(slug):
        errors["slug"] = "Use lowercase letters, digits and hyphens only"
    status = payload.get("status", "draft")
    if status not in EVENT_STATUSES:
        errors["status"] = "Status must be one of " + ", ".join(EVENT_STATUSES)

    starts, ends = parse_iso(payload.get("startsAt")), parse_iso(payload.get("endsAt"))
    for key, value in (("startsAt", starts), ("endsAt", ends)):
        if payload.get(key) and value is None:
            errors[key] = "Use an ISO date-time (e.g. 2026-09-20T10:00:00+09:00)"
    if status in ("published", "unlisted") and starts is None:
        errors["startsAt"] = "A start time is required to publish"
    if starts and ends and ends < starts:
        errors["endsAt"] = "End must not be before the start"
    opens, closes = parse_iso(payload.get("registrationOpensAt")), parse_iso(payload.get("registrationClosesAt"))
    if opens and closes and closes < opens:
        errors["registrationClosesAt"] = "Registration close must not be before it opens"

    capacity = payload.get("capacity")
    if capacity is not None and (not _is_int(capacity) or capacity < 0):
        errors["capacity"] = "Capacity must be a whole number, 0 or greater"

    design = payload.get("design") or {}
    if design.get("layout") and design["layout"] not in LAYOUTS:
        errors["design.layout"] = "Layout must be one of " + ", ".join(LAYOUTS)
    if design.get("accentColor") and not COLOR_RE.match(design["accentColor"]):
        errors["design.accentColor"] = "Use a hex colour like #1a7f5a"
    for section in design.get("sections") or []:
        if section.get("key") not in PAGE_SECTIONS:
            errors["design.sections"] = "Sections must be from " + ", ".join(PAGE_SECTIONS)
            break

    access = payload.get("access") or {}
    if "guestsAllowed" in access and not isinstance(access["guestsAllowed"], bool):
        errors["access.guestsAllowed"] = "Must be true or false"

    errors.update(event_capabilities.validate_input(payload.get("capabilities")))
    errors.update(event_addons.validate_config(payload))

    for section in design.get("sections") or []:
        if isinstance(section, dict) and len(str(section.get("content", ""))) > 20000:
            errors["design.sections"] = "A section's text is too long"
    keys = [s.get("key") for s in design.get("sections") or [] if isinstance(s, dict)]
    if len(keys) != len(set(keys)):
        errors["design.sections"] = "Each section can appear only once"
    for key in ("coverImage",):
        if design.get(key) and not _is_https_url(design[key]):
            errors[f"design.{key}"] = "Use an https:// image URL"
    gallery = design.get("gallery") or []
    if not isinstance(gallery, list) or len(gallery) > 12 or not all(_is_https_url(u) for u in gallery):
        errors["design.gallery"] = "Gallery must be up to 12 https:// image URLs"

    tags = payload.get("tags") or []
    if not isinstance(tags, list) or len(tags) > 20 or not all(isinstance(t, str) and 0 < len(t) <= 40 for t in tags):
        errors["tags"] = "Up to 20 tags of 1-40 characters"
    if len(str(payload.get("shortDescription", ""))) > 300:
        errors["shortDescription"] = "Keep the short description under 300 characters"
    if len(str(payload.get("longDescription", ""))) > 50000:
        errors["longDescription"] = "Description is too long"

    location = payload.get("location") or {}
    for key in ("mapUrl", "onlineUrl"):
        if location.get(key) and not _is_https_url(location[key]):
            errors[f"location.{key}"] = "Use an https:// link"

    errors.update({f"emails.{k}": v for k, v in validate_email_templates(payload.get("emails") or {}).items()})
    return errors


def validate_ticket_type(payload: dict) -> dict:
    errors: dict = {}
    if not str(payload.get("name", "")).strip():
        errors["name"] = "Name is required"
    for key in ("priceJpy", "earlyBirdPriceJpy"):
        value = payload.get(key)
        if value is None and key == "earlyBirdPriceJpy":
            continue
        if not _is_int(value) or value < 0:
            errors[key] = "Price must be whole yen, 0 or greater"
    for key in ("quantityLimit", "perPersonCap"):
        value = payload.get(key)
        if value is not None and (not _is_int(value) or value < 1):
            errors[key] = "Must be a whole number, 1 or greater"
    if payload.get("earlyBirdPriceJpy") is not None and not parse_iso(payload.get("earlyBirdCutoff")):
        errors["earlyBirdCutoff"] = "Early-bird pricing needs a cutoff date-time"
    return errors


# Keys an admin may set through create/update. Status moves only through the
# status endpoint (publishing has extra checks); everything else is server-owned.
EDITABLE_EVENT_KEYS = ("slug", "title", "shortDescription", "longDescription", "category", "tags",
                       "startsAt", "endsAt", "registrationOpensAt", "registrationClosesAt", "capacity",
                       "waitlistEnabled", "location", "design", "access", "emails", "capabilities", "slots", "upgrades", "seatMap")
_OPTIONAL_EVENT_KEYS = ("startsAt", "endsAt", "registrationOpensAt", "registrationClosesAt", "capacity")


def new_event(event_id: str, payload: dict, created_by: str = "") -> dict:
    """Create an event item (draft) from a validated payload, applying defaults."""
    now = now_iso()
    item = {
        "eventId": event_id,
        "status": "draft",
        "timezone": TIMEZONE_NAME,
        "revision": 1,
        "createdAt": now,
        "updatedAt": now,
    }
    if created_by:
        item["createdBy"] = created_by
    return apply_event_edit(item, payload, now=now)


def apply_event_edit(existing: dict, payload: dict, *, now: str = "") -> dict:
    """Return `existing` with the editable keys of `payload` applied (no server-owned key is touched)."""
    item = dict(existing)
    merged = {**{k: existing.get(k) for k in EDITABLE_EVENT_KEYS if k in existing}, **{
        k: payload[k] for k in EDITABLE_EVENT_KEYS if k in payload}}
    access = {"guestsAllowed": True}
    access.update(merged.get("access") or {})
    design = {"layout": "classic", "accentColor": "#1a7f5a", "gallery": [],
              "sections": [{"key": k, "enabled": False} for k in PAGE_SECTIONS]}
    design.update(merged.get("design") or {})
    design["sections"] = [
        {**sec, "content": sanitize_html(sec["content"])} if sec.get("content") else {k: v for k, v in sec.items() if k != "content"}
        for sec in design["sections"] if isinstance(sec, dict)
    ]
    item.update({
        "slug": merged.get("slug", ""),
        "title": str(merged.get("title", "")).strip(),
        "shortDescription": str(merged.get("shortDescription", "")).strip(),
        "longDescription": sanitize_html(merged.get("longDescription", "")),
        "category": str(merged.get("category", "")).strip(),
        "tags": [t.strip() for t in merged.get("tags") or []],
        "location": merged.get("location") or {},
        "design": design,
        "access": access,
        "waitlistEnabled": bool(merged.get("waitlistEnabled", False)),
        "emails": merged.get("emails") or {},
        "updatedAt": now or now_iso(),
    })
    if merged.get("capabilities") is not None:
        item["capabilities"] = event_capabilities.clean_input(merged["capabilities"])
    item.update(event_addons.clean_config(merged))
    for key in _OPTIONAL_EVENT_KEYS:
        if merged.get(key) in (None, ""):
            item.pop(key, None)            # DynamoDB keys/indexes must not hold empty values
        else:
            item[key] = merged[key]
    return item
