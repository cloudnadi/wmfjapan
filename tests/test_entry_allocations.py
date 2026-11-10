"""Entry-pass check-in tests.

The fakes here deliberately enforce two DynamoDB rules that a permissive stub
would let through, because both have already broken check-in in production:

  1. Condition expressions cannot do arithmetic. `checkedInCount + :quantity
     <= seatCount` is a ValidationException, so the bound must be precomputed.
  2. `dynamodb.meta.client` belongs to the boto3 *resource*, so it serializes
     plain Python values itself. Handing it TypeSerializer output serializes
     everything twice and every value lands as an {"M": ...} map.
"""

import os
import sys
import unittest
from pathlib import Path

from botocore.exceptions import ClientError

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lambdas"))

os.environ.setdefault("REGION", "ap-northeast-1")
os.environ.setdefault("DYNAMODB_TABLE", "registrations-test")
os.environ.setdefault("ENTRY_ALLOCATIONS_TABLE", "entry-allocations-test")
os.environ.setdefault("ENTRY_CHECK_INS_TABLE", "entry-check-ins-test")

import entry_allocations  # noqa: E402

ATTRIBUTE_VALUE_TAGS = {"S", "N", "B", "SS", "NS", "BS", "M", "L", "NULL", "BOOL"}


def conditional_check_failed(operation="UpdateItem"):
    return ClientError(
        {"Error": {"Code": "ConditionalCheckFailedException", "Message": "condition failed"}},
        operation,
    )


def transaction_cancelled():
    return ClientError(
        {"Error": {"Code": "TransactionCanceledException", "Message": "cancelled"}},
        "TransactWriteItems",
    )


def assert_no_arithmetic(expression):
    """DynamoDB rejects arithmetic anywhere in a condition expression."""
    if expression and ("+" in expression or "-" in expression):
        raise AssertionError(
            f"Invalid ConditionExpression: arithmetic is not supported: {expression}"
        )


def assert_not_serialized(value, path="value"):
    """Reject TypeSerializer output handed to the resource's client."""
    if isinstance(value, dict):
        if len(value) == 1 and set(value) <= ATTRIBUTE_VALUE_TAGS:
            raise AssertionError(
                f"{path} is already serialized ({value!r}); the resource client "
                "serializes plain Python values itself"
            )
        for key, nested in value.items():
            assert_not_serialized(nested, f"{path}.{key}")
    elif isinstance(value, list):
        for index, nested in enumerate(value):
            assert_not_serialized(nested, f"{path}[{index}]")


class FakeDynamoDB:
    def __init__(self):
        self.registrations = {}
        self.allocations = {}
        self.check_ins = []
        self.transactions = []
        # Set to run just before a transaction is evaluated, to simulate a
        # second gate device getting there first.
        self.before_transaction = None

    def Table(self, name):
        if name == entry_allocations.REGISTRATIONS_TABLE:
            return FakeRegistrationsTable(self)
        if name == entry_allocations.ALLOCATIONS_TABLE:
            return FakeAllocationsTable(self)
        raise AssertionError(f"Unexpected table: {name}")

    # ── the low-level client used for check-in transactions ────────────────
    def transact_write_items(self, TransactItems):
        self.transactions.append(TransactItems)
        if self.before_transaction:
            hook, self.before_transaction = self.before_transaction, None
            hook()

        updates = []
        for entry in TransactItems:
            if "Update" in entry:
                updates.append(self._plan_update(entry["Update"]))
            elif "Put" in entry:
                updates.append(self._plan_put(entry["Put"]))
            else:
                raise AssertionError(f"Unsupported transaction item: {entry}")
        for apply_change in updates:          # all-or-nothing
            apply_change()
        return {}

    def _plan_update(self, update):
        assert_not_serialized(update["Key"], "Update.Key")
        assert_not_serialized(update["ExpressionAttributeValues"], "Update.ExpressionAttributeValues")
        assert_no_arithmetic(update.get("ConditionExpression"))
        if update["TableName"] != entry_allocations.ALLOCATIONS_TABLE:
            raise AssertionError(f"Unexpected update table: {update['TableName']}")

        key = (update["Key"]["eventType"], update["Key"]["allocationId"])
        item = self.allocations[key]
        values = update["ExpressionAttributeValues"]

        if update.get("ConditionExpression") != entry_allocations._no_overflow_condition():
            raise AssertionError(f"Unexpected condition: {update.get('ConditionExpression')}")
        checked_in = item.get("checkedInCount")
        overflows = checked_in is not None and int(checked_in) > int(values[":max_before"])
        if item.get("status") != values[":active"] or overflows:
            raise transaction_cancelled()

        if "checkedInCount = if_not_exists(checkedInCount, :zero) + :quantity" not in update["UpdateExpression"]:
            raise AssertionError(f"Unexpected update: {update['UpdateExpression']}")

        def apply_change():
            item["checkedInCount"] = int(item.get("checkedInCount", values[":zero"])) + int(values[":quantity"])
            item["updatedAt"] = values[":updatedAt"]
            if ":used" in values:
                item["status"] = values[":used"]

        return apply_change

    def _plan_put(self, put):
        assert_not_serialized(put["Item"], "Put.Item")
        assert_no_arithmetic(put.get("ConditionExpression"))
        if put["TableName"] != os.environ["ENTRY_CHECK_INS_TABLE"]:
            raise AssertionError(f"Unexpected put table: {put['TableName']}")
        return lambda: self.check_ins.append(dict(put["Item"]))


