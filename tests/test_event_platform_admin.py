"""Admin event API: roles, create/update/status/duplicate/delete, tickets, images, audit, sanitising."""

import base64
import importlib.util
import json
import os
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lambdas"))
sys.path.insert(0, str(ROOT / "lambdas" / "manage_event_platform"))
os.environ.setdefault("REGION", "ap-northeast-1")

import admin_roles  # noqa: E402
import event_model  # noqa: E402

_spec = importlib.util.spec_from_file_location("event_platform_handler", ROOT / "lambdas" / "manage_event_platform" / "handler.py")
handler = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(handler)
import store as real_store  # noqa: E402


class FakeStore:
    """In-memory stand-in with the same semantics as store.py (revision check, slug lock)."""
    RevisionConflict = real_store.RevisionConflict
    SlugTaken = real_store.SlugTaken

    def __init__(self):
        self.events, self.tickets, self.forms, self.audit, self.regs = {}, {}, {}, [], set()

    def list_events(self): return [dict(e) for e in self.events.values()]
    def get_event(self, i): return dict(self.events[i]) if i in self.events else None
    def slug_taken(self, s): return any(e["slug"] == s for e in self.events.values())

    def save_event(self, item, *, expected_revision, old_slug=""):
        cur = self.events.get(item["eventId"])
        if expected_revision is None:
            if cur: raise self.RevisionConflict()
        elif not cur or cur.get("revision") != expected_revision:
            raise self.RevisionConflict()
        if any(e["slug"] == item["slug"] and e["eventId"] != item["eventId"] for e in self.events.values()):
            raise self.SlugTaken()
        self.events[item["eventId"]] = dict(item)

    def delete_event(self, item): self.events.pop(item["eventId"], None)
    def list_tickets(self, e): return sorted((t for (ev, _), t in self.tickets.items() if ev == e), key=lambda t: t.get("sortOrder", 0))
    def get_ticket(self, e, t): return self.tickets.get((e, t))
    def put_ticket(self, item): self.tickets[(item["eventId"], item["ticketTypeId"])] = dict(item)
    def delete_ticket(self, e, t): self.tickets.pop((e, t), None)
    def has_registrations(self, e): return e in self.regs
    def latest_form(self, f):
        versions = [v for (fid, v) in self.forms if fid == f]
        return self.forms[(f, max(versions))] if versions else None
    def put_form(self, item):
        if (item["formId"], item["version"]) in self.forms:
            raise self.RevisionConflict()
        self.forms[(item["formId"], item["version"])] = item
    def list_form_versions(self, f):
        return [{k: v for k, v in self.forms[key].items() if k != "fields"} for key in sorted(self.forms) if key[0] == f]
    def get_form_version(self, f, v): return self.forms.get((f, v))
    def list_templates(self):
        latest = {}
        for (fid, v), item in self.forms.items():
            if item.get("kind") == "template" and (fid not in latest or v > latest[fid]["version"]):
                latest[fid] = item
        return list(latest.values())
    def delete_form(self, f):
        for key in [k for k in self.forms if k[0] == f]:
            del self.forms[key]
    def put_audit(self, record): self.audit.append(record)
    def list_audit(self, key, limit=100): return [a for a in self.audit if a["entityKey"] == key]


def req(method, path, body=None, groups="Admins", query=None):
    return {
        "httpMethod": method,
        "pathParameters": {"proxy": path},
        "queryStringParameters": query,
        "body": json.dumps(body) if body is not None else None,
        "requestContext": {"authorizer": {"claims": {"email": "a@example.com", "cognito:groups": groups}}},
    }


BASE = {"title": "Autumn Picnic", "slug": "autumn-picnic", "shortDescription": "Fun",
        "longDescription": "<p>Hello <strong>all</strong></p>", "startsAt": "2026-10-10T10:00:00+09:00",
        "endsAt": "2026-10-10T16:00:00+09:00", "capacity": 100}
TICKET = {"name": "Adult", "priceJpy": 1500, "quantityLimit": 50}


