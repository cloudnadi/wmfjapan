"""Admin registration-management API: dashboard, table, manual add, actions, check-in scan, export, bulk e-mail."""

import base64
import csv
import importlib.util
import io
import json
import os
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
for sub in ("lambdas", "lambdas/manage_event_platform", "plugins/seat-upgrade/backend/lambdas", "tests"):
    sys.path.insert(0, str(ROOT / sub))
os.environ.setdefault("REGION", "ap-northeast-1")

import event_model  # noqa: E402
import event_registrations as logic  # noqa: E402
from event_fakes import FakePayments, LinkedStore  # noqa: E402
from test_event_platform_admin import BASE, FakeStore as AdminFakeStore, TICKET  # noqa: E402

spec = importlib.util.spec_from_file_location("ep_admin_regs", ROOT / "lambdas" / "manage_event_platform" / "handler.py")
handler = importlib.util.module_from_spec(spec)
spec.loader.exec_module(handler)

ANSWERS = {"fullName": "Ann Lee", "email": "ann@example.com", "phone": "+81 90-1234-5678", "consentPrivacy": True, "consentTerms": True}


def req(method, path, body=None, groups="Admins", query=None, email="admin@example.com"):
    return {"httpMethod": method, "pathParameters": {"proxy": path}, "queryStringParameters": query,
            "body": json.dumps(body) if body is not None else None,
            "requestContext": {"authorizer": {"claims": {"email": email, "cognito:groups": groups}}}}


class Base(unittest.TestCase):
    def setUp(self):
        self.admin_store = AdminFakeStore()
        self.regs = LinkedStore(self.admin_store)
        self.pay = FakePayments()
        self.pay.create_refund = lambda **kw: self.refunds.append(kw) or {"refundId": "rf-1", "status": "PENDING"}
        self.refunds, self.mails, self.bulk = [], [], []
        self.regs.config["payment_methods_config"] = {"methods": {"bank": {"enabled": True, "instructions": "Acct"}}}
        patches = [mock.patch.object(handler, "store", self.admin_store), mock.patch.object(handler, "regstore", self.regs),
                   mock.patch.object(handler, "square", self.pay),
                   mock.patch.object(handler.event_mailer, "send", lambda kind, reg, ev, methods: self.mails.append((kind, reg["registrationId"]))),
                   mock.patch.object(handler.event_mailer, "send_message", lambda to, subject, text: self.bulk.append((to, subject, text))),
                   mock.patch.object(handler.radm.time, "sleep", lambda s: None)]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        status, body = self.call("POST", "events", {**BASE, "capacity": 20, "waitlistEnabled": True})
        self.event = body["event"]
        self.eid = self.event["eventId"]
        self.adult = self.call("POST", f"events/{self.eid}/tickets", TICKET)[1]["ticket"]["ticketTypeId"]
        self.free = self.call("POST", f"events/{self.eid}/tickets", {"name": "Child", "priceJpy": 0})[1]["ticket"]["ticketTypeId"]

    def call(self, *args, **kwargs):
        res = handler.lambda_handler(req(*args, **kwargs), None)
        return res["statusCode"], json.loads(res["body"])

    def add(self, key="k", email="ann@example.com", tickets=None, **over):
        payload = {"tickets": tickets or {self.adult: 1}, "answers": {**ANSWERS, "email": email}, "idempotencyKey": key,
                   "paymentStatus": "pending", **over}
        status, body = self.call("POST", f"events/{self.eid}/registrations", payload)
        self.assertIn(status, (200, 201), body)
        return body["registration"]


