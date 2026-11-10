"""Registration lifecycle: create, capacity, waitlist, payment, cancel, expiry, promotion, self-service."""

import copy
import sys
import threading
import unittest
import unittest.mock
from datetime import timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lambdas"))
sys.path.insert(0, str(ROOT / "tests"))

import event_model  # noqa: E402
import event_registrations as logic  # noqa: E402
from event_fakes import (ACTIVE_MEMBER, ADULT, ANSWERS, EXPIRED_MEMBER, FREE, NOW, FakePayments, FakeStore)  # noqa: E402

METHODS = {"square": {}, "bank": {}, "paypay": {}}


class Base(unittest.TestCase):
    def setUp(self):
        self.store = FakeStore()
        self.pay = FakePayments()
        self.sent = []
        logic.store, logic.payments = self.store, self.pay
        logic.notify = lambda kind, reg, event: self.sent.append((kind, reg["registrationId"]))
        self.event = self.store.add_event()
        self.store.add_tickets("e1", ADULT, FREE)

    def reg(self, tickets=None, member=None, event=None, answers=None, method="square", now=NOW, key="k1", **extra):
        event = event or self.store.get_event("e1")
        body = {"tickets": {"adult": 2} if tickets is None else tickets, "answers": {**ANSWERS, **(answers or {})}, "paymentMethod": method,
                "idempotencyKey": key, **extra}
        return logic.register(event=event, tickets=self.store.get_tickets("e1"), form=self.store.latest_form("f1"),
                              body=body, member=member, methods=METHODS, now=now, redirect_base="https://site.test")

    def counters(self):
        return dict(self.store.counters)