class ApiTestCase(unittest.TestCase):
    def setUp(self):
        self.store = FakeStore()
        patcher = mock.patch.object(handler, "store", self.store)
        patcher.start()
        self.addCleanup(patcher.stop)

    def call(self, *args, **kwargs):
        response = handler.lambda_handler(req(*args, **kwargs), None)
        return response["statusCode"], json.loads(response["body"])

    def create(self, **over):
        status, body = self.call("POST", "events", {**BASE, **over})
        self.assertEqual(status, 201, body)
        return body["event"]


class RoleTests(unittest.TestCase):
    def test_group_claim_formats(self):
        for raw in ("Admins", "[Admins EventManagers]", "Admins,EventManagers", ["Admins"]):
            self.assertIn("super_admin", admin_roles.roles_of(req("GET", "me", groups=raw)))
        self.assertEqual(admin_roles.roles_of(req("GET", "me", groups="EventManagers")), {"event_manager"})
        self.assertEqual(admin_roles.roles_of(req("GET", "me", groups="Other")), set())
        self.assertEqual(admin_roles.roles_of({}), set())


class AccessTests(ApiTestCase):
    def test_no_group_is_forbidden_everywhere(self):
        for method, path in (("GET", "events"), ("POST", "events"), ("GET", "me")):
            self.assertEqual(self.call(method, path, {} if method == "POST" else None, groups="Other")[0], 403)
        self.assertEqual(self.call("GET", "events", groups="")[0], 403)

    def test_event_manager_can_manage_but_not_delete(self):
        status, body = self.call("POST", "events", BASE, groups="EventManagers")
        self.assertEqual(status, 201)
        eid = body["event"]["eventId"]
        self.assertEqual(self.call("DELETE", f"events/{eid}", groups="EventManagers")[0], 403)
        self.assertIn(eid, self.store.events)
        self.assertEqual(self.call("DELETE", f"events/{eid}", groups="Admins")[0], 200)
        self.assertNotIn(eid, self.store.events)

    def test_me(self):
        self.assertEqual(self.call("GET", "me", groups="EventManagers")[1]["roles"], ["event_manager"])


class CapabilityAdminTests(ApiTestCase):
    """Per-event capabilities through the admin API: persisted, validated, and unsupported add-ons refused."""

    def test_capabilities_are_stored_when_valid(self):
        event = self.create(capabilities={"entrySlots": {"enabled": False}, "seatReservation": {"enabled": False}})
        self.assertEqual(event["capabilities"], {"seatReservation": {"enabled": False}, "entrySlots": {"enabled": False}})

    def test_events_without_capabilities_stay_without(self):
        self.assertNotIn("capabilities", self.create())

    def test_enabling_an_addon_that_native_events_cannot_book_is_refused(self):
        for key in ("seatReservation", "zoneUpgrade", "entrySlots", "celebrityDinner"):
            status, body = self.call("POST", "events", {**BASE, "capabilities": {key: {"enabled": True}}})
            self.assertEqual(status, 400, key)
            self.assertIn(f"capabilities.{key}", body["fields"])
        self.assertEqual(self.store.events, {})

    def test_unknown_or_malformed_capabilities_are_refused(self):
        for bad in ({"guestCheckout": {"enabled": True}}, {"entrySlots": True}, {"seatReservation": {"enabled": "yes"}}, "all", ["x"]):
            self.assertEqual(self.call("POST", "events", {**BASE, "capabilities": bad})[0], 400, bad)

    def test_update_applies_the_same_rules(self):
        event = self.create()
        body = {**BASE, "revision": event["revision"], "capabilities": {"zoneUpgrade": {"enabled": True}}}
        status, resp = self.call("PUT", f"events/{event['eventId']}", body)
        self.assertEqual(status, 400)
        self.assertIn("capabilities.zoneUpgrade", resp["fields"])
        body["capabilities"] = {"zoneUpgrade": {"enabled": False}}
        self.assertEqual(self.call("PUT", f"events/{event['eventId']}", body)[0], 200)

    def test_requires_an_admin_group(self):
        self.assertEqual(self.call("POST", "events", {**BASE, "capabilities": {}}, groups="Other")[0], 403)


