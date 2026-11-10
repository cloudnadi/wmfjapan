"""Native add-ons: entry slots, zone upgrades and seat reservation — configuration, pricing, atomic capacity,
release/re-reservation, the public API and the admin API."""

import json
import sys
import unittest
from datetime import timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for sub in ("lambdas", "lambdas/manage_event_platform", "tests"):
    sys.path.insert(0, str(ROOT / sub))

import event_addons as ad  # noqa: E402
import event_model  # noqa: E402
import event_registrations as logic  # noqa: E402
from event_fakes import ADULT, ANSWERS, FREE, NOW, FakePayments, FakeStore  # noqa: E402
import test_event_platform_public as tp  # noqa: E402
import test_event_platform_admin as ta  # noqa: E402

SLOTS = [{"slotId": "am", "name": "Morning", "capacity": 3, "startsAt": "2026-10-10T10:00:00+09:00"},
         {"slotId": "pm", "name": "Afternoon", "capacity": 10}]
UPGRADES = [{"upgradeId": "vip", "name": "VIP zone", "description": "Front rows", "priceJpy": 500, "limit": 2},
            {"upgradeId": "lounge", "name": "Lounge", "priceJpy": 300}]
SEATMAP = {"sections": [{"sectionId": "main", "name": "Main hall", "rows": 2, "seatsPerRow": 3, "extraPriceJpy": 0},
                        {"sectionId": "front", "name": "Front", "rows": 1, "seatsPerRow": 2, "extraPriceJpy": 700}]}
ALL_ON = {"seatReservation": {"enabled": True}, "zoneUpgrade": {"enabled": True}, "entrySlots": {"enabled": True}}


def event(**over):
    return {"eventId": "e1", "slots": SLOTS, "upgrades": UPGRADES, "seatMap": SEATMAP, "capabilities": ALL_ON, **over}


class ConfigTests(unittest.TestCase):
    def test_valid_config_and_ids(self):
        self.assertEqual(ad.validate_config(event()), {})
        clean = ad.clean_config({"slots": [{"name": "Early", "capacity": 5}], "upgrades": [{"name": "VIP", "priceJpy": 100}],
                                 "seatMap": {"sections": [{"name": "Hall", "rows": 1, "seatsPerRow": 2}]}})
        self.assertTrue(all(clean["slots"][0]["slotId"] and clean["upgrades"][0]["upgradeId"] for _ in [0]))
        self.assertEqual(clean["seatMap"]["sections"][0]["extraPriceJpy"], 0)
        self.assertEqual(ad.clean_config({"slots": SLOTS})["slots"][0]["slotId"], "am")        # existing ids are kept

    def test_seat_ids(self):
        self.assertEqual(ad.seat_ids(SEATMAP["sections"][0]), ["main-A1", "main-A2", "main-A3", "main-B1", "main-B2", "main-B3"])
        self.assertEqual(len(ad.seat_index(event())), 8)

    def test_invalid_values_are_refused(self):
        bad = [({"slots": [{"name": "", "capacity": 1}]}, "slots[0]"), ({"slots": [{"name": "A", "capacity": 0}]}, "slots[0]"),
               ({"slots": [{"name": "A", "capacity": 1, "startsAt": "tomorrow"}]}, "slots[0]"),
               ({"slots": [{"name": "A", "capacity": 1, "startsAt": "2026-10-10T12:00:00+09:00", "endsAt": "2026-10-10T10:00:00+09:00"}]}, "slots[0]"),
               ({"slots": [{"name": "A", "capacity": 1, "slotId": "x"}, {"name": "B", "capacity": 1, "slotId": "x"}]}, "slots[1]"),
               ({"slots": "all"}, "slots"), ({"upgrades": [{"name": "A", "priceJpy": -1}]}, "upgrades[0]"),
               ({"upgrades": [{"name": "A", "priceJpy": 1, "limit": 0}]}, "upgrades[0]"),
               ({"seatMap": {"sections": [{"name": "A", "rows": 0, "seatsPerRow": 1}]}}, "seatMap.sections[0]"),
               ({"seatMap": {"sections": [{"name": "A", "rows": 26, "seatsPerRow": 60}, {"name": "B", "rows": 26, "seatsPerRow": 60}]}}, "seatMap"),
               ({"seatMap": []}, "seatMap")]
        for payload, key in bad:
            self.assertIn(key, ad.validate_config(payload), payload)

    def test_enabling_needs_something_to_offer(self):
        for name, cfg in (("entrySlots", "slots"), ("zoneUpgrade", "upgrades"), ("seatReservation", "seatMap")):
            errors = ad.validate_config({"capabilities": {name: {"enabled": True}}})
            self.assertIn(f"capabilities.{name}", errors)
        self.assertEqual(ad.validate_config({"capabilities": {"entrySlots": {"enabled": True}}, "slots": SLOTS}), {})
        self.assertEqual(ad.validate_config({"capabilities": "oops"}), {})          # reported by event_capabilities instead


