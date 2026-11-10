"""Registration emails for the event platform (SES). `build_email` is pure and tested."""

import logging
import re
import struct
import zlib
import os
from datetime import datetime
from email.mime.image import MIMEImage
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from html import escape

import event_addons
import event_model

logger = logging.getLogger(__name__)

FROM_EMAIL = os.environ.get("SES_FROM_EMAIL", "")
FRONTEND_URL = os.environ.get("FRONTEND_URL", "").rstrip("/")
REGION = os.environ.get("REGION", "ap-northeast-1")

DEFAULTS = {
    "confirmed": ("Your registration for {event_title} is confirmed",
                  "Hello {name},\n\nThank you for registering for {event_title}.\nWhen: {event_date}\nWhere: {venue}\n\n"
                  "Your registration ID is {registration_id}. Please show the QR code below at check-in.\n\n"
                  "Tickets: {ticket_type}\nDetails: {event_url}"),
    "pending_payment": ("Complete your payment for {event_title}",
                        "Hello {name},\n\nWe have reserved your place for {event_title} (registration ID {registration_id}).\n"
                        "Tickets: {ticket_type}\n\nYour place is held until the time shown below. After payment is received "
                        "we will email your confirmation and QR code."),
    "waitlisted": ("You are on the waiting list for {event_title}",
                   "Hello {name},\n\n{event_title} is currently full, so we have added you to the waiting list "
                   "(registration ID {registration_id}).\nTickets: {ticket_type}\n\nIf a place becomes available an organiser will "
                   "contact you by email. You have not been charged."),
    "promoted": ("A place is available for {event_title}",
                 "Hello {name},\n\nGood news: a place has opened up for you at {event_title} (registration ID {registration_id}).\n"
                 "Tickets: {ticket_type}"),
    "cancelled": ("Your registration for {event_title} was cancelled",
                  "Hello {name},\n\nYour registration {registration_id} for {event_title} has been cancelled."),
    "expired": ("Your reserved place for {event_title} was released",
                "Hello {name},\n\nWe did not receive payment for registration {registration_id} in time, so your reserved place "
                "for {event_title} was released. You are welcome to register again if places are still available.\n\n{event_url}"),
}


def _event_date(event: dict) -> str:
    start = event_model.parse_iso(event.get("startsAt"))
    if not start:
        return "To be announced"
    text = start.astimezone(event_model.JST).strftime("%Y-%m-%d %H:%M JST")
    end = event_model.parse_iso(event.get("endsAt"))
    if end and end.date() != start.date():
        text += " – " + end.astimezone(event_model.JST).strftime("%Y-%m-%d %H:%M JST")
    return text


def _yen(amount) -> str:
    return f"¥{int(amount):,}"


def placeholder_values(reg: dict, event: dict) -> dict:
    tickets = ", ".join(f"{l['name']} × {l['qty']}" for l in reg.get("tickets", []))
    location = event.get("location") or {}
    return {
        "name": reg.get("fullName", ""), "event_title": event.get("title", ""), "ticket_type": tickets,
        "registration_id": reg["registrationId"], "event_date": _event_date(event),
        "venue": ", ".join(x for x in (location.get("venueName"), location.get("address")) if x),
        "event_url": f"{FRONTEND_URL}/events/{event['slug']}" if FRONTEND_URL else "",
    }


def payment_block(reg: dict, methods: dict) -> list:
    """Lines telling the registrant how to pay."""
    lines = []
    if reg.get("totalJpy"):
        lines.append(f"Amount due: {_yen(reg['totalJpy'])}")
    method = reg.get("paymentMethod")
    if method == "square" and reg.get("checkoutUrl"):
        lines.append(f"Pay by card: {reg['checkoutUrl']}")
    elif method in ("bank", "paypay"):
        info = methods.get(method) or {}
        lines.append({"bank": "Pay by bank transfer", "paypay": "Pay by PayPay"}[method] + ":")
        if info.get("instructions"):
            lines.append(info["instructions"])
        if info.get("url"):
            lines.append(info["url"])
        lines.append(f"Please include the registration ID {reg['registrationId']} with your payment.")
    if reg.get("holdUntil"):
        until = event_model.parse_iso(reg["holdUntil"])
        if until:
            lines.append("Your place is held until " + until.astimezone(event_model.JST).strftime("%Y-%m-%d %H:%M JST") + ".")
    return lines