class CreateTests(Base):
    def test_paid_guest_registration_holds_seats_and_starts_card_checkout(self):
        reg, created = self.reg()
        self.assertTrue(created)
        self.assertEqual((reg["status"], reg["paymentStatus"], reg["totalJpy"], reg["seats"]), ("pending_payment", "pending", 3000, 2))
        self.assertRegex(reg["registrationId"], r"^NK-[0-9A-Z]{4}-[0-9A-Z]{4}$")
        self.assertEqual(reg["holdUntil"], (NOW + timedelta(minutes=30)).isoformat(timespec="seconds"))
        self.assertEqual(reg["checkoutUrl"], "https://square.test/order-1")
        self.assertEqual(self.store.find_by_provider_id("order-1")["registrationId"], reg["registrationId"])
        self.assertEqual(self.counters(), {"EVENT#e1": 2, "TICKET#e1#adult": 2})
        self.assertEqual(self.pay.created[0]["amount"], 3000)
        self.assertIn("/events/picnic/registered?reg=", self.pay.created[0]["redirect"])
        self.assertEqual(self.sent, [("pending_payment", reg["registrationId"])])
        self.assertNotIn("memberEmail", reg)

    def test_bank_transfer_holds_for_seven_days_without_checkout(self):
        reg, _ = self.reg(method="bank")
        self.assertEqual(reg["holdUntil"], (NOW + timedelta(days=7)).isoformat(timespec="seconds"))
        self.assertEqual(self.pay.created, [])
        self.assertNotIn("checkoutUrl", reg)

    def test_free_registration_is_confirmed_immediately_without_payment_method(self):
        reg, _ = self.reg(tickets={"free": 1}, method=None)
        self.assertEqual((reg["status"], reg["paymentStatus"], reg["paymentMethod"], reg["totalJpy"]), ("confirmed", "none", "none", 0))
        self.assertNotIn("holdUntil", reg)
        self.assertEqual(self.sent[-1][0], "confirmed")

    def test_snapshot_of_form_version_and_consent(self):
        reg, _ = self.reg()
        form = self.store.latest_form("f1")
        self.assertEqual((reg["formId"], reg["formVersion"], reg["formHash"]), ("f1", 1, form["schemaHash"]))
        self.assertEqual(reg["consentedAt"], NOW.isoformat(timespec="seconds"))
        self.assertTrue(reg["checkInToken"])
        self.assertEqual(reg["answers"]["membershipStatus"], "none")

    def test_member_gets_member_price_account_email_and_system_fields(self):
        reg, _ = self.reg(member=ACTIVE_MEMBER, answers={"email": "someone-else@example.com", "membershipStatus": "hack"})
        self.assertEqual(reg["totalJpy"], 2000)
        self.assertEqual(reg["email"], "m@example.com")
        self.assertEqual((reg["memberEmail"], reg["memberId"]), ("m@example.com", "M1"))
        self.assertEqual((reg["answers"]["membershipStatus"], reg["answers"]["membershipId"], reg["answers"]["membershipExpiry"]),
                         ("active", "M1", "2027-03-31"))

    def test_expired_member_pays_guest_price(self):
        reg, _ = self.reg(member=EXPIRED_MEMBER)
        self.assertEqual((reg["totalJpy"], reg["answers"]["membershipStatus"]), (3000, "expired"))

    def test_client_cannot_set_prices(self):
        reg, _ = self.reg(totalJpy=1, tickets={"adult": 1}, **{"unitJpy": 1})
        self.assertEqual(reg["totalJpy"], 1500)

    def test_idempotent_replay_returns_same_registration_and_takes_no_extra_seats(self):
        first, created1 = self.reg(key="same")
        again, created2 = self.reg(key="same")
        self.assertEqual((created1, created2), (True, False))
        self.assertEqual(first["registrationId"], again["registrationId"])
        self.assertEqual(self.counters()["EVENT#e1"], 2)
        self.assertEqual(len(self.pay.created), 1)
        other, _ = self.reg(key="different")
        self.assertNotEqual(other["registrationId"], first["registrationId"])

    def test_id_collision_with_another_person_moves_to_next_salt(self):
        victim = logic.make_registration_id("e1", ANSWERS["email"], "k1", 0)
        self.store.regs[victim] = {"registrationId": victim, "email": "other@example.com", "eventId": "e1"}
        reg, created = self.reg(key="k1")
        self.assertTrue(created)
        self.assertNotEqual(reg["registrationId"], victim)

    def test_validation_errors(self):
        for kwargs, field in (({"tickets": {"nope": 1}}, "nope"), ({"tickets": {}}, "tickets"),
                              ({"method": None}, "paymentMethod"), ({"method": "cash"}, "paymentMethod"),
                              ({"answers": {"fullName": ""}}, "fullName"), ({"answers": {"consentTerms": False}}, "consentTerms")):
            with self.assertRaises(logic.RegError) as ctx:
                self.reg(**kwargs)
            self.assertEqual(ctx.exception.status, 400)
            self.assertIn(field, ctx.exception.fields)
        self.assertEqual(self.counters(), {})
        self.assertEqual(self.store.regs, {})

    def test_unavailable_payment_method_is_rejected(self):
        with self.assertRaises(logic.RegError):
            logic.register(event=self.event, tickets=self.store.get_tickets("e1"), form=self.store.latest_form("f1"),
                           body={"tickets": {"adult": 1}, "answers": ANSWERS, "paymentMethod": "square"},
                           member=None, methods={"bank": {}}, now=NOW)

    def test_square_failure_rolls_the_booking_back(self):
        self.pay.fail = True
        with self.assertRaises(logic.RegError) as ctx:
            self.reg()
        self.assertEqual(ctx.exception.status, 502)
        self.assertEqual(self.counters(), {"EVENT#e1": 0, "TICKET#e1#adult": 0})
        (reg,) = self.store.regs.values()
        self.assertEqual((reg["status"], reg["cancelReason"]), ("cancelled", "payment_setup_failed"))
        self.assertEqual(self.sent, [])


