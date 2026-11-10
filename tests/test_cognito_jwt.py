"""The stdlib RS256 verifier for Cognito ID tokens: accepts a good token and refuses every kind of bad one."""

import base64
import json
import os
import sys
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for sub in ("lambdas", "tests"):
    sys.path.insert(0, str(ROOT / sub))

import cognito_jwt  # noqa: E402
import jwt_fakes as jf  # noqa: E402


class VerifierTests(unittest.TestCase):
    def setUp(self):
        self.fetches = []
        self.now = time.time()

        def fetch(url):
            self.fetches.append(url)
            return jf.jwks("kid-1")

        self.v = cognito_jwt.CognitoVerifier(jf.REGION, jf.POOL, jf.CLIENT, fetcher=fetch, clock=lambda: self.now)

    def code(self, token):
        with self.assertRaises(cognito_jwt.TokenError) as ctx:
            self.v.verify_id_token(token)
        return ctx.exception.code

    def test_valid_token(self):
        claims = self.v.verify_id_token(jf.mint())
        self.assertEqual(claims["sub"], "sub-123")
        self.assertEqual(self.fetches, [f"{jf.ISSUER}/.well-known/jwks.json"])

    def test_keys_are_cached(self):
        self.v.verify_id_token(jf.mint())
        self.v.verify_id_token(jf.mint())
        self.assertEqual(len(self.fetches), 1)

    def test_unknown_kid_refetches_once_then_fails(self):
        self.v.verify_id_token(jf.mint())
        self.now += 120                                     # past the refetch throttle
        self.assertEqual(self.code(jf.mint(kid="kid-2")), "unknown_key")
        self.assertEqual(len(self.fetches), 2)
        self.assertEqual(self.code(jf.mint(kid="kid-2")), "unknown_key")   # throttled: no storm of fetches
        self.assertEqual(len(self.fetches), 2)

    def test_signature_from_another_key_is_refused(self):
        forged = jf.mint(signer=jf.key("kid-other"))        # right kid header, wrong private key
        self.assertEqual(self.code(forged), "bad_signature")

    def test_tampered_payload_is_refused(self):
        head, payload, sig = jf.mint().split(".")
        data = json.loads(base64.urlsafe_b64decode(payload + "=="))
        data["email"] = "admin@example.com"
        evil = base64.urlsafe_b64encode(json.dumps(data).encode()).rstrip(b"=").decode()
        self.assertEqual(self.code(f"{head}.{evil}.{sig}"), "bad_signature")

    def test_algorithm_confusion_is_refused(self):
        self.assertEqual(self.code(jf.mint(alg="none")), "bad_algorithm")
        self.assertEqual(self.code(jf.mint(alg="HS256")), "bad_algorithm")
        self.assertEqual(self.code(jf.mint().rsplit(".", 1)[0] + "."), "malformed")

    def test_claims_are_checked(self):
        self.assertEqual(self.code(jf.mint({"iss": "https://evil.example/pool"})), "bad_issuer")
        self.assertEqual(self.code(jf.mint({"aud": "someone-elses-client"})), "bad_audience")
        self.assertEqual(self.code(jf.mint({"token_use": "access"})), "bad_token_use")
        self.assertEqual(self.code(jf.mint({"exp": int(self.now) - 3600})), "expired")
        self.assertEqual(self.code(jf.mint({"nbf": int(self.now) + 3600})), "not_yet_valid")
        self.assertEqual(self.code(jf.mint({"sub": None})), "malformed")

    def test_garbage_is_refused(self):
        for junk in ("", "a.b", "a.b.c", "x" * 9000, None, 12):
            self.assertIn(self.code(junk), ("malformed",))

    def test_unconfigured_verifier_refuses_everything(self):
        v = cognito_jwt.CognitoVerifier("", "", "")
        with self.assertRaises(cognito_jwt.TokenError):
            v.verify_id_token(jf.mint())

    def test_jwks_outage_fails_closed(self):
        def boom(url):
            raise OSError("network down")

        v = cognito_jwt.CognitoVerifier(jf.REGION, jf.POOL, jf.CLIENT, fetcher=boom)
        with self.assertRaises(cognito_jwt.TokenError) as ctx:
            v.verify_id_token(jf.mint())
        self.assertEqual(ctx.exception.code, "keys_unavailable")


class IdentityTests(unittest.TestCase):
    def test_requires_verified_email(self):
        base = {"sub": "s", "email": "Pat@Example.com"}
        self.assertEqual(cognito_jwt.identity_from_claims({**base, "email_verified": True})["email"], "pat@example.com")
        self.assertIsNotNone(cognito_jwt.identity_from_claims({**base, "email_verified": "true"}))
        self.assertIsNone(cognito_jwt.identity_from_claims({**base, "email_verified": False}))
        self.assertIsNone(cognito_jwt.identity_from_claims({**base, "email_verified": "false"}))
        self.assertIsNone(cognito_jwt.identity_from_claims({"sub": "s", "email_verified": True}))

    def test_routing_between_token_formats(self):
        self.assertTrue(cognito_jwt.looks_like_jwt("a.b.c"))
        self.assertFalse(cognito_jwt.looks_like_jwt("payload.sig"))


if __name__ == "__main__":
    unittest.main()
