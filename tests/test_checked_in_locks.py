"""The locks that close a registration once its party has been admitted.

Changing the entry slot, resizing the party and deleting the record are all
refused after check-in: the pass has been honoured and its seats consumed, and
the slot can no longer be reassigned to match a different head count. The UI
hides these actions; these tests cover the API, which is what stops a direct
call.
"""

import importlib.util
import json
import os
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lambdas"))
# my_registrations/handler.py imports paypay/square as siblings — on disk
# they only live under the seat-upgrade plugin; Terraform copies them into
# my_registrations' own Lambda zip at build time (see backend/lambda.tf's
# archive_file "my_registrations"), so mirror that here for the source tree.
sys.path.insert(0, str(ROOT / "plugins" / "seat-upgrade" / "backend" / "lambdas"))

os.environ.setdefault("REGION", "ap-northeast-1")
os.environ.setdefault("DYNAMODB_TABLE", "registrations-test")
os.environ.setdefault("ENTRY_SLOTS_TABLE", "entry-slots-test")
os.environ.setdefault("SLOT_RESERVATIONS_TABLE", "slot-reservations-test")
os.environ.setdefault("ENTRY_ALLOCATIONS_TABLE", "entry-allocations-test")
os.environ.setdefault("ENTRY_CHECK_INS_TABLE", "entry-check-ins-test")

import entry_allocations  # noqa: E402