class AccessTests(Base):
    def test_window_and_status(self):
        for over, code in (({"registrationOpensAt": "2026-09-02T00:00:00+09:00"}, "not_open"),
                           ({"registrationClosesAt": "2026-08-31T00:00:00+09:00"}, "closed"),
                           ({"status": "closed"}, "closed"), ({"status": "draft"}, "closed")):
            event = {**self.store.get_event("e1"), **over}
            with self.assertRaises(logic.RegError) as ctx:
                self.reg(event=event)
            self.assertEqual((ctx.exception.status, ctx.exception.code), (409, code))

    def test_members_only(self):
        event = {**self.store.get_event("e1"), "access": {"membersOnly": True, "guestsAllowed": False, "requireRenewalCover": True}}
        for member, code in ((None, "login_required"), (EXPIRED_MEMBER, "members_only")):
            with self.assertRaises(logic.RegError) as ctx:
                self.reg(event=event, member=member)
            self.assertEqual((ctx.exception.status, ctx.exception.code), (403, code))
        lapsed = {**ACTIVE_MEMBER, "summary": {"validNow": True, "coversRenewal": False}}
        with self.assertRaises(logic.RegError) as ctx:
            self.reg(event=event, member=lapsed)
        self.assertEqual(ctx.exception.code, "renewal_required")
        reg, _ = self.reg(event=event, member=ACTIVE_MEMBER)
        self.assertEqual(reg["totalJpy"], 2000)
        self.assertEqual(self.counters()["EVENT#e1"], 2)        # refused attempts took no seats

    def test_guests_not_allowed(self):
        event = {**self.store.get_event("e1"), "access": {"guestsAllowed": False}}
        with self.assertRaises(logic.RegError) as ctx:
            self.reg(event=event)
        self.assertEqual(ctx.exception.code, "login_required")
        self.assertEqual(self.reg(event=event, member=EXPIRED_MEMBER)[0]["status"], "pending_payment")


class CapacityTests(Base):
    def setUp(self):
        super().setUp()
        patcher = unittest.mock.patch.object(logic, "MAX_UNPAID_PER_EMAIL", 99)     # these tests reuse one address on purpose
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_event_capacity_blocks_and_ticket_limit_blocks(self):
        self.store.events["e1"]["capacity"] = 3
        self.reg(tickets={"adult": 2}, key="a")
        with self.assertRaises(logic.RegError) as ctx:
            self.reg(tickets={"adult": 2}, key="b")
        self.assertEqual((ctx.exception.status, ctx.exception.code), (409, "sold_out"))
        self.assertIn("full", ctx.exception.message)
        self.reg(tickets={"adult": 1}, key="c")                        # exactly fills it
        self.assertEqual(self.counters()["EVENT#e1"], 3)
        self.store.events["e1"].pop("capacity")
        self.store.tickets["e1"] = [{**ADULT, "quantityLimit": 2}, FREE]
        self.store.counters = {}
        self.reg(tickets={"adult": 2}, key="d")
        with self.assertRaises(logic.RegError) as ctx:
            self.reg(tickets={"adult": 1}, key="e")
        self.assertIn("sold out", ctx.exception.message)
        self.reg(tickets={"free": 5}, key="f")                         # other ticket types unaffected

    def test_a_failed_booking_leaves_no_partial_counters(self):
        self.store.tickets["e1"] = [{**ADULT, "quantityLimit": 1}, FREE]
        with self.assertRaises(logic.RegError):
            self.reg(tickets={"adult": 2, "free": 1})
        self.assertEqual(self.counters(), {})

    def test_request_larger_than_capacity_is_refused(self):
        self.store.events["e1"]["capacity"] = 2
        with self.assertRaises(logic.RegError):
            self.reg(tickets={"adult": 3})

    def test_concurrent_registrations_never_overbook(self):
        self.store.events["e1"]["capacity"] = 10
        results = []

        def attempt(i):
            try:
                self.reg(tickets={"adult": 1}, key=f"k{i}", answers={"email": f"p{i}@example.com"}, method="bank")
                results.append("ok")
            except logic.RegError:
                results.append("full")

        threads = [threading.Thread(target=attempt, args=(i,)) for i in range(40)]
        [t.start() for t in threads]
        [t.join() for t in threads]
        self.assertEqual((results.count("ok"), results.count("full")), (10, 30))
        self.assertEqual(self.counters()["EVENT#e1"], 10)
        self.assertNotIn("get_counters", self.store.calls)             # no read-then-write: only atomic reserve

    def test_waitlist_when_full(self):
        self.store.events["e1"].update(capacity=2, waitlistEnabled=True)
        self.reg(tickets={"adult": 2}, key="a")
        wait, _ = self.reg(tickets={"adult": 1}, key="b", method="bank", answers={"email": "w@example.com"})
        self.assertEqual((wait["status"], wait["paymentStatus"], wait["paymentMethod"]), ("waitlisted", "none", "none"))
        self.assertEqual(wait["waitlistedFor"], "bank")
        self.assertNotIn("holdUntil", wait)
        self.assertEqual(self.counters()["EVENT#e1"], 2)               # waitlist holds no seats
        self.assertEqual(self.sent[-1][0], "waitlisted")
        self.assertEqual(self.pay.created[-1]["reference_id"] != wait["registrationId"], True)   # no checkout for the waitlisted

    def test_idempotent_replay_of_waitlisted(self):
        self.store.events["e1"].update(capacity=1, waitlistEnabled=True)
        self.reg(tickets={"adult": 1}, key="a")
        w1, c1 = self.reg(tickets={"adult": 1}, key="b", answers={"email": "w@example.com"})
        w2, c2 = self.reg(tickets={"adult": 1}, key="b", answers={"email": "w@example.com"})
        self.assertEqual((c1, c2, w1["registrationId"]), (True, False, w2["registrationId"]))


