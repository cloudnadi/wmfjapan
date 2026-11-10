"""Server-side validation of dynamic form submissions."""

import copy
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lambdas"))

import event_model as m  # noqa: E402
import form_engine as fe  # noqa: E402

OPTS = [{"value": "yes", "label": "Yes"}, {"value": "no", "label": "No"}]


def schema(*extra):
    s = {"fields": m.ensure_core_fields(list(extra))}
    assert m.validate_form_schema(s) == [], m.validate_form_schema(s)
    return s


def f(key, ftype="text", **kw):
    return {"key": key, "type": ftype, "label": key.title(), **kw}


CORE_OK = {"fullName": "Ann Lee", "email": "ann@example.com", "phone": "+81 90-1234-5678",
           "consentPrivacy": True, "consentTerms": True}
CTX = {"email": "member@example.com"}


def run(s, answers, ctx=None, core=True):
    return fe.validate_submission(s, {**(CORE_OK if core else {}), **answers}, ctx)


class CoreFieldTests(unittest.TestCase):
    def test_minimal_valid_guest(self):
        clean, errors = run(schema(), {})
        self.assertEqual(errors, {})
        self.assertEqual(clean["email"], "ann@example.com")

    def test_unknown_membership_style_answers_are_dropped(self):
        clean, _ = run(schema(), {"membershipId": "M999", "membershipStatus": "active"})
        self.assertNotIn("membershipId", clean)
        self.assertNotIn("membershipStatus", clean)

    def test_logged_in_email_is_forced_from_account(self):
        clean, errors = run(schema(), {"email": "other@example.com"}, CTX)
        self.assertEqual((errors, clean["email"]), ({}, "member@example.com"))

    def test_required_core_fields_and_consents(self):
        _, errors = run(schema(), {"fullName": "", "consentPrivacy": False, "consentTerms": None}, core=True)
        self.assertEqual(set(errors), {"fullName", "consentPrivacy", "consentTerms"})
        _, errors = fe.validate_submission(schema(), {}, None)
        self.assertEqual({"fullName", "email", "phone", "consentPrivacy", "consentTerms"}, set(errors))

    def test_phone_is_mandatory_and_accepts_japanese_domestic_numbers(self):
        clean, errors = run(schema(), {"phone": "090-1234-5678"})
        self.assertEqual((errors, clean["phone"]), ({}, "+81 9012345678"))
        self.assertEqual(run(schema(), {"phone": "03 (1234) 5678"})[0]["phone"], "+81 312345678")
        self.assertIn("phone", run(schema(), {"phone": "12345"})[1])
        self.assertIn("phone", run(schema(), {"phone": "090-1234-567x"})[1])
        self.assertEqual(run(schema(), {"phone": ""})[1], {"phone": "Required"})
        self.assertEqual(run(schema(), {"phone": None})[1], {"phone": "Required"})

    def test_phone_stays_mandatory_on_forms_saved_before_it_was_required(self):
        old = [dict(f, required=False, label="Phone") if f["key"] == "phone" else f for f in m.default_form_fields()]
        without_phone = {k: v for k, v in CORE_OK.items() if k != "phone"}
        self.assertEqual(fe.validate_submission({"fields": old}, without_phone, None)[1], {"phone": "Required"})
        upgraded = {f["key"]: f for f in m.ensure_core_fields(old)}["phone"]
        self.assertEqual((upgraded["required"], upgraded["label"]), (True, "WhatsApp number (with country code)"))
        custom = [dict(f, label="Mobile") if f["key"] == "phone" else f for f in old]
        self.assertEqual({f["key"]: f for f in m.ensure_core_fields(custom)}["phone"]["label"], "Mobile")

    def test_hidden_core_field_tampering_is_impossible(self):
        s = schema()
        s["fields"] = [x for x in s["fields"] if x["key"] != "consentTerms"]      # a tampered schema is not accepted upstream
        self.assertTrue(m.validate_form_schema(s))


