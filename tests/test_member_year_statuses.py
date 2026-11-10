"""Admin Members list: per-year NPO status labels (New / Returning / Renewed / Non-member)."""

import importlib.util
import os
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
os.environ.update(REGION="ap-northeast-1", DYNAMODB_TABLE="regs")

# The handler pulls in the seat / dinner plugins and mailers, which this test doesn't need.
_STUBS = ["onam_pricing", "emailer", "entry_slots_logic", "entry_allocations", "seat_selection_logic",
          "seat_purchase_logic", "seat_purchase_emailer", "qr_email", "celebrity_dinner_logic", "celebrity_dinner_emailer"]
sys.path.insert(0, str(ROOT / "lambdas/manage_registrations"))
with mock.patch.dict(sys.modules, {name: mock.MagicMock() for name in _STUBS}):
    spec = importlib.util.spec_from_file_location("manage_registrations_handler", ROOT / "lambdas/manage_registrations/handler.py")
    handler = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(handler)
sys.path.remove(str(ROOT / "lambdas/manage_registrations"))  # don't shadow other tests' modules


def txn(start, end):
    return {"membDateFrom": start, "membDateTo": end}


Y2223 = txn("2022-11-01", "2023-10-31")
Y2324 = txn("2023-11-01", "2024-10-31")
Y2425 = txn("2024-11-01", "2025-10-31")
Y2526 = txn("2025-11-01", "2026-10-31")
Y2627 = txn("2026-11-01", "2027-10-31")


class YearStatusTests(unittest.TestCase):
    def status(self, *txns):
        return handler._year_statuses(list(txns))

    def test_a_first_time_member_is_new(self):
        self.assertEqual(self.status(Y2627)["npoStatus2026"], "New Member")

    def test_renewed_when_also_covered_last_year(self):
        s = self.status(Y2526, Y2627)
        self.assertEqual((s["npoStatus2025"], s["npoStatus2026"]), ("Member", "Renewed"))

    def test_a_member_who_skipped_2025_26_is_returning_not_new(self):
        s = self.status(Y2223, Y2324, Y2627)
        self.assertEqual((s["npoStatus2025"], s["npoStatus2026"]), ("Non-member", "Returning Member"))

    def test_a_member_of_2024_25_who_skipped_a_year_is_returning(self):
        self.assertEqual(self.status(Y2425, Y2627)["npoStatus2026"], "Returning Member")

    def test_not_covered_for_2026_27_is_a_non_member_whatever_the_history(self):
        self.assertEqual(self.status(Y2223, Y2324)["npoStatus2026"], "Non-member")
        self.assertEqual(self.status()["npoStatus2026"], "Non-member")

    def test_pending_and_cancelled_records_have_no_dates_so_they_do_not_count_as_history(self):
        pending = {"paymentStatus": "pending", "membershipYear": "2026-27"}
        cancelled = {"paymentStatus": "cancelled", "cancelledMembDateFrom": "2023-11-01", "cancelledMembDateTo": "2024-10-31"}
        self.assertEqual(self.status(pending, cancelled, Y2627)["npoStatus2026"], "New Member")


class ListMembersTests(unittest.TestCase):
    def test_the_list_carries_each_members_whatsapp_group_status(self):
        members = [
            {"email": "a@x.test", "memberid": "1", "fullname": "Ann", "whatsappGroup": {"2026-27": "joined"}},
            {"email": "b@x.test", "memberid": "2", "fullname": "Bo"},
            {"email": "c@x.test", "memberid": "3", "fullname": "Cy", "whatsappGroup": "garbage"},
        ]
        tables = {"members": members, "npo": []}
        resource = mock.Mock()
        resource.Table = lambda name: name
        with mock.patch.object(handler, "MEMBERS_TABLE", "members"), mock.patch.object(handler, "MEMBERSHIP_TABLE", "npo"), \
                mock.patch.object(handler, "dynamodb", resource), mock.patch.object(handler, "scan_all", lambda table: tables[table]):
            listed = {m["email"]: m for m in handler.list_members()}
        self.assertEqual(listed["a@x.test"]["whatsappGroup"], {"2026-27": "joined"})
        self.assertEqual(listed["b@x.test"]["whatsappGroup"], {})
        self.assertEqual(listed["c@x.test"]["whatsappGroup"], {})


if __name__ == "__main__":
    unittest.main()