class FakeRegistrationsTable:
    def __init__(self, db):
        self.db = db

    def query(self, IndexName=None, KeyConditionExpression=None, **kwargs):
        token = KeyConditionExpression._values[1]
        items = [
            dict(item) for item in self.db.registrations.values()
            if item.get("registrationPassToken") == token
        ]
        return {"Items": items}

    def get_item(self, Key):
        item = self.db.registrations.get(Key["registrationId"])
        return {"Item": dict(item)} if item else {}

    def update_item(self, Key, UpdateExpression, ExpressionAttributeValues,
                    ConditionExpression=None, ExpressionAttributeNames=None):
        assert_no_arithmetic(ConditionExpression)
        item = self.db.registrations[Key["registrationId"]]
        if "SET #allocations = :allocations" not in UpdateExpression:
            raise AssertionError(f"Unsupported registration update: {UpdateExpression}")
        if ConditionExpression and item.get("allocations"):
            raise conditional_check_failed()
        item["allocations"] = ExpressionAttributeValues[":allocations"]
        return {}


class FakeAllocationsTable:
    def __init__(self, db):
        self.db = db

    def get_item(self, Key):
        item = self.db.allocations.get((Key["eventType"], Key["allocationId"]))
        return {"Item": dict(item)} if item else {}

    def put_item(self, Item, ConditionExpression=None):
        key = (Item["eventType"], Item["allocationId"])
        if ConditionExpression and key in self.db.allocations:
            raise conditional_check_failed("PutItem")
        self.db.allocations[key] = dict(Item)
        return {}

    def query(self, IndexName=None, KeyConditionExpression=None, **kwargs):
        token = KeyConditionExpression._values[1]
        items = [
            dict(item) for item in self.db.allocations.values()
            if item.get("publicToken") == token
        ]
        return {"Items": items}

    def update_item(self, Key, UpdateExpression, ExpressionAttributeValues,
                    ExpressionAttributeNames=None, ConditionExpression=None):
        assert_no_arithmetic(ConditionExpression)
        item = self.db.allocations[(Key["eventType"], Key["allocationId"])]
        item["status"] = ExpressionAttributeValues[":cancelled"]
        item["updatedAt"] = ExpressionAttributeValues[":now"]
        return {}


class EntryAllocationTestCase(unittest.TestCase):
    def setUp(self):
        self.db = FakeDynamoDB()
        self._real_resource = entry_allocations.dynamodb
        self._real_client = entry_allocations.dynamodb_client
        entry_allocations.dynamodb = self.db
        entry_allocations.dynamodb_client = self.db
        self.addCleanup(self._restore)

    def _restore(self):
        entry_allocations.dynamodb = self._real_resource
        entry_allocations.dynamodb_client = self._real_client

    # ── fixtures ───────────────────────────────────────────────────────────
    def add_allocation(self, registration_id, slot_id, seats, name="6:00 PM",
                       checked_in=0, status="ACTIVE"):
        item = entry_allocations.create_allocation_item(
            "onam", registration_id, {"slotId": slot_id, "seatCount": seats},
            {"name": name}, {"reservationId": f"res-{slot_id}"},
        )
        item["checkedInCount"] = checked_in
        item["status"] = status
        entry_allocations.persist_allocation(item)
        return item

    def add_registration(self, registration_id, token, allocations=(), payment_status="paid",
                         **extra):
        # Paid by default: check-in is only allowed once the money is in, so an
        # unpaid record is the exception a test has to ask for.
        record = {
            "registrationId": registration_id,
            "eventType": "onam",
            "registrationType": "onam",
            "registrationPassToken": token,
            "paymentStatus": payment_status,
            **extra,
        }
        if allocations:
            record["allocations"] = [entry_allocations.public_allocation(a) for a in allocations]
        self.db.registrations[registration_id] = record
        return record

    def stored(self, item):
        return self.db.allocations[("onam", item["allocationId"])]


