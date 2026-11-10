"""Event platform data model: core fields, form schemas, events, legacy mirroring."""

import copy
import importlib.util
import sys
import unittest
from decimal import Decimal
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lambdas"))

import event_model as m  # noqa: E402

_spec = importlib.util.spec_from_file_location("migrate_events_to_v2", ROOT / "scripts" / "migrate_events_to_v2.py")
migrate = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(migrate)


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
            if f["key"] == "membershipId":
                f["readOnly"] = False
                f["source"] = "user"
        self.assertTrue(m.validate_form_schema({"fields": fields}))
        fixed = {f["key"]: f for f in m.ensure_core_fields(fields)}
        self.assertTrue(fixed["consentTerms"]["required"])
        self.assertTrue(fixed["membershipId"]["readOnly"])
        self.assertEqual(fixed["membershipId"]["source"], "system")

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
        self.assertTrue(item["access"]["requireRenewalCover"])
        self.assertEqual(item["timezone"], "Asia/Tokyo")
        self.assertNotIn("startsAt", item)

    def test_rules(self):
        errs = m.validate_event({**self.base, "slug": "Bad Slug", "status": "published"})
        self.assertIn("slug", errs)
        self.assertIn("startsAt", errs)                            # publish needs a start
        errs = m.validate_event({**self.base, "startsAt": "2026-09-02T10:00:00+09:00",
                                 "endsAt": "2026-09-01T10:00:00+09:00", "capacity": -1,
                                 "design": {"layout": "nope", "accentColor": "red"},
                                 "access": {"membersOnly": True, "guestsAllowed": True}})
        for key in ("endsAt", "capacity", "design.layout", "design.accentColor", "access.guestsAllowed"):
            self.assertIn(key, errs)

    def test_ticket_type(self):
        ok = {"name": "Adult", "memberPriceJpy": 1000, "nonMemberPriceJpy": 1500, "quantityLimit": 10}
        self.assertEqual(m.validate_ticket_type(ok), {})
        self.assertIn("earlyBirdCutoff", m.validate_ticket_type({**ok, "earlyBirdMemberPriceJpy": 800}))
        self.assertEqual(m.validate_ticket_type(
            {**ok, "earlyBirdMemberPriceJpy": 800, "earlyBirdCutoff": "2026-08-01T00:00:00+09:00"}), {})
        errs = m.validate_ticket_type({**ok, "memberPriceJpy": 10.5, "perPersonCap": 0})
        self.assertIn("memberPriceJpy", errs)
        self.assertIn("perPersonCap", errs)


class MembershipRuleTests(unittest.TestCase):
    def test_status_and_members_only_gate(self):
        active = {"validNow": True, "coversRenewal": True, "latestTo": "2027-03-31"}
        lapsed_renewal = {"validNow": True, "coversRenewal": False, "latestTo": "2026-03-31"}
        expired = {"validNow": False, "coversRenewal": False, "latestTo": "2025-03-31"}
        never = {"validNow": False, "coversRenewal": False, "latestTo": ""}
        self.assertEqual([m.membership_status(s) for s in (active, expired, never, None)],
                         ["active", "expired", "none", "none"])
        self.assertTrue(m.meets_membership_requirement(active))
        self.assertFalse(m.meets_membership_requirement(lapsed_renewal))   # must cover renewal year
        self.assertTrue(m.meets_membership_requirement(lapsed_renewal, require_renewal_cover=False))
        self.assertFalse(m.meets_membership_requirement(expired))
        self.assertFalse(m.meets_membership_requirement(None))


LEGACY_EVENT = {
    "eventType": "onam_2026", "eventName": "Onam 2026", "eventDate": "September 20, 2026",
    "hostId": "h", "status": "active", "description": "Celebration", "venue": "Hall",
    "venueAddress": "Edogawa", "capacity": Decimal("350"), "registrationPath": "/onam/register",
    "registration": {"closesOn": "2026-09-10", "sadhyaCapacity": Decimal("300")},
    "eventInfo": {"payment": {"bank": "x"}}, "createdAt": "2026-01-01T00:00:00+00:00",
}
LEGACY_REG = {
    "registrationId": "r1", "eventType": "onam_2026", "email": " Ann@Example.com ", "name": "Ann",
    "phone": "+81 90", "submittedAt": "2026-08-01T00:00:00+00:00", "paymentStatus": "paid",
    "totalAmount": Decimal("5000"), "registrationOption": "combo",
    "attendeeCounts": {"adults": Decimal("2")}, "registrationPassToken": "tok", "npoName": "",
}