class EventCrudTests(ApiTestCase):
    def test_create_defaults_form_and_audit(self):
        ev = self.create()
        self.assertEqual((ev["status"], ev["revision"]), ("draft", 1))
        self.assertTrue(ev["access"]["guestsAllowed"])
        form = self.store.latest_form(ev["formId"])
        self.assertEqual(event_model.validate_form_schema(form), [])
        self.assertEqual(self.store.audit[0]["action"], "create")

    def test_validation_and_slug_conflict(self):
        status, body = self.call("POST", "events", {**BASE, "slug": "Bad Slug"})
        self.assertEqual(status, 400)
        self.assertIn("slug", body["fields"])
        self.create()
        status, body = self.call("POST", "events", BASE)
        self.assertEqual((status, "slug" in body["fields"]), (409, True))

    def test_update_needs_current_revision_and_logs_diff(self):
        ev = self.create()
        eid = ev["eventId"]
        self.assertEqual(self.call("PUT", f"events/{eid}", {"title": "New"})[0], 400)   # no revision
        status, body = self.call("PUT", f"events/{eid}", {"revision": 1, "title": "New title", "capacity": 80})
        self.assertEqual(status, 200)
        self.assertEqual((body["event"]["title"], body["event"]["revision"]), ("New title", 2))
        self.assertEqual(body["event"]["slug"], "autumn-picnic")                         # untouched fields kept
        self.assertEqual(set(self.store.audit[-1]["changes"]), {"title", "capacity"})
        status, body = self.call("PUT", f"events/{eid}", {"revision": 1, "title": "Stale"})
        self.assertEqual(status, 409)                                                    # lost update refused

    def test_server_owned_keys_cannot_be_set(self):
        ev = self.create()
        status, body = self.call("PUT", f"events/{ev['eventId']}", {
            "revision": 1, "status": "published", "source": "legacy", "createdBy": "x", "formId": "other", "eventId": "evil"})
        self.assertEqual(status, 200)
        e = body["event"]
        self.assertEqual((e["status"], e["formId"], e["eventId"]), ("draft", ev["formId"], ev["eventId"]))

    def test_description_is_sanitised(self):
        ev = self.create(longDescription='<p onclick="x()">Hi</p><script>alert(1)</script><a href="javascript:alert(1)">bad</a>'
                                         '<a href="https://ok.example">ok</a><img src=x onerror=y>')
        d = ev["longDescription"]
        for bad in ("script", "onclick", "javascript:", "<img", "alert"):
            self.assertNotIn(bad, d)
        self.assertIn('href="https://ok.example"', d)
        self.assertIn("<p>Hi</p>", d)

    def test_section_content_is_sanitised_and_kept(self):
        sections = [{"key": "faq", "enabled": True, "content": "<p>Q?</p><script>x()</script><b onclick=1>A</b>"},
                    {"key": "agenda", "enabled": False}]
        ev = self.create(design={"sections": sections})
        faq = ev["design"]["sections"][0]
        self.assertEqual(faq["content"], "<p>Q?</p><b>A</b>")
        self.assertNotIn("content", ev["design"]["sections"][1])
        status, body = self.call("PUT", f"events/{ev['eventId']}", {"revision": 1, "design": {"sections": [{"key": "faq", "enabled": True, "content": "x" * 20001}]}})
        self.assertEqual((status, "design.sections" in body["fields"]), (400, True))

    def test_list_filters_by_status(self):
        a = self.create()
        self.create(slug="other", title="Other")
        self.call("POST", f"events/{a['eventId']}/tickets", TICKET)
        self.call("POST", f"events/{a['eventId']}/status", {"status": "published"})
        self.assertEqual(self.call("GET", "events", query={"status": "published"})[1]["count"], 1)
        self.assertEqual(self.call("GET", "events")[1]["count"], 2)


