"""
Shared email templates + SES senders for the Celebrity Dinner reservation
lifecycle — the standalone (registration-scoped) sign-up and the guest
(no-registration) sign-up alike, since both produce the same
celebrity_dinner_registrations table record shape (see
celebrity_dinner_logic.py's normalize_dinner_registration).

This module is COPIED into every Lambda that needs it (my_registrations,
manage_celebrity_dinner, celebrity_dinner_webhook) — same convention as
celebrity_dinner_logic.py itself, and closely mirrors the seat-upgrade
plugin's own seat_upgrade_emailer.py. It only ever works off a `dinner_reg`
dict (a celebrity_dinner_registrations table item, raw or normalized — both
use the same field names), never the underlying event registration or
config, so every call site — including the payment-provider webhook, which
never sees a registration at all — can use it without an extra table
lookup.

Unlike the seat-upgrade plugin's emailer, these templates are NOT
admin-editable: Celebrity Dinner is a core app feature, not a plugin, and
has no Email Templates admin tab. Revising the copy means editing
DEFAULT_TEMPLATES below directly.

Env vars:
  REGION, EVENTS_TABLE, SES_FROM_EMAIL, ADMIN_EMAIL, FRONTEND_URL
"""

import logging
import os
import re
from datetime import datetime
from html import escape
from html.parser import HTMLParser
from zoneinfo import ZoneInfo

import boto3
from botocore.exceptions import ClientError

# The event and its Celebrity Dinner are Japan-only — dinnerDate is stored as
# a true UTC instant (the admin form's toIsoString() converts the browser's
# local wall-clock entry to UTC before saving), but the Lambda runtime itself
# is UTC, so it must be converted back to Asia/Tokyo before display or these
# emails show the raw UTC clock digits instead of the actual Japan-local
# dinner time. Duplicated in plugins/seat-upgrade/backend/lambdas/
# seat_upgrade_emailer.py — same "copied module" convention as the rest of
# this pair (see that file's own JST constant).
JST = ZoneInfo("Asia/Tokyo")

logger = logging.getLogger(__name__)

_REGION = os.environ.get("REGION", "")
ses = boto3.client("ses", region_name=_REGION) if _REGION else boto3.client("ses")
dynamodb = boto3.resource("dynamodb", region_name=_REGION) if _REGION else boto3.resource("dynamodb")

EVENTS_TABLE = os.environ.get("EVENTS_TABLE", "")
FROM_EMAIL = os.environ.get("SES_FROM_EMAIL", "")
# A friendly display name on the Source/From header — a bare address reads
# more like an automated bulk sender, one of the signals mail providers
# weigh toward Promotions/spam. Mirrors plugins/seat-selection/backend/
# seat_purchase_emailer.py's own FROM_DISPLAY/REPLY_TO_EMAIL treatment.
FROM_DISPLAY = f'"WMF Japan" <{FROM_EMAIL}>' if FROM_EMAIL else ""
REPLY_TO_EMAIL = "info@wmfjapan.com"
ADMIN_EMAIL = os.environ.get("ADMIN_EMAIL", "")
FRONTEND_URL = os.environ.get("FRONTEND_URL", "").rstrip("/")
# frontend/public/logo.png ships as a static file, so it's served at this
# exact path off the frontend's own domain — no separate asset upload needed.
LOGO_URL = f"{FRONTEND_URL}/logo.png" if FRONTEND_URL else ""

# ── Brand palette — same values as seat_upgrade_emailer.py's COLORS/TONES,
# kept as its own copy rather than imported since these are two independent
# deployment packages (see this module's own docstring).
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


def _event_name(event_type: str) -> str:
    """Best-effort friendly event name; falls back to the raw eventType."""
    if not EVENTS_TABLE or not event_type:
        return event_type or "your event"
    try:
        item = dynamodb.Table(EVENTS_TABLE).get_item(Key={"eventType": event_type}).get("Item")
        return (item or {}).get("eventName", event_type)
    except ClientError:
        return event_type


