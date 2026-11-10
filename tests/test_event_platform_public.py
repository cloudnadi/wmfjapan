"""Public event API: listing, event page, quote, upload, register, token views, attendee self-service,
the Square webhook and the hold-expiry job."""

import base64
import hashlib
import hmac
import importlib.util
import json
import os
import sys
import time
import unittest
from datetime import timedelta
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
for sub in ("lambdas", "plugins/seat-upgrade/backend/lambdas", "tests"):
    sys.path.insert(0, str(ROOT / sub))
os.environ.setdefault("REGION", "ap-northeast-1")
os.environ["ATTENDEE_USER_POOL_ID"] = "ap-northeast-1_TESTPOOL"
os.environ["ATTENDEE_APP_CLIENT_ID"] = "test-client-id"
os.environ["ASSETS_BUCKET"] = "bucket"

import cognito_jwt  # noqa: E402
import event_model  # noqa: E402
import jwt_fakes as jf  # noqa: E402
import event_registrations as logic  # noqa: E402
from event_fakes import ADULT, ANSWERS, FREE, NOW, FakePayments, FakeStore  # noqa: E402


def load(name, rel):
    spec = importlib.util.spec_from_file_location(name, ROOT / rel)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


public = load("ep_public", "lambdas/event_platform_public/handler.py")
webhook = load("ep_webhook", "lambdas/event_platform_webhook/handler.py")
expiry = load("ep_expiry", "lambdas/event_platform_expiry/handler.py")


def mint(email, exp=None, **claims):
    """An attendee ID token as the attendee pool would issue it (signed with a throw-away test key)."""
    extra = {"email": email, "sub": f"sub-{email}", **claims}
    if exp is not None:
        extra["exp"] = exp
    return jf.mint(extra)


def req(method, path, body=None, token=None, query=None, ip="1.2.3.4"):
    headers = {"Authorization": token} if token else {}
    return {"httpMethod": method, "pathParameters": {"proxy": path}, "queryStringParameters": query,
            "headers": headers, "body": json.dumps(body) if body is not None else None,
            "requestContext": {"identity": {"sourceIp": ip}}}


class Base(unittest.TestCase):
    def setUp(self):
        self.store = FakeStore()
        self.pay = FakePayments()
        self.pay.get_payment_status = lambda order: {"status": self.order_status, "paymentId": "pay-9"}
        self.order_status = "OPEN"
        self.emails = []   # [(kind, registrationId)]; event_mailer is one shared module, so patch it once
        self.s3 = mock.Mock()
        patches = [mock.patch.object(public, "store", self.store), mock.patch.object(public, "square", self.pay),
                   mock.patch.object(public, "now", lambda: NOW), mock.patch.object(public, "s3", lambda: self.s3),
                   mock.patch.object(public.event_mailer, "send", lambda kind, reg, ev, methods: self.emails.append((kind, reg["registrationId"]))),
                   mock.patch.object(public, "FRONTEND_URL", "https://site.test"),
                   mock.patch.object(webhook, "store", self.store), mock.patch.object(webhook, "square", self.pay),
                   mock.patch.object(expiry, "store", self.store)]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        self.event = self.store.add_event(shortDescription="Fun", category="Picnic", emails={"confirmation": {"subject": "S", "body": "B"}},
                                          location={"venueName": "Park", "onlineUrl": "https://zoom.test/secret", "address": "Tokyo"},
                                          createdBy="admin@example.com", design={"coverImage": "https://img/x.jpg", "accentColor": "#112233"})
        self.store.add_tickets("e1", ADULT, FREE)
        self.store.config["payment_methods_config"] = {"methods": {
            "square": {"enabled": True}, "bank": {"enabled": True, "instructions": "Account 123"},
            "paypay": {"enabled": False}}}
        verifier = cognito_jwt.CognitoVerifier(jf.REGION, jf.POOL, jf.CLIENT, fetcher=lambda url: jf.jwks("kid-1"))
        patcher = mock.patch.object(public, "_verifier", verifier)
        patcher.start()
        self.addCleanup(patcher.stop)

    def call(self, *args, **kwargs):
        res = public.lambda_handler(req(*args, **kwargs), None)
        return res["statusCode"], json.loads(res["body"]), res["headers"]

    def register(self, body=None, **kwargs):
        payload = {"tickets": {"adult": 2}, "answers": ANSWERS, "paymentMethod": "bank", "idempotencyKey": "k1", **(body or {})}
        return self.call("POST", "events/picnic/register", payload, **kwargs)


