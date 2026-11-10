"""Admin registration management: table queries, dashboard, manual add, edit, refunds, check-in, bulk e-mail."""

import sys
import unittest
from datetime import timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lambdas"))
sys.path.insert(0, str(ROOT / "tests"))

import event_model  # noqa: E402
import event_registration_admin as adm  # noqa: E402
import event_registrations as logic  # noqa: E402
from event_fakes import (ACCOUNT, ADULT, ANSWERS, FREE, NOW, FakePayments, FakeStore)  # noqa: E402

METHODS = {"square": {}, "bank": {}, "paypay": {}}


class Base(unittest.TestCase):
    def setUp(self):
        self.store = FakeStore()
        self.pay = FakePayments()
        self.pay.create_refund = lambda **kw: self.refunds.append(kw) or {"refundId": "rf-1", "status": "PENDING"}
        self.refunds, self.sent = [], []
        logic.store, logic.payments = self.store, self.pay
        logic.notify = lambda kind, reg, event: self.sent.append((kind, reg["registrationId"]))
        self.event = self.store.add_event()
        self.store.add_tickets("e1", ADULT, FREE)
        extra = [{"key": "diet", "type": "text", "label": "Diet"}]
        fields = event_model.default_form_fields()
        fields = fields[:-2] + extra + fields[-2:]
        self.store.forms[("f1", 1)] = event_model.new_form_version("f1", None, fields, event_id="e1")
        self.form = self.store.latest_form("f1")

    def public(self, key="k", tickets=None, account=None, method="bank", **answers):
        body = {"tickets": tickets or {"adult": 1}, "answers": {**ANSWERS, **answers}, "paymentMethod": method, "idempotencyKey": key}
        reg, _ = logic.register(event=self.store.get_event("e1"), tickets=self.store.get_tickets("e1"), form=self.form,
                                body=body, account=account, methods=METHODS, now=NOW)
        return reg

    def add(self, body=None, account=None, event=None):
        payload = {"tickets": {"adult": 2}, "answers": ANSWERS, "idempotencyKey": "m1", **(body or {})}
        return adm.manual_add(event=event or self.store.get_event("e1"), tickets=self.store.get_tickets("e1"), form=self.form,
                              body=payload, account=account, now=NOW, actor="admin@example.com")


class QueryTests(Base):
    def setUp(self):
        super().setUp()
        self.regs = [
            self.public("a", fullName="Ann Lee", email="ann@example.com", diet="vegan"),
            self.public("b", tickets={"free": 2}, method=None, fullName="Bo Tan", email="bo@example.com", diet="none"),
            self.public("c", tickets={"adult": 3}, fullName="Cy Ng", email="cy@example.com", diet="Vegan"),
        ]
        self.regs[1]["checkedInAt"] = "2026-10-10T18:00:00+09:00"
        self.regs[2]["status"], self.regs[2]["paymentStatus"] = "cancelled", "refund_pending"
        self.names = lambda p: [r["fullName"] for r in adm.query_registrations(self.regs, p)["rows"]]

    def test_search_across_name_email_id_and_any_answer(self):
        self.assertEqual(self.names({"q": "bo@example"}), ["Bo Tan"])
        self.assertEqual(self.names({"q": "VEGAN", "sort": "fullName"}), ["Ann Lee", "Cy Ng"])           # answers + case-insensitive
        self.assertEqual(self.names({"q": self.regs[0]["registrationId"].lower()}), ["Ann Lee"])
        self.assertEqual(self.names({"q": "ann vegan"}), ["Ann Lee"])                                     # all terms must match
        self.assertEqual(self.names({"q": "nobody"}), [])

    def test_filters(self):
        self.assertEqual(self.names({"status": "confirmed", "sort": "fullName"}), ["Bo Tan"])
        self.assertEqual(sorted(self.names({"status": "pending_payment,cancelled"})), ["Ann Lee", "Cy Ng"])
        self.assertEqual(self.names({"payment": "refund_pending"}), ["Cy Ng"])
        self.assertEqual(self.names({"ticket": "free"}), ["Bo Tan"])
        self.assertEqual(self.names({"checkedIn": "yes"}), ["Bo Tan"])
        self.assertEqual(sorted(self.names({"checkedIn": "no"})), ["Ann Lee", "Cy Ng"])
        self.assertEqual(sorted(self.names({"f.diet": "vegan"})), ["Ann Lee", "Cy Ng"])                    # any form field, exact, no case
        self.assertEqual(self.names({"f.diet": "vegan", "status": "pending_payment"}), ["Ann Lee"])
        self.assertEqual(len(adm.query_registrations(self.regs, {"f.diet": ""})["rows"]), 3)               # empty filter ignored

    def test_sorting_on_columns_and_form_fields(self):
        self.assertEqual(self.names({"sort": "totalJpy", "dir": "asc"}), ["Bo Tan", "Ann Lee", "Cy Ng"])
        self.assertEqual(self.names({"sort": "totalJpy", "dir": "desc"}), ["Cy Ng", "Ann Lee", "Bo Tan"])
        self.assertEqual(self.names({"sort": "a.diet", "dir": "asc"})[0], "Bo Tan")                         # "none" < "vegan"
        self.assertEqual(self.names({"sort": "fullName"}), ["Ann Lee", "Bo Tan", "Cy Ng"])
        self.assertEqual(len(self.names({"sort": "__evil__"})), 3)                                         # unknown sort ignored

    def test_pagination_and_hidden_credentials(self):
        out = adm.query_registrations(self.regs, {"pageSize": "2", "page": "2", "sort": "fullName"})
        self.assertEqual((out["total"], out["pages"], out["page"], [r["fullName"] for r in out["rows"]]), (3, 2, 2, ["Cy Ng"]))
        self.assertEqual(adm.query_registrations(self.regs, {"page": "99"})["page"], 1)
        self.assertEqual(adm.query_registrations(self.regs, {"pageSize": "banana"})["pageSize"], 50)
        for row in out["rows"]:
            self.assertNotIn("checkInToken", row)
            self.assertNotIn("providerPaymentId", row)


