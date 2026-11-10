"""
Lambda: POST /events/{eventType}/register
- Fetches event configuration from the events table
- Validates the registration payload
- Stores record in DynamoDB registrations table
- Sends confirmation email to registrant (SES)
- Sends notification email to admin (SES)
"""

import json
import logging
import os
import secrets
import uuid
import re
from datetime import datetime, timezone
from typing import Optional

import boto3
from boto3.dynamodb.conditions import Key
from botocore.exceptions import ClientError

import onam_pricing
import npo_membership
import entry_slots_logic
import entry_allocations
import qr_email

# ── Clients ───────────────────────────────────────────────────────────────────
dynamodb = boto3.resource("dynamodb", region_name=os.environ["REGION"])
ses = boto3.client("ses",        region_name=os.environ["REGION"])

REGISTRATIONS_TABLE = os.environ["DYNAMODB_TABLE"]
EVENTS_TABLE = os.environ["EVENTS_TABLE"]
MEMBERSHIP_TABLE = os.environ.get("MEMBERSHIP_TABLE", "")
MEMBERS_TABLE = os.environ.get("MEMBERS_TABLE", "")
SITE_CONFIG_TABLE = os.environ.get("SITE_CONFIG_TABLE", "")
ENTRY_SLOTS_TABLE = os.environ.get("ENTRY_SLOTS_TABLE", "")
SLOT_RESERVATIONS_TABLE = os.environ.get("SLOT_RESERVATIONS_TABLE", "")
ENTRY_ALLOCATIONS_TABLE = os.environ.get("ENTRY_ALLOCATIONS_TABLE", "")
FROM_EMAIL = os.environ["SES_FROM_EMAIL"]
ADMIN_EMAIL = os.environ["ADMIN_EMAIL"]
ADMIN_CC = [e.strip() for e in os.environ.get(
    "ADMIN_EMAIL_CC", "").split(",") if e.strip()]
ENVIRONMENT = os.environ.get("ENVIRONMENT", "prod")
LOG_LEVEL = os.environ.get("LOG_LEVEL", "INFO").upper()

# Onam membership config (mirrors the membership Lambda). Membership validity is
# by date period; RENEWAL is the year a join/renew enrolls into. MEMBERSHIP_AS_OF
# optionally pins "today" for the validity check.
RENEWAL_MEMBERSHIP_YEAR = os.environ.get("RENEWAL_MEMBERSHIP_YEAR", "2026-27")
MEMBERSHIP_AS_OF = os.environ.get("MEMBERSHIP_AS_OF", "")
# The Onam event type this handler recognises for the dedicated pricing branch.
ONAM_EVENT_TYPE = os.environ.get("EVENT_TYPE_ONAM", "onam_2026")
# Max combined attendees across the 'onam' and 'combo' options (Onam Sadhya
# seating). 'event' (Celebrity-only) registrations don't count toward this.
ONAM_CAPACITY = int(os.environ.get("ONAM_CAPACITY", "350"))

# Seat pools shared by the registration options, as
# (event registration setting, options drawing on the pool, label). The capacity
# key caps the pool; the closed key is a manual admin switch that shuts the pool
# regardless of how many seats are left.
SEAT_POOLS = (
    ("sadhyaCapacity", "onamRegistrationClosed", ("onam", "combo"), "Onam Sadhya"),
    ("celebrityCapacity", "celebrityRegistrationClosed", ("event", "combo"), "Celebrity Event"),
)
# Attendee categories that occupy a seat (under-6s are lap seats).
SEATED_CATEGORIES = ("adults", "kids914", "kids68")

logger = logging.getLogger(__name__)
logger.setLevel(LOG_LEVEL)

# ── Helpers ───────────────────────────────────────────────────────────────────


def cors_headers(origin="*"):
    return {
        "Access-Control-Allow-Origin":  origin,
        "Access-Control-Allow-Headers": "Content-Type,Authorization",
        "Access-Control-Allow-Methods": "OPTIONS,POST,GET",
        "Content-Type": "application/json",
    }


def respond(status: int, body: dict, origin: str = "*"):
    return {
        "statusCode": status,
        "headers": cors_headers(origin),
        "body": json.dumps(body, default=str),
    }


def validate_email(email: str) -> bool:
    return bool(re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", email))


def validate_phone(phone: str) -> bool:
    # Require explicit country code: +<country code> <number>
    return bool(re.match(r"^\+\d{1,4}[\s\-]?\d[\d\s\-]{5,18}$", phone.strip()))


def get_event_config(event_type: str) -> Optional[dict]:
    """
    Fetch event configuration from the events table.
    Returns: dict with event metadata or None if not found
    """
    try:
        events_table = dynamodb.Table(EVENTS_TABLE)
        response = events_table.get_item(Key={"eventType": event_type})
        return response.get("Item")
    except ClientError:
        logger.exception("Error fetching event config for event_type=%s", event_type)
        return None


def get_registration_setting(key: str, default: str = "") -> str:
    if not SITE_CONFIG_TABLE:
        return default
    try:
        item = dynamodb.Table(SITE_CONFIG_TABLE).get_item(
            Key={"configKey": key}
        ).get("Item")
        return str(item.get("value", default)) if item else default
    except ClientError:
        logger.exception("Could not read registration setting %s", key)
        return default


def get_default_event_type() -> str:
    """Best-effort fallback for single-event setups when eventType is not provided."""
    try:
        events_table = dynamodb.Table(EVENTS_TABLE)
        response = events_table.scan(
            FilterExpression="#status = :active",
            ExpressionAttributeNames={"#status": "status"},
            ExpressionAttributeValues={":active": "active"},
            Limit=1,
        )
        items = response.get("Items", [])
        if items:
            return items[0].get("eventType", "")

        response = events_table.scan(Limit=1)
        items = response.get("Items", [])
        if items:
            return items[0].get("eventType", "")
    except ClientError:
        logger.exception("Error resolving default event type")
    return ""


def parse_non_negative_int(value, default=0):
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    if parsed < 0:
        return None
    return parsed


def parse_bool(value) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    if isinstance(value, (int, float)):
        return value != 0
    return False


def normalize_event_info(event_config: dict) -> dict:
    """Normalize eventInfo into a dictionary regardless of storage format."""
    event_info = event_config.get("eventInfo", {})

    if isinstance(event_info, str):
        event_info = event_info.strip()
        if not event_info:
            return {}
        try:
            parsed = json.loads(event_info)
            return parsed if isinstance(parsed, dict) else {}
        except json.JSONDecodeError:
            logger.warning("Could not parse eventInfo JSON for eventType=%s", event_config.get("eventType"))
            return {}

    return event_info if isinstance(event_info, dict) else {}


def get_event_registration_settings(event_config: dict) -> dict:
    """Return the selected event's registration controls."""
    registration = event_config.get("registration", {})
    return registration if isinstance(registration, dict) else {}


def sadhya_slot_window_status(registration: dict) -> str:
    """"not_started" | "open" | "ended" — from today's UTC date against the
    admin-configured sadhyaSlotStartDate/sadhyaSlotEndDate on
    event.registration (both optional "YYYY-MM-DD", inclusive; blank means
    no bound on that side). Gates picking or changing a Sadhya slot — see
    reserve_entry_allocations — separately from the registration deadline
    itself (closesOn, above): a registrant can still register while slot
    selection opens later or has closed, same non-retroactive spirit as
    per-slot bookingStartsAt/bookingEndsAt in entry_slots_logic.py.
    """
    today = datetime.now(timezone.utc).date()
    start = str(registration.get("sadhyaSlotStartDate", "")).strip()
    end = str(registration.get("sadhyaSlotEndDate", "")).strip()
    if start and today < datetime.strptime(start, "%Y-%m-%d").date():
        return "not_started"
    if end and today > datetime.strptime(end, "%Y-%m-%d").date():
        return "ended"
    return "open"


def seats_held_in_pool(existing_registration: Optional[dict], options: tuple[str, ...]) -> int:
    """Seats an existing registration already occupies in one seat pool."""
    if not existing_registration:
        return 0
    if existing_registration.get("registrationOption") not in options:
        return 0
    counts = existing_registration.get("attendeeCounts", {}) or {}
    return sum(int(counts.get(key, 0) or 0) for key in SEATED_CATEGORIES)


def normalize_event_date(raw_event_date) -> str:
    """Convert event dates into an ISO-like value suitable for sorting."""
    if raw_event_date is None:
        return ""

    value = str(raw_event_date).strip()
    if not value:
        return ""

    for fmt in (
        "%Y-%m-%d",
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%dT%H:%M:%S.%f",
        "%Y-%m-%dT%H:%M:%S%z",
        "%Y-%m-%dT%H:%M:%S.%f%z",
        "%B %d, %Y",
        "%b %d, %Y",
        "%d %B %Y",
        "%d %b %Y",
        "%Y/%m/%d",
    ):
        try:
            return datetime.strptime(value, fmt).date().isoformat()
        except ValueError:
            continue

    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).date().isoformat()
    except ValueError:
        logger.warning("Unrecognized event date format: %s", raw_event_date)
        return value


