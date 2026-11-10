"""
Shared email templates + SES senders for the seat-selection plugin's paid
purchase flow.

This module is COPIED into every Lambda that needs it (manage_seats,
seat_purchase, seat_purchase_webhook) — same convention as
plugins/seat-upgrade's own emailer, and the host's lambdas/*/emailer.py
modules. It only ever works off a `purchase` record (the seat_purchases
table item), never the original registration or event config, so every
call site — including the payment-provider webhook, which never sees a
registration at all — can use it without an extra table lookup. The one
exception is the "paid" email's check-in ticket PDF, which optionally
looks up that purchase's registrationId in two other, host-provided
tables (see SEAT_UPGRADES_TABLE / CELEBRITY_DINNER_REGISTRATIONS_TABLE
below) to show a registrant's zone upgrade / celebrity dinner add-ons
alongside their seats.

Every email — registrant-facing and the admin notification alike — is built
from the same small set of generic pieces (_shell, _banner, _button,
_detail_table). Revising the *look* means editing COLORS or those functions
once.

Env vars: REGION, EVENTS_TABLE, SES_FROM_EMAIL, ADMIN_EMAIL, FRONTEND_URL,
and the optional SEAT_UPGRADES_TABLE / CELEBRITY_DINNER_REGISTRATIONS_TABLE.
"""

import json
import logging
import os
import re
from datetime import datetime
from html import escape
from html.parser import HTMLParser

import boto3
from boto3.dynamodb.conditions import Key
from botocore.exceptions import ClientError

import checkin_pdf
import seat_purchase_qr
import seat_selection_logic

logger = logging.getLogger(__name__)

_REGION = os.environ.get("REGION", "")
ses = boto3.client("ses", region_name=_REGION) if _REGION else boto3.client("ses")
dynamodb = boto3.resource("dynamodb", region_name=_REGION) if _REGION else boto3.resource("dynamodb")

EVENTS_TABLE = os.environ.get("EVENTS_TABLE", "")
FROM_EMAIL = os.environ.get("SES_FROM_EMAIL", "")
# A friendly display name on the Source/From header — a bare address (no
# name at all) reads more like an automated bulk sender, one of the
# signals mail providers weigh toward Promotions/spam. SES accepts
# "Display Name <address>" as its Source, and IAM's ses:FromAddress
# condition key matches on the parsed-out address either way, so this is
# safe alongside the plain FROM_EMAIL var used elsewhere (the SES email
# identity itself still has to be verified as the bare address — that
# identity is provisioned by Terraform against SES_FROM_EMAIL unchanged).
FROM_DISPLAY = f'"WMF Japan" <{FROM_EMAIL}>' if FROM_EMAIL else ""
ADMIN_EMAIL = os.environ.get("ADMIN_EMAIL", "")
# Same support address already shown in _shell()'s "Questions?" footer —
# set as Reply-To (not just a "from a noreply address, good luck" send) so
# a reply actually reaches someone, and so the message reads as real
# correspondence rather than a one-way broadcast. This is one of the
# signals Gmail/other providers weigh when deciding Primary vs Promotions.
REPLY_TO_EMAIL = "info@wmfjapan.com"
FRONTEND_URL = os.environ.get("FRONTEND_URL", "").rstrip("/")
LOGO_URL = f"{FRONTEND_URL}/logo.png" if FRONTEND_URL else ""

# Optional, host-provided (see plugins/seat-selection/terraform/variables.tf)
# — a purchase's own registrationId, when it has one, looks these up so the
# guest check-in ticket PDF can show a registrant's zone upgrade / celebrity
# dinner add-ons alongside their seats. Left unset by a host (or a second
# app mounting this module) simply means those sections never show.
SEAT_UPGRADES_TABLE = os.environ.get("SEAT_UPGRADES_TABLE", "")
CELEBRITY_DINNER_REGISTRATIONS_TABLE = os.environ.get("CELEBRITY_DINNER_REGISTRATIONS_TABLE", "")
_INACTIVE_ADDON_STATUSES = {"cancelled", "refunded"}

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
    if not EVENTS_TABLE or not event_type:
        return event_type or "your event"
    try:
        item = dynamodb.Table(EVENTS_TABLE).get_item(Key={"eventType": event_type}).get("Item")
        return (item or {}).get("eventName", event_type)
    except ClientError:
        return event_type