class ResolveTests(unittest.TestCase):
    def resolve(self, addons, seats=2, **over):
        return ad.resolve(event(**over), {"addons": addons}, seats)

    def test_all_three(self):
        record, jpy, ops, errors = self.resolve({"entrySlots": {"slotId": "am"}, "zoneUpgrade": {"vip": 1, "lounge": 1},
                                                "seatReservation": {"seats": ["main-A1", "front-A2"]}})
        self.assertEqual(errors, {})
        self.assertEqual(jpy, 500 + 300 + 700)                                 # upgrades + the front seat's extra
        self.assertEqual(sorted(ops), sorted([("SLOT#e1#am", 2, 3), ("UPGRADE#e1#vip", 1, 2), ("UPGRADE#e1#lounge", 1, None),
                                              ("SEAT#e1#main-A1", 1, 1), ("SEAT#e1#front-A2", 1, 1)]))
        self.assertEqual(record["entrySlot"]["name"], "Morning")
        self.assertEqual([s["seatId"] for s in record["seats"]], ["main-A1", "front-A2"])
        self.assertEqual(ad.describe({"addons": record}), "Time slot: Morning; Upgrades: VIP zone × 1, Lounge × 1; Seats: main-A1, front-A2")

    def test_disabled_addons_are_ignored_here(self):
        record, jpy, ops, errors = ad.resolve({"eventId": "e1", "slots": SLOTS}, {"addons": {"entrySlots": {"slotId": "am"}}}, 2)
        self.assertEqual((record, jpy, ops, errors), ({}, 0, [], {}))

    def test_required_choices(self):
        errors = self.resolve({})[3]
        self.assertEqual(set(errors), {"addons.entrySlots", "addons.seatReservation"})   # upgrades are optional

    def test_slot_and_seat_validation(self):
        self.assertIn("addons.entrySlots", self.resolve({"entrySlots": {"slotId": "nope"}, "seatReservation": {"seats": ["main-A1", "main-A2"]}})[3])
        for seats in (["main-A1"], ["main-A1", "main-A1"], ["main-A1", "main-Z9"], "main-A1", ["main-A1", 5]):
            self.assertIn("addons.seatReservation", self.resolve({"entrySlots": {"slotId": "am"}, "seatReservation": {"seats": seats}})[3], seats)

    def test_upgrade_validation(self):
        base = {"entrySlots": {"slotId": "am"}, "seatReservation": {"seats": ["main-A1", "main-A2"]}}
        for up in ({"nope": 1}, {"vip": -1}, {"vip": 1.5}, {"vip": "1"}, {"vip": 2, "lounge": 1}):    # the last is 3 upgrades for 2 tickets
            self.assertIn("addons.zoneUpgrade", self.resolve({**base, "zoneUpgrade": up})[3], up)
        self.assertEqual(self.resolve({**base, "zoneUpgrade": {"vip": 0}})[3], {})

    def test_client_cannot_set_prices_or_invent_upgrades(self):
        errors = self.resolve({"entrySlots": {"slotId": "am"}, "seatReservation": {"seats": ["main-A1", "main-A2"]},
                               "zoneUpgrade": {"vip": 1, "priceJpy": 1}})[3]
        self.assertIn("addons.zoneUpgrade", errors)                                # unknown keys are not upgrades
        self.assertEqual(self.resolve({"entrySlots": {"slotId": "am", "capacity": 99, "priceJpy": 1},
                                       "seatReservation": {"seats": ["main-A1", "main-A2"], "extraJpy": 0}})[1], 0)

    def test_public_view_and_counters(self):
        counters = {"SLOT#e1#am": 2, "UPGRADE#e1#vip": 2, "SEAT#e1#main-A1": 1}
        view = ad.public_view(event(), counters)
        self.assertEqual([(s["slotId"], s["remaining"]) for s in view["entrySlots"]["slots"]], [("am", 1), ("pm", 10)])
        self.assertEqual([(u["upgradeId"], u["remaining"]) for u in view["zoneUpgrade"]["upgrades"]], [("vip", 0), ("lounge", None)])
        self.assertEqual(view["seatReservation"]["taken"], ["main-A1"])
        off = ad.public_view(event(capabilities={"zoneUpgrade": {"enabled": True}}), {})
        self.assertEqual(set(off), {"zoneUpgrade"})                                # disabled add-ons are not exposed at all
        self.assertEqual(len(ad.counter_keys(event())), 2 + 2 + 8)
        self.assertEqual(ad.counter_keys(event(capabilities={})), [])

    def test_limits_follow_the_current_configuration(self):
        e = event()
        self.assertEqual([ad.limit_for_key(e, k) for k in ("SLOT#e1#am", "UPGRADE#e1#vip", "UPGRADE#e1#lounge", "SEAT#e1#main-A1")], [3, 2, None, 1])
        self.assertEqual(ad.limit_for_key(e, "SLOT#e1#removed"), 0)                # a deleted slot can no longer be re-taken