def summary_block(reg: dict) -> list:
    lines = [f"Registration ID: {reg['registrationId']}"]
    lines += [f"  {l['name']} × {l['qty']}: {_yen(l['subtotalJpy'])}" for l in reg.get("tickets", [])]
    extra = event_addons.describe(reg)
    if extra:
        lines += [f"  {part}" for part in extra.split("; ")]
    lines.append(f"Total: {_yen(reg.get('totalJpy', 0))}")
    return lines


def qr_payload(reg: dict) -> str:
    return f"WMFEV1.{reg['registrationId']}.{reg['checkInToken']}"


CONTACT_EMAIL = "info@wmfjapan.com"
SAFE_COLOR_RE = re.compile(r"^#[0-9a-fA-F]{6}$")


def _paragraphs(text: str) -> str:
    return "".join(
        f'<p style="margin:0 0 14px;color:#1b2621;font-size:14px;line-height:1.6;">{escape(p).replace(chr(10), "<br>")}</p>'
        for p in text.split("\n\n") if p.strip())


def _detail_card(title: str, rows_html: str) -> str:
    return (f'<div style="background:#f1f5f3;border:1px solid #d5dfd9;padding:16px;margin:20px 0;border-radius:4px;">'
            f'<p style="margin:0 0 10px;color:#1b2621;font-size:13px;font-weight:700;">{escape(title)}</p>{rows_html}</div>')


def _row(label: str, value: str, *, strong: bool = False) -> str:
    weight = "font-weight:700;" if strong else ""
    return (f'<tr><td style="padding:6px 0;color:#5d6b65;font-size:13px;">{escape(label)}</td>'
            f'<td style="padding:6px 0;color:#1b2621;font-size:13px;{weight}text-align:right;">{escape(value)}</td></tr>')


def summary_html(reg: dict, event: dict) -> str:
    rows = _row("Registration ID", reg["registrationId"], strong=True)
    rows += _row("Event", event.get("title", ""))
    rows += _row("When", _event_date(event))
    venue = placeholder_values(reg, event)["venue"]
    if venue:
        rows += _row("Where", venue)
    rows += "".join(_row(f"{l['name']} × {l['qty']}", _yen(l["subtotalJpy"])) for l in reg.get("tickets", []))
    rows += "".join(_row(part.split(": ", 1)[0], part.split(": ", 1)[1]) for part in event_addons.describe(reg).split("; ") if ": " in part)
    total = (f'<tr><td style="padding:12px 0 0;color:#1b2621;font-size:15px;font-weight:700;border-top:2px solid #d5dfd9;">Total</td>'
             f'<td style="padding:12px 0 0;color:#1b2621;font-size:15px;font-weight:900;text-align:right;border-top:2px solid #d5dfd9;">'
             f'{escape(_yen(reg.get("totalJpy", 0)))}</td></tr>')
    return _detail_card("Registration summary", f'<table width="100%" cellpadding="0" cellspacing="0">{rows}{total}</table>')


def payment_html(reg: dict, methods: dict) -> str:
    lines = payment_block(reg, methods)
    if not lines:
        return ""
    body = "".join(f'<p style="margin:0 0 6px;color:#77582f;font-size:13px;line-height:1.5;">{escape(l).replace(chr(10), "<br>")}</p>' for l in lines)
    button = ""
    if reg.get("paymentMethod") == "square" and str(reg.get("checkoutUrl", "")).startswith("https://"):
        button = (f'<p style="margin:12px 0 0;"><a href="{escape(reg["checkoutUrl"], quote=True)}" style="display:inline-block;background:#3d7258;'
                  f'color:#f3f8f5;padding:10px 18px;border-radius:4px;font-size:13px;font-weight:700;text-decoration:none;">Pay by card</a></p>')
    return (f'<div style="background:#fff8eb;border:1px solid #efd6a9;padding:16px;margin:20px 0;border-radius:4px;">'
            f'<p style="margin:0 0 8px;color:#5c3d12;font-size:13px;font-weight:700;">Payment</p>{body}{button}</div>')