class CancelTests(Base):
    def test_cancel_releases_exact_seats_once(self):
        reg, _ = self.reg(tickets={"adult": 2, "free": 1}, method="bank")
        self.assertEqual(self.counters(), {"EVENT#e1": 3, "TICKET#e1#adult": 2, "TICKET#e1#free": 1})
        out = logic.cancel(reg, by="admin", reason="x", now=NOW, event=self.event)
        self.assertEqual((out["status"], out["paymentStatus"]), ("cancelled", "none"))
        self.assertNotIn("holdUntil", out)
        self.assertEqual(self.counters(), {"EVENT#e1": 0, "TICKET#e1#adult": 0, "TICKET#e1#free": 0})
        with self.assertRaises(logic.StateConflict):
            logic.cancel(reg, by="admin", reason="x", now=NOW)
        self.assertEqual(self.counters()["EVENT#e1"], 0)               # not released twice
        self.assertEqual(self.sent[-1][0], "cancelled")

    def test_paid_cancellation_needs_refund(self):
        reg, _ = self.reg(method="bank")
        paid = logic.mark_paid(reg, via="bank", reference="r", now=NOW, event=self.event, actor="admin")
        out = logic.cancel(paid, by="admin", reason="x", now=NOW, event=self.event)
        self.assertEqual(out["paymentStatus"], "refund_pending")

    def test_cancelling_pending_card_registration_cancels_square_order(self):
        reg, _ = self.reg()
        logic.cancel(reg, by="x", reason="y", now=NOW, event=self.event)
        self.assertEqual(self.pay.cancelled, ["order-1"])

    def test_cancelling_a_waitlisted_registration_holds_no_seats(self):
        self.store.events["e1"].update(capacity=1, waitlistEnabled=True)
        self.reg(tickets={"adult": 1}, key="a")
        w, _ = self.reg(tickets={"adult": 1}, key="b", answers={"email": "w@example.com"})
        logic.cancel(w, by="x", reason="y", now=NOW, event=self.event)
        self.assertEqual(self.counters()["EVENT#e1"], 1)


