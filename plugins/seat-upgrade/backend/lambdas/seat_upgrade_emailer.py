"""
Shared email templates + SES senders for the seat-upgrade plugin.

This module is COPIED into every Lambda that needs it (manage_seat_upgrades,
seat_upgrade, seat_upgrade_webhook) — same convention as the host's own
emailer.py modules (see lambdas/manage_registrations/emailer.py). It only
ever works off an `upgrade` record (the seat_upgrades table item), never the
original registration or event config, so every call site — including the
payment-provider webhook, which never sees a registration at all — can use
it without an extra table lookup.

Every email — registrant-facing and the admin notification alike — is built
from the same small set of generic pieces below (_shell, _banner, _button,
_detail_table), not five copies of similar HTML. Revising the *look*
(colors, spacing, the logo) means editing COLORS or those functions once.
Revising the *copy* of a registrant-facing email doesn't require a code
change at all: DEFAULT_TEMPLATES holds the built-in text for each of the
four templates (request/paid/refund_requested/cancelled), and an admin can
override any field of it from the Seat Upgrades admin tab — see
_get_template() and seat_upgrade_logic.py's EMAIL_TEMPLATE_* constants for
the storage side, and manage_seat_upgrades/handler.py's
handle_email_templates for the CRUD + live-preview API.

Env vars (all provided by the host's common_env, already merged into every
seat-upgrade Lambda's environment — see backend/terraform/main.tf):
  REGION, EVENTS_TABLE, SES_FROM_EMAIL, ADMIN_EMAIL, FRONTEND_URL,
  SEAT_UPGRADE_EMAIL_TEMPLATES_TABLE
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
# is UTC, so it must be converted back to Asia/Tokyo before display or the
# announcement email shows the raw UTC clock digits instead of the actual
# Japan-local dinner time.
JST = ZoneInfo("Asia/Tokyo")

logger = logging.getLogger(__name__)

_REGION = os.environ.get("REGION", "")
ses = boto3.client("ses", region_name=_REGION) if _REGION else boto3.client("ses")
dynamodb = boto3.resource("dynamodb", region_name=_REGION) if _REGION else boto3.resource("dynamodb")

EVENTS_TABLE = os.environ.get("EVENTS_TABLE", "")
FROM_EMAIL = os.environ.get("SES_FROM_EMAIL", "")
ADMIN_EMAIL = os.environ.get("ADMIN_EMAIL", "")
FRONTEND_URL = os.environ.get("FRONTEND_URL", "").rstrip("/")
SEAT_UPGRADE_EMAIL_TEMPLATES_TABLE = os.environ.get("SEAT_UPGRADE_EMAIL_TEMPLATES_TABLE", "")
# frontend/public/logo.png ships as a static file, so it's served at this
# exact path off the frontend's own domain — no separate asset upload needed
# (see Navbar.jsx's `<img src="/logo.png">` for the same convention).
LOGO_URL = f"{FRONTEND_URL}/logo.png" if FRONTEND_URL else ""

# ── Brand palette — the single source of truth for every color used below.
# "Revise the look" means editing this dict, not hunting hex codes scattered
# across HTML-building functions.
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

# Named tones a banner/callout can take — success (paid), pending (awaiting
# something), danger (cancelled). Keeps every send_*_email call declarative
# ("tone='success'") instead of picking colors by hand each time.
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


def _quantity_label(upgrade: dict) -> str:
    seat_count = int(upgrade.get("seatCount", 0) or 0)
    return f"{seat_count} seat{'s' if seat_count != 1 else ''}"


# (display label, veg field, non-veg field) on an upgrade record's own
# bundled-dinner fields (dinnerAdultVegGuests, ...) — same four age
# categories as lambdas/celebrity_dinner_emailer.py's own DINNER_CATEGORIES,
# just against this module's differently-named fields (a bundled add-on
# here, rather than a standalone celebrity_dinner_registrations row).
_DINNER_CATEGORIES = (
    ("Dinner – Adults", "dinnerAdultVegGuests", "dinnerAdultNonVegGuests"),
    ("Dinner – Kids (9-14)", "dinnerKids914VegGuests", "dinnerKids914NonVegGuests"),
    ("Dinner – Kids (6-8)", "dinnerKids68VegGuests", "dinnerKids68NonVegGuests"),
    ("Dinner – Kids (under 6)", "dinnerKidsUnder6VegGuests", "dinnerKidsUnder6NonVegGuests"),
)


def _dinner_guest_rows(upgrade: dict) -> list:
    """One (label, "N (V veg, NV non-veg)") row per age category with at
    least one guest — categories with nothing booked are omitted."""
    rows = []
    for label, veg_field, non_veg_field in _DINNER_CATEGORIES:
        veg = int(upgrade.get(veg_field, 0) or 0)
        non_veg = int(upgrade.get(non_veg_field, 0) or 0)
        if veg + non_veg <= 0:
            continue
        rows.append((label, f"{veg + non_veg} ({veg} veg, {non_veg} non-veg)"))
    return rows


# ── Generic building blocks ──────────────────────────────────────────────────
# None of these know anything about "seat upgrades" specifically — they're
# the reusable template pieces the user asked for. A future plugin email
# type composes the same _shell/_banner/_button/_detail_table rather than
# writing new HTML from scratch.


def _logo_html() -> str:
    if not LOGO_URL:
        return f'<span style="color:{COLORS["header_text"]};font-size:20px;font-weight:800;">WMF Japan</span>'
    # Explicit width+height (not just CSS) keeps Outlook's renderer, which
    # ignores height:auto, from distorting the logo's aspect ratio.
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


def _shell(*, kicker: str, title: str, body_html: str) -> str:
    """The one wrapper every seat-upgrade email — registrant and admin alike
    — goes through: branded header with the WMF Japan logo, a title,
    the caller's own body content, and a consistent footer."""
    return f"""
<!DOCTYPE html>
<html>
<head><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1"></head>
<body style="margin:0;padding:0;background:{COLORS['page_bg']};font-family:'Noto Sans','Helvetica Neue',Arial,sans-serif;">
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
      Questions? Contact <a href="mailto:info@wmfjapan.com" style="color:{COLORS['brand']};font-weight:700;text-decoration:none;">info@wmfjapan.com</a>
    </p>
    <p style="margin:6px 0 0;color:{COLORS['faint']};font-size:11px;">&copy; 2026 WMF Japan</p>
  </td></tr>
  <tr><td style="padding:18px 30px;border-top:1px solid {COLORS['border']};text-align:center;">
    <div style="margin:auto;padding:14px 16px;background:{TONES['success']['bg']};">
      <p style="margin:0 0 6px;color:{COLORS['ink']};font-size:14px;font-weight:700;">Host Your Event With Us</p>
      <p style="margin:0;color:{COLORS['muted']};font-size:13px;line-height:1.6;">
        For bookings and inquiries, contact <a href="mailto:hello@cloudnadi.com" style="color:{COLORS['brand']};text-decoration:none;font-weight:700;">hello@cloudnadi.com</a>.
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


# ── Editable templates ───────────────────────────────────────────────────────
#
# The built-in copy for each registrant-facing email, and the one place an
# admin's saved override (seat_upgrade_logic.EMAIL_TEMPLATE_FIELDS, stored in
# SEAT_UPGRADE_EMAIL_TEMPLATES_TABLE) gets merged onto it. `{eventName}`,
# `{tierName}`, `{seats}`, and `{amount}` are available in every field — see
# _format() below. Keep this in sync with EMAIL_TEMPLATE_KEYS in
# seat_upgrade_logic.py and with the admin editor's field list
# (plugins/seat-upgrade/frontend/SeatUpgradeManagement.jsx).
DEFAULT_TEMPLATES = {
    "request": {
        "subject": "Your zone upgrade request for {eventName} — {tierName}",
        "title": "We've got your request",
        "tone": "pending",
        "bannerText": "Awaiting payment",
        "introText": "We've received your request to upgrade {seats} to {tierName} for {eventName}. "
                      "We'll confirm as soon as payment is received.",
        "ctaLabel": "View My Zone Upgrade",
    },
    "paid": {
        "subject": "Payment received for your zone upgrade — {tierName}",
        "title": "You're all set!",
        "tone": "success",
        "bannerText": "Payment received",
        "introText": "Your zone upgrade for {eventName} is confirmed — see you there!",
        "ctaLabel": "View My Zone Upgrade",
    },
    "refund_requested": {
        "subject": "Refund requested for your zone upgrade — {tierName}",
        "title": "Cancellation received",
        "tone": "pending",
        "bannerText": "Refund requested",
        "introText": "Your zone upgrade for {eventName} has been cancelled.",
        "ctaLabel": "",
    },
    "cancelled": {
        "subject": "Your zone upgrade has been cancelled — {tierName}",
        "title": "Cancelled",
        "tone": "danger",
        "bannerText": "Cancelled",
        "introText": "Your zone upgrade for {eventName} has been cancelled. Your original registration is unaffected.",
        "ctaLabel": "",
    },
}

_TEMPLATE_FIELDS = ("subject", "title", "tone", "bannerText", "introText", "ctaLabel")


def _get_template(template_key: str, override: dict | None = None) -> dict:
    """The built-in default for `template_key`, with whatever an admin has
    customized layered on top. `override`, when given (the template editor's
    live preview — see render_preview), is used as-is instead of reading the
    saved row from DynamoDB, so a preview can reflect draft, unsaved edits.
    """
    template = dict(DEFAULT_TEMPLATES[template_key])
    source = override
    if source is None and SEAT_UPGRADE_EMAIL_TEMPLATES_TABLE:
        try:
            source = dynamodb.Table(SEAT_UPGRADE_EMAIL_TEMPLATES_TABLE).get_item(
                Key={"templateKey": template_key}
            ).get("Item")
        except ClientError:
            logger.exception("Could not read email template override for %s", template_key)
            source = None
    if source:
        for field in _TEMPLATE_FIELDS:
            value = source.get(field)
            if isinstance(value, str) and value.strip():
                template[field] = value
    return template


def _format(template_str: str, **context) -> str:
    """Substitute {placeholder}s in admin-edited text. A typo'd or unknown
    placeholder (KeyError/IndexError — str.format also treats a stray "{"
    or "}" as a formatting error, ValueError) falls back to the raw text
    rather than raising and breaking the send."""
    try:
        return template_str.format(**context)
    except (KeyError, IndexError, ValueError):
        logger.warning("Email template placeholder error in %r", template_str)
        return template_str


def _render_upgrade_email(
    upgrade: dict,
    template_key: str,
    *,
    extra_message: str = "",
    banner_suffix: str = "",
    template_override: dict | None = None,
) -> tuple:
    """The one function every registrant-facing seat-upgrade email is built
    from. Returns (html, subject). `extra_message`/`banner_suffix` are
    system-computed additions (e.g. the refund-status note, or "— refund
    complete") that aren't part of the editable template itself — appended
    after the admin's own copy, never overriding it.
    """
    template = _get_template(template_key, template_override)
    event_name = _event_name(upgrade.get("eventType", ""))
    tier_name = upgrade.get("tierName", "")
    amount = f"¥{int(upgrade.get('totalAmount', 0) or 0):,}"
    seats = _quantity_label(upgrade)

    # Plain context for subject/title/banner (already bold via CSS — literal
    # "<strong>" text there would leak into a subject line); a second,
    # HTML-emphasized context only for the intro paragraph, so admin-edited
    # plain text still gets the event/zone name visually bolded inline
    # without needing to hand-write HTML.
    plain_ctx = {"eventName": event_name, "tierName": tier_name, "seats": seats, "amount": amount}
    html_ctx = {**plain_ctx, "eventName": f"<strong>{event_name}</strong>", "tierName": f"<strong>{tier_name}</strong>"}

    subject = _format(template["subject"], **plain_ctx)
    title = _format(template["title"], **plain_ctx)
    banner = _format(template["bannerText"], **plain_ctx) + banner_suffix
    message = _format(template["introText"], **html_ctx)
    if extra_message:
        message = f"{message} {extra_message}"

    rows = [
        ("Event", event_name),
        ("Zone", tier_name),
        ("Seats", seats),
    ]
    rows += _dinner_guest_rows(upgrade)
    rows.append(("Amount", amount, True))
    cta_label = template.get("ctaLabel", "")
    body = (
        _greeting_html(upgrade.get("name", ""))
        + _banner(banner, template.get("tone", "success"))
        + f'<p style="margin:0;color:{COLORS["muted"]};font-size:14px;line-height:1.7;">{message}</p>'
        + _detail_table(rows)
        + (_button(cta_label, f"{FRONTEND_URL}/my/seat-upgrade") if cta_label and FRONTEND_URL else "")
    )
    return _shell(kicker="Zone Upgrade", title=title, body_html=body), subject


_PREVIEW_UPGRADE = {
    "eventType": "",  # _event_name("") -> "your event"; a preview has no real event
    "tierName": "Zone 1 — Front Row",
    "seatCount": 4,
    "totalAmount": 20000,
    "email": "preview@example.com",
    "name": "Sample Registrant",
}


def render_preview(template_key: str, template_override: dict) -> dict:
    """Render one template against canned sample data and DRAFT (not yet
    saved) field values — the admin template editor's live preview. Returns
    {"subject": ..., "html": ...}; never sends anything."""
    if template_key not in DEFAULT_TEMPLATES:
        raise ValueError(f"Unknown template key: {template_key!r}")
    extra_message = ""
    banner_suffix = ""
    if template_key == "refund_requested":
        extra_message = (
            "We've started your refund with the payment provider — it can take a few business days "
            "to appear back on your original payment method."
        )
    elif template_key == "cancelled":
        banner_suffix = " — refund complete"
    html, subject = _render_upgrade_email(
        _PREVIEW_UPGRADE,
        template_key,
        extra_message=extra_message,
        banner_suffix=banner_suffix,
        template_override=template_override,
    )
    return {"subject": subject, "html": html}


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
        logger.warning("Seat upgrade emailer: skipping send (missing FROM_EMAIL or recipients)")
        return False
    try:
        ses.send_email(
            Source=FROM_EMAIL,
            Destination={"ToAddresses": to_addresses},
            Message=_message(subject, html_body),
        )
        return True
    except ClientError:
        logger.exception("Seat upgrade emailer: SES send failed to %s", to_addresses)
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
        logger.exception("Seat upgrade emailer: admin notification send failed")
        return False


# ── Announcement (admin-triggered broadcast, not an upgrade-lifecycle email) ──
#
# Unlike the four templates above, this isn't keyed to one upgrade record —
# it's a one-off broadcast an admin sends to every paid event/combo
# registrant announcing that premium seating is available at all, so it
# isn't part of DEFAULT_TEMPLATES/EMAIL_TEMPLATE_KEYS (no per-field admin
# override, no live-preview editor). See manage_seat_upgrades/handler.py's
# handle_announcement for who it's sent to and when.


def _announcement_tier_card(tier: dict) -> str:
    description = tier.get("description") or ""
    return f"""
    <div style="border:1px solid {COLORS['border']};border-radius:8px;padding:16px 18px;margin:0 0 12px;background:{COLORS['table_bg']};">
      <p style="margin:0 0 4px;color:{COLORS['ink']};font-size:15px;font-weight:800;">{tier.get('name', '')}</p>
      {f'<p style="margin:0 0 10px;color:{COLORS["muted"]};font-size:13px;">{description}</p>' if description else ''}
      <table width="100%" cellpadding="0" cellspacing="0">
        <tr>
          <td style="padding:2px 0;color:{COLORS['muted']};font-size:12px;">Upgrade price</td>
          <td style="padding:2px 0;text-align:right;color:{COLORS['ink']};font-size:14px;font-weight:700;">
            ¥{int(tier.get('upgradePrice', 0) or 0):,}
          </td>
        </tr>
      </table>
    </div>
    """


def _announcement_dinner_card(celebrity_dinner: dict) -> str:
    """Mirrors _announcement_tier_card's look for the optional Celebrity
    Dinner section — see _normalize_announcement_celebrity_dinner in
    manage_seat_upgrades/handler.py for where this dict comes from."""
    currency = celebrity_dinner.get("currency", "JPY")
    price_rows = [
        ("Adult", celebrity_dinner.get("dinnerPriceAdults", 0)),
        ("Kid (9-14)", celebrity_dinner.get("dinnerPriceKids914", 0)),
        ("Kid (6-8)", celebrity_dinner.get("dinnerPriceKids68", 0)),
    ]
    price_html = "".join(
        f'<tr><td style="padding:2px 0;color:{COLORS["muted"]};font-size:12px;">{label}</td>'
        f'<td style="padding:2px 0;text-align:right;color:{COLORS["ink"]};font-size:14px;font-weight:700;">'
        f'{currency} {int(price or 0):,}</td></tr>'
        for label, price in price_rows
    )
    venue = celebrity_dinner.get("venue", "")
    dinner_date = _format_dinner_date_for_announcement(celebrity_dinner.get("dinnerDate", ""))
    meta_line = " · ".join(part for part in (dinner_date, venue) if part)
    conditions = celebrity_dinner.get("conditions", "")
    return f"""
    <div style="border:1px solid {COLORS['border']};border-radius:8px;padding:16px 18px;margin:0 0 12px;background:{COLORS['table_bg']};">
      <p style="margin:0 0 4px;color:{COLORS['ink']};font-size:15px;font-weight:800;">Celebrity Dinner</p>
      {f'<p style="margin:0 0 10px;color:{COLORS["muted"]};font-size:13px;">{meta_line}</p>' if meta_line else ''}
      <table width="100%" cellpadding="0" cellspacing="0">{price_html}</table>
      <p style="margin:8px 0 0;color:{COLORS['faint']};font-size:11px;">Kids under 6 are never priced — headcount only.</p>
      {f'<p style="margin:10px 0 0;color:{COLORS["muted"]};font-size:12px;">{conditions}</p>' if conditions else ''}
    </div>
    """


def _format_dinner_date_for_announcement(raw: str) -> str:
    if not raw:
        return ""
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return raw
    if dt.tzinfo is not None:
        dt = dt.astimezone(JST)
    return dt.strftime("%B %-d, %Y, %-I:%M %p")


def _render_announcement_email(
    name: str, event_name: str, tiers: list, seat_map_image_url: str, window: dict, celebrity_dinner: dict | None = None
) -> tuple:
    greeting = f"Hello {name}," if name else "Hello,"

    when_note = ""
    if window.get("status") == "not_started" and window.get("startDate"):
        when_note = f"Booking opens {window['startDate']}."
    elif window.get("endDate"):
        when_note = f"Offer available through {window['endDate']}."

    shown = tiers[:3]
    tier_cards = "".join(_announcement_tier_card(t) for t in shown)
    more_note = (
        f'<p style="margin:-4px 0 16px;color:{COLORS["faint"]};font-size:12px;">'
        f"+{len(tiers) - len(shown)} more option(s) available</p>"
        if len(tiers) > len(shown) else ""
    )
    seat_map_html = (
        f'<div style="margin:18px 0 0;border:1px solid {COLORS["border"]};border-radius:8px;overflow:hidden;">'
        f'<img src="{seat_map_image_url}" alt="Venue seat map" style="display:block;width:100%;" /></div>'
        if seat_map_image_url else ""
    )
    dinner_html = ""
    dinner_intro = ""
    if celebrity_dinner:
        dinner_html = f"""
        <p style="margin:22px 0 10px;color:{COLORS['ink']};font-size:15px;font-weight:800;">
          Also joining us this year — the Celebrity Dinner
        </p>
        {_announcement_dinner_card(celebrity_dinner)}
        {_button('Book the Celebrity Dinner', f'{FRONTEND_URL}/celebrity-dinner') if FRONTEND_URL else ''}
        """
        dinner_intro = " and reserve your seat at the Celebrity Dinner"

    body = f"""
    <p style="margin:0 0 4px;color:{COLORS['ink']};font-size:18px;font-weight:800;">{greeting}</p>
    <p style="margin:0 0 18px;color:{COLORS['muted']};font-size:14px;line-height:1.7;">
      As a confirmed guest of <strong>{event_name}</strong>, you're invited to upgrade into one of our
      premium seating zones{dinner_intro} — better sightlines, reserved rows, and a more elevated evening.
      {f'<br/><strong style="color:{COLORS["brand"]};">{when_note}</strong>' if when_note else ''}
    </p>
    {tier_cards}
    {more_note}
    {_button('View Upgrade Options', f'{FRONTEND_URL}/my/seat-upgrade') if FRONTEND_URL else ''}
    {seat_map_html}
    {dinner_html}
    """
    subject = (
        f"✨ Premium Seating & Celebrity Dinner Now Open — {event_name}"
        if celebrity_dinner
        else f"✨ Premium Seating Now Available — {event_name}"
    )
    html = _shell(kicker="Premium Seating Upgrade", title="Reserve Your Upgrade", body_html=body)
    return html, subject


def send_announcement_email(
    reg: dict, event_name: str, tiers: list, seat_map_image_url: str, window: dict, celebrity_dinner: dict | None = None
) -> bool:
    html, subject = _render_announcement_email(
        reg.get("name", ""), event_name, tiers, seat_map_image_url, window, celebrity_dinner=celebrity_dinner
    )
    return _send([reg.get("email")], subject, html)


# ── Registrant-facing ────────────────────────────────────────────────────────


def send_request_email(upgrade: dict) -> bool:
    html, subject = _render_upgrade_email(upgrade, "request")
    return _send([upgrade.get("email")], subject, html)


def send_paid_email(upgrade: dict) -> bool:
    html, subject = _render_upgrade_email(upgrade, "paid")
    return _send([upgrade.get("email")], subject, html)


def send_refund_requested_email(upgrade: dict, *, auto_initiated: bool) -> bool:
    note = (
        "We've started your refund with the payment provider — it can take a few business days to "
        "appear back on your original payment method."
        if auto_initiated
        else "Our team will process your refund by hand and reach out if we need anything from you."
    )
    html, subject = _render_upgrade_email(upgrade, "refund_requested", extra_message=note)
    return _send([upgrade.get("email")], subject, html)


def send_cancelled_email(upgrade: dict) -> bool:
    """Final notice once an admin finalizes a refund_requested/paid upgrade to cancelled."""
    refunded = bool(upgrade.get("paymentMethod") or upgrade.get("paymentProvider"))
    html, subject = _render_upgrade_email(
        upgrade, "cancelled", banner_suffix=" — refund complete" if refunded else ""
    )
    return _send([upgrade.get("email")], subject, html)


# ── Admin-facing ─────────────────────────────────────────────────────────────


def send_admin_notification(upgrade: dict, event_label: str, extra_note: str = "") -> bool:
    """Admin notification for a seat-upgrade lifecycle event (requested /
    paid / refund requested / cancelled) — the same branded shell as the
    registrant emails, sized for scanning rather than a CTA button."""
    event_name = _event_name(upgrade.get("eventType", ""))
    subject = f"Zone upgrade {event_label}: {upgrade.get('tierName', '')} — {upgrade.get('name', '')}"
    rows = [
        ("Registrant", upgrade.get("name", "N/A")),
        ("Email", upgrade.get("email", "N/A")),
        ("Zone", upgrade.get("tierName", "N/A")),
        ("Seats", _quantity_label(upgrade)),
    ]
    rows += _dinner_guest_rows(upgrade)
    rows += [
        ("Amount", f"¥{int(upgrade.get('totalAmount', 0) or 0):,}", True),
        ("Payment method", upgrade.get("paymentProvider") or upgrade.get("paymentMethod", "N/A")),
        ("Status", upgrade.get("status", "N/A")),
        ("Upgrade id", upgrade.get("upgradeId", "N/A")),
        ("Registration id", upgrade.get("registrationId", "N/A")),
    ]
    body = (
        f'<p style="margin:0;color:{COLORS["muted"]};font-size:14px;line-height:1.6;">'
        f"Zone upgrade <strong>{event_label}</strong> for <strong>{event_name}</strong>.</p>"
        + _detail_table(rows)
        + (f'<p style="margin:14px 0 0;color:{COLORS["muted"]};font-size:13px;">{extra_note}</p>' if extra_note else "")
    )
    html = _shell(kicker="Admin Notification", title=f"Zone upgrade {event_label}", body_html=body)
    return _send_admin(subject, html)