def resolve_event_date(event_config: dict, requested_event_date: str = "") -> str:
    event_info = normalize_event_info(event_config)
    raw_event_date = (
        requested_event_date
        or event_info.get("date")
        or event_config.get("eventDate")
        or event_config.get("date")
    )
    return normalize_event_date(raw_event_date)


def format_entry_slot_summary_text(registration_data: dict) -> str:
    """Plain-text entry-slot line(s) for admin notifications, one per allocation."""
    allocations = registration_data.get("allocations") or []
    if allocations:
        multiple = len(allocations) > 1
        lines = []
        for idx, allocation in enumerate(allocations, start=1):
            label = f"Entry Slot {idx}" if multiple else "Entry Slot"
            name = allocation.get("slotName") or f"Slot {idx}"
            lines.append(f"- {label}: {name} ({allocation.get('seatCount', 0)} seats)")
        return "\n".join(lines)
    return f"- Entry Slot: {registration_data.get('slotName', 'N/A')} ({registration_data.get('slotSeats', 0)} seats)"


def build_registration_details_html(registration_details: Optional[dict]) -> str:
    """
    Build an optional HTML block summarising the registration/order details
    (option, line items, amounts, membership, payment method). Returns an empty
    string when no registration details are available (e.g. standard RSVPs).
    """
    if not registration_details:
        return ""

    option_label = registration_details.get("registrationOptionLabel", "")
    payment_method = registration_details.get("paymentMethod", "")
    line_items = registration_details.get("lineItems") or []
    tickets_amount = registration_details.get("ticketsAmount")
    discount_applied = registration_details.get("discountApplied")
    npo_fee = registration_details.get("npoFee")
    total_amount = registration_details.get("totalAmount")
    membership = registration_details.get("membership") or {}
    allocations = registration_details.get("allocations") or []
    slot_name = registration_details.get("slotName", "")
    slot_seats = registration_details.get("slotSeats")

    rows = []

    if option_label:
        rows.append(
            f'<tr><td style="padding:6px 0;color:#5d6b65;font-size:13px;">Registration Option</td>'
            f'<td style="padding:6px 0;color:#1b2621;font-size:13px;font-weight:700;text-align:right;">{option_label}</td></tr>'
        )

    if allocations:
        multiple = len(allocations) > 1
        for idx, allocation in enumerate(allocations, start=1):
            label = f"Entry Slot {idx}" if multiple else "Entry Slot"
            name = allocation.get("slotName") or f"Slot {idx}"
            seats = allocation.get("seatCount")
            slot_value = f"{name} ({seats} seat{'s' if seats != 1 else ''})" if seats else name
            rows.append(
                f'<tr><td style="padding:6px 0;color:#5d6b65;font-size:13px;">{label}</td>'
                f'<td style="padding:6px 0;color:#1b2621;font-size:13px;font-weight:700;text-align:right;">{slot_value}</td></tr>'
            )
    elif slot_name:
        slot_value = f"{slot_name} ({slot_seats} seat{'s' if slot_seats != 1 else ''})" if slot_seats else slot_name
        rows.append(
            f'<tr><td style="padding:6px 0;color:#5d6b65;font-size:13px;">Entry Slot</td>'
            f'<td style="padding:6px 0;color:#1b2621;font-size:13px;font-weight:700;text-align:right;">{slot_value}</td></tr>'
        )

    # Line items (attendees / tickets).
    for li in line_items:
        label = li.get("label", "")
        quantity = li.get("quantity", 0)
        unit_price = li.get("unitPrice", 0)
        total = li.get("total", 0)
        rows.append(
            f'<tr><td style="padding:6px 0;color:#5d6b65;font-size:13px;">{label} '
            f'({quantity} × ¥{unit_price})</td>'
            f'<td style="padding:6px 0;color:#1b2621;font-size:13px;text-align:right;">¥{total}</td></tr>'
        )

    if tickets_amount is not None:
        rows.append(
            f'<tr><td style="padding:6px 0;color:#5d6b65;font-size:13px;">Tickets Subtotal</td>'
            f'<td style="padding:6px 0;color:#1b2621;font-size:13px;text-align:right;">¥{tickets_amount}</td></tr>'
        )

    if discount_applied:
        rows.append(
            f'<tr><td style="padding:6px 0;color:#5d6b65;font-size:13px;">NPO Discount</td>'
            f'<td style="padding:6px 0;color:#3d7258;font-size:13px;text-align:right;">−¥{discount_applied}</td></tr>'
        )

    if npo_fee:
        rows.append(
            f'<tr><td style="padding:6px 0;color:#5d6b65;font-size:13px;">NPO Membership Fee</td>'
            f'<td style="padding:6px 0;color:#1b2621;font-size:13px;text-align:right;">¥{npo_fee}</td></tr>'
        )

    if payment_method:
        rows.append(
            f'<tr><td style="padding:6px 0;color:#5d6b65;font-size:13px;">Payment Method</td>'
            f'<td style="padding:6px 0;color:#1b2621;font-size:13px;font-weight:700;text-align:right;">{payment_method}</td></tr>'
        )

    total_row = ""
    if total_amount is not None:
        total_row = (
            f'<tr><td style="padding:12px 0 0;color:#1b2621;font-size:15px;font-weight:700;border-top:2px solid #d5dfd9;">Total Amount</td>'
            f'<td style="padding:12px 0 0;color:#1b2621;font-size:15px;font-weight:900;text-align:right;border-top:2px solid #d5dfd9;">¥{total_amount}</td></tr>'
        )

    # Membership summary block.
    membership_html = ""
    action = str(membership.get("action", "none")).lower()
    if action in {"join", "renew"}:
        year = membership.get("year") or ""
        # valid_until = membership.get("validUntil") or ""
        action_label = "Renewed" if action == "renew" else "Joined"
        detail = f"NPO membership {action_label.lower()}"
        if year:
            detail += f" for {year}"
        # if valid_until:
        #     detail += f" (valid until {valid_until})"
        membership_html = (
            f'<div style="background:#edf7f1;border-left:4px solid #69a785;padding:12px 14px;margin:14px 0 0;border-radius:2px;">'
            f'<p style="margin:0;color:#1b2621;font-size:13px;font-weight:700;">🌿 Membership</p>'
            f'<p style="margin:4px 0 0;color:#5d6b65;font-size:13px;">{detail}.</p></div>'
        )
    elif membership.get("wasMember"):
        # valid_until = membership.get("validUntil") or ""
        detail = "You are an active NPO member"
        # if valid_until:
        #     detail += f" (valid until {valid_until})"
        membership_html = (
            f'<div style="background:#edf7f1;border-left:4px solid #69a785;padding:12px 14px;margin:14px 0 0;border-radius:2px;">'
            f'<p style="margin:0;color:#1b2621;font-size:13px;font-weight:700;">🌿 Membership</p>'
            f'<p style="margin:4px 0 0;color:#5d6b65;font-size:13px;">{detail}.</p></div>'
        )

    if not rows and not total_row and not membership_html:
        return ""

    return f"""
    <div style="background:#f1f5f3;border:1px solid #d5dfd9;padding:16px;margin:20px 0;border-radius:4px;">
      <p style="margin:0 0 10px;color:#1b2621;font-size:13px;font-weight:700;">Registration Summary:</p>
      <table width="100%" cellpadding="0" cellspacing="0">
        {''.join(rows)}
        {total_row}
            </table>
            {membership_html}
        </div>
        """