def qr_html() -> str:
    return ('<div style="text-align:center;margin:20px 0;padding:16px;border:1px dashed #69a785;border-radius:4px;">'
            '<p style="margin:0 0 10px;color:#1b2621;font-size:13px;font-weight:700;">Your check-in QR code</p>'
            '<img src="cid:qrcode" alt="Check-in QR code" width="220" height="220" style="border:0;" />'
            '<p style="margin:10px 0 0;color:#5d6b65;font-size:12px;">Show this at the entrance.</p></div>')


def branded_html(title: str, inner_html: str, *, accent: str = "", event_url: str = "", button_label: str = "View event details") -> str:
    """The shared WMF Japan email frame (WMF Japan branding)."""
    accent = accent if SAFE_COLOR_RE.match(accent or "") else "#69a785"
    logo = (f'<img src="{FRONTEND_URL}/logo.png" alt="WMF Japan" width="100" height="54" '
            f'style="display:block;margin:0 auto 14px;border:0;max-width:100px;height:auto;" />' if FRONTEND_URL else "")
    button = (f'<p style="margin:22px 0 0;text-align:center;"><a href="{escape(event_url, quote=True)}" style="display:inline-block;background:#3d7258;'
              f'color:#f3f8f5;padding:11px 22px;border-radius:4px;font-size:14px;font-weight:700;text-decoration:none;">{escape(button_label)}</a></p>'
              if event_url.startswith("http") else "")
    return f"""<!DOCTYPE html>
<html>
<head><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1"></head>
<body style="margin:0;padding:0;background:#edf3ef;font-family:'Noto Sans','Helvetica Neue',Arial,sans-serif;">
<table width="100%" cellpadding="0" cellspacing="0" style="background:#edf3ef;padding:28px 0;">
<tr><td align="center">
<table width="620" cellpadding="0" cellspacing="0" style="max-width:100%;background:#ffffff;border-radius:4px;overflow:hidden;border:2px solid {accent};">
  <tr><td style="background:#1c2722;padding:0;border-bottom:4px solid {accent};">
    <div style="padding:28px 30px 24px;text-align:center;">
      {logo}
      <h1 style="margin:0;color:#cfe8da;font-size:26px;font-weight:900;letter-spacing:0.03em;line-height:1.2;">{escape(title)}</h1>
    </div>
  </td></tr>
  <tr><td style="padding:28px;">
    {inner_html}{button}
  </td></tr>
  <tr><td style="background:#edf3ef;padding:18px 28px;border-top:1px solid #d5dfd9;text-align:center;">
    <p style="margin:0;color:#5d6b65;font-size:12px;">Questions? Contact <a href="mailto:{CONTACT_EMAIL}" style="color:#3d7258;font-weight:700;text-decoration:none;">{CONTACT_EMAIL}</a></p>
    <p style="margin:6px 0 0;color:#5d6b65;font-size:12px;">&copy;{datetime.now().year} WMF Japan</p>
  </td></tr>
</table></td></tr></table>
</body>
</html>"""


