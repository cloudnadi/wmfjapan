"""Phase 6 hardening: input validation, sanitising, abuse limits, payment checks, uploads, personal data erasure."""

import json
import sys
import unittest
import unittest.mock
from datetime import timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for sub in ("lambdas", "lambdas/manage_event_platform", "tests"):
    sys.path.insert(0, str(ROOT / sub))

import event_model as m  # noqa: E402
import event_registration_admin as adm  # noqa: E402
import event_registrations as logic  # noqa: E402
import form_engine as fe  # noqa: E402
from event_fakes import ACCOUNT, ADULT, ANSWERS, FREE, NOW, FakePayments, FakeStore  # noqa: E402
import test_event_platform_public as P  # noqa: E402  (loads the public / webhook / expiry handlers)

METHODS = {"square": {}, "bank": {}, "paypay": {}}


def schema(*extra):
    return {"fields": m.ensure_core_fields(list(extra))}


# ── validation of free text ───────────────────────────────────────────────────

class InputValidationTests(unittest.TestCase):
    def email_errors(self, value):
        return fe.validate_submission(schema(), {**ANSWERS, "email": value})[1]

    def test_ordinary_addresses_pass(self):
        for good in ("ann@example.com", "a.b+tag@sub.example.co.jp", "o'neil@example.org", "x_y-z@ex-ample.com", "ANN@Example.COM"):
            self.assertEqual(self.email_errors(good), {}, good)

    def test_addresses_that_could_inject_headers_or_recipients_are_rejected(self):
        bad = ["a@b.co,evil@x.com", "a@b.co;evil@x.com", "Ann <a@b.co>", '"a b"@x.com', "a b@x.com", "a@b.co\nBcc: x@y.z",
               "a@b.co\r\n", "a@@b.co", "a@b", "a@-b.com", "a@b..com", "@b.co", "a@.co", "(c)a@b.co", "a@b.co>", "a\\b@x.com",
               "ann@example.com " + "x" * 300, "é@example.com"]
        for value in bad:
            self.assertIn("email", self.email_errors(value), repr(value))

    def test_email_is_lowercased_for_storage(self):
        clean, _ = fe.validate_submission(schema(), {**ANSWERS, "email": "ANN@Example.COM"})
        self.assertEqual(clean["email"], "ann@example.com")

    def test_control_characters_and_line_breaks(self):
        s = schema({"key": "note", "type": "text", "label": "Note"}, {"key": "long", "type": "textarea", "label": "Long"})
        for field, value, ok in (("note", "one line", True), ("note", "two\nlines", False), ("note", "cr\rhere", False),
                                 ("note", "nul\x00byte", False), ("note", "bell\x07", False), ("note", "tab\tok", True),
                                 ("long", "two\nlines", True), ("long", "nul\x00byte", False), ("long", "esc\x1b[31m", False),
                                 ("fullName", "Ann\nLee", False), ("phone", "+81 90\n1234 5678", False)):
            errors = fe.validate_submission(s, {**ANSWERS, field: value})[1]
            self.assertEqual(field not in errors, ok, (field, value))

    def test_unicode_names_are_fine(self):
        clean, errors = fe.validate_submission(schema(), {**ANSWERS, "fullName": "山田 太郎 / മലയാളം"})
        self.assertEqual((errors, clean["fullName"]), ({}, "山田 太郎 / മലയാളം"))


# ── rich text sanitiser ───────────────────────────────────────────────────────