class CheckInTests(EntryAllocationTestCase):
    def test_condition_expression_contains_no_arithmetic(self):
        # DynamoDB only allows arithmetic in an UpdateExpression.
        assert_no_arithmetic(entry_allocations._no_overflow_condition())

    def test_check_in_consumes_every_remaining_seat(self):
        first = self.add_allocation("reg-1", "slot-1", 3)
        second = self.add_allocation("reg-1", "slot-2", 2, name="7:00 PM")
        self.add_registration("reg-1", "tok-1", [first, second])

        summary = entry_allocations.check_in_registration_by_token("tok-1", "gate@example.com")

        self.assertEqual(summary["totalAllocated"], 5)
        self.assertEqual(summary["totalCheckedIn"], 5)
        self.assertEqual(summary["status"], "FULLY_USED")
        self.assertEqual(int(self.stored(first)["checkedInCount"]), 3)
        self.assertEqual(int(self.stored(second)["checkedInCount"]), 2)
        self.assertEqual(self.stored(first)["status"], "FULLY_USED")
        self.assertEqual(
            sorted(int(c["quantity"]) for c in self.db.check_ins), [2, 3]
        )
        self.assertEqual(
            {c["checkedInBy"] for c in self.db.check_ins}, {"gate@example.com"}
        )

    def test_transaction_values_are_plain_python(self):
        allocation = self.add_allocation("reg-1", "slot-1", 2)
        self.add_registration("reg-1", "tok-1", [allocation])

        entry_allocations.check_in_registration_by_token("tok-1", "gate@example.com")

        # The fake raises on serialized values; assert we actually exercised it.
        update = self.db.transactions[0][0]["Update"]
        self.assertEqual(update["Key"]["eventType"], "onam")
        self.assertEqual(update["ExpressionAttributeValues"][":quantity"], 2)

    def test_second_scan_is_rejected(self):
        allocation = self.add_allocation("reg-1", "slot-1", 2)
        self.add_registration("reg-1", "tok-1", [allocation])
        entry_allocations.check_in_registration_by_token("tok-1", "gate@example.com")

        with self.assertRaisesRegex(ValueError, "already been fully used"):
            entry_allocations.check_in_registration_by_token("tok-1", "gate@example.com")
        self.assertEqual(int(self.stored(allocation)["checkedInCount"]), 2)

    def test_fully_used_allocation_does_not_block_the_rest_of_the_party(self):
        remaining = self.add_allocation("reg-1", "slot-1", 4, checked_in=1)
        finished = self.add_allocation("reg-1", "slot-2", 2, checked_in=2, status="FULLY_USED")
        self.add_registration("reg-1", "tok-1", [remaining, finished])

        summary = entry_allocations.check_in_registration_by_token("tok-1", "gate@example.com")

        self.assertEqual(summary["totalCheckedIn"], 6)
        self.assertEqual(int(self.stored(remaining)["checkedInCount"]), 4)
        # The finished allocation is left alone: no second update, no 0-seat row.
        self.assertEqual([int(c["quantity"]) for c in self.db.check_ins], [3])

    def test_cancelled_pass_is_rejected(self):
        allocation = self.add_allocation("reg-1", "slot-1", 2, status="CANCELLED")
        self.add_registration("reg-1", "tok-1", [allocation])

        with self.assertRaisesRegex(ValueError, "cancelled"):
            entry_allocations.check_in_registration_by_token("tok-1", "gate@example.com")

    def test_concurrent_scan_cannot_double_count(self):
        allocation = self.add_allocation("reg-1", "slot-1", 4)
        self.add_registration("reg-1", "tok-1", [allocation])

        def other_device_scans_first():
            stored = self.stored(allocation)
            stored["checkedInCount"] = 4
            stored["status"] = "FULLY_USED"

        self.db.before_transaction = other_device_scans_first

        with self.assertRaisesRegex(ValueError, "already been fully used"):
            entry_allocations.check_in_registration_by_token("tok-1", "gate@example.com")
        self.assertEqual(int(self.stored(allocation)["checkedInCount"]), 4)
        self.assertEqual(self.db.check_ins, [])

    def test_check_in_refreshes_the_registration_snapshot(self):
        # The registrant's page reads the copy stored on the registration, so a
        # stale snapshot there reports a checked-in party as not arrived.
        allocation = self.add_allocation("reg-1", "slot-1", 3)
        self.add_registration("reg-1", "tok-1", [allocation])

        entry_allocations.check_in_registration_by_token("tok-1", "gate@example.com")

        snapshot = self.db.registrations["reg-1"]["allocations"]
        self.assertEqual([int(a["checkedInCount"]) for a in snapshot], [3])
        self.assertEqual([int(a["remaining"]) for a in snapshot], [0])
        self.assertEqual([a["status"] for a in snapshot], ["FULLY_USED"])

    def test_check_in_survives_a_failed_snapshot_refresh(self):
        # The counters are already committed; a snapshot write is best-effort.
        allocation = self.add_allocation("reg-1", "slot-1", 2)
        self.add_registration("reg-1", "tok-1", [allocation])


        def boom(self, **kwargs):
            raise ClientError(
                {"Error": {"Code": "ThrottlingException", "Message": "slow down"}}, "UpdateItem"
            )

        original, FakeRegistrationsTable.update_item = FakeRegistrationsTable.update_item, boom
        try:
            summary = entry_allocations.check_in_registration_by_token("tok-1", "gate@example.com")
        finally:
            FakeRegistrationsTable.update_item = original

        self.assertEqual(summary["totalCheckedIn"], 2)
        self.assertEqual(int(self.stored(allocation)["checkedInCount"]), 2)

    def test_unknown_token_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "Invalid entry pass"):
            entry_allocations.check_in_registration_by_token("nope", "gate@example.com")

    def test_registration_without_allocations_is_rejected(self):
        self.add_registration("reg-1", "tok-1")

        with self.assertRaisesRegex(ValueError, "no entry allocations"):
            entry_allocations.check_in_registration_by_token("tok-1", "gate@example.com")


