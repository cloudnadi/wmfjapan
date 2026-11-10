"""Admin AGM list / create / edit-only-while-active / registrations."""

import importlib.util
import json
import os
import sys
import unittest
from pathlib import Path
from unittest import mock

from botocore.exceptions import ClientError

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lambdas"))  # agm_logic
sys.path.insert(0, str(ROOT / "lambdas/manage_agms"))  # npo_membership
os.environ.update(REGION="ap-northeast-1", AGMS_TABLE="agms", AGM_TABLE="agm", MEMBERS_TABLE="members", MEMBERSHIP_TABLE="npo")

spec = importlib.util.spec_from_file_location("manage_agms_handler", ROOT / "lambdas/manage_agms/handler.py")
handler = importlib.util.module_from_spec(spec)
spec.loader.exec_module(handler)


class FakeTable:
    def __init__(self, keys):
        self.keys, self.items = keys, {}

    def _key(self, item):
        return tuple(item[k] for k in self.keys)

    def get_item(self, Key):
        item = self.items.get(self._key(Key))
        return {"Item": dict(item)} if item else {}

    def put_item(self, Item, ConditionExpression=None):
        if ConditionExpression and self._key(Item) in self.items:
            raise ClientError({"Error": {"Code": "ConditionalCheckFailedException"}}, "PutItem")
        self.items[self._key(Item)] = dict(Item)

    def scan(self, **_):
        return {"Items": [dict(i) for i in self.items.values()]}

    def query(self, KeyConditionExpression=None, **_):
        wanted = KeyConditionExpression.get_expression()["values"][1]
        return {"Items": [dict(i) for k, i in self.items.items() if k[0] == wanted]}