class RegistrationTests(unittest.TestCase):
    def setUp(self):
        self.store = FakeStore()
        self.pay = FakePayments()
        logic.store, logic.payments = self.store, self.pay
        logic.notify = lambda *a: None
        self.store.add_event(slots=SLOTS, upgrades=UPGRADES, seatMap=SEATMAP, capabilities=ALL_ON, waitlistEnabled=True, capacity=6)
        self.store.add_tickets("e1", ADULT, FREE)

    def reg(self, addons=None, tickets=None, key="k1", answers=None):
        ev = self.store.get_event("e1")
        body = {"tickets": tickets or {"adult": 2}, "answers": {**ANSWERS, **(answers or {})}, "paymentMethod": "bank", "idempotencyKey": key,
                "addons": addons if addons is not None else {"entrySlots": {"slotId": "am"}, "seatReservation": {"seats": ["main-A1", "main-A2"]}}}
        return logic.register(event=ev, tickets=self.store.get_tickets("e1"), form=self.store.latest_form("f1"), body=body,
                              account=None, methods={"bank": {}}, now=NOW)

    def test_registration_stores_addons_and_charges_for_them(self):
        reg, _ = self.reg({"entrySlots": {"slotId": "am"}, "zoneUpgrade": {"vip": 1}, "seatReservation": {"seats": ["front-A1", "main-A1"]}})
        self.assertEqual((reg["ticketsJpy"], reg["addonJpy"], reg["totalJpy"]), (3000, 1200, 4200))
        self.assertEqual(reg["addons"]["entrySlot"]["slotId"], "am")
        self.assertEqual(self.store.counters["SLOT#e1#am"], 2)
        self.assertEqual(self.store.counters["SEAT#e1#front-A1"], 1)
        self.assertEqual(self.store.counters["UPGRADE#e1#vip"], 1)
        self.assertEqual(self.pay.created[0]["amount"], 4200) if self.pay.created else None

    def test_missing_required_choice_is_a_400_and_takes_nothing(self):
        with self.assertRaises(logic.RegError) as ctx:
            self.reg({"seatReservation": {"seats": ["main-A1", "main-A2"]}})
        self.assertEqual((ctx.exception.status, ctx.exception.code), (400, "addons_invalid"))
        self.assertEqual(self.store.counters, {})

    def test_a_full_slot_is_refused_not_waitlisted(self):
        self.reg(key="a")                                                          # takes 2 of the morning slot's 3
        with self.assertRaises(logic.RegError) as ctx:
            self.reg(addons={"entrySlots": {"slotId": "am"}, "seatReservation": {"seats": ["main-B1", "main-B2"]}}, key="b")
        self.assertEqual((ctx.exception.status, ctx.exception.code), (409, "addon_unavailable"))
        self.assertIn("addons.entrySlots", ctx.exception.fields)
        self.assertEqual(self.store.counters["SLOT#e1#am"], 2)                     # the refused attempt left no trace
        self.assertNotIn("SEAT#e1#main-B1", self.store.counters)
        self.assertEqual(len(self.store.regs), 1)

    def test_a_taken_seat_is_refused(self):
        self.reg(key="a")
        with self.assertRaises(logic.RegError) as ctx:
            self.reg(addons={"entrySlots": {"slotId": "pm"}, "seatReservation": {"seats": ["main-A1", "main-B1"]}}, key="b")
        self.assertEqual(ctx.exception.code, "addon_unavailable")
        self.assertIn("addons.seatReservation", ctx.exception.fields)

    def test_an_upgrade_limit_is_enforced(self):
        self.reg({"entrySlots": {"slotId": "pm"}, "zoneUpgrade": {"vip": 2}, "seatReservation": {"seats": ["main-A1", "main-A2"]}}, key="a")
        with self.assertRaises(logic.RegError) as ctx:
            self.reg({"entrySlots": {"slotId": "pm"}, "zoneUpgrade": {"vip": 1}, "seatReservation": {"seats": ["main-B1", "main-B2"]}}, key="b")
        self.assertIn("addons.zoneUpgrade", ctx.exception.fields)

    def test_concurrent_bookings_of_one_seat_have_one_winner(self):
        import threading
        outcomes = []

        def attempt(n):
            try:
                self.reg({"entrySlots": {"slotId": "pm"}, "seatReservation": {"seats": ["main-B3", f"main-A{n % 3 + 1}"]}}, key=f"c{n}",
                         answers={"email": f"p{n}@example.com"})
                outcomes.append("ok")
            except logic.RegError as exc:
                outcomes.append(exc.code)
        threads = [threading.Thread(target=attempt, args=(n,)) for n in range(8)]
        [t.start() for t in threads]
        [t.join() for t in threads]
        self.assertEqual(outcomes.count("ok"), 1, outcomes)                          # everyone wanted main-B3
        self.assertEqual(self.store.counters["SEAT#e1#main-B3"], 1)
        self.assertEqual(self.store.counters["SLOT#e1#pm"], 2)

    def test_cancel_returns_every_place(self):
        reg, _ = self.reg({"entrySlots": {"slotId": "am"}, "zoneUpgrade": {"vip": 1}, "seatReservation": {"seats": ["main-A1", "main-A2"]}})
        logic.cancel(reg, by="x", reason="r", now=NOW, quiet=True)
        self.assertTrue(all(v == 0 for v in self.store.counters.values()), self.store.counters)
        # ...and the same seats can be booked again
        self.reg({"entrySlots": {"slotId": "am"}, "seatReservation": {"seats": ["main-A1", "main-A2"]}}, key="again")

    def test_unpaid_hold_expiry_returns_the_places(self):
        reg, _ = self.reg()
        logic.expire_holds(NOW + timedelta(days=8), {"e1": self.store.get_event("e1")})
        self.assertTrue(all(v == 0 for v in self.store.counters.values()))

    def test_waitlisted_registration_holds_no_places_and_retakes_them_on_promotion(self):
        self.reg(tickets={"adult": 6}, addons={"entrySlots": {"slotId": "pm"}, "seatReservation": {"seats": ["main-A1", "main-A2", "main-A3", "main-B1", "main-B2", "main-B3"]}}, key="fill")
        wait, _ = self.reg(tickets={"adult": 2}, addons={"entrySlots": {"slotId": "am"}, "seatReservation": {"seats": ["front-A1", "front-A2"]}}, key="w")
        self.assertEqual(wait["status"], "waitlisted")
        self.assertNotIn("SEAT#e1#front-A1", self.store.counters)                  # nothing reserved while waiting
        first = self.store.get_registration([r for r in self.store.regs if self.store.regs[r]["status"] == "pending_payment"][0])
        logic.cancel(first, by="x", reason="r", now=NOW, quiet=True)
        promoted = logic.promote(self.store.get_registration(wait["registrationId"]), self.store.get_event("e1"),
                                 self.store.get_tickets("e1"), now=NOW, actor="admin")
        self.assertEqual(promoted["status"], "pending_payment")
        self.assertEqual((self.store.counters["SEAT#e1#front-A1"], self.store.counters["SLOT#e1#am"]), (1, 2))

    def test_promotion_fails_if_a_chosen_seat_was_taken_meanwhile(self):
        self.reg(tickets={"adult": 6}, addons={"entrySlots": {"slotId": "pm"}, "seatReservation": {"seats": ["main-A1", "main-A2", "main-A3", "main-B1", "main-B2", "main-B3"]}}, key="fill")
        wait, _ = self.reg(tickets={"adult": 2}, addons={"entrySlots": {"slotId": "am"}, "seatReservation": {"seats": ["front-A1", "front-A2"]}}, key="w")
        first = self.store.get_registration([r for r in self.store.regs if self.store.regs[r]["status"] == "pending_payment"][0])
        logic.cancel(first, by="x", reason="r", now=NOW, quiet=True)
        self.reg(tickets={"adult": 2}, addons={"entrySlots": {"slotId": "pm"}, "seatReservation": {"seats": ["front-A1", "main-B1"]}}, key="thief")
        with self.assertRaises(logic.RegError) as ctx:
            logic.promote(self.store.get_registration(wait["registrationId"]), self.store.get_event("e1"), self.store.get_tickets("e1"), now=NOW, actor="admin")
        self.assertEqual(ctx.exception.code, "sold_out")

    def test_events_without_addons_are_unchanged(self):
        self.store.events["e1"]["capabilities"] = {}
        reg, _ = self.reg(addons={})
        self.assertNotIn("addons", reg)
        self.assertEqual((reg["totalJpy"], reg["addonJpy"]), (3000, 0))
        self.assertEqual(set(self.store.counters), {"EVENT#e1", "TICKET#e1#adult"})

    def test_late_payment_retakes_places_or_flags_a_refund(self):
        reg, _ = self.reg()
        logic.expire_holds(NOW + timedelta(days=8), {"e1": self.store.get_event("e1")})
        expired = self.store.get_registration(reg["registrationId"])
        back = logic.mark_paid(expired, via="bank", reference="r", now=NOW, event=self.store.get_event("e1"))
        self.assertEqual((back["status"], self.store.counters["SEAT#e1#main-A1"]), ("confirmed", 1))
        logic.cancel(back, by="x", reason="hold_expired", now=NOW, quiet=True)
        self.reg(addons={"entrySlots": {"slotId": "am"}, "seatReservation": {"seats": ["main-A1", "main-A2"]}}, key="thief")   # someone else takes them
        late = logic.mark_paid(self.store.get_registration(reg["registrationId"]), via="bank", reference="r2", now=NOW, event=self.store.get_event("e1"))
        self.assertEqual(late["paymentStatus"], "refund_pending")