class PaymentTests(Base):
    def test_mark_paid_confirms_and_is_idempotent(self):
        reg, _ = self.reg()
        paid = logic.mark_paid(reg, via="square", reference="pay-1", now=NOW, event=self.event)
        self.assertEqual((paid["status"], paid["paymentStatus"], paid["paidVia"]), ("confirmed", "paid", "square"))
        self.assertNotIn("holdUntil", paid)
        again = logic.mark_paid(self.store.get_registration(reg["registrationId"]), via="square", reference="pay-1", now=NOW, event=self.event)
        self.assertEqual(again["status"], "confirmed")
        self.assertEqual([k for k, _ in self.sent].count("confirmed"), 1)       # one confirmation email

    def test_stale_pending_snapshot_still_settles_once(self):
        reg, _ = self.reg()
        logic.mark_paid(reg, via="square", reference="p", now=NOW, event=self.event)
        logic.mark_paid(reg, via="square", reference="p", now=NOW, event=self.event)     # webhook retry with an old copy
        self.assertEqual(self.store.get_registration(reg["registrationId"])["status"], "confirmed")
        self.assertEqual([k for k, _ in self.sent].count("confirmed"), 1)

    def test_payment_after_hold_expired_retakes_seats_when_free(self):
        reg, _ = self.reg()
        later = NOW + timedelta(hours=2)
        self.assertEqual(logic.expire_holds(later, {"e1": self.event}), 1)
        self.assertEqual(self.counters()["EVENT#e1"], 0)
        expired = self.store.get_registration(reg["registrationId"])
        out = logic.mark_paid(expired, via="square", reference="late", now=later, event=self.event)
        self.assertEqual((out["status"], out["paymentStatus"]), ("confirmed", "paid"))
        self.assertNotIn("cancelReason", out)
        self.assertEqual(self.counters()["EVENT#e1"], 2)

    def test_payment_after_expiry_when_event_filled_up_flags_refund(self):
        self.store.events["e1"]["capacity"] = 2
        reg, _ = self.reg(tickets={"adult": 2}, key="a")
        later = NOW + timedelta(hours=2)
        logic.expire_holds(later, {"e1": self.store.get_event("e1")})
        self.reg(tickets={"adult": 2}, key="b", answers={"email": "new@example.com"}, now=later)       # someone else takes the seats
        out = logic.mark_paid(self.store.get_registration(reg["registrationId"]), via="square", reference="late",
                              now=later, event=self.store.get_event("e1"))
        self.assertEqual((out["status"], out["paymentStatus"]), ("cancelled", "refund_pending"))
        self.assertEqual(self.counters()["EVENT#e1"], 2)

    def test_payment_for_cancelled_registration_flags_refund(self):
        reg, _ = self.reg()
        cancelled = logic.cancel(reg, by="admin", reason="changed mind", now=NOW, event=self.event)
        out = logic.mark_paid(cancelled, via="square", reference="x", now=NOW, event=self.event)
        self.assertEqual((out["status"], out["paymentStatus"]), ("cancelled", "refund_pending"))
        self.assertEqual(self.counters()["EVENT#e1"], 0)

    def test_begin_payment_restarts_card_checkout(self):
        reg, _ = self.reg(method="bank")
        out = logic.begin_payment(reg, self.event, "square", METHODS, NOW + timedelta(hours=1), "https://site.test")
        self.assertEqual((out["paymentMethod"], out["checkoutUrl"]), ("square", "https://square.test/order-1"))
        self.assertEqual(out["holdUntil"], (NOW + timedelta(hours=1, minutes=30)).isoformat(timespec="seconds"))
        with self.assertRaises(logic.RegError):
            logic.begin_payment(out, self.event, "cash", METHODS, NOW)
        paid = logic.mark_paid(out, via="square", reference="p", now=NOW, event=self.event)
        with self.assertRaises(logic.RegError):
            logic.begin_payment(paid, self.event, "square", METHODS, NOW)


class ExpiryTests(Base):
    def test_only_expired_unpaid_holds_are_released(self):
        card, _ = self.reg(key="a", answers={"email": "a@example.com"})
        bank, _ = self.reg(key="b", answers={"email": "b@example.com"}, method="bank")
        free, _ = self.reg(tickets={"free": 1}, method=None, key="c", answers={"email": "c@example.com"})
        self.assertEqual(logic.expire_holds(NOW + timedelta(minutes=31), {"e1": self.event}), 1)
        status = lambda r: self.store.get_registration(r["registrationId"])["status"]
        self.assertEqual((status(card), status(bank), status(free)), ("cancelled", "pending_payment", "confirmed"))
        self.assertEqual(self.store.get_registration(card["registrationId"])["cancelReason"], "hold_expired")
        self.assertEqual(logic.expire_holds(NOW + timedelta(days=8), {"e1": self.event}), 1)
        self.assertEqual(logic.expire_holds(NOW + timedelta(days=9), {"e1": self.event}), 0)       # nothing left, no double release
        self.assertEqual(self.counters()["EVENT#e1"], 1)                                          # the free one still holds its seat
        self.assertIn(("expired", card["registrationId"]), self.sent)


