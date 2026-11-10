"""
Dynamic registration form engine (pure Python).

  * evaluate_condition / is_visible   conditional logic ("showIf")
  * validate_submission               server-side validation of a submission against a stored schema

The front end mirrors the condition rules in frontend/src/formLogic.js; both are
tested against tests/fixtures/form_conditions.json so they cannot drift apart.

Rules
-----
* Fields are processed in schema order. A field whose `showIf` is false is
  hidden: its answer is discarded and it is never required. Later conditions
  see hidden fields as empty.
* Unknown answer keys are dropped; only schema fields are ever stored.
* System fields and the account email are filled from the
  server-side `context`, never from the client.
"""

import json
import re
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any, Optional

import event_model

MAX_SUBMISSION_BYTES = 100_000
TEXT_HARD_MAX = 1000
TEXTAREA_HARD_MAX = 5000
# One plain address: no spaces, commas, angle brackets, quotes or parentheses (they would let a value smuggle
# a second recipient or a display name into a mail header).
EMAIL_RE = re.compile(r"^[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?)+$")
CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
# Explicit country code.
PHONE_RE = re.compile(r"^\+\d{1,4}[\s\-]?\d[\d\s\-]{5,18}$")
# Domestic Japanese numbers (090-1234-5678, 03-1234-5678) are accepted and stored as +81 ...
JP_DOMESTIC_RE = re.compile(r"^0\d{9,10}$")
SAFE_FILENAME_RE = re.compile(r"[^\w.\- ()]")

# Read-only "system" fields take their value from server-side context. None are defined by default.
SYSTEM_FIELD_CONTEXT: dict = {}


# ── conditions ────────────────────────────────────────────────────────────────

def _is_empty(value: Any) -> bool:
    return value is None or value is False or value == "" or value == [] or value == {}


def _norm(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float, Decimal)):
        number = Decimal(str(value))
        return str(int(number)) if number == number.to_integral_value() else format(number.normalize(), "f")
    return str(value)


def _number(value: Any) -> Optional[Decimal]:
    if isinstance(value, bool) or value in (None, ""):
        return None
    try:
        return Decimal(str(value))
    except InvalidOperation:
        return None


def _rule_holds(rule: dict, answers: dict) -> bool:
    answer = answers.get(rule.get("field"))
    op, expected = rule.get("op"), rule.get("value")
    if op == "empty":
        return _is_empty(answer)
    if op == "notEmpty":
        return not _is_empty(answer)
    answered = answer if isinstance(answer, list) else [answer]
    given = [_norm(a) for a in answered if a is not None]
    if op == "eq":
        return _norm(expected) in given
    if op == "ne":
        return _norm(expected) not in given
    if op == "in":
        wanted = {_norm(v) for v in (expected if isinstance(expected, list) else [expected])}
        return bool(wanted & set(given))
    if op in ("gt", "lt"):
        a, b = _number(answer), _number(expected)
        if a is None or b is None:
            return False
        return a > b if op == "gt" else a < b
    return False


def evaluate_condition(cond: Optional[dict], answers: dict) -> bool:
    """True when `cond` is absent or satisfied."""
    if not cond:
        return True
    if "all" in cond:
        return all(_rule_holds(r, answers) for r in cond["all"])
    if "any" in cond:
        return any(_rule_holds(r, answers) for r in cond["any"])
    return True


def is_visible(field: dict, answers: dict) -> bool:
    return evaluate_condition(field.get("showIf"), answers)


# ── submission validation ─────────────────────────────────────────────────────

def _is_blank(value: Any) -> bool:
    return value is None or value == "" or value == [] or (isinstance(value, str) and not value.strip())


def _check_regex(pattern: str, text: str) -> bool:
    try:
        return re.search(pattern, text) is not None
    except re.error:
        return False


