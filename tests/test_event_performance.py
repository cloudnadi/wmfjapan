"""Registration management at 5,000 registrations: generous bounds that catch accidental O(n^2) code."""

import os
import sys
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for sub in ("lambdas", "tests"):
    sys.path.insert(0, str(ROOT / sub))
os.environ.setdefault("REGION", "ap-northeast-1")

import event_exports  # noqa: E402
import event_registration_admin as radm  # noqa: E402

N = 5000


def make_regs():
    regs = []
    for i in range(N):
        regs.append({
            "registrationId": f"WMF-{i:04d}", "eventId": "e1", "source": "native", "fullName": f"Person {i}", "email": f"p{i}@example.com",
            "status": ("confirmed", "pending_payment", "waitlisted", "cancelled")[i % 4], "paymentStatus": ("paid", "pending", "none", "refunded")[i % 4],
            "totalJpy": 1500 * (1 + i % 3), "tickets": [{"ticketTypeId": "adult", "name": "Adult", "qty": 1 + i % 3, "unitPriceJpy": 1500, "subtotalJpy": 1500 * (1 + i % 3)}],
            "createdAt": f"2026-09-{1 + i % 28:02d}T10:{i % 60:02d}:00+09:00", "answers": {"fullName": f"Person {i}", "email": f"p{i}@example.com", "note": "x" * 50},
            "revision": 1, "formVersion": 1, "checkInToken": f"tok{i}"})
    return regs


class PerformanceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.regs = make_regs()

    def timed(self, label, fn, limit=3.0):
        start = time.perf_counter()
        out = fn()
        elapsed = time.perf_counter() - start
        self.assertLess(elapsed, limit, f"{label} took {elapsed:.2f}s")
        return out

    def test_table_query_search_and_sort(self):
        out = self.timed("query", lambda: radm.query_registrations(self.regs, {"q": "person 49", "sort": "createdAt", "dir": "desc", "page": 1, "pageSize": 100}))
        self.assertGreater(out["total"], 0)
        self.assertLessEqual(len(out["rows"]), 100)
        out = self.timed("sort by name", lambda: radm.query_registrations(self.regs, {"sort": "fullName", "dir": "asc", "page": 3, "pageSize": 100}))
        self.assertEqual(out["total"], N)

    def test_csv_export(self):
        tickets = [{"ticketTypeId": "adult", "name": "Adult"}]
        columns = event_exports.ticket_columns(tickets)
        data = self.timed("csv", lambda: event_exports.to_csv(event_exports.export_table(self.regs, columns, tickets)), limit=5.0)
        self.assertGreater(len(data), N)


if __name__ == "__main__":
    unittest.main()