def load_handler(relative_path, module_name):
    """Both lambdas call their entry point `handler`, so load them by path."""
    path = ROOT / relative_path
    spec = importlib.util.spec_from_file_location(module_name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    sys.path.insert(0, str(path.parent))
    try:
        spec.loader.exec_module(module)
    finally:
        sys.path.remove(str(path.parent))
    return module


admin = load_handler("lambdas/manage_registrations/handler.py", "admin_handler")
registrant = load_handler("lambdas/my_registrations/handler.py", "registrant_handler")

ONAM_COUNTS = {"adults": 2, "kids914": 1, "kids68": 0, "kidsUnder6": 1}  # 3 seats
STANDARD_COUNTS = {"adult": 2, "teen": 0, "child": 1, "toddler": 1}      # 3 seats


def body_of(response):
    return json.loads(response["body"])


class FakeTable:
    def __init__(self, item):
        self.item = item
        self.deleted = False
        self.written = None

    def get_item(self, Key):
        return {"Item": dict(self.item)} if self.item else {}

    def delete_item(self, Key, ReturnValues=None):
        self.deleted = True
        return {"Attributes": dict(self.item)} if self.item else {}

    def put_item(self, Item):
        self.written = Item
        return {}


class FakeResource:
    def __init__(self, table):
        self.table = table

    def Table(self, name):
        return self.table


class LockTestCase(unittest.TestCase):
    """Stubs out the live check-in count; the real one is covered elsewhere."""

    def setUp(self):
        self.checked_in = 0
        self._real_checked_in_seats = entry_allocations.checked_in_seats
        entry_allocations.checked_in_seats = lambda registration: self.checked_in
        self.addCleanup(setattr, entry_allocations, "checked_in_seats",
                        self._real_checked_in_seats)

    def use_table(self, module, item):
        table = FakeTable(item)
        real = module.dynamodb
        module.dynamodb = FakeResource(table)
        self.addCleanup(setattr, module, "dynamodb", real)
        return table

    def registration(self, **extra):
        return {
            "registrationId": "reg-1",
            "eventType": "onam",
            "registrationType": "onam",
            "email": "guest@example.com",
            "paymentStatus": "paid",
            "attendeeCounts": dict(ONAM_COUNTS),
            "allocations": [{"allocationId": "alloc-1", "seatCount": 3, "checkedInCount": 0}],
            **extra,
        }


class AdminSeatCountLockTests(LockTestCase):
    def test_seat_change_is_refused_after_check_in(self):
        self.checked_in = 3
        counts = {**ONAM_COUNTS, "adults": 3}

        response = admin._seat_count_change_blocked({"counts": counts}, self.registration())

        self.assertEqual(response["statusCode"], 409)
        payload = body_of(response)
        self.assertTrue(payload["checkedIn"])
        self.assertIn("seated attendees", payload["error"])

    def test_seat_change_is_allowed_before_check_in(self):
        counts = {**ONAM_COUNTS, "adults": 3}

        self.assertIsNone(admin._seat_count_change_blocked({"counts": counts}, self.registration()))

    def test_editing_other_fields_is_still_allowed(self):
        self.checked_in = 3

        self.assertIsNone(admin._seat_count_change_blocked({"phone": "+819012345678"},
                                                           self.registration()))

    def test_changing_under_sixes_is_still_allowed(self):
        # Under-6s occupy no seat, so the pass and the record stay in step.
        self.checked_in = 3
        counts = {**ONAM_COUNTS, "kidsUnder6": 4}

        self.assertIsNone(admin._seat_count_change_blocked({"counts": counts},
                                                           self.registration()))

    def test_standard_schema_seat_change_is_refused(self):
        self.checked_in = 3
        existing = self.registration(registrationType="standard",
                                     attendeeCounts=dict(STANDARD_COUNTS))
        counts = {**STANDARD_COUNTS, "teen": 2}

        response = admin._seat_count_change_blocked({"attendeeCounts": counts}, existing)

        self.assertEqual(response["statusCode"], 409)

    def test_standard_schema_toddler_change_is_allowed(self):
        self.checked_in = 3
        existing = self.registration(registrationType="standard",
                                     attendeeCounts=dict(STANDARD_COUNTS))
        counts = {**STANDARD_COUNTS, "toddler": 3}

        self.assertIsNone(admin._seat_count_change_blocked({"attendeeCounts": counts}, existing))

    def test_new_registration_is_never_locked(self):
        self.checked_in = 3

        self.assertIsNone(admin._seat_count_change_blocked({"counts": ONAM_COUNTS}, None))


class AdminDeleteLockTests(LockTestCase):
    def test_checked_in_registration_is_not_deleted(self):
        self.checked_in = 3
        table = self.use_table(admin, self.registration())

        response = admin.delete_registration("reg-1")

        self.assertEqual(response["statusCode"], 409)
        payload = body_of(response)
        self.assertTrue(payload["checkedIn"])
        # The UI offers the override off the back of this flag.
        self.assertTrue(payload["canForce"])
        self.assertFalse(table.deleted)

    def test_registration_is_deleted_before_check_in(self):
        # No allocations, so the reservation-release path stays out of the way.
        table = self.use_table(admin, self.registration(allocations=[]))

        response = admin.delete_registration("reg-1")

        self.assertEqual(response["statusCode"], 200)
        self.assertTrue(table.deleted)

    def test_missing_registration_is_reported(self):
        self.use_table(admin, None)

        self.assertEqual(admin.delete_registration("reg-1")["statusCode"], 404)


class AdminForceDeleteTests(LockTestCase):
    """The override for a registration checked in by mistake.

    It deletes the record but deliberately keeps the seats reserved: they have
    been walked through the gate and cannot be resold.
    """

    def setUp(self):
        super().setUp()
        self.checked_in = 3
        self.allocation = {"allocationId": "alloc-1", "reservationId": "res-1",
                           "checkedInCount": 3}
        self.cancelled = []

        real_get = admin.entry_allocations.get_allocation
        admin.entry_allocations.get_allocation = lambda event_type, allocation_id: (
            self.allocation if allocation_id == self.allocation["allocationId"] else None
        )
        self.addCleanup(setattr, admin.entry_allocations, "get_allocation", real_get)

        real_cancel = admin.entry_slots_logic.cancel_reservation
        admin.entry_slots_logic.cancel_reservation = (
            lambda reservation_id, event_type: self.cancelled.append(reservation_id)
        )
        self.addCleanup(setattr, admin.entry_slots_logic, "cancel_reservation", real_cancel)

    def test_force_deletes_a_checked_in_registration(self):
        table = self.use_table(admin, self.registration())

        response = admin.delete_registration("reg-1", force=True)

        self.assertEqual(response["statusCode"], 200)
        self.assertTrue(table.deleted)
        payload = body_of(response)
        self.assertTrue(payload["forced"])
        self.assertEqual(payload["checkedInSeats"], 3)

    def test_admitted_seats_are_not_returned_to_the_slot(self):
        # Cancelling here would advertise capacity the party inside is using.
        self.use_table(admin, self.registration())

        admin.delete_registration("reg-1", force=True)

        self.assertEqual(self.cancelled, [])

    def test_an_unused_pass_on_the_same_registration_is_released(self):
        # A party may hold several passes; only the ones actually scanned are spent.
        self.allocation = {"allocationId": "alloc-2", "reservationId": "res-2",
                           "checkedInCount": 0}
        registration = self.registration()
        registration["allocations"] = [{"allocationId": "alloc-2", "seatCount": 1}]
        self.use_table(admin, registration)

        admin.delete_registration("reg-1", force=True)

        self.assertEqual(self.cancelled, ["res-2"])

    def test_an_ordinary_delete_is_never_reported_as_forced(self):
        self.checked_in = 0
        self.allocation = {"allocationId": "alloc-1", "reservationId": "res-1",
                           "checkedInCount": 0}
        self.use_table(admin, self.registration())

        payload = body_of(admin.delete_registration("reg-1"))

        self.assertFalse(payload["forced"])
        self.assertEqual(self.cancelled, ["res-1"])


class AdminDeleteRoutingTests(LockTestCase):
    """?force=true is how the override arrives: a DELETE body would not survive
    the trip through API Gateway."""

    def event(self, query=None):
        return {
            "httpMethod": "DELETE",
            "resource": "/admin/registration/{registrationId}",
            "pathParameters": {"registrationId": "reg-1"},
            "queryStringParameters": query,
        }

    def test_force_query_parameter_unlocks_the_delete(self):
        self.checked_in = 3
        table = self.use_table(admin, self.registration(allocations=[]))

        response = admin.lambda_handler(self.event({"force": "true"}), None)

        self.assertEqual(response["statusCode"], 200)
        self.assertTrue(table.deleted)

    def test_delete_without_the_parameter_still_refuses(self):
        self.checked_in = 3
        table = self.use_table(admin, self.registration(allocations=[]))

        response = admin.lambda_handler(self.event(), None)

        self.assertEqual(response["statusCode"], 409)
        self.assertFalse(table.deleted)

    def test_a_non_affirmative_value_is_not_an_override(self):
        self.checked_in = 3
        self.use_table(admin, self.registration(allocations=[]))

        response = admin.lambda_handler(self.event({"force": "false"}), None)

        self.assertEqual(response["statusCode"], 409)


class RegistrantLockTests(LockTestCase):
    def setUp(self):
        super().setUp()
        # Releasing the seats talks to the slots and allocations tables; record
        # the call instead. The release itself is covered by the admin path.
        self.released = []
        real = registrant._release_existing_allocations
        registrant._release_existing_allocations = lambda event_type, item: self.released.append(
            (event_type, item)
        )
        self.addCleanup(setattr, registrant, "_release_existing_allocations", real)

    def test_delete_is_refused_after_check_in(self):
        self.checked_in = 3
        table = self.use_table(registrant, self.registration())

        response = registrant.delete_my_registration("reg-1", "guest@example.com", "*")

        self.assertEqual(response["statusCode"], 409)
        self.assertTrue(body_of(response)["checkedIn"])
        self.assertFalse(table.deleted)

    def test_delete_is_refused_once_paid(self):
        table = self.use_table(registrant, self.registration())

        response = registrant.delete_my_registration("reg-1", "guest@example.com", "*")

        self.assertEqual(response["statusCode"], 403)
        self.assertFalse(table.deleted)

    def test_unpaid_registration_can_still_be_deleted(self):
        table = self.use_table(registrant, self.registration(paymentStatus="pending"))

        response = registrant.delete_my_registration("reg-1", "guest@example.com", "*")

        self.assertEqual(response["statusCode"], 200)
        self.assertTrue(table.deleted)

    def test_deleting_hands_the_seats_back(self):
        # Otherwise the slot holds the reservation forever and the capacity is
        # lost, which is what the admin delete path already avoids.
        self.use_table(registrant, self.registration(paymentStatus="pending"))

        registrant.delete_my_registration("reg-1", "guest@example.com", "*")

        self.assertEqual(len(self.released), 1)
        event_type, item = self.released[0]
        self.assertEqual(event_type, "onam")
        self.assertEqual(item["registrationId"], "reg-1")

    def test_a_failed_release_still_reports_the_delete(self):
        # The record is already gone; a stranded reservation is for the log, not
        # for the registrant, who would otherwise retry a delete that can't work.
        def boom(event_type, item):
            raise RuntimeError("slots table unavailable")

        registrant._release_existing_allocations = boom
        table = self.use_table(registrant, self.registration(paymentStatus="pending"))

        response = registrant.delete_my_registration("reg-1", "guest@example.com", "*")

        self.assertEqual(response["statusCode"], 200)
        self.assertTrue(table.deleted)

    def test_edit_is_refused_after_check_in(self):
        self.checked_in = 3
        self.use_table(registrant, self.registration(paymentStatus="pending"))

        response = registrant.update_my_registration("reg-1", {"name": "New Name"},
                                                     "guest@example.com", "*")

        self.assertEqual(response["statusCode"], 409)
        self.assertTrue(body_of(response)["checkedIn"])

    def test_slot_change_is_refused_after_check_in(self):
        self.checked_in = 3
        table = self.use_table(registrant, self.registration())
        body = {"allocations": [{"slotId": "slot-9", "seatCount": 3}]}

        response = registrant.change_my_registration_slot("reg-1", body,
                                                          "guest@example.com", "*")

        self.assertEqual(response["statusCode"], 409)
        self.assertTrue(body_of(response)["checkedIn"])
        self.assertIsNone(table.written)

    def test_resubmitting_the_same_slot_is_not_an_error(self):
        # A no-op is not a change, so it answers before the lock.
        self.checked_in = 3
        registration = self.registration()
        registration["allocations"] = [
            {"allocationId": "alloc-1", "entrySlotId": "slot-1", "seatCount": 3,
             "checkedInCount": 3}
        ]
        self.use_table(registrant, registration)
        body = {"allocations": [{"slotId": "slot-1", "seatCount": 3}]}

        response = registrant.change_my_registration_slot("reg-1", body,
                                                          "guest@example.com", "*")

        self.assertEqual(response["statusCode"], 200)


if __name__ == "__main__":
    unittest.main()