class DashboardTests(Base):
    def test_numbers(self):
        a = self.public("a", tickets={"adult": 2}, method="bank", email="a@example.com")                  # pending 3000
        b = self.public("b", tickets={"adult": 1, "free": 1}, method="bank", email="b@example.com")
        b = logic.mark_paid(b, via="bank", reference="r", now=NOW, event=self.event)                      # paid 1500
        c = self.public("c", tickets={"adult": 1}, method="bank", email="c@example.com")
        c = logic.mark_paid(c, via="bank", reference="r", now=NOW, event=self.event)
        c = logic.cancel(c, by="admin", reason="x", now=NOW, event=self.event)                            # refund pending 1500
        d = self.public("d", tickets={"free": 1}, method=None, email="d@example.com")
        d["checkedInAt"] = "2026-10-10T18:00:00+09:00"
        self.store.events["e1"].update(capacity=10, waitlistEnabled=True)
        regs = [self.store.get_registration(r["registrationId"]) for r in (a, b, c)] + [d]
        regs[3] = {**self.store.get_registration(d["registrationId"]), "checkedInAt": "2026-10-10T18:00:00+09:00"}
        out = adm.dashboard(self.store.get_event("e1"), self.store.get_tickets("e1"), regs, dict(self.store.counters))
        self.assertEqual(out["registrations"]["pending_payment"], 1)
        self.assertEqual((out["registrations"]["confirmed"], out["registrations"]["cancelled"]), (2, 1))
        self.assertEqual(out["revenue"], {"paidJpy": 1500, "pendingJpy": 3000, "refundPendingJpy": 1500, "refundedJpy": 0, "netJpy": 1500})
        self.assertEqual(out["capacity"], {"limit": 10, "used": 5, "remaining": 5, "percent": 50})
        adult = next(t for t in out["tickets"] if t["ticketTypeId"] == "adult")
        self.assertEqual((adult["confirmed"], adult["pending"], adult["revenueJpy"]), (1, 2, 1500))
        self.assertEqual((out["checkedInSeats"], out["confirmedSeats"]), (1, 3))
        self.assertEqual(out["needsAttention"], {"refundPending": 1, "awaitingPayment": 1, "waitlisted": 0})

    def test_empty_event(self):
        out = adm.dashboard({"eventId": "e1"}, [], [], {})
        self.assertEqual((out["total"], out["capacity"]["limit"], out["capacity"]["percent"], out["revenue"]["paidJpy"]), (0, None, None, 0))