class StatusTests(ApiTestCase):
    def test_publish_requires_ticket_and_start(self):
        ev = self.create()
        eid = ev["eventId"]
        status, body = self.call("POST", f"events/{eid}/status", {"status": "published"})
        self.assertEqual(status, 400)
        self.assertIn("tickets", body["fields"])
        self.call("POST", f"events/{eid}/tickets", TICKET)
        self.assertEqual(self.call("POST", f"events/{eid}/status", {"status": "published"})[0], 200)
        self.assertEqual(self.call("POST", f"events/{eid}/status", {"status": "bogus"})[0], 400)
        for s in ("unlisted", "closed", "archived", "draft"):
            self.assertEqual(self.call("POST", f"events/{eid}/status", {"status": s})[0], 200)

    def test_publish_without_start_time_is_refused(self):
        ev = self.create(startsAt=None)
        self.call("POST", f"events/{ev['eventId']}/tickets", TICKET)
        status, body = self.call("POST", f"events/{ev['eventId']}/status", {"status": "published"})
        self.assertEqual((status, "startsAt" in body["fields"]), (400, True))

    def test_status_change_bumps_revision(self):
        ev = self.create()
        self.call("POST", f"events/{ev['eventId']}/status", {"status": "closed"})
        self.assertEqual(self.store.events[ev["eventId"]]["revision"], 2)


class DuplicateDeleteTests(ApiTestCase):
    def test_duplicate_copies_tickets_and_form(self):
        ev = self.create()
        eid = ev["eventId"]
        self.call("POST", f"events/{eid}/tickets", TICKET)
        status, body = self.call("POST", f"events/{eid}/duplicate")
        self.assertEqual(status, 201)
        copy = body["event"]
        self.assertEqual((copy["status"], copy["slug"], copy["revision"]), ("draft", "autumn-picnic-copy", 1))
        self.assertNotEqual(copy["formId"], ev["formId"])
        self.assertEqual(len(self.store.list_tickets(copy["eventId"])), 1)
        self.assertEqual(event_model.validate_form_schema(self.store.latest_form(copy["formId"])), [])
        self.assertEqual(self.call("POST", f"events/{eid}/duplicate")[1]["event"]["slug"], "autumn-picnic-copy-2")

    def test_delete_blocked_by_registrations(self):
        ev = self.create()
        self.store.regs.add(ev["eventId"])
        status, body = self.call("DELETE", f"events/{ev['eventId']}")
        self.assertEqual(status, 409)
        self.assertIn(ev["eventId"], self.store.events)


class TicketTests(ApiTestCase):
    def test_crud_and_validation(self):
        eid = self.create()["eventId"]
        status, body = self.call("POST", f"events/{eid}/tickets", {**TICKET, "priceJpy": 10.5})
        self.assertEqual((status, "priceJpy" in body["fields"]), (400, True))
        status, body = self.call("POST", f"events/{eid}/tickets", {**TICKET, "earlyBirdPriceJpy": 800,
                                                                  "earlyBirdCutoff": "2026-09-01T00:00:00+09:00"})
        self.assertEqual(status, 201)
        tid = body["ticket"]["ticketTypeId"]
        status, body = self.call("PUT", f"events/{eid}/tickets/{tid}", {**TICKET, "name": "Adult+", "active": False})
        self.assertEqual((status, body["ticket"]["name"], body["ticket"]["active"]), (200, "Adult+", False))
        self.assertEqual(self.call("PUT", f"events/{eid}/tickets/nope", TICKET)[0], 404)
        self.store.regs.add(eid)
        self.assertEqual(self.call("DELETE", f"events/{eid}/tickets/{tid}")[0], 409)
        self.store.regs.clear()
        self.assertEqual(self.call("DELETE", f"events/{eid}/tickets/{tid}")[0], 200)

    def test_inactive_only_tickets_do_not_allow_publishing(self):
        eid = self.create()["eventId"]
        self.call("POST", f"events/{eid}/tickets", {**TICKET, "active": False})
        self.assertEqual(self.call("POST", f"events/{eid}/status", {"status": "published"})[0], 400)


