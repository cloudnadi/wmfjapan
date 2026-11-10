"""Per-event capabilities (guest checkout, authentication, seat reservation, zone upgrade, entry slots, dinner):
defaults for older records, admin validation, API exposure, server-side refusal, Cognito sign-in on the public API."""

import importlib.util
import json
import os
import sys
import time
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
for sub in ("lambdas", "lambdas/membership", "plugins/seat-upgrade/backend/lambdas", "tests"):
    sys.path.insert(0, str(ROOT / sub))
os.environ.setdefault("REGION", "ap-northeast-1")
os.environ["ATTENDEE_USER_POOL_ID"] = "ap-northeast-1_TESTPOOL"
os.environ["ATTENDEE_APP_CLIENT_ID"] = "test-client-id"

import cognito_jwt  # noqa: E402
import entry_slots_logic  # noqa: E402
import event_capabilities as caps  # noqa: E402
import event_model  # noqa: E402
import jwt_fakes as jf  # noqa: E402
from test_event_platform_public import Base, mint  # noqa: E402  (the shared fakes and request helpers)

NATIVE = {"source": "native", "access": {"guestsAllowed": True, "membersOnly": False}}


class DefaultsTests(unittest.TestCase):
    def test_native_event_without_capability_fields_gets_safe_defaults(self):
        out = caps.normalize({"source": "native"})
        self.assertEqual(out["guestCheckout"], {"enabled": True})
        self.assertEqual(out["authentication"], {"required": False})
        for name in caps.ADDONS:
            self.assertEqual(out[name], {"enabled": False}, name)
        self.assertEqual(set(out), {"guestCheckout", "authentication", *caps.ADDONS})

    def test_guest_and_auth_follow_access_settings(self):
        no_guests = caps.normalize({"access": {"guestsAllowed": False}})
        self.assertEqual((no_guests["guestCheckout"]["enabled"], no_guests["authentication"]["required"]), (False, True))
        members = caps.normalize({"access": {"membersOnly": True, "guestsAllowed": True}})
        self.assertFalse(members["guestCheckout"]["enabled"])
        self.assertTrue(members["authentication"]["required"])

    def test_native_events_cannot_switch_on_unsupported_addons(self):
        event = {**NATIVE, "capabilities": {"seatReservation": {"enabled": True}}}
        self.assertFalse(caps.addon_enabled(event, "seatReservation"))

    def test_legacy_mirror_reads_the_events_table_switches(self):
        mirror = event_model.legacy_event_to_v2({"eventType": "fest_1", "eventName": "Fest", "status": "active", "eventInfo": {
            "seatSelection": {"enabled": True}, "seatUpgrade": {"enabled": False}, "celebrityDinner": {"enabled": True}}})
        c = caps.normalize(mirror)
        self.assertEqual({k: c[k]["enabled"] for k in caps.ADDONS},
                         {"seatReservation": True, "zoneUpgrade": False, "entrySlots": True, "celebrityDinner": True})

    def test_entry_slots_default_on_for_legacy_only_an_explicit_false_turns_them_off(self):
        self.assertTrue(caps.from_legacy_event_info({})["entrySlots"]["enabled"])
        self.assertTrue(caps.from_legacy_event_info({"entrySlots": {"enabled": "no"}})["entrySlots"]["enabled"])
        self.assertFalse(caps.from_legacy_event_info({"entrySlots": {"enabled": False}})["entrySlots"]["enabled"])

    def test_older_mirror_rows_without_capabilities_keep_working(self):
        old = {"source": "legacy", "eventId": "legacy-x"}
        c = caps.normalize(old)
        self.assertTrue(c["entrySlots"]["enabled"])
        self.assertFalse(c["seatReservation"]["enabled"] or c["zoneUpgrade"]["enabled"])

    def test_only_a_real_true_enables_a_legacy_addon(self):
        self.assertFalse(caps.from_legacy_event_info({"seatSelection": {"enabled": "true"}})["seatReservation"]["enabled"])
        self.assertFalse(caps.from_legacy_event_info({"seatSelection": "yes"})["seatReservation"]["enabled"])
        self.assertFalse(caps.from_legacy_event_info(None)["zoneUpgrade"]["enabled"])