class PublicApiTests(tp.Base):
    def setUp(self):
        super().setUp()
        self.store.events["e1"].update(slots=SLOTS, upgrades=UPGRADES, seatMap=SEATMAP, capabilities=ALL_ON)

    def addons(self, **over):
        return {"entrySlots": {"slotId": "am"}, "seatReservation": {"seats": ["main-A1", "main-A2"]}, **over}

    def test_event_page_lists_enabled_addons_with_remaining_places(self):
        page = self.call("GET", "events/picnic")[1]["event"]
        self.assertEqual(set(page["addons"]), {"entrySlots", "zoneUpgrade", "seatReservation"})
        self.assertEqual(page["addons"]["entrySlots"]["slots"][0]["remaining"], 3)
        self.store.events["e1"]["capabilities"] = {"zoneUpgrade": {"enabled": True}}
        self.assertEqual(set(self.call("GET", "events/picnic")[1]["event"]["addons"]), {"zoneUpgrade"})
        self.store.events["e1"]["capabilities"] = {}
        self.assertEqual(self.call("GET", "events/picnic")[1]["event"]["addons"], {})

    def test_quote_includes_addons(self):
        status, body, _ = self.call("POST", "events/picnic/quote", {"tickets": {"adult": 2}, "addons": self.addons(zoneUpgrade={"vip": 1})})
        self.assertEqual((status, body["ticketsJpy"], body["addonJpy"], body["totalJpy"]), (200, 3000, 500, 3500))
        status, body, _ = self.call("POST", "events/picnic/quote", {"tickets": {"adult": 2}, "addons": {}})
        self.assertEqual((status, body["code"]), (400, "addons_invalid"))

    def test_register_with_addons_and_the_page_then_shows_them_taken(self):
        status, body, _ = self.register({"addons": self.addons(zoneUpgrade={"lounge": 1})})
        self.assertEqual(status, 201, body)
        reg = body["registration"]
        self.assertEqual((reg["totalJpy"], reg["addonJpy"]), (3300, 300))
        page = self.call("GET", "events/picnic")[1]["event"]
        self.assertEqual(sorted(page["addons"]["seatReservation"]["taken"]), ["main-A1", "main-A2"])
        self.assertEqual(page["addons"]["entrySlots"]["slots"][0]["remaining"], 1)
        status, body, _ = self.register({"addons": self.addons(), "idempotencyKey": "other"})
        self.assertEqual((status, body["code"]), (409, "addon_unavailable"))

    def test_a_disabled_addon_in_the_request_is_refused_even_if_configured(self):
        self.store.events["e1"]["capabilities"] = {"entrySlots": {"enabled": True}}
        status, body, _ = self.register({"addons": {"entrySlots": {"slotId": "am"}, "seatReservation": {"seats": ["main-A1", "main-A2"]}}})
        self.assertEqual((status, body["code"]), (400, "feature_disabled"))
        self.assertIn("addons.seatReservation", body["fields"])
        self.assertEqual(self.store.regs, {})

    def test_registrant_view_shows_the_booked_addons(self):
        body = self.register({"addons": self.addons()})[1]
        self.assertEqual(body["registration"]["addons"]["entrySlot"]["name"], "Morning")
        self.assertNotIn("addonOps", [k for k in body["registration"] if False])


