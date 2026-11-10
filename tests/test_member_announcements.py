"""Member announcements: audiences, email content and the background send."""

import importlib
import json
import os
import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lambdas"))  # npo_payment_logic (used by manage_members)
sys.path.insert(0, str(ROOT / "lambdas/member_announcements"))

os.environ.update(
    REGION="ap-northeast-1", MEMBERS_TABLE="members", MEMBERSHIP_TABLE="npo", AGMS_TABLE="agms",
    MEMBER_ANNOUNCEMENTS_TABLE="campaigns", SITE_CONFIG_TABLE="site", SES_FROM_EMAIL="from@x.test",
    FRONTEND_URL="https://example.test", RENEWAL_MEMBERSHIP_YEAR="2026-27", CURRENT_MEMBERSHIP_YEAR="2025-26",
)

import announcement_logic as logic  # noqa: E402
import member_emailer as emailer  # noqa: E402

import importlib.util  # noqa: E402


def load(name, relative):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


worker = load("member_announcer_handler", "lambdas/member_announcements/handler.py")
admin = load("manage_members_handler", "lambdas/manage_members/handler.py")


class FakeTable:
    def __init__(self, key):
        self.key, self.items = key, {}

    def put_item(self, Item):
        self.items[Item[self.key]] = dict(Item)

    def get_item(self, Key):
        item = self.items.get(Key[self.key])
        return {"Item": dict(item)} if item else {}

    def scan(self, **_):
        return {"Items": [dict(i) for i in self.items.values()]}

    def update_item(self, Key, ExpressionAttributeValues, **_):
        self.items[Key[self.key]].update({k[1:]: v for k, v in ExpressionAttributeValues.items()})


def period(start, end, **extra):
    return {"membDateFrom": start, "membDateTo": end, **extra}