def _admin_notifications_enabled(event_type: str) -> bool:
    """Per-event opt-out for send_admin_notification below (eventInfo.
    seatSelection.adminNotificationsEnabled — see seat_selection_logic.
    parse_seat_selection_config's own docstring). Defaults to enabled, same
    as a lookup failure or a missing/unconfigured event, so this is a pure
    opt-out rather than something an admin has to turn on."""
    if not EVENTS_TABLE or not event_type:
        return True
    try:
        item = dynamodb.Table(EVENTS_TABLE).get_item(Key={"eventType": event_type}).get("Item")
    except ClientError:
        return True
    event_info = (item or {}).get("eventInfo", {})
    if isinstance(event_info, str):
        try:
            event_info = json.loads(event_info) if event_info.strip() else {}
        except json.JSONDecodeError:
            event_info = {}
    if not isinstance(event_info, dict):
        event_info = {}
    return seat_selection_logic.parse_seat_selection_config(event_info).get("adminNotificationsEnabled", True)


def _seats_label(purchase: dict) -> str:
    seat_ids = purchase.get("seatIds") or []
    count = len(seat_ids)
    return f"{count} seat{'s' if count != 1 else ''}"


def _zone_names(purchase: dict) -> str:
    breakdown = purchase.get("breakdown") or []
    names = [row.get("displayName") or row.get("category", "") for row in breakdown]
    return ", ".join(names) if names else "N/A"


# Mirrors plugins/seat-selection/frontend/rowLabels.js's seatDisplayLabel —
# duplicated here (rather than imported) because this module is itself
# copied into each Lambda that needs it, same convention as the rest of
# this file. Keep both in sync if the venue's row/seat shape ever changes.
_ROW_LABELS = ["い", "ろ", "は", "に", "ほ", "へ", "と", "ち", "り", "ぬ", "る", "を", "わ", "か", "よ", "た", "れ", "そ", "つ"]
_CENTER_COUNT = 11
_LEFT_COUNT_OVERRIDES = {1: 5, 2: 6, 3: 7}
_BLOCK_NAMES = {"left": "Left", "center": "Center", "right": "Right"}
_SEAT_ID_RE = re.compile(r"^R(\d+)C(\d+)$")


def _seat_display(seat_id: str) -> str:
    """"R03C11" -> "R03C11 (は-11 · Center)" — the raw seat ID plus the
    venue's own printed row letter/seat number and which block (left/
    center/right) it's in, so a registrant can find their actual seat."""
    match = _SEAT_ID_RE.match(seat_id or "")
    if not match:
        return seat_id
    row, col = int(match.group(1)), int(match.group(2))
    left_count = _LEFT_COUNT_OVERRIDES.get(row, 8)

    if col <= left_count:
        number = 9 if col == left_count else col + 8 - left_count
        block = "left"
    elif col <= left_count + _CENTER_COUNT:
        number = col - left_count + 8
        block = "center"
    else:
        number = col - left_count - _CENTER_COUNT + 19
        block = "right"

    row_label = _ROW_LABELS[row - 1] if 0 <= row - 1 < len(_ROW_LABELS) else str(row)
    return f"{seat_id} ({row_label}-{number} · {_BLOCK_NAMES[block]})"


def _seats_display_list(purchase: dict) -> str:
    return ", ".join(_seat_display(seat_id) for seat_id in (purchase.get("seatIds") or [])) or "N/A"


# ── Generic building blocks ──────────────────────────────────────────────────


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
    email (zero size, hidden, background-colored), so it changes no
    visible design/layout. Empty by default, so every existing caller is
    unaffected."""
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


def _greeting_html(name: str) -> str:
    """A personal "Hi <name>," opener — personal-sounding mail is far more
    likely to land in Gmail's Primary tab than a generic headline-first one."""
    name = str(name or "").strip()
    text = f"Hi {escape(name)}," if name else "Hi,"
    return f'<p style="margin:0 0 14px;color:{COLORS["ink"]};font-size:14px;">{text}</p>'


# ── Registrant-facing purchase lifecycle emails ─────────────────────────────
#
# Unlike seat-upgrade, there's no admin-editable-template layer here yet —
# copy is fixed in code. If that's needed later, follow seat-upgrade's
# EMAIL_TEMPLATE_* + a small overrides table for the same 4 keys.