class SanitiserTests(unittest.TestCase):
    ATTACKS = [
        '<script>alert(1)</script>', '<img src=x onerror=alert(1)>', '<svg onload=alert(1)>', '<iframe src="javascript:alert(1)"></iframe>',
        '<a href="javascript:alert(1)">x</a>', '<a href="JaVaScRiPt:alert(1)">x</a>', '<a href=" javascript:alert(1)">x</a>',
        '<a href="java&#115;cript:alert(1)">x</a>', '<a href="&#106;avascript:alert(1)">x</a>', '<a href="data:text/html,<script>alert(1)</script>">x</a>',
        '<a href="vbscript:msgbox(1)">x</a>', '<a href="//evil.example/x">x</a>', '<p onclick="alert(1)">x</p>', '<p style="background:url(javascript:alert(1))">x</p>',
        '<math><mi><style><img src=x onerror=alert(1)></style></mi></math>', '<noscript><p title="</noscript><img src=x onerror=alert(1)>">',
        '<object data="x"></object><embed src="x">', '<form action="x"><input name=a></form>', '<base href="http://evil/">',
        '<meta http-equiv="refresh" content="0;url=http://evil">', '<link rel=stylesheet href="http://evil/x.css">',
        '<div><script>alert(1)</script></div>', '<<script>script>alert(1)<</script>/script>', '<a href="http://ok" onmouseover="alert(1)">x</a>',
        '<b onmouseover=alert(1)>x</b>', '<!--<script>alert(1)</script>-->', '<![CDATA[<script>alert(1)</script>]]>', '&lt;script&gt;alert(1)&lt;/script&gt;',
        '<textarea></textarea><script>alert(1)</script>', '<template><script>alert(1)</script></template>',
    ]

    def test_no_attack_survives(self):
        for attack in self.ATTACKS:
            out = m.sanitize_html(attack)
            lowered = out.lower()
            for needle in ("<script", "onerror", "onload", "onclick", "onmouseover", "javascript:", "vbscript:", "data:", "<iframe", "<img",
                           "<svg", "<style", "<object", "<embed", "<form", "<input", "<meta", "<link", "<base", "style=", "//evil"):
                self.assertNotIn(needle, lowered, (attack, out))
            self.assertNotRegex(out, r"<[^>]*\s(on\w+|style|src)\s*=", (attack, out))

    def test_every_emitted_tag_is_allow_listed_and_has_no_stray_attributes(self):
        import re
        for attack in self.ATTACKS + ['<p>fine <strong>bold</strong> <em>x</em> <a href="https://ok.example/p?q=1&r=2">link</a></p>']:
            for tag in re.findall(r"<(/?)([a-z0-9]+)([^>]*)>", m.sanitize_html(attack)):
                _, name, rest = tag
                self.assertIn(name, {"p", "br", "strong", "b", "em", "i", "u", "ul", "ol", "li", "h2", "h3", "h4", "blockquote", "a"})
                if name != "a":
                    self.assertEqual(rest.strip(), "")
                else:
                    self.assertRegex(rest, r'^( href="(https?://|mailto:)[^"<>]*"| rel="noopener noreferrer"| target="_blank")*$')

    def test_legitimate_content_is_kept(self):
        html = '<h2>Title</h2><p>Text with <strong>bold</strong>, <em>italic</em> and <a href="https://example.com/a?b=1&c=2">a link</a>.</p><ul><li>One</li><li>Two</li></ul><p>Line<br>break</p>'
        out = m.sanitize_html(html)
        for part in ("<h2>Title</h2>", "<strong>bold</strong>", "<em>italic</em>", "<li>One</li>", "<br>", 'href="https://example.com/a?b=1&amp;c=2"'):
            self.assertIn(part, out)
        self.assertIn('rel="noopener noreferrer"', out)

    def test_unbalanced_markup_is_closed(self):
        out = m.sanitize_html("<p>open <strong>bold <em>both")
        self.assertEqual(out.count("<strong>"), out.count("</strong>"))
        self.assertEqual(out.count("<em>"), out.count("</em>"))
        self.assertEqual(m.sanitize_html("</p></strong>stray"), "stray")

    def test_text_is_escaped_not_dropped(self):
        self.assertEqual(m.sanitize_html("1 < 2 & 3 > 2"), "1 &lt; 2 &amp; 3 &gt; 2")
        self.assertEqual(m.sanitize_html(None), "")
        self.assertEqual(m.sanitize_html(12345), "12345")

    def test_huge_nesting_does_not_hang(self):
        import time
        start = time.time()
        m.sanitize_html("<div>" * 5000 + "x" + "</div>" * 5000)
        m.sanitize_html("<p>" * 3000 + "x")
        self.assertLess(time.time() - start, 2)


# ── abuse limits ──────────────────────────────────────────────────────────────

class Base(unittest.TestCase):
    def setUp(self):
        self.store = FakeStore()
        self.pay = FakePayments()
        self.sent = []
        logic.store, logic.payments = self.store, self.pay
        logic.notify = lambda kind, reg, event: self.sent.append((kind, reg["registrationId"]))
        self.event = self.store.add_event(capacity=50)
        self.store.add_tickets("e1", ADULT, FREE)
        self.form = self.store.latest_form("f1")

    def reg(self, key, email="ann@example.com", tickets=None, method="bank", event=None, now=NOW):
        return logic.register(event=event or self.store.get_event("e1"), tickets=self.store.get_tickets("e1"), form=self.form,
                              body={"tickets": tickets or {"adult": 1}, "answers": {**ANSWERS, "email": email}, "paymentMethod": method,
                                    "idempotencyKey": key}, account=None, methods=METHODS, now=now)[0]