class ListingTests(Base):
    def test_listing_filters_and_visibility(self):
        self.store.add_event(eventId="e2", slug="old", title="Old", startsAt="2026-01-01T10:00:00+09:00", endsAt="2026-01-01T12:00:00+09:00", formId="f1")
        self.store.add_event(eventId="e3", slug="hidden", title="Draft", status="draft", formId="f1")
        self.store.add_event(eventId="e4", slug="link", title="Unlisted", status="unlisted", formId="f1")
        self.store.add_event(eventId="e5", slug="shut", title="Closed", status="closed", category="Other", formId="f1",
                             startsAt="2026-12-01T10:00:00+09:00", endsAt="2026-12-01T12:00:00+09:00")
        titles = lambda q: [e["title"] for e in self.call("GET", "events", query=q)[1]["events"]]
        self.assertEqual(titles({"filter": "upcoming"}), ["Picnic", "Closed"])
        self.assertEqual(titles({"filter": "past"}), ["Old"])
        self.assertEqual(sorted(titles({"filter": "all"})), ["Closed", "Old", "Picnic"])
        self.assertEqual(titles({"category": "other", "filter": "all"}), ["Closed"])
        body = self.call("GET", "events")[1]
        self.assertEqual(body["categories"], ["Other", "Picnic"])
        by_title = {e["title"]: e for e in body["events"]}
        self.assertNotIn("onlineUrl", by_title["Picnic"]["location"])
        self.assertTrue(by_title["Picnic"]["hasOnlineLink"])


class EventPageTests(Base):
    def page(self, **kw):
        status, body, _ = self.call("GET", "events/picnic", **kw)
        self.assertEqual(status, 200, body)
        return body["event"]

    def test_hides_internal_fields(self):
        page = self.page()
        raw = json.dumps(page)
        for secret in ("zoom.test/secret", "createdBy", "admin@example.com", "emails", "revision"):
            self.assertNotIn(secret, raw)
        self.assertEqual(page["design"]["accentColor"], "#112233")
        self.assertEqual(page["form"]["version"], 1)

    def test_drafts_and_archived_are_404_but_unlisted_works_by_link(self):
        for status, expected in (("draft", 404), ("archived", 404), ("unlisted", 200), ("closed", 200)):
            self.store.events["e1"]["status"] = status
            self.assertEqual(self.call("GET", "events/picnic")[0], expected, status)
        self.assertEqual(self.call("GET", "events/none")[0], 404)

    def test_closed_event_page_says_registration_closed(self):
        self.store.events["e1"]["status"] = "closed"
        self.assertEqual(self.page()["registration"]["window"], "closed")

    def test_early_bird_price_applies_to_everyone_until_its_cutoff(self):
        self.store.add_tickets("e1", {**ADULT, "earlyBirdPriceJpy": 800, "earlyBirdCutoff": "2026-09-10T00:00:00+09:00"}, FREE)
        for token in (None, mint("m@example.com")):
            adult = {t["name"]: t for t in self.page(token=token)["tickets"]}["Adult"]
            self.assertEqual((adult["yourPriceJpy"], adult["yourPriceIsEarlyBird"], adult["earlyBirdOpen"]), (800, True, True))
        self.store.add_tickets("e1", {**ADULT, "earlyBirdPriceJpy": 800, "earlyBirdCutoff": "2026-08-01T00:00:00+09:00"}, FREE)
        adult = {t["name"]: t for t in self.page()["tickets"]}["Adult"]
        self.assertEqual((adult["yourPriceJpy"], adult["earlyBirdOpen"]), (1500, False))

    def test_viewer_is_the_signed_in_account_only(self):
        viewer = self.page(token=mint("m@example.com", name="Mia"))["viewer"]
        self.assertEqual((viewer["loggedIn"], viewer["email"], viewer["name"]), (True, "m@example.com", "Mia"))
        self.assertEqual(self.page()["viewer"], {"loggedIn": False, "email": "", "name": ""})
        self.assertNotIn("memberPricing", viewer)

    def test_invalid_token_on_a_page_view_is_just_a_guest(self):
        self.assertFalse(self.page(token="garbage")["viewer"]["loggedIn"])

    def test_availability_and_waitlist_flag(self):
        self.store.events["e1"].update(capacity=2, waitlistEnabled=True)
        self.store.counters["EVENT#e1"] = 2
        page = self.page()
        self.assertTrue(page["availability"]["full"])
        self.assertTrue(page["registration"]["waitlist"])
        self.assertEqual({t["name"]: t["remaining"] for t in page["tickets"]}, {"Adult": 0, "Child": 0})

    def test_only_enabled_payment_methods_listed_without_details(self):
        page = self.page()
        self.assertEqual([m["key"] for m in page["paymentMethods"]], ["square", "bank"])
        self.assertNotIn("Account 123", json.dumps(page))


