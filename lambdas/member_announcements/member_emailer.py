"""
Email templates + SES sender for member announcements (AGM, join, renew).

Follows the same look and building blocks as the other announcement emails
(Zone Upgrade / Seat Selection / Celebrity Dinner): branded header with logo
and kicker, a titled body, a call-to-action button and the standard footer.
The generic pieces below are copied from celebrity_dinner_emailer.py — the
repo's convention is one copy per Lambda package.

Env vars:
  REGION, SES_FROM_EMAIL, FRONTEND_URL
"""

import logging
import os
import re
from datetime import datetime
from html import escape
from html.parser import HTMLParser

import boto3
from botocore.exceptions import ClientError

logger = logging.getLogger(__name__)

_REGION = os.environ.get("REGION", "")
ses = boto3.client("ses", region_name=_REGION) if _REGION else boto3.client("ses")

FROM_EMAIL = os.environ.get("SES_FROM_EMAIL", "")
FROM_DISPLAY = f'"WMF Japan" <{FROM_EMAIL}>' if FROM_EMAIL else ""
REPLY_TO_EMAIL = "info@wmfjapan.com"
FRONTEND_URL = os.environ.get("FRONTEND_URL", "").rstrip("/")
LOGO_URL = f"{FRONTEND_URL}/logo.png" if FRONTEND_URL else ""

COLORS = {
    "ink": "#1b2621",
    "muted": "#5d6b65",
    "faint": "#8b9993",
    "page_bg": "#edf3ef",
    "card_bg": "#ffffff",
    "border": "#e3e9e6",
    "table_bg": "#f8faf9",
    "header_from": "#2a3832",
    "header_to": "#1c2722",
    "header_text": "#cfe8da",
    "brand": "#3d7258",
}

TONES = {
    "success": {"fg": "#3d7258", "bg": "#edf7f1"},
    "pending": {"fg": "#a9741f", "bg": "#fdf3e2"},
    "danger": {"fg": "#b3453f", "bg": "#fbebea"},
}


def _logo_html() -> str:
    if not LOGO_URL:
        return f'<span style="color:{COLORS["header_text"]};font-size:20px;font-weight:800;">WMF Japan</span>'
    return (
        f'<img src="{LOGO_URL}" alt="WMF Japan" width="120" height="65" '
        f'style="display:block;margin:0 auto;border:0;max-width:120px;height:auto;" />'
    )


def _banner(text: str, tone: str = "success") -> str:
    colors = TONES.get(tone, TONES["success"])
    return (
        f'<div style="background:{colors["bg"]};border-left:4px solid {colors["fg"]};'
        f'padding:12px 16px;margin:16px 0;border-radius:2px;">'
        f'<p style="margin:0;color:{COLORS["ink"]};font-size:14px;font-weight:700;">{text}</p></div>'
    )


def _button(label: str, url: str) -> str:
    if not label or not url:
        return ""
    return f"""
    <div style="text-align:center;margin:22px 0 2px;">
      <a href="{url}" style="display:inline-block;background:{COLORS['brand']};color:#f3f8f5;
         padding:12px 28px;text-decoration:none;border-radius:6px;font-size:14px;font-weight:700;">{label}</a>
    </div>
    """


def _detail_table(rows: list) -> str:
    """rows: list of (label, value) or (label, value, is_strong) tuples."""
    cells = []
    for row in rows:
        label, value = row[0], row[1]
        strong = row[2] if len(row) > 2 else False
        weight = "font-weight:700;" if strong else ""
        cells.append(
            f'<tr><td style="padding:5px 0;color:{COLORS["muted"]};font-size:13px;">{label}</td>'
            f'<td style="padding:5px 0;color:{COLORS["ink"]};font-size:13px;{weight}text-align:right;">{value}</td></tr>'
        )
    return f"""
    <div style="background:{COLORS['table_bg']};border:1px solid {COLORS['border']};padding:16px 18px;margin:16px 0 0;border-radius:6px;">
      <table width="100%" cellpadding="0" cellspacing="0">{''.join(cells)}</table>
    </div>
    """


