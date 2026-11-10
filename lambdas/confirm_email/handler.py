"""
Lambda: GET /confirm?token=<hmac_token>&id=<registration_id>
Public endpoint — called when user clicks the confirmation link in their email.
Updates emailConfirmed = True in DynamoDB and returns a simple HTML response.
"""

import hashlib
import hmac
import json
import os

import boto3
from botocore.exceptions import ClientError

dynamodb   = boto3.resource("dynamodb", region_name=os.environ["REGION"])
TABLE_NAME = os.environ["DYNAMODB_TABLE"]
EVENT_NAME = os.environ.get("EVENT_NAME", "Onam Party")
EVENT_DATE = os.environ.get("EVENT_DATE", "September 12, 2026")
EVENT_VENUE = os.environ.get("EVENT_VENUE", "Edogawa East Citizen Hall, Tokyo")
EVENT_ADDRESS = os.environ.get("EVENT_ADDRESS", "1 Chome-17-1 Higashimizue, Edogawa City, Tokyo 132-0014")
SECRET     = os.environ.get("CONFIRMATION_SECRET", "supersecretkey")  # Must match the one used when generating tokens


def verify_token(registration_id: str, token: str) -> bool:
    expected = hmac.new(
        SECRET.encode(),
        registration_id.encode(),
        hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(expected, token)


def html_response(status: int, title: str, message: str, emoji: str = "🪔") -> dict:
    html = f"""<!DOCTYPE html>
<html>
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>{title}</title>
  <link href="https://fonts.googleapis.com/css2?family=Cinzel:wght@700;900&family=Noto+Sans:wght@400;500&display=swap" rel="stylesheet">
  <style>
    body {{
      margin:0; background:#0d1f4e; font-family:'Noto Sans',sans-serif;
      display:flex; align-items:center; justify-content:center; min-height:100vh;
    }}
    .card {{
      background:#fff; max-width:480px; width:90%; border-radius:4px;
      overflow:hidden; box-shadow:0 8px 32px rgba(0,0,0,0.3); text-align:center;
    }}
    .top-bar {{ height:5px; background:linear-gradient(90deg,#f77f00 33%,#fff 33%,#fff 66%,#138808 66%); }}
    .header {{ background:#0d1f4e; padding:28px; }}
    .header h1 {{ margin:0; color:#f0c84a; font-family:'Cinzel',serif; font-size:22px; }}
    .header p  {{ margin:6px 0 0; color:#aab5cc; font-size:12px; }}
    .body {{ padding:32px; }}
    .emoji {{ font-size:48px; margin-bottom:16px; }}
    .body h2 {{ margin:0 0 12px; color:#0d1f4e; font-family:'Cinzel',serif; font-size:20px; }}
    .body p  {{ margin:0 0 16px; color:#555; font-size:14px; line-height:1.7; }}
    .bottom-bar {{ height:4px; background:linear-gradient(90deg,#f77f00 33%,#fff 33%,#fff 66%,#138808 66%); }}
  </style>
</head>
<body>
  <div class="card">
    <div class="top-bar"></div>
    <div class="header">
      <h1>{EVENT_NAME}</h1>
      <p>{EVENT_DATE} · {EVENT_VENUE}</p>
      <p>{EVENT_ADDRESS}</p>
    </div>
    <div class="body">
      <div class="emoji">{emoji}</div>
      <h2>{title}</h2>
      <p>{message}</p>
      <p style="font-size:18px;">🇮🇳 &nbsp; 🇯🇵</p>
    </div>
    <div class="bottom-bar"></div>
  </div>
</body>
</html>"""
    return {
        "statusCode": status,
        "headers": {"Content-Type": "text/html; charset=UTF-8"},
        "body": html,
    }


def lambda_handler(event, context):
    params = event.get("queryStringParameters") or {}
    token           = params.get("token", "").strip()
    registration_id = params.get("id", "").strip()

    if not token or not registration_id:
        return html_response(
            400,
            "Invalid Link",
            "This confirmation link is missing required parameters. "
            "Please check your email and try again.",
            "⚠️"
        )

    # Verify HMAC token
    if not verify_token(registration_id, token):
        return html_response(
            403,
            "Invalid Token",
            "This confirmation link is invalid or has been tampered with. "
            "Please contact us if you believe this is an error.",
            "❌"
        )

    # Fetch record
    table = dynamodb.Table(TABLE_NAME)
    try:
        response = table.get_item(Key={"registrationId": registration_id})
    except ClientError as e:
        print(f"DynamoDB error: {e}")
        return html_response(500, "Server Error", "Something went wrong. Please try again later.", "⚠️")

    item = response.get("Item")
    if not item:
        return html_response(404, "Not Found", "No registration found for this link.", "🔍")

    # Already confirmed?
    if item.get("emailConfirmed"):
        return html_response(
            200,
            "Already Confirmed",
            f"Your email has already been confirmed. "
            f"We look forward to seeing you at {EVENT_NAME}! 🌸",
            "✅"
        )

    # Mark confirmed
    try:
        table.update_item(
            Key={"registrationId": registration_id},
            UpdateExpression="SET emailConfirmed = :t",
            ExpressionAttributeValues={":t": True},
        )
    except ClientError as e:
        print(f"DynamoDB update error: {e}")
        return html_response(500, "Server Error", "Could not update your record. Please try again.", "⚠️")

    return html_response(
        200,
        "Email Confirmed!",
        f"Thank you, <strong>{item.get('firstName', '')}</strong>! "
        f"Your email address has been confirmed. "
        f"A payment link will be sent to your WhatsApp shortly to complete your registration for "
        f"<strong>{EVENT_NAME}</strong>.",
        "🌸"
    )