class QuoteTests(Base):
    def test_quote_is_server_priced(self):
        status, body, _ = self.call("POST", "events/picnic/quote", {"tickets": {"adult": 2, "free": 1}, "totalJpy": 1})
        self.assertEqual((status, body["totalJpy"]), (200, 3000))
        status, body, _ = self.call("POST", "events/picnic/quote", {"tickets": {"adult": 2}}, token=mint("m@example.com"))
        self.assertEqual(body["totalJpy"], 3000)                                  # signing in does not change the price
        self.assertEqual(self.call("POST", "events/picnic/quote", {"tickets": {"zz": 1}})[0], 400)

    def test_rate_limited(self):
        codes = [self.call("POST", "events/picnic/quote", {"tickets": {"adult": 1}}, ip="9.9.9.9")[0] for _ in range(62)]
        self.assertEqual(codes.count(200), 60)
        self.assertEqual(codes[-1], 429)
        self.assertEqual(self.call("POST", "events/picnic/quote", {"tickets": {"adult": 1}}, ip="8.8.8.8")[0], 200)


class RegisterTests(Base):
    def test_guest_bank_registration(self):
        status, body, _ = self.register()
        self.assertEqual(status, 201, body)
        reg = body["registration"]
        self.assertEqual((reg["status"], reg["totalJpy"], reg["email"]), ("pending_payment", 3000, "ann@example.com"))
        self.assertEqual(body["payment"]["method"], "bank")
        self.assertEqual(body["payment"]["instructions"], "Account 123")
        self.assertNotIn("qr", body)
        self.assertNotIn("providerPaymentId", json.dumps(body))
        self.assertEqual(self.emails, [("pending_payment", reg["registrationId"])])

    def test_card_registration_returns_checkout_url(self):
        body = self.register({"paymentMethod": "square"})[1]
        self.assertEqual(body["payment"]["checkoutUrl"], "https://square.test/order-1")
        self.assertNotIn("instructions", body["payment"])

    def test_free_confirmed_registration_has_qr_and_online_link(self):
        body = self.register({"tickets": {"free": 1}, "paymentMethod": None})[1]
        self.assertEqual(body["registration"]["status"], "confirmed")
        self.assertTrue(body["qr"].startswith("NKEV1.NK-"))
        self.assertEqual(body["event"]["location"]["onlineUrl"], "https://zoom.test/secret")     # attendees only

    def test_signed_in_registration_links_the_account_and_charges_the_same_price(self):
        body = self.register(token=mint("m@example.com"))[1]
        self.assertEqual((body["registration"]["totalJpy"], body["registration"]["accountEmail"]), (3000, "m@example.com"))
        self.assertEqual(body["registration"]["accountSub"], "sub-m@example.com")

    def test_expired_session_is_401_not_a_silent_guest_registration(self):
        status, body, _ = self.register(token=mint("m@example.com", exp=int(time.time()) - 600))
        self.assertEqual((status, body["code"]), (401, "session_expired"))
        self.assertEqual(self.register(token="garbage")[0], 401)
        self.assertEqual(self.store.regs, {})

    def test_guest_registration_can_be_switched_off_per_event(self):
        self.store.events["e1"]["access"] = {"guestsAllowed": False}
        status, body, _ = self.register()
        self.assertEqual((status, body["code"]), (403, "login_required"))
        listing = self.call("GET", "events")[1]["events"][0]
        self.assertFalse(listing["guestsAllowed"])
        page = self.call("GET", "events/picnic")[1]["event"]
        self.assertFalse(page["registration"]["allowed"])
        # any signed-in user may still register
        status, body, _ = self.register({"idempotencyKey": "lapsed"}, token=mint("x@example.com"))
        self.assertEqual(status, 201, body)
        self.assertEqual(body["registration"]["totalJpy"], 3000)
        # and with guests on (the default) the same anonymous request works
        self.store.events["e1"]["access"] = {"guestsAllowed": True}
        self.assertEqual(self.register({"idempotencyKey": "guest"})[0], 201)
        self.assertTrue(self.call("GET", "events")[1]["events"][0]["guestsAllowed"])

    def test_idempotent_replay(self):
        first = self.register()
        again = self.register()
        self.assertEqual((first[0], again[0]), (201, 200))
        self.assertEqual(first[1]["registration"]["registrationId"], again[1]["registration"]["registrationId"])
        self.assertEqual(self.store.counters["EVENT#e1"], 2)
        self.assertEqual(len(self.emails), 1)

    def test_validation_and_capacity_errors_are_structured(self):
        status, body, _ = self.register({"answers": {**ANSWERS, "fullName": ""}})
        self.assertEqual((status, "fullName" in body["fields"]), (400, True))
        self.store.events["e1"]["capacity"] = 1
        status, body, _ = self.register()
        self.assertEqual((status, body["code"]), (409, "sold_out"))

    def test_waitlist_response(self):
        self.store.events["e1"].update(capacity=1, waitlistEnabled=True)
        self.register({"tickets": {"adult": 1}})
        body = self.register({"idempotencyKey": "w", "answers": {**ANSWERS, "email": "w@example.com"}, "tickets": {"adult": 1}})[1]
        self.assertEqual(body["registration"]["status"], "waitlisted")
        self.assertEqual(self.emails[-1][0], "waitlisted")

    def test_rate_limits_per_ip_and_per_email(self):
        codes = [self.register({"idempotencyKey": f"k{i}", "answers": {**ANSWERS, "email": f"p{i}@example.com"}}, ip="7.7.7.7")[0]
                 for i in range(12)]
        self.assertEqual(codes.count(201), 10)
        self.assertEqual(codes[-1], 429)
        status, body, headers = self.register({"idempotencyKey": "z"}, ip="7.7.7.7")
        self.assertEqual((status, body["code"], headers["Retry-After"]), (429, "rate_limited", "600"))
        emails = [self.register({"idempotencyKey": f"e{i}", "tickets": {"free": 1}, "paymentMethod": None}, ip=f"10.0.0.{i}")[0] for i in range(7)]
        self.assertEqual((emails.count(201), emails[-1]), (5, 429))

    def test_rate_limiter_outage_fails_open(self):
        with mock.patch.object(self.store, "rate_limit_allows", side_effect=RuntimeError("down")):
            self.assertEqual(self.register()[0], 201)

    def test_bad_bodies(self):
        bad = req("POST", "events/picnic/register")
        bad["body"] = "{nope"
        self.assertEqual(public.lambda_handler(bad, None)["statusCode"], 400)
        self.assertEqual(self.call("POST", "events/picnic/register", {"tickets": {}, "answers": "x"})[0], 400)