def _shell(*, kicker: str, title: str, body_html: str, preheader: str = "") -> str:
    """preheader (optional): hidden text placed right at the top of <body>
    for the inbox preview snippet — completely invisible in the rendered
    email, so it changes no visible design/layout. Empty by default, so
    every existing caller is unaffected."""
    preheader_html = (
        f'<div style="display:none;max-height:0;overflow:hidden;font-size:1px;line-height:1px;'
        f'color:{COLORS["page_bg"]};opacity:0;">{escape(preheader)}</div>'
        if preheader
        else ""
    )
    return f"""
<!DOCTYPE html>
<html>
<head><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1"></head>
<body style="margin:0;padding:0;background:{COLORS['page_bg']};font-family:'Noto Sans','Helvetica Neue',Arial,sans-serif;">
{preheader_html}
<table width="100%" cellpadding="0" cellspacing="0" style="background:{COLORS['page_bg']};padding:32px 0;">
<tr><td align="center">
<table width="560" cellpadding="0" cellspacing="0" style="background:{COLORS['card_bg']};border-radius:10px;overflow:hidden;border:1px solid {COLORS['border']};box-shadow:0 6px 24px rgba(27,38,33,0.08);">
  <tr><td style="background:linear-gradient(180deg,{COLORS['header_from']} 0%,{COLORS['header_to']} 100%);padding:30px 30px 24px;text-align:center;">
    {_logo_html()}
    <p style="margin:14px 0 0;color:{COLORS['header_text']};font-size:12px;letter-spacing:0.12em;text-transform:uppercase;font-weight:700;">{kicker}</p>
  </td></tr>
  <tr><td style="padding:30px;">
    <h1 style="margin:0 0 16px;color:{COLORS['ink']};font-size:19px;font-weight:800;">{title}</h1>
    {body_html}
  </td></tr>
  <tr><td style="padding:18px 30px;border-top:1px solid {COLORS['border']};text-align:center;background:{COLORS['table_bg']};">
    <p style="margin:0;color:{COLORS['faint']};font-size:12px;">
      Questions? Write to us at <a href="mailto:info@wmfjapan.com" style="color:{COLORS['brand']};font-weight:700;text-decoration:none;">info@wmfjapan.com</a>
    </p>
    <p style="margin:6px 0 0;color:{COLORS['faint']};font-size:11px;">&copy; 2026 WMF Japan</p>
  </td></tr>
  <tr><td style="padding:18px 30px;border-top:1px solid {COLORS['border']};text-align:center;">
    <div style="margin:auto;padding:14px 16px;background:{TONES['success']['bg']};">
      <p style="margin:0 0 6px;color:{COLORS['ink']};font-size:14px;font-weight:700;">Host Your Event With Us</p>
      <p style="margin:0;color:{COLORS['muted']};font-size:13px;line-height:1.6;">
        Planning an event of your own? Reach us at <a href="mailto:hello@cloudnadi.com" style="color:{COLORS['brand']};text-decoration:none;font-weight:700;">hello@cloudnadi.com</a>.
      </p>
    </div>
  </td></tr>
</table>
</td></tr>
</table>
</body>
</html>
"""


