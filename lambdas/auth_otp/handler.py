"""
Lambda: passwordless email OTP for the public registration gate.

Routes (API Gateway proxy):
  POST /auth/request-otp   { "email": "..." }        -> emails a 6-digit code
  POST /auth/verify-otp    { "email": "...", "code": "123456" } -> { token }

A short-lived code is stored in the OTP DynamoDB table keyed by email, with a
TTL so DynamoDB auto-expires it. On successful verification we mint a compact
signed access token (HMAC) the SPA keeps for the browser session. The token is
NOT a Cognito credential — it only proves the email was verified for gating the
registration page.
"""

import base64
import hashlib
import hmac
import json
import logging
import os
import re
import secrets
import time
from datetime import datetime, timezone

import boto3
from botocore.exceptions import ClientError

# ── Clients ───────────────────────────────────────────────────────────────────
dynamodb = boto3.resource("dynamodb", region_name=os.environ["REGION"])
ses = boto3.client("ses", region_name=os.environ["REGION"])

OTP_TABLE = os.environ["OTP_TABLE"]
MEMBERS_TABLE = os.environ.get("MEMBERS_TABLE", "")
MEMBER_REGISTER_URL = os.environ.get(
    "MEMBER_REGISTER_URL", "https://www.wmfjapan.com/en/member/register"
)
FROM_EMAIL = os.environ["SES_FROM_EMAIL"]
# frontend/public/logo.png ships as a static file, so it's served at this
# exact path off the frontend's own domain (see Navbar.jsx's <img src="/logo.png">).
FRONTEND_URL = os.environ.get("FRONTEND_URL", "").rstrip("/")
TOKEN_SECRET = os.environ.get("OTP_TOKEN_SECRET", "")
ENVIRONMENT = os.environ.get("ENVIRONMENT", "prod")
LOG_LEVEL = os.environ.get("LOG_LEVEL", "INFO").upper()

# Tunables
OTP_TTL_SECONDS = int(os.environ.get("OTP_TTL_SECONDS", "600"))          # 10 min
OTP_RESEND_COOLDOWN = int(os.environ.get("OTP_RESEND_COOLDOWN", "30"))   # 30 s
OTP_MAX_ATTEMPTS = int(os.environ.get("OTP_MAX_ATTEMPTS", "5"))
TOKEN_TTL_SECONDS = int(os.environ.get("OTP_ACCESS_TTL", "86400"))       # 24 h

logger = logging.getLogger(__name__)
logger.setLevel(LOG_LEVEL)

# ── Helpers ───────────────────────────────────────────────────────────────────


def cors_headers(origin="*"):
    return {
        "Access-Control-Allow-Origin": origin,
        "Access-Control-Allow-Headers": "Content-Type,Authorization",
        "Access-Control-Allow-Methods": "OPTIONS,POST",
        "Content-Type": "application/json",
    }


def respond(status, body, origin="*"):
    return {
        "statusCode": status,
        "headers": cors_headers(origin),
        "body": json.dumps(body, default=str),
    }