class UploadTests(Base):
    PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 50

    def setUp(self):
        super().setUp()
        fields = event_model.default_form_fields() + [
            {"key": "proof", "type": "file", "label": "Proof", "validation": {"maxSizeMb": 1, "allowedTypes": ["image/png"]}}]
        self.store.forms[("f1", 2)] = event_model.new_form_version("f1", self.store.forms[("f1", 1)], fields, event_id="e1")

    def up(self, **over):
        body = {"fieldKey": "proof", "name": "../a b.png", "contentType": "image/png", "data": base64.b64encode(self.PNG).decode(), **over}
        return self.call("POST", "events/picnic/upload", body)

    def test_valid_upload_is_private_and_sanitised(self):
        status, body, _ = self.up()
        self.assertEqual(status, 201, body)
        kwargs = self.s3.put_object.call_args.kwargs
        self.assertTrue(kwargs["Key"].startswith("event-uploads-pending/e1/"))
        self.assertFalse(kwargs["Key"].startswith("media/"))
        self.assertEqual((kwargs["ContentDisposition"], kwargs["ServerSideEncryption"]), ("attachment", "AES256"))
        self.assertNotIn("/", body["name"])

    def test_rejections(self):
        cases = [dict(fieldKey="nope"), dict(contentType="application/pdf"), dict(contentType="image/svg+xml"),
                 dict(data=base64.b64encode(b"<html>x</html>").decode()), dict(data="!!!"),
                 dict(data=base64.b64encode(self.PNG + b"0" * (1024 * 1024)).decode()), dict(data="")]
        for over in cases:
            self.assertEqual(self.up(**over)[0], 400, over)
        self.assertEqual(self.s3.put_object.call_count, 0)

    def test_registration_uses_stored_metadata_and_rejects_foreign_files(self):
        self.s3.head_object.return_value = {"ContentLength": 58, "ContentType": "image/png", "Metadata": {"name": "proof.png"}}
        answers = {**ANSWERS, "proof": {"fileId": "abc123", "name": "evil.exe", "size": 1, "contentType": "application/x-msdownload"}}
        status, body, _ = self.register({"answers": answers, "tickets": {"free": 1}, "paymentMethod": None})
        self.assertEqual(status, 201, body)
        self.assertEqual(body["registration"]["answers"]["proof"], {"fileId": "abc123", "name": "proof.png", "size": 58, "contentType": "image/png"})
        self.assertEqual(self.s3.head_object.call_args.kwargs["Key"], "event-uploads/e1/abc123")
        from botocore.exceptions import ClientError
        self.s3.head_object.side_effect = ClientError({"Error": {"Code": "404"}}, "HeadObject")
        status, body, _ = self.register({"idempotencyKey": "k2", "answers": {**answers, "email": "b@example.com"}, "tickets": {"free": 1}, "paymentMethod": None})
        self.assertEqual((status, "proof" in body["fields"]), (400, True))
        for bad_id in ("../x", "a/b", "", 5):
            self.s3.head_object.side_effect = None
            status, body, _ = self.register({"idempotencyKey": "k3", "answers": {**answers, "proof": {"fileId": bad_id}}, "tickets": {"free": 1}, "paymentMethod": None})
            self.assertEqual(status, 400, bad_id)