class SeatHoardingTests(Base):
    def test_unpaid_holds_per_address_are_capped(self):
        regs = [self.reg(f"k{i}") for i in range(logic.MAX_UNPAID_PER_EMAIL)]
        with self.assertRaises(logic.RegError) as ctx:
            self.reg("one-too-many")
        self.assertEqual((ctx.exception.status, ctx.exception.code), (429, "too_many_unpaid"))
        self.assertEqual(self.store.counters["EVENT#e1"], logic.MAX_UNPAID_PER_EMAIL)

    def test_paying_or_cancelling_frees_a_slot_and_other_people_are_unaffected(self):
        regs = [self.reg(f"k{i}") for i in range(3)]
        self.reg("other", email="other@example.com")
        logic.cancel(regs[0], by="x", reason="y", now=NOW, event=self.event)
        self.reg("again")                                                       # a slot opened up
        logic.mark_paid(regs[1], via="bank", reference="r", now=NOW, event=self.event)
        self.reg("again2")                                                      # paid ones no longer count as unpaid
        with self.assertRaises(logic.RegError):
            self.reg("again3")

    def test_free_registrations_are_not_limited_and_other_events_do_not_count(self):
        for i in range(6):
            self.reg(f"f{i}", tickets={"free": 1}, method=None)
        self.store.add_event(eventId="e2", slug="two", formId="f1")
        other = self.store.get_event("e2")
        self.store.tickets["e2"] = [{**ADULT, "eventId": "e2"}]
        for i in range(3):
            self.reg(f"x{i}", email="busy@example.com")
        reg = logic.register(event=other, tickets=self.store.get_tickets("e2"), form=self.form,
                             body={"tickets": {"adult": 1}, "answers": {**ANSWERS, "email": "busy@example.com"}, "paymentMethod": "bank"},
                             account=None, methods=METHODS, now=NOW)[0]
        self.assertEqual(reg["status"], "pending_payment")

    def test_the_admin_can_still_add_for_such_an_address(self):
        for i in range(3):
            self.reg(f"k{i}")
        reg, _ = adm.manual_add(event=self.event, tickets=self.store.get_tickets("e1"), form=self.form, now=NOW, actor="a", account=None,
                                body={"tickets": {"adult": 1}, "answers": {**ANSWERS}, "idempotencyKey": "x"})
        self.assertEqual(reg["status"], "pending_payment")


# ── payments ──────────────────────────────────────────────────────────────────

class PaymentAmountTests(unittest.TestCase):
    def test_amount_rule(self):
        reg = {"totalJpy": 3000}
        for paid, ok in ((3000, True), ("3000", True), (None, True), (2999, False), (3001, False), (0, False), ("abc", False), (3000.5, False)):
            self.assertEqual(logic.payment_amount_ok(reg, paid), ok, paid)


class WebhookAmountTests(P.Base):
    def setUp(self):
        super().setUp()
        self.reg = self.register({"paymentMethod": "square"})[1]["registration"]

    def hook(self, amount):
        payment = {"id": "pay-1", "order_id": "order-1", "status": "COMPLETED"}
        if amount is not None:
            payment["amount_money"] = {"amount": amount, "currency": "JPY"}
        body = json.dumps({"type": "payment.updated", "data": {"object": {"payment": payment}}})
        with unittest.mock.patch.object(P.webhook.square, "verify_webhook_signature", lambda *a: True):
            return P.webhook.lambda_handler({"pathParameters": {"provider": "square"}, "headers": {"x-square-hmacsha256-signature": "sig"}, "body": body}, None)

    def test_correct_amount_confirms(self):
        self.assertEqual(self.hook(3000)["statusCode"], 200)
        self.assertEqual(self.store.regs[self.reg["registrationId"]]["status"], "confirmed")

    def test_wrong_amount_is_flagged_and_not_confirmed(self):
        self.assertEqual(self.hook(100)["statusCode"], 200)               # still 200 so Square does not retry forever
        stored = self.store.regs[self.reg["registrationId"]]
        self.assertEqual((stored["status"], stored["paymentStatus"]), ("pending_payment", "pending"))
        self.assertEqual((stored["paymentMismatch"]["expectedJpy"], stored["paymentMismatch"]["reportedJpy"]), (3000, 100))
        self.assertNotIn("confirmed", [k for k, _ in self.emails])

    def test_missing_amount_does_not_block(self):
        self.hook(None)
        self.assertEqual(self.store.regs[self.reg["registrationId"]]["status"], "confirmed")

    def test_card_return_check_also_compares_the_order_total(self):
        self.order_status = "COMPLETED"
        self.pay.get_payment_status = lambda order: {"status": "COMPLETED", "paymentId": "p", "raw": {"order": {"total_money": {"amount": 1}}}}
        q = {"token": self.reg["checkInToken"]}
        _, body, _ = self.call("GET", f"registrations/{self.reg['registrationId']}", query=q)
        self.assertEqual(body["registration"]["status"], "pending_payment")
        self.assertIn("paymentMismatch", body["registration"])
        self.pay.get_payment_status = lambda order: {"status": "COMPLETED", "paymentId": "p", "raw": {"order": {"total_money": {"amount": 3000}}}}
        _, body, _ = self.call("GET", f"registrations/{self.reg['registrationId']}", query=q)
        self.assertEqual(body["registration"]["status"], "confirmed")