class AdminValidationTests(unittest.TestCase):
    def test_validate_input(self):
        self.assertEqual(caps.validate_input(None), {})
        self.assertEqual(caps.validate_input({"seatReservation": {"enabled": False}}), {})
        self.assertIn("capabilities", caps.validate_input("on"))
        self.assertIn("capabilities.guestCheckout", caps.validate_input({"guestCheckout": {"enabled": True}}))
        self.assertIn("capabilities.zoneUpgrade", caps.validate_input({"zoneUpgrade": {"enabled": "yes"}}))
        self.assertIn("capabilities.entrySlots", caps.validate_input({"entrySlots": {"enabled": True}}))   # native: unsupported
        self.assertEqual(caps.validate_input({"entrySlots": {"enabled": True}}, legacy=True), {})

    def test_event_validation_and_edit_use_it(self):
        payload = {"title": "T", "slug": "t", "status": "draft", "capabilities": {"seatReservation": {"enabled": True}}}
        self.assertIn("capabilities.seatReservation", event_model.validate_event(payload))
        ok = {**payload, "capabilities": {"seatReservation": {"enabled": False}, "bogus": 1}}
        self.assertIn("capabilities.bogus", event_model.validate_event(ok))
        good = {**payload, "capabilities": {"seatReservation": {"enabled": False}}}
        self.assertEqual(event_model.validate_event(good), {})
        item = event_model.apply_event_edit({"eventId": "e"}, good)
        self.assertEqual(item["capabilities"], {"seatReservation": {"enabled": False}})
        self.assertNotIn("capabilities", event_model.apply_event_edit({"eventId": "e"}, {k: v for k, v in good.items() if k != "capabilities"}))

    def test_requested_addons(self):
        self.assertEqual(caps.requested_addons({}), [])
        self.assertEqual(caps.requested_addons({"addons": {"entrySlots": {"slotId": "s"}, "zoneUpgrade": None}}), ["entrySlots"])
        self.assertEqual(caps.requested_addons({"addons": "all"}), ["addons"])
        errors = caps.reject_unavailable_addons(NATIVE, {"addons": {"entrySlots": {"slotId": "s"}, "mystery": True}})
        self.assertEqual(set(errors), {"addons.entrySlots", "addons.mystery"})
        with self.assertRaises(caps.FeatureDisabled):
            caps.require_addon(NATIVE, "zoneUpgrade")