class TokenViewTests(Base):
    def setUp(self):
        super().setUp()
        body = self.register({"paymentMethod": "square"})[1]
        self.reg = body["registration"]
        self.q = {"token": self.reg["checkInToken"]}

    def test_requires_the_right_token(self):
        rid = self.reg["registrationId"]
        for query in (None, {"token": "wrong"}, {"token": ""}):
            self.assertEqual(self.call("GET", f"registrations/{rid}", query=query)[0], 404)
        self.assertEqual(self.call("GET", "registrations/NK-NOPE-NOPE", query=self.q)[0], 404)
        status, body, _ = self.call("GET", f"registrations/{rid}", query=self.q)
        self.assertEqual((status, body["registration"]["status"]), (200, "pending_payment"))

    def test_card_return_reconciles_with_square_and_confirms(self):
        self.order_status = "COMPLETED"
        status, body, _ = self.call("GET", f"registrations/{self.reg['registrationId']}", query=self.q)
        self.assertEqual((body["registration"]["status"], body["registration"]["paymentStatus"]), ("confirmed", "paid"))
        self.assertIn("qr", body)
        self.assertIn(("confirmed", self.reg["registrationId"]), self.emails)

    def test_pay_again_with_another_method(self):
        rid = self.reg["registrationId"]
        status, body, _ = self.call("POST", f"registrations/{rid}/pay", {"paymentMethod": "bank"}, query=self.q)
        self.assertEqual((status, body["payment"]["method"], body["payment"]["instructions"]), (200, "bank", "Account 123"))
        self.assertEqual(self.call("POST", f"registrations/{rid}/pay", {"paymentMethod": "paypay"}, query=self.q)[0], 400)   # disabled
        self.assertEqual(self.call("POST", f"registrations/{rid}/pay", {"paymentMethod": "bank"}, query={"token": "x"})[0], 404)