class AdminCheckInByIdTests(EntryAllocationTestCase):
    """An admin can check in a registration directly by ID, without a pass
    token — e.g. the attendee lost their QR pass or it never arrived."""

    def test_check_in_by_id_consumes_every_remaining_seat(self):
        allocation = self.add_allocation("reg-1", "slot-1", 3)
        self.add_registration("reg-1", "tok-1", [allocation])

        summary = entry_allocations.check_in_registration_by_id("reg-1", "admin@example.com")

        self.assertEqual(summary["totalCheckedIn"], 3)
        self.assertEqual(summary["status"], "FULLY_USED")
        self.assertEqual(int(self.stored(allocation)["checkedInCount"]), 3)
        self.assertEqual({c["checkedInBy"] for c in self.db.check_ins}, {"admin@example.com"})

    def test_unknown_registration_id_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "Registration not found"):
            entry_allocations.check_in_registration_by_id("nope", "admin@example.com")

    def test_unpaid_registration_is_rejected(self):
        allocation = self.add_allocation("reg-1", "slot-1", 2)
        self.add_registration("reg-1", "tok-1", [allocation], payment_status="pending")

        with self.assertRaisesRegex(ValueError, "Payment is not complete"):
            entry_allocations.check_in_registration_by_id("reg-1", "admin@example.com")