class ImageTests(ApiTestCase):
    PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 20

    def test_upload_validates_type_content_and_size(self):
        eid = self.create()["eventId"]
        s3 = mock.Mock()
        with mock.patch.object(handler, "s3", return_value=s3), \
             mock.patch.object(handler, "ASSETS_BUCKET", "bucket"), \
             mock.patch.object(handler, "ASSETS_BASE_URL", "https://cdn.example"):
            ok = self.call("POST", f"events/{eid}/images", {"contentType": "image/png", "data": base64.b64encode(self.PNG).decode()})
            self.assertEqual(ok[0], 201)
            self.assertTrue(ok[1]["url"].startswith("https://cdn.example/media/events/" + eid + "/"))
            self.assertEqual(s3.put_object.call_args.kwargs["ContentType"], "image/png")
            bad = (
                {"contentType": "image/svg+xml", "data": "AAAA"},
                {"contentType": "image/png", "data": base64.b64encode(b"<html>not a png</html>").decode()},
                {"contentType": "image/png", "data": "!!!"},
                {"contentType": "image/png", "data": base64.b64encode(self.PNG + b"0" * handler.MAX_IMAGE_BYTES).decode()},
            )
            for body in bad:
                self.assertEqual(self.call("POST", f"events/{eid}/images", body)[0], 400)
            self.assertEqual(s3.put_object.call_count, 1)


class RoutingTests(ApiTestCase):
    def test_unknown_routes_and_bad_json(self):
        self.assertEqual(self.call("GET", "nothing")[0], 404)
        self.assertEqual(self.call("PATCH", "events")[0], 405)
        self.assertEqual(self.call("GET", "events/missing")[0], 404)
        bad = req("POST", "events")
        bad["body"] = "{not json"
        self.assertEqual(handler.lambda_handler(bad, None)["statusCode"], 400)
        self.assertEqual(handler.lambda_handler({"httpMethod": "OPTIONS"}, None)["statusCode"], 200)

    def test_audit_endpoint(self):
        eid = self.create()["eventId"]
        self.assertEqual(len(self.call("GET", f"events/{eid}/audit")[1]["entries"]), 1)


def custom_field(key="dietary", **kw):
    return {"key": key, "type": "text", "label": key.title(), **kw}


