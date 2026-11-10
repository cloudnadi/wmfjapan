"""Legacy NPO transaction import: legacy ids are kept in their own attribute, never used as the key."""

import copy
import importlib.util
import unittest
from decimal import Decimal
from pathlib import Path
from unittest import mock

spec = importlib.util.spec_from_file_location(
    "import_npo_membership", Path(__file__).resolve().parents[1] / "scripts/import_npo_membership.py"
)
imp = importlib.util.module_from_spec(spec)
spec.loader.exec_module(imp)


def row(memberid="7", legacy="1", **kw):
    base = {"memberid": memberid, "transaction_no": legacy, "membership_amount_paid": "1000",
            "paid_date": "2025-01-05", "memb_datefrom": "2024-11-01", "memb_dateto": "2025-10-31", "paidvia": "bank"}
    base.update(kw)
    return imp.build_item(base)


class BuildItemTests(unittest.TestCase):
    def test_keeps_the_legacy_number_in_its_own_attribute(self):
        item = row(legacy="42")
        self.assertEqual(item["legacyTransactionId"], "42")
        self.assertNotIn("transactionNo", item)
        self.assertEqual(item["membershipAmountPaid"], Decimal(1000))

    def test_alternative_column_names_and_missing_keys(self):
        self.assertEqual(imp.build_item({"memberid": "1", "legacy_transaction_id": "9"})["legacyTransactionId"], "9")
        self.assertEqual(imp.build_item({"memberid": "1", "transaction_id": "8"})["legacyTransactionId"], "8")
        self.assertIsNone(imp.build_item({"memberid": "", "transaction_no": "1"}))
        self.assertIsNone(imp.build_item({"memberid": "1", "transaction_no": ""}))


class PlanTests(unittest.TestCase):
    def test_new_rows_get_fresh_numbers_not_the_legacy_ones(self):
        actions = imp.plan_import([row(legacy="100"), row(legacy="205")], {})
        self.assertEqual([(a["action"], a["key"], a["item"]["legacyTransactionId"]) for a in actions],
                         [("insert", ("7", "1"), "100"), ("insert", ("7", "2"), "205")])
        self.assertTrue(all(a["item"]["source"] == "legacy-import" for a in actions))

    def test_never_overwrites_a_site_transaction_with_the_same_number(self):
        site = {"memberid": "7", "transactionNo": "1", "source": "my-page", "paymentStatus": "paid",
                "membDateFrom": "2026-11-01", "membDateTo": "2027-10-31", "membershipAmountPaid": 1000}
        existing = {"7": [copy.deepcopy(site)]}
        (action,) = imp.plan_import([row(legacy="1")], existing)  # legacy "1" collides with site "1"
        self.assertEqual((action["action"], action["key"]), ("insert", ("7", "2")))
        self.assertEqual(existing["7"][0]["source"], "my-page")  # untouched

    def test_rerunning_the_same_file_creates_no_duplicates(self):
        existing = {}
        first = imp.plan_import([row(legacy="1"), row(legacy="2")], existing)
        stored = {"7": [dict(a["item"]) for a in first]}  # what the table now holds
        again = imp.plan_import([row(legacy="1"), row(legacy="2"), row(legacy="3")], stored)
        self.assertEqual([(a["action"], a["key"]) for a in again],
                         [("update", ("7", "1")), ("update", ("7", "2")), ("insert", ("7", "3"))])

    def test_a_new_file_reusing_old_numbers_for_different_transactions_is_added_not_merged(self):
        stored = {"7": [{"memberid": "7", "transactionNo": "1", "legacyTransactionId": "500", "source": "legacy-import"}]}
        (action,) = imp.plan_import([row(legacy="1")], stored)  # legacy "1" != stored legacy "500"
        self.assertEqual((action["action"], action["key"]), ("insert", ("7", "2")))

    def test_adopts_an_older_import_only_when_it_is_clearly_the_same_record(self):
        older = {"memberid": "7", "transactionNo": "5", "membershipAmountPaid": Decimal(1000),
                 "membDateFrom": "2024-11-01", "membDateTo": "2025-10-31"}
        # missing paid date / method on the stored record is a difference too
        (action,) = imp.plan_import([row(legacy="5")], {"7": [dict(older)]})
        self.assertEqual(action["action"], "insert")
        # an older import stored the same fields as strings, exactly as the CSV had them
        older.update(paidDate="2025-01-05", paidVia="bank")
        (action,) = imp.plan_import([row(legacy="5")], {"7": [dict(older)]})
        self.assertEqual((action["action"], action["key"]), ("adopt", ("7", "5")))
        # any difference — dates, paid date, method or amount — means a different transaction
        for field, value in (("membDateTo", "2026-10-31"), ("paidDate", "2025-02-01"),
                             ("paidVia", "paypay"), ("membershipAmountPaid", Decimal(2000))):
            (action,) = imp.plan_import([row(legacy="5")], {"7": [dict(older, **{field: value})]})
            self.assertEqual((action["action"], action["key"]), ("insert", ("7", "6")), field)

    def test_numbering_is_per_member(self):
        actions = imp.plan_import([row(memberid="7", legacy="9"), row(memberid="8", legacy="9")], {})
        self.assertEqual([a["key"] for a in actions], [("7", "1"), ("8", "1")])


class ApplyTests(unittest.TestCase):
    def test_insert_refuses_to_replace_and_update_only_sets_imported_attributes(self):
        table = mock.Mock()
        item = {"memberid": "7", "transactionNo": "2", "legacyTransactionId": "1", "source": "legacy-import",
                "membershipAmountPaid": Decimal(1000)}
        imp.apply_action(table, {"action": "insert", "key": ("7", "2"), "item": item}, "now")
        kwargs = table.put_item.call_args.kwargs
        self.assertIn("attribute_not_exists(memberid)", kwargs["ConditionExpression"])
        imp.apply_action(table, {"action": "update", "key": ("7", "2"), "item": item}, "now")
        kwargs = table.update_item.call_args.kwargs
        self.assertEqual(kwargs["Key"], {"memberid": "7", "transactionNo": "2"})
        self.assertNotIn("source", kwargs["ExpressionAttributeNames"].values())
        self.assertEqual(kwargs["ConditionExpression"], "attribute_exists(memberid)")


if __name__ == "__main__":
    unittest.main()
