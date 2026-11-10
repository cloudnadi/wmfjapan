"""Registration confirmation email: consolidated check-in ticket PDF.

Attaches a single PDF (logo, QR code, event/registrant info, and any zone
upgrade / celebrity dinner / seat selection add-ons this registrant has —
see checkin_pdf.py) in place of the QR-only PNG this module used to embed
inline. The pass only means something once entry slots have been picked,
so an unallocated registration still gets no ticket at all.
"""

import logging
import os
from email import encoders
from email.mime.base import MIMEBase
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

import boto3
from boto3.dynamodb.conditions import Key

import checkin_pdf

logger = logging.getLogger(__name__)

FRONTEND_URL = os.environ.get("FRONTEND_URL", "").rstrip("/")

# Cross-plugin table names for the add-on lookups below. Each is optional —
# a deployment without a plugin enabled simply never finds any rows for it.
SEAT_UPGRADES_TABLE = os.environ.get("SEAT_UPGRADES_TABLE", "")
CELEBRITY_DINNER_REGISTRATIONS_TABLE = os.environ.get("CELEBRITY_DINNER_REGISTRATIONS_TABLE", "")
SEAT_PURCHASES_TABLE = os.environ.get("SEAT_PURCHASES_TABLE", "")

PLACEHOLDER = checkin_pdf.PLACEHOLDER

_dynamodb = None


def _dynamo():
    global _dynamodb
    if _dynamodb is None:
        _dynamodb = boto3.resource("dynamodb")
    return _dynamodb


# A registrant can rack up several rows in one of these tables (e.g. an
# edited/re-submitted upgrade); prefer an active one over a cancelled/
# refunded leftover, but fall back to whatever exists rather than showing
# nothing.
_INACTIVE_ADDON_STATUSES = {"cancelled", "refunded"}


def _query_by_registration_id(table_name: str, registration_id: str) -> list:
    if not table_name or not registration_id:
        return []
    try:
        items = _dynamo().Table(table_name).query(
            IndexName="RegistrationIdIndex",
            KeyConditionExpression=Key("registrationId").eq(registration_id),
        ).get("Items", [])
    except Exception:
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


def _merge_seat_purchases(seat_purchases: list):
    """Combine every active seat purchase's seatIds into one synthetic
    record — build_check_in_ticket_pdf's seat_selection param only takes a
    single dict, but a registration can hold several active purchases (a
    self-service top-up after the first purchase, or more than one admin
    allocation — see manage_registrations.allocate_registration_seats), and
    the ticket should show the full current set, not just whichever
    purchase happened to sort first. _query_by_registration_id's GSI query
    has no explicit ScanIndexForward, so "first" here is the OLDEST
    purchase (ascending requestedAt) — picking just that one, as this used
    to, meant the ticket could never show seats from any purchase made
    after the first."""
    if not seat_purchases:
        return None
    seat_ids = []
    for purchase in seat_purchases:
        for seat_id in purchase.get("seatIds") or []:
            if seat_id not in seat_ids:
                seat_ids.append(seat_id)
    return {"seatIds": seat_ids, "status": seat_purchases[0].get("status", "")}


def gather_addons(registration_id: str) -> dict:
    """Best-effort lookup of this registrant's zone upgrade, celebrity
    dinner, and seat selection records (by registrationId) for the
    consolidated check-in ticket. Each key is None when that add-on
    wasn't purchased/booked. celebrityDinner comes from either a
    standalone booking or one bundled onto the zone upgrade — see
    _dinner_from_zone_upgrade above. seatSelection merges every active
    seat purchase (see _merge_seat_purchases) rather than picking just one,
    since a registration can hold more than one."""
    registration_id = str(registration_id or "").strip()
    if not registration_id:
        return {}
    zone_upgrades = _query_by_registration_id(SEAT_UPGRADES_TABLE, registration_id)
    dinners = _query_by_registration_id(CELEBRITY_DINNER_REGISTRATIONS_TABLE, registration_id)
    seat_purchases = _query_by_registration_id(SEAT_PURCHASES_TABLE, registration_id)
    zone_upgrade = zone_upgrades[0] if zone_upgrades else None
    celebrity_dinner = (dinners[0] if dinners else None) or _dinner_from_zone_upgrade(zone_upgrade)
    return {
        "zoneUpgrade": zone_upgrade,
        "celebrityDinner": celebrity_dinner,
        "seatSelection": _merge_seat_purchases(seat_purchases),
    }