class FormApiTests(ApiTestCase):
    def setUp(self):
        super().setUp()
        self.ev = self.create()
        self.eid = self.ev["eventId"]

    def form(self):
        status, body = self.call("GET", f"events/{self.eid}/form")
        self.assertEqual(status, 200, body)
        return body

    def put(self, fields, base=None, **kw):
        base = base if base is not None else self.form()["form"]["version"]
        return self.call("PUT", f"events/{self.eid}/form", {"fields": fields, "baseVersion": base}, **kw)

    def test_new_event_has_core_only_form_v1(self):
        body = self.form()
        self.assertEqual(body["form"]["version"], 1)
        self.assertEqual([f["key"] for f in body["form"]["fields"]], list(event_model.CORE_KEYS))
        self.assertEqual(len(body["versions"]), 1)

    def test_save_creates_new_immutable_version_and_audit(self):
        v1 = self.form()["form"]
        status, body = self.put(v1["fields"] + [custom_field()])
        self.assertEqual((status, body["form"]["version"], body["changed"]), (200, 2, True))
        self.assertEqual(len(self.store.forms[(v1["formId"], 1)]["fields"]), len(event_model.CORE_KEYS))   # v1 untouched
        self.assertEqual(self.store.audit[-1]["changes"], {"added": ["dietary"]})
        status, body = self.put(body["form"]["fields"], base=2)
        self.assertEqual((status, body["changed"], body["form"]["version"]), (200, False, 2))             # no-op

    def test_core_fields_cannot_be_removed_or_weakened_through_the_api(self):
        v1 = self.form()["form"]
        fields = [f for f in v1["fields"] if f["key"] != "consentPrivacy"]
        for f in fields:
            if f["key"] == "consentTerms":
                f["required"] = False
            if f["key"] == "membershipId":
                f["source"] = "user"
                f["readOnly"] = False
        status, body = self.put(fields)
        self.assertEqual(status, 200)
        saved = {f["key"]: f for f in body["form"]["fields"]}
        self.assertIn("consentPrivacy", saved)
        self.assertTrue(saved["consentTerms"]["required"])
        self.assertEqual(saved["email"]["source"], "profile")
        self.assertTrue(all(f["locked"] for k, f in saved.items() if k in event_model.CORE_KEYS))

    def test_relabel_and_reorder_core_allowed(self):
        fields = self.form()["form"]["fields"]
        fields.insert(0, fields.pop(2))
        fields[0]["label"] = "Mobile"
        status, body = self.put(fields)
        self.assertEqual((status, body["form"]["fields"][0]["key"], body["form"]["fields"][0]["label"]), (200, "phone", "Mobile"))
        self.assertTrue(self.store.audit[-1]["changes"].get("reordered"))

    def test_invalid_form_is_rejected_with_reasons(self):
        fields = self.form()["form"]["fields"]
        status, body = self.put(fields + [custom_field(showIf={"all": [{"field": "ghost", "op": "notEmpty"}]}),
                                          {"key": "pick", "type": "dropdown", "label": "Pick"}])
        self.assertEqual(status, 400)
        text = " ".join(body["fields"].values())
        self.assertIn("ghost", text)
        self.assertIn("options", text)
        self.assertEqual(self.form()["form"]["version"], 1)

    def test_stale_base_version_conflicts(self):
        fields = self.form()["form"]["fields"]
        self.assertEqual(self.put(fields + [custom_field("a")])[0], 200)
        self.assertEqual(self.put(fields + [custom_field("b")], base=1)[0], 409)
        self.assertEqual(self.call("PUT", f"events/{self.eid}/form", {"fields": fields})[0], 400)         # base required

    def test_type_changes_blocked_once_registrations_exist(self):
        fields = self.form()["form"]["fields"] + [custom_field("age")]
        self.assertEqual(self.put(fields)[0], 200)
        changed = [dict(f, type="number") if f["key"] == "age" else f for f in fields]
        self.assertEqual(self.put(changed)[0], 200)                                # allowed while nobody registered
        self.store.regs.add(self.eid)
        back = [dict(f, type="text") if f["key"] == "age" else f for f in changed]
        status, body = self.put(back)
        self.assertEqual(status, 400)
        self.assertIn("cannot become", " ".join(body["fields"].values()))
        extra = changed + [custom_field("ageText")]
        self.assertEqual(self.put(extra)[0], 200)                                  # adding fields is fine

    def test_versions_listing_and_fetch(self):
        fields = self.form()["form"]["fields"]
        self.put(fields + [custom_field()])
        body = self.form()
        self.assertEqual([v["version"] for v in body["versions"]], [1, 2])
        self.assertNotIn("fields", body["versions"][0])
        old = self.call("GET", f"events/{self.eid}/form/versions/1")[1]["form"]
        self.assertEqual(len(old["fields"]), len(event_model.CORE_KEYS))
        self.assertEqual(self.call("GET", f"events/{self.eid}/form/versions/9")[0], 404)
        self.assertEqual(self.call("GET", f"events/{self.eid}/form/versions/x")[0], 404)

    def test_validate_endpoint_uses_server_rules(self):
        fields = self.form()["form"]["fields"] + [custom_field("diet", required=True)]
        self.put(fields)
        good = {"fullName": "Ann", "email": "a@b.co", "phone": "+81 90 1234 5678", "consentPrivacy": True, "consentTerms": True, "diet": "x"}
        status, body = self.call("POST", f"events/{self.eid}/form/validate", {"answers": good})
        self.assertEqual((status, body["errors"]), (200, {}))
        status, body = self.call("POST", f"events/{self.eid}/form/validate", {"answers": {**good, "diet": ""}})
        self.assertEqual(set(body["errors"]), {"diet"})

    def test_unknown_events(self):
        self.assertEqual(self.call("GET", "events/none/form")[0], 404)

    def test_event_manager_can_edit_form(self):
        fields = self.form()["form"]["fields"]
        self.assertEqual(self.put(fields + [custom_field()], groups="EventManagers")[0], 200)
        self.assertEqual(self.put(fields, groups="Other", base=2)[0], 403)


