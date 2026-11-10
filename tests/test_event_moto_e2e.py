"""End to end against a mocked DynamoDB (moto): the real stores, handlers and table definitions together.
Skipped when moto is not installed (pip install "moto[dynamodb]")."""

import importlib.util
import json
import os
import sys
import unittest
from pathlib import Path
from unittest import mock

try:
    from moto import mock_aws
except ImportError:  # pragma: no cover
    mock_aws = None

ROOT = Path(__file__).resolve().parents[1]
for sub in ("lambdas", "lambdas/manage_event_platform", "plugins/seat-upgrade/backend/lambdas", "tests"):
    sys.path.insert(0, str(ROOT / sub))

ENV = {"REGION": "ap-northeast-1", "AWS_DEFAULT_REGION": "ap-northeast-1", "AWS_ACCESS_KEY_ID": "x", "AWS_SECRET_ACCESS_KEY": "x",
       "EVENTS_V2_TABLE": "events", "EVENT_TICKET_TYPES_TABLE": "tickets", "FORM_SCHEMAS_TABLE": "forms",
       "EVENT_REGISTRATIONS_TABLE": "regs", "EVENT_COUNTERS_TABLE": "counters", "AUDIT_LOG_TABLE": "audit",
       "SITE_CONFIG_TABLE": "siteconfig", "ATTENDEE_USER_POOL_ID": "ap-northeast-1_TESTPOOL", "ATTENDEE_APP_CLIENT_ID": "test-client-id"}


def gsi(name, hash_key, range_key=None):
    keys = [{"AttributeName": hash_key, "KeyType": "HASH"}] + ([{"AttributeName": range_key, "KeyType": "RANGE"}] if range_key else [])
    return {"IndexName": name, "KeySchema": keys, "Projection": {"ProjectionType": "ALL"}}


def create_tables(db):
    def table(name, hash_key, range_key=None, gsis=(), types=None):
        types = types or {}
        attrs = {hash_key, *([range_key] if range_key else []), *[k for g in gsis for k in (g["KeySchema"][i]["AttributeName"] for i in range(len(g["KeySchema"])))]}
        keys = [{"AttributeName": hash_key, "KeyType": "HASH"}] + ([{"AttributeName": range_key, "KeyType": "RANGE"}] if range_key else [])
        db.create_table(TableName=name, BillingMode="PAY_PER_REQUEST", KeySchema=keys,
                        AttributeDefinitions=[{"AttributeName": a, "AttributeType": types.get(a, "S")} for a in sorted(attrs)],
                        **({"GlobalSecondaryIndexes": list(gsis)} if gsis else {}))
    table("events", "eventId", gsis=[gsi("SlugIndex", "slug"), gsi("StatusStartIndex", "status", "startsAt")])
    table("tickets", "eventId", "ticketTypeId")
    table("forms", "formId", "version", gsis=[gsi("KindIndex", "kind", "updatedAt")], types={"version": "N"})
    table("regs", "registrationId", gsis=[gsi("EventIndex", "eventId", "createdAt"), gsi("EmailIndex", "email", "createdAt"),
                                          gsi("CheckInTokenIndex", "checkInToken"), gsi("ProviderPaymentIndex", "providerPaymentId"),
                                          gsi("StatusHoldIndex", "status", "holdUntil")])
    table("counters", "counterKey")
    table("audit", "entityKey", "sortKey")
    table("siteconfig", "configKey")


def load(name, rel):
    spec = importlib.util.spec_from_file_location(name, ROOT / rel)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def admin_req(method, path, body=None, groups="Admins", query=None):
    return {"httpMethod": method, "pathParameters": {"proxy": path}, "queryStringParameters": query,
            "body": json.dumps(body) if body is not None else None,
            "requestContext": {"authorizer": {"claims": {"email": "admin@example.com", "cognito:groups": groups}}}}


def public_req(method, path, body=None, token=None, query=None):
    return {"httpMethod": method, "pathParameters": {"proxy": path}, "queryStringParameters": query,
            "headers": {"Authorization": token} if token else {}, "body": json.dumps(body) if body is not None else None,
            "requestContext": {"identity": {"sourceIp": "1.1.1.1"}}}