_TEMPLATES = {
    "request": {
        "kicker": "Seat Purchase",
        "title": "We've got your request",
        "tone": "pending",
        "banner": "Awaiting payment",
        "intro": "We've received your request for {seats} in {zones} at {eventName}. "
        "We'll confirm as soon as payment is received.",
        "subject": "Your seat selection request for {eventName}",
        "ctaLabel": "View My Seats",
    },
    "paid": {
        "kicker": "Seat Purchase",
        "title": "You're all set!",
        "tone": "success",
        "banner": "Payment received",
        "intro": "Your seats for {eventName} are confirmed — see you there!",
        "subject": "Payment received for your seat selection — {eventName}",
        "ctaLabel": "View My Seats",
    },
    # Selected instead of "paid" whenever the purchase's own pricingEnabled
    # is false (see _render_purchase_email) — a free claim goes straight to
    # status "paid" (create_purchase_request never has a "pending" free
    # purchase to send a "request" email for in the first place), so this is
    # the one lifecycle email a pricing-disabled event ever sends. Same
    # shape as "paid", just without any language implying money changed
    # hands.
    "paid_free": {
        "kicker": "Seat Purchase",
        "title": "You're all set!",
        "tone": "success",
        "banner": "Confirmed",
        "intro": "Your seats for {eventName} are confirmed — see you there!",
        "subject": "Your seat selection is confirmed — {eventName}",
        "ctaLabel": "View My Seats",
    },
    "refund_requested": {
        "kicker": "Seat Purchase",
        "title": "Cancellation received",
        "tone": "pending",
        "banner": "Refund requested",
        "intro": "Your seats for {eventName} have been cancelled.",
        "subject": "Refund requested for your seat selection — {eventName}",
        "ctaLabel": "",
    },
    "cancelled": {
        "kicker": "Seat Purchase",
        "title": "Cancelled",
        "tone": "danger",
        "banner": "Cancelled",
        "intro": "Your seat purchase for {eventName} has been cancelled. Your original registration is unaffected.",
        "subject": "Your seat selection has been cancelled — {eventName}",
        "ctaLabel": "",
    },
}


def _render_purchase_email(purchase: dict, template_key: str, *, extra_message: str = "", banner_suffix: str = "") -> tuple:
    if template_key == "paid" and not purchase.get("pricingEnabled", True):
        template_key = "paid_free"
    template = _TEMPLATES[template_key]
    event_name = _event_name(purchase.get("eventType", ""))
    zones = _zone_names(purchase)
    seats = _seats_label(purchase)
    amount = f"¥{int(purchase.get('totalAmount', 0) or 0):,}"

    ctx = {"eventName": event_name, "zones": zones, "seats": seats, "amount": amount}
    html_ctx = {**ctx, "eventName": f"<strong>{event_name}</strong>", "zones": f"<strong>{zones}</strong>"}

    subject = template["subject"].format(**ctx)
    title = template["title"]
    banner = template["banner"] + banner_suffix
    message = template["intro"].format(**html_ctx)
    if extra_message:
        message = f"{message} {extra_message}"

    rows = [
        ("Event", event_name),
        ("Zones", zones),
        ("Seats", _seats_display_list(purchase)),
    ]
    # Nothing to charge, and nothing an admin needs to reconcile, once this
    # event has turned seat-selection pricing off entirely (see
    # seat_purchase_logic.create_purchase_request's `free`/`pricingEnabled`)
    # — wmfjapan's pricingEnabled defaults true for purchases from
    # before this field existed, so an older purchase still shows its amount.
    if purchase.get("pricingEnabled", True):
        rows.append(("Amount", amount, True))
    cta_label = template.get("ctaLabel", "")
    body = (
        _greeting_html(purchase.get("name", ""))
        + _banner(banner, template["tone"])
        + f'<p style="margin:0;color:{COLORS["muted"]};font-size:14px;line-height:1.7;">{message}</p>'
        + _detail_table(rows)
        + (_button(cta_label, f"{FRONTEND_URL}/my/registrations") if cta_label and FRONTEND_URL else "")
    )
    return _shell(kicker=template["kicker"], title=title, body_html=body), subject


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