def build_celebrity_seating_note_html(registration_details: Optional[dict]) -> str:
        """Return the under-6 seating note for Celebrity Event registrations only."""
        details = registration_details or {}
        option_key = str(details.get("registrationOption") or "").strip().lower()
        option_label = str(details.get("registrationOptionLabel") or "").strip().lower()
        if option_key not in {"event", "combo"} and "celebrity" not in option_label:
                return ""

        return """
        <div style="margin:0 0 18px;padding:12px 14px;background:#fff5df;border:1px solid #f0cf7d;border-left:4px solid #d79a1e;border-radius:2px;">
            <p style="margin:0;color:#5f4a18;font-size:13px;line-height:1.6;">
                <strong>Please note:</strong> No separate seating will be provided for children under 6 years of age for the Celebrity Event.
            </p>
        </div>
        """


def _normalize_payment_method(method: str) -> str:
    """Map method aliases to canonical keys used in event payment links."""
    value = str(method or "").strip().lower()
    if value in {"credit", "card", "creditcard", "credit_card"}:
        return "credit"
    if value in {"paypal", "paypay"}:
        return "paypay"
    if value in {"bank", "banktransfer", "bank_transfer", "transfer"}:
        return "bank"
    return value


def _extract_payment_link_value(raw_value) -> str:
    """Extract a URL from either a raw string or a nested payment-link object."""
    if isinstance(raw_value, str):
        url = raw_value.strip()
        return url if url.startswith(("http://", "https://")) else ""
    if isinstance(raw_value, dict):
        for key in ("url", "link", "href", "paymentUrl", "paymentLink"):
            candidate = raw_value.get(key)
            if isinstance(candidate, str):
                url = candidate.strip()
                if url.startswith(("http://", "https://")):
                    return url
    return ""


def resolve_payment_links(event_config: dict) -> dict:
    """Resolve payment links from event config/eventInfo into method→URL map."""
    event_info = normalize_event_info(event_config)
    link_sources = [
        event_info.get("paymentLinks"),
        event_config.get("paymentLinks"),
        event_info.get("payment"),
        event_config.get("payment"),
    ]

    links = {}
    for source in link_sources:
        if not isinstance(source, dict):
            continue
        for key, raw_value in source.items():
            method = _normalize_payment_method(str(key))
            url = _extract_payment_link_value(raw_value)
            if method and url:
                links[method] = url
    return links


def build_payment_links_html(event_config: dict, selected_method: str = "") -> str:
    """Render a payment-links block for the confirmation email."""
    links = resolve_payment_links(event_config)
    selected = _normalize_payment_method(selected_method)
    labels = {
        "credit": "Credit Card",
        "paypay": "PayPay",
        "bank": "Bank Transfer",
    }

    if not links:
        method_label = labels.get(selected, selected.replace("_", " ").title()) if selected else "your selected method"
        return f"""
    <div style="background:#fff8eb;border:1px solid #efd6a9;padding:16px;margin:20px 0;border-radius:4px;">
      <p style="margin:0 0 8px;color:#5c3d12;font-size:13px;font-weight:700;">Payment</p>
      <p style="margin:0;color:#77582f;font-size:12px;line-height:1.5;">
        Payment links are not configured for this event yet. Please complete payment using <strong>{method_label}</strong>
        and follow the organizer's payment instructions.
      </p>
    </div>
    """

    ordered_methods = ["credit", "paypay", "bank"]
    ordered_methods.extend([m for m in links if m not in ordered_methods])

    rows = []
    for method in ordered_methods:
        url = links.get(method)
        if not url:
            continue
        label = labels.get(method, method.replace("_", " ").title())
        selected_badge = ""
        if selected and method == selected:
            selected_badge = (
                '<span style="margin-left:8px;background:#edf7f1;color:#2d6a4f;'
                'font-size:11px;font-weight:700;padding:2px 6px;border-radius:999px;">Selected</span>'
            )
        rows.append(
            f'<tr><td style="padding:7px 0;color:#1b2621;font-size:13px;font-weight:700;">'
            f'{label}{selected_badge}</td>'
            f'<td style="padding:7px 0;text-align:right;">'
            f'<a href="{url}" style="display:inline-block;background:#3d7258;color:#f3f8f5;'
            f'padding:8px 12px;text-decoration:none;border-radius:4px;font-size:12px;font-weight:700;">Pay Now</a>'
            f'</td></tr>'
        )

    if not rows:
        return ""

    helper_line = (
        f"Use the <strong>{labels.get(selected, selected.title() if selected else '')}</strong> link below to complete your payment."
        if selected
        else "Use any of the links below to complete your payment."
    )

    return f"""
    <div style="background:#fff8eb;border:1px solid #efd6a9;padding:16px;margin:20px 0;border-radius:4px;">
      <p style="margin:0 0 8px;color:#5c3d12;font-size:13px;font-weight:700;">Payment Links</p>
      <p style="margin:0 0 10px;color:#77582f;font-size:12px;line-height:1.5;">{helper_line}</p>
      <table width="100%" cellpadding="0" cellspacing="0">{''.join(rows)}</table>
    </div>
    """