class ManualAddTests(Base):
    def test_paid_cash_registration_is_confirmed_with_audit_trail_fields(self):
        reg, created = self.add({"paymentStatus": "paid", "paidVia": "cash", "adminNote": "Paid at the door"})
        self.assertTrue(created)
        self.assertEqual((reg["status"], reg["paymentStatus"], reg["paidVia"], reg["totalJpy"]), ("confirmed", "paid", "cash", 3000))
        self.assertEqual((reg["addedByAdmin"], reg["createdBy"], reg["consentRecordedBy"]), (True, "admin@example.com", "admin@example.com"))
        self.assertEqual(self.store.counters["EVENT#e1"], 2)
        self.assertEqual(self.sent[-1][0], "confirmed")

    def test_pending_hold_and_free_and_notify_off(self):
        reg, _ = self.add({"paymentStatus": "pending", "paymentMethod": "paypay", "notify": False})
        self.assertEqual((reg["status"], reg["paymentMethod"]), ("pending_payment", "paypay"))
        self.assertEqual(reg["holdUntil"], (NOW + timedelta(days=7)).isoformat(timespec="seconds"))
        self.assertEqual(self.sent, [])
        free, _ = self.add({"tickets": {"free": 1}, "idempotencyKey": "m2"})
        self.assertEqual((free["status"], free["paymentStatus"], free["paymentMethod"]), ("confirmed", "none", "none"))

    def test_total_override_needs_a_note(self):
        with self.assertRaises(logic.RegError) as ctx:
            self.add({"totalJpy": 0})
        self.assertIn("adminNote", ctx.exception.fields)
        reg, _ = self.add({"totalJpy": 0, "adminNote": "Volunteer, comped"})
        self.assertEqual((reg["totalJpy"], reg["paymentStatus"], reg["status"]), (0, "none", "confirmed"))
        for bad in (-1, 1.5, "x", True):
            with self.assertRaises(logic.RegError):
                self.add({"totalJpy": bad, "adminNote": "n", "idempotencyKey": f"b{bad}"})

    def test_validation_still_applies(self):
        for body, field in (({"tickets": {"nope": 1}}, "nope"), ({"answers": {**ANSWERS, "fullName": ""}}, "fullName"),
                            ({"answers": {**ANSWERS, "consentTerms": False}}, "consentTerms"), ({"paymentStatus": "maybe"}, "paymentStatus"),
                            ({"paymentStatus": "paid", "paidVia": "bitcoin"}, "paidVia"), ({"paymentMethod": "square"}, "paymentMethod")):
            with self.assertRaises(logic.RegError) as ctx:
                self.add(body)
            self.assertIn(field, ctx.exception.fields, body)
        self.assertEqual((self.store.regs, self.store.counters), ({}, {}))

    def test_capacity_is_enforced_unless_overridden_or_waitlisted(self):
        self.store.events["e1"]["capacity"] = 2
        self.add({"idempotencyKey": "a"})
        with self.assertRaises(logic.RegError) as ctx:
            self.add({"idempotencyKey": "b", "answers": {**ANSWERS, "email": "b@example.com"}})
        self.assertEqual(ctx.exception.code, "sold_out")
        over, _ = self.add({"idempotencyKey": "c", "ignoreCapacity": True, "answers": {**ANSWERS, "email": "c@example.com"}})
        self.assertEqual((over["status"], self.store.counters["EVENT#e1"]), ("pending_payment", 4))
        wait, _ = self.add({"idempotencyKey": "d", "waitlist": True, "answers": {**ANSWERS, "email": "d@example.com"}})
        self.assertEqual((wait["status"], wait["paymentStatus"], self.store.counters["EVENT#e1"]), ("waitlisted", "none", 4))

    def test_closed_window_does_not_block_admins(self):
        event = {**self.store.get_event("e1"), "status": "closed", "access": {"guestsAllowed": False}}
        reg, _ = self.add(event=event)
        self.assertEqual(reg["status"], "pending_payment")

    def test_replay_is_idempotent(self):
        first, c1 = self.add({"idempotencyKey": "same"})
        again, c2 = self.add({"idempotencyKey": "same"})
        self.assertEqual((c1, c2, first["registrationId"] == again["registrationId"], self.store.counters["EVENT#e1"]), (True, False, True, 2))