class LegacyBackfillTests(EntryAllocationTestCase):
    def test_legacy_inline_slot_is_backfilled_and_checkable(self):
        self.add_registration(
            "reg-legacy", "tok-legacy",
            slotId="slot-5", slotName="6:30 PM", slotSeats=3, slotReservationId="res-9",
            attendeeCounts={"adults": 2, "kids914": 1, "kidsUnder6": 1},
        )

        resolved = entry_allocations.get_registration_by_pass_token("tok-legacy")

        self.assertEqual(len(resolved["allocations"]), 1)
        self.assertEqual(resolved["allocations"][0]["seatCount"], 3)
        self.assertEqual(resolved["allocations"][0]["slotName"], "6:30 PM")
        self.assertEqual(resolved["allocations"][0]["entrySlotId"], "slot-5")
        # Persisted, so the next scan reuses it rather than making another.
        self.assertTrue(self.db.registrations["reg-legacy"]["allocations"])
        self.assertEqual(len(self.db.allocations), 1)
        allocation = next(iter(self.db.allocations.values()))
        self.assertEqual(allocation["reservationId"], "res-9")
        self.assertEqual(int(allocation["checkedInCount"]), 0)

        summary = entry_allocations.check_in_registration_by_token("tok-legacy", "gate@example.com")
        self.assertEqual(summary["totalCheckedIn"], 3)

    def test_backfill_is_idempotent(self):
        self.add_registration("reg-legacy", "tok-legacy", slotId="slot-5", slotSeats=2)

        entry_allocations.get_registration_by_pass_token("tok-legacy")
        entry_allocations.get_registration_by_pass_token("tok-legacy")

        self.assertEqual(len(self.db.allocations), 1)

    def test_backfill_falls_back_to_attendee_counts(self):
        self.add_registration(
            "reg-legacy", "tok-legacy", slotId="slot-5",
            attendeeCounts={"adults": 2, "kids68": 1, "kidsUnder6": 2},
        )

        resolved = entry_allocations.get_registration_by_pass_token("tok-legacy")

        # Under-6s do not occupy a seat.
        self.assertEqual(resolved["allocations"][0]["seatCount"], 3)

    def test_concurrent_backfill_adopts_the_winner(self):
        winner = self.add_allocation("reg-legacy", "slot-5", 3)
        self.add_registration("reg-legacy", "tok-legacy", [winner])
        loser_view = {
            "registrationId": "reg-legacy", "eventType": "onam",
            "slotId": "slot-5", "slotSeats": 3,
        }

        adopted = entry_allocations.backfill_legacy_allocation(loser_view)

        self.assertEqual(
            [a["allocationId"] for a in adopted["allocations"]], [winner["allocationId"]]
        )
        orphans = [
            a for a in self.db.allocations.values()
            if a["allocationId"] != winner["allocationId"]
        ]
        self.assertTrue(orphans and all(a["status"] == "CANCELLED" for a in orphans))

    def test_registration_with_no_slot_is_untouched(self):
        registration = {"registrationId": "reg-1", "eventType": "onam"}

        self.assertIsNone(
            entry_allocations.backfill_legacy_allocation(registration).get("allocations")
        )
        self.assertEqual(self.db.allocations, {})


class PaymentGateTests(EntryAllocationTestCase):
    def test_unpaid_registration_cannot_check_in(self):
        allocation = self.add_allocation("reg-1", "slot-1", 2)
        self.add_registration("reg-1", "tok-1", [allocation], payment_status="pending")

        with self.assertRaisesRegex(ValueError, "Payment is not complete"):
            entry_allocations.check_in_registration_by_token("tok-1", "gate@example.com")

        # Nothing was written, so the pass still works once payment lands.
        self.assertEqual(int(self.stored(allocation)["checkedInCount"]), 0)
        self.assertEqual(self.db.transactions, [])
        self.assertEqual(self.db.check_ins, [])

    def test_refunded_registration_cannot_check_in(self):
        allocation = self.add_allocation("reg-1", "slot-1", 2)
        self.add_registration("reg-1", "tok-1", [allocation], payment_status="refunded")

        with self.assertRaisesRegex(ValueError, "Payment is not complete"):
            entry_allocations.check_in_registration_by_token("tok-1", "gate@example.com")

    def test_missing_payment_status_cannot_check_in(self):
        allocation = self.add_allocation("reg-1", "slot-1", 2)
        self.add_registration("reg-1", "tok-1", [allocation], payment_status="")

        with self.assertRaisesRegex(ValueError, "Payment is not complete"):
            entry_allocations.check_in_registration_by_token("tok-1", "gate@example.com")

    def test_paid_registration_checks_in(self):
        allocation = self.add_allocation("reg-1", "slot-1", 2)
        self.add_registration("reg-1", "tok-1", [allocation], payment_status="Paid")

        summary = entry_allocations.check_in_registration_by_token("tok-1", "gate@example.com")

        self.assertEqual(summary["totalCheckedIn"], 2)

    def test_payment_is_complete_only_for_paid(self):
        self.assertTrue(entry_allocations.payment_is_complete({"paymentStatus": " PAID "}))
        for status in ("pending", "refunded", "", None):
            self.assertFalse(entry_allocations.payment_is_complete({"paymentStatus": status}))
        self.assertFalse(entry_allocations.payment_is_complete({}))