def _validate_value(field: dict, value: Any, path: str, errors: dict) -> Any:
    """Validate one visible, non-empty value. Returns the cleaned value."""
    ftype = field["type"]
    rules = field.get("validation") or {}

    if ftype in ("text", "textarea", "email", "phone"):
        if not isinstance(value, str):
            errors[path] = "Must be text"
            return None
        text = value.strip()
        if CONTROL_RE.search(text) or (ftype != "textarea" and ("\n" in value or "\r" in value)):
            errors[path] = "Contains characters that are not allowed"
            return None
        hard = TEXTAREA_HARD_MAX if ftype == "textarea" else TEXT_HARD_MAX
        limit = min(int(rules.get("maxLength") or hard), hard)
        if len(text) > limit:
            errors[path] = f"Must be at most {limit} characters"
        elif rules.get("minLength") and len(text) < int(rules["minLength"]):
            errors[path] = f"Must be at least {int(rules['minLength'])} characters"
        elif ftype == "email" and (len(text) > 254 or not EMAIL_RE.match(text)):
            errors[path] = "Enter a valid email address"
        elif ftype == "phone" and not PHONE_RE.match(text):
            digits = re.sub(r"[\s\-()]", "", text)
            if JP_DOMESTIC_RE.match(digits) and re.fullmatch(r"[\d\s\-()]+", text):
                text = "+81 " + digits[1:]
            else:
                errors[path] = "Enter a phone number such as 090-1234-5678 or +81 90-1234-5678"
        elif rules.get("regex") and not _check_regex(rules["regex"], text):
            errors[path] = rules.get("regexMessage") or "Does not match the required format"
        return text.lower() if ftype == "email" else text

    if ftype == "number":
        number = _number(value)
        if number is None or not number.is_finite():
            errors[path] = "Enter a number"
            return None
        if rules.get("min") is not None and number < _number(rules["min"]):
            errors[path] = f"Must be at least {_norm(rules['min'])}"
        elif rules.get("max") is not None and number > _number(rules["max"]):
            errors[path] = f"Must be at most {_norm(rules['max'])}"
        return int(number) if number == number.to_integral_value() else float(number)

    if ftype in ("dropdown", "radio"):
        allowed = {o["value"] for o in field.get("options", [])}
        if not isinstance(value, str) or value not in allowed:
            errors[path] = "Choose one of the listed options"
            return None
        return value

    if ftype == "multiselect":
        allowed = {o["value"] for o in field.get("options", [])}
        if not isinstance(value, list) or not all(isinstance(v, str) and v in allowed for v in value) \
                or len(set(value)) != len(value):
            errors[path] = "Choose from the listed options"
            return None
        if rules.get("min") is not None and len(value) < rules["min"]:
            errors[path] = f"Choose at least {rules['min']}"
        elif rules.get("max") is not None and len(value) > rules["max"]:
            errors[path] = f"Choose at most {rules['max']}"
        return list(value)

    if ftype in ("checkbox", "consent"):
        if not isinstance(value, bool):
            errors[path] = "Must be checked or unchecked"
            return None
        return value

    if ftype == "date":
        try:
            parsed = date.fromisoformat(value) if isinstance(value, str) and event_model.DATE_RE.match(value) else None
        except ValueError:
            parsed = None
        if parsed is None:
            errors[path] = "Enter a date as YYYY-MM-DD"
            return None
        if rules.get("min") and value < rules["min"]:
            errors[path] = f"Must be on or after {rules['min']}"
        elif rules.get("max") and value > rules["max"]:
            errors[path] = f"Must be on or before {rules['max']}"
        return value

    if ftype == "file":
        # Phase 4 adds the upload endpoint that issues fileId; here we validate the reference.
        ok = isinstance(value, dict) and isinstance(value.get("fileId"), str) and value["fileId"] \
            and isinstance(value.get("name"), str) and isinstance(value.get("contentType"), str) \
            and isinstance(value.get("size"), int) and not isinstance(value.get("size"), bool)
        if not ok:
            errors[path] = "Upload a file"
            return None
        if value["contentType"] not in rules.get("allowedTypes", []):
            errors[path] = "That file type is not allowed"
        elif value["size"] <= 0 or value["size"] > rules.get("maxSizeMb", 0) * 1024 * 1024:
            errors[path] = f"File must be {rules.get('maxSizeMb')} MB or smaller"
        return {"fileId": value["fileId"][:100], "contentType": value["contentType"], "size": value["size"],
                "name": SAFE_FILENAME_RE.sub("_", value["name"])[:200]}

    errors[path] = "Unsupported field"
    return None


def _validate_fields(fields: list, raw: dict, context: dict, errors: dict, prefix: str = "",
                     outer: Optional[dict] = None) -> dict:
    clean: dict = {}
    scope = dict(outer or {})
    for field in fields:
        ftype, key = field["type"], field["key"]
        path = f"{prefix}{key}"
        if ftype in event_model.DISPLAY_ONLY_TYPES:
            continue
        if not is_visible(field, scope):
            scope[key] = None
            continue

        if not prefix and field.get("source") == "system":
            value = context.get(SYSTEM_FIELD_CONTEXT.get(key, ""))
            if value not in (None, ""):
                clean[key] = scope[key] = value
            continue
        if not prefix and key == "email" and context.get("email"):
            clean[key] = scope[key] = str(context["email"]).strip().lower()
            continue

        value = raw.get(key)
        if ftype == "group":
            entries = value if value is not None else []
            if not isinstance(entries, list) or not all(isinstance(e, dict) for e in entries):
                errors[path] = "Invalid entries"
                continue
            if len(entries) > field["maxRepeat"]:
                errors[path] = f"At most {field['maxRepeat']} entries"
                continue
            if field.get("required") and not entries:
                errors[path] = "Add at least one"
                continue
            cleaned = [_validate_fields(field["fields"], e, context, errors, f"{path}[{i}].", scope)
                       for i, e in enumerate(entries)]
            clean[key] = scope[key] = cleaned
            continue

        if ftype in ("checkbox", "consent") and value is None:
            value = False
        if _is_blank(value) or (ftype == "multiselect" and value == []):
            scope[key] = None
            # The WhatsApp number is mandatory even on forms saved before it became required.
            if field.get("required") or (not prefix and key == "phone" and field.get("source") == "profile"):
                errors[path] = "Required"
            continue
        cleaned = _validate_value(field, value, path, errors)
        if ftype in ("checkbox", "consent") and field.get("required") and cleaned is not True and path not in errors:
            errors[path] = "Required"
        if path not in errors:
            clean[key] = scope[key] = cleaned
        else:
            scope[key] = None
    return clean


def validate_submission(schema: dict, answers: Any, context: Optional[dict] = None) -> tuple:
    """Validate `answers` against `schema` ({"fields": [...]}).

    context: server-side facts, e.g. {"email": "..."} for a signed-in account.
    Returns (clean_answers, errors) where errors maps a field path (``key`` or ``group[0].child``)
    to a message. Registrations must be stored from clean_answers only.
    """
    errors: dict = {}
    if not isinstance(answers, dict):
        return {}, {"": "Answers must be an object"}
    if len(json.dumps(answers, default=str)) > MAX_SUBMISSION_BYTES:
        return {}, {"": "Submission is too large"}
    context = dict(context or {})
    clean = _validate_fields(schema["fields"], answers, context, errors)
    return clean, errors