class TemplateApiTests(ApiTestCase):
    def setUp(self):
        super().setUp()
        self.a = self.create()
        self.b = self.create(slug="second", title="Second")

    def fields_of(self, ev):
        return self.call("GET", f"events/{ev['eventId']}/form")[1]["form"]

    def test_save_from_event_list_get_and_apply(self):
        fa = self.fields_of(self.a)
        extra = {"key": "dietary", "type": "text", "label": "Dietary"}
        self.call("PUT", f"events/{self.a['eventId']}/form", {"fields": fa["fields"] + [extra], "baseVersion": 1})
        status, body = self.call("POST", "form-templates", {"name": "Dinner", "fromEventId": self.a["eventId"]})
        self.assertEqual((status, body["template"]["fieldCount"]), (201, len(event_model.CORE_KEYS) + 1))
        tid = body["template"]["templateId"]
        self.assertEqual([t["name"] for t in self.call("GET", "form-templates")[1]["templates"]], ["Dinner"])
        self.assertEqual(self.call("GET", f"form-templates/{tid}")[1]["template"]["fields"][-1]["key"], "dietary")
        status, body = self.call("POST", f"events/{self.b['eventId']}/form/apply", {"templateId": tid, "baseVersion": 1})
        self.assertEqual((status, body["form"]["version"]), (200, 2))
        self.assertIn("dietary", [f["key"] for f in body["form"]["fields"]])
        self.assertEqual(body["form"]["formId"], self.b["formId"])              # the event keeps its own form id
        self.assertEqual(self.store.latest_form(tid)["kind"], "template")        # template unchanged

    def test_copy_from_another_event(self):
        fa = self.fields_of(self.a)
        self.call("PUT", f"events/{self.a['eventId']}/form", {"fields": fa["fields"] + [{"key": "t", "type": "text", "label": "T"}], "baseVersion": 1})
        status, body = self.call("POST", f"events/{self.b['eventId']}/form/apply", {"copyFromEventId": self.a["eventId"], "baseVersion": 1})
        self.assertEqual(status, 200)
        self.assertIn("t", [f["key"] for f in body["form"]["fields"]])
        self.assertEqual(self.call("POST", f"events/{self.b['eventId']}/form/apply", {"baseVersion": 2})[0], 400)
        self.assertEqual(self.call("POST", f"events/{self.b['eventId']}/form/apply", {"templateId": "tpl_x", "baseVersion": 2})[0], 404)
        self.assertEqual(self.call("POST", f"events/{self.b['eventId']}/form/apply", {"templateId": self.a["formId"], "baseVersion": 2})[0], 404)   # an event form is not a template

    def test_template_from_raw_fields_enforces_core_and_validity(self):
        status, body = self.call("POST", "form-templates", {"name": "Bare", "fields": [{"key": "x", "type": "text", "label": "X"}]})
        self.assertEqual(status, 201)
        full = self.call("GET", f"form-templates/{body['template']['templateId']}")[1]["template"]["fields"]
        self.assertEqual({f["key"] for f in full} & set(event_model.CORE_KEYS), set(event_model.CORE_KEYS))
        self.assertEqual(self.call("POST", "form-templates", {"name": "Bad", "fields": [{"key": "x", "type": "nope", "label": "X"}]})[0], 400)
        self.assertEqual(self.call("POST", "form-templates", {"name": "", "fields": []})[0], 400)
        self.assertEqual(self.call("POST", "form-templates", {"name": "n"})[0], 400)

    def test_delete_template_super_admin_only(self):
        tid = self.call("POST", "form-templates", {"name": "T", "fromEventId": self.a["eventId"]})[1]["template"]["templateId"]
        self.assertEqual(self.call("DELETE", f"form-templates/{tid}", groups="EventManagers")[0], 403)
        self.assertEqual(self.call("DELETE", f"form-templates/{tid}")[0], 200)
        self.assertEqual(self.call("GET", f"form-templates/{tid}")[0], 404)
        self.assertEqual(self.call("DELETE", f"form-templates/{tid}")[0], 404)

    def test_applied_form_is_independent_of_source_later_edits(self):
        fa = self.fields_of(self.a)
        self.call("POST", f"events/{self.b['eventId']}/form/apply", {"copyFromEventId": self.a["eventId"], "baseVersion": 1})
        self.call("PUT", f"events/{self.a['eventId']}/form", {"fields": fa["fields"] + [{"key": "later", "type": "text", "label": "L"}], "baseVersion": 1})
        self.assertNotIn("later", [f["key"] for f in self.fields_of(self.b)["fields"]])