# ── Generic building blocks (identical pattern to seat_upgrade_emailer.py) ──


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
        f'padding:12px 16px;margin:0 0 16px;border-radius:2px;">'
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


# ── Dinner-specific helpers ──────────────────────────────────────────────────

# (display label, veg field, non-veg field) — field names match
# celebrity_dinner_logic.py's normalize_dinner_registration exactly.
DINNER_CATEGORIES = (
    ("Adults", "numberOfAdultsVeg", "numberOfAdultsNonVeg"),
    ("Kids (9-14)", "numberOfKids914Veg", "numberOfKids914NonVeg"),
    ("Kids (6-8)", "numberOfKids68Veg", "numberOfKids68NonVeg"),
    ("Kids (under 6)", "numberOfKidsUnder6Veg", "numberOfKidsUnder6NonVeg"),
)


def _guest_rows(dinner_reg: dict) -> list:
    """One (label, "N (V veg, NV non-veg)") row per category with at least
    one guest — categories with nothing booked are omitted."""
    rows = []
    for label, veg_field, non_veg_field in DINNER_CATEGORIES:
        veg = int(dinner_reg.get(veg_field, 0) or 0)
        non_veg = int(dinner_reg.get(non_veg_field, 0) or 0)
        total = veg + non_veg
        if total <= 0:
            continue
        rows.append((label, f"{total} ({veg} veg, {non_veg} non-veg)"))
    return rows


def _format_dinner_date(dinner_reg: dict) -> str:
    """Human-friendly rendering of the dinnerDate snapshot (e.g. "September
    30, 2026, 7:00 PM"), or "" if this booking predates the field / the
    admin hasn't set one yet — callers skip the row entirely in that case."""
    raw = str(dinner_reg.get("dinnerDate", "") or "")
    if not raw:
        return ""
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return raw
    if dt.tzinfo is not None:
        dt = dt.astimezone(JST)
    return dt.strftime("%B %-d, %Y, %-I:%M %p")


def _format_window_date(iso_str: str) -> str:
    """A registration-window timestamp (celebrity_dinner_logic.normalize_
    config's registrationStartsAt/registrationEndsAt, full ISO datetimes)
    rendered as just a human date (e.g. "October 1, 2026") for "register
    by ..." copy — falls back to the raw string if it doesn't parse."""
    iso_str = str(iso_str or "").strip()
    if not iso_str:
        return iso_str
    try:
        dt = datetime.fromisoformat(iso_str.replace("Z", "+00:00"))
    except ValueError:
        return iso_str
    if dt.tzinfo is not None:
        dt = dt.astimezone(JST)
    return dt.strftime("%B %-d, %Y")


def _total_guests(dinner_reg: dict) -> int:
    return sum(
        int(dinner_reg.get(field, 0) or 0)
        for _, veg_field, non_veg_field in DINNER_CATEGORIES
        for field in (veg_field, non_veg_field)
    )


def _cta_url(dinner_reg: dict) -> str:
    """A registration-scoped booking is managed from My Registrations; a
    guest (no-registration) booking has its own dedicated page instead."""
    if not FRONTEND_URL:
        return ""
    return f"{FRONTEND_URL}/my/registrations" if dinner_reg.get("registrationId") else f"{FRONTEND_URL}/celebrity-dinner"


def _greeting_html(name: str) -> str:
    """A personal "Hi <name>," opener — personal-sounding mail is far more
    likely to land in Gmail's Primary tab than a generic headline-first one."""
    name = str(name or "").strip()
    text = f"Hi {escape(name)}," if name else "Hi,"
    return f'<p style="margin:0 0 14px;color:{COLORS["ink"]};font-size:14px;">{text}</p>'


