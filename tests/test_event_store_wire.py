"""The event stores' DynamoDB transactions must be valid *low-level* API calls (pre-serialised values).
Uses botocore's request validation, which the in-memory fakes used elsewhere cannot catch."""

import importlib.util
import os
import sys
import unittest
from pathlib import Path

from botocore.stub import Stubber

ROOT = Path(__file__).resolve().parents[1]
for sub in ("lambdas", "lambdas/manage_event_platform"):
    sys.path.insert(0, str(ROOT / sub))
os.environ.setdefault("REGION", "ap-northeast-1")
os.environ.setdefault("AWS_ACCESS_KEY_ID", "x")
os.environ.setdefault("AWS_SECRET_ACCESS_KEY", "x")
os.environ["EVENTS_V2_TABLE"] = "events"
os.environ["EVENT_REGISTRATIONS_TABLE"] = "regs"
os.environ["EVENT_COUNTERS_TABLE"] = "counters"

import event_model  # noqa: E402


def load(name, rel):
    spec = importlib.util.spec_from_file_location(name, ROOT / rel)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


admin_store = load("wire_admin_store", "lambdas/manage_event_platform/store.py")
reg_store = load("wire_reg_store", "lambdas/event_reg_store.py")


def sent_params(client, fn):
    """Run fn under a stubbed client and return the TransactItems exactly as they reach request building."""
    seen = []
    client.meta.events.register("before-parameter-build.dynamodb.TransactWriteItems", lambda params, **kw: seen.append(params["TransactItems"]))
    with Stubber(client) as stub:
        stub.add_response("transact_write_items", {})
        fn()
    return seen[0]


class WireTests(unittest.TestCase):
    def test_keys_reach_dynamodb_as_plain_typed_values_not_double_serialised(self):
        item = event_model.new_event("evt1", {"title": "Picnic", "slug": "picnic"})
        ops = sent_params(admin_store.client, lambda: admin_store.save_event(item, expected_revision=None))
        self.assertEqual(ops[0]["Put"]["Item"]["eventId"], {"S": "evt1"})
        self.assertEqual(ops[1]["Put"]["Item"]["eventId"], {"S": "SLUG#picnic"})

    def test_creating_and_renaming_an_event_sends_valid_transactions(self):
        item = event_model.new_event("evt1", {"title": "Picnic", "slug": "picnic"}, created_by="a@b.c")
        with Stubber(admin_store.client) as stub:
            stub.add_response("transact_write_items", {})
            admin_store.save_event(item, expected_revision=None)
            stub.add_response("transact_write_items", {})
            admin_store.save_event({**item, "slug": "picnic-2"}, expected_revision=1, old_slug="picnic")
            stub.assert_no_pending_responses()

    def test_deleting_an_event(self):
        item = event_model.new_event("evt1", {"title": "Picnic", "slug": "picnic"})
        with Stubber(admin_store.client) as stub:
            stub.add_response("transact_write_items", {})
            admin_store.delete_event(item)
            stub.assert_no_pending_responses()

    def test_registration_update_goes_through_the_resource_client(self):
        """update_registration mixes pre-serialised values with the resource API; it must run end to end."""
        with Stubber(reg_store.dynamodb.meta.client) as stub:
            stub.add_response("update_item", {"Attributes": {"registrationId": {"S": "r1"}, "status": {"S": "confirmed"}}})
            out = reg_store.update_registration(
                "r1", {"paymentStatus": "paid", "paidAt": "2026-11-09T10:00:00+09:00", "status": "confirmed", "paymentReference": ""},
                expect_status="pending_payment", expect_revision=2, bump=True, remove=["holdUntil"])
            stub.assert_no_pending_responses()
        self.assertEqual(out["status"], "confirmed")

    def test_counter_and_registration_transactions(self):
        ops = [reg_store._counter_update("EVENT#e1", 2, 10), reg_store._counter_update("EVENT#e1", 1, None, release=True)]
        with Stubber(reg_store.client) as stub:
            stub.add_response("transact_write_items", {})
            reg_store._transact(ops, counters=[("EVENT#e1", 2, 10)], first="update")
            stub.assert_no_pending_responses()


if __name__ == "__main__":
    unittest.main()