class EntrySlotGateTests(unittest.TestCase):
    def test_per_event_switch(self):
        on = entry_slots_logic.is_entry_slots_enabled
        self.assertTrue(on({}))
        self.assertTrue(on({"entrySlots": {"enabled": True}}))
        self.assertTrue(on(json.dumps({"seatSelection": {"enabled": True}})))
        self.assertTrue(on("not json"))
        self.assertFalse(on({"entrySlots": {"enabled": False}}))
        self.assertFalse(on(json.dumps({"entrySlots": {"enabled": False}})))

    def test_public_slot_listing_returns_nothing_when_disabled(self):
        env = {"ENTRY_SLOTS_TABLE": "slots", "EVENTS_TABLE": "events", "REGION": "ap-northeast-1"}
        with mock.patch.dict(os.environ, env):
            spec = importlib.util.spec_from_file_location("slots_handler", ROOT / "lambdas/get_available_slots/handler.py")
            handler = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(handler)
        slot = {"eventType": "fest_1", "slotId": "a", "status": "ACTIVE", "bookableCapacity": 10, "reservedSeats": 1, "name": "Early"}

        class Table:
            def __init__(self, name, info):
                self.name, self.info = name, info

            def get_item(self, Key):
                return {"Item": {"eventType": Key["eventType"], "eventInfo": self.info}}

            def query(self, **kwargs):
                return {"Items": [slot]}

        for info, expected in (({"entrySlots": {"enabled": False}}, 0), ({}, 1), ({"entrySlots": {"enabled": True}}, 1)):
            fake = mock.Mock()
            fake.Table.side_effect = lambda name, info=info: Table(name, info)
            with mock.patch.object(handler, "dynamodb", fake):
                res = handler.lambda_handler({"httpMethod": "GET", "queryStringParameters": {"eventType": "fest_1"}}, None)
            body = json.loads(res["body"])
            self.assertEqual((res["statusCode"], len(body["availableSlots"])), (200, expected), info)

    def test_registration_api_refuses_a_slot_when_the_event_has_them_off(self):
        env = {"REGION": "ap-northeast-1", "DYNAMODB_TABLE": "r", "EVENTS_TABLE": "e", "SES_FROM_EMAIL": "a@example.com",
               "ADMIN_EMAIL": "b@example.com"}
        with mock.patch.dict(os.environ, env), mock.patch.object(sys, "path", [str(ROOT / "lambdas/register")] + sys.path):
            spec = importlib.util.spec_from_file_location("register_handler", ROOT / "lambdas/register/handler.py")
            handler = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(handler)
        config = {"eventType": "fest_1", "status": "active", "eventInfo": {"entrySlots": {"enabled": False}}}
        request = {"httpMethod": "POST", "pathParameters": {"eventType": "fest_1"}, "headers": {},
                   "body": json.dumps({"name": "A", "email": "a@example.com", "slotId": "slot-1"})}
        with mock.patch.object(handler, "get_event_config", return_value=config):
            res = handler.lambda_handler(request, None)
        self.assertEqual(res["statusCode"], 400)
        self.assertEqual(json.loads(res["body"])["code"], "feature_disabled")