def _send(to_addresses: list, subject: str, html_body: str, attachments: list | None = None) -> bool:
    to_addresses = [addr for addr in to_addresses if addr]
    if not FROM_EMAIL or not to_addresses:
        logger.warning("Seat purchase emailer: skipping send (missing FROM_EMAIL or recipients)")
        return False
    try:
        if attachments:
            # A QR attachment (see the "paid" send site) needs a MIME
            # multipart message; SES's simple send_email can't attach files.
            # The text/HTML pair still goes in as a nested "alternative" part
            # (same reasoning as _message below) so the attachment doesn't
            # force this one back to HTML-only.
            from email.mime.application import MIMEApplication
            from email.mime.multipart import MIMEMultipart
            from email.mime.text import MIMEText

            msg = MIMEMultipart("mixed")
            msg["Subject"] = subject
            msg["From"] = FROM_DISPLAY
            msg["To"] = ", ".join(to_addresses)
            msg["Reply-To"] = REPLY_TO_EMAIL
            alternative = MIMEMultipart("alternative")
            alternative.attach(MIMEText(_html_to_text(html_body), "plain", "utf-8"))
            alternative.attach(MIMEText(html_body, "html", "utf-8"))
            msg.attach(alternative)
            for filename, content_bytes in attachments:
                part = MIMEApplication(content_bytes)
                part.add_header("Content-Disposition", "attachment", filename=filename)
                msg.attach(part)
            ses.send_raw_email(
                Source=FROM_DISPLAY,
                Destinations=to_addresses,
                RawMessage={"Data": msg.as_string()},
            )
        else:
            ses.send_email(
                Source=FROM_DISPLAY,
                Destination={"ToAddresses": to_addresses},
                Message=_message(subject, html_body),
                ReplyToAddresses=[REPLY_TO_EMAIL],
            )
        return True
    except ClientError:
        logger.exception("Seat purchase emailer: SES send failed to %s", to_addresses)
        return False


def send_request_email(purchase: dict) -> bool:
    html, subject = _render_purchase_email(purchase, "request")
    return _send([purchase.get("email")], subject, html)


def _query_by_registration_id(table_name: str, registration_id: str) -> list:
    if not table_name or not registration_id:
        return []
    try:
        items = dynamodb.Table(table_name).query(
            IndexName="RegistrationIdIndex",
            KeyConditionExpression=Key("registrationId").eq(registration_id),
        ).get("Items", [])
    except ClientError:
        logger.exception(
            "check-in ticket: could not query %s for registrationId=%s", table_name, registration_id
        )
        return []
    active = [i for i in items if str(i.get("status", "")).lower() not in _INACTIVE_ADDON_STATUSES]
    return active or items


# A zone upgrade can bundle its own Celebrity Dinner add-on directly onto
# the upgrade record (seat_upgrade_logic.request_upgrade's dinner_guest_
# counts/dinnerAmount fields) instead of a standalone celebrity_dinner_
# registrations row — the two paths are mutually exclusive (the standalone
# sign-up refuses a registration that already has the bundled add-on), so
# at most one of these two sources ever has anything for a given
# registrant.
_DINNER_GUEST_FIELDS = (
    "dinnerAdultVegGuests", "dinnerAdultNonVegGuests",
    "dinnerKids914VegGuests", "dinnerKids914NonVegGuests",
    "dinnerKids68VegGuests", "dinnerKids68NonVegGuests",
    "dinnerKidsUnder6VegGuests", "dinnerKidsUnder6NonVegGuests",
)


def _dinner_from_zone_upgrade(zone_upgrade: dict) -> dict:
    """Normalize a zone upgrade's bundled dinner fields into the same shape
    as a standalone celebrity_dinner_registrations row (see checkin_pdf.py's
    celebrity_dinner rendering) — or None if this upgrade has no dinner
    guests bundled in."""
    if not zone_upgrade:
        return None
    if not any(int(zone_upgrade.get(f, 0) or 0) for f in _DINNER_GUEST_FIELDS):
        return None
    return {
        "numberOfAdultsVeg": zone_upgrade.get("dinnerAdultVegGuests", 0),
        "numberOfAdultsNonVeg": zone_upgrade.get("dinnerAdultNonVegGuests", 0),
        "numberOfKids914Veg": zone_upgrade.get("dinnerKids914VegGuests", 0),
        "numberOfKids914NonVeg": zone_upgrade.get("dinnerKids914NonVegGuests", 0),
        "numberOfKids68Veg": zone_upgrade.get("dinnerKids68VegGuests", 0),
        "numberOfKids68NonVeg": zone_upgrade.get("dinnerKids68NonVegGuests", 0),
        "numberOfKidsUnder6Veg": zone_upgrade.get("dinnerKidsUnder6VegGuests", 0),
        "numberOfKidsUnder6NonVeg": zone_upgrade.get("dinnerKidsUnder6NonVegGuests", 0),
        "totalAmount": zone_upgrade.get("dinnerAmount", 0),
        "status": zone_upgrade.get("status", ""),
    }


