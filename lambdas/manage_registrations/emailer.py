"""
Shared email templates + SES senders for registration confirmations.

Used by the edit flows (admin `manage_registrations` and self-service
`my_registrations`) so an edit re-sends the same confirmation the public
`register` flow sends on first submit, plus a dedicated "payment received"
email when an admin marks a registration paid.

This module is COPIED into each Lambda that needs it (same convention as
onam_pricing.py / npo_membership.py). Keep the copies in sync.

Env vars (all provided by the common Terraform lambda_env):
  REGION, EVENTS_TABLE, SES_FROM_EMAIL, ADMIN_EMAIL, ADMIN_EMAIL_CC
"""

import json
import logging
import os
from email import encoders
from email.mime.base import MIMEBase
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from datetime import datetime
from typing import Optional

import boto3
from botocore.exceptions import ClientError
import qr_email

logger = logging.getLogger(__name__)

_REGION = os.environ.get("REGION", "")
ses = boto3.client("ses", region_name=_REGION) if _REGION else boto3.client("ses")
dynamodb = boto3.resource("dynamodb", region_name=_REGION) if _REGION else boto3.resource("dynamodb")

EVENTS_TABLE = os.environ.get("EVENTS_TABLE", "")
FROM_EMAIL = os.environ.get("SES_FROM_EMAIL", "")
ADMIN_EMAIL = os.environ.get("ADMIN_EMAIL", "")
ADMIN_CC = [e.strip() for e in os.environ.get("ADMIN_EMAIL_CC", "").split(",") if e.strip()]

# Human-facing labels for the internal payment-status codes.
PAYMENT_STATUS_LABELS = {
    "pending": "Payment Pending",
    "paid": "Paid",
    "refunded": "Refunded",
}


# ── Event config ─────────────────────────────────────────────────────────────


def get_event_config(event_type: str) -> Optional[dict]:
    """Fetch event metadata (name, venue, etc.) for the email body."""
    if not EVENTS_TABLE or not event_type:
        return None
    try:
        return dynamodb.Table(EVENTS_TABLE).get_item(Key={"eventType": event_type}).get("Item")
    except ClientError:
        logger.exception("Emailer: could not fetch event config for %s", event_type)
        return None


def normalize_event_info(event_config: dict) -> dict:
    """Normalize eventInfo into a dict regardless of storage format."""
    event_info = (event_config or {}).get("eventInfo", {})
    if isinstance(event_info, str):
        event_info = event_info.strip()
        if not event_info:
            return {}
        try:
            parsed = json.loads(event_info)
            return parsed if isinstance(parsed, dict) else {}
        except json.JSONDecodeError:
            return {}
    return event_info if isinstance(event_info, dict) else {}


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
    """Resolve payment links from event config/eventInfo into method->URL map."""
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


# ── HTML fragments ───────────────────────────────────────────────────────────


def _detail_row(label: str, value: str, *, strong: bool = False, color: str = "#1b2621") -> str:
    weight = "font-weight:700;" if strong else ""
    return (
        f'<tr><td style="padding:6px 0;color:#5d6b65;font-size:13px;">{label}</td>'
        f'<td style="padding:6px 0;color:{color};font-size:13px;{weight}text-align:right;">{value}</td></tr>'
    )


def _format_entry_slot_summary_text(record: dict) -> str:
    """Plain-text entry-slot line(s) for admin notifications, one per allocation."""
    allocations = record.get("allocations") or []
    if allocations:
        multiple = len(allocations) > 1
        lines = []
        for idx, allocation in enumerate(allocations, start=1):
            label = f"Entry Slot {idx}" if multiple else "Entry Slot"
            name = allocation.get("slotName") or f"Slot {idx}"
            lines.append(f"- {label}: {name} ({allocation.get('seatCount', 0)} seats)")
        return "\n".join(lines)
    return f"- Entry Slot: {record.get('slotName', 'N/A')} ({record.get('slotSeats', 0)} seats)"


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