ANSWERS = {"fullName": "Ann Lee", "email": "ann@example.com", "phone": "+81 90-1234-5678", "consentPrivacy": True, "consentTerms": True}


@unittest.skipIf(mock_aws is None, "moto not installed")
class EndToEnd(unittest.TestCase):
    def setUp(self):
        patcher = mock.patch.dict(os.environ, ENV)
        patcher.start()
        self.addCleanup(patcher.stop)
        mocked = mock_aws()
        mocked.start()
        self.addCleanup(mocked.stop)
        import boto3
        create_tables(boto3.client("dynamodb", region_name="ap-northeast-1"))
        for name in ("event_reg_store", "event_registrations", "event_mailer", "store", "manage_event_platform_handler", "ep_public_handler"):
            sys.modules.pop(name, None)
        self.admin = load("manage_event_platform_handler", "lambdas/manage_event_platform/handler.py")
        self.public = load("ep_public_handler", "lambdas/event_platform_public/handler.py")
        db = boto3.resource("dynamodb", region_name="ap-northeast-1")
        db.Table("siteconfig").put_item(Item={"configKey": "payment_methods_config", "value": json.dumps(
            {"methods": {"bank": {"enabled": True, "instructions": "Account 123"}, "square": {"enabled": True}}})})
        self.webhook = load("ep_webhook_handler", "lambdas/event_platform_webhook/handler.py")
        self.expiry = load("ep_expiry_handler", "lambdas/event_platform_expiry/handler.py")
        import square
        self.square = square
        self.square_status = {"status": "OPEN"}
        self.cancelled_orders = []
        self.checkouts = 0
        for name, fake in (("is_configured", lambda: True),
                           ("create_checkout", self._checkout),
                           ("verify_webhook_signature", lambda url, raw, sig: sig == "good"),
                           ("get_payment_status", lambda order: self.square_status),
                           ("cancel_order", lambda order: self.cancelled_orders.append(order))):
            patcher = mock.patch.object(square, name, fake, create=True)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.emails = []
        for mod in (self.admin.event_mailer, self.public.event_mailer, self.webhook.event_mailer):
            mod.send = lambda kind, reg, ev, methods: self.emails.append((kind, reg["registrationId"]))
        self.mock_square = mock.Mock()
        self.mock_square.create_checkout.side_effect = lambda **kw: {"orderId": "order-1", "checkoutUrl": "https://square.test/x"}
        self.admin.store.client  # noqa: B018  (module imported)
        status, body = self.a("POST", "events", {"title": "Picnic", "slug": "picnic", "startsAt": "2026-12-10T10:00:00+09:00",
                                                 "endsAt": "2026-12-10T16:00:00+09:00", "capacity": 10, "waitlistEnabled": True})
        self.assertEqual(status, 201, body)
        self.eid = body["event"]["eventId"]
        status, body = self.a("POST", f"events/{self.eid}/tickets", {"name": "Adult", "priceJpy": 1500})
        self.assertEqual(status, 201, body)
        self.adult = body["ticket"]["ticketTypeId"]
        status, body = self.a("POST", f"events/{self.eid}/status", {"status": "published"})
        self.assertEqual(status, 200, body)

    def _checkout(self, *, reference_id, amount_yen, description, redirect_url):
        self.checkouts += 1
        return {"providerPaymentId": f"order-{self.checkouts}", "checkoutUrl": f"https://square.test/{self.checkouts}"}

    def card_registration(self, key, email="card@example.com", qty=2):
        status, body = self.p("POST", "events/picnic/register", {"tickets": {self.adult: qty}, "paymentMethod": "square",
                                                                  "answers": {**ANSWERS, "email": email}, "idempotencyKey": key})
        self.assertEqual(status, 201, body)
        return body["registration"]["registrationId"], body

    def hook(self, order_id, status="COMPLETED", amount=None, signature="good", payment_id="pay-1"):
        payment = {"id": payment_id, "order_id": order_id, "status": status}
        if amount is not None:
            payment["amount_money"] = {"amount": amount, "currency": "JPY"}
        raw = json.dumps({"type": "payment.updated", "data": {"object": {"payment": payment}}})
        res = self.webhook.lambda_handler({"pathParameters": {"provider": "square"}, "headers": {"x-square-hmacsha256-signature": signature},
                                           "body": raw}, None)
        return res["statusCode"]

    def reg_item(self, rid):
        import boto3
        return boto3.resource("dynamodb", region_name="ap-northeast-1").Table("regs").get_item(Key={"registrationId": rid})["Item"]

    def expire_hold_now(self, rid):
        import boto3
        boto3.resource("dynamodb", region_name="ap-northeast-1").Table("regs").update_item(
            Key={"registrationId": rid}, UpdateExpression="SET holdUntil = :h", ExpressionAttributeValues={":h": "2020-01-01T00:00:00+09:00"})

    def a(self, *args, **kw):
        res = self.admin.lambda_handler(admin_req(*args, **kw), None)
        return res["statusCode"], json.loads(res["body"])

    def p(self, *args, **kw):
        res = self.public.lambda_handler(public_req(*args, **kw), None)
        return res["statusCode"], json.loads(res["body"])

    def register(self, key, email="ann@example.com", qty=2):
        status, body = self.p("POST", "events/picnic/register", {"tickets": {self.adult: qty}, "paymentMethod": "bank",
                                                                  "answers": {**ANSWERS, "email": email}, "idempotencyKey": key})
        self.assertEqual(status, 201, body)
        return body["registration"]["registrationId"]

    def counter(self):
        import boto3
        item = boto3.resource("dynamodb", region_name="ap-northeast-1").Table("counters").get_item(Key={"counterKey": f"EVENT#{self.eid}"}).get("Item")
        return int(item["sold"]) if item else 0

    def test_page_and_registration(self):
        status, body = self.p("GET", "events/picnic")
        self.assertEqual(status, 200, body)
        rid = self.register("k1")
        self.assertEqual(self.counter(), 2)
        status, body = self.a("GET", f"events/{self.eid}/registrations/{rid}")
        self.assertEqual(status, 200, body)

    def test_mark_paid(self):
        rid = self.register("k2")
        status, body = self.a("POST", f"events/{self.eid}/registrations/{rid}/mark-paid", {"via": "bank", "reference": "ref"})
        self.assertEqual(status, 200, body)
        self.assertEqual((body["registration"]["status"], body["registration"]["paymentStatus"]), ("confirmed", "paid"))

    def test_cancel_unpaid_releases_seats(self):
        rid = self.register("k3")
        status, body = self.a("POST", f"events/{self.eid}/registrations/{rid}/cancel", {"reason": "test"})
        self.assertEqual(status, 200, body)
        self.assertEqual(body["registration"]["status"], "cancelled")
        self.assertEqual(self.counter(), 0)

    def test_cancel_paid_becomes_refund_pending(self):
        rid = self.register("k4")
        self.a("POST", f"events/{self.eid}/registrations/{rid}/mark-paid", {"via": "bank"})
        status, body = self.a("POST", f"events/{self.eid}/registrations/{rid}/cancel", {"reason": "test"})
        self.assertEqual(status, 200, body)
        self.assertEqual(body["registration"]["paymentStatus"], "refund_pending")
        self.assertEqual(self.counter(), 0)

    def test_check_in_and_undo(self):
        rid = self.register("k5")
        self.a("POST", f"events/{self.eid}/registrations/{rid}/mark-paid", {"via": "bank"})
        status, body = self.a("POST", f"events/{self.eid}/registrations/{rid}/check-in", {})
        self.assertEqual((status, body["result"]), (200, "ok"), body)
        status, body = self.a("POST", f"events/{self.eid}/registrations/{rid}/check-in", {"undo": True})
        self.assertEqual((status, body["result"]), (200, "undone"), body)

    def test_listing_dashboard_and_export(self):
        self.register("k6")
        for path in ("dashboard", "registrations", "export"):
            status, body = self.a("GET", f"events/{self.eid}/{path}")
            self.assertEqual(status, 200, f"{path}: {body}")

    def test_waitlist_cancel_and_promote(self):
        status, body = self.a("PUT", f"events/{self.eid}", {"revision": self.revision(), "capacity": 2})
        self.assertEqual(status, 200, body)
        first = self.register("w1")
        status, body = self.p("POST", "events/picnic/register", {"tickets": {self.adult: 1}, "paymentMethod": "bank",
                                                                  "answers": {**ANSWERS, "email": "w@example.com"}, "idempotencyKey": "w2"})
        self.assertEqual((status, body["registration"]["status"]), (201, "waitlisted"), body)
        waiting = body["registration"]["registrationId"]
        status, body = self.a("POST", f"events/{self.eid}/registrations/{waiting}/cancel", {"reason": "x"})
        self.assertEqual((status, body["registration"]["status"]), (200, "cancelled"), body)
        status, body = self.p("POST", "events/picnic/register", {"tickets": {self.adult: 1}, "paymentMethod": "bank",
                                                                  "answers": {**ANSWERS, "email": "w3@example.com"}, "idempotencyKey": "w3"})
        waiting = body["registration"]["registrationId"]
        self.a("POST", f"events/{self.eid}/registrations/{first}/cancel", {"reason": "free a seat"})
        status, body = self.a("POST", f"events/{self.eid}/registrations/{waiting}/promote", {})
        self.assertEqual(status, 200, body)
        self.assertEqual(body["registration"]["status"], "pending_payment")

    def test_edit_refund_and_erase(self):
        rid = self.register("e1")
        reg = self.a("GET", f"events/{self.eid}/registrations/{rid}")[1]["registration"]
        status, body = self.a("PUT", f"events/{self.eid}/registrations/{rid}",
                              {"revision": reg["revision"], "answers": {**reg["answers"], "fullName": "Ann Q"}, "adminNote": "vip"})
        self.assertEqual((status, body["registration"]["fullName"]), (200, "Ann Q"), body)
        self.a("POST", f"events/{self.eid}/registrations/{rid}/mark-paid", {"via": "bank"})
        self.a("POST", f"events/{self.eid}/registrations/{rid}/cancel", {"reason": "x"})
        status, body = self.a("POST", f"events/{self.eid}/registrations/{rid}/refund", {"mode": "manual", "note": "bank"})
        self.assertEqual((status, body["registration"]["paymentStatus"]), (200, "refunded"), body)
        status, body = self.a("POST", f"events/{self.eid}/registrations/{rid}/anonymize", {})
        self.assertEqual((status, body["registration"]["email"]), (200, "erased"), body)

    def test_manual_add_scan_and_email(self):
        status, body = self.a("POST", f"events/{self.eid}/registrations", {
            "tickets": {self.adult: 1}, "answers": ANSWERS, "idempotencyKey": "add1", "paymentStatus": "paid"})
        self.assertIn(status, (200, 201), body)
        import boto3
        reg = boto3.resource("dynamodb", region_name="ap-northeast-1").Table("regs").get_item(
            Key={"registrationId": body["registration"]["registrationId"]})["Item"]
        status, body = self.a("POST", f"events/{self.eid}/check-in", {"code": f"NKEV1.{reg['registrationId']}.{reg['checkInToken']}"})
        self.assertEqual((status, body["result"]), (200, "ok"), body)

    # ── Square webhook ────────────────────────────────────────────────────────

    def test_card_payment_webhook_confirms_once(self):
        rid, body = self.card_registration("c1")
        self.assertEqual(body["payment"]["checkoutUrl"], "https://square.test/1")
        self.assertEqual(self.reg_item(rid)["providerPaymentId"], "order-1")
        self.assertEqual(self.hook("order-1", amount=3000), 200)
        self.assertEqual(self.hook("order-1", amount=3000), 200)          # Square retries
        reg = self.reg_item(rid)
        self.assertEqual((reg["status"], reg["paymentStatus"], reg["paidVia"], reg["paymentReference"]), ("confirmed", "paid", "square", "pay-1"))
        self.assertNotIn("holdUntil", reg)
        self.assertEqual(self.counter(), 2)
        self.assertEqual([k for k, _ in self.emails].count("confirmed"), 1)

    def test_webhook_rejects_bad_signature_and_ignores_unknown_orders(self):
        rid, _ = self.card_registration("c2")
        self.assertEqual(self.hook("order-1", amount=3000, signature="bad"), 401)
        self.assertEqual(self.reg_item(rid)["status"], "pending_payment")
        self.assertEqual(self.hook("membership-order", amount=3000), 200)
        self.assertEqual(self.reg_item(rid)["status"], "pending_payment")

    def test_webhook_with_wrong_amount_is_flagged_not_confirmed(self):
        rid, _ = self.card_registration("c3")
        self.assertEqual(self.hook("order-1", amount=1), 200)
        reg = self.reg_item(rid)
        self.assertEqual(reg["status"], "pending_payment")
        self.assertEqual((int(reg["paymentMismatch"]["expectedJpy"]), int(reg["paymentMismatch"]["reportedJpy"])), (3000, 1))
        status, body = self.a("GET", f"events/{self.eid}/registrations/{rid}")
        self.assertEqual(status, 200, body)
        self.assertIn("paymentMismatch", body["registration"])

    def test_failed_payment_keeps_the_hold(self):
        rid, _ = self.card_registration("c4")
        self.assertEqual(self.hook("order-1", status="FAILED"), 200)
        reg = self.reg_item(rid)
        self.assertEqual(reg["status"], "pending_payment")
        self.assertIn("lastPaymentFailedAt", reg)
        self.assertEqual(self.counter(), 2)

    def test_return_page_reconciles_a_paid_order(self):
        rid, body = self.card_registration("c5")
        token = self.reg_item(rid)["checkInToken"]
        self.square_status = {"status": "COMPLETED", "paymentId": "pay-7", "raw": {"order": {"total_money": {"amount": 3000}}}}
        status, body = self.p("GET", f"registrations/{rid}", query={"token": token})
        self.assertEqual(status, 200, body)
        self.assertEqual(self.reg_item(rid)["status"], "confirmed")

    # ── hold expiry ───────────────────────────────────────────────────────────

    def test_expiry_releases_only_stale_holds(self):
        stale = self.register("x1")
        fresh = self.register("x2", email="b@example.com")
        card, _ = self.card_registration("x3", email="c@example.com")
        paid = self.register("x4", email="d@example.com", qty=1)
        self.a("POST", f"events/{self.eid}/registrations/{paid}/mark-paid", {"via": "bank"})
        for rid in (stale, card, paid):
            self.expire_hold_now(rid)            # `paid` has no hold any more, so it must not be touched
        self.assertEqual(self.counter(), 7)
        self.assertEqual(self.expiry.lambda_handler({}, None), {"released": 2})
        self.assertEqual(self.reg_item(stale)["cancelReason"], "hold_expired")
        self.assertEqual(self.reg_item(card)["status"], "cancelled")
        self.assertEqual(self.reg_item(fresh)["status"], "pending_payment")
        self.assertEqual(self.reg_item(paid)["status"], "confirmed")
        self.assertEqual(self.counter(), 3)
        self.assertIn(("expired", stale), self.emails)
        self.assertEqual(self.expiry.lambda_handler({}, None), {"released": 0})

    def test_payment_after_expiry_retakes_the_seats(self):
        rid, _ = self.card_registration("r1")
        self.expire_hold_now(rid)
        self.assertEqual(self.expiry.lambda_handler({}, None), {"released": 1})
        self.assertEqual(self.counter(), 0)
        self.assertEqual(self.hook("order-1", amount=3000), 200)
        reg = self.reg_item(rid)
        self.assertEqual((reg["status"], reg["paymentStatus"]), ("confirmed", "paid"))
        self.assertEqual(self.counter(), 2)

    def test_payment_after_expiry_with_no_seats_left_becomes_a_refund(self):
        rid, _ = self.card_registration("r2", qty=2)
        self.expire_hold_now(rid)
        self.expiry.lambda_handler({}, None)
        status, body = self.a("PUT", f"events/{self.eid}", {"revision": self.revision(), "capacity": 2})
        self.assertEqual(status, 200, body)
        self.register("r3", email="other@example.com", qty=2)          # takes the freed seats
        self.assertEqual(self.hook("order-1", amount=3000), 200)
        reg = self.reg_item(rid)
        self.assertEqual((reg["status"], reg["paymentStatus"]), ("cancelled", "refund_pending"))
        self.assertEqual(self.counter(), 2)

    def revision(self):
        return self.a("GET", f"events/{self.eid}")[1]["event"]["revision"]

    def test_attendee_can_list_and_cancel_through_the_public_api(self):
        import cognito_jwt
        import jwt_fakes as jf
        verifier = cognito_jwt.CognitoVerifier(jf.REGION, jf.POOL, jf.CLIENT, fetcher=lambda url: jf.jwks("kid-1"))
        with mock.patch.object(self.public, "_verifier", verifier):
            token = jf.mint({"email": "m@example.com", "sub": "sub-m"})
            status, body = self.p("POST", "events/picnic/register", {"tickets": {self.adult: 1}, "paymentMethod": "bank",
                                                                      "answers": {**ANSWERS, "email": "m@example.com"}, "idempotencyKey": "m1"}, token=token)
            self.assertEqual(status, 201, body)
            rid = body["registration"]["registrationId"]
            status, body = self.p("GET", "my/registrations", token=token)
            self.assertEqual((status, len(body["registrations"])), (200, 1))
            status, body = self.p("DELETE", f"my/registrations/{rid}", token=token)
            self.assertEqual(status, 200, body)
            self.assertEqual(self.counter(), 0)

    def configure_addons(self):
        body = {"title": "Picnic", "slug": "picnic", "startsAt": "2026-12-10T10:00:00+09:00", "endsAt": "2026-12-10T16:00:00+09:00",
                "capacity": 10, "waitlistEnabled": True, "revision": self.revision(),
                "slots": [{"slotId": "am", "name": "Morning", "capacity": 3}],
                "upgrades": [{"upgradeId": "vip", "name": "VIP", "priceJpy": 500, "limit": 1}],
                "seatMap": {"sections": [{"sectionId": "main", "name": "Main", "rows": 1, "seatsPerRow": 4, "extraPriceJpy": 0}]},
                "capabilities": {"entrySlots": {"enabled": True}, "zoneUpgrade": {"enabled": True}, "seatReservation": {"enabled": True}}}
        status, resp = self.a("PUT", f"events/{self.eid}", body)
        self.assertEqual(status, 200, resp)

    def counter_value(self, key):
        import boto3
        item = boto3.resource("dynamodb", region_name="ap-northeast-1").Table("counters").get_item(Key={"counterKey": key}).get("Item")
        return int(item["sold"]) if item else 0

    def test_addons_are_reserved_in_dynamodb_and_a_taken_seat_is_refused(self):
        self.configure_addons()
        results = []

        def attempt(n):
            results.append(self.p("POST", "events/picnic/register", {
                "tickets": {self.adult: 1}, "paymentMethod": "bank", "answers": {**ANSWERS, "email": f"p{n}@example.com"},
                "idempotencyKey": f"seat-{n}", "addons": {"entrySlots": {"slotId": "am"}, "seatReservation": {"seats": ["main-A1"]},
                                                          "zoneUpgrade": {"vip": 1}}}))
        for n in range(4):                       # (moto is not thread-safe; real concurrency is covered with the locking fake store)
            attempt(n)
        codes = sorted(r[0] for r in results)
        self.assertEqual(codes, [201, 409, 409, 409], results)                    # one seat, one winner
        self.assertEqual(self.counter_value(f"SEAT#{self.eid}#main-A1"), 1)
        self.assertEqual(self.counter_value(f"SLOT#{self.eid}#am"), 1)             # the losers took nothing
        self.assertEqual(self.counter_value(f"UPGRADE#{self.eid}#vip"), 1)
        self.assertEqual(self.counter(), 1)
        winner = next(r for r in results if r[0] == 201)[1]["registration"]
        self.assertEqual(winner["totalJpy"], 2000)
        status, _ = self.p("DELETE", f"my/registrations/{winner['registrationId']}")           # guests cannot self-cancel...
        self.assertEqual(status, 401)
        reg = self.reg_item(winner["registrationId"])
        status, resp = self.a("POST", f"events/{self.eid}/registrations/{winner['registrationId']}/cancel", {"notify": False})
        self.assertEqual(status, 200, resp)                                        # ...an organiser can, and everything is returned
        self.assertEqual(self.counter_value(f"SEAT#{self.eid}#main-A1"), 0)
        self.assertEqual(self.counter_value(f"SLOT#{self.eid}#am"), 0)
        self.assertEqual(self.counter_value(f"UPGRADE#{self.eid}#vip"), 0)


if __name__ == "__main__":
    unittest.main()