class PromoteTests(Base):
    def setUp(self):
        super().setUp()
        self.store.events["e1"].update(capacity=2, waitlistEnabled=True)
        self.event = self.store.get_event("e1")
        self.first, _ = self.reg(tickets={"adult": 2}, key="a", method="bank")
        self.wait, _ = self.reg(tickets={"adult": 1}, key="b", method="bank", answers={"email": "w@example.com"})

    def test_promote_needs_free_seats_then_creates_a_payment_hold(self):
        with self.assertRaises(logic.RegError) as ctx:
            logic.promote(self.wait, self.event, self.store.get_tickets("e1"), now=NOW, actor="admin")
        self.assertEqual(ctx.exception.code, "sold_out")
        logic.cancel(self.first, by="admin", reason="x", now=NOW, event=self.event)
        out = logic.promote(self.wait, self.event, self.store.get_tickets("e1"), now=NOW, actor="admin")
        self.assertEqual((out["status"], out["paymentStatus"], out["paymentMethod"]), ("pending_payment", "pending", "bank"))
        self.assertEqual(out["holdUntil"], (NOW + timedelta(days=7)).isoformat(timespec="seconds"))
        self.assertEqual(self.counters()["EVENT#e1"], 1)
        self.assertEqual(self.sent[-1][0], "promoted")
        with self.assertRaises(logic.RegError):
            logic.promote(out, self.event, self.store.get_tickets("e1"), now=NOW, actor="admin")

    def test_double_promotion_race_takes_seats_once(self):
        logic.cancel(self.first, by="admin", reason="x", now=NOW, event=self.event)
        logic.promote(self.wait, self.event, self.store.get_tickets("e1"), now=NOW, actor="a1")
        with self.assertRaises(logic.RegError):
            logic.promote(self.wait, self.event, self.store.get_tickets("e1"), now=NOW, actor="a2")   # stale copy
        self.assertEqual(self.counters()["EVENT#e1"], 1)

    def test_free_event_promotion_confirms(self):
        self.store.regs.clear()
        self.store.counters.clear()
        self.store.tickets["e1"] = [ADULT, FREE]
        self.store.events["e1"]["capacity"] = 1
        event = self.store.get_event("e1")
        a, _ = self.reg(tickets={"free": 1}, key="f1", method=None, answers={"email": "f1@example.com"}, event=event)
        w, _ = self.reg(tickets={"free": 1}, key="f2", method=None, answers={"email": "f2@example.com"}, event=event)
        self.assertEqual(w["status"], "waitlisted")
        logic.cancel(a, by="x", reason="y", now=NOW, event=event)
        self.assertEqual(logic.promote(w, event, self.store.get_tickets("e1"), now=NOW, actor="admin")["status"], "confirmed")