def build_confirmation_email(
    registrant_name: str,
    event_config: dict,
    is_update: bool = False,
    event_date_override: str = "",
    registration_details: Optional[dict] = None,
) -> tuple[str, str]:
    """
    Build a confirmation email using event-specific metadata.
    Returns: (subject, html_body)
    """
    event_name = event_config.get("eventName", "Event")
    event_info = normalize_event_info(event_config)

    logger.info(
        "Building confirmation email for registrant=%s event=%s",
        registrant_name,
        event_name,
    )

    # resolved_event_date = event_date_override or event_info.get("date", "Unknown Date")

    # venue_name = event_config.get("venue") or "Unknown Venue"
    # venue_address = event_config.get("venueAddress") or "Unknown Address"
    # event_note = event_config.get("eventNote") or ""
    # description = event_config.get("description") or ""

    # details_lines = [
    #     f"📍 Venue: {venue_name}",
    #     f"🏠 Address: {venue_address}",
    #     f"📝 Description: {description}",
    # ]
    # if event_note:
    #     details_lines.append(f"ℹ️ Note: {event_note}")

    # event_details = "<br>".join(details_lines)

    registration_details_html = build_registration_details_html(registration_details)
    seating_note_html = build_celebrity_seating_note_html(registration_details)
    payment_links_html = build_payment_links_html(
        event_config,
        selected_method=(registration_details or {}).get("paymentMethod", ""),
    )
    # Explicit width+height (not just CSS) keeps Outlook's renderer, which
    # ignores height:auto, from distorting the logo's aspect ratio.
    logo_html = (
        f'<img src="{qr_email.FRONTEND_URL}/logo.png" alt="WMF Japan" width="100" height="54" '
        f'style="display:block;margin:0 auto 14px;border:0;max-width:100px;height:auto;" />'
        if qr_email.FRONTEND_URL
        else ""
    )

    subject_prefix = "Registration Updated" if is_update else "Registration Confirmed"
    subject = f"{subject_prefix} – {event_name}"

    html_body = f"""
<!DOCTYPE html>
<html>
<head><meta charset="UTF-8"></head>
<body style="margin:0;padding:0;background:#edf3ef;font-family:'Noto Sans','Helvetica Neue',Arial,sans-serif;">
<table width="100%" cellpadding="0" cellspacing="0" style="background:#edf3ef;padding:28px 0;">
<tr><td align="center">
<table width="620" cellpadding="0" cellspacing="0" style="background:#ffffff;border-radius:4px;overflow:hidden;border:2px solid #69a785;box-shadow:0 4px 20px rgba(27,38,33,0.14);">

  <!-- Header -->
  <tr><td style="background:linear-gradient(180deg, #2a3832 0%, #1c2722 100%);padding:0;">
    <div style="padding:28px 30px 24px;text-align:center;">
      {logo_html}
      <h1 style="margin:0;color:#cfe8da;font-size:30px;font-weight:900;letter-spacing:0.03em;line-height:1.15;">{event_name}</h1>
    </div>
  </td></tr>

  <!-- Body -->
  <tr><td style="padding:28px;">
    <p style="margin:0 0 10px;color:#1b2621;font-size:18px;font-weight:700;">Hello {registrant_name},</p>
    <p style="margin:0 0 18px;color:#5d6b65;font-size:14px;line-height:1.7;">
        Thank you for RSVPing to <strong>{event_name}</strong>! We look forward to seeing you!
    </p>
    
{seating_note_html}
{registration_details_html}
<!-- REGISTRATION_QR -->
{payment_links_html}

    <!--
    <div style="margin:20px 0;text-align:center;">
      <a href="https://wmfjapan.cloudnadi.com/parking-guide.html" style="display:inline-block;background:#69a785;color:#f3f8f5;padding:12px 24px;text-decoration:none;border-radius:4px;font-weight:700;font-size:14px;">🚗 View Full Parking Guide</a>
    </div>
    -->

    <!-- Optional additional info section (can be enabled if needed)
    <p style="margin:18px 0 0;color:#4c5d83;font-size:13px;line-height:1.6;">
      If you have any questions, feel free to reach out to us.
    </p>
    -->
  </td></tr>

  <!-- Footer -->
  <tr>
   <td style="background:#edf3ef;padding:18px 28px;border-top:1px solid #d5dfd9;text-align:center;">
    <p style="margin:0;color:#5d6b65;font-size:12px;">
      Questions? Contact <a href="mailto:info@wmfjapan.com" style="color:#3d7258;font-weight:700;text-decoration:none;">info@wmfjapan.com</a>
    </p>
    <p style="margin:6px 0 0;color:#5d6b65;font-size:12px;">©2026 WMF Japan</p>
  </td>
 </tr>
  <tr><td style="padding:18px 28px;border-top:1px solid #d5dfd9;text-align:center;">
        <div style="margin:auto;padding:14px 16px;background:#edf7f1;">
            <p style="margin:0 0 6px;color:#1b2621;font-size:14px;font-weight:700;">Host Your Event With Us</p>
            <p style="margin:0;color:#2a3832;font-size:13px;line-height:1.6;">
            For bookings and inquiries, contact
            <a href="mailto:hello@cloudnadi.com" style="color:#3d7258;text-decoration:none;font-weight:700;">hello@cloudnadi.com</a>.
            </p>
        </div>
  </td></tr>

</table></td></tr></table>
</body>
</html>
"""
    return subject, html_body


def reserve_entry_slot(event_type: str, slot_id: str, registration_id: str, attendee_counts: dict) -> Optional[dict]:
    """
    Attempt to reserve an entry slot for the registration.
    Returns the reservation record if successful, None if slot optional, or raises error.
    
    attendee_counts should have keys: adults, kids914, kids68, kidsUnder6
    """
    if not slot_id or not ENTRY_SLOTS_TABLE:
        # Slot selection is optional; if none provided, just skip
        return None

    try:
        # Calculate required seats using the business rule
        required_seats = entry_slots_logic.calculate_required_seats(
            adults=int(attendee_counts.get("adults", 0) or 0),
            kids9_to_14=int(attendee_counts.get("kids914", 0) or 0),
            kids6_to_8=int(attendee_counts.get("kids68", 0) or 0),
            kids_below_6=int(attendee_counts.get("kidsUnder6", 0) or 0),
        )

        # Atomically reserve seats in the slot
        reservation = entry_slots_logic.reserve_seats_atomic(
            event_type=event_type,
            slot_id=slot_id,
            registration_id=registration_id,
            required_seats=required_seats,
        )

        # Attach the slot's display name so emails can show it without a lookup.
        slot = entry_slots_logic.get_slot(event_type, slot_id)
        if slot:
            reservation = {**reservation, "slotName": slot.get("name", "")}
        return reservation

    except ValueError as exc:
        logger.warning(f"Slot validation error for {slot_id}: {exc}")
        raise ValueError(f"Slot validation failed: {exc}")
    except RuntimeError as exc:
        logger.warning(f"Slot reservation failed for {slot_id}: {exc}")
        raise ValueError(f"Slot reservation failed: {exc}")


def reserve_entry_allocations(event_type: str, registration_id: str, attendee_counts: dict, body: dict) -> list:
    """Reserve each requested slot and create one opaque pass per allocation.

    All-or-nothing: if any slot in the request fails, every reservation and
    allocation already created for this call is rolled back so a registration
    never ends up with only some of its passes.
    """
    event_config = get_event_config(event_type)
    if event_config and not entry_slots_logic.is_entry_slots_enabled(normalize_event_info(event_config)):
        raise ValueError("Entry slots are not available for this event.")
    registration = get_event_registration_settings(event_config) if event_config else {}
    window = sadhya_slot_window_status(registration)
    if window == "not_started":
        raise ValueError(f"Sadhya slot selection opens on {registration['sadhyaSlotStartDate']}.")
    if window == "ended":
        raise ValueError(f"Sadhya slot selection closed on {registration['sadhyaSlotEndDate']}.")

    required_seats = entry_slots_logic.calculate_required_seats_from_dict(attendee_counts)
    requested = entry_allocations.normalize_requested_allocations(body, required_seats)
    if not ENTRY_ALLOCATIONS_TABLE:
        raise ValueError("Entry allocations are not configured")

    created_items = []
    try:
        for allocation in requested:
            if len(requested) == 1 and allocation["seatCount"] == required_seats:
                reservation = reserve_entry_slot(
                    event_type, allocation["slotId"], registration_id,
                    attendee_counts,
                )
            else:
                reservation = entry_slots_logic.reserve_seats_atomic(
                    event_type=event_type,
                    slot_id=allocation["slotId"],
                    registration_id=registration_id,
                    required_seats=allocation["seatCount"],
                )
            slot = entry_slots_logic.get_slot(event_type, allocation["slotId"])
            item = entry_allocations.create_allocation_item(
                event_type, registration_id, allocation, slot, reservation
            )
            entry_allocations.persist_allocation(item)
            created_items.append(item)
        return [entry_allocations.public_allocation(item) for item in created_items]
    except (ValueError, RuntimeError, ClientError) as exc:
        for item in created_items:
            try:
                if item.get("reservationId"):
                    entry_slots_logic.cancel_reservation(item["reservationId"], event_type)
            except Exception:
                logger.exception("Could not release reservation during allocation rollback")
            entry_allocations.cancel_allocation(item)
        if isinstance(exc, ClientError):
            raise ValueError(f"Entry allocation failed: {exc}") from exc
        raise


def reconcile_entry_allocations(event_type: str, registration_id: str, existing_registration: Optional[dict], attendee_counts: dict, body: dict) -> Optional[list]:
    """Keep, replace, or create entry allocations for a create/update submission.

    Reuses existing allocations untouched when the request matches what's
    already on file (so an unrelated edit can't mint duplicate reservations or
    tokens). Otherwise releases the stale allocations first, so a changed
    attendee count or slot choice never leaves seats double-reserved.
    """
    required_seats = entry_slots_logic.calculate_required_seats_from_dict(attendee_counts)
    existing_allocations = (existing_registration or {}).get("allocations") or []
    wants_allocations = body.get("allocations") is not None or str(body.get("slotId", "")).strip()

    if not existing_allocations:
        if not wants_allocations:
            return None
        return reserve_entry_allocations(event_type, registration_id, attendee_counts, body)

    existing_signature = sorted(
        (str(a.get("entrySlotId", "")), int(a.get("seatCount", 0) or 0)) for a in existing_allocations
    )
    existing_total = sum(seat for _, seat in existing_signature)

    if wants_allocations:
        # Validated against the *current* attendee count before anything existing is touched.
        requested = entry_allocations.normalize_requested_allocations(body, required_seats)
        requested_signature = sorted((a["slotId"], a["seatCount"]) for a in requested)
    elif existing_total == required_seats:
        return existing_allocations
    else:
        raise ValueError(
            f"Attendee count changed to {required_seats} seated people; "
            "please reselect your entry slot allocation."
        )

    if requested_signature == existing_signature:
        return existing_allocations

    # Slot choice or seat split changed: release the stale passes before creating new ones.
    for allocation in existing_allocations:
        full_item = entry_allocations.get_allocation(event_type, allocation.get("allocationId", ""))
        if not full_item:
            continue
        if full_item.get("reservationId"):
            try:
                entry_slots_logic.cancel_reservation(full_item["reservationId"], event_type)
            except Exception:
                logger.exception("Could not release stale reservation during reallocation")
        entry_allocations.cancel_allocation(full_item)

    return reserve_entry_allocations(event_type, registration_id, attendee_counts, body)