class AccessTests(Base):
    def test_roles(self):
        for method, path in (("GET", "dashboard"), ("GET", "registrations"), ("POST", "check-in"), ("GET", "export")):
            self.assertEqual(self.call(method, f"events/{self.eid}/{path}", {} if method == "POST" else None, groups="Other")[0], 403, path)
        self.assertEqual(self.call("GET", f"events/{self.eid}/dashboard", groups="EventManagers")[0], 200)
        self.assertEqual(self.call("GET", f"events/{self.eid}/registrations", groups="EventManagers")[0], 200)

    def test_registration_of_another_event_is_404(self):
        reg = self.add()
        other = self.call("POST", "events", {**BASE, "slug": "other", "title": "Other"})[1]["event"]["eventId"]
        self.assertEqual(self.call("GET", f"events/{other}/registrations/{reg['registrationId']}")[0], 404)
        self.assertEqual(self.call("POST", f"events/{other}/registrations/{reg['registrationId']}/cancel", {})[0], 404)


class DashboardAndTableTests(Base):
    def test_dashboard_and_table(self):
        self.add("a", "a@example.com", paymentStatus="paid", paidVia="cash", tickets={self.adult: 2})
        self.add("b", "b@example.com")
        self.add("c", "c@example.com", tickets={self.free: 1})
        status, body = self.call("GET", f"events/{self.eid}/dashboard")
        d = body["dashboard"]
        self.assertEqual((status, d["total"], d["revenue"]["paidJpy"], d["revenue"]["pendingJpy"], d["capacity"]["used"]), (200, 3, 3000, 1500, 4))
        self.assertEqual({t["name"]: (t["confirmed"], t["pending"]) for t in d["tickets"]}, {"Adult": (2, 1), "Child": (1, 0)})
        status, body = self.call("GET", f"events/{self.eid}/registrations", query={"status": "pending_payment"})
        self.assertEqual((status, body["total"], body["rows"][0]["email"]), (200, 1, "b@example.com"))
        self.assertNotIn("checkInToken", json.dumps(body))
        self.assertEqual([c["key"] for c in body["columns"]][:3], ["fullName", "email", "phone"])
        self.assertEqual({t["name"] for t in body["tickets"]}, {"Adult", "Child"})


class PaymentSettingsTests(Base):
    def test_super_admin_saves_and_everyone_with_access_can_read(self):
        body = {"methods": {"bank": {"enabled": True, "instructions": " Account 123 "}, "paypay": {"enabled": True, "url": "https://pay.example/x"},
                            "square": {"enabled": True}}}
        status, resp = self.call("PUT", "payment-methods", {"methods": {"paypay": {"enabled": True, "url": "javascript:x"}}})
        self.assertEqual((status, "paypay.url" in resp["fields"]), (400, True))
        self.assertIn("square.url", self.call("PUT", "payment-methods", {"methods": {"square": {"url": "https://x.example"}}})[1]["fields"])
        status, resp = self.call("PUT", "payment-methods", body)
        self.assertEqual(status, 200, resp)
        stored = self.regs.config["payment_methods_config"]["methods"]
        self.assertEqual((stored["bank"]["instructions"], stored["square"]["url"], stored["paypay"]["enabled"]), ("Account 123", "", True))
        self.assertEqual(self.call("GET", "payment-methods")[1]["methods"]["bank"]["enabled"], True)
        self.assertTrue(any(a["entityKey"] == "SETTINGS#payments" for a in self.admin_store.audit))

    def test_event_managers_cannot_change_where_money_goes(self):
        self.assertEqual(self.call("PUT", "payment-methods", {"methods": {}}, groups="EventManagers")[0], 403)
        self.assertEqual(self.call("GET", "payment-methods", groups="EventManagers")[0], 200)