class SelfServiceTests(Base):
    def setUp(self):
        super().setUp()
        self.mine, _ = self.reg(member=ACTIVE_MEMBER, method="bank")
        self.form = self.store.latest_form("f1")
        self.event = self.store.get_event("e1")

    def test_edit_answers_only_validated_against_registered_form_version(self):
        new = {**self.mine["answers"], "fullName": "Ann B. Lee", "phone": "+81 80-0000-0000", "email": "attacker@example.com",
               "membershipStatus": "none", "membershipId": "HACK"}
        out = logic.member_edit(self.mine, self.event, self.form, new, ACTIVE_MEMBER, NOW)
        self.assertEqual((out["fullName"], out["phone"], out["revision"]), ("Ann B. Lee", "+81 80-0000-0000", 2))
        self.assertEqual((out["answers"]["email"], out["answers"]["membershipStatus"], out["answers"]["membershipId"]),
                         ("m@example.com", "active", "M1"))
        self.assertEqual((out["tickets"], out["totalJpy"]), (self.mine["tickets"], self.mine["totalJpy"]))   # price untouched

    def test_edit_validation_and_stale_revision(self):
        with self.assertRaises(logic.RegError) as ctx:
            logic.member_edit(self.mine, self.event, self.form, {**self.mine["answers"], "fullName": ""}, ACTIVE_MEMBER, NOW)
        self.assertIn("fullName", ctx.exception.fields)
        logic.member_edit(self.mine, self.event, self.form, self.mine["answers"], ACTIVE_MEMBER, NOW)
        with self.assertRaises(logic.RegError) as ctx:
            logic.member_edit(self.mine, self.event, self.form, self.mine["answers"], ACTIVE_MEMBER, NOW)     # stale copy
        self.assertEqual(ctx.exception.status, 409)

    def test_only_the_owner_can_touch_a_registration(self):
        stranger = {**ACTIVE_MEMBER, "email": "stranger@example.com"}
        for fn in (lambda: logic.member_cancel(self.mine, self.event, stranger, NOW),
                   lambda: logic.member_edit(self.mine, self.event, self.form, self.mine["answers"], stranger, NOW),
                   lambda: logic.member_cancel(self.mine, self.event, None, NOW),
                   lambda: logic.member_cancel(None, self.event, ACTIVE_MEMBER, NOW)):
            with self.assertRaises(logic.RegError) as ctx:
                fn()
            self.assertEqual(ctx.exception.status, 404)
        guest, _ = self.reg(key="g", answers={"email": "guest@example.com"}, method="bank")
        with self.assertRaises(logic.RegError):
            logic.member_cancel(guest, self.event, {**ACTIVE_MEMBER, "email": "guest@example.com"}, NOW)    # guests have no self-service

    def test_cutoff_is_the_registration_close_time(self):
        event = {**self.event, "registrationClosesAt": "2026-09-01T18:00:00+09:00"}
        logic.member_edit(self.mine, event, self.form, self.mine["answers"], ACTIVE_MEMBER, NOW)
        late = NOW + timedelta(hours=7)
        for fn in (lambda: logic.member_cancel(self.store.get_registration(self.mine["registrationId"]), event, ACTIVE_MEMBER, late),
                   lambda: logic.member_edit(self.store.get_registration(self.mine["registrationId"]), event, self.form,
                                             self.mine["answers"], ACTIVE_MEMBER, late)):
            with self.assertRaises(logic.RegError) as ctx:
                fn()
            self.assertEqual((ctx.exception.status, ctx.exception.code), (409, "closed"))

    def test_member_cancel_releases_seats_and_paid_goes_to_refund(self):
        paid = logic.mark_paid(self.mine, via="bank", reference="r", now=NOW, event=self.event, actor="admin")
        out = logic.member_cancel(paid, self.event, ACTIVE_MEMBER, NOW)
        self.assertEqual((out["status"], out["paymentStatus"], out["cancelledBy"], out["cancelReason"]),
                         ("cancelled", "refund_pending", "m@example.com", "member_cancelled"))
        self.assertEqual(self.counters()["EVENT#e1"], 0)
        with self.assertRaises(logic.RegError) as ctx:
            logic.member_cancel(out, self.event, ACTIVE_MEMBER, NOW)
        self.assertEqual(ctx.exception.status, 409)

    def test_cancelled_registration_cannot_be_edited(self):
        out = logic.member_cancel(self.mine, self.event, ACTIVE_MEMBER, NOW)
        with self.assertRaises(logic.RegError):
            logic.member_edit(out, self.event, self.form, out["answers"], ACTIVE_MEMBER, NOW)


class HelperTests(unittest.TestCase):
    def test_registration_ids_are_deterministic_and_distinct(self):
        a = logic.make_registration_id("e", "a@b.co", "k")
        self.assertEqual(a, logic.make_registration_id("e", "a@b.co", "k"))
        self.assertNotEqual(a, logic.make_registration_id("e", "a@b.co", "k2"))
        self.assertNotEqual(a, logic.make_registration_id("e", "a@b.co", "k", 1))
        ids = {logic.make_registration_id("e", f"{i}@b.co", "k") for i in range(2000)}
        self.assertEqual(len(ids), 2000)

    def test_tokens_are_unique_and_long(self):
        tokens = {logic.new_check_in_token() for _ in range(100)}
        self.assertEqual(len(tokens), 100)
        self.assertGreaterEqual(min(len(t) for t in tokens), 30)

    def test_public_registration_hides_provider_id(self):
        self.assertNotIn("providerPaymentId", logic.public_registration({"providerPaymentId": "o", "a": 1}))


if __name__ == "__main__":
    unittest.main()