class _TextExtractor(HTMLParser):
    _SKIP_TAGS = {"head", "style", "title", "script"}
    _BLOCK_TAGS = {"p", "div", "h1", "h2", "h3", "tr", "table", "br"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []
        self._skip = 0
        self._href = None
        self._link_text = []
        self._cells_in_row = 0

    def handle_starttag(self, tag, attrs):
        if tag in self._SKIP_TAGS:
            self._skip += 1
        elif tag == "tr":
            self._cells_in_row = 0
        elif tag in self._BLOCK_TAGS:
            self.parts.append("\n")
        elif tag == "td":
            # Detail-table rows are (label, value) cell pairs → "Label: Value".
            if self._cells_in_row:
                self.parts.append(": ")
            self._cells_in_row += 1
        elif tag == "a":
            self._href = dict(attrs).get("href")
            self._link_text = []
        elif tag == "img":
            alt = dict(attrs).get("alt")
            if alt:
                self.parts.append(alt)

    def handle_endtag(self, tag):
        if tag in self._SKIP_TAGS:
            self._skip = max(0, self._skip - 1)
        elif tag in self._BLOCK_TAGS:
            self.parts.append("\n")
        elif tag == "a" and self._href:
            text = "".join(self._link_text).strip()
            if self._href not in (text, f"mailto:{text}"):
                self.parts.append(f" ({self._href})")
            self._href = None

    def handle_data(self, data):
        if self._skip:
            return
        self.parts.append(data)
        if self._href:
            self._link_text.append(data)


def _html_to_text(html_body: str) -> str:
    parser = _TextExtractor()
    parser.feed(html_body)
    lines = [re.sub(r"[ \t\r\f\v]+", " ", line).strip() for line in "".join(parser.parts).split("\n")]
    text = "\n".join(lines)
    return re.sub(r"\n{3,}", "\n\n", text).strip() + "\n"


def _message(subject: str, html_body: str) -> dict:
    return {
        "Subject": {"Data": subject, "Charset": "UTF-8"},
        "Body": {
            "Text": {"Data": _html_to_text(html_body), "Charset": "UTF-8"},
            "Html": {"Data": html_body, "Charset": "UTF-8"},
        },
    }


def _send(to_addresses: list, subject: str, html_body: str) -> bool:
    to_addresses = [addr for addr in to_addresses if addr]
    if not FROM_EMAIL or not to_addresses:
        logger.warning("Member announcement emailer: skipping send (missing FROM_EMAIL or recipients)")
        return False
    try:
        ses.send_email(
            Source=FROM_DISPLAY,
            Destination={"ToAddresses": to_addresses},
            Message=_message(subject, html_body),
            ReplyToAddresses=[REPLY_TO_EMAIL],
        )
        return True
    except ClientError:
        logger.exception("Member announcement emailer: SES send failed to %s", to_addresses)
        return False




# ── Announcements ────────────────────────────────────────────────────────────

MEMBERSHIP_URL = f"{FRONTEND_URL}/member/membership" if FRONTEND_URL else ""
IGNORE_NOTE = (
    "Already registered or renewed? Please ignore this email — thank you for being part of the "
    "WMF Japan family!"
)


def _p(text: str, *, bold_lead: str = "") -> str:
    lead = f'<strong style="color:{COLORS["ink"]};">{escape(bold_lead)}</strong> ' if bold_lead else ""
    return f'<p style="margin:0 0 14px;color:{COLORS["muted"]};font-size:14px;line-height:1.7;">{lead}{escape(text)}</p>'


def _greeting(recipient: dict) -> str:
    name = str(recipient.get("name", "") or "").strip()
    return (
        f'<p style="margin:0 0 14px;color:{COLORS["ink"]};font-size:14px;font-weight:700;">'
        f'{"Dear " + escape(name) + "," if name else "Dear member,"}</p>'
    )


def _ignore_note(text: str = "") -> str:
    return (
        f'<p style="margin:18px 0 0;color:{COLORS["faint"]};font-size:13px;text-align:center;font-style:italic;">'
        f"{escape(text or IGNORE_NOTE)}</p>"
    )


def _long_date(iso_date: str) -> str:
    try:
        return datetime.strptime(iso_date, "%Y-%m-%d").strftime("%A, %-d %B %Y")
    except ValueError:
        return iso_date


def _short_date(iso_date: str) -> str:
    try:
        return datetime.strptime(str(iso_date)[:10], "%Y-%m-%d").strftime("%-d %b %Y")
    except ValueError:
        return str(iso_date)


def _year(code: str) -> str:
    match = re.fullmatch(r"(\d{4})-(\d{2})", str(code or ""))
    return f"{match.group(1)}–{match.group(2)}" if match else str(code or "")


METHOD_LABELS = {"square": "Credit / Debit Card", "paypay": "PayPay", "bank": "Bank Transfer"}
CONFIRMATION_DAYS = 3  # how long a manual (PayPay / bank) payment takes to confirm
PROFILE_URL = f"{FRONTEND_URL}/member/profile" if FRONTEND_URL else ""


def _yen(amount) -> str:
    return f"&yen;{int(amount or 0):,}"


AGE_GROUP_LABELS = (
    ("adults", "adult(s) 18+"),
    ("teens", "teenager(s) 13–17"),
    ("kids", "child(ren) 6–12"),
    ("preschoolers", "preschooler(s) 3–5"),
    ("toddlers", "toddler(s) 0–3"),
)
ADULT_STATUS_TEXT = {"member": "NPO member", "pending": "membership payment pending"}


def _attendees_text(counts: dict) -> str:
    return ", ".join(f"{counts[key]} {label}" for key, label in AGE_GROUP_LABELS if counts.get(key))


def _adult_attendees_html(counts: dict) -> str:
    """The named adults (the registrant first) with their NPO membership status."""
    lines = []
    for adult in counts.get("adultAttendees") or []:
        who = escape(adult.get("name") or adult.get("email", ""))
        email = escape(adult.get("email", ""))
        detail = f" ({email})" if adult.get("name") and email else ""
        status = ADULT_STATUS_TEXT.get(adult.get("status"), "")
        lines.append(f"{who}{detail}" + (f" — {escape(status)}" if status else ""))
    return "<br>".join(lines)


def _agm_rows(agm: dict) -> list:
    """Detail rows for an AGM (date, time, venue, access, remote, refreshments, map)."""
    time = "–".join(x for x in (agm.get("startTime", ""), agm.get("endTime", "")) if x)
    rows = [
        ("Date", escape(_long_date(agm.get("date", "")))),
        ("Time", escape(time)),
        ("Venue", escape(" · ".join(x for x in (agm.get("venue", ""), agm.get("venueJa", "")) if x))),
        ("Access", escape(agm.get("access", ""))),
        ("Remote", escape(agm.get("remoteNote", ""))),
        ("Refreshments", escape(agm.get("refreshmentsNote", ""))),
    ]
    rows = [row for row in rows if row[1]]
    map_url = agm.get("mapUrl", "")
    if map_url.startswith(("http://", "https://")):
        rows.append(("Map", f'<a href="{escape(map_url, quote=True)}" style="color:{COLORS["brand"]};font-weight:700;">Open in Google Maps</a>'))
    return rows


def _agm_registration_rows(agm: dict, counts: dict) -> list:
    mode = "Remote" if counts.get("attendance") == "remote" else "In person"
    total = counts.get("totalAttendees") or sum(int(counts.get(k, 0) or 0) for k, _ in AGE_GROUP_LABELS)
    rows = [
        ("Event", escape(agm.get("title") or "Annual General Meeting")),
        *_agm_rows(agm),
        ("Attending", mode),
        ("Who", escape(_attendees_text(counts))),
    ]
    adults = _adult_attendees_html(counts)
    if adults:
        rows.append(("Adult attendees", adults))
    rows.append(("Total people", str(total), True))
    return rows


def build_order_received(recipient: dict, year: str, lines: list, total: int, method: dict,
                         agm: dict = None, agm_counts: dict = None) -> tuple:
    """(subject, html) — the order was recorded and is awaiting payment."""
    y = _year(year)
    automatic = method.get("key") == "square"
    rows = [(escape(label), _yen(amount)) for label, amount in lines]
    rows += [("Total", _yen(total), True), ("Payment method", escape(method.get("label", "")))]
    instructions = (
        f'<p style="margin:0 0 14px;color:{COLORS["muted"]};font-size:14px;line-height:1.7;white-space:pre-line;">'
        f'{escape(method["instructions"])}</p>'
        if method.get("instructions")
        else ""
    )
    if automatic:
        next_text = "Your membership starts automatically as soon as your payment completes."
    else:
        next_text = (
            f"Once you have paid, we will confirm your payment within {CONFIRMATION_DAYS} days and your "
            "membership will start then. You don't need to do anything else."
        )
    agm_html = ""
    if agm and agm_counts:
        agm_html = (
            f'<h2 style="margin:26px 0 0;color:{COLORS["ink"]};font-size:16px;font-weight:800;">AGM registration</h2>'
            f"{_detail_table(_agm_registration_rows(agm, agm_counts))}"
        )
    body = f"""
    {_greeting(recipient)}
    {_p(f"Thank you! We have received your WMF Japan NPO membership request for {y}.")}
    {_detail_table(rows)}
    {_banner("Awaiting payment — your membership starts once your payment is confirmed.", "pending")}
    {instructions}
    {_button("Pay Now", method.get("url", ""))}
    {_p(next_text)}
    {agm_html}
    """
    return (
        f"WMF Japan NPO membership {y} — awaiting payment",
        _shell(kicker="NPO Membership", title="We've Received Your Request", body_html=body,
               preheader=f"Your NPO membership request for {y} is awaiting payment."),
    )


def _whatsapp_block(url: str, year: str) -> str:
    """Invite to the members' WhatsApp group (empty when no link is configured for the year)."""
    if not (url and url.startswith("https://")):
        return ""
    safe = escape(url, quote=True)
    return f"""
    {_p(f"As a {_year(year)} NPO member you are welcome to join our members' WhatsApp group for news and updates.")}
    <div style="text-align:center;margin:18px 0 2px;">
      <a href="{safe}" style="display:inline-block;background:#25D366;color:#073b1d;
         padding:12px 28px;text-decoration:none;border-radius:6px;font-size:14px;font-weight:800;">Join the WhatsApp Group</a>
    </div>
    <p style="margin:10px 0 0;color:{COLORS['faint']};font-size:12px;text-align:center;">
      Members only — please don't share this link.
    </p>
    """


def build_payment_confirmed(recipient: dict, txn: dict, whatsapp_url: str = "") -> tuple:
    """(subject, html) — payment received and the membership is confirmed."""
    y = _year(txn.get("membershipYear", ""))
    amount = txn.get("orderTotal") or txn.get("membershipAmountPaid", 0)
    for_spouse = txn.get("kind") == "spouse-payment"
    rows = [
        ("Membership", escape(f"{'Spouse membership' if for_spouse else 'NPO membership'} {y}".strip())),
        ("Amount paid", _yen(amount)),
        ("Payment method", escape(METHOD_LABELS.get(str(txn.get("paidVia", "")), ""))),
    ]
    if not for_spouse and txn.get("membDateFrom") and txn.get("membDateTo"):
        rows.append(("Membership period", escape(f"{_short_date(txn['membDateFrom'])} – {_short_date(txn['membDateTo'])}")))
    rows = [row for row in rows if row[1]]
    lead = (
        "We have received your payment — your spouse's membership is now confirmed."
        if for_spouse
        else "We have received your payment — your membership is now confirmed."
    )
    body = f"""
    {_greeting(recipient)}
    {_p(lead)}
    {_detail_table(rows)}
    {_banner("Payment received — thank you for being part of the WMF Japan family!", "success")}
    {_whatsapp_block(whatsapp_url, txn.get("membershipYear", "")) if not for_spouse else ""}
    {_button("View My Membership", PROFILE_URL)}
    """
    return (
        f"WMF Japan NPO membership {y} — payment received",
        _shell(kicker="NPO Membership", title="Payment Received", body_html=body,
               preheader=f"Your NPO membership for {y} is confirmed."),
    )


def build_signed_up_by_other(recipient: dict, payer_email: str, year: str) -> tuple:
    """(subject, html) — someone has ordered this member's NPO membership; awaiting their payment."""
    y = _year(year)
    body = f"""
    {_greeting(recipient)}
    {_p(f"{payer_email} has signed you up for WMF Japan NPO membership for {y}.")}
    {_banner("Your membership starts once their payment is confirmed. We'll email you then.", "pending")}
    {_button("View My Membership", PROFILE_URL)}
    """
    return (
        f"WMF Japan NPO membership {y} — you've been signed up",
        _shell(kicker="NPO Membership", title="You've Been Signed Up", body_html=body,
               preheader=f"{payer_email} has signed you up for NPO membership {y}."),
    )


def build_confirmed_by_other(recipient: dict, txn: dict, payer_email: str, whatsapp_url: str = "") -> tuple:
    """(subject, html) — this member's membership was paid for by someone else and is now confirmed."""
    y = _year(txn.get("membershipYear", ""))
    rows = [("Membership", escape(f"NPO membership {y}")), ("Paid by", escape(payer_email))]
    if txn.get("membDateFrom") and txn.get("membDateTo"):
        rows.append(("Membership period", escape(f"{_short_date(txn['membDateFrom'])} – {_short_date(txn['membDateTo'])}")))
    body = f"""
    {_greeting(recipient)}
    {_p("Your WMF Japan NPO membership has been paid for and is now confirmed. Welcome!")}
    {_detail_table(rows)}
    {_banner("Membership confirmed", "success")}
    {_whatsapp_block(whatsapp_url, txn.get("membershipYear", ""))}
    {_button("View My Membership", PROFILE_URL)}
    """
    return (
        f"WMF Japan NPO membership {y} — confirmed",
        _shell(kicker="NPO Membership", title="Your Membership Is Confirmed", body_html=body,
               preheader=f"Your NPO membership for {y} is confirmed."),
    )


def build_agm_registered(recipient: dict, agm: dict, counts: dict, updated: bool = False) -> tuple:
    """(subject, html) — AGM registration saved (or updated)."""
    title = agm.get("title") or "Annual General Meeting"
    body = f"""
    {_greeting(recipient)}
    {_p(f"Your AGM registration has been {'updated' if updated else 'received'}. Here are the details:")}
    {_detail_table(_agm_registration_rows(agm, counts))}
    {_banner("You're registered — we look forward to seeing you!", "success")}
    {_banner("Some adults' NPO membership payments are still awaiting confirmation. Only NPO members can attend the AGM, so their attendance is confirmed once those payments are received.", "pending") if any(a.get("status") == "pending" for a in counts.get("adultAttendees") or []) else ""}
    {_button("Manage My Registration", MEMBERSHIP_URL)}
    <p style="margin:18px 0 0;color:{COLORS['faint']};font-size:13px;text-align:center;font-style:italic;">
      Need to change your numbers? Update or cancel your registration from the membership page.
    </p>
    """
    return (
        f"{title} — registration {'updated' if updated else 'confirmed'}",
        _shell(kicker="Annual General Meeting", title="You're Registered for the AGM", body_html=body,
               preheader=f"{title}: your registration is {'updated' if updated else 'confirmed'}."),
    )


def build_agm_guest(recipient: dict, agm: dict, counts: dict, registrant_name: str, registrant_email: str,
                    status: str = "member") -> tuple:
    """(subject, html) — someone has listed this NPO member as an adult attending the AGM with them."""
    title = agm.get("title") or "Annual General Meeting"
    who = registrant_name or registrant_email
    mode = "remotely" if counts.get("attendance") == "remote" else "in person"
    rows = [("Event", escape(title)), *_agm_rows(agm), ("Attending", mode.capitalize()),
            ("Registered by", escape(f"{who} ({registrant_email})" if registrant_name else registrant_email))]
    pending = status == "pending"
    body = f"""
    {_greeting(recipient)}
    {_p(f"{escape(who)} has registered you as an adult attending {escape(title)} with them {mode}. Here are the details:")}
    {_detail_table(rows)}
    {_banner("Your NPO membership payment is still awaiting confirmation. Only NPO members can attend the AGM, so your attendance is confirmed once the payment is received.", "pending") if pending else _banner("You're on the list — we look forward to seeing you!", "success")}
    {_button("View My Membership", MEMBERSHIP_URL)}
    <p style="margin:18px 0 0;color:{COLORS['faint']};font-size:13px;text-align:center;font-style:italic;">
      Can't make it? Please let {escape(who)} know so they can update the registration.
    </p>
    """
    return (
        f"{title} — you've been registered",
        _shell(kicker="Annual General Meeting", title="You're Attending the AGM", body_html=body,
               preheader=f"{who} has registered you for {title}."),
    )


def send_agm_guest_emails(agm: dict, record: dict, registrant_email: str, registrant_name: str,
                          already_notified: set = frozenset()) -> int:
    """Email each additional adult in `record` (not the registrant, nor anyone in `already_notified`)."""
    sent = 0
    for adult in record.get("adultAttendees") or []:
        email = str(adult.get("email", "")).strip().lower()
        if not email or email == registrant_email or email in already_notified:
            continue
        subject, html = build_agm_guest({"name": adult.get("name", "")}, agm, record, registrant_name,
                                        registrant_email, adult.get("status", "member"))
        sent += bool(_send([email], subject, html))
    return sent


def build_membership_cancelled(recipient: dict, txn: dict, cancelled_by_payer: str = "") -> tuple:
    """(subject, html) — an NPO membership was cancelled by the committee, with the refund (if any)."""
    y = _year(txn.get("membershipYear", ""))
    rows = [("Membership", escape(f"NPO membership {y}".strip()))]
    if txn.get("cancelledMembDateFrom") and txn.get("cancelledMembDateTo"):
        rows.append(("Membership period", escape(
            f"{_short_date(txn['cancelledMembDateFrom'])} – {_short_date(txn['cancelledMembDateTo'])}")))
    if cancelled_by_payer:
        rows.append(("Paid by", escape(cancelled_by_payer)))
    status = txn.get("refundStatus", "none")
    refunded = status in ("processing", "completed", "refunded-manually")
    if refunded:
        rows.append(("Refund", f"&yen;{int(txn.get('refundAmount') or 0):,}", True))
        rows.append(("Refund method", "Back to your card (may take 5–10 business days to appear)"
                     if txn.get("paidVia") == "square" else "Paid out by the committee"))
    if txn.get("cancelReason"):
        rows.append(("Reason", escape(txn["cancelReason"])))
    if refunded:
        note = _banner("Your membership has been cancelled and your refund has been issued.", "success")
    elif status == "included-in-order-refund":
        note = _banner("This membership was part of an order that has been cancelled and refunded to the payer.", "pending")
    else:
        note = _banner("Your membership has been cancelled. No refund applies to this cancellation.", "pending")
    body = f"""
    {_greeting(recipient)}
    {_p("Your WMF Japan NPO membership has been cancelled.")}
    {_detail_table(rows)}
    {note}
    {_p("Any AGM registration that relied on this membership may be reviewed by the committee. You're welcome to join again at any time.")}
    {_button("View My Membership", MEMBERSHIP_URL)}
    <p style="margin:18px 0 0;color:{COLORS['faint']};font-size:13px;text-align:center;font-style:italic;">
      Questions? Write to info@wmfjapan.com.
    </p>
    """
    return (
        f"WMF Japan NPO membership {y} — cancelled",
        _shell(kicker="NPO Membership", title="Your Membership Was Cancelled", body_html=body,
               preheader=f"Your NPO membership {y} has been cancelled."),
    )


def build_payment_failed(recipient: dict, txn: dict) -> tuple:
    """(subject, html) — a card payment didn't go through; the request stays open
    and the member can retry from the membership page."""
    y = _year(txn.get("membershipYear", ""))
    amount = int(txn.get("orderTotal") or txn.get("membershipAmountPaid", 0) or 0)
    for_spouse = txn.get("kind") == "spouse-payment"
    rows = [
        ("Membership", escape(f"{'Spouse membership' if for_spouse else 'NPO membership'} {y}".strip())),
        ("Amount", f"&yen;{amount:,}"),
        ("Payment method", "Credit / Debit Card"),
    ]
    body = f"""
    {_greeting(recipient)}
    {_p(f"Your card payment for {'your spouse' + chr(39) + 's' if for_spouse else 'your'} WMF Japan NPO membership didn't go through, so the membership hasn't started yet.")}
    {_detail_table(rows)}
    {_p("This can happen if the card was declined or the payment was cancelled. You can try again with the same card or use a different one.")}
    {_banner("Your request is still open — nothing needs to be re-entered.", "pending")}
    {_button("Retry Payment", MEMBERSHIP_URL)}
    <p style="margin:18px 0 0;color:{COLORS['faint']};font-size:13px;text-align:center;font-style:italic;">
      If you think you were charged, please write to info@wmfjapan.com.
    </p>
    """
    return (
        "Your WMF Japan NPO membership payment didn't go through",
        _shell(kicker="NPO Membership", title="Your Payment Didn't Go Through", body_html=body,
               preheader="Your card payment for WMF Japan NPO membership didn't go through. You can retry it online."),
    )


def build_agm(recipient: dict, agm: dict) -> tuple:
    """(subject, html) — the AGM invitation. The register button only appears
    while registration for this AGM is open."""
    title = agm.get("title") or "Annual General Meeting"
    time = "–".join(x for x in (agm.get("startTime", ""), agm.get("endTime", "")) if x)
    rows = _agm_rows(agm)
    registration_open = agm.get("enabled") is True
    body = f"""
    {_greeting(recipient)}
    {_p(f"You are invited to the {title}.")}
    {_detail_table(rows)}
    {_p(agm.get("notes", "")) if agm.get("notes") else ""}
    {_banner("Register your attendance online — in person or remote." if registration_open else "Registration will open soon — we will let you know.", "success" if registration_open else "pending")}
    {_button("Register for the AGM", MEMBERSHIP_URL) if registration_open else ""}
    <p style="margin:18px 0 0;color:{COLORS['faint']};font-size:13px;text-align:center;font-style:italic;">
      Looking forward to seeing you all there!
    </p>
    """
    preheader = f"{_long_date(agm.get('date', ''))} · {time}" if agm.get("date") else "You're invited to the WMF Japan AGM."
    subject = f"{title} — {_long_date(agm.get('date', ''))}" if agm.get("date") else title
    return subject, _shell(kicker="Annual General Meeting", title=escape(title), body_html=body, preheader=preheader)


def build_join(recipient: dict, year: str, fee: int) -> tuple:
    y = _year(year)
    fee_line = f" The fee is ¥{fee:,} per person." if fee else ""
    sections = [
        ("Member pricing", "NPO members enjoy member pricing at our events, such as discounted Onam adult tickets."),
        ("Bring your family", "Join or renew yourself and your spouse together, in a single payment."),
        ("Be part of the AGM", "Register for the Annual General Meeting online, in person or remotely."),
    ]
    sections_html = "".join(_p(text, bold_lead=title + ".") for title, text in sections)
    body = f"""
    {_greeting(recipient)}
    {_p(f"Membership of the WMF Japan NPO for {y} is open — register, or renew if you are already a member.{fee_line}")}
    {sections_html}
    {_button("Register / Renew Membership", MEMBERSHIP_URL)}
    {_ignore_note()}
    """
    return (
        f"Join or renew the WMF Japan NPO — membership {y}",
        _shell(kicker="NPO Membership", title=f"NPO Membership {y} Is Open", body_html=body,
               preheader="Register or renew your WMF Japan NPO membership online."),
    )


def build_whatsapp(recipient: dict, year: str, url: str) -> tuple:
    """(subject, html) — WhatsApp group invite for members covered for `year`."""
    y = _year(year)
    body = f"""
    {_greeting(recipient)}
    {_p(f"Thank you for being a WMF Japan NPO member for {y}.")}
    {_whatsapp_block(url, year)}
    {_ignore_note("Already joined the group? Please ignore this email — thank you for being part of the WMF Japan family!")}
    """
    return (
        f"Join the WMF Japan members' WhatsApp group ({y})",
        _shell(kicker="NPO Membership", title="Join Our WhatsApp Group", body_html=body,
               preheader=f"Your invite to the WMF Japan members' WhatsApp group for {y}."),
    )


def build_renew(recipient: dict, year: str, fee: int) -> tuple:
    y = _year(year)
    until = recipient.get("validUntil", "")
    until_line = f" Your current membership runs until {_long_date(until)}." if until else ""
    fee_line = f" The fee is ¥{fee:,} per person." if fee else ""
    body = f"""
    {_greeting(recipient)}
    {_p(f"Thank you for being a WMF Japan NPO member.{until_line}")}
    {_p(f"It is time to renew for {y}, so you keep your member benefits.{fee_line} You can also renew your spouse's membership in the same payment.")}
    {_banner(f"Renew your membership for {y} online in just a few minutes.", "pending")}
    {_button("Renew My Membership", MEMBERSHIP_URL)}
    {_ignore_note()}
    """
    return (
        f"Please renew your WMF Japan NPO membership for {y}",
        _shell(kicker="NPO Membership", title=f"Renew Your Membership for {y}", body_html=body,
               preheader=f"Renew your WMF Japan NPO membership for {y}."),
    )