class AdminApiTests(ta.ApiTestCase):
    def test_config_round_trip_and_enable_rules(self):
        event = self.create()
        body = {**ta.BASE, "revision": event["revision"], "slots": [{"name": "Early", "capacity": 5}],
                "capabilities": {"entrySlots": {"enabled": True}}}
        status, resp = self.call("PUT", f"events/{event['eventId']}", body)
        self.assertEqual(status, 200, resp)
        saved = resp["event"]
        self.assertEqual((saved["slots"][0]["name"], saved["capabilities"]), ("Early", {"entrySlots": {"enabled": True}}))
        self.assertTrue(saved["slots"][0]["slotId"])                               # the server assigned a stable id
        again = self.call("PUT", f"events/{event['eventId']}", {**ta.BASE, "revision": saved["revision"], "slots": saved["slots"],
                                                                "capabilities": saved["capabilities"]})[1]["event"]
        self.assertEqual(again["slots"][0]["slotId"], saved["slots"][0]["slotId"])

    def test_cannot_enable_an_addon_with_nothing_configured(self):
        for name in ("seatReservation", "zoneUpgrade", "entrySlots"):
            status, resp = self.call("POST", "events", {**ta.BASE, "capabilities": {name: {"enabled": True}}})
            self.assertEqual(status, 400, name)
            self.assertIn(f"capabilities.{name}", resp["fields"])

    def test_bad_config_is_refused(self):
        status, resp = self.call("POST", "events", {**ta.BASE, "slots": [{"name": "x", "capacity": 0}], "seatMap": {"sections": [{"name": "y", "rows": 99, "seatsPerRow": 1}]}})
        self.assertEqual(status, 400)
        self.assertTrue({"slots[0]", "seatMap.sections[0]"} <= set(resp["fields"]))

    def test_duplicate_keeps_the_configuration(self):
        event = self.create(slots=[{"name": "Early", "capacity": 5}], capabilities={"entrySlots": {"enabled": True}})
        copy = self.call("POST", f"events/{event['eventId']}/duplicate")[1]["event"]
        self.assertEqual((copy["slots"], copy["capabilities"]), (event["slots"], event["capabilities"]))

    def test_registration_table_and_export_show_addons(self):
        import event_exports
        reg = {"addons": {"entrySlot": {"name": "Morning"}, "seats": [{"seatId": "main-A1"}]}}
        self.assertEqual(event_exports.cell(reg, "addons"), "Time slot: Morning; Seats: main-A1")


if __name__ == "__main__":
    unittest.main()