def build_registration_ticket(registration_details: dict, event_name: str = "") -> tuple[str, bytes]:
    """Return (filename, pdf_bytes) for this registration's consolidated
    check-in ticket, or ("", b"") if there's nothing to build a QR against
    (no pass token, no FRONTEND_URL configured, or no entry slot allocated
    yet)."""
    registration_details = registration_details or {}
    token = str(registration_details.get("registrationPassToken", "")).strip()
    allocations = registration_details.get("allocations") or []
    if not token or not FRONTEND_URL or not allocations:
        return "", b""

    registration_id = registration_details.get("registrationId", "")
    addons = gather_addons(registration_id)
    pdf_bytes = checkin_pdf.build_check_in_ticket_pdf(
        event_name=event_name,
        registrant_name=registration_details.get("name", ""),
        check_in_url=f"{FRONTEND_URL}/check-in?token={token}",
        allocations=allocations,
        zone_upgrade=addons.get("zoneUpgrade"),
        celebrity_dinner=addons.get("celebrityDinner"),
        seat_selection=addons.get("seatSelection"),
    )
    if not pdf_bytes:
        return "", b""
    return checkin_pdf.check_in_filename(registration_id), pdf_bytes


def ticket_note_html(registration_details: dict) -> str:
    """HTML swapped in for PLACEHOLDER once the ticket PDF is attached —
    no inline QR image any more, just a pointer to the attachment."""
    allocations = (registration_details or {}).get("allocations") or []
    slot_lines = []
    for allocation in allocations:
        name = str(allocation.get("slotName") or "Entry slot")
        seats = int(allocation.get("seatCount", 0) or 0)
        slot_lines.append(f"{name} - {seats} people")
    slots = "<br>".join(slot_lines) or "Your assigned entry slots"
    return f'''
<div style="margin:22px 0 0;padding:20px 16px;background:#edf7f1;border:1px solid #b9d7c3;border-radius:4px;text-align:center;">
  <p style="margin:0 0 6px;color:#1b2621;font-size:18px;font-weight:800;">Your Guest Check-In Ticket</p>
  <p style="margin:0 0 4px;color:#5d6b65;font-size:13px;line-height:1.6;">{slots}</p>
  <p style="margin:0;color:#5d6b65;font-size:12px;line-height:1.5;">Your ticket, including your QR code, is attached to this email as a PDF. Show it — printed or on your phone — at entry.</p>
</div>
'''


def send_html_email(ses_client, source: str, recipients: list[str], subject: str, html_body: str,
                     registration_details=None, event_name: str = ""):
    """Send HTML through SES, attaching the consolidated check-in ticket
    PDF when this registration has a pass token and an entry slot."""
    filename, pdf_bytes = "", b""
    if PLACEHOLDER in html_body:
        filename, pdf_bytes = build_registration_ticket(registration_details or {}, event_name)
        html_body = html_body.replace(
            PLACEHOLDER, ticket_note_html(registration_details or {}) if pdf_bytes else ""
        )

    if not pdf_bytes:
        return ses_client.send_email(
            Source=source,
            Destination={"ToAddresses": recipients},
            Message={"Subject": {"Data": subject}, "Body": {"Html": {"Data": html_body}}},
        )

    message = MIMEMultipart("mixed")
    message["Subject"] = subject
    message["From"] = source
    message["To"] = ", ".join(recipients)
    message.attach(MIMEText(html_body, "html", "utf-8"))
    attachment = MIMEBase("application", "pdf")
    attachment.set_payload(pdf_bytes)
    encoders.encode_base64(attachment)
    attachment.add_header("Content-Disposition", "attachment", filename=filename)
    message.attach(attachment)
    return ses_client.send_raw_email(
        Source=source,
        Destinations=recipients,
        RawMessage={"Data": message.as_bytes()},
    )