# ── Editable-in-code templates (see module docstring — not admin-editable) ──
DEFAULT_TEMPLATES = {
    "request": {  # covers both a brand-new booking and an edit to a still-pending one
        "subject": "Your Celebrity Dinner booking for {eventName}",
        "title": "You're booked!",
        "tone": "pending",
        "bannerText": "Awaiting payment",
        "introText": "We've received your Celebrity Dinner booking for {eventName} ({guests}). "
                      "We'll confirm as soon as payment is received.",
        "ctaLabel": "View My Booking",
    },
    "paid": {
        "subject": "Payment received for your Celebrity Dinner booking — {eventName}",
        "title": "You're all set!",
        "tone": "success",
        "bannerText": "Payment received",
        "introText": "Your Celebrity Dinner booking for {eventName} is confirmed — see you there!",
        "ctaLabel": "View My Booking",
    },
    "refund_requested": {
        "subject": "Refund requested for your Celebrity Dinner booking — {eventName}",
        "title": "Cancellation received",
        "tone": "pending",
        "bannerText": "Refund requested",
        "introText": "Your Celebrity Dinner booking for {eventName} has been cancelled.",
        "ctaLabel": "",
    },
    "cancelled": {
        "subject": "Your Celebrity Dinner booking has been cancelled — {eventName}",
        "title": "Cancelled",
        "tone": "danger",
        "bannerText": "Cancelled",
        "introText": "Your Celebrity Dinner booking for {eventName} has been cancelled.",
        "ctaLabel": "",
    },
}


def _render_dinner_email(dinner_reg: dict, template_key: str, *, extra_message: str = "", banner_suffix: str = "") -> tuple:
    template = DEFAULT_TEMPLATES[template_key]
    event_name = _event_name(dinner_reg.get("eventType", ""))
    amount = f"¥{int(dinner_reg.get('totalAmount', 0) or 0):,}"
    total_guests = _total_guests(dinner_reg)
    guests_label = f"{total_guests} guest{'s' if total_guests != 1 else ''}"

    # Plain context for subject/title/banner (already bold via CSS); a
    # second, HTML-emphasized context only for the intro paragraph.
    plain_ctx = {"eventName": event_name, "amount": amount, "guests": guests_label}
    html_ctx = {**plain_ctx, "eventName": f"<strong>{event_name}</strong>"}

    subject = template["subject"].format(**plain_ctx)
    title = template["title"]
    banner = template["bannerText"] + banner_suffix
    message = template["introText"].format(**html_ctx)
    if extra_message:
        message = f"{message} {extra_message}"

    dinner_date = _format_dinner_date(dinner_reg)
    rows = [("Event", event_name)]
    if dinner_date:
        rows.append(("Date", dinner_date))
    rows += _guest_rows(dinner_reg) + [("Amount", amount, True)]
    cta_label = template.get("ctaLabel", "")
    cta_url = _cta_url(dinner_reg)
    body = (
        _greeting_html(dinner_reg.get("name", ""))
        + _banner(banner, template.get("tone", "success"))
        + f'<p style="margin:0;color:{COLORS["muted"]};font-size:14px;line-height:1.7;">{message}</p>'
        + _detail_table(rows)
        + (_button(cta_label, cta_url) if cta_label and cta_url else "")
    )
    return _shell(kicker="Celebrity Dinner", title=title, body_html=body), subject


# ── Plain-text alternative ───────────────────────────────────────────────────
#
# Every send carries a text/plain part alongside the HTML (multipart/
# alternative). HTML-only mail is one of the strongest signals Gmail uses to
# file a message under Promotions instead of Primary, so the text part is
# derived from the exact same rendered HTML rather than maintained by hand.


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
        logger.warning("Celebrity Dinner emailer: skipping send (missing FROM_EMAIL or recipients)")
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
        logger.exception("Celebrity Dinner emailer: SES send failed to %s", to_addresses)
        return False