def build_registration_details_html(registration_details: Optional[dict]) -> str:
    """HTML summary of the order (option, line items, amounts, payment status)."""
    if not registration_details:
        return ""

    option_label = registration_details.get("registrationOptionLabel", "")
    payment_method = registration_details.get("paymentMethod", "")
    payment_status = str(registration_details.get("paymentStatus", "")).lower()
    line_items = registration_details.get("lineItems") or []
    tickets_amount = registration_details.get("ticketsAmount")
    discount_applied = registration_details.get("discountApplied")
    npo_fee = registration_details.get("npoFee")
    total_amount = registration_details.get("totalAmount")
    paid_amount = registration_details.get("paidAmount")
    balance_due = registration_details.get("balanceDue")
    membership = registration_details.get("membership") or {}
    allocations = registration_details.get("allocations") or []
    slot_name = registration_details.get("slotName", "")
    slot_seats = registration_details.get("slotSeats")

    rows = []
    if option_label:
        rows.append(_detail_row("Registration Option", option_label, strong=True))
    if allocations:
        multiple = len(allocations) > 1
        for idx, allocation in enumerate(allocations, start=1):
            label = f"Entry Slot {idx}" if multiple else "Entry Slot"
            name = allocation.get("slotName") or f"Slot {idx}"
            seats = allocation.get("seatCount")
            value = f"{name} ({seats} seat{'s' if seats != 1 else ''})" if seats else name
            rows.append(_detail_row(label, value, strong=True))
    elif slot_name:
        slot_value = f"{slot_name} ({slot_seats} seat{'s' if slot_seats != 1 else ''})" if slot_seats else slot_name
        rows.append(_detail_row("Entry Slot", slot_value, strong=True))
    for li in line_items:
        label = li.get("label", "")
        quantity = li.get("quantity", 0)
        unit_price = li.get("unitPrice", 0)
        rows.append(_detail_row(f"{label} ({quantity} × ¥{unit_price})", f"¥{li.get('total', 0)}"))
    if tickets_amount is not None:
        rows.append(_detail_row("Tickets Subtotal", f"¥{tickets_amount}"))
    if discount_applied:
        rows.append(_detail_row("NPO Discount", f"−¥{discount_applied}", color="#3d7258"))
    if npo_fee:
        rows.append(_detail_row("NPO Membership Fee", f"¥{npo_fee}"))
    if payment_method:
        rows.append(_detail_row("Payment Method", payment_method, strong=True))
    if payment_status in PAYMENT_STATUS_LABELS:
        color = "#3d7258" if payment_status == "paid" else "#1b2621"
        rows.append(_detail_row("Payment Status", PAYMENT_STATUS_LABELS[payment_status], strong=True, color=color))

    total_row = ""
    if total_amount is not None:
        total_row = (
            f'<tr><td style="padding:12px 0 0;color:#1b2621;font-size:15px;font-weight:700;border-top:2px solid #d5dfd9;">Total Amount</td>'
            f'<td style="padding:12px 0 0;color:#1b2621;font-size:15px;font-weight:900;text-align:right;border-top:2px solid #d5dfd9;">¥{total_amount}</td></tr>'
        )

    # When money has been recorded against the order, show what was paid and any
    # outstanding balance (e.g. after an edit changed the total).
    try:
        paid_val = int(paid_amount) if paid_amount is not None else 0
    except (TypeError, ValueError):
        paid_val = 0
    try:
        balance_val = int(balance_due) if balance_due is not None else None
    except (TypeError, ValueError):
        balance_val = None
    # Only reconcile once money has actually been recorded; an unpaid record's
    # "balance" is just its total and would be noise here.
    if paid_val:
        total_row += _detail_row("Amount Paid", f"¥{paid_val}", color="#3d7258")
        if balance_val:
            if balance_val > 0:
                total_row += _detail_row("Balance Due", f"¥{balance_val}", strong=True, color="#c85050")
            else:
                total_row += _detail_row("Refund Due", f"¥{abs(balance_val)}", strong=True, color="#c85050")

    membership_html = ""
    action = str(membership.get("action", "none")).lower()
    if action in {"join", "renew"}:
        year = membership.get("year") or ""
        action_label = "renewed" if action == "renew" else "joined"
        detail = f"NPO membership {action_label}"
        if year:
            detail += f" for {year}"
        membership_html = (
            f'<div style="background:#edf7f1;border-left:4px solid #69a785;padding:12px 14px;margin:14px 0 0;border-radius:2px;">'
            f'<p style="margin:0;color:#1b2621;font-size:13px;font-weight:700;">🌿 Membership</p>'
            f'<p style="margin:4px 0 0;color:#5d6b65;font-size:13px;">{detail}.</p></div>'
        )
    elif membership.get("wasMember"):
        membership_html = (
            f'<div style="background:#edf7f1;border-left:4px solid #69a785;padding:12px 14px;margin:14px 0 0;border-radius:2px;">'
            f'<p style="margin:0;color:#1b2621;font-size:13px;font-weight:700;">🌿 Membership</p>'
            f'<p style="margin:4px 0 0;color:#5d6b65;font-size:13px;">You are an active NPO member.</p></div>'
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


def _email_shell(event_name: str, registrant_name: str, intro_html: str, body_html: str) -> str:
    """Wrap body content in the shared branded email shell."""
    # Explicit width+height (not just CSS) keeps Outlook's renderer, which
    # ignores height:auto, from distorting the logo's aspect ratio.
    logo_html = (
        f'<img src="{qr_email.FRONTEND_URL}/logo.png" alt="WMF Japan" width="100" height="54" '
        f'style="display:block;margin:0 auto 14px;border:0;max-width:100px;height:auto;" />'
        if qr_email.FRONTEND_URL
        else ""
    )
    return f"""
<!DOCTYPE html>
<html>
<head><meta charset="UTF-8"></head>
<body style="margin:0;padding:0;background:#edf3ef;font-family:'Noto Sans','Helvetica Neue',Arial,sans-serif;">
<table width="100%" cellpadding="0" cellspacing="0" style="background:#edf3ef;padding:28px 0;">
<tr><td align="center">
<table width="620" cellpadding="0" cellspacing="0" style="background:#ffffff;border-radius:4px;overflow:hidden;border:2px solid #69a785;box-shadow:0 4px 20px rgba(27,38,33,0.14);">
  <tr><td style="background:linear-gradient(180deg, #2a3832 0%, #1c2722 100%);padding:0;">
    <div style="padding:28px 30px 24px;text-align:center;">
      {logo_html}
      <h1 style="margin:0;color:#cfe8da;font-size:30px;font-weight:900;letter-spacing:0.03em;line-height:1.15;">{event_name}</h1>
    </div>
  </td></tr>
  <tr><td style="padding:28px;">
    <p style="margin:0 0 10px;color:#1b2621;font-size:18px;font-weight:700;">Hello {registrant_name},</p>
    {intro_html}
{body_html}
  </td></tr>
  <tr><td style="background:#edf3ef;padding:18px 28px;border-top:1px solid #d5dfd9;text-align:center;">
    <p style="margin:0;color:#5d6b65;font-size:12px;">
      Questions? Contact <a href="mailto:info@wmfjapan.com" style="color:#3d7258;font-weight:700;text-decoration:none;">info@wmfjapan.com</a>
    </p>
    <p style="margin:6px 0 0;color:#5d6b65;font-size:12px;">©2026 WMF Japan</p>
  </td></tr>
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


def build_confirmation_email(
    registrant_name: str,
    event_config: dict,
    is_update: bool = False,
    registration_details: Optional[dict] = None,
    subject_prefix: Optional[str] = None,
) -> tuple:
    """Return (subject, html_body) for the event registration confirmation/update email."""
    event_name = (event_config or {}).get("eventName", "Event")
    # venue_name = (event_config or {}).get("venue") or "Unknown Venue"
    # venue_address = (event_config or {}).get("venueAddress") or "Unknown Address"
    # event_note = (event_config or {}).get("eventNote") or ""
    # description = (event_config or {}).get("description") or ""

    # details_lines = [
    #     f"📍 Venue: {venue_name}",
    #     f"🏠 Address: {venue_address}",
    #     f"📝 Description: {description}",
    # ]
    # if event_note:
    #     details_lines.append(f"ℹ️ Note: {event_note}")
    # event_details = "<br>".join(details_lines)

    intro = (
        f'<p style="margin:0 0 18px;color:#5d6b65;font-size:14px;line-height:1.7;">'
        f"Your registration for <strong>{event_name}</strong> has been "
        f"{'updated' if is_update else 'confirmed'}. We look forward to seeing you!</p>"
    )
    seating_note_html = build_celebrity_seating_note_html(registration_details)
    payment_links_html = build_payment_links_html(
        event_config,
        selected_method=(registration_details or {}).get("paymentMethod", ""),
    )

    body = f"""
{seating_note_html}
{build_registration_details_html(registration_details)}
<!-- REGISTRATION_QR -->
{payment_links_html}
"""
    resolved_subject_prefix = subject_prefix or (
        "Registration Updated" if is_update else "Registration Confirmed"
    )
    return f"{resolved_subject_prefix} – {event_name}", _email_shell(event_name, registrant_name, intro, body)


def build_payment_received_email(
    registrant_name: str,
    event_config: dict,
    registration_details: Optional[dict] = None,
) -> tuple:
    """Return (subject, html_body) for the 'payment received' email."""
    event_name = (event_config or {}).get("eventName", "Event")
    total_amount = (registration_details or {}).get("totalAmount")
    amount_txt = f" of ¥{total_amount}" if total_amount is not None else ""
    intro = (
        f'<p style="margin:0 0 18px;color:#5d6b65;font-size:14px;line-height:1.7;">'
        f"We've received your payment{amount_txt} for <strong>{event_name}</strong>. "
        f"Your registration is now fully confirmed — thank you!</p>"
        f'<div style="background:#edf7f1;border-left:4px solid #3d7258;padding:12px 14px;margin:0 0 6px;border-radius:2px;">'
        f'<p style="margin:0;color:#1b2621;font-size:14px;font-weight:700;">✅ Payment received</p></div>'
    )
    body = build_registration_details_html(registration_details)
    return f"💳 Payment Received – {event_name}", _email_shell(event_name, registrant_name, intro, body)


def build_payment_pending_email(
    registrant_name: str,
    event_config: dict,
    registration_details: Optional[dict] = None,
) -> tuple:
    """Return (subject, html_body) for a payment-pending reminder."""
    event_name = (event_config or {}).get("eventName", "Event")
    total_amount = (registration_details or {}).get("totalAmount")
    amount_txt = f" of ¥{total_amount}" if total_amount is not None else ""
    intro = (
        f'<p style="margin:0 0 18px;color:#5d6b65;font-size:14px;line-height:1.7;">'
        f"Your payment{amount_txt} for <strong>{event_name}</strong> is still pending. "
        f"Please complete payment to confirm your registration.</p>"
    )
    body = (
        build_registration_details_html(registration_details)
        + build_payment_links_html(
            event_config,
            selected_method=(registration_details or {}).get("paymentMethod", ""),
        )
    )
    return f"Payment Pending – {event_name}", _email_shell(event_name, registrant_name, intro, body)


def build_sadhya_slot_reminder_email(
    registrant_name: str,
    event_config: dict,
    registration_details: Optional[dict] = None,
) -> tuple:
    """Return (subject, html_body) for an Onam Sadhya entry-slot reminder.

    Only sent to paid Onam/Combo registrants who haven't picked a slot yet
    (see the "send_sadhya_slot_reminder" bulk action), so the copy always
    assumes no slot is selected.
    """
    event_name = (event_config or {}).get("eventName", "Event")
    intro = (
        f'<p style="margin:0 0 18px;color:#5d6b65;font-size:14px;line-height:1.7;">'
        f"You haven't selected your Onam Sadhya entry slot for <strong>{event_name}</strong> yet. "
        f"Please pick a slot from <strong>My Registrations</strong> so we can plan your seating.</p>"
    )

    cta_html = ""
    if qr_email.FRONTEND_URL:
        cta_html = f"""
    <div style="text-align:center;margin:0 0 20px;">
      <a href="{qr_email.FRONTEND_URL}/my/registrations" style="display:inline-block;background:#3d7258;color:#f3f8f5;padding:12px 24px;text-decoration:none;border-radius:4px;font-weight:700;font-size:14px;">🎟 Select Your Sadhya Slot</a>
    </div>
    """

    body = build_registration_details_html(registration_details) + cta_html
    return f"Reminder: Select Your Sadhya Entry Slot – {event_name}", _email_shell(event_name, registrant_name, intro, body)


# ── Senders ──────────────────────────────────────────────────────────────────


def _send(to_addresses: list, subject: str, html_body: str, registration_details=None,
          event_name: str = "") -> bool:
    if not FROM_EMAIL or not to_addresses:
        logger.warning("Emailer: skipping send (missing FROM_EMAIL or recipients)")
        return False
    try:
        filename, pdf_bytes = "", b""
        if qr_email.PLACEHOLDER in html_body:
            filename, pdf_bytes = qr_email.build_registration_ticket(registration_details or {}, event_name)
            html_body = html_body.replace(
                qr_email.PLACEHOLDER,
                qr_email.ticket_note_html(registration_details or {}) if pdf_bytes else "",
            )
        if pdf_bytes:
            message = MIMEMultipart("mixed")
            message["Subject"] = subject
            message["From"] = FROM_EMAIL
            message["To"] = ", ".join(to_addresses)
            message.attach(MIMEText(html_body, "html", "utf-8"))
            attachment = MIMEBase("application", "pdf")
            attachment.set_payload(pdf_bytes)
            encoders.encode_base64(attachment)
            attachment.add_header("Content-Disposition", "attachment", filename=filename)
            message.attach(attachment)
            ses.send_raw_email(Source=FROM_EMAIL, Destinations=to_addresses, RawMessage={"Data": message.as_bytes()})
        else:
            ses.send_email(
                Source=FROM_EMAIL,
                Destination={"ToAddresses": to_addresses},
                Message={"Subject": {"Data": subject}, "Body": {"Html": {"Data": html_body}}},
            )
        return True
    except ClientError:
        logger.exception("Emailer: SES send failed to %s", to_addresses)
        return False


def send_confirmation_email(registrant_email, registrant_name, event_config,
                            is_update=False, registration_details=None, subject_prefix=None) -> bool:
    if not registrant_email:
        return False
    subject, html_body = build_confirmation_email(
        registrant_name, event_config, is_update=is_update,
        registration_details=registration_details,
        subject_prefix=subject_prefix,
    )
    return _send(
        [registrant_email], subject, html_body, registration_details,
        event_name=(event_config or {}).get("eventName", "Event"),
    )


def send_payment_received_email(registrant_email, registrant_name, event_config,
                                registration_details=None) -> bool:
    if not registrant_email:
        return False
    subject, html_body = build_payment_received_email(
        registrant_name, event_config, registration_details=registration_details,
    )
    return _send([registrant_email], subject, html_body)


def send_payment_pending_email(registrant_email, registrant_name, event_config,
                               registration_details=None) -> bool:
    if not registrant_email:
        return False
    subject, html_body = build_payment_pending_email(
        registrant_name, event_config, registration_details=registration_details,
    )
    return _send([registrant_email], subject, html_body)


def send_sadhya_slot_reminder_email(registrant_email, registrant_name, event_config,
                                    registration_details=None) -> bool:
    if not registrant_email:
        return False
    subject, html_body = build_sadhya_slot_reminder_email(
        registrant_name, event_config, registration_details=registration_details,
    )
    return _send([registrant_email], subject, html_body)


def send_admin_notification(record: dict, event_config: dict, is_update: bool = False) -> bool:
    """Plain-text admin notification covering both Onam and standard records."""
    if not ADMIN_EMAIL:
        return False
    event_name = (event_config or {}).get("eventName", "Event")
    subject = f"{'Update' if is_update else 'New'} registration: {event_name}"
    counts = record.get("attendeeCounts", {}) or {}
    counts_txt = "\n".join(f"    - {k}: {v}" for k, v in counts.items())
    membership = record.get("membership", {}) or {}
    status = str(record.get("paymentStatus", "")).lower()
    body = f"""{'Updated' if is_update else 'New'} registration for {event_name}:
- Name: {record.get('name', 'N/A')}
- Email: {record.get('email', 'N/A')}
- Phone: {record.get('phone', 'N/A')}
- Type: {record.get('registrationType', 'N/A')}
- Option: {record.get('registrationOptionLabel') or record.get('registrationOption') or 'N/A'}
{_format_entry_slot_summary_text(record)}
- Attendees:
{counts_txt}
- Total: ¥{record.get('totalAmount', 'N/A')}
- NPO membership: was_member={membership.get('wasMember')} action={membership.get('action')} year={membership.get('year')}
- Payment method: {record.get('paymentMethod', 'N/A')}
- Payment status: {PAYMENT_STATUS_LABELS.get(status, status or 'N/A')}
- Updated at: {record.get('updatedAt', record.get('submittedAt', 'N/A'))}
"""
    recipients = [ADMIN_EMAIL] + ADMIN_CC
    try:
        ses.send_email(
            Source=FROM_EMAIL,
            Destination={"ToAddresses": recipients},
            Message={"Subject": {"Data": subject}, "Body": {"Text": {"Data": body}}},
        )
        return True
    except ClientError:
        logger.exception("Emailer: admin notification send failed")
        return False