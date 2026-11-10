"""Celebrity Dinner eligibility tests.

`eligible_guest_count` is the rule the whole feature hinges on: it caps how
many dinner guests a registration may request. Kids under 6 count toward
that cap same as any other category — they're welcome at the Celebrity
Dinner, just never charged for it (see celebrity_dinner_logic.py's
upsert_dinner_registration, which prices every category except kidsUnder6).
"""

import os
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lambdas"))

os.environ.setdefault("REGION", "ap-northeast-1")
os.environ.setdefault("CELEBRITY_DINNER_CONFIG_TABLE", "celebrity-dinner-config-test")
os.environ.setdefault("CELEBRITY_DINNER_REGISTRATIONS_TABLE", "celebrity-dinner-registrations-test")

import celebrity_dinner_logic as dinner  # noqa: E402


class EligibleGuestCountTests(unittest.TestCase):
    def test_onam_schema_includes_kids_under_6(self):
        registration = {
            "registrationType": "onam",
            "attendeeCounts": {"adults": 2, "kids914": 1, "kids68": 1, "kidsUnder6": 3},
        }
        # 2 adults + 1 (9-14) + 1 (6-8) + 3 (under 6) = 7 — under-6 kids are
        # counted for headcount even though they're never priced.
        self.assertEqual(dinner.eligible_guest_count(registration), 7)

    def test_standard_schema_excludes_toddlers(self):
        registration = {
            "registrationType": "standard",
            "attendeeCounts": {"adult": 2, "teen": 1, "child": 1, "toddler": 5},
        }
        self.assertEqual(dinner.eligible_guest_count(registration), 4)

    def test_standard_schema_detected_without_registration_type(self):
        # A record may not carry registrationType explicitly; "adults" in the
        # counts dict is what actually distinguishes the Onam schema.
        registration = {"attendeeCounts": {"adult": 1, "teen": 0, "child": 0, "toddler": 2}}
        self.assertEqual(dinner.eligible_guest_count(registration), 1)

    def test_only_kids_under_6_still_counts_toward_eligible_guests(self):
        registration = {
            "registrationType": "onam",
            "attendeeCounts": {"adults": 0, "kids914": 0, "kids68": 0, "kidsUnder6": 4},
        }
        self.assertEqual(dinner.eligible_guest_count(registration), 4)

    def test_missing_counts_default_to_zero(self):
        self.assertEqual(dinner.eligible_guest_count({}), 0)


class RegistrationWindowStatusTests(unittest.TestCase):
    def test_open_when_no_window_configured(self):
        self.assertEqual(dinner.registration_window_status({}), "open")

    def test_not_open_before_start(self):
        config = {"registrationStartsAt": "2999-01-01T00:00:00+00:00", "registrationEndsAt": ""}
        self.assertEqual(dinner.registration_window_status(config), "not_open")

    def test_closed_after_end(self):
        config = {"registrationStartsAt": "", "registrationEndsAt": "2000-01-01T00:00:00+00:00"}
        self.assertEqual(dinner.registration_window_status(config), "closed")

    def test_open_within_window(self):
        config = {
            "registrationStartsAt": "2000-01-01T00:00:00+00:00",
            "registrationEndsAt": "2999-01-01T00:00:00+00:00",
        }
        self.assertEqual(dinner.registration_window_status(config), "open")


class CancellationAllowedTests(unittest.TestCase):
    """cancellation_allowed gates the registrant-facing Cancel/Cancel &
    Request Refund button (and the server-side delete routes) on the
    admin's cancellationEnabled toggle and optional refundWindowEndsAt
    cutoff — see CelebrityDinnerManagement.jsx and normalize_config."""

    def test_allowed_by_default_with_no_config(self):
        self.assertTrue(dinner.cancellation_allowed({}))

    def test_allowed_when_explicitly_enabled_with_no_window(self):
        self.assertTrue(dinner.cancellation_allowed({"cancellationEnabled": True, "refundWindowEndsAt": ""}))

    def test_blocked_when_disabled(self):
        self.assertFalse(dinner.cancellation_allowed({"cancellationEnabled": False}))

    def test_blocked_when_disabled_even_within_window(self):
        config = {"cancellationEnabled": False, "refundWindowEndsAt": "2999-01-01T00:00:00+00:00"}
        self.assertFalse(dinner.cancellation_allowed(config))

    def test_blocked_when_the_dinner_feature_is_switched_off(self):
        self.assertFalse(dinner.cancellation_allowed({"enabled": False, "cancellationEnabled": True}))
        self.assertTrue(dinner.cancellation_allowed({"enabled": True, "cancellationEnabled": True}))

    def test_allowed_before_refund_window_ends(self):
        config = {"cancellationEnabled": True, "refundWindowEndsAt": "2999-01-01T00:00:00+00:00"}
        self.assertTrue(dinner.cancellation_allowed(config))

    def test_blocked_after_refund_window_ends(self):
        config = {"cancellationEnabled": True, "refundWindowEndsAt": "2000-01-01T00:00:00+00:00"}
        self.assertFalse(dinner.cancellation_allowed(config))


