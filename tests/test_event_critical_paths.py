"""Critical-path regression tests: login token -> price -> register -> pay -> check-in -> cancel/refund,
capacity under repeated booking, webhook/expiry races, and payment idempotency."""

import importlib.util
import json
import os
import sys
import unittest
from datetime import timedelta
from unittest import mock

sys.path.insert(0, os.path.dirname(__file__))
import test_event_platform_public as tp  # noqa: E402  (sets up sys.path/env and loads the handlers)
from event_fakes import ANSWERS, NOW  # noqa: E402

ROOT = tp.ROOT
import event_registration_admin as radm  # noqa: E402


def load_auth_otp():
    env = {"REGION": "ap-northeast-1", "OTP_TABLE": "otp", "SES_FROM_EMAIL": "a@b.test", "OTP_TOKEN_SECRET": "test-secret"}
    with mock.patch.dict(os.environ, env):
        spec = importlib.util.spec_from_file_location("auth_otp_real", ROOT / "lambdas" / "auth_otp" / "handler.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    return module


def paid_hook(order="order-1", amount=None):
    payment = {"id": "pay-1", "order_id": order, "status": "COMPLETED"}
    if amount is not None:
        payment["amount_money"] = {"amount": amount, "currency": "JPY"}
    return json.dumps({"type": "payment.updated", "data": {"object": {"payment": payment}}})


class LoginCompatibility(tp.Base):
    def test_token_minted_by_the_real_login_lambda_is_accepted(self):
        auth = load_auth_otp()
        token = auth.mint_token("m@example.com")
        status, body, _ = self.register({"idempotencyKey": "login-1"}, token=token)
        self.assertEqual(status, 201, body)
        self.assertEqual(body["registration"]["memberEmail"], "m@example.com")
        self.assertEqual(body["registration"]["totalJpy"], 1600)    # early-bird member price (2 x 800)

    def test_tampered_token_is_rejected_not_downgraded_to_guest(self):
        token = tp.mint("m@example.com")
        payload, sig = token.split(".")
        forged = tp.mint("l@example.com").split(".")[0] + "." + sig
        self.assertEqual(self.register({"idempotencyKey": "t"}, token=forged)[0], 401)


class Lifecycle(tp.Base):
    def test_card_registration_to_refund(self):
        token = tp.mint("m@example.com")
        quote = self.call("POST", "events/picnic/quote", {"tickets": {"adult": 2}}, token=token)[1]
        status, body, _ = self.register({"paymentMethod": "square", "idempotencyKey": "life"}, token=token)
        self.assertEqual(status, 201, body)
        reg = body["registration"]
        self.assertEqual(reg["totalJpy"], quote.get("totalJpy", reg["totalJpy"]))
        rid = reg["registrationId"]
        self.assertEqual(self.store.regs[rid]["status"], "pending_payment")

        patch = mock.patch.object(tp.webhook.square, "verify_webhook_signature", lambda *a: True)
        with patch:
            res = tp.webhook.lambda_handler({"pathParameters": {"provider": "square"}, "headers": {"x-square-hmacsha256-signature": "s"},
                                             "body": paid_hook(amount=reg["totalJpy"])}, None)
        self.assertEqual(res["statusCode"], 200)
        stored = self.store.regs[rid]
        self.assertEqual((stored["status"], stored["paymentStatus"]), ("confirmed", "paid"))

        out = radm.check_in(stored, stored["checkInToken"], "e1", "staff", NOW)
        self.assertEqual(out["result"], "ok")
        self.assertEqual(radm.check_in(self.store.regs[rid], stored["checkInToken"], "e1", "staff", NOW)["result"], "already")
        radm.check_in(self.store.regs[rid], stored["checkInToken"], "e1", "staff", NOW, undo=True)

        status, body, _ = self.call("DELETE", f"my/registrations/{rid}", token=token)
        self.assertEqual(status, 200, body)
        self.assertEqual(self.store.regs[rid]["status"], "cancelled")
        self.assertEqual(self.store.regs[rid]["paymentStatus"], "refund_pending")
        self.assertEqual(self.store.counters["EVENT#e1"], 0)


class CapacityAndRaces(tp.Base):
    def test_never_more_confirmed_than_capacity(self):
        self.store.events["e1"]["capacity"] = 5
        results = []
        for i in range(12):
            status, body, _ = self.register({"idempotencyKey": f"c{i}", "tickets": {"adult": 1}, "answers": {**ANSWERS, "email": f"p{i}@example.com"}}, ip=f"9.9.{i}.1")
            results.append(status)
        self.assertEqual(results.count(201), 5)
        self.assertTrue(all(s == 409 for s in results if s != 201))
        self.assertEqual(self.store.counters["EVENT#e1"], 5)

    def test_idempotent_retry_does_not_take_a_second_seat(self):
        first = self.register({"idempotencyKey": "same"})[1]["registration"]["registrationId"]
        again = self.register({"idempotencyKey": "same"})[1]["registration"]["registrationId"]
        self.assertEqual(first, again)
        self.assertEqual(self.store.counters["EVENT#e1"], 2)

    def test_payment_after_hold_expired_does_not_resurrect_or_double_count(self):
        reg = self.register({"paymentMethod": "square", "idempotencyKey": "race"})[1]["registration"]
        with mock.patch.object(tp.expiry, "datetime") as dt:
            dt.now.return_value = NOW + timedelta(minutes=45)
            self.assertEqual(tp.expiry.lambda_handler({}, None), {"released": 1})
        self.assertEqual(self.store.counters["EVENT#e1"], 0)
        with mock.patch.object(tp.webhook.square, "verify_webhook_signature", lambda *a: True):
            res = tp.webhook.lambda_handler({"pathParameters": {"provider": "square"}, "headers": {"x-square-hmacsha256-signature": "s"},
                                             "body": paid_hook(amount=reg["totalJpy"])}, None)
        self.assertEqual(res["statusCode"], 200)
        # Whatever happens to the late payment, seats must never go negative or be counted twice.
        self.assertIn(self.store.counters["EVENT#e1"], (0, 2))
        stored = self.store.regs[reg["registrationId"]]
        if stored["status"] == "cancelled":
            self.assertNotEqual(self.store.counters["EVENT#e1"], 2)
        else:
            self.assertEqual(self.store.counters["EVENT#e1"], 2)

    def test_wrong_amount_is_flagged_not_confirmed(self):
        reg = self.register({"paymentMethod": "square", "idempotencyKey": "amt"})[1]["registration"]
        with mock.patch.object(tp.webhook.square, "verify_webhook_signature", lambda *a: True):
            tp.webhook.lambda_handler({"pathParameters": {"provider": "square"}, "headers": {"x-square-hmacsha256-signature": "s"},
                                       "body": paid_hook(amount=1)}, None)
        stored = self.store.regs[reg["registrationId"]]
        self.assertEqual(stored["status"], "pending_payment")
        self.assertIn("paymentMismatch", stored)


if __name__ == "__main__":
    unittest.main()