class AnnouncementTests(unittest.TestCase):
    def setUp(self):
        self.tables = {
            "members": FakeTable("email"), "npo": FakeTable("tx"), "agms": FakeTable("agmId"),
            "campaigns": FakeTable("campaignId"), "site": FakeTable("configKey"),
        }
        res = mock.Mock()
        res.Table = lambda name: self.tables[name]
        patch = mock.patch.object(logic, "dynamodb", res)
        patch.start()
        self.addCleanup(patch.stop)
        self.txn_count = 0

    def member(self, email, memberid, name="", *txns):
        self.tables["members"].items[email] = {"email": email, "memberid": memberid, "fullname": name}
        for txn in txns:
            self.txn_count += 1
            self.tables["npo"].items[f"t{self.txn_count}"] = {"tx": f"t{self.txn_count}", "memberid": memberid, **txn}

    def emails(self, kind):
        return sorted(r["email"] for r in logic.audience(kind))

    def seed(self):
        this_year = period("2025-11-01", "2026-10-31")
        next_year = period("2026-11-01", "2027-10-31")
        self.member("renewed@x.test", "1", "Renewed", this_year, next_year)
        self.member("due@x.test", "2", "Due", this_year)
        self.member("pending@x.test", "3", "Pending", this_year, {"paymentStatus": "pending", "membershipYear": "2026-27"})
        self.member("lapsed@x.test", "4", "Lapsed", period("2022-11-01", "2023-10-31"))
        self.member("never@x.test", "5", "Never")
        self.member("new@x.test", "6", "New", next_year)
        self.member("BAD EMAIL", "7")
        self.member("noname@x.test", "8")

    def test_agm_and_join_go_to_every_member_with_a_valid_email(self):
        self.seed()
        expected = ["due@x.test", "lapsed@x.test", "never@x.test", "new@x.test", "noname@x.test",
                    "pending@x.test", "renewed@x.test"]
        self.assertEqual(self.emails("agm"), expected)
        self.assertEqual(self.emails("join"), expected)

    def test_renew_goes_only_to_current_members_not_yet_renewed(self):
        self.seed()
        # covered 2025-26, not 2026-27, nothing awaiting payment
        self.assertEqual(self.emails("renew"), ["due@x.test"])
        due = logic.audience("renew")[0]
        self.assertEqual(due["validUntil"], "2026-10-31")

    def test_whatsapp_goes_to_members_covered_for_the_renewal_year(self):
        self.seed()
        # renewed + brand-new members; a pending renewal or an expired/never member is not covered yet
        self.assertEqual(self.emails("whatsapp"), ["new@x.test", "renewed@x.test"])

    def set_link(self, url="https://chat.whatsapp.com/ABC123"):
        self.tables["site"].items["npo_membership_config"] = {
            "configKey": "npo_membership_config",
            "value": json.dumps({"fee": 1000, "whatsappLinks": {"2026-27": url, "2025-26": "https://chat.whatsapp.com/OLD"}}),
        }

    def test_whatsapp_link_defaults_for_2026_27_and_a_saved_link_overrides_it(self):
        self.assertEqual(logic.whatsapp_link(), "https://chat.whatsapp.com/JLYiFD4voZaLx5RRv6oLGO?s=cl&p=i&mlu=4&ilr=4&iam=2")
        self.assertEqual(logic.whatsapp_link("2025-26"), "")
        self.set_link()
        self.assertEqual(logic.whatsapp_link(), "https://chat.whatsapp.com/ABC123")
        self.assertEqual(logic.whatsapp_link("2025-26"), "https://chat.whatsapp.com/OLD")
        self.assertEqual(logic.whatsapp_link("2030-31"), "")

    def test_whatsapp_email_has_the_link_and_the_ignore_note(self):
        subject, html = emailer.build_whatsapp({"name": "Ann"}, "2026-27", "https://chat.whatsapp.com/ABC123")
        self.assertIn("WhatsApp", subject)
        for text in ("Dear Ann", "https://chat.whatsapp.com/ABC123", "Join the WhatsApp Group", "Members only",
                     "Already joined the group? Please ignore this email"):
            self.assertIn(text, html)
        self.assertNotIn("Join the WhatsApp Group", emailer.build_whatsapp({"name": "Ann"}, "2026-27", "")[1])
        self.assertNotIn("javascript", emailer.build_whatsapp({"name": "A"}, "2026-27", "javascript:alert(1)")[1])

    def test_worker_sends_the_whatsapp_link_to_covered_members(self):
        self.seed()
        self.set_link()
        sent = []
        campaign = logic.create_campaign("whatsapp", 0)
        with mock.patch.object(worker.emailer, "_send", lambda to, subject, html: sent.append((to[0], html)) or True), \
                mock.patch.object(worker.time, "sleep"):
            result = worker.lambda_handler({"campaignId": campaign["campaignId"], "type": "whatsapp"}, None)
        self.assertEqual(result["sent"], 2)
        self.assertEqual(sorted(to for to, _ in sent), ["new@x.test", "renewed@x.test"])
        self.assertTrue(all("https://chat.whatsapp.com/ABC123" in html for _, html in sent))

    def test_worker_fails_the_whatsapp_send_when_no_link_is_set(self):
        self.seed()
        campaign = logic.create_campaign("whatsapp", 0)
        with mock.patch.dict(logic.DEFAULT_WHATSAPP_LINKS, clear=True), mock.patch.object(worker.time, "sleep"), \
                self.assertRaises(RuntimeError):
            worker.lambda_handler({"campaignId": campaign["campaignId"], "type": "whatsapp"}, None)
        self.assertIn("WhatsApp", self.tables["campaigns"].items[campaign["campaignId"]]["error"])

    def test_unknown_type_is_rejected(self):
        with self.assertRaises(ValueError):
            logic.audience("everyone")

    def test_announceable_agm_is_the_soonest_upcoming_one(self):
        self.assertIsNone(logic.announceable_agm())
        for agm_id, date in (("old", "2020-01-01"), ("b", "2099-12-01"), ("a", "2099-06-01")):
            self.tables["agms"].items[agm_id] = {"agmId": agm_id, "date": date}
        self.assertEqual(logic.announceable_agm()["agmId"], "a")

    def test_a_campaign_in_progress_blocks_a_duplicate_send(self):
        campaign = logic.create_campaign("join", 10, "admin@x.test")
        self.assertEqual(logic.active_campaign("join")["campaignId"], campaign["campaignId"])
        self.assertIsNone(logic.active_campaign("renew"))
        logic.update_campaign(campaign["campaignId"], status="done")
        self.assertIsNone(logic.active_campaign("join"))
        stuck = logic.create_campaign("agm", 1)
        old = (datetime.now(timezone.utc) - timedelta(minutes=45)).isoformat()
        self.tables["campaigns"].items[stuck["campaignId"]]["createdAt"] = old
        self.assertIsNone(logic.active_campaign("agm"))

    def test_emails_follow_the_template_and_carry_the_ignore_note(self):
        recipient = {"email": "a@x.test", "name": "Ann <b>", "validUntil": "2026-10-31"}
        for subject, html in (emailer.build_join(recipient, "2026-27", 1000), emailer.build_renew(recipient, "2026-27", 1000)):
            self.assertIn("already registered or renewed? Please ignore this email".lower(), html.lower())
            self.assertIn("https://example.test/member/membership", html)
            self.assertIn("Dear Ann &lt;b&gt;,", html)  # names are escaped
            self.assertIn("2026–27", subject + html)
            self.assertIn("¥1,000", html)
            self.assertIn("Host Your Event With Us", html)  # standard footer
        renew_subject, renew_html = emailer.build_renew(recipient, "2026-27", 0)
        self.assertIn("31 October 2026", renew_html)
        self.assertNotIn("¥", renew_html)

    def test_agm_email_shows_details_and_only_offers_registration_while_open(self):
        agm = {"title": "WMF Japan AGM 2026", "date": "2026-11-15", "startTime": "18:00", "endTime": "21:00",
               "venue": "Tobu Kuminkan", "venueJa": "東部区民館", "mapUrl": "https://maps.example/x", "enabled": True}
        subject, html = emailer.build_agm({"name": "Ann"}, agm)
        self.assertIn("Sunday, 15 November 2026", subject)
        for text in ("18:00–21:00", "Tobu Kuminkan", "https://maps.example/x", "Register for the AGM"):
            self.assertIn(text, html)
        _, closed = emailer.build_agm({"name": "Ann"}, {**agm, "enabled": False})
        self.assertNotIn("Register for the AGM", closed)

    def test_worker_sends_to_the_audience_and_records_the_result(self):
        self.seed()
        sent_to = []

        def fake_send(to, subject, html):
            sent_to.extend(to)
            return to[0] != "never@x.test"  # one address fails

        campaign = logic.create_campaign("join", 0)
        with mock.patch.object(worker.emailer, "_send", fake_send), mock.patch.object(worker.time, "sleep"), \
                mock.patch.object(worker, "load_fee", return_value=1000):
            result = worker.lambda_handler({"campaignId": campaign["campaignId"], "type": "join"}, None)
        saved = self.tables["campaigns"].items[campaign["campaignId"]]
        self.assertEqual((saved["status"], saved["sent"], saved["failed"]), ("done", result["sent"], 1))
        self.assertEqual(saved["failedSample"], ["never@x.test"])
        self.assertEqual(saved["recipientCount"], 7)

    def test_worker_fails_the_campaign_when_there_is_no_agm(self):
        self.seed()
        campaign = logic.create_campaign("agm", 0)
        with mock.patch.object(worker.time, "sleep"), self.assertRaises(RuntimeError):
            worker.lambda_handler({"campaignId": campaign["campaignId"], "type": "agm"}, None)
        self.assertEqual(self.tables["campaigns"].items[campaign["campaignId"]]["status"], "failed")