class ManualAddTests(Base):
    def test_add_with_audit(self):
        reg = self.add("a", paymentStatus="paid", paidVia="cash")
        self.assertEqual((reg["status"], reg["createdBy"], reg["addedByAdmin"]), ("confirmed", "admin@example.com", True))
        self.assertEqual(self.mails, [("confirmed", reg["registrationId"])])
        self.assertTrue(any(a["entityKey"] == f"REG#{reg['registrationId']}" and a["action"] == "create" for a in self.admin_store.audit))

    def test_validation_errors_are_structured(self):
        status, body = self.call("POST", f"events/{self.eid}/registrations", {"tickets": {self.adult: 1}, "answers": {**ANSWERS, "fullName": ""}})
        self.assertEqual((status, "fullName" in body["fields"]), (400, True))
        status, body = self.call("POST", f"events/{self.eid}/registrations", {"tickets": {self.adult: 1}, "answers": ANSWERS, "totalJpy": 0})
        self.assertEqual((status, "adminNote" in body["fields"]), (400, True))

    def test_replay(self):
        first = self.add("same")
        status, body = self.call("POST", f"events/{self.eid}/registrations", {"tickets": {self.adult: 1}, "answers": {**ANSWERS}, "idempotencyKey": "same", "paymentStatus": "pending"})
        self.assertEqual((status, body["registration"]["registrationId"]), (200, first["registrationId"]))


class ActionTests(Base):
    def test_detail_edit_and_conflict(self):
        reg = self.add("a")
        rid = reg["registrationId"]
        status, body = self.call("GET", f"events/{self.eid}/registrations/{rid}")
        self.assertEqual((status, body["qr"].startswith("NKEV1.NK-"), body["form"]["version"]), (200, True, 1))
        self.assertNotIn("checkInToken", json.dumps(body["registration"]))
        status, body = self.call("PUT", f"events/{self.eid}/registrations/{rid}", {"revision": 1, "adminNote": "VIP", "answers": {**reg["answers"], "fullName": "Ann L."}})
        self.assertEqual((status, body["registration"]["fullName"], body["registration"]["revision"]), (200, "Ann L.", 2))
        self.assertEqual(self.call("PUT", f"events/{self.eid}/registrations/{rid}", {"revision": 1, "adminNote": "x"})[0], 409)
        history = self.call("GET", f"events/{self.eid}/registrations/{rid}")[1]["history"]
        self.assertEqual([h["action"] for h in history if h["action"] == "update"], ["update"])

    def test_mark_paid_cancel_refund_flow_with_history(self):
        reg = self.add("a", paymentMethod="bank")
        rid = reg["registrationId"]
        base = f"events/{self.eid}/registrations/{rid}"
        status, body = self.call("POST", f"{base}/mark-paid", {"via": "bank", "reference": "deposit 10/1"})
        self.assertEqual((status, body["registration"]["status"], body["registration"]["paymentStatus"], body["registration"]["markedPaidBy"]), (200, "confirmed", "paid", "admin@example.com"))
        self.assertEqual(self.call("POST", f"{base}/mark-paid", {"via": "bitcoin"})[0], 400)
        status, body = self.call("POST", f"{base}/cancel", {"reason": "asked to cancel", "notify": False})
        self.assertEqual((body["registration"]["status"], body["registration"]["paymentStatus"]), ("cancelled", "refund_pending"))
        self.assertNotIn("cancelled", [m[0] for m in self.mails])                       # notify:false is respected
        self.assertEqual(self.call("POST", f"{base}/cancel", {})[0], 409)
        status, body = self.call("POST", f"{base}/refund", {"mode": "manual", "note": "sent"})
        self.assertEqual((status, body["registration"]["paymentStatus"]), (200, "refunded"))
        self.assertEqual(self.call("POST", f"{base}/refund", {"mode": "manual"})[0], 409)
        actions = [h["action"] for h in self.call("GET", f"events/{self.eid}/registrations/{rid}")[1]["history"]]
        self.assertEqual(sorted(actions), sorted(["create", "mark-paid", "cancel", "refund"]))
        self.assertEqual(self.call("POST", f"{base}/teleport", {})[0], 404)

    def test_cancel_notifies_by_default_and_frees_seats(self):
        reg = self.add("a", tickets={self.adult: 2})
        self.call("POST", f"events/{self.eid}/registrations/{reg['registrationId']}/cancel", {})
        self.assertIn(("cancelled", reg["registrationId"]), self.mails)
        self.assertEqual(self.regs.counters["EVENT#" + self.eid], 0)

    def test_promote_from_waiting_list(self):
        self.admin_store.events[self.eid]["capacity"] = 1
        first = self.add("a")
        wait = self.add("b", "b@example.com", waitlist=True)
        self.assertEqual(wait["status"], "waitlisted")
        base = f"events/{self.eid}/registrations/{wait['registrationId']}"
        status, body = self.call("POST", f"{base}/promote", {})
        self.assertEqual((status, body["code"]), (409, "sold_out"))
        self.call("POST", f"events/{self.eid}/registrations/{first['registrationId']}/cancel", {})
        status, body = self.call("POST", f"{base}/promote", {})
        self.assertEqual((status, body["registration"]["status"]), (200, "pending_payment"))
        self.assertIn(("promoted", wait["registrationId"]), self.mails)

    def test_square_refund_through_the_api(self):
        reg = self.add("a", paymentStatus="paid", paidVia="square")
        stored = self.regs.regs[reg["registrationId"]]
        stored["paymentReference"] = "pay-5"
        base = f"events/{self.eid}/registrations/{reg['registrationId']}"
        self.call("POST", f"{base}/cancel", {})
        status, body = self.call("POST", f"{base}/refund", {"mode": "square"})
        self.assertEqual((status, body["registration"]["refundReference"]), (200, "rf-1"))
        self.assertEqual(self.refunds[0]["payment_id"], "pay-5")