def send_confirmation_email(
    registrant_email: str,
    registrant_name: str,
    event_config: dict,
    is_update: bool = False,
    event_date_override: str = "",
    registration_details: Optional[dict] = None,
) -> bool:
    """Send confirmation email to registrant."""
    try:
        subject, html_body = build_confirmation_email(
            registrant_name,
            event_config,
            is_update=is_update,
            event_date_override=event_date_override,
            registration_details=registration_details,
        )

        qr_email.send_html_email(
            ses, FROM_EMAIL, [registrant_email], subject, html_body, registration_details,
            event_name=event_config.get("eventName", "Event"),
        )
        return True
    except ClientError:
        logger.exception("Error sending confirmation email to email=%s", registrant_email)
        return False


def send_admin_notification(registration_data: dict, event_config: dict, is_update: bool = False) -> bool:
    """Send admin notification email."""
    try:
        event_name = event_config.get("eventName", "Event")
        subject_prefix = "Update Registration" if is_update else "New Registration"
        body_prefix = f"Registration update for {event_name}" if is_update else f"New registration received for {event_name}"
        subject = f"{subject_prefix}: {event_name}"
        attendee_counts = registration_data.get('attendeeCounts', {})

        body = f"""
{body_prefix}:
- Name: {registration_data.get('name', 'N/A')}
- Email: {registration_data.get('email', 'N/A')}
- Phone: {registration_data.get('phone', 'N/A')}
{format_entry_slot_summary_text(registration_data)}
- Attendee Counts:
    - Adults: {attendee_counts.get('adult', 0)}
    - Teens: {attendee_counts.get('teen', 0)}
    - Children: {attendee_counts.get('child', 0)}
    - Toddlers: {attendee_counts.get('toddler', 0)}
- Registered At: {registration_data.get('submittedAt', 'N/A')}
- Updated At: {registration_data.get('updatedAt', 'N/A')}
"""

        recipients = [ADMIN_EMAIL] + ADMIN_CC
        ses.send_email(
            Source=FROM_EMAIL,
            Destination={"ToAddresses": recipients},
            Message={
                "Subject": {"Data": subject},
                "Body": {"Text": {"Data": body}},
            },
        )
        return True
    except ClientError:
        logger.exception("Error sending admin notification")
        return False


# ── Onam-specific registration (server-authoritative pricing) ─────────────────


def get_capacity_used(event_type: str, options: tuple[str, ...], exclude_registration_id: str = "") -> int:
    """Sum seated attendees for registration options in one event capacity pool."""
    table = dynamodb.Table(REGISTRATIONS_TABLE)
    option_values = {f":o{index}": option for index, option in enumerate(options)}
    scan_kwargs = {
        "FilterExpression": "eventType = :et AND registrationOption IN (" + ", ".join(option_values) + ")",
        "ExpressionAttributeValues": {":et": event_type, **option_values},
    }
    total = 0
    while True:
        try:
            response = table.scan(**scan_kwargs)
        except ClientError:
            logger.exception("Could not scan registrations for Onam capacity check")
            break
        for item in response.get("Items", []):
            if exclude_registration_id and item.get("registrationId") == exclude_registration_id:
                continue
            counts = item.get("attendeeCounts", {}) or {}
            total += sum(
                int(counts.get(key, 0) or 0)
                for key in ("adults", "kids914", "kids68")
            )
        if "LastEvaluatedKey" not in response:
            break
        scan_kwargs["ExclusiveStartKey"] = response["LastEvaluatedKey"]
    return total


def email_already_availed_discount(email: str, event_type: str, exclude_registration_id: str = "") -> bool:
    """True if `email` already received an NPO discount on another registration
    for this event — either as the registrant or as someone else's additional
    adult. One email can now hold several registrations, but the NPO discount
    is a one-time benefit per event, not one per registration.
    """
    email = (email or "").strip().lower()
    if not email:
        return False

    table = dynamodb.Table(REGISTRATIONS_TABLE)

    # As the registrant: a cheap, indexed lookup.
    try:
        response = table.query(
            IndexName="EmailIndex",
            KeyConditionExpression=Key("email").eq(email),
        )
        for item in response.get("Items", []):
            if item.get("eventType") != event_type:
                continue
            if exclude_registration_id and item.get("registrationId") == exclude_registration_id:
                continue
            option = item.get("registrationOption", "")
            tier = item.get("pricingTier", "none")
            if onam_pricing.DISCOUNT_PCT.get(tier, {}).get(option, 0) > 0:
                return True
    except ClientError:
        logger.exception("Could not check prior NPO discount usage for email=%s", email)

    # As an additional adult on someone else's registration: not indexed, so
    # scan within the event (mirrors the capacity scan above).
    scan_kwargs = {
        "FilterExpression": "eventType = :et",
        "ExpressionAttributeValues": {":et": event_type},
    }
    try:
        while True:
            response = table.scan(**scan_kwargs)
            for item in response.get("Items", []):
                if exclude_registration_id and item.get("registrationId") == exclude_registration_id:
                    continue
                option = item.get("registrationOption", "")
                for adult in item.get("additionalAdults") or []:
                    if not adult.get("isMember"):
                        continue
                    if str(adult.get("email", "")).strip().lower() != email:
                        continue
                    if onam_pricing.DISCOUNT_PCT.get("member", {}).get(option, 0) > 0:
                        return True
            if "LastEvaluatedKey" not in response:
                break
            scan_kwargs["ExclusiveStartKey"] = response["LastEvaluatedKey"]
    except ClientError:
        logger.exception("Could not scan prior NPO discount usage (additional adult) for email=%s", email)

    return False


def get_member_record(email: str) -> Optional[dict]:
    """Resolve email → members-table record (memberid, npoMembershipActive)."""
    if not MEMBERS_TABLE or not email:
        return None
    try:
        return dynamodb.Table(MEMBERS_TABLE).get_item(Key={"email": email}).get("Item")
    except ClientError:
        logger.exception("Could not read members table for email=%s", email)
        return None


def is_active_member(email: str) -> bool:
    """True if `email` is a current NPO member (flag active + a period covering today).

    Used to price ADDITIONAL adults: each is discounted on their own membership,
    but cannot join/renew through this flow, so only the "member" state matters.
    """
    email = (email or "").strip().lower()
    if not email or not validate_email(email):
        return False
    member_record = get_member_record(email)
    if not member_record:
        return False
    memberid = str(member_record.get("memberid", ""))
    flag_active = bool(member_record.get("npoMembershipActive"))
    if not (memberid and flag_active):
        return False
    transactions = get_membership_transactions(memberid)
    summary = npo_membership.evaluate(
        transactions, as_of=MEMBERSHIP_AS_OF, renewal_year=RENEWAL_MEMBERSHIP_YEAR
    )
    return bool(summary["validNow"])


def get_membership_transactions(memberid: str) -> list:
    """All npo_membership transactions for a memberid (empty if none/unset)."""
    if not MEMBERSHIP_TABLE or not memberid:
        return []
    try:
        resp = dynamodb.Table(MEMBERSHIP_TABLE).query(
            KeyConditionExpression=Key("memberid").eq(str(memberid))
        )
        return resp.get("Items", [])
    except ClientError:
        logger.exception("Could not query membership for memberid=%s", memberid)
        return []