class AdminRouteTests(AnnouncementTests):
    """POST /admin/member-announcements (inherits the table fakes and seed data)."""

    def setUp(self):
        super().setUp()
        self.lambda_client = mock.Mock()
        patches = [
            mock.patch.object(admin, "ANNOUNCER_FUNCTION", "announcer"),
            mock.patch.object(admin.boto3, "client", return_value=self.lambda_client),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        self.seed()

    def post(self, body):
        resp = admin.handle_send_announcement({"body": json.dumps(body), "requestContext": {"authorizer": {"claims": {"email": "admin@x.test"}}}})
        return resp["statusCode"], json.loads(resp["body"])

    def test_dry_run_previews_the_audience_without_sending(self):
        status, body = self.post({"type": "renew", "dryRun": True})
        self.assertEqual((status, body["recipientCount"], body["sample"]), (200, 1, ["due@x.test"]))
        self.lambda_client.invoke.assert_not_called()
        self.assertEqual(self.tables["campaigns"].items, {})

    def test_send_queues_a_background_job_and_blocks_a_double_click(self):
        status, body = self.post({"type": "join"})
        self.assertEqual((status, body["recipientCount"]), (202, 7))
        call = self.lambda_client.invoke.call_args.kwargs
        self.assertEqual((call["FunctionName"], call["InvocationType"]), ("announcer", "Event"))
        self.assertEqual(json.loads(call["Payload"])["type"], "join")
        self.assertEqual(self.post({"type": "join"})[0], 409)
        self.assertEqual(self.lambda_client.invoke.call_count, 1)

    def test_whatsapp_needs_the_year_link_and_previews_it(self):
        with mock.patch.dict(logic.DEFAULT_WHATSAPP_LINKS, clear=True):  # no default and nothing saved
            status, body = self.post({"type": "whatsapp", "dryRun": True})
        self.assertEqual(status, 409)
        self.assertIn("NPO Membership", body["error"])
        status, body = self.post({"type": "whatsapp", "dryRun": True})  # the built-in default applies
        self.assertEqual((status, body["whatsappLink"]), (200, "https://chat.whatsapp.com/JLYiFD4voZaLx5RRv6oLGO?s=cl&p=i&mlu=4&ilr=4&iam=2"))
        self.set_link()
        status, body = self.post({"type": "whatsapp", "dryRun": True})
        self.assertEqual((status, body["recipientCount"], body["whatsappLink"]), (200, 2, "https://chat.whatsapp.com/ABC123"))
        self.assertEqual(self.post({"type": "whatsapp"})[0], 202)

    def test_validation(self):
        self.assertEqual(self.post({"type": "nope"})[0], 400)
        self.assertEqual(self.post({"type": "agm"})[0], 409)  # no AGM added yet
        self.tables["agms"].items["a"] = {"agmId": "a", "date": "2099-01-01", "title": "AGM"}
        status, body = self.post({"type": "agm", "dryRun": True})
        self.assertEqual((status, body["agm"]["title"]), (200, "AGM"))


if __name__ == "__main__":
    unittest.main()
