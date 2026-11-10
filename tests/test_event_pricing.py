"""Pricing, access, window and availability rules."""

import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lambdas"))

import event_pricing as p  # noqa: E402

JST = timezone(timedelta(hours=9))
NOW = datetime(2026, 9, 1, 12, 0, tzinfo=JST)

ADULT = {"ticketTypeId": "a", "name": "Adult", "priceJpy": 1500, "sortOrder": 0,
         "earlyBirdPriceJpy": 800, "earlyBirdCutoff": "2026-09-10T00:00:00+09:00", "perPersonCap": 4}
CHILD = {"ticketTypeId": "c", "name": "Child", "priceJpy": 500, "sortOrder": 1}


class WindowTests(unittest.TestCase):
    def ev(self, **kw):
        return {"status": "published", **kw}

    def test_states(self):
        self.assertEqual(p.registration_window(self.ev(), NOW), "open")
        self.assertEqual(p.registration_window(self.ev(registrationOpensAt="2026-09-02T00:00:00+09:00"), NOW), "not_open")
        self.assertEqual(p.registration_window(self.ev(registrationClosesAt="2026-08-31T00:00:00+09:00"), NOW), "closed")
        self.assertEqual(p.registration_window(self.ev(registrationClosesAt="2026-09-30T00:00:00+09:00",
                                                       endsAt="2026-08-01T00:00:00+09:00"), NOW), "open")   # close time wins
        self.assertEqual(p.registration_window(self.ev(endsAt="2026-08-31T00:00:00+09:00"), NOW), "closed")  # no close time: ends with the event
        for status in ("draft", "closed", "archived"):
            self.assertEqual(p.registration_window({"status": status}, NOW), "closed")
        self.assertEqual(p.registration_window({"status": "unlisted"}, NOW), "open")

    def test_visibility(self):
        self.assertFalse(p.is_publicly_visible({"status": "draft"}))
        self.assertFalse(p.is_publicly_visible({"status": "archived"}))
        self.assertTrue(p.is_publicly_visible({"status": "unlisted"}))
        self.assertFalse(p.is_listed({"status": "unlisted"}))
        self.assertTrue(p.is_listed({"status": "closed"}))


class AccessTests(unittest.TestCase):
    def test_default_event_allows_guests(self):
        self.assertTrue(p.access_decision({"access": {"guestsAllowed": True}}, False)["allowed"])
        self.assertTrue(p.access_decision({}, False)["allowed"])                      # guests allowed by default

    def test_guests_disallowed_needs_login(self):
        d = p.access_decision({"access": {"guestsAllowed": False}}, False)
        self.assertEqual((d["allowed"], d["reason"]), (False, "login_required"))
        self.assertTrue(p.access_decision({"access": {"guestsAllowed": False}}, True)["allowed"])


class PricingTests(unittest.TestCase):
    def test_one_price_for_everyone_with_a_shared_early_bird(self):
        self.assertEqual(p.unit_price(ADULT, NOW), (800, True))
        later = NOW + timedelta(days=30)
        self.assertEqual(p.unit_price(ADULT, later), (1500, False))
        self.assertEqual(p.unit_price(CHILD, NOW), (500, False))

    def test_early_bird_never_raises_the_price(self):
        pricey = {**ADULT, "earlyBirdPriceJpy": 2000}
        self.assertEqual(p.unit_price(pricey, NOW), (1500, False))

    def test_quote(self):
        lines, total, errors = p.quote([ADULT, CHILD], {"a": 2, "c": 1}, NOW)
        self.assertEqual((errors, total), ({}, 2 * 800 + 500))
        self.assertEqual([l["name"] for l in lines], ["Adult", "Child"])
        self.assertEqual(p.quote([ADULT, CHILD], {"a": 2, "c": 1}, NOW + timedelta(days=30))[1], 3000 + 500)

    def test_quote_validation(self):
        bad = [({}, "tickets"), ("x", "tickets"), ({"a": 0}, "tickets"), ({"zz": 1}, "zz"), ({"a": -1}, "a"),
               ({"a": 1.5}, "a"), ({"a": True}, "a"), ({"a": "2"}, "a"), ({"a": 5}, "a")]       # cap is 4
        for selection, key in bad:
            lines, total, errors = p.quote([ADULT, CHILD], selection, NOW)
            self.assertIn(key, errors, selection)
            self.assertEqual((lines, total), ([], 0))

    def test_inactive_ticket_and_total_cap(self):
        off = {**CHILD, "active": False}
        self.assertIn("c", p.quote([ADULT, off], {"c": 1}, NOW)[2])
        many = {**CHILD, "ticketTypeId": "m"}
        self.assertIn("tickets", p.quote([many], {"m": 51}, NOW)[2])
        self.assertEqual(p.quote([many, CHILD], {"m": 25, "c": 25}, NOW)[2], {})

    def test_zero_quantity_lines_are_ignored(self):
        lines, total, errors = p.quote([ADULT, CHILD], {"a": 0, "c": 2}, NOW)
        self.assertEqual((len(lines), total, errors), (1, 1000, {}))


class SeatOpsAndAvailabilityTests(unittest.TestCase):
    EVENT = {"eventId": "e1", "capacity": 10}

    def test_seat_ops(self):
        lines = p.attach_limits([{"ticketTypeId": "a", "qty": 2}, {"ticketTypeId": "c", "qty": 1}],
                                [{**ADULT, "quantityLimit": 5}, CHILD])
        ops = p.seat_ops(self.EVENT, lines)
        self.assertEqual(ops, [("EVENT#e1", 3, 10), ("TICKET#e1#a", 2, 5), ("TICKET#e1#c", 1, None)])
        self.assertEqual(p.seat_ops({"eventId": "e1"}, lines)[0], ("EVENT#e1", 3, None))

    def test_availability(self):
        tickets = [{"ticketTypeId": "a", "quantityLimit": 5}, {"ticketTypeId": "c"}]
        av = p.availability(self.EVENT, tickets, {"EVENT#e1": 8, "TICKET#e1#a": 5})
        self.assertEqual(av["eventRemaining"], 2)
        self.assertEqual(av["tickets"], {"a": 0, "c": 2})                       # limited by the event total
        self.assertFalse(av["full"])
        full = p.availability(self.EVENT, tickets, {"EVENT#e1": 10})
        self.assertTrue(full["full"])
        unlimited = p.availability({"eventId": "e1"}, tickets, {"EVENT#e1": 999})
        self.assertEqual((unlimited["eventRemaining"], unlimited["tickets"]["c"], unlimited["full"]), (None, None, False))


if __name__ == "__main__":
    unittest.main()