def next_transaction_no(memberid: str, transactions: list) -> str:
    """Next sequential transaction number for a member (max existing + 1)."""
    highest = 0
    for txn in transactions or []:
        try:
            highest = max(highest, int(str(txn.get("transactionNo", "0"))))
        except (TypeError, ValueError):
            continue
    return str(highest + 1)


def record_membership_transaction(memberid: str, txn: dict) -> bool:
    # """Append a paid NPO transaction and flip the members flag active."""
    # if not MEMBERSHIP_TABLE or not memberid:
    #     logger.warning("Cannot persist membership: table/memberid missing (memberid=%s)", memberid)
    #     return False
    # try:
    #     dynamodb.Table(MEMBERSHIP_TABLE).put_item(Item=txn)
    # except ClientError:
    #     logger.exception("Could not persist membership txn for memberid=%s", memberid)
    #     return False
    """Flip the members flag active."""
    if not memberid:
        logger.warning("Cannot persist membership: memberid missing (memberid=%s)", memberid)
        return False

    # Reflect active status on the master record so future logins see it.
    if MEMBERS_TABLE and txn.get("email"):
        try:
            dynamodb.Table(MEMBERS_TABLE).update_item(
                Key={"email": txn["email"]},
                UpdateExpression="SET npoMembershipActive = :t, memberid = if_not_exists(memberid, :m)",
                ExpressionAttributeValues={":t": True, ":m": str(memberid)},
            )
        except ClientError:
            logger.exception("Could not flip npoMembershipActive for %s", txn.get("email"))
    return True


def send_onam_admin_notification(record: dict, order: dict, event_config: dict, is_update: bool) -> bool:
    """Plain-text admin notification for an Onam registration."""
    try:
        event_name = event_config.get("eventName", "Onam Celebration 2026")
        subject = f"{'Update' if is_update else 'New'} Onam registration: {order['optionLabel']}"
        lines = "\n".join(
            f"    - {li['label']}: {li['quantity']} × ¥{li['unitPrice']} = ¥{li['total']}"
            for li in order["lineItems"]
        )
        membership = record.get("membership", {})
        body = f"""{'Updated' if is_update else 'New'} registration for {event_name}:
- Name: {record.get('name', 'N/A')}
- Email: {record.get('email', 'N/A')}
- Phone: {record.get('phone', 'N/A')}
- Option: {order['optionLabel']} ({order['pricingTier']} pricing)
{format_entry_slot_summary_text(record)}
- Attendees:
{lines}
- Tickets: ¥{order['ticketsAmount']}
- NPO discount applied: ¥{order['discountApplied']}
- NPO membership: was_member={membership.get('wasMember')} action={membership.get('action')} year={membership.get('year')} fee=¥{order['npoFee']}
- TOTAL: ¥{order['totalAmount']}
- Payment method: {record.get('paymentMethod', 'N/A')}
- Registered at: {record.get('submittedAt', 'N/A')}
"""
        ses.send_email(
            Source=FROM_EMAIL,
            Destination={"ToAddresses": [ADMIN_EMAIL] + ADMIN_CC},
            Message={"Subject": {"Data": subject}, "Body": {"Text": {"Data": body}}},
        )
        return True
    except ClientError:
        logger.exception("Error sending Onam admin notification")
        return False