class SelfServiceTests(Base):
    def setUp(self):
        super().setUp()
        self.token = mint("m@example.com")
        self.reg = self.register(token=self.token)[1]["registration"]
        self.rid = self.reg["registrationId"]

    def test_requires_sign_in(self):
        self.assertEqual(self.call("GET", "my/registrations")[0], 401)
        self.assertEqual(self.call("GET", f"my/registrations/{self.rid}", token="bad")[0], 401)

    def test_list_only_my_registrations_with_can_change(self):
        self.register({"idempotencyKey": "other", "answers": {**ANSWERS, "email": "g@example.com"}})        # a guest's
        self.register({"idempotencyKey": "x"}, token=mint("l@example.com"))
        rows = self.call("GET", "my/registrations", token=self.token)[1]["registrations"]
        self.assertEqual([r["registration"]["registrationId"] for r in rows], [self.rid])
        self.assertTrue(rows[0]["canChange"])
        self.assertEqual(rows[0]["event"]["title"], "Picnic")

    def test_get_includes_the_form_version_registered_with(self):
        fields = event_model.default_form_fields() + [{"key": "extra", "type": "text", "label": "Extra"}]
        self.store.forms[("f1", 2)] = event_model.new_form_version("f1", self.store.forms[("f1", 1)], fields, event_id="e1")
        view = self.call("GET", f"my/registrations/{self.rid}", token=self.token)[1]
        self.assertEqual(view["form"]["version"], 1)
        self.assertNotIn("extra", [f["key"] for f in view["form"]["fields"]])

    def test_edit_and_cancel(self):
        answers = {**self.reg["answers"], "fullName": "Mia Q"}
        status, body, _ = self.call("PUT", f"my/registrations/{self.rid}", {"answers": answers}, token=self.token)
        self.assertEqual((status, body["registration"]["fullName"]), (200, "Mia Q"))
        self.assertEqual(self.call("PUT", f"my/registrations/{self.rid}", {"answers": {**answers, "fullName": ""}}, token=self.token)[0], 400)
        status, body, _ = self.call("DELETE", f"my/registrations/{self.rid}", token=self.token)
        self.assertEqual((status, body["registration"]["status"]), (200, "cancelled"))
        self.assertEqual(self.store.counters["EVENT#e1"], 0)
        self.assertEqual(self.call("DELETE", f"my/registrations/{self.rid}", token=self.token)[0], 409)
        self.assertEqual(self.call("PUT", f"my/registrations/{self.rid}", {"answers": answers}, token=self.token)[0], 409)

    def test_other_members_get_404(self):
        other = mint("l@example.com")
        for method in ("GET", "PUT", "DELETE"):
            self.assertEqual(self.call(method, f"my/registrations/{self.rid}", {"answers": {}}, token=other)[0], 404, method)
        self.assertEqual(self.call("GET", "my/registrations/NK-NONE-NONE", token=self.token)[0], 404)

    def test_after_registration_closes_only_organisers_can_change(self):
        self.store.events["e1"]["registrationClosesAt"] = (NOW - timedelta(hours=1)).isoformat()
        self.assertFalse(self.call("GET", "my/registrations", token=self.token)[1]["registrations"][0]["canChange"])
        status, body, _ = self.call("DELETE", f"my/registrations/{self.rid}", token=self.token)
        self.assertEqual((status, body["code"]), (409, "closed"))