def _send_admin(subject: str, html_body: str) -> bool:
    if not ADMIN_EMAIL or not FROM_EMAIL:
        return False
    try:
        ses.send_email(
            Source=FROM_EMAIL,
            Destination={"ToAddresses": [ADMIN_EMAIL]},
            Message=_message(subject, html_body),
        )
        return True
    except ClientError:
        logger.exception("Celebrity Dinner emailer: admin notification send failed")
        return False


# ── Registrant-facing ────────────────────────────────────────────────────────


def send_request_email(dinner_reg: dict, *, is_update: bool = False) -> bool:
    html, subject = _render_dinner_email(dinner_reg, "request", banner_suffix=" — updated" if is_update else "")
    return _send([dinner_reg.get("email")], subject, html)


def send_paid_email(dinner_reg: dict) -> bool:
    html, subject = _render_dinner_email(dinner_reg, "paid")
    return _send([dinner_reg.get("email")], subject, html)


def send_refund_requested_email(dinner_reg: dict, *, auto_initiated: bool) -> bool:
    note = (
        "We've started your refund with the payment provider — it can take a few business days to "
        "appear back on your original payment method."
        if auto_initiated
        else "Our team will process your refund by hand and reach out if we need anything from you."
    )
    html, subject = _render_dinner_email(dinner_reg, "refund_requested", extra_message=note)
    return _send([dinner_reg.get("email")], subject, html)


def send_cancelled_email(dinner_reg: dict) -> bool:
    """Final notice once an admin finalizes a refund_requested/paid booking to cancelled."""
    refunded = bool(dinner_reg.get("paymentMethod") or dinner_reg.get("paymentProvider"))
    html, subject = _render_dinner_email(
        dinner_reg, "cancelled", banner_suffix=" — refund complete" if refunded else ""
    )
    return _send([dinner_reg.get("email")], subject, html)


# ── Announcement (admin-triggered broadcast/selective) ──────────────────────
#
# Distinct from the request/paid/refund/cancelled lifecycle emails above,
# which only ever fire once someone has actually booked — this one invites a
# registration that hasn't booked yet. Deliberately no emojis anywhere in it
# (unlike the copy this was drafted from): an emoji-heavy subject/body is one
# of the signals mail providers weigh toward Promotions, and this email in
# particular is the most "marketing broadcast"-shaped one in this module, so
# it gets the same Primary-inbox treatment as the closely-related seat-
# selection announcement (personal greeting, no emoji subject, Reply-To,
# friendly From, hidden preheader — see seat_purchase_emailer.py's own
# send_announcement_email for the sibling implementation).