class CheckInApiTests(Base):
    def setUp(self):
        super().setUp()
        self.reg = self.add("a", paymentStatus="paid", paidVia="cash")
        self.stored = self.regs.regs[self.reg["registrationId"]]
        self.code = f"NKEV1.{self.reg['registrationId']}.{self.stored['checkInToken']}"

    def scan(self, code, **kw):
        status, body = self.call("POST", f"events/{self.eid}/check-in", {"code": code, **kw})
        self.assertEqual(status, 200)
        return body

    def test_scan_flow(self):
        self.assertEqual(self.scan(self.code)["result"], "ok")
        again = self.scan(self.code)
        self.assertEqual((again["result"], again["registration"]["fullName"]), ("already", "Ann Lee"))
        self.assertEqual(self.scan(self.code, undo=True)["result"], "undone")
        self.assertEqual(self.scan(self.code)["result"], "ok")
        self.assertEqual(sum(1 for a in self.admin_store.audit if a["action"] == "check-in"), 3)

    def test_bad_codes(self):
        for code in ("", "garbage", f"NKEV1.{self.reg['registrationId']}.wrongtoken", "NKEV1.NK-NONE-NONE.x", None):
            self.assertEqual(self.scan(code)["result"], "not_found", code)
        other = self.call("POST", "events", {**BASE, "slug": "other", "title": "Other"})[1]["event"]["eventId"]
        status, body = self.call("POST", f"events/{other}/check-in", {"code": self.code})
        self.assertEqual(body["result"], "wrong_event")

    def test_typed_id_and_pending_payment(self):
        self.assertEqual(self.scan(self.reg["registrationId"].lower())["result"], "ok")
        pending = self.add("p", "p@example.com")
        self.assertEqual(self.scan(pending["registrationId"])["result"], "payment_due")
        status, body = self.call("POST", f"events/{self.eid}/registrations/{pending['registrationId']}/check-in", {})
        self.assertEqual(body["result"], "payment_due")
        self.call("POST", f"events/{self.eid}/registrations/{pending['registrationId']}/mark-paid", {"via": "cash"})
        self.assertEqual(self.call("POST", f"events/{self.eid}/registrations/{pending['registrationId']}/check-in", {})[1]["result"], "ok")

    def test_dashboard_counts_checked_in_seats(self):
        self.scan(self.code)
        self.assertEqual(self.call("GET", f"events/{self.eid}/dashboard")[1]["dashboard"]["checkedInSeats"], 1)