class FieldTypeTests(unittest.TestCase):
    def check(self, field, good, bad, key="x"):
        s = schema(field)
        for value in good:
            clean, errors = run(s, {key: value})
            self.assertEqual(errors, {}, (field["type"], value))
        for value in bad:
            _, errors = run(s, {key: value})
            self.assertIn(key, errors, (field["type"], value))

    def test_text_limits_regex(self):
        self.check(f("x", validation={"minLength": 2, "maxLength": 5, "regex": "^[a-z]+$"}),
                   ["ab", "abcde"], ["a", "abcdef", "ab1", 5, ["a"]])

    def test_hard_length_cap_without_rules(self):
        self.check(f("x"), ["a" * 1000], ["a" * 1001])
        self.check(f("x", "textarea"), ["a" * 5000], ["a" * 5001])

    def test_email_and_phone_types(self):
        self.check(f("x", "email"), ["a@b.co"], ["nope", "a@b", "a b@c.d"])
        self.check(f("x", "phone"), ["+81 90 1234 5678"], ["12345", "+8"])

    def test_number_bounds(self):
        self.check(f("x", "number", validation={"min": 1, "max": 10}), [1, 10, "5", 2.5], [0, 11, "abc", True, "NaN", "Infinity"])

    def test_choice_types(self):
        self.check(f("x", "dropdown", options=OPTS), ["yes"], ["maybe", ["yes"], 1])
        self.check(f("x", "radio", options=OPTS), ["no"], ["", "YES"] if False else ["YES"])
        self.check(f("x", "multiselect", options=OPTS, validation={"max": 1}), [["yes"], ["no"]], [["yes", "no"], ["maybe"], "yes", ["yes", "yes"]])

    def test_checkbox_and_date(self):
        self.check(f("x", "checkbox"), [True, False], ["yes", 1])
        self.check(f("x", "date", validation={"min": "2026-01-01", "max": "2026-12-31"}),
                   ["2026-06-15"], ["2025-12-31", "2027-01-01", "2026-02-30", "15/06/2026", 20260615])

    def test_required_checkbox_must_be_ticked(self):
        s = schema(f("x", "checkbox", required=True))
        self.assertIn("x", run(s, {"x": False})[1])
        self.assertIn("x", run(s, {})[1])
        self.assertEqual(run(s, {"x": True})[1], {})

    def test_optional_empty_is_fine_and_required_empty_is_not(self):
        self.assertEqual(run(schema(f("x")), {})[1], {})
        for blank in (None, "", "   ", []):
            self.assertIn("x", run(schema(f("x", required=True)), {"x": blank})[1])

    def test_file_reference(self):
        rules = {"maxSizeMb": 2, "allowedTypes": ["image/png"]}
        ok = {"fileId": "f1", "name": "../../evil name.png", "size": 1000, "contentType": "image/png"}
        s = schema(f("x", "file", validation=rules))
        clean, errors = run(s, {"x": ok})
        self.assertEqual(errors, {})
        self.assertNotIn("/", clean["x"]["name"])
        for bad in ({**ok, "contentType": "text/html"}, {**ok, "size": 3 * 1024 * 1024}, {**ok, "size": 0},
                    {"fileId": "f"}, "file.png", {**ok, "size": True}):
            self.assertIn("x", run(s, {"x": bad})[1])

    def test_unknown_keys_and_display_fields_are_dropped(self):
        s = schema(f("h", "heading"), f("i", "info"))
        clean, errors = run(s, {"h": "x", "i": "y", "evil": "z", "__proto__": "q"})
        self.assertEqual(errors, {})
        self.assertFalse({"h", "i", "evil", "__proto__"} & set(clean))

    def test_oversized_and_malformed_submissions(self):
        self.assertIn("", fe.validate_submission(schema(), ["x"])[1])
        self.assertIn("", fe.validate_submission(schema(), {"a": "x" * 200_000})[1])


class ConditionalTests(unittest.TestCase):
    S = staticmethod(lambda: schema(
        f("dinner", "radio", options=OPTS, required=True),
        f("diet", "text", required=True, showIf={"all": [{"field": "dinner", "op": "eq", "value": "yes"}]}),
        f("dietNote", "text", required=True, showIf={"all": [{"field": "diet", "op": "notEmpty"}]}),
    ))

    def test_hidden_required_field_is_not_required_and_is_discarded(self):
        clean, errors = run(self.S(), {"dinner": "no", "diet": "vegan", "dietNote": "x"})
        self.assertEqual(errors, {})
        self.assertNotIn("diet", clean)
        self.assertNotIn("dietNote", clean)           # depends on a hidden field -> hidden too

    def test_shown_required_field_is_enforced(self):
        _, errors = run(self.S(), {"dinner": "yes"})
        self.assertEqual(set(errors), {"diet"})
        _, errors = run(self.S(), {"dinner": "yes", "diet": "vegan"})
        self.assertEqual(set(errors), {"dietNote"})
        clean, errors = run(self.S(), {"dinner": "yes", "diet": "vegan", "dietNote": "no nuts"})
        self.assertEqual((errors, clean["diet"], clean["dietNote"]), ({}, "vegan", "no nuts"))