class IsEventCelebrityDinnerEnabledTests(unittest.TestCase):
    """The per-event on/off switch lives on eventInfo.celebrityDinner.enabled
    (set via the Events admin form), not in the config table."""

    def _events_table(self, item):
        table = MagicMock()
        table.get_item.return_value = {"Item": item} if item is not None else {}
        return table

    def test_false_when_events_table_not_configured(self):
        with patch.object(dinner, "EVENTS_TABLE_NAME", ""):
            self.assertFalse(dinner.is_event_celebrity_dinner_enabled("onam_2026"))

    def test_false_when_event_not_found(self):
        with patch.object(dinner, "EVENTS_TABLE_NAME", "events-test"), \
             patch.object(dinner.dynamodb, "Table", return_value=self._events_table(None)):
            self.assertFalse(dinner.is_event_celebrity_dinner_enabled("onam_2026"))

    def test_true_when_enabled_in_dict_event_info(self):
        item = {"eventType": "onam_2026", "eventInfo": {"celebrityDinner": {"enabled": True}}}
        with patch.object(dinner, "EVENTS_TABLE_NAME", "events-test"), \
             patch.object(dinner.dynamodb, "Table", return_value=self._events_table(item)):
            self.assertTrue(dinner.is_event_celebrity_dinner_enabled("onam_2026"))

    def test_true_when_enabled_in_json_string_event_info(self):
        item = {"eventType": "onam_2026", "eventInfo": '{"celebrityDinner": {"enabled": true}}'}
        with patch.object(dinner, "EVENTS_TABLE_NAME", "events-test"), \
             patch.object(dinner.dynamodb, "Table", return_value=self._events_table(item)):
            self.assertTrue(dinner.is_event_celebrity_dinner_enabled("onam_2026"))

    def test_false_when_no_celebrity_dinner_block(self):
        item = {"eventType": "onam_2026", "eventInfo": {"seatUpgrade": {"enabled": True}}}
        with patch.object(dinner, "EVENTS_TABLE_NAME", "events-test"), \
             patch.object(dinner.dynamodb, "Table", return_value=self._events_table(item)):
            self.assertFalse(dinner.is_event_celebrity_dinner_enabled("onam_2026"))

    def test_false_when_explicitly_disabled(self):
        item = {"eventType": "onam_2026", "eventInfo": {"celebrityDinner": {"enabled": False}}}
        with patch.object(dinner, "EVENTS_TABLE_NAME", "events-test"), \
             patch.object(dinner.dynamodb, "Table", return_value=self._events_table(item)):
            self.assertFalse(dinner.is_event_celebrity_dinner_enabled("onam_2026"))


class HasActiveSeatUpgradeDinnerTests(unittest.TestCase):
    """The mutual-exclusivity check: a registration may pick the standalone
    Celebrity Dinner sign-up or the seat-upgrade plugin's bundled 50%-off
    add-on, never both."""

    def _query_result(self, items):
        table = MagicMock()
        table.query.return_value = {"Items": items}
        return table

    def test_false_when_table_not_configured(self):
        with patch.object(dinner, "SEAT_UPGRADES_TABLE", ""):
            self.assertFalse(dinner.has_active_seat_upgrade_dinner("reg-1"))

    def test_false_when_no_upgrades_exist(self):
        with patch.object(dinner, "SEAT_UPGRADES_TABLE", "seat-upgrades-test"), \
             patch.object(dinner.dynamodb, "Table", return_value=self._query_result([])):
            self.assertFalse(dinner.has_active_seat_upgrade_dinner("reg-1"))

    def test_true_for_pending_upgrade_with_dinner_guests(self):
        items = [{"status": "pending", "dinnerAdultVegGuests": 2}]
        with patch.object(dinner, "SEAT_UPGRADES_TABLE", "seat-upgrades-test"), \
             patch.object(dinner.dynamodb, "Table", return_value=self._query_result(items)):
            self.assertTrue(dinner.has_active_seat_upgrade_dinner("reg-1"))

    def test_false_when_upgrade_has_no_dinner_guests(self):
        items = [{"status": "paid", "dinnerAdultVegGuests": 0}]
        with patch.object(dinner, "SEAT_UPGRADES_TABLE", "seat-upgrades-test"), \
             patch.object(dinner.dynamodb, "Table", return_value=self._query_result(items)):
            self.assertFalse(dinner.has_active_seat_upgrade_dinner("reg-1"))

    def test_false_when_upgrade_is_cancelled(self):
        items = [{"status": "cancelled", "dinnerAdultVegGuests": 3}]
        with patch.object(dinner, "SEAT_UPGRADES_TABLE", "seat-upgrades-test"), \
             patch.object(dinner.dynamodb, "Table", return_value=self._query_result(items)):
            self.assertFalse(dinner.has_active_seat_upgrade_dinner("reg-1"))

    def test_true_for_refund_requested_with_dinner_guests(self):
        items = [{"status": "refund_requested", "dinnerKidsUnder6NonVegGuests": 1}]
        with patch.object(dinner, "SEAT_UPGRADES_TABLE", "seat-upgrades-test"), \
             patch.object(dinner.dynamodb, "Table", return_value=self._query_result(items)):
            self.assertTrue(dinner.has_active_seat_upgrade_dinner("reg-1"))