class ModelAdditionsTests(unittest.TestCase):
    def test_email_templates(self):
        ok = {"confirmation": {"subject": "Hi {name}", "body": "{event_title} {ticket_type} {registration_id}"}}
        self.assertEqual(event_model.validate_email_templates(ok), {})
        self.assertIn("{oops}", event_model.validate_email_templates({"reminder": {"subject": "{oops}", "body": ""}})["reminder"])
        self.assertIn("nope", event_model.validate_email_templates({"nope": {}}))

    def test_render_template_escapes_html_values(self):
        out = event_model.render_template("Hi {name} {unknown}", {"name": "<b>Ann</b>"}, html_escape=True)
        self.assertEqual(out, "Hi &lt;b&gt;Ann&lt;/b&gt; {unknown}")

    def test_event_url_and_section_rules(self):
        base = {"title": "T", "slug": "t"}
        errs = event_model.validate_event({**base, "location": {"mapUrl": "http://insecure"},
                                           "design": {"coverImage": "javascript:x",
                                                      "sections": [{"key": "faq"}, {"key": "faq"}]}})
        for key in ("location.mapUrl", "design.coverImage", "design.sections"):
            self.assertIn(key, errs)


class StoreTransactionTests(unittest.TestCase):
    """store.save_event builds the right transaction and maps cancellations to errors."""

    def run_save(self, item, expected, old_slug="", fail_index=None):
        from botocore.exceptions import ClientError
        client = mock.Mock()
        if fail_index is not None:
            reasons = [{"Code": "None"}] * 3
            reasons[fail_index] = {"Code": "ConditionalCheckFailed"}
            client.transact_write_items.side_effect = ClientError(
                {"Error": {"Code": "TransactionCanceledException"}, "CancellationReasons": reasons}, "TransactWriteItems")
        with mock.patch.object(real_store, "client", client):
            real_store.save_event(item, expected_revision=expected, old_slug=old_slug)
        return client.transact_write_items.call_args.kwargs["TransactItems"]

    ITEM = {"eventId": "evt_1", "slug": "new-slug", "revision": 2}

    def test_create_locks_slug(self):
        ops = self.run_save(self.ITEM, None)
        self.assertEqual(len(ops), 2)
        self.assertIn("attribute_not_exists", ops[0]["Put"]["ConditionExpression"])
        self.assertEqual(ops[1]["Put"]["Item"]["eventId"]["S"], "SLUG#new-slug")

    def test_update_same_slug_only_checks_revision(self):
        ops = self.run_save(self.ITEM, 1, old_slug="new-slug")
        self.assertEqual(len(ops), 1)
        self.assertEqual(ops[0]["Put"]["ConditionExpression"], "revision = :rev")

    def test_rename_moves_lock(self):
        ops = self.run_save(self.ITEM, 1, old_slug="old-slug")
        self.assertEqual(ops[2]["Delete"]["Key"]["eventId"]["S"], "SLUG#old-slug")

    def test_cancellation_mapping(self):
        with self.assertRaises(real_store.RevisionConflict):
            self.run_save(self.ITEM, 1, old_slug="x", fail_index=0)
        with self.assertRaises(real_store.SlugTaken):
            self.run_save(self.ITEM, 1, old_slug="x", fail_index=1)


if __name__ == "__main__":
    unittest.main()