def build_email(kind: str, reg: dict, event: dict, methods: dict) -> tuple:
    """(subject, plain_text, html, qr_payload_or_None)."""
    default_subject, default_body = DEFAULTS[kind]
    custom = (event.get("emails") or {}).get("confirmation") if kind == "confirmed" else None
    subject_t = (custom or {}).get("subject") or default_subject
    body_t = (custom or {}).get("body") or default_body
    values = placeholder_values(reg, event)
    subject = re.sub(r"[\r\n]+", " ", event_model.render_template(subject_t, values))[:200]
    body = event_model.render_template(body_t, values)
    extra = summary_block(reg) if kind in ("confirmed", "pending_payment", "waitlisted", "promoted") else []
    if kind in ("pending_payment", "promoted") and reg.get("paymentStatus") == "pending":
        extra += [""] + payment_block(reg, methods)
    text = body + ("\n\n" + "\n".join(extra) if extra else "")
    qr = qr_payload(reg) if kind == "confirmed" or (kind == "promoted" and reg.get("status") == "confirmed") else None
    inner = _paragraphs(body)
    if kind in ("confirmed", "pending_payment", "waitlisted", "promoted"):
        inner += summary_html(reg, event)
    if kind in ("pending_payment", "promoted") and reg.get("paymentStatus") == "pending":
        inner += payment_html(reg, methods)
    if qr:
        inner += qr_html()
    html = branded_html(event.get("title", "") or "WMF Japan", inner, accent=(event.get("design") or {}).get("accentColor", ""),
                        event_url=values["event_url"])
    return subject, text, html, qr


def _png(width: int, height: int, rows: list) -> bytes:
    """Minimal 8-bit grayscale PNG (the QR layer ships no imaging library)."""
    def chunk(tag: bytes, data: bytes) -> bytes:
        body = tag + data
        return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body) & 0xFFFFFFFF)
    raw = b"".join(b"\x00" + row for row in rows)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 0, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b""))


def qr_png(payload: str, scale: int = 8, border: int = 3):
    """PNG bytes of a QR code for `payload`, or None if the qrcode package is unavailable."""
    try:
        import qrcode
    except ImportError:
        return None
    code = qrcode.QRCode(error_correction=qrcode.constants.ERROR_CORRECT_M, border=0)
    code.add_data(payload)
    code.make(fit=True)
    matrix = code.get_matrix()
    size = (len(matrix) + 2 * border) * scale
    white, black = b"\xff", b"\x00"
    blank = white * size
    rows = [blank] * (border * scale)
    for line in matrix:
        pixels = white * (border * scale) + b"".join((black if cell else white) * scale for cell in line) + white * (border * scale)
        rows += [pixels] * scale
    rows += [blank] * (border * scale)
    return _png(size, size, rows)


def make_mime(subject: str, text: str, html: str, qr, to: str, sender: str):
    root = MIMEMultipart("related")
    root["Subject"], root["From"], root["To"] = subject, sender, to
    alternative = MIMEMultipart("alternative")
    alternative.attach(MIMEText(text, "plain", "utf-8"))
    alternative.attach(MIMEText(html, "html", "utf-8"))
    root.attach(alternative)
    png = qr_png(qr) if qr else None
    if png:
        image = MIMEImage(png, "png")
        image.add_header("Content-ID", "<qrcode>")
        image.add_header("Content-Disposition", "inline", filename="checkin-qr.png")
        root.attach(image)
    return root


_ses = None


def send(kind: str, reg: dict, event: dict, methods: dict) -> None:
    global _ses
    if not FROM_EMAIL or not reg.get("email"):
        logger.warning("email %s skipped: sender or recipient missing", kind)
        return
    import boto3
    _ses = _ses or boto3.client("ses", region_name=REGION)
    subject, text, html, qr = build_email(kind, reg, event, methods)
    message = make_mime(subject, text, html, qr, reg["email"], FROM_EMAIL)
    _ses.send_raw_email(Source=FROM_EMAIL, Destinations=[reg["email"]], RawMessage={"Data": message.as_string()})


def build_message(text: str) -> tuple:
    """(plain, html) for a free-text message such as a bulk announcement."""
    return text, branded_html("WMF Japan", _paragraphs(text))


def send_message(to: str, subject: str, text: str) -> None:
    """Send an organiser-written message (no QR, no registration summary)."""
    global _ses
    if not FROM_EMAIL or not to:
        raise RuntimeError("sender or recipient missing")
    import boto3
    _ses = _ses or boto3.client("ses", region_name=REGION)
    plain, html = build_message(text)
    message = make_mime(re.sub(r"[\r\n]+", " ", subject)[:200], plain, html, None, to, FROM_EMAIL)
    _ses.send_raw_email(Source=FROM_EMAIL, Destinations=[to], RawMessage={"Data": message.as_string()})