class ManageAgmsTests(unittest.TestCase):
    def setUp(self):
        self.tables = {
            "agms": FakeTable(("agmId",)), "agm": FakeTable(("agmId", "email")),
            "members": FakeTable(("email",)), "npo": FakeTable(("memberid", "transactionNo")),
        }
        res = mock.Mock()
        res.Table = lambda name: self.tables[name]
        patcher = mock.patch.object(handler, "dynamodb", res)
        patcher.start()
        self.addCleanup(patcher.stop)

    def call(self, method, agm_id=None, body=None):
        event = {"httpMethod": method, "pathParameters": {"agmId": agm_id} if agm_id else None, "body": json.dumps(body or {})}
        resp = handler.lambda_handler(event, None)
        return resp["statusCode"], json.loads(resp["body"])

    def register(self, agm_id, email, adults, attendance="in_person", **extra):
        self.tables["agm"].items[(agm_id, email)] = {
            "agmId": agm_id, "email": email, "adults": adults, "teens": 0, "kids": 0, "preschoolers": 0,
            "toddlers": 0, "attendance": attendance, "totalAttendees": adults,
            "registeredAt": "2026-10-01", **extra,
        }

    def test_create_generates_ids_per_year_and_rejects_bad_input(self):
        base = {"title": "AGM", "date": "2099-11-15"}
        self.assertEqual(self.call("POST", body=base)[1]["agm"]["agmId"], "agm-2099")
        self.assertEqual(self.call("POST", body=base)[1]["agm"]["agmId"], "agm-2099-2")
        self.assertEqual(self.call("POST", body={**base, "date": "2001-01-01"})[0], 400)
        self.assertEqual(self.call("POST", body={**base, "title": ""})[0], 400)
        self.assertEqual(self.call("POST", body={**base, "startTime": "25:00"})[0], 400)
        self.assertEqual(self.call("POST", body={**base, "mapUrl": "javascript:x"})[0], 400)

    def test_only_active_agms_can_be_edited(self):
        self.tables["agms"].items[("agm-2020",)] = {"agmId": "agm-2020", "title": "Old", "date": "2020-11-15"}
        self.tables["agms"].items[("agm-2099",)] = {"agmId": "agm-2099", "title": "New", "date": "2099-11-15"}
        status, body = self.call("PUT", "agm-2020", {"title": "Edited", "date": "2020-11-15"})
        self.assertEqual(status, 409)
        self.assertEqual(self.tables["agms"].items[("agm-2020",)]["title"], "Old")
        status, body = self.call("PUT", "agm-2099", {"title": "Edited", "date": "2099-12-01", "enabled": True})
        self.assertEqual((status, body["agm"]["title"], body["agm"]["agmId"]), (200, "Edited", "agm-2099"))
        self.assertEqual(self.call("PUT", "nope", {"title": "x", "date": "2099-01-01"})[0], 404)
        self.assertEqual(self.call("PUT", "agm-2099", {"title": "x", "date": "2001-01-01"})[0], 400)

    def test_list_and_registrations_cover_past_and_new(self):
        self.tables["agms"].items[("agm-2020",)] = {"agmId": "agm-2020", "title": "Old", "date": "2020-11-15"}
        self.tables["agms"].items[("agm-2099",)] = {"agmId": "agm-2099", "title": "New", "date": "2099-11-15"}
        self.register("agm-2020", "a@x.test", 2)
        self.register("agm-2099", "b@x.test", 1)
        self.register("agm-2099", "c@x.test", 3, "remote")
        agms = self.call("GET")[1]["agms"]
        self.assertEqual([a["agmId"] for a in agms], ["agm-2099", "agm-2020"])  # newest first
        self.assertEqual([(a["active"], a["registrationCount"], a["peopleCount"]) for a in agms], [(True, 2, 4), (False, 1, 2)])
        old = self.call("GET", "agm-2020")[1]  # past AGM's records are still viewable
        self.assertEqual((old["agm"]["active"], old["totals"]["people"], len(old["registrations"])), (False, 2, 1))
        new = self.call("GET", "agm-2099")[1]
        self.assertEqual((new["totals"]["inPerson"], new["totals"]["remote"]), (1, 3))
        self.assertEqual(self.call("GET", "nope")[0], 404)

    def test_adult_attendees_show_their_current_npo_status_and_totals_cover_every_age_group(self):
        self.tables["agms"].items[("agm-2099",)] = {"agmId": "agm-2099", "title": "New", "date": "2099-11-15"}
        for email, memberid in (("paid@x.test", "1"), ("lapsed@x.test", "2"), ("late@x.test", "3")):
            self.tables["members"].items[(email,)] = {"email": email, "memberid": memberid}
        self.tables["npo"].items[("1", "1")] = {"memberid": "1", "transactionNo": "1",
                                                "membDateFrom": "2099-11-01", "membDateTo": "2100-10-31"}
        self.tables["npo"].items[("3", "1")] = {"memberid": "3", "transactionNo": "1", "paymentStatus": "pending",
                                                "membershipYear": "2099-00"}
        # registered while "pending" — paid since; and two who never became members
        self.register("agm-2099", "paid@x.test", 3, teens=1, toddlers=1, totalAttendees=5, adultAttendees=[
            {"email": "paid@x.test", "name": "Pat", "status": "pending", "registrant": True},
            {"email": "lapsed@x.test", "name": "Lee", "status": "member", "registrant": False},
            {"email": "late@x.test", "name": "Lou", "status": "pending", "registrant": False},
        ])
        body = self.call("GET", "agm-2099")[1]
        statuses = {a["email"]: (a["registeredStatus"], a["status"]) for a in body["registrations"][0]["adultAttendees"]}
        self.assertEqual(statuses, {"paid@x.test": ("pending", "member"), "lapsed@x.test": ("member", "not_member"),
                                    "late@x.test": ("pending", "pending")})
        self.assertEqual(body["needsAttention"], 1)  # only the one who isn't a member (or about to be)
        self.assertEqual((body["totals"]["adults"], body["totals"]["teens"], body["totals"]["toddlers"], body["totals"]["people"]),
                         (3, 1, 1, 5))


if __name__ == "__main__":
    unittest.main()
