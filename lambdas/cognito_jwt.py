"""Verify Amazon Cognito ID tokens (RS256 JWTs) using only the standard library.

The Lambdas in this repo ship with boto3 only, so there is no JWT/crypto library to lean on. RSA
signature *verification* needs just modular exponentiation, which Python has built in:
RSASSA-PKCS1-v1_5 with SHA-256 is `pow(signature, e, n)` compared with the encoded digest.

What is checked (all of it, every call):
  * the token is three base64url parts and the header says alg=RS256 (``none`` / HS256 are refused),
  * the signing key is one of the pool's published keys (``kid``), fetched from the pool's JWKS URL,
  * the RSA signature,
  * ``iss`` is exactly this user pool, ``aud`` is exactly our app client, ``token_use`` is ``id``,
  * ``exp`` is in the future (and ``nbf`` / ``iat`` are not in the future, with a small clock skew).

Nothing here logs a token or a claim. Callers decide what to do with the claims (the public event
Lambda requires a verified e-mail address).
"""

import base64
import hashlib
import hmac
import json
import threading
import time
import urllib.request
from typing import Callable, Optional

# DER prefix of DigestInfo for SHA-256 (RFC 8017, section 9.2).
_SHA256_DIGEST_INFO = bytes.fromhex("3031300d060960864801650304020105000420")
CLOCK_SKEW_SECONDS = 120
JWKS_TTL_SECONDS = 3600
JWKS_MIN_REFETCH_SECONDS = 60
MAX_TOKEN_CHARS = 8192


class TokenError(Exception):
    """The token is not acceptable. ``code`` is a stable, non-sensitive reason."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def _b64url_decode(value: str) -> bytes:
    try:
        return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))
    except Exception:
        raise TokenError("malformed")


def _json_part(value: str) -> dict:
    try:
        parsed = json.loads(_b64url_decode(value))
    except (ValueError, TokenError):
        raise TokenError("malformed")
    if not isinstance(parsed, dict):
        raise TokenError("malformed")
    return parsed


def rsa_pkcs1_sha256_verify(n: int, e: int, message: bytes, signature: bytes) -> bool:
    """RSASSA-PKCS1-v1_5 / SHA-256 verification (constant-time compare of the encoded message)."""
    size = (n.bit_length() + 7) // 8
    if len(signature) != size:
        return False
    value = int.from_bytes(signature, "big")
    if value >= n:
        return False
    recovered = pow(value, e, n).to_bytes(size, "big")
    digest_info = _SHA256_DIGEST_INFO + hashlib.sha256(message).digest()
    if size < len(digest_info) + 11:
        return False
    expected = b"\x00\x01" + b"\xff" * (size - len(digest_info) - 3) + b"\x00" + digest_info
    return hmac.compare_digest(recovered, expected)


def fetch_jwks(url: str) -> dict:  # pragma: no cover - network; replaced in tests
    request = urllib.request.Request(url, headers={"Accept": "application/json"})
    with urllib.request.urlopen(request, timeout=5) as response:  # nosec - fixed https URL built from config
        return json.loads(response.read())


class CognitoVerifier:
    """Verifies ID tokens issued by one Cognito user pool for one app client."""

    def __init__(self, region: str, user_pool_id: str, client_id: str,
                 fetcher: Callable[[str], dict] = fetch_jwks, clock: Callable[[], float] = time.time):
        self.region, self.user_pool_id, self.client_id = region, user_pool_id, client_id
        self.issuer = f"https://cognito-idp.{region}.amazonaws.com/{user_pool_id}"
        self.jwks_url = f"{self.issuer}/.well-known/jwks.json"
        self._fetcher, self._clock = fetcher, clock
        self._keys: dict = {}
        self._fetched_at = 0.0
        self._lock = threading.Lock()

    @property
    def configured(self) -> bool:
        return bool(self.region and self.user_pool_id and self.client_id)

    def _load_keys(self, force: bool = False) -> dict:
        with self._lock:
            age = self._clock() - self._fetched_at
            stale = not self._keys or age > JWKS_TTL_SECONDS
            # An unknown `kid` may mean the pool rotated keys: refetch, but not more than once a minute.
            if stale or (force and age > JWKS_MIN_REFETCH_SECONDS):
                document = self._fetcher(self.jwks_url)
                self._keys = {k["kid"]: k for k in document.get("keys", []) if k.get("kty") == "RSA" and k.get("kid")}
                self._fetched_at = self._clock()
            return self._keys

    def _key_for(self, kid: str) -> dict:
        try:
            keys = self._load_keys()
            if kid not in keys:
                keys = self._load_keys(force=True)
        except TokenError:
            raise
        except Exception:
            raise TokenError("keys_unavailable")
        if kid not in keys:
            raise TokenError("unknown_key")
        return keys[kid]

    def verify_id_token(self, token: str) -> dict:
        """Return the verified claims, or raise TokenError."""
        if not self.configured:
            raise TokenError("not_configured")
        if not isinstance(token, str) or len(token) > MAX_TOKEN_CHARS:
            raise TokenError("malformed")
        parts = token.split(".")
        if len(parts) != 3 or not all(parts):
            raise TokenError("malformed")
        header, claims = _json_part(parts[0]), _json_part(parts[1])
        if header.get("alg") != "RS256":
            raise TokenError("bad_algorithm")
        kid = header.get("kid")
        if not isinstance(kid, str) or not kid:
            raise TokenError("unknown_key")
        jwk = self._key_for(kid)
        try:
            modulus = int.from_bytes(_b64url_decode(jwk["n"]), "big")
            exponent = int.from_bytes(_b64url_decode(jwk["e"]), "big")
        except (KeyError, TokenError):
            raise TokenError("unknown_key")
        if not rsa_pkcs1_sha256_verify(modulus, exponent, f"{parts[0]}.{parts[1]}".encode(), _b64url_decode(parts[2])):
            raise TokenError("bad_signature")

        now = self._clock()
        if claims.get("iss") != self.issuer:
            raise TokenError("bad_issuer")
        if claims.get("token_use") != "id":
            raise TokenError("bad_token_use")
        if claims.get("aud") != self.client_id:
            raise TokenError("bad_audience")
        try:
            expires = float(claims["exp"])
        except (KeyError, TypeError, ValueError):
            raise TokenError("malformed")
        if expires <= now - CLOCK_SKEW_SECONDS:
            raise TokenError("expired")
        for claim in ("nbf", "iat"):
            if claim in claims:
                try:
                    if float(claims[claim]) > now + CLOCK_SKEW_SECONDS:
                        raise TokenError("not_yet_valid")
                except (TypeError, ValueError):
                    raise TokenError("malformed")
        if not isinstance(claims.get("sub"), str) or not claims["sub"]:
            raise TokenError("malformed")
        return claims


def looks_like_jwt(token: str) -> bool:
    """Cheap routing test: our own OTP tokens have two parts, JWTs have three."""
    return isinstance(token, str) and token.count(".") == 2


def email_is_verified(claims: dict) -> bool:
    """Cognito emits email_verified as a boolean, but some federated paths deliver the string 'true'."""
    return str(claims.get("email_verified", "")).lower() == "true" and bool(str(claims.get("email", "")).strip())


def identity_from_claims(claims: dict) -> Optional[dict]:
    """{sub, email, name} for a verified-email token, else None."""
    if not email_is_verified(claims):
        return None
    return {"sub": claims["sub"], "email": str(claims["email"]).strip().lower(),
            "name": str(claims.get("name") or "").strip()}
