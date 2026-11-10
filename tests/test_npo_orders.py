"""NPO membership orders from My Page: spouse add-on, linked payment, AGM."""

import importlib.util
import json
import os
import sys
import unittest
from pathlib import Path
from unittest import mock

from botocore.exceptions import ClientError

ROOT = Path(__file__).resolve().parents[1]
for sub in ("lambdas", "lambdas/membership", "lambdas/member_announcements", "plugins/seat-upgrade/backend/lambdas"):
    sys.path.insert(0, str(ROOT / sub))

os.environ.update(
    REGION="ap-northeast-1",
    MEMBERSHIP_TABLE="npo",
    MEMBERS_TABLE="members",
    SITE_CONFIG_TABLE="site",
    AGM_TABLE="agm",
    AGMS_TABLE="agms",
    MEMBER_ANNOUNCEMENTS_TABLE="campaigns",  # modules imported here are shared by the other test files
    CURRENT_MEMBERSHIP_YEAR="2025-26",
    SES_FROM_EMAIL="from@x.test",
    OTP_TOKEN_SECRET="secret",
    SQUARE_ACCESS_TOKEN="t",
    SQUARE_LOCATION_ID="L",
    FRONTEND_URL="https://example.test",
    RENEWAL_MEMBERSHIP_YEAR="2026-27",
    MEMBERSHIP_AS_OF="2026-10-07",
)

import agm_logic  # noqa: E402
import npo_payment_logic  # noqa: E402
import square  # noqa: E402

spec = importlib.util.spec_from_file_location("npo_membership_handler", ROOT / "lambdas/membership/handler.py")
handler = importlib.util.module_from_spec(spec)
spec.loader.exec_module(handler)

admin_spec = importlib.util.spec_from_file_location("manage_members_handler", ROOT / "lambdas/manage_members/handler.py")
admin = importlib.util.module_from_spec(admin_spec)
admin_spec.loader.exec_module(admin)


def failed():
    return ClientError({"Error": {"Code": "ConditionalCheckFailedException"}}, "PutItem")


class FakeTable:
    def __init__(self, keys):
        self.keys = keys
        self.items = {}

    def _key(self, item):
        return tuple(item[k] for k in self.keys)

    def get_item(self, Key):
        item = self.items.get(self._key(Key))
        return {"Item": dict(item)} if item else {}

    def put_item(self, Item, ConditionExpression=None):
        if ConditionExpression and self._key(Item) in self.items:
            raise failed()
        self.items[self._key(Item)] = dict(Item)

    def scan(self, **_):
        return {"Items": [dict(i) for i in self.items.values()]}

    def delete_item(self, Key):
        self.items.pop(self._key(Key), None)

    def query(self, KeyConditionExpression=None, IndexName=None):
        if IndexName:
            wanted = KeyConditionExpression.get_expression()["values"][1]
            return {"Items": [dict(i) for i in self.items.values() if i.get("providerPaymentId") == wanted]}
        wanted = KeyConditionExpression.get_expression()["values"][1]
        return {"Items": [dict(i) for k, i in self.items.items() if k[0] == wanted]}

    def update_item(self, Key, UpdateExpression, ExpressionAttributeValues, ConditionExpression=None, ReturnValues=None):
        item = self.items.setdefault(self._key(Key), dict(Key)) if not ConditionExpression else self.items.get(self._key(Key))
        if ConditionExpression and (not item or item.get("paymentStatus") != "pending"):
            raise failed()
        if ":failed" in ExpressionAttributeValues and item.get("paymentAttemptStatus") == "failed":
            raise failed()  # already flagged: the condition expression rejects a repeat
        v = ExpressionAttributeValues
        if ":oldid" in v:  # retry: swap in the new checkout
            if item.get("providerPaymentId") != v[":oldid"]:
                raise failed()
            item.update(providerPaymentId=v[":new"], checkoutUrl=v[":url"],
                        previousPaymentIds=item.get("previousPaymentIds", []) + v[":old"],
                        retryCount=item.get("retryCount", 0) + 1)
            item.pop("paymentAttemptStatus", None)
        elif UpdateExpression.startswith("SET paymentReportedAt"):
            item.update(paymentReportedAt=v[":now"], updatedAt=v[":now"])
        elif UpdateExpression.startswith("REMOVE paymentReportedAt"):
            item.pop("paymentReportedAt", None)
            item["updatedAt"] = v[":now"]
        elif ":failed" in v:
            item.update(paymentAttemptStatus="failed", lastAttemptFailedAt=v[":now"])
        elif ":paid" in v:
            item.update(paymentStatus="paid", paidDate=v[":now"], paymentReference=v[":ref"])
            if ":f" in v:
                item.update(membDateFrom=v[":f"], membDateTo=v[":t"])
        else:  # member flag activation (":t" from the payment logic, ":v" from the admin handler)
            item.update(npoMembershipActive=v.get(":t", v.get(":v")))
            item.setdefault("memberid", v[":m"])
        return {"Attributes": dict(item)}