def handle_onam_registration(body: dict, event_type: str, event_config: dict, origin: str):
    """Dedicated Onam flow: recompute pricing server-side and persist membership.

    Never trusts client-sent amounts. The option, counts, and membership state
    determine the authoritative total; the ¥1000 fee and adults-only NPO
    discount are applied here.
    """
    name = body.get("fullName", "").strip() or body.get("name", "").strip()
    email = body.get("email", "").strip().lower()
    phone = body.get("phone", "").strip()
    option = body.get("registrationOption", "")
    slot_id = str(body.get("slotId", "")).strip() or None
    counts = body.get("counts") or {}
    payment_method = body.get("paymentMethod", "").strip()
    # Emails of the additional adults (adults 2…N). The registrant is adult 1.
    additional_adult_emails = body.get("additionalAdultEmails") or []
    client_membership = body.get("membership") or {}
    requested_action = str(client_membership.get("action", "none")).lower()
    update_existing = parse_bool(body.get("updateExisting", False))

    # Basic field validation.
    if not name:
        return respond(400, {"error": "Name is required", "fields": {"fullName": "Name is required"}}, origin)
    if not email or not validate_email(email):
        return respond(400, {"error": "Valid email is required", "fields": {"email": "Valid email is required"}}, origin)
    if phone and not validate_phone(phone):
        return respond(400, {"error": "Phone must include country code (e.g. +81 90-1234-5678)", "fields": {"phone": "Use format: +country code and number"}}, origin)

    # One email can hold several registrations for the same event (e.g.
    # different options for different family groups), so email + eventType no
    # longer identifies a single record. A specific registration is only
    # targeted for update when the client explicitly asks for one by id.
    registrations_table = dynamodb.Table(REGISTRATIONS_TABLE)
    requested_registration_id = str(body.get("registrationId", "")).strip()
    existing_registration = None
    if requested_registration_id and update_existing:
        try:
            candidate = registrations_table.get_item(
                Key={"registrationId": requested_registration_id}
            ).get("Item")
            if (
                candidate
                and str(candidate.get("email", "")).strip().lower() == email
                and candidate.get("eventType") == event_type
            ):
                existing_registration = candidate
        except ClientError:
            logger.exception(
                "Could not load registration to update id=%s", requested_registration_id
            )

    # A completed payment locks the registration against self-service changes;
    # only an admin can amend a paid record.
    if existing_registration and str(existing_registration.get("paymentStatus", "")).strip().lower() == "paid":
        return respond(
            403,
            {
                "error": "This registration is paid and can no longer be edited. "
                         "Please contact us if you need to make a change.",
                "locked": True,
            },
            origin,
        )

    exclude_registration_id = existing_registration.get("registrationId", "") if existing_registration else ""

    # Determine authoritative membership state from the DB, not the client.
    # 1. Resolve email → memberid + npoMembershipActive via the members table.
    # 2. Validate by transaction period (a covering [from,to] range).
    member_record = get_member_record(email)
    memberid = str(member_record.get("memberid", "")) if member_record else ""
    flag_active = bool(member_record.get("npoMembershipActive")) if member_record else False
    transactions = get_membership_transactions(memberid) if memberid else []
    summary = npo_membership.evaluate(
        transactions, as_of=MEMBERSHIP_AS_OF, renewal_year=RENEWAL_MEMBERSHIP_YEAR
    )
    already_member = flag_active and summary["validNow"]
    covers_renewal = summary["coversRenewal"]

    # A join/renew only counts if the user asked for it AND it's still available.
    wants_join = requested_action in {"join", "renew"}
    do_join_or_renew = wants_join and not covers_renewal
    resolved_action = ("renew" if already_member else "join") if do_join_or_renew else "none"

    # Discount tier follows the confirmed rules (adults-only percentages):
    #   full   → renewed/joined for the target year (Onam+Event, combo 20%)
    #   member → valid member, no renewal (Onam 10%, combo 10%, event 0%)
    #   none   → not a member and not joining
    discount_tier = onam_pricing.resolve_discount_tier(
        valid_now=already_member,
        covers_renewal=covers_renewal,
        do_join_or_renew=do_join_or_renew,
    )

    # The NPO discount is a one-time benefit per event: if this email already
    # drew a discount on another registration for this event, it doesn't get
    # a second one here (joining/renewing membership itself is unaffected).
    if discount_tier != "none" and email_already_availed_discount(
        email, event_type, exclude_registration_id=exclude_registration_id
    ):
        discount_tier = "none"

    def _additional_adult_is_discountable(addl_email: str) -> bool:
        return is_active_member(addl_email) and not email_already_availed_discount(
            addl_email, event_type, exclude_registration_id=exclude_registration_id
        )

    # Per-adult tiers: adult 1 is the registrant (full tier logic above);
    # adults 2…N are each priced on their OWN membership — "member" if their
    # email is a current NPO member (and hasn't already used the discount
    # elsewhere for this event), "none" otherwise. They can't join/renew here,
    # so they never reach the "full" tier. Each additional email must be valid
    # and distinct (from the registrant and each other) — a reused address is
    # rejected rather than silently discounted twice.
    adult_count = onam_pricing._int(counts.get("adults", 0)) or 0
    adult_tiers, additional_adults, adult_err = onam_pricing.resolve_additional_adults(
        additional_adult_emails,
        adult_count,
        discount_tier,
        registrant_email=email,
        is_member=_additional_adult_is_discountable,
    )
    if adult_err:
        return respond(400, {"error": adult_err["error"], "fields": adult_err["fields"]}, origin)

    # Recompute the order authoritatively.
    order, err = onam_pricing.compute_order(
        option,
        counts,
        discount_tier=discount_tier,
        adult_tiers=adult_tiers,
        apply_npo_fee=do_join_or_renew,
    )
    if err:
        return respond(400, {"error": err}, origin)
    if order["totalAttendees"] < 1:
        return respond(400, {"error": "Add at least one attendee", "fields": {"counts": "Add at least one attendee"}}, origin)

    event_date = resolve_event_date(event_config)

    registration = get_event_registration_settings(event_config)
    expiry = str(registration.get("closesOn", "")).strip()
    # Preserve existing Onam behavior until its event record is next saved.
    if not expiry and event_type == ONAM_EVENT_TYPE:
        expiry = get_registration_setting("onam_registration_expiry")
    if expiry and datetime.now(timezone.utc).date() > datetime.strptime(expiry, "%Y-%m-%d").date():
        return respond(
            400,
            {
                "error": "Registrations are closed because the registration deadline has passed.",
                "registrationClosed": True,
            },
            origin,
        )

    capacity_attendees = sum(
        int(counts.get(key, 0) or 0)
        for key in SEATED_CATEGORIES
    )
    for capacity_key, closed_key, options, label in SEAT_POOLS:
        if order["option"] not in options:
            continue

        # Admin switch: a closed pool takes no new bookings, and an existing one
        # can't grow into it. Shrinking or unrelated edits still go through.
        if registration.get(closed_key) is True:
            if capacity_attendees > seats_held_in_pool(existing_registration, options):
                return respond(
                    400,
                    {
                        "error": f"{label} registrations are closed.",
                        "registrationClosed": True,
                    },
                    origin,
                )

        capacity_used = get_capacity_used(event_type, options, exclude_registration_id=exclude_registration_id)
        configured_capacity = registration.get(capacity_key)
        if configured_capacity is None and capacity_key == "sadhyaCapacity" and event_type == ONAM_EVENT_TYPE:
            configured_capacity = get_registration_setting("onam_capacity", str(ONAM_CAPACITY))
        try:
            configured_capacity = int(configured_capacity or 0)
        except (TypeError, ValueError):
            configured_capacity = 0
        if configured_capacity > 0 and capacity_used + capacity_attendees > configured_capacity:
            available_slots = max(configured_capacity - capacity_used, 0)
            return respond(
                400,
                {
                    "error": (
                        f"{label} has {available_slots} available slot(s). "
                        f"Your registration requires {capacity_attendees} slot(s)."
                    ),
                    "capacityReached": True,
                    "capacity": configured_capacity,
                    "usedSlots": capacity_used,
                    "availableSlots": available_slots,
                    "requestedSlots": capacity_attendees,
                },
                origin,
            )

    timestamp = datetime.now(timezone.utc).isoformat()
    if existing_registration:
        registration_id = existing_registration.get("registrationId")
        submitted_at = existing_registration.get("submittedAt", timestamp)
    else:
        registration_id = str(uuid.uuid4())
        submitted_at = timestamp

    # Build the transaction we'll append on join/renew up-front so the same
    # dates land in both the registration snapshot and the membership table.
    membership_txn = None
    if do_join_or_renew:
        memb_from, memb_to = npo_membership.year_span(RENEWAL_MEMBERSHIP_YEAR)
        txn_no = next_transaction_no(memberid, transactions)
        membership_txn = {
            "memberid": memberid or email,
            "transactionNo": txn_no,
            "membershipAmountPaid": order["npoFee"],
            "paidDate": timestamp,
            "membDateFrom": memb_from.isoformat() if memb_from else "",
            "membDateTo": memb_to.isoformat() if memb_to else "",
            "paidVia": payment_method or "onam-registration",
            "email": email,
        }

    membership_summary = {
        "wasMember": already_member,
        "action": resolved_action,      # 'join' | 'renew' | 'none'
        "year": RENEWAL_MEMBERSHIP_YEAR if do_join_or_renew else None,
        "validUntil": summary["latestTo"] or None,
        "fee": order["npoFee"],
    }

    registration_item = {
        "registrationId": registration_id,
        "registrationPassToken": (
            existing_registration.get("registrationPassToken")
            if existing_registration else secrets.token_urlsafe(32)
        ),
        "eventType": event_type,
        "eventDate": event_date,
        "email": email,
        "name": name,
        "phone": phone,
        "registrationType": "onam",
        "registrationOption": order["option"],
        "registrationOptionLabel": order["optionLabel"],
        "pricingTier": order["pricingTier"],
        "npoName": body.get("npoName", "").strip(),
        "additionalAdults": additional_adults,
        "attendeeCounts": {
            "adults": onam_pricing._int(counts.get("adults", 0)) or 0,
            "kids914": onam_pricing._int(counts.get("kids914", 0)) or 0,
            "kids68": onam_pricing._int(counts.get("kids68", 0)) or 0,
            "kidsUnder6": onam_pricing._int(counts.get("kidsUnder6", 0)) or 0,
        },
        "lineItems": order["lineItems"],
        "ticketsAmount": order["ticketsAmount"],
        "discountApplied": order["discountApplied"],
        "npoFee": order["npoFee"],
        "totalAmount": order["totalAmount"],
        "membership": membership_summary,
        "paymentMethod": payment_method,
        # New public registrations start unpaid; an admin marks them paid later.
        # Preserve the existing status on an update so a re-submit can't reset it.
        "paymentStatus": (
            existing_registration.get("paymentStatus", "pending")
            if existing_registration else "pending"
        ),
        "submittedAt": submitted_at,
        "updatedAt": timestamp,
    }

    # Reuse, replace, or create allocations/passes for the requested slots. A
    # legacy slotId is normalized by the helper into a single allocation.
    try:
        allocations = reconcile_entry_allocations(
            event_type, registration_id, existing_registration, registration_item["attendeeCounts"], body
        )
    except ValueError as exc:
        return respond(400, {"error": str(exc)}, origin)
    if allocations is not None:
        registration_item["allocations"] = allocations
        if len(allocations) == 1:
            registration_item["slotId"] = allocations[0]["entrySlotId"]
            registration_item["slotName"] = allocations[0]["slotName"]
            registration_item["slotSeats"] = allocations[0]["seatCount"]

    registrations_table.put_item(Item=registration_item)

    # Persist the NPO membership join/renewal so it's reflected on future logins:
    # append the dated transaction and flip npoMembershipActive on the member.
    if do_join_or_renew and membership_txn:
        record_membership_transaction(memberid or email, membership_txn)

    # Emails: reuse the shared confirmation template; Onam-specific admin notice.
    send_confirmation_email(
        email, name, event_config,
        is_update=bool(existing_registration),
        event_date_override=event_date,
        registration_details=registration_item,
    )
    send_onam_admin_notification(
        registration_item, order, event_config, is_update=bool(existing_registration)
    )

    return respond(
        200,
        {
            "message": "Registration updated successfully" if existing_registration else "Registration successful",
            "registrationId": registration_id,
            "registrationPassToken": (
                existing_registration.get("registrationPassToken")
                if existing_registration else secrets.token_urlsafe(32)
            ),
            "eventName": event_config.get("eventName"),
            "updated": bool(existing_registration),
            "pricingTier": order["pricingTier"],
            "ticketsAmount": order["ticketsAmount"],
            "discountApplied": order["discountApplied"],
            "npoFee": order["npoFee"],
            "totalAmount": order["totalAmount"],
            "membership": membership_summary,
            "allocations": registration_item.get("allocations", []),
        },
        origin,
    )