class LookupThrottleTests(P.Base):
    def test_token_lookups_are_rate_limited_per_ip(self):
        reg = self.register()[1]["registration"]
        codes = [self.call("GET", f"registrations/{reg['registrationId']}", query={"token": "wrong"}, ip="6.6.6.6")[0] for _ in range(62)]
        self.assertEqual((codes.count(404), codes[-1]), (60, 429))
        self.assertEqual(self.call("GET", f"registrations/{reg['registrationId']}", query={"token": reg["checkInToken"]}, ip="7.7.7.7")[0], 200)


# ── uploads ───────────────────────────────────────────────────────────────────

class UploadLifecycleTests(P.Base):
    PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 50

    def setUp(self):
        super().setUp()
        fields = m.default_form_fields() + [{"key": "proof", "type": "file", "label": "Proof", "validation": {"maxSizeMb": 1, "allowedTypes": ["image/png"]}}]
        self.store.forms[("f1", 2)] = m.new_form_version("f1", self.store.forms[("f1", 1)], fields, event_id="e1")
        from botocore.exceptions import ClientError
        self.missing = ClientError({"Error": {"Code": "404"}}, "HeadObject")
        self.objects = {}

        def head(Bucket, Key):
            if Key not in self.objects:
                raise self.missing
            return self.objects[Key]

        def copy(Bucket, Key, CopySource, **kw):
            if CopySource["Key"] not in self.objects:
                raise self.missing
            self.objects[Key] = self.objects[CopySource["Key"]]

        self.s3.head_object.side_effect = head
        self.s3.copy_object.side_effect = copy
        self.s3.put_object.side_effect = lambda **kw: self.objects.__setitem__(kw["Key"], {"ContentLength": len(kw["Body"]), "ContentType": kw["ContentType"], "Metadata": kw["Metadata"]})

    def upload(self):
        import base64
        return self.call("POST", "events/picnic/upload", {"fieldKey": "proof", "name": "p.png", "contentType": "image/png", "data": base64.b64encode(self.PNG).decode()})[1]

    def test_uploads_start_pending_and_move_when_attached(self):
        up = self.upload()
        pending = f"event-uploads-pending/e1/{up['fileId']}"
        final = f"event-uploads/e1/{up['fileId']}"
        self.assertIn(pending, self.objects)
        self.assertNotIn(final, self.objects)
        status, body, _ = self.register({"answers": {**P.ANSWERS, "proof": {"fileId": up["fileId"]}}, "tickets": {"free": 1}, "paymentMethod": None})
        self.assertEqual(status, 201, body)
        self.assertIn(final, self.objects)
        self.assertEqual(body["registration"]["answers"]["proof"]["name"], "p.png")
        self.assertEqual(self.s3.copy_object.call_args.kwargs["ServerSideEncryption"], "AES256")

    def test_unknown_or_other_events_files_are_refused(self):
        status, body, _ = self.register({"answers": {**P.ANSWERS, "proof": {"fileId": "deadbeef"}}, "tickets": {"free": 1}, "paymentMethod": None})
        self.assertEqual((status, "proof" in body["fields"]), (400, True))
        self.objects["event-uploads-pending/OTHER/abc"] = {"ContentLength": 1, "ContentType": "image/png", "Metadata": {"name": "x"}}
        status, body, _ = self.register({"idempotencyKey": "k2", "answers": {**P.ANSWERS, "proof": {"fileId": "abc"}}, "tickets": {"free": 1}, "paymentMethod": None})
        self.assertEqual(status, 400)                                          # keys are scoped per event

    def test_a_file_can_be_reused_after_it_was_attached(self):
        up = self.upload()
        answers = {**P.ANSWERS, "proof": {"fileId": up["fileId"]}}
        self.assertEqual(self.register({"answers": answers, "tickets": {"free": 1}, "paymentMethod": None})[0], 201)
        del self.objects[f"event-uploads-pending/e1/{up['fileId']}"]          # the 2-day lifecycle removed the pending copy
        status, _, _ = self.register({"idempotencyKey": "k2", "answers": {**answers, "email": "b@example.com"}, "tickets": {"free": 1}, "paymentMethod": None})
        self.assertEqual(status, 201)