class EditTests(Base):
    def test_edit_answers_and_note_with_revision(self):
        reg = self.public("a", diet="none")
        out = adm.admin_edit(reg, self.form, {"revision": 1, "answers": {**reg["answers"], "fullName": "Ann L.", "diet": "vegan",
                                                                         "phone": "+81 80-1111-2222"}, "adminNote": "called"}, "admin@x", NOW)
        self.assertEqual((out["fullName"], out["answers"]["diet"], out["adminNote"], out["revision"], out["updatedBy"]), ("Ann L.", "vegan", "called", 2, "admin@x"))
        self.assertEqual((out["tickets"], out["totalJpy"]), (reg["tickets"], reg["totalJpy"]))
        with self.assertRaises(logic.RegError) as ctx:
            adm.admin_edit(reg, self.form, {"revision": 1, "adminNote": "stale"}, "admin@x", NOW)
        self.assertEqual(ctx.exception.status, 409)
        with self.assertRaises(logic.RegError) as ctx:
            adm.admin_edit(out, self.form, {"answers": {}}, "admin@x", NOW)
        self.assertEqual(ctx.exception.status, 400)

    def test_guest_email_can_be_corrected_account_email_cannot(self):
        guest = self.public("g", email="typo@example.com")
        out = adm.admin_edit(guest, self.form, {"revision": 1, "answers": {**guest["answers"], "email": "fixed@example.com"}}, "a", NOW)
        self.assertEqual((out["email"], out["answers"]["email"]), ("fixed@example.com", "fixed@example.com"))
        member = self.public("m", account=ACCOUNT)
        out = adm.admin_edit(member, self.form, {"revision": 1, "answers": {**member["answers"], "email": "other@example.com"}}, "a", NOW)
        self.assertEqual((out["email"], out["answers"]["email"]), ("m@example.com", "m@example.com"))

    def test_validation_errors(self):
        reg = self.public("a")
        with self.assertRaises(logic.RegError) as ctx:
            adm.admin_edit(reg, self.form, {"revision": 1, "answers": {**reg["answers"], "fullName": ""}}, "a", NOW)
        self.assertIn("fullName", ctx.exception.fields)