class TotalActiveDinnerGuestCountTests(unittest.TestCase):
    """maxTotalGuests enforcement hinges on this: the combined headcount
    across the host's own standalone sign-ups AND the seat-upgrade plugin's
    bundled add-on, for one event."""

    def _standalone_reg(self, dinner_registration_id, status, adults_veg=0):
        return {
            "dinnerRegistrationId": dinner_registration_id,
            "status": status,
            "numberOfAdultsVeg": adults_veg,
            "numberOfAdultsNonVeg": 0,
            "numberOfKids914Veg": 0,
            "numberOfKids914NonVeg": 0,
            "numberOfKids68Veg": 0,
            "numberOfKids68NonVeg": 0,
            "numberOfKidsUnder6Veg": 0,
            "numberOfKidsUnder6NonVeg": 0,
        }

    def _tables(self, registrations_items, seat_upgrade_items):
        registrations_table = MagicMock()
        registrations_table.query.return_value = {"Items": registrations_items}
        seat_upgrades_table = MagicMock()
        seat_upgrades_table.query.return_value = {"Items": seat_upgrade_items}

        def _table(name):
            if name == "seat-upgrades-test":
                return seat_upgrades_table
            return registrations_table

        return _table

    def test_zero_when_no_bookings(self):
        with patch.object(dinner, "SEAT_UPGRADES_TABLE", ""), \
             patch.object(dinner.dynamodb, "Table", side_effect=self._tables([], [])):
            self.assertEqual(dinner.total_active_dinner_guest_count("onam_2026"), 0)

    def test_sums_active_standalone_bookings_only(self):
        items = [
            self._standalone_reg("d1", "paid", adults_veg=2),
            self._standalone_reg("d2", "pending", adults_veg=3),
            self._standalone_reg("d3", "cancelled", adults_veg=99),  # excluded: inactive
        ]
        with patch.object(dinner, "SEAT_UPGRADES_TABLE", ""), \
             patch.object(dinner.dynamodb, "Table", side_effect=self._tables(items, [])):
            self.assertEqual(dinner.total_active_dinner_guest_count("onam_2026"), 5)

    def test_excludes_the_given_booking_from_its_own_count(self):
        items = [
            self._standalone_reg("d1", "pending", adults_veg=2),
            self._standalone_reg("d2", "pending", adults_veg=3),
        ]
        with patch.object(dinner, "SEAT_UPGRADES_TABLE", ""), \
             patch.object(dinner.dynamodb, "Table", side_effect=self._tables(items, [])):
            self.assertEqual(
                dinner.total_active_dinner_guest_count("onam_2026", exclude_dinner_registration_id="d1"), 3
            )

    def test_includes_active_bundled_seat_upgrade_guests(self):
        standalone = [self._standalone_reg("d1", "paid", adults_veg=2)]
        bundled = [
            {"status": "pending", "dinnerAdultVegGuests": 4, "dinnerKidsUnder6NonVegGuests": 1},
            {"status": "cancelled", "dinnerAdultVegGuests": 99},  # excluded: inactive
        ]
        with patch.object(dinner, "SEAT_UPGRADES_TABLE", "seat-upgrades-test"), \
             patch.object(dinner.dynamodb, "Table", side_effect=self._tables(standalone, bundled)):
            # 2 standalone + (4 + 1) bundled = 7
            self.assertEqual(dinner.total_active_dinner_guest_count("onam_2026"), 7)

    def test_refund_requested_bookings_do_not_hold_seats(self):
        standalone = [
            self._standalone_reg("d1", "paid", adults_veg=2),
            self._standalone_reg("d2", "refund_requested", adults_veg=10),  # cancelled by registrant
        ]
        bundled = [
            {"status": "paid", "dinnerAdultVegGuests": 3},
            {"status": "refund_requested", "dinnerAdultVegGuests": 20},  # cancelled by registrant
        ]
        with patch.object(dinner, "SEAT_UPGRADES_TABLE", "seat-upgrades-test"), \
             patch.object(dinner.dynamodb, "Table", side_effect=self._tables(standalone, bundled)):
            self.assertEqual(dinner.total_active_dinner_guest_count("onam_2026"), 5)

    def test_is_seat_holding_status(self):
        self.assertTrue(dinner.is_seat_holding_status("pending"))
        self.assertTrue(dinner.is_seat_holding_status("PAID"))
        self.assertFalse(dinner.is_seat_holding_status("refund_requested"))
        self.assertFalse(dinner.is_seat_holding_status("cancelled"))