class WebhookAndExpiryTests(Base):
    def setUp(self):
        super().setUp()
        self.reg = self.register({"paymentMethod": "square"})[1]["registration"]
        self.good = {"x-square-hmacsha256-signature": "sig"}
        self.body = json.dumps({"type": "payment.updated", "data": {"object": {"payment": {
            "id": "pay-1", "order_id": "order-1", "status": "COMPLETED"}}}})

    def hook(self, body=None, headers=None, provider="square"):
        patch_ok = mock.patch.object(webhook.square, "verify_webhook_signature", lambda url, raw, sig: sig == "sig")
        with patch_ok:
            return webhook.lambda_handler({"pathParameters": {"provider": provider}, "headers": headers or self.good,
                                           "body": body or self.body}, None)

    def test_signature_and_provider_checks(self):
        self.assertEqual(self.hook(headers={"x-square-hmacsha256-signature": "bad"})["statusCode"], 401)
        self.assertEqual(self.hook(provider="paypay")["statusCode"], 404)
        self.assertEqual(self.store.regs[self.reg["registrationId"]]["status"], "pending_payment")

    def test_completed_payment_confirms_once(self):
        self.assertEqual(self.hook()["statusCode"], 200)
        self.assertEqual(self.hook()["statusCode"], 200)              # Square retries
        reg = self.store.regs[self.reg["registrationId"]]
        self.assertEqual((reg["status"], reg["paymentStatus"], reg["paymentReference"]), ("confirmed", "paid", "pay-1"))
        self.assertEqual([k for k, _ in self.emails].count("confirmed"), 1)

    def test_failed_payment_keeps_the_hold_and_unknown_orders_are_ignored(self):
        body = json.dumps({"type": "payment.updated", "data": {"object": {"payment": {"id": "p", "order_id": "order-1", "status": "FAILED"}}}})
        self.assertEqual(self.hook(body)["statusCode"], 200)
        reg = self.store.regs[self.reg["registrationId"]]
        self.assertEqual(reg["status"], "pending_payment")
        self.assertIn("lastPaymentFailedAt", reg)
        other = json.dumps({"type": "payment.updated", "data": {"object": {"payment": {"id": "p", "order_id": "membership-order", "status": "COMPLETED"}}}})
        self.assertEqual(self.hook(other)["statusCode"], 200)
        self.assertEqual(self.hook(json.dumps({"type": "refund.updated"}))["statusCode"], 200)

    def test_expiry_job_releases_only_stale_holds(self):
        with mock.patch.object(expiry, "datetime") as dt:
            dt.now.return_value = NOW + timedelta(minutes=10)
            self.assertEqual(expiry.lambda_handler({}, None), {"released": 0})
            dt.now.return_value = NOW + timedelta(minutes=45)
            self.assertEqual(expiry.lambda_handler({}, None), {"released": 1})
            self.assertEqual(expiry.lambda_handler({}, None), {"released": 0})
        self.assertEqual(self.store.regs[self.reg["registrationId"]]["cancelReason"], "hold_expired")
        self.assertEqual(self.store.counters["EVENT#e1"], 0)
        self.assertIn("expired", [k for k, _ in self.emails])