class PublicApiTests(Base):
    """Uses the shared event-platform fakes; adds a Cognito verifier backed by a throw-away key."""

    def setUp(self):
        super().setUp()
        from test_event_platform_public import public
        self.public = public
        verifier = cognito_jwt.CognitoVerifier(jf.REGION, jf.POOL, jf.CLIENT, fetcher=lambda url: jf.jwks("kid-1"))
        patcher = mock.patch.object(public, "_verifier", verifier)
        patcher.start()
        self.addCleanup(patcher.stop)

    def cognito(self, email="pat@example.com", **claims):
        return jf.mint({"email": email, **claims})

    def test_capabilities_are_in_the_listing_and_the_page(self):
        listing = self.call("GET", "events")[1]["events"][0]
        page = self.call("GET", "events/picnic")[1]["event"]
        for view in (listing, page):
            self.assertTrue(view["capabilities"]["guestCheckout"]["enabled"])
            self.assertFalse(view["capabilities"]["seatReservation"]["enabled"])
        self.store.events["e1"]["access"] = {"membersOnly": False, "guestsAllowed": False}
        page = self.call("GET", "events/picnic")[1]["event"]
        self.assertEqual((page["capabilities"]["guestCheckout"]["enabled"], page["capabilities"]["authentication"]["required"]), (False, True))

    def test_legacy_sourced_event_exposes_its_switches(self):
        legacy = event_model.legacy_event_to_v2({"eventType": "fest_1", "eventName": "Fest", "status": "active",
                                                  "eventInfo": {"seatSelection": {"enabled": True}, "entrySlots": {"enabled": False}}})
        self.store.events[legacy["eventId"]] = legacy
        body = self.call("GET", f"events/{legacy['slug']}")[1]
        self.assertTrue(body["legacy"])
        c = body["event"]["capabilities"]
        self.assertEqual((c["seatReservation"]["enabled"], c["entrySlots"]["enabled"], c["zoneUpgrade"]["enabled"]), (True, False, False))

    def test_registration_api_rejects_addons_the_event_does_not_offer(self):
        for addons in ({"seatReservation": {"seats": ["A1"]}}, {"entrySlots": {"slotId": "s1"}}, {"zoneUpgrade": {"tier": "gold"}}):
            status, body, _ = self.register({"addons": addons, "idempotencyKey": "x" + next(iter(addons))})
            self.assertEqual((status, body["code"]), (400, "feature_disabled"), addons)
            self.assertTrue(all(k.startswith("addons.") for k in body["fields"]))
        self.assertEqual(self.store.regs, {})
        self.assertEqual(self.register({"addons": {}, "idempotencyKey": "none"})[0], 201)      # empty add-ons are not a request

    def test_signed_in_registration_with_a_cognito_token(self):
        status, body, _ = self.register(token=self.cognito("m@example.com"))
        self.assertEqual(status, 201, body)
        reg = body["registration"]
        self.assertEqual((reg["memberEmail"], reg["accountSub"], reg["totalJpy"]), ("m@example.com", "sub-123", 1600))   # member price

    def test_bearer_prefix_is_accepted(self):
        status, body, _ = self.register(token="Bearer " + self.cognito("m@example.com"))
        self.assertEqual(status, 201, body)

    def test_guest_registration_stores_no_account_identifier(self):
        reg = self.register()[1]["registration"]
        self.assertNotIn("accountSub", reg)
        self.assertNotIn("memberEmail", reg)

    def test_guest_checkout_disabled_blocks_guests_but_not_accounts(self):
        self.store.events["e1"]["access"] = {"membersOnly": False, "guestsAllowed": False}
        status, body, _ = self.register()
        self.assertEqual((status, body["code"]), (403, "login_required"))
        self.assertEqual(self.register({"idempotencyKey": "acct"}, token=self.cognito())[0], 201)

    def test_unverified_email_cannot_register_but_can_browse(self):
        token = self.cognito(email_verified=False)
        status, body, _ = self.register(token=token)
        self.assertEqual((status, body["code"]), (401, "email_unverified"))
        page = self.call("GET", "events/picnic", token=token)[1]["event"]
        self.assertFalse(page["viewer"]["loggedIn"])
        self.assertEqual(self.store.regs, {})

    def test_bad_cognito_tokens_are_a_401_not_a_guest(self):
        wrong_pool = jf.mint({"iss": "https://cognito-idp.ap-northeast-1.amazonaws.com/other"})
        expired = jf.mint({"exp": int(time.time()) - 600})
        wrong_client = jf.mint({"aud": "another-client"})
        access_token = jf.mint({"token_use": "access"})
        for token in (wrong_pool, expired, wrong_client, access_token):
            status, body, _ = self.register(token=token)
            self.assertEqual((status, body["code"]), (401, "session_expired"))
        self.assertEqual(self.store.regs, {})

    def test_self_service_works_for_a_cognito_account_and_not_for_other_accounts(self):
        mine = self.cognito("pat@example.com")
        reg_id = self.register(token=mine)[1]["registration"]["registrationId"]
        listed = self.call("GET", "my/registrations", token=mine)
        self.assertEqual([r["registration"]["registrationId"] for r in listed[1]["registrations"]], [reg_id])
        other = self.cognito("someone@example.com", sub="sub-999")
        self.assertEqual(self.call("GET", f"my/registrations/{reg_id}", token=other)[0], 404)
        self.assertEqual(self.call("GET", "my/registrations")[0], 401)                      # no token at all

    def test_legacy_otp_tokens_still_work(self):
        status, body, _ = self.register(token=mint("m@example.com"))
        self.assertEqual(status, 201, body)
        self.assertNotIn("accountSub", body["registration"])

    def test_unconfigured_attendee_pool_rejects_jwts_but_not_guests(self):
        off = cognito_jwt.CognitoVerifier("", "", "")
        with mock.patch.object(self.public, "_verifier", off):
            self.assertEqual(self.register(token=self.cognito())[0], 401)
            self.assertEqual(self.register({"idempotencyKey": "g"})[0], 201)


if __name__ == "__main__":
    unittest.main()
