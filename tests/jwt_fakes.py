"""Throw-away RSA key + Cognito-style token minting for tests (pure Python; no crypto library, no stored keys).

A fresh 1024-bit key is generated per test process. It exists only in memory, signs only test tokens
and is never written to disk, so there is nothing here for a secret scanner to find.
"""

import base64
import hashlib
import json
import random
import time
import zlib

POOL = "ap-northeast-1_TESTPOOL"
REGION = "ap-northeast-1"
CLIENT = "test-client-id"
ISSUER = f"https://cognito-idp.{REGION}.amazonaws.com/{POOL}"
_DIGEST_INFO = bytes.fromhex("3031300d060960864801650304020105000420")


def _is_prime(n, rng, rounds=16):
    if n < 2:
        return False
    for p in (2, 3, 5, 7, 11, 13, 17, 19, 23, 29):
        if n % p == 0:
            return n == p
    d, r = n - 1, 0
    while d % 2 == 0:
        d //= 2
        r += 1
    for _ in range(rounds):
        x = pow(rng.randrange(2, n - 1), d, n)
        if x in (1, n - 1):
            continue
        for _ in range(r - 1):
            x = pow(x, 2, n)
            if x == n - 1:
                break
        else:
            return False
    return True


def _prime(bits, rng):
    while True:
        candidate = rng.getrandbits(bits) | (1 << (bits - 1)) | 1
        if _is_prime(candidate, rng):
            return candidate


class Key:
    def __init__(self, kid, seed):
        rng = random.Random(seed)            # deterministic per kid so failures are reproducible
        e = 65537
        while True:
            p, q = _prime(512, rng), _prime(512, rng)
            phi = (p - 1) * (q - 1)
            if p != q and phi % e:
                break
        self.kid, self.n, self.e, self.d = kid, p * q, e, pow(e, -1, phi)

    def sign(self, message: bytes) -> bytes:
        size = (self.n.bit_length() + 7) // 8
        info = _DIGEST_INFO + hashlib.sha256(message).digest()
        em = b"\x00\x01" + b"\xff" * (size - len(info) - 3) + b"\x00" + info
        return pow(int.from_bytes(em, "big"), self.d, self.n).to_bytes(size, "big")

    def jwk(self):
        enc = lambda i: _b64(i.to_bytes((i.bit_length() + 7) // 8, "big"))
        return {"kty": "RSA", "kid": self.kid, "alg": "RS256", "use": "sig", "n": enc(self.n), "e": enc(self.e)}


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


_KEYS = {}


def key(kid="kid-1"):
    if kid not in _KEYS:
        _KEYS[kid] = Key(kid, seed=zlib.crc32(kid.encode()))
    return _KEYS[kid]


def jwks(*kids):
    return {"keys": [key(k).jwk() for k in (kids or ("kid-1",))]}


def mint(claims=None, *, kid="kid-1", alg="RS256", signer=None):
    now = int(time.time())
    body = {"sub": "sub-123", "iss": ISSUER, "aud": CLIENT, "token_use": "id", "email": "pat@example.com",
            "email_verified": True, "iat": now, "exp": now + 3600, **(claims or {})}
    body = {k: v for k, v in body.items() if v is not None}
    head = _b64(json.dumps({"alg": alg, "kid": kid}).encode())
    payload = _b64(json.dumps(body).encode())
    signature = (signer or key(kid)).sign(f"{head}.{payload}".encode())
    return f"{head}.{payload}.{_b64(signature)}"