class CheckedInSeatsTests(EntryAllocationTestCase):
    """The guard that freezes an entry slot once anybody has been admitted."""

    def test_zero_before_anyone_arrives(self):
        allocation = self.add_allocation("reg-1", "slot-1", 3)
        registration = self.add_registration("reg-1", "tok-1", [allocation])

        self.assertEqual(entry_allocations.checked_in_seats(registration), 0)

    def test_counts_live_rows_not_the_stale_snapshot(self):
        first = self.add_allocation("reg-1", "slot-1", 3)
        second = self.add_allocation("reg-1", "slot-2", 2, name="7:00 PM")
        registration = self.add_registration("reg-1", "tok-1", [first, second])
        # The snapshot on the record still says nobody has arrived.
        self.assertEqual(
            [int(a["checkedInCount"]) for a in registration["allocations"]], [0, 0]
        )
        self.stored(first)["checkedInCount"] = 2

        self.assertEqual(entry_allocations.checked_in_seats(registration), 2)

    def test_broken_map_counter_does_not_freeze_the_slot(self):
        # Pre-fix rows stored counters as maps; check-in rejects those itself
        # rather than silently blocking a slot change on unreadable data.
        allocation = self.add_allocation("reg-1", "slot-1", 3)
        registration = self.add_registration("reg-1", "tok-1", [allocation])
        self.stored(allocation)["checkedInCount"] = {"N": "0"}

        self.assertEqual(entry_allocations.checked_in_seats(registration), 0)

    def test_registration_without_allocations_has_nobody_checked_in(self):
        self.assertEqual(entry_allocations.checked_in_seats({"eventType": "onam"}), 0)


class RefreshAllocationsTests(EntryAllocationTestCase):
    def test_live_counters_replace_a_stale_snapshot(self):
        allocation = self.add_allocation("reg-1", "slot-1", 4)
        registration = self.add_registration("reg-1", "tok-1", [allocation])
        # Somebody else checked the party in against the source of truth.
        stored = self.stored(allocation)
        stored["checkedInCount"] = 4
        stored["status"] = "FULLY_USED"

        refreshed = entry_allocations.refresh_allocations(registration)["allocations"]

        self.assertEqual(int(refreshed[0]["checkedInCount"]), 4)
        self.assertEqual(int(refreshed[0]["remaining"]), 0)
        self.assertEqual(refreshed[0]["status"], "FULLY_USED")
        self.assertEqual(entry_allocations.summarize_status(refreshed), "FULLY_USED")

    def test_missing_allocation_row_keeps_the_snapshot(self):
        allocation = self.add_allocation("reg-1", "slot-1", 2)
        registration = self.add_registration("reg-1", "tok-1", [allocation])
        self.db.allocations.clear()

        refreshed = entry_allocations.refresh_allocations(registration)["allocations"]

        self.assertEqual(int(refreshed[0]["seatCount"]), 2)

    def test_registration_without_allocations_is_left_alone(self):
        registration = {"registrationId": "reg-1", "eventType": "onam"}

        self.assertEqual(entry_allocations.refresh_allocations(registration)["allocations"], [])


class SummarizeStatusTests(unittest.TestCase):
    def test_status_reflects_the_allocations(self):
        active = {"seatCount": 3, "checkedInCount": 1, "status": "ACTIVE"}
        used = {"seatCount": 3, "checkedInCount": 3, "status": "FULLY_USED"}
        cancelled = {"seatCount": 3, "checkedInCount": 0, "status": "CANCELLED"}

        self.assertEqual(entry_allocations.summarize_status([active]), "ACTIVE")
        self.assertEqual(entry_allocations.summarize_status([used]), "FULLY_USED")
        self.assertEqual(entry_allocations.summarize_status([used, active]), "ACTIVE")
        self.assertEqual(entry_allocations.summarize_status([used, cancelled]), "CANCELLED")
        self.assertEqual(entry_allocations.summarize_status([]), "ACTIVE")


if __name__ == "__main__":
    unittest.main()