def validate_email(email):
    return bool(re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", email))


def member_exists(email):
    """
    True if `email` is present in the members master table.

    Login is gated against this list: only known members receive an OTP.
    If MEMBERS_TABLE is unset (e.g. before the table is provisioned) we fail
    open so the login flow keeps working, and log a warning.
    """
    if not MEMBERS_TABLE:
        logger.warning("MEMBERS_TABLE not configured; skipping membership gate.")
        return True
    try:
        record = dynamodb.Table(MEMBERS_TABLE).get_item(Key={"email": email}).get("Item")
        return bool(record)
    except ClientError:
        logger.exception("Could not read members table for email=%s", email)
        # Fail open on infrastructure errors rather than locking everyone out.
        return True


def hash_code(email, code):
    """Store only a salted hash of the code, never the plaintext."""
    return hashlib.sha256(f"{email}:{code}:{TOKEN_SECRET}".encode("utf-8")).hexdigest()


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("utf-8").rstrip("=")


def _b64url_decode(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode(value + padding)


def mint_token(email):
    """Compact HMAC-signed token: base64(payload).base64(signature)."""
    payload = {"email": email, "exp": int(time.time()) + TOKEN_TTL_SECONDS}
    payload_b = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    sig = hmac.new(TOKEN_SECRET.encode("utf-8"), payload_b, hashlib.sha256).digest()
    return f"{_b64url(payload_b)}.{_b64url(sig)}"


def verify_token(token):
    """Return the payload dict if valid & unexpired, else None."""
    try:
        payload_part, sig_part = token.split(".", 1)
        payload_b = _b64url_decode(payload_part)
        expected = hmac.new(TOKEN_SECRET.encode("utf-8"), payload_b, hashlib.sha256).digest()
        if not hmac.compare_digest(expected, _b64url_decode(sig_part)):
            return None
        payload = json.loads(payload_b)
        if int(payload.get("exp", 0)) < int(time.time()):
            return None
        return payload
    except Exception:
        return None


def send_otp_email(email, code):
    subject = "Your WMF Japan Login Verification Code"
    # Explicit width+height (not just CSS) keeps Outlook's renderer, which
    # ignores height:auto, from distorting the logo's aspect ratio.
    logo_html = (
        f'<img src="{FRONTEND_URL}/logo.png" alt="WMF Japan" width="120" height="65" '
        f'style="display:block;margin:0 auto;border:0;max-width:120px;height:auto;" />'
        if FRONTEND_URL
        else '<span style="color:#cfe8da;font-size:20px;font-weight:800;">WMF Japan</span>'
    )
    html_body = f"""
<!DOCTYPE html>
<html>
<head><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1"></head>
<body style="margin:0;padding:0;background:#edf3ef;font-family:'Noto Sans','Helvetica Neue',Arial,sans-serif;">
<table width="100%" cellpadding="0" cellspacing="0" style="background:#edf3ef;padding:32px 0;">
<tr><td align="center">
<table width="480" cellpadding="0" cellspacing="0" style="background:#ffffff;border-radius:10px;overflow:hidden;border:1px solid #e3e9e6;box-shadow:0 6px 24px rgba(27,38,33,0.08);">
  <tr><td style="background:linear-gradient(180deg,#2a3832 0%,#1c2722 100%);padding:30px 30px 24px;text-align:center;">
    {logo_html}
    <p style="margin:14px 0 0;color:#cfe8da;font-size:12px;letter-spacing:0.12em;text-transform:uppercase;font-weight:700;">Login Verification</p>
  </td></tr>
  <tr><td style="padding:30px;text-align:center;">
    <h1 style="margin:0 0 12px;color:#1b2621;font-size:19px;font-weight:800;">Your Verification Code</h1>
    <p style="margin:0 0 18px;color:#5d6b65;font-size:13px;line-height:1.6;">
      Enter this code to log in to your WMF Japan account. It expires in {OTP_TTL_SECONDS // 60} minutes.
    </p>
    <div style="display:inline-block;background:#f1f5f3;border:1px dashed #69a785;border-radius:6px;padding:14px 26px;font-size:30px;font-weight:900;letter-spacing:0.35em;color:#1b2621;">
      {code}
    </div>
    <p style="margin:20px 0 0;color:#8b9993;font-size:12px;line-height:1.6;">
      If you didn't request this, you can safely ignore this email.
    </p>
  </td></tr>
  <tr><td style="padding:18px 30px;border-top:1px solid #e3e9e6;text-align:center;background:#f8faf9;">
    <p style="margin:0;color:#8b9993;font-size:12px;">
      Questions? Contact <a href="mailto:info@wmfjapan.com" style="color:#3d7258;font-weight:700;text-decoration:none;">info@wmfjapan.com</a>
    </p>
    <p style="margin:6px 0 0;color:#8b9993;font-size:11px;">&copy; 2026 WMF Japan</p>
  </td></tr>
  <tr><td style="padding:18px 30px;border-top:1px solid #e3e9e6;text-align:center;">
    <div style="margin:auto;padding:14px 16px;background:#edf7f1;">
      <p style="margin:0 0 6px;color:#1b2621;font-size:14px;font-weight:700;">Host Your Event With Us</p>
      <p style="margin:0;color:#5d6b65;font-size:13px;line-height:1.6;">
        For bookings and inquiries, contact <a href="mailto:hello@cloudnadi.com" style="color:#3d7258;text-decoration:none;font-weight:700;">hello@cloudnadi.com</a>.
      </p>
    </div>
  </td></tr>
</table>
</td></tr>
</table>
</body>
</html>
"""
    ses.send_email(
        Source=FROM_EMAIL,
        Destination={"ToAddresses": [email]},
        Message={
            "Subject": {"Data": subject},
            "Body": {"Html": {"Data": html_body}},
        },
    )


# ── Route handlers ────────────────────────────────────────────────────────────


def handle_request_otp(body, origin):
    email = body.get("email", "").strip().lower()
    if not email or not validate_email(email):
        return respond(400, {"error": "A valid email address is required."}, origin)

    # Gate: only emails present in the members master list may receive an OTP.
    # Everyone else is directed to the public membership registration flow.
    if not member_exists(email):
        logger.info("Login blocked — email not a known member: %s", email)
        return respond(
            404,
            {
                "error": "We couldn't find a membership for this email. Please register first.",
                "notRegistered": True,
                "registerUrl": MEMBER_REGISTER_URL,
            },
            origin,
        )

    table = dynamodb.Table(OTP_TABLE)
    now = int(time.time())

    # Enforce a resend cooldown so the endpoint can't be used to spam an inbox.
    try:
        existing = table.get_item(Key={"email": email}).get("Item")
        if existing and (now - int(existing.get("issuedAt", 0))) < OTP_RESEND_COOLDOWN:
            wait = OTP_RESEND_COOLDOWN - (now - int(existing.get("issuedAt", 0)))
            return respond(429, {"error": f"Please wait {wait}s before requesting a new code."}, origin)
    except ClientError:
        logger.exception("Could not read existing OTP for email=%s", email)

    code = f"{secrets.randbelow(1_000_000):06d}"

    item = {
        "email": email,
        "codeHash": hash_code(email, code),
        "issuedAt": now,
        "attempts": 0,
        "expiresAt": now + OTP_TTL_SECONDS,  # DynamoDB TTL attribute
        "createdAt": datetime.now(timezone.utc).isoformat(),
    }

    try:
        table.put_item(Item=item)
    except ClientError:
        logger.exception("Failed to store OTP for email=%s", email)
        return respond(500, {"error": "Could not generate a code. Please try again."}, origin)

    try:
        send_otp_email(email, code)
    except ClientError:
        logger.exception("Failed to email OTP to email=%s", email)
        return respond(500, {"error": "Could not send the code email. Please try again."}, origin)

    logger.info("OTP issued for email=%s", email)
    return respond(200, {"message": "A verification code has been sent to your email.", "cooldown": OTP_RESEND_COOLDOWN}, origin)


def handle_verify_otp(body, origin):
    email = body.get("email", "").strip().lower()
    code = str(body.get("code", "")).strip()

    if not email or not validate_email(email):
        return respond(400, {"error": "A valid email address is required."}, origin)
    if not re.fullmatch(r"\d{6}", code):
        return respond(400, {"error": "Enter the 6-digit code from your email."}, origin)

    table = dynamodb.Table(OTP_TABLE)

    try:
        record = table.get_item(Key={"email": email}).get("Item")
    except ClientError:
        logger.exception("Could not read OTP for email=%s", email)
        return respond(500, {"error": "Verification failed. Please try again."}, origin)

    now = int(time.time())
    if not record or int(record.get("expiresAt", 0)) < now:
        return respond(400, {"error": "Your code has expired. Please request a new one."}, origin)

    if int(record.get("attempts", 0)) >= OTP_MAX_ATTEMPTS:
        table.delete_item(Key={"email": email})
        return respond(429, {"error": "Too many attempts. Please request a new code."}, origin)

    if not hmac.compare_digest(record.get("codeHash", ""), hash_code(email, code)):
        # Count the failed attempt so brute-forcing is bounded.
        try:
            table.update_item(
                Key={"email": email},
                UpdateExpression="SET attempts = if_not_exists(attempts, :zero) + :one",
                ExpressionAttributeValues={":one": 1, ":zero": 0},
            )
        except ClientError:
            logger.exception("Could not increment OTP attempts for email=%s", email)
        return respond(400, {"error": "Incorrect code. Please check and try again."}, origin)

    # Success — consume the code so it can't be reused.
    try:
        table.delete_item(Key={"email": email})
    except ClientError:
        logger.exception("Could not delete used OTP for email=%s", email)

    token = mint_token(email)
    logger.info("OTP verified for email=%s", email)
    return respond(200, {"message": "Verified.", "token": token, "email": email, "expiresIn": TOKEN_TTL_SECONDS}, origin)


def lambda_handler(event, context):
    if event.get("httpMethod") == "OPTIONS":
        return respond(200, {})

    headers = {k.lower(): v for k, v in (event.get("headers") or {}).items()}
    origin = headers.get("origin", "*")

    if not TOKEN_SECRET:
        logger.error("OTP_TOKEN_SECRET is not configured.")
        return respond(500, {"error": "Server is not configured for OTP login."}, origin)

    resource_path = event.get("resource") or event.get("path") or ""

    try:
        body = json.loads(event.get("body") or "{}")
    except json.JSONDecodeError:
        return respond(400, {"error": "Invalid JSON in request body."}, origin)

    if resource_path.endswith("/request-otp"):
        return handle_request_otp(body, origin)
    if resource_path.endswith("/verify-otp"):
        return handle_verify_otp(body, origin)

    return respond(404, {"error": "Unknown auth route."}, origin)