class LegacyMirrorTests(unittest.TestCase):
    def test_event_mirror(self):
        ev = m.legacy_event_to_v2(LEGACY_EVENT)
        self.assertEqual(ev["eventId"], "legacy-onam_2026")
        self.assertEqual(ev["slug"], "onam-2026")
        self.assertEqual(ev["status"], "published")
        self.assertEqual(ev["startsAt"], "2026-09-20T00:00:00+09:00")
        self.assertEqual(ev["registrationClosesAt"], "2026-09-10T23:59:59+09:00")
        self.assertEqual(ev["capacity"], 350)
        self.assertEqual(ev["registrationMode"], "legacy")
        self.assertEqual(ev["registrationPath"], "/onam/register")
        self.assertEqual(ev["legacyEventDate"], "September 20, 2026")
        self.assertNotIn("", ev.values())
        self.assertEqual(m.legacy_event_to_v2(LEGACY_EVENT), ev)   # deterministic

    def test_unparseable_date_omits_startsat(self):
        ev = m.legacy_event_to_v2({**LEGACY_EVENT, "eventDate": "TBD autumn"})
        self.assertNotIn("startsAt", ev)
        self.assertEqual(ev["legacyEventDate"], "TBD autumn")

    def test_registration_mirror_loses_nothing(self):
        reg = m.legacy_registration_to_v2(LEGACY_REG)
        self.assertEqual(reg["registrationId"], "legacy-r1")
        self.assertEqual(reg["eventId"], "legacy-onam_2026")
        self.assertEqual(reg["email"], "ann@example.com")
        self.assertEqual(reg["paymentStatus"], "paid")
        self.assertEqual(reg["totalJpy"], 5000)
        self.assertEqual(reg["answers"]["registrationOption"], "combo")
        self.assertEqual(reg["answers"]["attendeeCounts"], {"adults": 2})
        self.assertEqual(reg["legacyRegistrationId"], "r1")


class FakeTable:
    def __init__(self, items=None, key="id"):
        self.items = {i[key]: dict(i) for i in (items or [])}
        self.key = key

    def scan(self, **_):
        return {"Items": [dict(i) for i in self.items.values()]}

    def put_item(self, Item, ConditionExpression=None):
        from botocore.exceptions import ClientError
        if ConditionExpression and Item[self.key] in self.items:
            raise ClientError({"Error": {"Code": "ConditionalCheckFailedException"}}, "PutItem")
        self.items[Item[self.key]] = dict(Item)

    def delete_item(self, Key):
        self.items.pop(Key[self.key], None)


class MigrationScriptTests(unittest.TestCase):
    def test_plan_flags_problems(self):
        events, regs, problems = migrate.build_plan(
            [LEGACY_EVENT, {"eventType": "onam-2026", "eventName": "dup slug"}],
            [LEGACY_REG, {**LEGACY_REG, "registrationId": "r2", "eventType": "gone"}, {"eventType": "x"}],
        )
        self.assertEqual((len(events), len(regs)), (2, 2))
        text = " | ".join(problems)
        self.assertIn("slug collision", text)
        self.assertIn("unknown event", text)
        self.assertIn("without registrationId", text)

    def test_idempotent_write_refresh_and_rollback(self):
        events, regs, _ = migrate.build_plan([LEGACY_EVENT], [LEGACY_REG])
        et, rt = FakeTable(key="eventId"), FakeTable(key="registrationId")
        self.assertEqual(migrate.put_missing(et, events, "eventId", False), (1, 0))
        self.assertEqual(migrate.put_missing(et, events, "eventId", False), (0, 1))      # re-run skips
        et.items["legacy-onam_2026"]["title"] = "edited"
        migrate.put_missing(et, events, "eventId", False)
        self.assertEqual(et.items["legacy-onam_2026"]["title"], "edited")                # not clobbered
        migrate.put_missing(et, events, "eventId", True)
        self.assertEqual(et.items["legacy-onam_2026"]["title"], "Onam 2026")             # --refresh
        migrate.put_missing(rt, regs, "registrationId", False)
        native = {"registrationId": "native-1", "source": "native"}
        rt.items["native-1"] = native
        with mock.patch.object(migrate, "scan_all", lambda t: list(t.items.values())):
            self.assertEqual(migrate.rollback(rt, "registrationId"), 1)
        self.assertEqual(list(rt.items), ["native-1"])                                   # native untouched


if __name__ == "__main__":
    unittest.main()