class RoutingTests(Base):
    def test_unknown_routes_and_options(self):
        self.assertEqual(self.call("GET", "nothing")[0], 404)
        self.assertEqual(self.call("DELETE", "events")[0], 404)
        self.assertEqual(public.lambda_handler({"httpMethod": "OPTIONS"}, None)["statusCode"], 200)


class StoreTransactionTests(unittest.TestCase):
    """event_reg_store builds the right DynamoDB transactions and maps cancellations to domain errors."""

    def setUp(self):
        import event_reg_store as real
        self.real = real
        self.client = mock.Mock()
        p = mock.patch.object(real, "client", self.client)
        p.start()
        self.addCleanup(p.stop)
        self.reg = {"registrationId": "NK-1", "eventId": "e1", "status": "confirmed", "total": 1.5}

    def cancel(self, codes):
        from botocore.exceptions import ClientError
        self.client.transact_write_items.side_effect = ClientError(
            {"Error": {"Code": "TransactionCanceledException"}, "CancellationReasons": [{"Code": c} for c in codes]}, "T")

    def test_reserve_and_create_shape(self):
        self.real.reserve_and_create(self.reg, [("EVENT#e1", 2, 10), ("TICKET#e1#a", 2, None)])
        items = self.client.transact_write_items.call_args.kwargs["TransactItems"]
        self.assertEqual(len(items), 3)
        self.assertIn("attribute_not_exists(registrationId)", items[0]["Put"]["ConditionExpression"])
        self.assertEqual(items[1]["Update"]["ConditionExpression"], "attribute_not_exists(sold) OR sold <= :max")
        self.assertEqual(items[1]["Update"]["ExpressionAttributeValues"][":max"], {"N": "8"})       # limit - amount
        self.assertNotIn("ConditionExpression", items[2]["Update"])                                 # no limit, no condition
        self.assertEqual(items[0]["Put"]["Item"]["total"], {"N": "1.5"})                            # floats become Decimals

    def test_error_mapping(self):
        self.cancel(["ConditionalCheckFailed", "None"])
        with self.assertRaises(logic.AlreadyExists):
            self.real.reserve_and_create(self.reg, [("EVENT#e1", 1, 5)])
        self.cancel(["None", "ConditionalCheckFailed", "None"])
        with self.assertRaises(logic.CapacityFull) as ctx:
            self.real.reserve_and_create(self.reg, [("EVENT#e1", 1, 5), ("TICKET#e1#a", 1, 5)])
        self.assertEqual(ctx.exception.scope, "event")
        self.cancel(["None", "None", "ConditionalCheckFailed"])
        with self.assertRaises(logic.CapacityFull) as ctx:
            self.real.reserve_and_create(self.reg, [("EVENT#e1", 1, 5), ("TICKET#e1#a", 1, 5)])
        self.assertEqual(ctx.exception.scope, "ticket")
        self.cancel(["ConditionalCheckFailed", "None"])
        with self.assertRaises(logic.StateConflict):
            self.real.release("NK-1", "confirmed", {"status": "cancelled"}, [("EVENT#e1", 1)])

    def test_request_bigger_than_limit_fails_without_calling_dynamo(self):
        with self.assertRaises(logic.CapacityFull):
            self.real.reserve_and_create(self.reg, [("EVENT#e1", 6, 5)])
        self.client.transact_write_items.assert_not_called()

    def test_release_shape(self):
        self.client.transact_write_items.return_value = {}
        with mock.patch.object(self.real, "get_registration", return_value={}):
            self.real.release("NK-1", "confirmed", {"status": "cancelled"}, [("EVENT#e1", 2)], remove=["holdUntil"])
        items = self.client.transact_write_items.call_args.kwargs["TransactItems"]
        self.assertEqual(items[0]["Update"]["ConditionExpression"], "#st = :es")
        self.assertIn("REMOVE", items[0]["Update"]["UpdateExpression"])
        self.assertEqual((items[1]["Update"]["UpdateExpression"], items[1]["Update"]["ConditionExpression"]),
                         ("SET sold = sold - :n", "sold >= :n"))


if __name__ == "__main__":
    unittest.main()
