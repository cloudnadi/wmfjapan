"""Event platform data model: core fields, form schemas, events, legacy mirroring."""

import copy
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lambdas"))

import event_model as m  # noqa: E402


def text_field(key="dietary", **extra):
    return {"key": key, "type": "text", "label": "Dietary", **extra}


class CoreFieldTests(unittest.TestCase):
    def test_default_form_is_valid_and_has_all_core_fields(self):
        fields = m.default_form_fields()
        self.assertEqual(m.validate_form_schema({"fields": fields}), [])
        self.assertEqual([f["key"] for f in fields], list(m.CORE_KEYS))

    def test_removed_core_field_is_rejected_and_restored(self):
        fields = [f for f in m.default_form_fields() if f["key"] != "consentPrivacy"]
        errors = m.validate_form_schema({"fields": fields})
        self.assertTrue(any("consentPrivacy" in e and "removed" in e for e in errors))
        restored = m.ensure_core_fields(fields)
        self.assertEqual(m.validate_form_schema({"fields": restored}), [])
        self.assertEqual(restored[-1]["key"], "consentPrivacy")

    def test_weakened_core_field_is_restored(self):
        fields = m.default_form_fields()
        for f in fields:
            if f["key"] == "consentTerms":
                f["required"] = False
            if f["key"] == "email":
                f["source"] = "user"
                f["required"] = False
        self.assertTrue(m.validate_form_schema({"fields": fields}))
        fixed = {f["key"]: f for f in m.ensure_core_fields(fields)}
        self.assertTrue(fixed["consentTerms"]["required"])
        self.assertTrue(fixed["email"]["required"])
        self.assertEqual(fixed["email"]["source"], "profile")

    def test_relabel_and_reorder_are_allowed(self):
        fields = m.default_form_fields()
        fields.insert(0, fields.pop(2))              # move phone first
        fields[0]["label"] = "Mobile number"
        out = m.ensure_core_fields(fields)
        self.assertEqual(out[0]["key"], "phone")
        self.assertEqual(out[0]["label"], "Mobile number")
        self.assertEqual(m.validate_form_schema({"fields": out}), [])

    def test_core_fields_cannot_be_conditional_or_grouped(self):
        fields = m.default_form_fields()
        fields[0]["showIf"] = {"all": [{"field": "x", "op": "notEmpty"}]}
        self.assertTrue(any("cannot be conditional" in e for e in m.validate_form_schema({"fields": fields})))


class FormValidationTests(unittest.TestCase):
    def schema(self, *extra):
        return {"fields": m.default_form_fields() + list(extra)}

    def test_valid_custom_fields(self):
        s = self.schema(
            {"key": "dinner", "type": "radio", "label": "Dinner?",
             "options": [{"value": "yes", "label": "Yes"}, {"value": "no", "label": "No"}]},
            text_field("diet", showIf={"all": [{"field": "dinner", "op": "eq", "value": "yes"}]}),
            {"key": "guests", "type": "group", "label": "Guests", "maxRepeat": 4,
             "fields": [text_field("guestName")]},
            {"key": "photo", "type": "file", "label": "Photo",
             "validation": {"maxSizeMb": 4, "allowedTypes": ["image/png"]}},
        )
        self.assertEqual(m.validate_form_schema(s), [])

    def test_duplicate_unknown_and_missing_options(self):
        errs = m.validate_form_schema(self.schema(
            text_field("a"), text_field("a"),
            {"key": "b", "type": "bogus", "label": "B"},
            {"key": "c", "type": "dropdown", "label": "C"},
        ))
        joined = " | ".join(errs)
        self.assertIn("duplicate key", joined)
        self.assertIn("unknown type", joined)
        self.assertIn("options", joined)

    def test_condition_must_reference_earlier_field(self):
        errs = m.validate_form_schema(self.schema(
            text_field("a", showIf={"all": [{"field": "b", "op": "notEmpty"}]}),
            text_field("b"),
        ))
        self.assertTrue(any("earlier field" in e for e in errs))
        errs = m.validate_form_schema(self.schema(
            text_field("a", showIf={"all": [{"field": "a", "op": "notEmpty"}]})))
        self.assertTrue(any("itself" in e for e in errs))

    def test_bad_regex_file_limits_and_nested_group(self):
        errs = m.validate_form_schema(self.schema(
            text_field("a", validation={"regex": "("}),
            {"key": "f", "type": "file", "label": "F", "validation": {"maxSizeMb": 500, "allowedTypes": []}},
            {"key": "g", "type": "group", "label": "G", "maxRepeat": 2,
             "fields": [{"key": "h", "type": "group", "label": "H", "maxRepeat": 2, "fields": [text_field("i")]}]},
        ))
        joined = " | ".join(errs)
        self.assertIn("regex is not valid", joined)
        self.assertIn("maxSizeMb", joined)
        self.assertIn("allowedTypes", joined)
        self.assertIn("cannot nest", joined)