class OrderTests(unittest.TestCase):
    def setUp(self):
        self.tables = {
            "npo": FakeTable(("memberid", "transactionNo")),
            "members": FakeTable(("email",)),
            "site": FakeTable(("configKey",)),
            "agm": FakeTable(("agmId", "email")),
            "agms": FakeTable(("agmId",)),
        }
        res = mock.Mock()
        res.Table = lambda name: self.tables[name]
        self.patches = [
            mock.patch.object(handler, "dynamodb", res),
            mock.patch.object(npo_payment_logic, "dynamodb", res),
            mock.patch.object(admin, "dynamodb", res),
            mock.patch.object(npo_payment_logic, "send_email", lambda to, subject, html: True),
            mock.patch.object(npo_payment_logic.member_emailer, "_send", lambda to, subject, html: self.emails.append((to, subject, html)) or True),
        ]
        for p in self.patches:
            p.start()
        self.checkouts = []
        self.emails = []

        def fake_checkout(**kwargs):
            self.checkouts.append(kwargs)
            return {"checkoutUrl": "https://sq.test/pay", "providerPaymentId": "ORDER1"}

        self.patches.append(mock.patch.object(square, "create_checkout", fake_checkout))
        self.patches[-1].start()
        self.set_config("npo_membership_config", {"fee": 1000})
        self.set_config(
            "payment_methods_config",
            {"methods": {"square": {"enabled": True}, "bank": {"enabled": True, "instructions": "Acct 1"}}},
        )
        self.add_member("a@x.test", "1", "Ann")

    def tearDown(self):
        for p in self.patches:
            p.stop()

    def set_config(self, key, value):
        self.tables["site"].items[(key,)] = {"configKey": key, "value": json.dumps(value)}

    def set_agm(self, **fields):
        agm = {"agmId": "agm-2026", "title": "AGM 2026", "date": "2099-11-15", "enabled": True, **fields}
        self.tables["agms"].items[(agm["agmId"],)] = agm

    def use_agm_year(self):
        """Run the order flow in the AGM's own membership year (2099-00) so the tests never depend on today's date."""
        p = mock.patch.object(handler, "RENEWAL_MEMBERSHIP_YEAR", "2099-00")
        p.start()
        self.addCleanup(p.stop)
        self.set_agm()

    def npo_member(self, email, memberid, name="", **kw):
        """A member whose NPO membership covers the AGM date."""
        self.add_member(email, memberid, name, **kw)
        self.tables["npo"].items[(memberid, "99")] = {
            "memberid": memberid, "transactionNo": "99", "membDateFrom": "2099-11-01", "membDateTo": "2100-10-31",
            "email": email, "paymentStatus": "paid",
        }

    def add_member(self, email, memberid, name="", active=False, covered_2026=False, covered_2025=False):
        self.tables["members"].items[(email,)] = {
            "email": email, "memberid": memberid, "fullname": name, "npoMembershipActive": active,
        }
        for n, (flag, start, end) in enumerate(
            [(covered_2025, "2025-11-01", "2026-10-31"), (covered_2026, "2026-11-01", "2027-10-31")], 1
        ):
            if flag:
                self.tables["npo"].items[(memberid, str(n))] = {
                    "memberid": memberid, "transactionNo": str(n), "membDateFrom": start, "membDateTo": end,
                    "email": email, "paymentStatus": "paid",
                }

    def agm_body(self, *adult_emails, **groups):
        return {"adultEmails": list(adult_emails), "attendance": "in_person", **groups}

    def apply(self, body, email="a@x.test"):
        body = {"consent": {"terms": True, "cancellation": True, "privacy": True}, **body}
        resp = handler.handle_apply(email, body, "*")
        return resp["statusCode"], json.loads(resp["body"])

    def txns(self):
        return sorted(self.tables["npo"].items.values(), key=lambda t: (t["memberid"], t["transactionNo"]))

    # ── Branded member emails ────────────────────────────────────────────────

    def emails_to(self, address):
        return [m for m in self.emails if address in m[0]]

    def test_order_email_is_branded_and_says_what_happens_next(self):
        self.add_member("a@x.test", "1", "Ann")
        status, body = self.apply({"paymentMethod": "bank"})
        (to, subject, html), = self.emails_to("a@x.test")
        self.assertIn("awaiting payment", subject)
        for text in ("Dear Ann,", "We've Received Your Request", "Your membership (new)", "Total",
                     "Bank Transfer", "Acct 1", "within 3 days", "Host Your Event With Us"):
            self.assertIn(text, html)
        self.assertNotIn("Pay Now", html)  # no link for a bank transfer

    def test_card_order_email_has_a_pay_now_button_and_no_manual_wait(self):
        self.apply({"paymentMethod": "square"})
        # Square sends the member back marked as a checkout return, so the page can say "confirming…".
        self.assertEqual(self.checkouts[0]["redirect_url"], "https://example.test/member/membership?payment=return")
        (to, subject, html), = self.emails_to("a@x.test")
        self.assertIn("Pay Now", html)
        self.assertIn("https://sq.test/pay", html)
        self.assertIn("starts automatically", html)
        self.assertNotIn("within 3 days", html)

    def test_payment_received_emails_for_the_payer_and_the_spouse(self):
        self.add_member("spouse@x.test", "9", "Sam")
        self.apply({"paymentMethod": "square", "spouseEmail": "spouse@x.test"})
        self.emails.clear()
        updated, newly = npo_payment_logic.mark_paid_from_provider("ORDER1", payment_reference="P1")
        npo_payment_logic.send_paid_email(updated, notify_admin=False)
        (_, subject, html), = self.emails_to("a@x.test")
        self.assertIn("payment received", subject)
        for text in ("Payment Received", "&yen;2,000", "Credit / Debit Card", "1 Nov 2026", "31 Oct 2027", "Host Your Event With Us"):
            self.assertIn(text, html)
        (_, subject, spouse_html), = self.emails_to("spouse@x.test")
        self.assertIn("confirmed", subject)
        self.assertIn("a@x.test", spouse_html)

    def test_admin_mark_paid_emails_the_member_like_an_automatic_payment(self):
        self.add_member("spouse@x.test", "9", "Sam")
        self.apply({"paymentMethod": "bank", "spouseEmail": "spouse@x.test"})
        self.emails.clear()
        resp = admin.handle_update_npo_transaction(
            {"body": json.dumps({"paymentStatus": "paid"})}, "1", "1"
        )
        self.assertEqual(resp["statusCode"], 200)
        self.assertNotIn("linkedPaid", json.loads(resp["body"])["transaction"])
        (_, subject, html), = self.emails_to("a@x.test")
        self.assertIn("payment received", subject)
        self.assertIn("Bank Transfer", html)
        self.assertEqual(len(self.emails_to("spouse@x.test")), 1)
        self.assertEqual(self.tables["npo"].items[("9", "1")]["paymentStatus"], "paid")

    # ── WhatsApp group link in the payment-confirmed emails ──────────────────

    def set_whatsapp_link(self, url="https://chat.whatsapp.com/ABC123", year="2026-27"):
        self.tables["site"].items[("npo_membership_config",)] = {
            "configKey": "npo_membership_config", "value": json.dumps({"fee": 1000, "whatsappLinks": {year: url}}),
        }
        p = mock.patch.object(npo_payment_logic, "SITE_CONFIG_TABLE", "site")
        p.start()
        self.addCleanup(p.stop)

    def test_payment_confirmed_emails_carry_the_whatsapp_group_link(self):
        self.set_whatsapp_link()
        self.add_member("spouse@x.test", "9", "Sam")
        self.apply({"paymentMethod": "square", "spouseEmail": "spouse@x.test"})
        self.emails.clear()
        updated, _ = npo_payment_logic.mark_paid_from_provider("ORDER1", payment_reference="P1")
        npo_payment_logic.send_paid_email(updated, notify_admin=False)
        for address in ("a@x.test", "spouse@x.test"):
            (_, _, html), = self.emails_to(address)
            self.assertIn("https://chat.whatsapp.com/ABC123", html, address)
            self.assertIn("Join the WhatsApp Group", html)

    def test_the_default_link_is_used_when_none_is_saved_and_none_when_there_is_no_default(self):
        self.apply({"paymentMethod": "square"})
        self.emails.clear()
        updated, _ = npo_payment_logic.mark_paid_from_provider("ORDER1", payment_reference="P1")
        npo_payment_logic.send_paid_email(updated, notify_admin=False)
        (_, _, html), = self.emails_to("a@x.test")
        self.assertIn("https://chat.whatsapp.com/JLYiFD4voZaLx5RRv6oLGO", html)
        self.emails.clear()
        with mock.patch.dict(npo_payment_logic.DEFAULT_WHATSAPP_LINKS, clear=True):
            npo_payment_logic.send_paid_email(updated, notify_admin=False)
        (_, _, html), = self.emails_to("a@x.test")
        self.assertNotIn("WhatsApp", html)

    def test_a_spouse_only_payment_does_not_resend_the_link_to_the_payer(self):
        self.set_whatsapp_link()
        self.add_member("a@x.test", "1", "Ann", active=True, covered_2025=True, covered_2026=True)
        self.add_member("s@x.test", "10", "Sue")
        self.apply({"paymentMethod": "square", "spouseEmail": "s@x.test"})
        self.emails.clear()
        updated, _ = npo_payment_logic.mark_paid_from_provider("ORDER1", payment_reference="P1")
        npo_payment_logic.send_paid_email(updated, notify_admin=False)
        (_, _, payer_html), = self.emails_to("a@x.test")
        (_, _, spouse_html), = self.emails_to("s@x.test")
        self.assertNotIn("WhatsApp", payer_html)
        self.assertIn("https://chat.whatsapp.com/ABC123", spouse_html)

    # ── Admin: WhatsApp group joining status (kept per membership year) ─────────

    def set_whatsapp(self, email, value):
        resp = admin.handle_upsert_member({"body": json.dumps({"whatsappGroup": value})}, email_path=email)
        return resp["statusCode"], json.loads(resp["body"])

    def test_whatsapp_status_is_stored_per_year_merged_and_clearable(self):
        self.tables["members"].items[("a@x.test",)]["fullname"] = "Ann"
        status, body = self.set_whatsapp("a@x.test", {"2025-26": "joined"})
        self.assertEqual(status, 200)
        status, body = self.set_whatsapp("a@x.test", {"2026-27": "not_joined"})
        stored = self.tables["members"].items[("a@x.test",)]
        self.assertEqual(stored["whatsappGroup"], {"2025-26": "joined", "2026-27": "not_joined"})  # merged, not replaced
        self.assertEqual(stored["fullname"], "Ann")  # other fields untouched
        self.set_whatsapp("a@x.test", {"2026-27": "joined", "2025-26": ""})  # blank clears a year
        self.assertEqual(self.tables["members"].items[("a@x.test",)]["whatsappGroup"], {"2026-27": "joined"})

    def test_whatsapp_status_is_validated(self):
        before = dict(self.tables["members"].items[("a@x.test",)])
        for value in ({"2026-27": "maybe"}, {"2026": "joined"}, {"soon": "joined"}, "joined", ["joined"]):
            with self.subTest(value=value):
                self.assertEqual(self.set_whatsapp("a@x.test", value)[0], 400)
        self.assertEqual(self.tables["members"].items[("a@x.test",)], before)

    # ── Admin: confirmation emails for Mark paid and for a manually added transaction ──

    def test_mark_paid_emails_the_member_for_paypay_and_bank_transfer(self):
        self.set_config("payment_methods_config", {"methods": {
            "paypay": {"enabled": True, "url": "https://paypay.test/x"}, "bank": {"enabled": True, "instructions": "Acct 1"}}})
        for method, label in (("paypay", "PayPay"), ("bank", "Bank Transfer")):
            with self.subTest(method=method):
                self.tables["npo"].items.clear()
                self.tables["members"].items.clear()
                self.add_member("a@x.test", "1", "Ann")
                self.apply({"paymentMethod": method})
                self.emails.clear()
                resp = admin.handle_update_npo_transaction({"body": json.dumps({"paymentStatus": "paid"})}, "1", "1")
                body = json.loads(resp["body"])
                self.assertEqual((resp["statusCode"], body["emailed"]), (200, True))
                (_, subject, html), = self.emails_to("a@x.test")
                self.assertIn("payment received", subject)
                self.assertIn(label, html)
                self.assertIn("1 Nov 2026", html)

    def add_transaction(self, **fields):
        body = {"email": "a@x.test", "membershipAmountPaid": 1000, "paidVia": "bank", **fields}
        resp = admin.handle_create_npo_transaction({"body": json.dumps(body)}, "1")
        return resp["statusCode"], json.loads(resp["body"])

    def test_an_admin_added_transaction_emails_the_member_when_asked(self):
        self.add_member("a@x.test", "1", "Ann")
        status, body = self.add_transaction(membDateFrom="2026-11-01", membDateTo="2027-10-31", notify=True)
        self.assertEqual((status, body["emailed"]), (200, True))
        (_, subject, html), = self.emails_to("a@x.test")
        self.assertIn("payment received", subject)
        self.assertIn("2026–27", html)
        self.assertIn("1 Nov 2026", html)
        self.assertIn("https://chat.whatsapp.com/", html)  # the 2026–27 group link (default)
        # the stored transaction itself is unchanged by the email
        self.assertEqual(self.tables["npo"].items[("1", "1")]["membDateFrom"], "2026-11-01")

    def test_an_admin_added_transaction_sends_nothing_unless_asked_or_without_an_address(self):
        self.add_member("a@x.test", "1", "Ann")
        for fields in ({}, {"notify": False}, {"notify": True, "email": ""}, {"notify": True, "email": "not-an-email"}):
            with self.subTest(fields=fields):
                status, body = self.add_transaction(membDateFrom="2026-11-01", membDateTo="2027-10-31", **fields)
                self.assertEqual((status, body["emailed"]), (200, False))
        self.assertEqual(self.emails, [])

    def test_back_filled_old_years_get_no_whatsapp_link(self):
        self.add_member("a@x.test", "1", "Ann")
        self.add_transaction(membDateFrom="2023-11-01", membDateTo="2024-10-31", notify=True)
        (_, _, html), = self.emails_to("a@x.test")
        self.assertIn("2023–24", html)
        self.assertNotIn("WhatsApp", html)

    # ── Admin: cancel & refund ───────────────────────────────────────────────

    def paid_card_order(self):
        self.add_member("spouse@x.test", "9", "Sam")
        self.apply({"paymentMethod": "square", "spouseEmail": "spouse@x.test"})
        npo_payment_logic.mark_paid_from_provider("ORDER1", payment_reference="P1")
        self.emails.clear()

    def cancel(self, memberid="1", txn_no="1", **body):
        resp = admin.handle_update_npo_transaction(
            {"body": json.dumps({"action": "cancel", **body})}, memberid, txn_no
        )
        return resp["statusCode"], json.loads(resp["body"])

    def test_cancelling_a_card_order_refunds_through_square_and_cancels_the_spouse_too(self):
        self.paid_card_order()
        refunds = []

        def fake_refund(**kwargs):
            refunds.append(kwargs)
            return {"refundId": "R1", "status": "PENDING"}

        with mock.patch.object(square, "create_refund", fake_refund):
            status, body = self.cancel(refund="square", reason="Joined by mistake")
        self.assertEqual(status, 200)
        self.assertEqual(refunds, [{"payment_id": "P1", "idempotency_key": "npo-1-1-refund", "amount_yen": 2000,
                                    "reason": "Joined by mistake"}])
        primary, spouse = self.tables["npo"].items[("1", "1")], self.tables["npo"].items[("9", "1")]
        self.assertEqual((primary["paymentStatus"], primary["refundStatus"], primary["refundId"]), ("cancelled", "processing", "R1"))
        self.assertEqual((spouse["paymentStatus"], spouse["refundStatus"]), ("cancelled", "included-in-order-refund"))
        # The membership no longer counts anywhere: dates are cleared, the member flag is off.
        self.assertNotIn("membDateTo", primary)
        self.assertEqual(primary["cancelledMembDateTo"], "2027-10-31")
        self.assertFalse(self.tables["members"].items[("a@x.test",)]["npoMembershipActive"])
        self.assertFalse(self.tables["members"].items[("spouse@x.test",)]["npoMembershipActive"])
        (_, subject, html), = self.emails_to("a@x.test")
        self.assertIn("cancelled", subject)
        self.assertIn("&yen;2,000", html)
        self.assertIn("Joined by mistake", html)
        (_, _, spouse_html), = self.emails_to("spouse@x.test")
        self.assertIn("refunded to the payer", spouse_html)

    def test_partial_manual_and_no_refund_modes(self):
        self.paid_card_order()
        with mock.patch.object(square, "create_refund", side_effect=AssertionError("no card refund expected")):
            status, body = self.cancel(refund="manual", refundAmount=1500, notify=False)
        self.assertEqual(status, 200)
        self.assertEqual(body["transaction"]["refundStatus"], "refunded-manually")
        self.assertEqual(body["transaction"]["refundAmount"], 1500)
        self.assertEqual(self.emails, [])  # notify=False

    def test_cancel_validation(self):
        self.paid_card_order()
        self.assertEqual(self.cancel(refund="bogus")[0], 400)
        self.assertEqual(self.cancel(refund="manual", refundAmount=0)[0], 400)
        self.assertEqual(self.cancel(refund="square", refundAmount=2001)[0], 400)
        status, body = self.cancel("9", "1", refund="none")  # the spouse's own record
        self.assertEqual(status, 409)
        self.assertIn("a@x.test", body["error"])
        self.assertEqual(self.tables["npo"].items[("1", "1")]["paymentStatus"], "paid")  # nothing changed

    def test_a_failed_square_refund_changes_nothing_and_can_be_retried(self):
        self.paid_card_order()
        with mock.patch.object(square, "create_refund", side_effect=RuntimeError("boom")):
            status, body = self.cancel(refund="square")
        self.assertEqual(status, 502)
        self.assertEqual(self.tables["npo"].items[("1", "1")]["paymentStatus"], "paid")
        self.assertEqual(self.emails, [])
        with mock.patch.object(square, "create_refund", lambda **kw: {"refundId": "R1", "status": "COMPLETED"}):
            self.assertEqual(self.cancel(refund="square")[0], 200)
        self.assertEqual(self.cancel(refund="square")[0], 409)  # already cancelled

    def test_a_cancelled_membership_cannot_be_revived_by_editing_it(self):
        self.paid_card_order()
        self.cancel(refund="none")
        resp = admin.handle_update_npo_transaction(
            {"body": json.dumps({"paymentStatus": "paid", "membDateFrom": "2026-11-01", "membDateTo": "2027-10-31", "notes": "n"})}, "1", "1")
        self.assertEqual(resp["statusCode"], 200)
        txn = self.tables["npo"].items[("1", "1")]
        self.assertEqual((txn["paymentStatus"], txn["notes"]), ("cancelled", "n"))
        self.assertNotIn("membDateTo", txn)

    def test_bank_payments_cannot_be_refunded_through_square_and_pending_ones_cannot_be_cancelled(self):
        self.apply({"paymentMethod": "bank"})
        self.assertEqual(self.cancel()[0], 409)  # still pending
        admin.handle_update_npo_transaction({"body": json.dumps({"paymentStatus": "paid"})}, "1", "1")
        status, body = self.cancel(refund="square")
        self.assertEqual(status, 409)
        self.assertIn("already refunded", body["error"])
        self.assertEqual(self.cancel(refund="manual")[0], 200)

    # ── "I've paid" for PayPay / bank transfer ───────────────────────────────

    def report(self, reported=True, email="a@x.test"):
        resp = handler.handle_apply(email, {"reportPaid": reported}, "*")
        return resp["statusCode"], json.loads(resp["body"])

    def status_pending(self):
        body = json.loads(handler.handle_status("a@x.test", "*")["body"])
        return body["pendingPayment"]

    def test_member_can_report_a_manual_payment_and_take_it_back(self):
        self.apply({"paymentMethod": "bank"})
        self.assertEqual(self.status_pending()["reportedAt"], "")
        self.emails.clear()
        with mock.patch.object(npo_payment_logic, "send_email", lambda to, subject, html: self.emails.append((to, subject, html)) or True), \
             mock.patch.object(handler, "ADMIN_EMAIL", "admin@x.test"):
            self.assertEqual(self.report(True)[0], 200)
            self.assertEqual(self.report(True)[0], 200)  # idempotent; no second admin mail
        admin_mails = [m for m in self.emails if m[0] == ["admin@x.test"]]
        self.assertEqual(len(admin_mails), 1)
        self.assertIn("a@x.test", admin_mails[0][1])
        pending = self.status_pending()
        self.assertTrue(pending["reportedAt"])
        # Still awaiting payment: only an admin / the provider can mark it paid.
        self.assertEqual(self.tables["npo"].items[("1", "1")]["paymentStatus"], "pending")
        self.assertEqual(self.report(False)[0], 200)
        self.assertEqual(self.status_pending()["reportedAt"], "")

    def test_reporting_is_refused_for_card_orders_and_when_nothing_is_pending(self):
        self.assertEqual(self.report(True)[0], 409)  # nothing pending
        self.apply({"paymentMethod": "square"})
        self.assertEqual(self.report(True)[0], 409)  # card payments confirm themselves

    def test_a_reported_payment_does_not_activate_the_membership(self):
        self.apply({"paymentMethod": "bank"})
        self.report(True)
        self.assertFalse(self.tables["members"].items[("a@x.test",)]["npoMembershipActive"])
        self.assertNotIn("membDateFrom", self.tables["npo"].items[("1", "1")])

    def test_a_spouse_who_is_not_a_member_cannot_be_added(self):
        self.assertEqual(handler.evaluate_spouse("a@x.test", "stranger@x.test")["status"], "not_member")
        check = json.loads(handler.handle_spouse_check("a@x.test", {"email": "stranger@x.test"}, "*")["body"])
        self.assertFalse(check["valid"])
        self.assertIn("become an NK member first", check["message"])
        self.assertTrue(check["registerUrl"].startswith("https://"))
        members_before, txns_before = dict(self.tables["members"].items), len(self.txns())
        status, body = self.apply({"paymentMethod": "bank", "spouseEmail": "stranger@x.test"})
        self.assertEqual(status, 409)
        self.assertIn("become an NK member first", body["error"])
        self.assertEqual((self.tables["members"].items, len(self.txns())), (members_before, txns_before))
        self.assertEqual(self.checkouts, [])  # no Square checkout was created either

    def test_join_with_new_spouse_is_one_order_with_linked_transactions(self):
        self.add_member("spouse@x.test", "9", "Sam")  # a member who has not joined this year yet
        status, body = self.apply({"paymentMethod": "square", "spouseEmail": "Spouse@X.test"})
        self.assertEqual(status, 200)
        self.assertEqual(body["fee"], 2000)
        self.assertEqual(self.checkouts[0]["amount_yen"], 2000)
        primary = self.tables["npo"].items[("1", "1")]
        spouse = self.tables["npo"].items[("9", "1")]
        self.assertEqual(primary["orderTotal"], 2000)
        self.assertEqual(primary["linked"][0]["email"], "spouse@x.test")
        self.assertEqual(spouse["paidByEmail"], "a@x.test")
        self.assertNotIn("membDateFrom", spouse)
        self.assertEqual(len(self.tables["members"].items), 2)  # nobody new was created
        self.assertFalse(self.tables["members"].items[("a@x.test",)]["npoMembershipActive"])

    def test_paying_the_primary_activates_both_once(self):
        self.add_member("spouse@x.test", "9", "Sam")
        self.apply({"paymentMethod": "square", "spouseEmail": "spouse@x.test"})
        updated, newly = npo_payment_logic.mark_paid_from_provider("ORDER1", payment_reference="P1")
        self.assertTrue(newly)
        self.assertEqual(len(updated["linkedPaid"]), 1)
        spouse = self.tables["npo"].items[("9", "1")]
        self.assertEqual((spouse["paymentStatus"], spouse["membDateFrom"]), ("paid", "2026-11-01"))
        self.assertTrue(self.tables["members"].items[("spouse@x.test",)]["npoMembershipActive"])
        _, newly_again = npo_payment_logic.mark_paid_from_provider("ORDER1")
        self.assertFalse(newly_again)

    def test_covered_member_can_add_only_a_spouse(self):
        self.add_member("a@x.test", "1", "Ann", active=True, covered_2025=True, covered_2026=True)
        self.add_member("s@x.test", "10", "Sue")
        self.assertEqual(self.apply({"paymentMethod": "bank"})[0], 409)
        status, body = self.apply({"paymentMethod": "bank", "spouseEmail": "s@x.test"})
        self.assertEqual((status, body["fee"], body["action"]), (200, 1000, "spouse"))
        payer = self.tables["npo"].items[("1", "3")]
        self.assertEqual(payer["kind"], "spouse-payment")
        # One ¥1,000 payment is booked once: the payment-only record carries ¥0.
        self.assertEqual((payer["membershipAmountPaid"], payer["orderTotal"]), (0, 1000))
        self.assertEqual(self.tables["npo"].items[("10", "1")]["membershipAmountPaid"], 1000)
        self.assertIn("spouse's membership (s@x.test)", payer["notes"])
        npo_payment_logic.mark_paid("1", "3")
        self.assertNotIn("membDateFrom", self.tables["npo"].items[("1", "3")])
        self.assertEqual(self.tables["npo"].items[("10", "1")]["paymentStatus"], "paid")

    def test_booked_amounts_add_up_to_what_was_charged(self):
        # Joining with a spouse: two records, ¥1,000 each = the ¥2,000 order.
        self.add_member("a@x.test", "1", "Ann")
        self.add_member("s@x.test", "10", "Sue")
        self.apply({"paymentMethod": "bank", "spouseEmail": "s@x.test"})
        rows = [t for t in self.txns() if t["memberid"] in ("1", "10")]
        self.assertEqual(sum(t["membershipAmountPaid"] for t in rows), 2000)
        self.assertEqual(self.tables["npo"].items[("1", "1")]["orderTotal"], 2000)
        # Each side is easy to recognise in the admin Members tab.
        self.assertEqual(self.tables["npo"].items[("1", "1")]["notes"], "Includes spouse's membership (s@x.test)")
        self.assertEqual(self.tables["npo"].items[("10", "1")]["notes"], "Paid by a@x.test (their order #1)")
        # The payer's own history shows what they paid (the whole order); the spouse's their record.
        payer_rows = handler.transaction_history([self.tables["npo"].items[("1", "1")]])
        self.assertEqual(payer_rows[0]["amount"], 2000)

    def test_spouse_validation(self):
        self.add_member("b@x.test", "2", "Bo", active=True, covered_2025=True)
        self.add_member("c@x.test", "3", "Cy", active=True, covered_2025=True, covered_2026=True)
        self.assertEqual(handler.evaluate_spouse("a@x.test", "b@x.test")["status"], "renew")
        self.assertEqual(handler.evaluate_spouse("a@x.test", "c@x.test")["status"], "covered")
        self.assertEqual(handler.evaluate_spouse("a@x.test", "a@x.test")["status"], "self")
        self.assertEqual(handler.evaluate_spouse("a@x.test", "nope")["status"], "invalid")
        self.add_member("new@x.test", "11", "Nia")
        self.assertEqual(handler.evaluate_spouse("a@x.test", "new@x.test")["status"], "join")
        before = len(self.txns())
        self.assertEqual(self.apply({"paymentMethod": "bank", "spouseEmail": "c@x.test"})[0], 409)
        self.assertEqual(len(self.txns()), before)  # nothing written on a rejected order

    def test_pending_blocks_a_second_order_and_spouse_sees_who_pays(self):
        self.add_member("s@x.test", "10", "Sue")
        self.apply({"paymentMethod": "bank", "spouseEmail": "s@x.test"})
        self.assertEqual(self.apply({"paymentMethod": "bank"})[0], 409)
        self.assertEqual(handler.evaluate_spouse("z@x.test", "s@x.test")["status"], "pending")
        status = json.loads(handler.handle_status("s@x.test", "*")["body"])
        self.assertTrue(status["pendingPayment"]["paidByOther"])

    def test_only_the_soonest_open_upcoming_agm_is_offered(self):
        self.set_agm(agmId="agm-2020", date="2020-11-15")  # already happened
        self.set_agm(agmId="agm-2098", date="2098-11-15", enabled=False)  # registration closed
        self.assertFalse(handler.load_agm_config()["enabled"])
        self.set_agm(agmId="agm-2099b", date="2099-12-01")
        self.set_agm(agmId="agm-2099a", date="2099-06-01")
        self.assertEqual(handler.load_agm_config()["agmId"], "agm-2099a")

    # ── AGM: only NPO members attend as adults ───────────────────────────────

    def test_registrant_ordering_membership_is_provisionally_eligible_for_the_agm(self):
        self.use_agm_year()
        self.add_member("a@x.test", "1", "Ann")
        status, body = self.apply({"paymentMethod": "bank", "agm": self.agm_body(teens=1, kids=2)})
        self.assertEqual((status, body["agmSaved"]), (200, True))
        record = self.tables["agm"].items[("agm-2026", "a@x.test")]
        self.assertEqual((record["adults"], record["totalAttendees"]), (1, 4))
        self.assertEqual(record["adultAttendees"], [{"email": "a@x.test", "name": "Ann", "status": "pending", "registrant": True}])

    def test_extra_adults_must_be_npo_members_but_pending_is_accepted(self):
        self.use_agm_year()
        self.add_member("a@x.test", "1", "Ann")
        self.npo_member("paid@x.test", "2", "Pat")
        self.add_member("late@x.test", "3", "Lee")
        self.tables["npo"].items[("3", "1")] = {"memberid": "3", "transactionNo": "1", "paymentStatus": "pending",
                                                "membershipYear": "2099-00", "email": "late@x.test"}
        status, body = self.apply({"paymentMethod": "bank", "agm": self.agm_body("paid@x.test", "late@x.test")})
        self.assertEqual(status, 200)
        record = self.tables["agm"].items[("agm-2026", "a@x.test")]
        self.assertEqual([(a["email"], a["status"]) for a in record["adultAttendees"]],
                         [("a@x.test", "pending"), ("paid@x.test", "member"), ("late@x.test", "pending")])
        self.assertEqual((record["adults"], record["totalAttendees"]), (3, 3))

    def test_consent_is_required_and_recorded(self):
        self.add_member("a@x.test", "1", "Ann")
        for consent in (None, {}, {"terms": True, "cancellation": True}, {"terms": True, "cancellation": True, "privacy": "yes"}):
            resp = handler.handle_apply("a@x.test", {"paymentMethod": "bank", "consent": consent}, "*")
            self.assertEqual(resp["statusCode"], 400)
        self.assertEqual(self.txns(), [])
        status, _ = self.apply({"paymentMethod": "bank"})
        self.assertEqual(status, 200)
        txn = self.txns()[0]
        self.assertTrue(txn["consentAcceptedAt"])
        self.assertEqual(txn["consentPolicies"], ["terms", "cancellation", "privacy"])

    def test_additional_adults_are_emailed_but_a_same_order_spouse_is_not_doubled(self):
        self.use_agm_year()
        self.add_member("a@x.test", "1", "Ann")
        self.npo_member("paid@x.test", "2", "Pat")
        self.add_member("late@x.test", "3", "Lee")
        self.tables["npo"].items[("3", "1")] = {"memberid": "3", "transactionNo": "1", "paymentStatus": "pending",
                                                "membershipYear": "2099-00", "email": "late@x.test"}
        self.apply({"paymentMethod": "bank", "agm": self.agm_body("paid@x.test", "late@x.test")})
        guest = self.emails_to("paid@x.test")
        self.assertEqual(len(guest), 1)
        self.assertIn("Ann", guest[0][2])
        self.assertIn("Dear Pat", guest[0][2])
        self.assertIn("look forward", guest[0][2])
        late = self.emails_to("late@x.test")
        self.assertEqual(len(late), 1)
        self.assertIn("awaiting confirmation", late[0][2])
        self.assertEqual(len(self.emails_to("a@x.test")), 1)  # the registrant keeps a single order email

    def test_updating_an_agm_registration_only_emails_newly_added_adults(self):
        self.use_agm_year()
        self.npo_member("a@x.test", "1", "Ann")
        self.npo_member("b@x.test", "2", "Bo")
        self.npo_member("c@x.test", "3", "Cy")

        def agm(*adults):
            resp = handler.handle_agm("a@x.test", self.agm_body(*adults), "*")
            self.assertEqual(resp["statusCode"], 200)

        agm("b@x.test")
        agm("b@x.test", "c@x.test")
        self.assertEqual(len(self.emails_to("b@x.test")), 1)
        self.assertEqual(len(self.emails_to("c@x.test")), 1)

    def test_an_adult_who_is_not_an_npo_member_blocks_the_whole_registration(self):
        self.use_agm_year()
        self.add_member("a@x.test", "1", "Ann")
        self.add_member("nk@x.test", "2", "Nia")  # NK member, no NPO membership for the AGM date
        before = len(self.txns())
        status, body = self.apply({"paymentMethod": "square", "agm": self.agm_body("nk@x.test")})
        self.assertEqual(status, 409)
        self.assertIn("nk@x.test", body["error"])
        self.assertIn("isn't an NPO member", body["error"])
        status, body = self.apply({"paymentMethod": "square", "agm": self.agm_body("nobody@x.test")})
        self.assertEqual(status, 409)
        self.assertIn("isn't registered as a WMF Japan member", body["error"])
        # nothing was written: no order, no checkout, no AGM record
        self.assertEqual((len(self.txns()), self.checkouts, self.tables["agm"].items), (before, [], {}))

    def test_a_spouse_in_the_same_order_is_accepted_as_an_adult(self):
        self.use_agm_year()
        self.add_member("a@x.test", "1", "Ann")
        self.add_member("spouse@x.test", "9", "Sam")  # not an NPO member yet — joining in this order
        status, body = self.apply({"paymentMethod": "bank", "spouseEmail": "spouse@x.test",
                                   "agm": self.agm_body("spouse@x.test", toddlers=1)})
        self.assertEqual(status, 200)
        record = self.tables["agm"].items[("agm-2026", "a@x.test")]
        self.assertEqual([(a["email"], a["status"]) for a in record["adultAttendees"]],
                         [("a@x.test", "pending"), ("spouse@x.test", "pending")])
        # ...but the same address without the spouse order is not enough
        self.assertEqual(self.apply({"paymentMethod": "bank", "agm": self.agm_body("spouse@x.test")}, email="z@x.test")[0], 404)

    def test_teens_children_and_toddlers_are_only_counted(self):
        self.use_agm_year()
        self.npo_member("a@x.test", "1", "Ann")
        groups = {"teens": 2, "kids": 1, "preschoolers": 1, "toddlers": 1}
        resp = handler.handle_agm("a@x.test", self.agm_body(**groups), "*")
        self.assertEqual(resp["statusCode"], 200)
        record = self.tables["agm"].items[("agm-2026", "a@x.test")]
        self.assertEqual((record["adults"], record["totalAttendees"]), (1, 6))
        self.assertEqual(record["toddlers"], 1)

    def test_agm_request_validation(self):
        self.use_agm_year()
        self.npo_member("a@x.test", "1", "Ann")
        self.npo_member("b@x.test", "2", "Bo")
        bad = [
            self.agm_body("not-an-email"),
            self.agm_body("b@x.test", "b@x.test"),
            self.agm_body("a@x.test"),  # the registrant is added automatically
            {**self.agm_body(), "attendance": "teleport"},
            self.agm_body(teens=99),
            self.agm_body(toddlers="x"),
        ]
        for body in bad:
            self.assertIn(handler.handle_agm("a@x.test", body, "*")["statusCode"], (400, 409), body)
        self.assertEqual(self.tables["agm"].items, {})

    def test_registrant_whose_membership_lapses_before_the_agm_cannot_register(self):
        self.use_agm_year()
        self.add_member("a@x.test", "1", "Ann", active=True, covered_2026=True)  # member today, not on 15 Nov 2099
        resp = handler.handle_agm("a@x.test", self.agm_body(), "*")
        self.assertEqual(resp["statusCode"], 403)
        self.assertIn("join or renew first", json.loads(resp["body"])["error"])
        view = json.loads(handler.handle_status("a@x.test", "*")["body"])["agm"]
        self.assertEqual((view["eligible"], view["eligibility"]), (False, "not_member"))
        # a registration made earlier can always be cancelled
        self.tables["agm"].items[("agm-2026", "a@x.test")] = {"agmId": "agm-2026", "email": "a@x.test"}
        self.assertEqual(handler.handle_agm("a@x.test", {"cancel": True}, "*")["statusCode"], 200)
        self.assertEqual(self.tables["agm"].items, {})

    def test_the_adult_check_endpoint_reports_each_status(self):
        self.use_agm_year()
        self.npo_member("a@x.test", "1", "Ann")
        self.npo_member("paid@x.test", "2", "Pat")
        self.add_member("nk@x.test", "3", "Nia")

        def check(email):
            resp = handler.handle_agm_adult_check("a@x.test", {"email": email}, "*")
            return json.loads(resp["body"])

        self.assertEqual((check("paid@x.test")["status"], check("paid@x.test")["valid"]), ("member", True))
        self.assertEqual(check("nk@x.test")["status"], "not_member")
        self.assertFalse(check("nk@x.test")["valid"])
        self.assertEqual(check("who@x.test")["status"], "not_nk_member")
        self.assertEqual(check("a@x.test")["status"], "self")
        self.assertEqual(check("???")["status"], "invalid")

    def test_agm_confirmation_email_lists_the_adults_and_flags_pending_payments(self):
        self.use_agm_year()
        self.add_member("a@x.test", "1", "Ann")
        self.npo_member("paid@x.test", "2", "Pat")
        self.apply({"paymentMethod": "bank", "agm": self.agm_body("paid@x.test", teens=1)})
        (_, subject, html), = self.emails_to("a@x.test")
        for text in ("AGM registration", "Adult attendees", "Pat (paid@x.test) — NPO member",
                     "Ann (a@x.test) — membership payment pending", "2 adult(s) 18+, 1 teenager(s) 13–17", "Total people"):
            self.assertIn(text, html)
        self.emails.clear()
        # later: registration updated
        self.tables["npo"].items[("1", "1")]["paymentStatus"] = "paid"
        self.tables["npo"].items[("1", "1")].update(membDateFrom="2099-11-01", membDateTo="2100-10-31")
        handler.handle_agm("a@x.test", self.agm_body("paid@x.test", teens=2), "*")
        (_, subject, html), = self.emails_to("a@x.test")
        self.assertIn("registration updated", subject)
        self.assertNotIn("awaiting confirmation", html)

    def test_agm_logic_uses_the_membership_year_that_contains_the_agm(self):
        self.assertEqual(agm_logic.year_code_for("2026-11-15"), "2026-27")
        self.assertEqual(agm_logic.year_code_for("2027-03-01"), "2026-27")
        self.assertEqual(agm_logic.year_code_for("2026-10-31"), "2025-26")
        member = {"email": "m@x.test"}
        lapsed = [{"membDateFrom": "2025-11-01", "membDateTo": "2026-10-31"}]
        self.assertEqual(agm_logic.status_from(member, lapsed, "2026-11-15"), "not_member")
        self.assertEqual(agm_logic.status_from(member, lapsed, "2026-10-20"), "member")
        self.assertEqual(agm_logic.status_from(None, [], "2026-11-15"), "not_nk_member")


if __name__ == "__main__":
    unittest.main()
