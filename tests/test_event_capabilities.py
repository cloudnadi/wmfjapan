"""Per-event capabilities (guest checkout, authentication, seat reservation, zone upgrade, entry slots):
safe defaults for older records, admin validation, API exposure, server-side refusal, Cognito sign-in on the public API."""

import json
import os
import sys
import time
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
for sub in ("lambdas", "plugins/seat-upgrade/backend/lambdas", "tests"):
    sys.path.insert(0, str(ROOT / sub))
os.environ.setdefault("REGION", "ap-northeast-1")
os.environ["ATTENDEE_USER_POOL_ID"] = "ap-northeast-1_TESTPOOL"
os.environ["ATTENDEE_APP_CLIENT_ID"] = "test-client-id"

import cognito_jwt  # noqa: E402
import event_capabilities as caps  # noqa: E402
import event_model  # noqa: E402
import jwt_fakes as jf  # noqa: E402
from test_event_platform_public import Base, mint  # noqa: E402  (the shared fakes and request helpers)


class DefaultsTests(unittest.TestCase):
    def test_event_without_capability_fields_gets_safe_defaults(self):
        out = caps.normalize({})
        self.assertEqual(out["guestCheckout"], {"enabled": True})
        self.assertEqual(out["authentication"], {"required": False})
        for name in caps.ADDONS:
            self.assertEqual(out[name], {"enabled": False}, name)
        self.assertEqual(set(out), {"guestCheckout", "authentication", *caps.ADDONS})

    def test_guest_and_auth_follow_access_settings(self):
        no_guests = caps.normalize({"access": {"guestsAllowed": False}})
        self.assertEqual((no_guests["guestCheckout"]["enabled"], no_guests["authentication"]["required"]), (False, True))
        self.assertTrue(caps.normalize({"access": {}})["guestCheckout"]["enabled"])

    def test_only_a_real_true_enables_an_addon(self):
        for junk in ("true", 1, "yes", None, {"enabled": "true"}):
            self.assertFalse(caps.addon_enabled({"capabilities": {"entrySlots": junk}}, "entrySlots"), junk)
        self.assertTrue(caps.addon_enabled({"capabilities": {"entrySlots": {"enabled": True}}}, "entrySlots"))
        self.assertFalse(caps.addon_enabled({"capabilities": "all"}, "entrySlots"))

    def test_the_three_addons_are_independent(self):
        event = {"capabilities": {"seatReservation": {"enabled": True}, "zoneUpgrade": {"enabled": False}}}
        out = caps.normalize(event)
        self.assertEqual([k for k in caps.ADDONS if out[k]["enabled"]], ["seatReservation"])


class AdminValidationTests(unittest.TestCase):
    def test_validate_input(self):
        self.assertEqual(caps.validate_input(None), {})
        self.assertEqual(caps.validate_input({"seatReservation": {"enabled": False}}), {})
        self.assertIn("capabilities", caps.validate_input("on"))
        self.assertIn("capabilities.guestCheckout", caps.validate_input({"guestCheckout": {"enabled": True}}))
        self.assertIn("capabilities.zoneUpgrade", caps.validate_input({"zoneUpgrade": {"enabled": "yes"}}))
        self.assertIn("capabilities.celebrityDinner", caps.validate_input({"celebrityDinner": {"enabled": True}}))

    def test_requested_addons_and_refusal(self):
        self.assertEqual(caps.requested_addons({}), [])
        self.assertEqual(caps.requested_addons({"addons": {"entrySlots": {"slotId": "s"}, "zoneUpgrade": None}}), ["entrySlots"])
        self.assertEqual(caps.requested_addons({"addons": "all"}), ["addons"])
        errors = caps.reject_unavailable_addons({}, {"addons": {"entrySlots": {"slotId": "s"}, "mystery": True}})
        self.assertEqual(set(errors), {"addons.entrySlots", "addons.mystery"})
        on = {"capabilities": {"entrySlots": {"enabled": True}}}
        self.assertEqual(caps.reject_unavailable_addons(on, {"addons": {"entrySlots": {"slotId": "s"}}}), {})
        with self.assertRaises(caps.FeatureDisabled):
            caps.require_addon({}, "zoneUpgrade")


class PublicApiTests(Base):
    """Uses the shared event-platform fakes (Base already installs a Cognito verifier backed by a throw-away key)."""

    def cognito(self, email="pat@example.com", **claims):
        return jf.mint({"email": email, **claims})

    def test_capabilities_are_in_the_listing_and_the_page(self):
        listing = self.call("GET", "events")[1]["events"][0]
        page = self.call("GET", "events/picnic")[1]["event"]
        for view in (listing, page):
            self.assertTrue(view["capabilities"]["guestCheckout"]["enabled"])
            self.assertFalse(view["capabilities"]["seatReservation"]["enabled"])
        self.store.events["e1"]["access"] = {"guestsAllowed": False}
        page = self.call("GET", "events/picnic")[1]["event"]
        self.assertEqual((page["capabilities"]["guestCheckout"]["enabled"], page["capabilities"]["authentication"]["required"]), (False, True))

    def test_registration_api_rejects_addons_the_event_does_not_offer(self):
        for addons in ({"seatReservation": {"seats": ["main-A1"]}}, {"entrySlots": {"slotId": "s1"}}, {"zoneUpgrade": {"up1": 1}}):
            status, body, _ = self.register({"addons": addons, "idempotencyKey": "x" + next(iter(addons))})
            self.assertEqual((status, body["code"]), (400, "feature_disabled"), addons)
            self.assertTrue(all(k.startswith("addons.") for k in body["fields"]))
        self.assertEqual(self.store.regs, {})
        self.assertEqual(self.register({"addons": {}, "idempotencyKey": "none"})[0], 201)      # empty add-ons are not a request

    def test_signed_in_registration_with_a_cognito_token(self):
        status, body, _ = self.register(token=self.cognito("m@example.com"))
        self.assertEqual(status, 201, body)
        reg = body["registration"]
        self.assertEqual((reg["accountEmail"], reg["accountSub"], reg["totalJpy"]), ("m@example.com", "sub-123", 3000))

    def test_bearer_prefix_is_accepted(self):
        self.assertEqual(self.register(token="Bearer " + self.cognito("m@example.com"))[0], 201)

    def test_guest_registration_stores_no_account_identifier(self):
        reg = self.register()[1]["registration"]
        self.assertNotIn("accountSub", reg)
        self.assertNotIn("accountEmail", reg)

    def test_guest_checkout_disabled_blocks_guests_but_not_accounts(self):
        self.store.events["e1"]["access"] = {"guestsAllowed": False}
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
        bad = [jf.mint({"iss": "https://cognito-idp.ap-northeast-1.amazonaws.com/other"}), jf.mint({"exp": int(time.time()) - 600}),
               jf.mint({"aud": "another-client"}), jf.mint({"token_use": "access"}), "garbage", "a.b"]
        for token in bad:
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

    def test_unconfigured_attendee_pool_rejects_tokens_but_not_guests(self):
        from test_event_platform_public import public
        off = cognito_jwt.CognitoVerifier("", "", "")
        with mock.patch.object(public, "_verifier", off):
            self.assertEqual(self.register(token=self.cognito())[0], 401)
            self.assertEqual(self.register({"idempotencyKey": "g"})[0], 201)


if __name__ == "__main__":
    unittest.main()