class RefundTests(Base):
    def paid_then_cancelled(self, via, key="a"):
        reg = self.public(key, method="bank", email=f"{key}@example.com")
        reg = logic.mark_paid(reg, via=via, reference="pay-77", now=NOW, event=self.event)
        return logic.cancel(reg, by="admin", reason="x", now=NOW, event=self.event)

    def test_manual_refund(self):
        reg = self.paid_then_cancelled("bank")
        out = adm.refund(reg, {"mode": "manual", "note": "Bank transfer sent 10/2"}, "admin@x", NOW, self.pay)
        self.assertEqual((out["paymentStatus"], out["refundMode"], out["refundNote"], out["refundedBy"]), ("refunded", "manual", "Bank transfer sent 10/2", "admin@x"))
        self.assertEqual(self.refunds, [])
        with self.assertRaises(logic.RegError) as ctx:
            adm.refund(out, {"mode": "manual"}, "admin@x", NOW, self.pay)
        self.assertEqual(ctx.exception.status, 409)

    def test_square_refund_uses_the_card_payment_id_and_full_amount(self):
        reg = self.paid_then_cancelled("square")
        out = adm.refund(reg, {"mode": "square"}, "admin@x", NOW, self.pay)
        self.assertEqual(self.refunds, [{"payment_id": "pay-77", "amount_yen": 1500, "reason": f"Event registration {reg['registrationId']}"}])
        self.assertEqual((out["paymentStatus"], out["refundReference"], out["refundProviderStatus"]), ("refunded", "rf-1", "PENDING"))

    def test_square_refund_refused_for_non_card_payments_and_failures_change_nothing(self):
        reg = self.paid_then_cancelled("bank")
        with self.assertRaises(logic.RegError) as ctx:
            adm.refund(reg, {"mode": "square"}, "admin@x", NOW, self.pay)
        self.assertEqual(ctx.exception.status, 409)
        card = self.paid_then_cancelled("square", "card")
        self.pay.create_refund = lambda **kw: (_ for _ in ()).throw(RuntimeError("declined"))
        with self.assertRaises(logic.RegError) as ctx:
            adm.refund(card, {"mode": "square"}, "admin@x", NOW, self.pay)
        self.assertEqual(ctx.exception.status, 502)
        self.assertEqual(self.store.get_registration(card["registrationId"])["paymentStatus"], "refund_pending")

    def test_only_pending_refunds_and_valid_modes(self):
        live = self.public("b", email="b@example.com")
        with self.assertRaises(logic.RegError):
            adm.refund(live, {"mode": "manual"}, "a", NOW, self.pay)
        with self.assertRaises(logic.RegError):
            adm.refund(self.paid_then_cancelled("bank", "z"), {"mode": "crypto"}, "a", NOW, self.pay)

    def test_stale_copy_cannot_double_refund(self):
        reg = self.paid_then_cancelled("square")
        adm.refund(reg, {"mode": "square"}, "a", NOW, self.pay)
        with self.assertRaises(logic.RegError) as ctx:
            adm.refund(reg, {"mode": "square"}, "a", NOW, self.pay)               # old copy still says refund_pending
        self.assertEqual(ctx.exception.status, 409)
        self.assertEqual(len(self.refunds), 1)                                      # Square was called exactly once

    def test_failed_square_call_releases_the_claim_so_it_can_be_retried(self):
        reg = self.paid_then_cancelled("square", "retry")
        self.pay.create_refund = lambda **kw: (_ for _ in ()).throw(RuntimeError("declined"))
        with self.assertRaises(logic.RegError):
            adm.refund(reg, {"mode": "square"}, "a", NOW, self.pay)
        again = self.store.get_registration(reg["registrationId"])
        self.assertEqual(again["paymentStatus"], "refund_pending")
        self.pay.create_refund = lambda **kw: self.refunds.append(kw) or {"refundId": "rf-2", "status": "COMPLETED"}
        self.assertEqual(adm.refund(again, {"mode": "square"}, "a", NOW, self.pay)["paymentStatus"], "refunded")

    def test_registration_is_claimed_while_square_is_called(self):
        reg = self.paid_then_cancelled("square", "claim")
        seen = []
        self.pay.create_refund = lambda **kw: seen.append(self.store.get_registration(reg["registrationId"])["paymentStatus"]) or {"refundId": "r", "status": "PENDING"}
        adm.refund(reg, {"mode": "square"}, "a", NOW, self.pay)
        self.assertEqual(seen, ["refunding"])


class CheckInTests(Base):
    def setUp(self):
        super().setUp()
        self.reg = logic.mark_paid(self.public("a", method="bank"), via="bank", reference="r", now=NOW, event=self.event)

    def go(self, reg=None, token="__own__", **kw):
        reg = reg or self.store.get_registration(self.reg["registrationId"])
        token = reg["checkInToken"] if token == "__own__" else token
        return adm.check_in(reg, token, kw.pop("event_id", "e1"), "staff@x", NOW, **kw)

    def test_scan_parsing(self):
        self.assertEqual(adm.parse_scan("WMFEV1.WMF-AAAA-BBBB.tok_en-1"), ("WMF-AAAA-BBBB", "tok_en-1"))
        self.assertEqual(adm.parse_scan(" wmf-aaaa-bbbb "), ("WMF-AAAA-BBBB", None))
        for bad in ("", None, "WMFEV1.", "WMFEV1.only", "WMFEV1..tok"):
            self.assertIsNone(adm.parse_scan(bad), bad)

    def test_check_in_then_already(self):
        out = self.go()
        self.assertEqual(out["result"], "ok")
        self.assertEqual((out["registration"]["fullName"], out["registration"]["seats"]), ("Ann Lee", 1))
        stored = self.store.get_registration(self.reg["registrationId"])
        self.assertEqual((stored["checkedInBy"], bool(stored["checkedInAt"])), ("staff@x", True))
        again = self.go()
        self.assertEqual((again["result"], again["registration"]["checkedInAt"] == stored["checkedInAt"]), ("already", True))

    def test_two_scanners_racing_only_one_wins(self):
        first = self.store.get_registration(self.reg["registrationId"])
        stale = dict(first)
        self.assertEqual(self.go(first)["result"], "ok")
        self.assertEqual(self.go(stale)["result"], "already")                       # stale copy still shows not-checked-in

    def test_refusals(self):
        self.assertEqual(self.go(token="wrong")["result"], "not_found")
        self.assertEqual(adm.check_in(None, "x", "e1", "s", NOW)["result"], "not_found")
        wrong = self.go(event_id="other")
        self.assertEqual(wrong["result"], "wrong_event")
        self.assertNotIn("email", str(wrong))
        pending = self.public("p", email="p@example.com")
        self.assertEqual(self.go(pending)["result"], "payment_due")
        self.assertNotIn("checkedInAt", self.store.get_registration(pending["registrationId"]))
        cancelled = logic.cancel(self.public("c", email="c@example.com"), by="a", reason="x", now=NOW, event=self.event)
        self.assertEqual(self.go(cancelled)["result"], "cancelled")
        self.store.events["e1"].update(capacity=1, waitlistEnabled=True)
        self.store.counters["EVENT#e1"] = 99
        wait = self.public("w", email="w@example.com")
        self.assertEqual((wait["status"], self.go(wait)["result"]), ("waitlisted", "waitlisted"))

    def test_typed_registration_id_without_token(self):
        reg = self.store.get_registration(self.reg["registrationId"])
        self.assertEqual(adm.check_in(reg, None, "e1", "staff@x", NOW)["result"], "ok")

    def test_undo(self):
        self.go()
        out = self.go(undo=True)
        self.assertEqual(out["result"], "undone")
        self.assertNotIn("checkedInAt", self.store.get_registration(self.reg["registrationId"]))
        self.assertEqual(self.go(undo=True)["result"], "not_checked_in")
        self.assertEqual(self.go()["result"], "ok")


