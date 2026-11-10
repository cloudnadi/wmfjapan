import os
import sys
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from botocore.exceptions import ClientError

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lambdas"))

os.environ.setdefault("REGION", "ap-northeast-1")
os.environ.setdefault("ENTRY_SLOTS_TABLE", "entry-slots-test")
os.environ.setdefault("SLOT_RESERVATIONS_TABLE", "slot-reservations-test")

import entry_slots_logic  # noqa: E402


def conditional_check_failed():
    return ClientError(
        {"Error": {"Code": "ConditionalCheckFailedException", "Message": "condition failed"}},
        "UpdateItem",
    )


class FakeDynamoDB:
    def __init__(self):
        self.lock = threading.Lock()
        self.slots = {}
        self.reservations = {}

    def Table(self, name):
        if name == entry_slots_logic.ENTRY_SLOTS_TABLE:
            return FakeSlotTable(self)
        if name == entry_slots_logic.SLOT_RESERVATIONS_TABLE:
            return FakeReservationTable(self)
        raise AssertionError(f"Unexpected table: {name}")


class FakeSlotTable:
    def __init__(self, db):
        self.db = db

    def get_item(self, Key):
        with self.db.lock:
            item = self.db.slots.get((Key["eventType"], Key["slotId"]))
            return {"Item": dict(item)} if item else {}

    def put_item(self, Item):
        with self.db.lock:
            self.db.slots[(Item["eventType"], Item["slotId"])] = dict(Item)
        return {}

    def update_item(self, Key, UpdateExpression, ExpressionAttributeValues, ReturnValues=None, **kwargs):
        with self.db.lock:
            item = self.db.slots[(Key["eventType"], Key["slotId"])]

            if "reservedSeats = if_not_exists(reservedSeats, :occupied)" in UpdateExpression:
                item.setdefault("reservedSeats", ExpressionAttributeValues[":occupied"])
                item.setdefault("updatedAt", ExpressionAttributeValues[":now"])

            elif "reservedSeats = if_not_exists(reservedSeats, :zero) + :required" in UpdateExpression:
                required = ExpressionAttributeValues[":required"]
                max_reserved = int(item.get("bookableCapacity", 0)) - required
                if item.get("status") != "ACTIVE" or int(item.get("reservedSeats", 0)) > max_reserved:
                    raise conditional_check_failed()
                item["reservedSeats"] = int(item.get("reservedSeats", 0)) + required
                item["updatedAt"] = ExpressionAttributeValues[":now"]

            elif "reservedSeats = reservedSeats - :seats" in UpdateExpression:
                seats = ExpressionAttributeValues[":seats"]
                if int(item.get("reservedSeats", 0)) < seats:
                    raise conditional_check_failed()
                item["reservedSeats"] = int(item.get("reservedSeats", 0)) - seats
                item["updatedAt"] = ExpressionAttributeValues[":now"]

            elif "reservedSeats = :zero" in UpdateExpression:
                item["reservedSeats"] = ExpressionAttributeValues[":zero"]
                item["updatedAt"] = ExpressionAttributeValues[":now"]

            elif "bookableCapacity = bookableCapacity + :seats" in UpdateExpression:
                seats = ExpressionAttributeValues[":seats"]
                if int(item.get("bufferCapacity", 0)) < seats:
                    raise conditional_check_failed()
                item["bookableCapacity"] = int(item.get("bookableCapacity", 0)) + seats
                item["bufferCapacity"] = int(item.get("bufferCapacity", 0)) - seats
                item["updatedAt"] = ExpressionAttributeValues[":now"]

            elif "bookableCapacity = :bookable" in UpdateExpression:
                bookable = ExpressionAttributeValues[":bookable"]
                if kwargs.get("ConditionExpression") is not None and int(item.get("reservedSeats", 0)) > bookable:
                    raise conditional_check_failed()
                item["bookableCapacity"] = bookable
                item["updatedAt"] = ExpressionAttributeValues[":now"]

            else:
                raise AssertionError(f"Unsupported slot update: {UpdateExpression}")

            return {"Attributes": dict(item)} if ReturnValues == "ALL_NEW" else {}


class FakeReservationTable:
    def __init__(self, db):
        self.db = db

    def get_item(self, Key):
        with self.db.lock:
            item = self.db.reservations.get((Key["eventType"], Key["reservationId"]))
            return {"Item": dict(item)} if item else {}

    def put_item(self, Item):
        with self.db.lock:
            self.db.reservations[(Item["eventType"], Item["reservationId"])] = dict(Item)
        return {}

    def query(self, **kwargs):
        with self.db.lock:
            active = {"CONFIRMED", "HELD"}
            items = [
                {"requiredSeats": item.get("requiredSeats", 0)}
                for item in self.db.reservations.values()
                if item.get("entrySlotId") == "slot-1" and item.get("status") in active
            ]
        return {"Items": items}

    def update_item(self, Key, UpdateExpression, ExpressionAttributeValues, **kwargs):
        with self.db.lock:
            item = self.db.reservations[(Key["eventType"], Key["reservationId"])]
            if ":cancelled" in ExpressionAttributeValues:
                if kwargs.get("ConditionExpression") is not None and item.get("status") not in {"CONFIRMED", "HELD"}:
                    raise conditional_check_failed()
                item["status"] = ExpressionAttributeValues[":cancelled"]
                item["updatedAt"] = ExpressionAttributeValues[":now"]
                item.pop("expiresAt", None)
            elif ":new_status" in ExpressionAttributeValues:
                item["status"] = ExpressionAttributeValues[":new_status"]
                item["updatedAt"] = ExpressionAttributeValues[":now"]
            else:
                raise AssertionError(f"Unsupported reservation update: {UpdateExpression}")
        return {}