def gather_addons(registration_id: str) -> dict:
    """{"zoneUpgrade": ..., "celebrityDinner": ...} for this registrationId,
    each None when that add-on wasn't purchased/booked or the host didn't
    configure that table — used both for the ticket PDF above and for the
    door check-in scan page (see manage_seats/handler.py's
    handle_seat_check_in), so both show the same information.
    celebrityDinner comes from either a standalone booking or one bundled
    onto the zone upgrade — see _dinner_from_zone_upgrade above."""
    registration_id = str(registration_id or "").strip()
    if not registration_id:
        return {"zoneUpgrade": None, "celebrityDinner": None}
    zone_upgrades = _query_by_registration_id(SEAT_UPGRADES_TABLE, registration_id)
    dinners = _query_by_registration_id(CELEBRITY_DINNER_REGISTRATIONS_TABLE, registration_id)
    zone_upgrade = zone_upgrades[0] if zone_upgrades else None
    celebrity_dinner = (dinners[0] if dinners else None) or _dinner_from_zone_upgrade(zone_upgrade)
    return {
        "zoneUpgrade": zone_upgrade,
        "celebrityDinner": celebrity_dinner,
    }


def _build_ticket_attachment(purchase: dict) -> list:
    """[(filename, pdf_bytes)] for this purchase's guest check-in ticket, or
    [] if there's no checkInToken yet or FRONTEND_URL isn't configured (see
    seat_purchase_qr.check_in_url). When this purchase has a registrationId
    and the host configured SEAT_UPGRADES_TABLE / CELEBRITY_DINNER_
    REGISTRATIONS_TABLE, the ticket also shows that registrant's zone
    upgrade / celebrity dinner add-ons, same as the host's own Entry Pass
    ticket — otherwise it just shows this purchase's own seats."""
    url = seat_purchase_qr.check_in_url(str(purchase.get("checkInToken", "")).strip())
    if not url:
        return []
    registration_id = str(purchase.get("registrationId", "")).strip()
    addons = gather_addons(registration_id)
    pdf_bytes = checkin_pdf.build_check_in_ticket_pdf(
        event_name=_event_name(purchase.get("eventType", "")),
        registrant_name=purchase.get("name", ""),
        check_in_url=url,
        zone_upgrade=addons["zoneUpgrade"],
        celebrity_dinner=addons["celebrityDinner"],
        seat_selection=purchase,
    )
    if not pdf_bytes:
        return []
    filename = checkin_pdf.check_in_filename(registration_id or purchase.get("purchaseId", ""))
    return [(filename, pdf_bytes)]


def send_paid_email(purchase: dict, *, attachments: list | None = None) -> bool:
    """attachments, when given, overrides the check-in ticket PDF this
    builds automatically from the purchase — mainly so tests/callers that
    already have their own attachment can still pass it through."""
    html, subject = _render_purchase_email(purchase, "paid")
    if attachments is None:
        attachments = _build_ticket_attachment(purchase)
    return _send([purchase.get("email")], subject, html, attachments=attachments)


def send_refund_requested_email(purchase: dict, *, auto_initiated: bool) -> bool:
    note = (
        "We've started your refund with the payment provider — it can take a few business days to "
        "appear back on your original payment method."
        if auto_initiated
        else "Our team will process your refund by hand and reach out if we need anything from you."
    )
    html, subject = _render_purchase_email(purchase, "refund_requested", extra_message=note)
    return _send([purchase.get("email")], subject, html)