class BulkTests(Base):
    def setUp(self):
        super().setUp()
        a = self.public("a", email="a@example.com", fullName="Ann")
        b = logic.mark_paid(self.public("b", email="b@example.com", fullName="Bo", method="bank"), via="bank", reference="r", now=NOW, event=self.event)
        c = self.public("c", email="b@example.com", fullName="Bo again", method="bank")           # same address, second registration
        d = logic.cancel(self.public("d", email="d@example.com", fullName="Di"), by="a", reason="x", now=NOW, event=self.event)
        e = logic.mark_paid(self.public("e", email="e@example.com", fullName="Ed", method="bank"), via="bank", reference="r", now=NOW, event=self.event)
        e["checkedInAt"] = "2026-10-10T18:00:00+09:00"
        self.regs = [a, b, c, d, e]

    def emails(self, audience):
        return sorted(r["email"] for r in adm.bulk_recipients(self.regs, audience))

    def test_audiences_and_dedup(self):
        self.assertEqual(self.emails("confirmed"), ["b@example.com", "e@example.com"])
        self.assertEqual(self.emails("pending"), ["a@example.com", "b@example.com"])
        self.assertEqual(self.emails("active"), ["a@example.com", "b@example.com", "e@example.com"])       # one per address, cancelled excluded
        self.assertEqual(self.emails("cancelled"), ["d@example.com"])
        self.assertEqual(self.emails("checked_in"), ["e@example.com"])
        self.assertEqual(self.emails("not_checked_in"), ["b@example.com"])
        with self.assertRaises(logic.RegError):
            adm.bulk_recipients(self.regs, "everyone")

    def test_validation(self):
        self.assertEqual(adm.validate_message("Hi {name}", "See you at {event_title} {venue}"), {})
        for subject, body, field in (("", "x", "subject"), ("s", " ", "body"), ("s", "{bogus}", "body"), ("x" * 201, "b", "subject"), ("s", "b" * 10001, "body")):
            self.assertIn(field, adm.validate_message(subject, body), (subject[:5], body[:5]))

    def test_send_personalises_isolates_failures_and_counts(self):
        sent = []

        def send(to, subject, text):
            if to == "b@example.com":
                raise RuntimeError("bounced")
            sent.append((to, subject, text))

        out = adm.send_bulk(self.event, adm.bulk_recipients(self.regs, "active"), "Hello {name}", "Event {event_title}, id {registration_id}", send, pause=0)
        self.assertEqual((out["sent"], out["failed"]), (2, ["b@example.com"]))
        self.assertEqual(sent[0][:2], ("a@example.com", "Hello Ann"))
        self.assertIn("Event Picnic, id WMF-", sent[0][2])


if __name__ == "__main__":
    unittest.main()