class VersioningTests(unittest.TestCase):
    def test_first_version_then_unchanged_then_bump(self):
        v1 = m.new_form_version("f1", None, [text_field()], event_id="e1", name="Dinner")
        self.assertEqual(v1["version"], 1)
        self.assertEqual(m.validate_form_schema(v1), [])  # core fields injected
        self.assertIsNone(m.new_form_version("f1", v1, copy.deepcopy(v1["fields"]), name="Dinner"))
        changed = copy.deepcopy(v1["fields"])
        changed.append(text_field("extra"))
        v2 = m.new_form_version("f1", v1, changed)
        self.assertEqual(v2["version"], 2)
        self.assertNotEqual(v1["schemaHash"], v2["schemaHash"])
        self.assertEqual(len(v1["fields"]), len(v2["fields"]) - 1)  # v1 not mutated


class EventValidationTests(unittest.TestCase):
    base = {"title": "Picnic", "slug": "picnic-2026", "status": "draft"}

    def test_valid_and_defaults(self):
        self.assertEqual(m.validate_event(self.base), {})
        item = m.new_event("e1", self.base)
        self.assertTrue(item["access"]["guestsAllowed"])          # guests allowed by default
        self.assertEqual(item["access"], {"guestsAllowed": True})
        self.assertEqual(item["timezone"], "Asia/Tokyo")
        self.assertNotIn("startsAt", item)

    def test_rules(self):
        errs = m.validate_event({**self.base, "slug": "Bad Slug", "status": "published"})
        self.assertIn("slug", errs)
        self.assertIn("startsAt", errs)                            # publish needs a start
        errs = m.validate_event({**self.base, "startsAt": "2026-09-02T10:00:00+09:00",
                                 "endsAt": "2026-09-01T10:00:00+09:00", "capacity": -1,
                                 "design": {"layout": "nope", "accentColor": "red"},
                                 "access": {"guestsAllowed": "yes"}})
        for key in ("endsAt", "capacity", "design.layout", "design.accentColor", "access.guestsAllowed"):
            self.assertIn(key, errs)

    def test_ticket_type(self):
        ok = {"name": "Adult", "priceJpy": 1500, "quantityLimit": 10}
        self.assertEqual(m.validate_ticket_type(ok), {})
        self.assertIn("earlyBirdCutoff", m.validate_ticket_type({**ok, "earlyBirdPriceJpy": 800}))
        self.assertEqual(m.validate_ticket_type(
            {**ok, "earlyBirdPriceJpy": 800, "earlyBirdCutoff": "2026-08-01T00:00:00+09:00"}), {})
        errs = m.validate_ticket_type({**ok, "priceJpy": 10.5, "perPersonCap": 0})
        self.assertIn("priceJpy", errs)
        self.assertIn("perPersonCap", errs)


if __name__ == "__main__":
    unittest.main()