def lambda_handler(event, context):
    """Main Lambda handler for event registration."""

    # Handle CORS preflight
    if event.get("httpMethod") == "OPTIONS":
        return respond(200, {})

    headers = {k.lower(): v for k, v in (event.get("headers") or {}).items()}
    origin = headers.get("origin", "*")

    try:
        # Parse request body
        body = json.loads(event.get("body") or "{}")

        # Extract eventType from path parameters or body
        path_params = event.get("pathParameters", {}) or {}
        event_type = path_params.get("eventType") or body.get(
            "eventType") or get_default_event_type()

        if not event_type:
            logger.warning(
                "Missing eventType in request. path_params=%s",
                path_params,
            )
            return respond(400, {"error": "Missing eventType"}, origin)

        # Fetch event configuration from events table
        event_config = get_event_config(event_type)
        if not event_config:
            logger.warning("Event type not found: %s", event_type)
            return respond(404, {"error": f"Event type '{event_type}' not found"}, origin)

        # Check if event is active
        if event_config.get("status") != "active":
            logger.info("Event '%s' is not active. Current status: %s", event_type, event_config.get("status"))
            return respond(400, {"error": f"Event '{event_type}' is not currently accepting registrations"}, origin)

        # Entry slots are a per-event feature: refuse a slot request the event does not offer, whatever
        # the client UI showed (eventInfo.entrySlots.enabled; missing = on, for existing events).
        if (body.get("allocations") or str(body.get("slotId", "")).strip()) and not entry_slots_logic.is_entry_slots_enabled(
            normalize_event_info(event_config)
        ):
            return respond(400, {"error": "Entry slots are not available for this event",
                                 "code": "feature_disabled"}, origin)

        # Onam registrations use a dedicated, server-authoritative pricing flow.
        # Detected by the eventType or the presence of an Onam registrationOption.
        if event_type == ONAM_EVENT_TYPE or body.get("registrationOption"):
            return handle_onam_registration(body, event_type, event_config, origin)

        # Validate required fields
        first_name = body.get("firstName", "").strip()
        last_name = body.get("lastName", "").strip()
        name = body.get("name", "").strip(
        ) or f"{first_name} {last_name}".strip()
        email = body.get("email", "").strip().lower()
        phone = body.get("phone", "").strip(
        ) or body.get("whatsapp", "").strip()
        slot_id = str(body.get("slotId", "")).strip() or None
        update_existing = parse_bool(body.get("updateExisting", False))

        raw_counts = body.get("attendeeCounts") or {}
        adult_count = parse_non_negative_int(raw_counts.get("adult", 0))
        teen_count = parse_non_negative_int(raw_counts.get("teen", 0))
        child_count = parse_non_negative_int(raw_counts.get("child", 0))
        toddler_count = parse_non_negative_int(raw_counts.get("toddler", 0))

        if None in [adult_count, teen_count, child_count, toddler_count]:
            return respond(400, {"error": "Attendee counts must be whole numbers 0 or greater", "fields": {"attendeeCounts": "Attendee counts must be whole numbers 0 or greater"}}, origin)

        attendee_counts = {
            "adult": adult_count,
            "teen": teen_count,
            "child": child_count,
            "toddler": toddler_count,
        }
        total_attendees = sum(count or 0 for count in attendee_counts.values())

        vegetarian_count = parse_non_negative_int(
            body.get("vegetarianCount", 0))

        if not name:
            return respond(400, {"error": "Name is required"}, origin)
        if not email or not validate_email(email):
            return respond(400, {"error": "Valid email is required"}, origin)
        if phone and not validate_phone(phone):
            return respond(400, {"error": "Phone must include country code (e.g. +81 90-1234-5678)"}, origin)
        if vegetarian_count is None:
            return respond(400, {"error": "Vegetarian count must be a whole number 0 or greater", "fields": {"vegetarianCount": "Vegetarian count must be a whole number 0 or greater"}}, origin)
        if total_attendees > 0 and vegetarian_count > total_attendees:
            return respond(400, {"error": "Vegetarian count cannot exceed total attendees", "fields": {"vegetarianCount": "Vegetarian count cannot exceed total attendees"}}, origin)

        # One email can hold several registrations for the same event, so
        # email + eventType no longer identifies a single record. A specific
        # registration is only targeted for update when the client explicitly
        # asks for one by id.
        registrations_table = dynamodb.Table(REGISTRATIONS_TABLE)
        event_date = resolve_event_date(event_config, str(body.get("eventDate", "")))
        requested_registration_id = str(body.get("registrationId", "")).strip()
        existing_registration = None
        if requested_registration_id and update_existing:
            try:
                candidate = registrations_table.get_item(
                    Key={"registrationId": requested_registration_id}
                ).get("Item")
                if (
                    candidate
                    and str(candidate.get("email", "")).strip().lower() == email
                    and candidate.get("eventType") == event_type
                ):
                    existing_registration = candidate
            except ClientError:
                logger.exception(
                    "Could not load registration to update id=%s", requested_registration_id
                )

        # Create registration record
        timestamp = datetime.now(timezone.utc).isoformat()

        if existing_registration:
            registration_id = existing_registration.get("registrationId")
            submitted_at = existing_registration.get("submittedAt", timestamp)
        else:
            registration_id = str(uuid.uuid4())
            submitted_at = timestamp

        registration_item = {
            "registrationId": registration_id,
            "eventType": event_type,
            "eventDate": event_date,
            "email": email,
            "name": name,
            "firstName": first_name,
            "lastName": last_name,
            "phone": phone,
            "whatsapp": body.get("whatsapp", "").strip() or phone,
            "attendeeCounts": attendee_counts,
            "vegetarianCount": vegetarian_count,
            "registrationType": body.get("registrationType", "standard"),
            "paymentStatus": (
                existing_registration.get("paymentStatus", "pending")
                if existing_registration else "pending"
            ),
            "submittedAt": submitted_at,
            "updatedAt": timestamp,
            "additionalData": body.get("additionalData", {}),
        }

        # Reuse, replace, or create allocations/passes for the requested slots.
        onam_style_counts = {
            "adults": attendee_counts.get("adult", 0) + attendee_counts.get("teen", 0),
            "kids914": 0,
            "kids68": attendee_counts.get("child", 0),
            "kidsUnder6": attendee_counts.get("toddler", 0),
        }
        try:
            allocations = reconcile_entry_allocations(
                event_type, registration_id, existing_registration, onam_style_counts, body
            )
        except ValueError as exc:
            return respond(400, {"error": str(exc)}, origin)
        if allocations is not None:
            registration_item["allocations"] = allocations
            if len(allocations) == 1:
                registration_item["slotId"] = allocations[0]["entrySlotId"]
                registration_item["slotName"] = allocations[0]["slotName"]
                registration_item["slotSeats"] = allocations[0]["seatCount"]

        # Store registration in DynamoDB
        registrations_table.put_item(Item=registration_item)

        # Send confirmation email to registrant
        send_confirmation_email(
            email,
            name,
            event_config,
            is_update=bool(existing_registration),
            event_date_override=event_date,
            registration_details=registration_item,
        )

        # Send admin notification
        send_admin_notification(
            registration_item, event_config, is_update=bool(existing_registration))

        return respond(
            200,
            {
                "message": "Registration updated successfully" if existing_registration else "Registration successful",
                "registrationId": registration_id,
                "eventName": event_config.get("eventName"),
                "updated": bool(existing_registration),
                "allocations": registration_item.get("allocations", []),
            },
            origin
        )

    except json.JSONDecodeError as e:
        logger.exception("Invalid JSON in request body: %s", str(e))
        return respond(400, {"error": "Invalid JSON in request body"}, origin)
    except ClientError as e:
        logger.exception("AWS client error in registration handler: %s", str(e))
        return respond(500, {"error": "Internal server error"}, origin)
    except Exception as e:
        logger.exception("Unexpected error in registration handler: %s", str(e))
        return respond(500, {"error": "Internal server error"}, origin)