def send_cancelled_email(purchase: dict) -> bool:
    refunded = bool(purchase.get("paymentMethod") or purchase.get("paymentProvider"))
    html, subject = _render_purchase_email(
        purchase, "cancelled", banner_suffix=" — refund complete" if refunded else ""
    )
    return _send([purchase.get("email")], subject, html)


def send_admin_notification(purchase: dict, event_label: str, extra_note: str = "") -> bool:
    if not ADMIN_EMAIL or not FROM_EMAIL:
        return False
    if not _admin_notifications_enabled(purchase.get("eventType", "")):
        return False
    event_name = _event_name(purchase.get("eventType", ""))
    subject = f"Seat purchase {event_label}: {_zone_names(purchase)} — {purchase.get('name', '')}"
    rows = [
        ("Registrant", purchase.get("name", "N/A")),
        ("Email", purchase.get("email", "N/A")),
        ("Zones", _zone_names(purchase)),
        ("Seats", _seats_display_list(purchase)),
    ]
    # No amount was charged and no payment method was ever chosen when this
    # event has pricing off entirely — see _render_purchase_email's own
    # pricingEnabled check above for why this isn't just totalAmount == 0.
    if purchase.get("pricingEnabled", True):
        rows.append(("Amount", f"¥{int(purchase.get('totalAmount', 0) or 0):,}", True))
        rows.append(("Payment method", purchase.get("paymentProvider") or purchase.get("paymentMethod", "N/A")))
    rows.extend([
        ("Status", purchase.get("status", "N/A")),
        ("Purchase id", purchase.get("purchaseId", "N/A")),
        ("Registration id", purchase.get("registrationId", "N/A")),
    ])
    body = (
        f'<p style="margin:0;color:{COLORS["muted"]};font-size:14px;line-height:1.6;">'
        f"Seat purchase <strong>{event_label}</strong> for <strong>{event_name}</strong>.</p>"
        + _detail_table(rows)
        + (f'<p style="margin:14px 0 0;color:{COLORS["muted"]};font-size:13px;">{extra_note}</p>' if extra_note else "")
    )
    html = _shell(kicker="Admin Notification", title=f"Seat purchase {event_label}", body_html=body)
    try:
        ses.send_email(
            Source=FROM_EMAIL,
            Destination={"ToAddresses": [ADMIN_EMAIL]},
            Message=_message(subject, html),
        )
        return True
    except ClientError:
        logger.exception("Seat purchase emailer: admin notification send failed")
        return False


# ── Announcement (admin-triggered broadcast) ────────────────────────────────


def _zone_price_card(category: str, price: dict) -> str:
    return f"""
    <div style="border:1px solid {COLORS['border']};border-radius:8px;padding:16px 18px;margin:0 0 12px;background:{COLORS['table_bg']};">
      <p style="margin:0 0 10px;color:{COLORS['ink']};font-size:15px;font-weight:800;">{price.get('displayName', category)}</p>
      <table width="100%" cellpadding="0" cellspacing="0">
        <tr>
          <td style="padding:2px 0;color:{COLORS['muted']};font-size:12px;">Per family (up to {int(price.get('maxFamilySize', 0) or 0) or '∞'} seats)</td>
          <td style="padding:2px 0;text-align:right;color:{COLORS['ink']};font-size:14px;font-weight:700;">
            ¥{int(price.get('pricePerFamily', 0) or 0):,}
          </td>
        </tr>
        <tr>
          <td style="padding:2px 0;color:{COLORS['muted']};font-size:12px;">Per person</td>
          <td style="padding:2px 0;text-align:right;color:{COLORS['ink']};font-size:14px;font-weight:700;">
            ¥{int(price.get('pricePerPerson', 0) or 0):,}
          </td>
        </tr>
      </table>
    </div>
    """


def _format_long_date(date_str: str) -> str:
    """"2026-10-01" -> "October 1, 2026" for a human-facing display date —
    falls back to the raw value unchanged if it isn't a plain ISO date
    (already free text, or empty)."""
    date_str = str(date_str or "").strip()
    if not date_str:
        return date_str
    try:
        return datetime.strptime(date_str, "%Y-%m-%d").strftime("%B %-d, %Y")
    except ValueError:
        return date_str