class ExportTests(Base):
    def setUp(self):
        super().setUp()
        self.add("a", "a@example.com", answers={**ANSWERS, "email": "a@example.com", "fullName": "=HYPERLINK(\"http://evil\")"})
        self.add("b", "b@example.com", answers={**ANSWERS, "email": "b@example.com", "fullName": "Bo 山田"}, paymentStatus="paid", paidVia="cash")

    def export(self, **query):
        status, body = self.call("GET", f"events/{self.eid}/export", query=query)
        self.assertEqual(status, 200, body)
        return body, base64.b64decode(body["base64"])

    def test_csv_is_filtered_safe_and_audited(self):
        body, data = self.export(format="csv", status="confirmed")
        rows = list(csv.reader(io.StringIO(data.decode("utf-8-sig"))))
        self.assertEqual((body["rows"], len(rows)), (1, 2))
        self.assertIn("Bo 山田", rows[1])
        self.assertTrue(body["filename"].endswith(".csv"))
        full = list(csv.reader(io.StringIO(self.export(format="csv")[1].decode("utf-8-sig"))))
        self.assertEqual(len(full), 3)
        self.assertTrue(any(c.startswith("'=HYPERLINK") for r in full for c in r))        # formula neutralised
        self.assertIn("Adult (qty)", full[0])
        self.assertTrue(any(a["action"] == "export" for a in self.admin_store.audit))

    def test_xlsx_opens(self):
        body, data = self.export(format="xlsx", sort="fullName")
        try:
            import openpyxl
        except ImportError:
            self.skipTest("openpyxl is not installed")
        ws = openpyxl.load_workbook(io.BytesIO(data)).active
        self.assertEqual(ws.max_row, 3)
        self.assertEqual(body["contentType"], "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")

    def test_bad_format(self):
        self.assertEqual(self.call("GET", f"events/{self.eid}/export", query={"format": "pdf"})[0], 400)


class BulkEmailTests(Base):
    def setUp(self):
        super().setUp()
        for i in range(5):
            self.add(f"k{i}", f"p{i}@example.com", answers={**ANSWERS, "email": f"p{i}@example.com", "fullName": f"Person {i}"})
        self.add("dup", "p0@example.com")                                                  # a second registration, same address

    def send(self, **over):
        body = {"audience": "active", "subject": "Hello {name}", "body": "See you at {event_title}", **over}
        status, out = self.call("POST", f"events/{self.eid}/email", body)
        return status, out

    def test_personalised_deduplicated_send_and_audit(self):
        status, out = self.send()
        self.assertEqual((status, out["sent"], out["total"], out["nextOffset"], out["failed"]), (200, 5, 5, None, []))
        self.assertEqual(sorted(t for t, _, _ in self.bulk), [f"p{i}@example.com" for i in range(5)])
        mail = next(m for m in self.bulk if m[0] == "p3@example.com")
        self.assertEqual((mail[1], mail[2]), ("Hello Person 3", "See you at " + self.event["title"]))
        self.assertTrue(any(a["action"] == "email" and "5 sent" in a["summary"] for a in self.admin_store.audit))

    def test_batches(self):
        with mock.patch.object(handler, "BULK_BATCH", 2):
            first = self.send()[1]
            self.assertEqual((first["sent"], first["nextOffset"], first["total"]), (2, 2, 5))
            second = self.send(offset=2)[1]
            third = self.send(offset=4)[1]
        self.assertEqual((second["nextOffset"], third["nextOffset"], len(self.bulk)), (4, None, 5))
        self.assertEqual(len({t for t, _, _ in self.bulk}), 5)

    def test_failures_are_reported_not_fatal(self):
        def flaky(to, subject, text):
            if to == "p1@example.com":
                raise RuntimeError("bounce")
            self.bulk.append((to, subject, text))
        with mock.patch.object(handler.event_mailer, "send_message", flaky):
            out = self.send()[1]
        self.assertEqual((out["sent"], out["failed"]), (4, ["p1@example.com"]))

    def test_test_mode_sends_only_to_the_admin(self):
        status, out = self.send(test=True)
        self.assertEqual((status, out["test"], [t for t, _, _ in self.bulk]), (200, True, ["admin@example.com"]))
        self.assertEqual(self.call("POST", f"events/{self.eid}/email", {"test": True, "subject": "s", "body": "b"}, email="not-an-address")[0], 400)

    def test_template_validation_and_audience_errors(self):
        self.assertEqual(self.send(subject="", body="x")[0], 400)
        self.assertEqual(self.send(body="Hi {bogus}")[0], 400)
        self.assertEqual(self.send(audience="everyone")[0], 400)
        self.assertEqual(self.send(audience="checked_in")[0], 400)                         # nobody checked in yet
        self.assertEqual(self.send(offset=-1)[0], 400)
        self.assertEqual(self.send(useTemplate="reminder")[0], 400)                        # reminder not written yet
        self.call("PUT", f"events/{self.eid}", {"revision": self.admin_store.events[self.eid]["revision"],
                  "emails": {"reminder": {"subject": "Reminder: {event_title}", "body": "Tomorrow at {venue}"}}})
        status, out = self.send(useTemplate="reminder", subject="", body="")
        self.assertEqual((status, out["sent"]), (200, 5))
        self.assertTrue(self.bulk[-1][1].startswith("Reminder: "))
        self.assertEqual(self.bulk[-1][0], "p4@example.com")
        self.assertEqual(len(self.bulk), 5)