def send_announcement_email(reg: dict, event_name: str, dinner_config: dict) -> bool:
    """reg: a registrations-table item (any registrationOption/payment
    status — Celebrity Dinner sign-up isn't gated on either, see
    celebrity_dinner_logic.eligible_guest_counts_by_category). dinner_config:
    a normalized config (celebrity_dinner_logic.normalize_config) for an
    event where celebrity_dinner_logic.registration_window_status(config)
    is already confirmed "open" by the caller — this function doesn't
    re-check that itself, same division of labor as seat_purchase_
    emailer.send_announcement_email/its own caller context helper."""
    name = str(reg.get("name", "") or "").strip()
    greeting = f"Dear {escape(name)}," if name else "Dear,"

    ends_at = str(dinner_config.get("registrationEndsAt", "") or "")
    deadline_note = f"Please register by {_format_window_date(ends_at)}." if ends_at else ""

    sections = [
        ("An exclusive evening with the stars",
         "Spend a memorable evening with Stephen Devassy, Nithya Mammen and Sachin Warrier in a "
         "relaxed, family-friendly atmosphere."),
        ("Hear their stories",
         "Listen to interesting stories from their musical journeys, stage experiences, and their "
         "visit to Japan."),
        ("Meet and interact with the artists",
         "Get a chance to interact with the artists beyond the stage."),
        ("Capture the moment",
         "Take a memorable family photo with all three celebrities together."),
        ("Dinner and togetherness",
         "Enjoy a delicious dinner while connecting with fellow WMF Japan members and their "
         "families."),
        ("Be part of a special evening",
         "A unique opportunity to meet, talk, dine and create lasting memories with your favourite "
         "artists."),
    ]
    sections_html = "".join(
        f'<p style="margin:0 0 14px;color:{COLORS["muted"]};font-size:14px;line-height:1.7;">'
        f'<strong style="color:{COLORS["ink"]};">{escape(title)}.</strong> {escape(text)}</p>'
        for title, text in sections
    )

    cta_url = f"{FRONTEND_URL}/my/registrations" if FRONTEND_URL else ""

    body = f"""
    <p style="margin:0 0 14px;color:{COLORS['ink']};font-size:14px;font-weight:700;">{greeting}</p>
    <p style="margin:0 0 18px;color:{COLORS['muted']};font-size:14px;line-height:1.7;">
      The Celebrity Dinner for <strong>{event_name}</strong> is now open for registration.
    </p>
    {sections_html}
    {_banner("Limited seats available — register early to avoid disappointment.", "pending")}
    {f'<p style="margin:18px 0 0;"><strong style="color:{COLORS["brand"]};font-size:14px;">{deadline_note}</strong></p>' if deadline_note else ''}
    {_button('Book My Celebrity Dinner', cta_url) if cta_url else ''}
    <p style="margin:18px 0 0;color:{COLORS['faint']};font-size:13px;text-align:center;font-style:italic;">
      Don't just watch the stars on stage. Come meet them, talk with them, dine with them, and
      create memories together.
    </p>
    """
    subject = f"Join us for the Celebrity Dinner — {event_name}"
    preheader = (
        f"Registration is open until {_format_window_date(ends_at)}. "
        "Meet Stephen Devassy, Nithya Mammen and Sachin Warrier."
        if ends_at
        else "Meet Stephen Devassy, Nithya Mammen and Sachin Warrier at the Celebrity Dinner."
    )
    html = _shell(kicker="Celebrity Dinner", title="Join Us for the Celebrity Dinner", body_html=body, preheader=preheader)
    return _send([reg.get("email")], subject, html)


# ── Admin-facing ─────────────────────────────────────────────────────────────


def send_admin_notification(dinner_reg: dict, event_label: str, extra_note: str = "") -> bool:
    event_name = _event_name(dinner_reg.get("eventType", ""))
    subject = f"Celebrity Dinner {event_label}: {dinner_reg.get('name', '')}"
    rows = [
        ("Registrant", dinner_reg.get("name", "N/A")),
        ("Email", dinner_reg.get("email", "N/A")),
    ]
    dinner_date = _format_dinner_date(dinner_reg)
    if dinner_date:
        rows.append(("Date", dinner_date))
    rows += _guest_rows(dinner_reg)
    rows += [
        ("Amount", f"¥{int(dinner_reg.get('totalAmount', 0) or 0):,}", True),
        ("Payment method", dinner_reg.get("paymentProvider") or dinner_reg.get("paymentMethod", "N/A")),
        ("Status", dinner_reg.get("status", "N/A")),
        ("Booking id", dinner_reg.get("dinnerRegistrationId", "N/A")),
        ("Registration id", dinner_reg.get("registrationId") or "guest booking (no registration)"),
    ]
    body = (
        f'<p style="margin:0;color:{COLORS["muted"]};font-size:14px;line-height:1.6;">'
        f"Celebrity Dinner <strong>{event_label}</strong> for <strong>{event_name}</strong>.</p>"
        + _detail_table(rows)
        + (f'<p style="margin:14px 0 0;color:{COLORS["muted"]};font-size:13px;">{extra_note}</p>' if extra_note else "")
    )
    html = _shell(kicker="Admin Notification", title=f"Celebrity Dinner {event_label}", body_html=body)
    return _send_admin(subject, html)