class EntrySlotConcurrencyTests(unittest.TestCase):
    def setUp(self):
        self.db = FakeDynamoDB()
        entry_slots_logic.dynamodb = self.db
        entry_slots_logic.ENTRY_SLOTS_TABLE = "entry-slots-test"
        entry_slots_logic.SLOT_RESERVATIONS_TABLE = "slot-reservations-test"
        self.db.slots[("onam_2026", "slot-1")] = {
            "eventType": "onam_2026",
            "slotId": "slot-1",
            "name": "Morning",
            "nickname": "AM",
            "bookableCapacity": 3,
            "bufferCapacity": 2,
            "reservedSeats": 0,
            "status": "ACTIVE",
        }

    def test_simultaneous_reservations_do_not_oversubscribe_slot(self):
        def reserve(registration_id):
            try:
                return entry_slots_logic.reserve_seats_atomic(
                    "onam_2026", "slot-1", registration_id, 2
                )
            except RuntimeError as exc:
                return exc

        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(reserve, ["reg-a", "reg-b"]))

        successes = [result for result in results if isinstance(result, dict)]
        failures = [result for result in results if isinstance(result, RuntimeError)]
        self.assertEqual(len(successes), 1)
        self.assertEqual(len(failures), 1)
        self.assertIn("Insufficient capacity", str(failures[0]))
        self.assertEqual(self.db.slots[("onam_2026", "slot-1")]["reservedSeats"], 2)

    def test_exact_capacity_allows_first_registration_and_rejects_next(self):
        first = entry_slots_logic.reserve_seats_atomic("onam_2026", "slot-1", "reg-a", 3)

        self.assertEqual(first["requiredSeats"], 3)
        self.assertNotIn("expiresAt", first)
        with self.assertRaisesRegex(RuntimeError, "Insufficient capacity"):
            entry_slots_logic.reserve_seats_atomic("onam_2026", "slot-1", "reg-b", 1)
        self.assertEqual(entry_slots_logic.get_slot_remaining_capacity("onam_2026", "slot-1"), 0)

    def test_cancel_reservation_releases_reserved_seats_and_clears_ttl(self):
        reservation = entry_slots_logic.reserve_seats_atomic("onam_2026", "slot-1", "reg-a", 2)
        self.db.reservations[("onam_2026", reservation["reservationId"])]["expiresAt"] = "2026-09-01T00:00:00+00:00"

        entry_slots_logic.cancel_reservation(reservation["reservationId"], "onam_2026")

        stored = self.db.reservations[("onam_2026", reservation["reservationId"])]
        self.assertEqual(stored["status"], "CANCELLED")
        self.assertNotIn("expiresAt", stored)
        self.assertEqual(self.db.slots[("onam_2026", "slot-1")]["reservedSeats"], 0)

    def test_cancel_reservation_is_idempotent_for_capacity_cleanup(self):
        reservation = entry_slots_logic.reserve_seats_atomic("onam_2026", "slot-1", "reg-a", 2)

        entry_slots_logic.cancel_reservation(reservation["reservationId"], "onam_2026")
        entry_slots_logic.cancel_reservation(reservation["reservationId"], "onam_2026")

        self.assertEqual(self.db.reservations[("onam_2026", reservation["reservationId"])]["status"], "CANCELLED")
        self.assertEqual(self.db.slots[("onam_2026", "slot-1")]["reservedSeats"], 0)

    def test_marking_held_reservation_expired_releases_reserved_seats(self):
        self.db.slots[("onam_2026", "slot-1")]["reservedSeats"] = 2
        self.db.reservations[("onam_2026", "res-held")] = {
            "eventType": "onam_2026",
            "reservationId": "res-held",
            "entrySlotId": "slot-1",
            "registrationId": "reg-held",
            "requiredSeats": 2,
            "status": "HELD",
            "expiresAt": "2026-09-01T00:00:00+00:00",
        }

        entry_slots_logic.update_reservation_status("onam_2026", "res-held", "EXPIRED")

        self.assertEqual(self.db.reservations[("onam_2026", "res-held")]["status"], "EXPIRED")
        self.assertEqual(self.db.slots[("onam_2026", "slot-1")]["reservedSeats"], 0)

    def test_buffer_release_is_capacity_checked(self):
        with self.assertRaisesRegex(ValueError, "only 2 buffer seats"):
            entry_slots_logic.release_buffer_seats("onam_2026", "slot-1", 3)

        updated = entry_slots_logic.release_buffer_seats("onam_2026", "slot-1", 2)

        self.assertEqual(updated["bookableCapacity"], 5)
        self.assertEqual(updated["bufferCapacity"], 0)

    def test_bookable_capacity_cannot_be_lowered_below_reserved_seats(self):
        entry_slots_logic.reserve_seats_atomic("onam_2026", "slot-1", "reg-a", 2)

        with self.assertRaisesRegex(ValueError, "below reserved seats"):
            entry_slots_logic.update_entry_slot(
                "onam_2026",
                "slot-1",
                bookable_capacity=1,
            )

        updated = entry_slots_logic.update_entry_slot(
            "onam_2026",
            "slot-1",
            bookable_capacity=2,
        )
        self.assertEqual(updated["bookableCapacity"], 2)


if __name__ == "__main__":
    unittest.main()