# ── personal data ─────────────────────────────────────────────────────────────

class AnonymiseTests(Base):
    def setUp(self):
        super().setUp()
        self.deleted = []
        self.delete = lambda event_id, file_id: self.deleted.append((event_id, file_id))
        reg = self.reg("a", email="ann@example.com")
        reg = logic.mark_paid(reg, via="bank", reference="r", now=NOW, event=self.event)
        self.cancelled = logic.cancel(reg, by="x", reason="private reason", now=NOW, event=self.event)
        self.cancelled = logic.store.update_registration(reg["registrationId"], {"paymentStatus": "refunded", "adminNote": "called her at home"})

    def test_preconditions(self):
        live = self.reg("live", email="live@example.com")
        with self.assertRaises(logic.RegError) as ctx:
            adm.anonymize(live, "root", NOW, event_over=False, delete_file=self.delete)
        self.assertEqual(ctx.exception.status, 409)
        out = adm.anonymize(live, "root", NOW, event_over=True, delete_file=self.delete)           # fine once the event is over
        self.assertEqual(out["fullName"], "Erased")
        pending_refund = {**self.cancelled, "paymentStatus": "refund_pending"}
        with self.assertRaises(logic.RegError):
            adm.anonymize(pending_refund, "root", NOW, event_over=True, delete_file=self.delete)    # refund details still needed

    def test_personal_data_is_gone_accounting_data_stays(self):
        self.cancelled["answers"]["proof"] = {"fileId": "f1", "name": "id-card.png"}
        self.cancelled["answers"]["guests"] = [{"photo": {"fileId": "f2", "name": "x"}}]
        old_token = self.cancelled["checkInToken"]
        out = adm.anonymize(self.cancelled, "root@x", NOW, event_over=True, delete_file=self.delete)
        for key in ("fullName", "email", "phone"):
            self.assertIn(out[key], ("Erased", "erased", ""))
        self.assertEqual(out["answers"], {})
        for gone in ("accountEmail", "adminNote", "cancelReason"):
            self.assertNotIn(gone, out)
        self.assertNotEqual(out["checkInToken"], old_token)                       # the old confirmation link stops working
        self.assertEqual((out["totalJpy"], out["tickets"], out["status"], out["paymentStatus"], out["anonymizedBy"]), (1500, self.cancelled["tickets"], "cancelled", "refunded", "root@x"))
        self.assertEqual(sorted(self.deleted), [("e1", "f1"), ("e1", "f2")])
        self.assertNotIn("ann@example.com", json.dumps(out))
        self.assertNotIn("Ann Lee", json.dumps(out))
        with self.assertRaises(logic.RegError):
            adm.anonymize(out, "root", NOW, event_over=True, delete_file=self.delete)           # already done

    def test_member_links_and_exports_no_longer_show_the_person(self):
        member = logic.register(event=self.event, tickets=self.store.get_tickets("e1"), form=self.form, account=ACCOUNT, methods=METHODS, now=NOW,
                                body={"tickets": {"free": 1}, "answers": ANSWERS, "idempotencyKey": "m"})[0]
        member = logic.cancel(member, by="x", reason="y", now=NOW, event=self.event)
        out = adm.anonymize(member, "root", NOW, event_over=True, delete_file=self.delete)
        self.assertEqual(self.store.list_for_account("m@example.com"), [])
        import event_exports as x
        table = x.export_table([out], x.answer_columns([self.form], [out]), self.store.get_tickets("e1"))
        self.assertNotIn("Ann Lee", json.dumps(table))
        self.assertNotIn("m@example.com", json.dumps(table))

    def test_stale_copy_conflicts(self):
        adm.anonymize(self.cancelled, "root", NOW, event_over=True, delete_file=self.delete)
        with self.assertRaises(logic.RegError):
            adm.anonymize(self.cancelled, "root", NOW, event_over=True, delete_file=self.delete)


if __name__ == "__main__":
    unittest.main()