class NormalizeConfigMaxTotalGuestsTests(unittest.TestCase):
    def test_defaults_to_unlimited(self):
        self.assertEqual(dinner.normalize_config(None, "onam_2026")["maxTotalGuests"], 0)

    def test_reads_stored_value(self):
        config = dinner.normalize_config({"maxTotalGuests": 150}, "onam_2026")
        self.assertEqual(config["maxTotalGuests"], 150)


class ListBundledDinnerGuestsForEventTests(unittest.TestCase):
    """The admin sign-ups list needs to show guests booked through the
    seat-upgrade plugin's bundled add-on too — previously invisible on the
    Celebrity Dinner page (see manage_celebrity_dinner/handler.py's GET
    /registrations, which now merges this in)."""

    def _query_result(self, items):
        table = MagicMock()
        table.query.return_value = {"Items": items}
        return table

    def test_empty_when_table_not_configured(self):
        with patch.object(dinner, "SEAT_UPGRADES_TABLE", ""):
            self.assertEqual(dinner.list_bundled_dinner_guests_for_event("onam_2026"), [])

    def test_skips_upgrades_with_no_dinner_guests(self):
        items = [{"upgradeId": "u1", "status": "paid", "dinnerAdultVegGuests": 0}]
        with patch.object(dinner, "SEAT_UPGRADES_TABLE", "seat-upgrades-test"), \
             patch.object(dinner.dynamodb, "Table", return_value=self._query_result(items)):
            self.assertEqual(dinner.list_bundled_dinner_guests_for_event("onam_2026"), [])

    def test_skips_upgrades_with_an_invalid_status(self):
        items = [{"upgradeId": "u1", "status": "held", "dinnerAdultVegGuests": 2}]
        with patch.object(dinner, "SEAT_UPGRADES_TABLE", "seat-upgrades-test"), \
             patch.object(dinner.dynamodb, "Table", return_value=self._query_result(items)):
            self.assertEqual(dinner.list_bundled_dinner_guests_for_event("onam_2026"), [])

    def test_normalizes_a_bundled_booking(self):
        items = [{
            "upgradeId": "u1",
            "eventType": "onam_2026",
            "registrationId": "reg-1",
            "email": "guest@example.com",
            "name": "Jane Doe",
            "tierName": "Gold Zone",
            "status": "paid",
            "paymentMethod": "bank",
            "dinnerAdultVegGuests": 2,
            "dinnerAdultNonVegGuests": 1,
            "dinnerKids914VegGuests": 0,
            "dinnerKids914NonVegGuests": 0,
            "dinnerKids68VegGuests": 0,
            "dinnerKids68NonVegGuests": 0,
            "dinnerKidsUnder6VegGuests": 0,
            "dinnerKidsUnder6NonVegGuests": 0,
            "dinnerPriceAdults": 3000,
            "dinnerAmount": 9000,
            "requestedAt": "2026-01-01T00:00:00+00:00",
        }]
        with patch.object(dinner, "SEAT_UPGRADES_TABLE", "seat-upgrades-test"), \
             patch.object(dinner.dynamodb, "Table", return_value=self._query_result(items)):
            rows = dinner.list_bundled_dinner_guests_for_event("onam_2026")

        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row["source"], "zoneUpgrade")
        self.assertEqual(row["dinnerRegistrationId"], "zu-u1")
        self.assertEqual(row["tierName"], "Gold Zone")
        self.assertEqual(row["registrationId"], "reg-1")
        self.assertEqual(row["numberOfAdultsVeg"], 2)
        self.assertEqual(row["numberOfAdultsNonVeg"], 1)
        self.assertEqual(row["totalAmount"], 9000)
        self.assertEqual(row["status"], "paid")

    def test_includes_cancelled_for_visibility(self):
        items = [{"upgradeId": "u1", "status": "cancelled", "dinnerAdultVegGuests": 2}]
        with patch.object(dinner, "SEAT_UPGRADES_TABLE", "seat-upgrades-test"), \
             patch.object(dinner.dynamodb, "Table", return_value=self._query_result(items)):
            rows = dinner.list_bundled_dinner_guests_for_event("onam_2026")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["status"], "cancelled")


if __name__ == "__main__":
    unittest.main()