def send_announcement_email(reg: dict, event_name: str, zone_pricing: dict, window: dict) -> bool:
    """zone_pricing may be {} — announcing that seat selection is open
    doesn't require any zone to be priced yet (an always-free event, or
    pricing not decided yet); the price cards below only appear once at
    least one zone actually has a price configured.

    window's announcementClosingNote (see seat_selection_logic.
    parse_seat_selection_config), when the admin has set one for this
    event, adds a short closing line below the button — omitted entirely
    otherwise, since a generic one wouldn't fit every event.
    """
    name = str(reg.get("name", "") or "").strip()
    greeting = f"Dear {escape(name)}," if name else "Dear,"

    when_note = ""
    if window.get("status") == "not_started" and window.get("startDate"):
        when_note = f"Booking opens {_format_long_date(window['startDate'])}."
    elif window.get("endDate"):
        when_note = f"Please make your selection by {_format_long_date(window['endDate'])}."

    cards = "".join(_zone_price_card(category, price) for category, price in sorted(zone_pricing.items()))
    closing_note = str(window.get("announcementClosingNote", "") or "").strip()

    body = f"""
    <p style="margin:0 0 14px;color:{COLORS['ink']};font-size:14px;font-weight:700;">{greeting}</p>
    <p style="margin:0 0 12px;color:{COLORS['muted']};font-size:14px;line-height:1.7;">
      Seat selection is now open for <strong>{event_name}</strong>.
    </p>
    <p style="margin:0 0 18px;color:{COLORS['muted']};font-size:14px;line-height:1.7;">
      Browse the hall map and choose the seats that suit you best. Seats are assigned on a
      first-come basis, so we recommend selecting yours early.
    </p>
    {f'<p style="margin:0 0 18px;"><strong style="color:{COLORS["brand"]};font-size:14px;">{when_note}</strong></p>' if when_note else ''}
    {cards}
    {_button('Choose My Seats', f'{FRONTEND_URL}/my/registrations') if FRONTEND_URL else ''}
    {f'<p style="margin:18px 0 0;color:{COLORS["faint"]};font-size:12px;text-align:center;">{escape(closing_note)}</p>' if closing_note else ''}
    """
    subject = f"Choose your seats for {event_name}"
    preheader = (
        f"Seat selection is open until {_format_long_date(window['endDate'])}. Pick your seats on the hall map."
        if window.get("endDate")
        else "Pick your seats on the hall map."
    )
    html = _shell(kicker="Seat Selection", title="Your seats are waiting", body_html=body, preheader=preheader)
    return _send([reg.get("email")], subject, html)


def send_reminder_email(reg: dict, event_name: str, window: dict) -> bool:
    """Nudge a registrant who hasn't chosen any seats yet. Same shell and
    button as send_announcement_email, but no price cards — they've already
    seen those — and a firmer deadline line when the window has an end date."""
    name = str(reg.get("name", "") or "").strip()
    greeting = f"Dear {escape(name)}," if name else "Dear,"

    deadline = ""
    if window.get("endDate"):
        deadline = f"Seat selection closes on {_format_long_date(window['endDate'])}."

    body = f"""
    <p style="margin:0 0 14px;color:{COLORS['ink']};font-size:14px;font-weight:700;">{greeting}</p>
    <p style="margin:0 0 12px;color:{COLORS['muted']};font-size:14px;line-height:1.7;">
      You haven't chosen your seats for <strong>{event_name}</strong> yet.
    </p>
    <p style="margin:0 0 18px;color:{COLORS['muted']};font-size:14px;line-height:1.7;">
      Seats are assigned on a first-come basis, so please select yours soon to avoid missing out.
    </p>
    {f'<p style="margin:0 0 18px;"><strong style="color:{COLORS["brand"]};font-size:14px;">{deadline}</strong></p>' if deadline else ''}
    {_button('Choose My Seats', f'{FRONTEND_URL}/my/registrations') if FRONTEND_URL else ''}
    <p style="margin:18px 0 0;color:{COLORS['faint']};font-size:12px;text-align:center;">
      Already chosen your seats? You can ignore this reminder.
    </p>
    """
    subject = f"Reminder: choose your seats for {event_name}"
    preheader = "You haven't selected your seats yet."
    html = _shell(kicker="Seat Selection", title="Don't forget your seats", body_html=body, preheader=preheader)
    return _send([reg.get("email")], subject, html)