class GroupTests(unittest.TestCase):
    GROUP = staticmethod(lambda **kw: schema(
        f("hasGuests", "radio", options=OPTS),
        {"key": "guests", "type": "group", "label": "Guests", "maxRepeat": 2, **kw,
         "showIf": {"all": [{"field": "hasGuests", "op": "eq", "value": "yes"}]},
         "fields": [f("gname", required=True), f("age", "number", validation={"min": 0}),
                    f("note", showIf={"all": [{"field": "age", "op": "lt", "value": 18}]}, required=True)]}))

    def test_valid_entries_and_per_entry_errors(self):
        s = self.GROUP()
        clean, errors = run(s, {"hasGuests": "yes", "guests": [{"gname": "Bo", "age": 30}, {"gname": "Cy", "age": 5, "note": "kid"}]})
        self.assertEqual(errors, {})
        self.assertEqual([g["gname"] for g in clean["guests"]], ["Bo", "Cy"])
        _, errors = run(s, {"hasGuests": "yes", "guests": [{"gname": "Bo"}, {"age": 5}]})
        self.assertEqual(set(errors), {"guests[1].gname", "guests[1].note"})

    def test_max_repeat_required_and_hidden_group(self):
        s = self.GROUP()
        too_many = [{"gname": "a"}] * 3
        self.assertIn("guests", run(s, {"hasGuests": "yes", "guests": too_many})[1])
        self.assertIn("guests", run(self.GROUP(required=True), {"hasGuests": "yes", "guests": []})[1])
        clean, errors = run(s, {"hasGuests": "no", "guests": [{"gname": "ignored"}]})
        self.assertEqual(errors, {})
        self.assertNotIn("guests", clean)
        self.assertIn("guests", run(s, {"hasGuests": "yes", "guests": "x"})[1])
        self.assertIn("guests", run(s, {"hasGuests": "yes", "guests": ["x"]})[1])

    def test_child_condition_can_use_top_level_answers(self):
        s = schema(f("mode", "radio", options=OPTS),
                   {"key": "g", "type": "group", "label": "G", "maxRepeat": 2,
                    "fields": [f("extra", required=True, showIf={"all": [{"field": "mode", "op": "eq", "value": "yes"}]})]})
        self.assertEqual(run(s, {"mode": "no", "g": [{}]})[1], {})
        self.assertIn("g[0].extra", run(s, {"mode": "yes", "g": [{}]})[1])


class SchemaHardeningTests(unittest.TestCase):
    def test_nested_quantifier_regex_is_rejected(self):
        errs = m.validate_form_schema(schema_raw(f("x", validation={"regex": "(a+)+$"})))
        self.assertTrue(any("nested repetition" in e for e in errs))
        self.assertEqual(m.validate_form_schema(schema_raw(f("x", validation={"regex": r"^\d{3}-\d{4}$"}))), [])

    def test_min_max_types(self):
        errs = m.validate_form_schema(schema_raw(f("x", "number", validation={"min": "a", "max": 3})))
        self.assertTrue(errs)
        self.assertTrue(m.validate_form_schema(schema_raw(f("x", "number", validation={"min": 5, "max": 3}))))

    def test_child_may_reference_earlier_sibling(self):
        g = {"key": "g", "type": "group", "label": "G", "maxRepeat": 2, "fields": [
            f("a"), f("b", showIf={"all": [{"field": "a", "op": "notEmpty"}]})]}
        self.assertEqual(m.validate_form_schema(schema_raw(g)), [])
        g2 = copy.deepcopy(g)
        g2["fields"].reverse()
        self.assertTrue(m.validate_form_schema(schema_raw(g2)))


def schema_raw(*extra):
    return {"fields": m.ensure_core_fields(list(extra))}


if __name__ == "__main__":
    unittest.main()