class ErasureAndFileTests(Base):
    def setUp(self):
        super().setUp()
        self.deleted = []
        fake_s3 = mock.Mock()
        fake_s3.generate_presigned_url.side_effect = lambda op, **kw: "https://signed.example/" + kw["Params"]["Key"]
        fake_s3.delete_object.side_effect = lambda **kw: self.deleted.append(kw["Key"])
        for p in (mock.patch.object(handler, "s3", lambda: fake_s3), mock.patch.object(handler, "ASSETS_BUCKET", "bucket")):
            p.start()
            self.addCleanup(p.stop)

    def _with_file(self):
        reg = self.add(key="f1")
        stored = self.regs.regs[reg["registrationId"]]
        stored["answers"]["cv"] = {"fileId": "abc123", "name": "cv.pdf", "contentType": "application/pdf"}
        return reg["registrationId"]

    def test_file_link_is_limited_to_files_in_the_registration(self):
        rid = self._with_file()
        status, body = self.call("GET", f"events/{self.eid}/registrations/{rid}/files/abc123", groups="EventManagers")
        self.assertEqual(status, 200)
        self.assertTrue(body["url"].endswith(f"event-uploads/{self.eid}/abc123"))
        self.assertEqual(self.call("GET", f"events/{self.eid}/registrations/{rid}/files/other")[0], 404)
        self.assertEqual(self.call("GET", f"events/{self.eid}/registrations/{rid}/files/abc123", groups="Other")[0], 403)

    def test_only_super_admin_can_erase(self):
        rid = self._with_file()
        self.call("POST", f"events/{self.eid}/registrations/{rid}/cancel", {"reason": "x"})
        self.assertEqual(self.call("POST", f"events/{self.eid}/registrations/{rid}/anonymize", {}, groups="EventManagers")[0], 403)
        status, body = self.call("POST", f"events/{self.eid}/registrations/{rid}/anonymize", {})
        self.assertEqual(status, 200, body)
        stored = self.regs.regs[rid]
        self.assertEqual(stored["email"], "erased")
        self.assertNotIn("cv", stored["answers"])
        self.assertTrue(any(k.endswith("abc123") for k in self.deleted))

    def test_active_registration_cannot_be_erased(self):
        rid = self._with_file()
        before_event = event_model.parse_iso("2026-10-01T00:00:00+09:00")
        with mock.patch.object(handler, "now_jst", lambda: before_event):
            status, _ = self.call("POST", f"events/{self.eid}/registrations/{rid}/anonymize", {})
        self.assertEqual(status, 409)


if __name__ == "__main__":
    unittest.main()
