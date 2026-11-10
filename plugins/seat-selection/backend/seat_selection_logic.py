"""
Seat Selection Plugin — core logic

A reusable theater-style seat-map module: a fixed hall layout (rows, aisles,
zones) generated on demand rather than stored, sparse occupancy tracking (only
booked/blocked seats get a row), atomic multi-seat claiming, and per-seat
admin category overrides (bookable zones, or non-bookable special seats like
Camera/Projector).

This module has no knowledge of *why* seats are being claimed — the caller
supplies a plain `registration_id` string (any opaque identifier for
"whoever holds these seats": a registration, an order, a booking, ...).
Nothing here reads or assumes a particular record shape — see this repo's
`lambdas/my_registrations/handler.py` for an example host integration
(`create_my_seat_purchase`/`cancel_my_seat_purchase`), which calls
`seat_purchase_logic.create_purchase_request`/`cancel_purchase_request`,
themselves built on `claim_seats_atomic`/`release_seats` below.

Concurrency control follows the classic optimistic-lock idiom: each seat is
claimed with its own conditional Put (`ConditionExpression=
"attribute_not_exists(...)"`), and a multi-seat request commits through a
single DynamoDB transaction so a party's seats are either all assigned or
none are — never partially booked, and never double-booked.

Configuration (environment variables):
  SEATS_TABLE                    - required. Sparse hall/eventType x seatId table.
  SEAT_CATEGORY_OVERRIDES_TABLE  - optional. Per-seat cosmetic category overrides
                                    (Zone 1/2/3/Regular). Omit to disable overrides
                                    (every seat just uses its row-based default).
  ZONE_PRICING_TABLE             - optional. Per-eventType/category pricing (family
                                    flat fee + per-person fee + how many seats the
                                    flat fee covers). Omit to disable selling seats
                                    entirely (get_zone_pricing always returns {}).
  REGION                         - AWS region for the DynamoDB client.
"""

import os
from datetime import datetime, timezone

import boto3
from botocore.exceptions import ClientError
from boto3.dynamodb.conditions import Key

SEATS_TABLE = os.environ.get("SEATS_TABLE", "")
SEAT_CATEGORY_OVERRIDES_TABLE = os.environ.get("SEAT_CATEGORY_OVERRIDES_TABLE", "")
ZONE_PRICING_TABLE = os.environ.get("ZONE_PRICING_TABLE", "")

dynamodb = boto3.resource("dynamodb", region_name=os.environ.get("REGION"))
# transact_write_items lives on the resource's own client, so it takes/
# returns plain Python values exactly like Table.put_item does.
dynamodb_client = dynamodb.meta.client

MAX_SEATS_PER_REQUEST = 100

# ── Seat categories ──────────────────────────────────────────────────────────
#
# zone1/zone2/zone3/regular are cosmetic and always bookable — an admin can
# override any single seat's category away from its _ROW_SPECS default (e.g.
# carving a Zone 3 out of what would otherwise be Regular) without it ever
# creating a row in the sparse seats table, since that table's mere
# row-presence is what blocks a claim (attribute_not_exists below).
# Overrides live in SEAT_CATEGORY_OVERRIDES_TABLE, keyed per hall/eventType.
#
# camera/projector are NOT bookable — a camera platform or projector booth
# is a physical obstruction, so both behave exactly like a plain
# admin-blocked seat (reuses block_seat/unblock_seat), just tagged with a
# "blockCategory" so the frontend can show a distinct icon instead of a
# plain "blocked" swatch. Any other seat an admin needs to hold back (VIP,
# reserved, ...) uses a plain generic block instead of its own category.
ZONE_OVERRIDE_CATEGORIES = ("zone1", "zone2", "zone3", "regular")
NON_BOOKABLE_CATEGORIES = ("camera", "projector")

# Display names for the four bookable categories when nothing else supplies
# one — an admin's own zone_pricing row (when priced) has its own
# displayName, but the free path in price_seats below, and any other caller
# that just needs a human label for a bare category string, has no pricing
# row to read one from. Mirrors the frontend's own SeatMapSelector.jsx
# ZONE_LABELS constant (duplicated here rather than shared — this module
# never imports frontend code).
ZONE_DISPLAY_NAMES = {"zone1": "Zone 1", "zone2": "Zone 2", "zone3": "Zone 3", "regular": "Regular"}

# Regular is the hall's baseline seating — already covered by the base
# registration fee, never an admin-priced upsell — so it's deliberately
# excluded from the categories an admin can set a zone_pricing row for.
# price_seats() below always treats it as free (¥0, always "available"),
# regardless of whether a zone_pricing row happens to exist for it (e.g.
# left over from before this became a hardcoded rule).
PRICEABLE_CATEGORIES = ("zone1", "zone2", "zone3")

# A purchase picks one pricing mode for its whole seat selection: "family"
# bills a flat per-zone fee (pricePerFamily) covering up to that zone's
# maxFamilySize seats — any seats picked beyond that cap in the same zone
# bill at that zone's pricePerPerson instead; "person" always bills every
# seat at its zone's pricePerPerson, with no flat fee. See price_seats().
PRICING_MODES = ("family", "person")

# ── Hall layout (fixed; matches 葛西区民館ホール's own printed seating chart) ──
#
# Every row is built from three blocks — Left / Center (always 11 seats,
# the raked "preferred" section) / Right — separated by a vertical aisle,
# plus a symmetric blankEdges count: how many seat-widths of empty space
# the hall physically has past each end of the row (no seat there at all,
# not merely an unavailable one). Rows 1-3 are physically narrower; row 4
# onward is full width:
#
# (rowNumber, leftCount, centerCount, rightCount, centerZone-or-None, blankEdges)
#   Row 1: 5 / 11 / 5, blank 3 each side (21 real seats).
#   Row 2: 6 / 11 / 6, blank 2 each side (23 real seats).
#   Row 3: 7 / 11 / 7, blank 1 each side (25 real seats).
#   Row 4-19: 8 / 11 / 8, no blank (27 real seats each).
#   Zone 1 (preferred) = the Center block on rows 1-2.
#   Zone 2 (preferred) = the Center block on rows 3-7.
#   Regular = every Left/Right seat on every row, plus the Center block on
#   rows 8-19.
#
# Internal column numbers (used in seatId, e.g. "R04C08") run continuously
# 1..N per row and are NOT the venue's own printed seat numbers — the venue
# skips the digit 8 in every row's Left block (its last seat is numbered 9,
# same digit the Center block's own first seat also uses — two different
# seats, disambiguated by block, not a collision) and narrower rows drop
# seats off the *low* end of the Left/Right numbering rather than the end
# nearest the aisle. See plugins/seat-selection/frontend/rowLabels.js for
# the real-seat-number display mapping derived from this same shape.
#
# This fixed 501-seat/19-row shape is the plugin's one opinionated default.
# A different venue shape is a follow-up to make _ROW_SPECS configurable
# per hall — today every hall/eventType shares this same layout.
_ROW_SPECS = (
    (1, 5, 11, 5, "zone1", 3),
    (2, 6, 11, 6, "zone1", 2),
    (3, 7, 11, 7, "zone2", 1),
    (4, 8, 11, 8, "zone2", 0),
    (5, 8, 11, 8, "zone2", 0),
    (6, 8, 11, 8, "zone2", 0),
    (7, 8, 11, 8, "zone2", 0),
) + tuple((row, 8, 11, 8, None, 0) for row in range(8, 20))

TOTAL_SEATS = sum(spec[1] + spec[2] + spec[3] for spec in _ROW_SPECS)  # 501

# A horizontal walkway crosses the hall after this many rows (cosmetic —
# purely a frontend divider, not a seat-numbering gap).
WALKWAY_AFTER_ROW = 10


def now_iso():
    return datetime.now(timezone.utc).isoformat()


def _is_conditional_check_failed(exc: ClientError) -> bool:
    return exc.response.get("Error", {}).get("Code") == "ConditionalCheckFailedException"


def _seat_id(row: int, col: int) -> str:
    return f"R{row:02d}C{col:02d}"


def generate_seat_layout(category_overrides: dict | None = None) -> list[dict]:
    """[{"row": 1, "blankEdges": 2, "seats": [{"seatId", "category", "block"}, ...]}, ...]

    blankEdges is how many seat-widths of empty space to render past each
    end of the row — purely cosmetic, not seats. Each seat's "block" (left/
    center/right) is which side of the row's two aisles it's in — also
    purely cosmetic (which visual group to render it in), and the reason
    the frontend never needs to infer this from a column-number threshold:
    block sizes differ per row, so a fixed threshold would misplace seats
    on the narrower rows.

    category_overrides ({seatId: category}, from get_category_overrides)
    replaces a seat's _ROW_SPECS default category with an admin's per-seat
    reassignment (e.g. Zone 3 carved out of Regular). Pass the same dict
    that was already fetched for the hall/eventType in question — this
    function itself never fetches overrides on its own.
    """
    overrides = category_overrides or {}
    rows = []
    for row_num, left_count, center_count, right_count, zone_name, blank_edges in _ROW_SPECS:
        seats = []
        col = 1
        for _ in range(left_count):
            seat_id = _seat_id(row_num, col)
            seats.append({"seatId": seat_id, "category": overrides.get(seat_id, "regular"), "block": "left"})
            col += 1
        for _ in range(center_count):
            seat_id = _seat_id(row_num, col)
            seats.append({"seatId": seat_id, "category": overrides.get(seat_id, zone_name or "regular"), "block": "center"})
            col += 1
        for _ in range(right_count):
            seat_id = _seat_id(row_num, col)
            seats.append({"seatId": seat_id, "category": overrides.get(seat_id, "regular"), "block": "right"})
            col += 1
        rows.append({"row": row_num, "blankEdges": blank_edges, "seats": seats})
    return rows


def list_all_seat_ids() -> list[str]:
    # Seat IDs never change with category overrides, so the unoverridden
    # layout is fine here.
    return [seat["seatId"] for row in generate_seat_layout() for seat in row["seats"]]


def find_seat(seat_id: str, category_overrides: dict | None = None) -> dict | None:
    """The {"seatId", "category", "block"} for a seat ID, or None if unknown."""
    for row in generate_seat_layout(category_overrides):
        for seat in row["seats"]:
            if seat["seatId"] == seat_id:
                return seat
    return None


def get_zone_totals(category_overrides: dict | None = None) -> dict:
    """Total seat count per category across the hall. Always includes the
    four bookable categories (zone1/zone2/zone3/regular); camera/projector
    seats keep whichever of those they'd otherwise be (their unavailability
    comes from being BLOCKED, not from being excluded here)."""
    totals = {"zone1": 0, "zone2": 0, "zone3": 0, "regular": 0}
    for row in generate_seat_layout(category_overrides):
        for seat in row["seats"]:
            totals[seat["category"]] = totals.get(seat["category"], 0) + 1
    return totals


def validate_seat_ids(seat_ids: list) -> bool:
    if not seat_ids:
        return False
    valid = set(list_all_seat_ids())
    return all(seat_id in valid for seat_id in seat_ids)


def get_category_overrides(event_type: str) -> dict:
    """{seatId: category} admin reassignments of the row-based default,
    for the cosmetic zone1/zone2/zone3/regular categories only — camera/
    projector never appear here, since those go through block_seat
    instead (see the module docstring above). {} if the overrides table
    isn't configured, or nothing has been overridden yet."""
    if not SEAT_CATEGORY_OVERRIDES_TABLE or not event_type:
        return {}
    try:
        item = dynamodb.Table(SEAT_CATEGORY_OVERRIDES_TABLE).get_item(
            Key={"eventType": event_type}
        ).get("Item")
        overrides = (item or {}).get("overrides", {})
        return dict(overrides) if isinstance(overrides, dict) else {}
    except ClientError:
        return {}


def set_category_override(event_type: str, seat_id: str, category: str) -> None:
    """Admin: reassign a single seat's cosmetic category, overriding
    whatever _ROW_SPECS would otherwise give it. The seat stays fully
    bookable — this never touches the seats table."""
    if not SEAT_CATEGORY_OVERRIDES_TABLE:
        raise RuntimeError("Seat category overrides are not configured")
    if category not in ZONE_OVERRIDE_CATEGORIES:
        raise ValueError(f"category must be one of: {', '.join(ZONE_OVERRIDE_CATEGORIES)}")
    overrides = get_category_overrides(event_type)
    overrides[seat_id] = category
    dynamodb.Table(SEAT_CATEGORY_OVERRIDES_TABLE).put_item(
        Item={"eventType": event_type, "overrides": overrides, "updatedAt": now_iso()}
    )


def clear_category_override(event_type: str, seat_id: str) -> None:
    """Admin: drop a seat's override, reverting it to its _ROW_SPECS
    default. A no-op if the seat was never overridden."""
    if not SEAT_CATEGORY_OVERRIDES_TABLE:
        return
    overrides = get_category_overrides(event_type)
    if seat_id not in overrides:
        return
    del overrides[seat_id]
    dynamodb.Table(SEAT_CATEGORY_OVERRIDES_TABLE).put_item(
        Item={"eventType": event_type, "overrides": overrides, "updatedAt": now_iso()}
    )


def get_zone_pricing(event_type: str) -> dict:
    """{category: {displayName, pricePerFamily, pricePerPerson, maxFamilySize,
    status}} for every bookable category (zone1/zone2/zone3/regular) an admin
    has priced for this eventType. A category absent here has no price set —
    treat it as not for sale. {} if the table isn't configured."""
    if not ZONE_PRICING_TABLE or not event_type:
        return {}
    try:
        response = dynamodb.Table(ZONE_PRICING_TABLE).query(
            KeyConditionExpression=Key("eventType").eq(event_type)
        )
        return {item["category"]: item for item in response.get("Items", [])}
    except ClientError:
        return {}


def get_zone_price(event_type: str, category: str) -> dict | None:
    """A single category's pricing row, or None if unpriced/unconfigured."""
    if not ZONE_PRICING_TABLE or not event_type:
        return None
    try:
        return dynamodb.Table(ZONE_PRICING_TABLE).get_item(
            Key={"eventType": event_type, "category": category}
        ).get("Item")
    except ClientError:
        return None


def set_zone_price(
    event_type: str,
    category: str,
    display_name: str,
    price_per_family: int,
    price_per_person: int,
    max_family_size: int = 0,
    status: str = "ACTIVE",
) -> dict:
    """Admin: set (or replace) one category's price. price_per_family=0 means
    this zone has no family plan — it always bills at price_per_person, even
    when the purchase's overall pricing_mode is "family". max_family_size=0
    means uncapped: the flat family fee covers every seat picked in this
    zone, however many."""
    if not ZONE_PRICING_TABLE:
        raise RuntimeError("Zone pricing is not configured")
    if category not in PRICEABLE_CATEGORIES:
        raise ValueError(f"category must be one of: {', '.join(PRICEABLE_CATEGORIES)}")
    if price_per_family < 0 or price_per_person < 0 or max_family_size < 0:
        raise ValueError("Prices and max family size must not be negative")
    if status not in ("ACTIVE", "INACTIVE"):
        raise ValueError("status must be ACTIVE or INACTIVE")
    item = {
        "eventType": event_type,
        "category": category,
        "displayName": display_name or category,
        "pricePerFamily": price_per_family,
        "pricePerPerson": price_per_person,
        "maxFamilySize": max_family_size,
        "status": status,
        "updatedAt": now_iso(),
    }
    dynamodb.Table(ZONE_PRICING_TABLE).put_item(Item=item)
    return item


def delete_zone_price(event_type: str, category: str) -> None:
    """Admin: unprice a category — its seats become not-for-sale again."""
    if not ZONE_PRICING_TABLE:
        return
    dynamodb.Table(ZONE_PRICING_TABLE).delete_item(Key={"eventType": event_type, "category": category})


def price_seats(
    event_type: str,
    seat_ids: list,
    pricing_mode: str,
    category_overrides: dict | None = None,
    zone_pricing: dict | None = None,
    free: bool = False,
) -> dict:
    """Price a set of seat IDs under pricing_mode ("family"|"person").

    Returns {"totalAmount", "breakdown": [{"category","displayName",
    "seatIds","familySeats","personSeats","familyFee","personFee",
    "subtotal"}, ...], "unpriced": [seatId, ...]}. `unpriced` lists any
    seat whose category has no ACTIVE pricing row — callers should refuse
    to create a purchase while it's non-empty, rather than silently
    charging 0 for those seats. Regular seats are the one exception: they're
    never unpriced/blocked and always bill ¥0, regardless of zone_pricing —
    see PRICEABLE_CATEGORIES.

    `free=True` (a host that's turned its own pricing off entirely — see
    seat_purchase_logic.create_purchase_request's own `free` param) treats
    *every* category the same way "regular" is always treated: grouped into
    the breakdown at ¥0, never "unpriced", with ZONE_DISPLAY_NAMES supplying
    a label since there's no zone_pricing row to read one from. Without this,
    a free zone1/2/3 seat would have nowhere to go (zone_pricing has no
    ACTIVE row to price it against) and `breakdown` would come back empty —
    which is exactly why a purely free claim needs its own path rather than
    just being called with an empty `zone_pricing` dict.
    """
    if pricing_mode not in PRICING_MODES:
        raise ValueError(f"pricing_mode must be one of: {', '.join(PRICING_MODES)}")
    overrides = category_overrides if category_overrides is not None else get_category_overrides(event_type)
    pricing = zone_pricing if zone_pricing is not None else get_zone_pricing(event_type)

    by_category: dict = {}
    unpriced = []
    for seat_id in seat_ids:
        seat = find_seat(seat_id, overrides)
        category = seat["category"] if seat else None
        if not seat:
            unpriced.append(seat_id)
            continue
        if free or category == "regular":
            by_category.setdefault(category, []).append(seat_id)
            continue
        price = pricing.get(category)
        if not price or price.get("status") != "ACTIVE":
            unpriced.append(seat_id)
            continue
        by_category.setdefault(category, []).append(seat_id)

    breakdown = []
    total_amount = 0
    for category, ids in by_category.items():
        if free or category == "regular":
            breakdown.append(
                {
                    "category": category,
                    "displayName": ZONE_DISPLAY_NAMES.get(category, category),
                    "seatIds": sorted(ids),
                    "familySeats": 0,
                    "personSeats": len(ids),
                    "familyFee": 0,
                    "personFee": 0,
                    "subtotal": 0,
                }
            )
            continue
        price = pricing[category]
        price_per_family = int(price.get("pricePerFamily", 0) or 0)
        price_per_person = int(price.get("pricePerPerson", 0) or 0)
        max_family_size = int(price.get("maxFamilySize", 0) or 0)
        seat_count = len(ids)

        if pricing_mode == "family" and price_per_family > 0:
            family_seats = seat_count if max_family_size == 0 else min(seat_count, max_family_size)
            person_seats = seat_count - family_seats
            family_fee = price_per_family
            person_fee = person_seats * price_per_person
        else:
            family_seats = 0
            person_seats = seat_count
            family_fee = 0
            person_fee = seat_count * price_per_person

        subtotal = family_fee + person_fee
        total_amount += subtotal
        breakdown.append(
            {
                "category": category,
                "displayName": price.get("displayName", category),
                "seatIds": sorted(ids),
                "familySeats": family_seats,
                "personSeats": person_seats,
                "familyFee": family_fee,
                "personFee": person_fee,
                "subtotal": subtotal,
            }
        )

    breakdown.sort(key=lambda row: row["category"])
    return {"totalAmount": total_amount, "breakdown": breakdown, "unpriced": unpriced}


SEAT_SELECTION_PAYMENT_METHODS = ("paypay", "square", "bank")


def parse_seat_selection_config(event_info: dict) -> dict:
    """Pure parser for the admin-configured seat-selection settings your host
    app stores at eventInfo.seatSelection — {"enabled": bool, "pricingEnabled":
    bool, "paymentMethods": [...], "startDate": str, "endDate": str}. Takes
    the already-loaded eventInfo dict rather than reading your events table
    itself (this module never touches host-owned tables); your own Lambda
    reads eventInfo and passes it in.

    Off entirely for an event with no seatSelection block, or `enabled` not
    exactly `true`. Once enabled, an event that's never configured
    paymentMethods defaults to offering all three.

    `pricingEnabled` defaults to `true` (this module's original always-priced
    behavior) so an event configured before this flag existed is unaffected;
    an admin explicitly sets it `false` to switch this event's seat picker
    over to always-free claims (see create_purchase_request's `free` param) —
    typically because they're gating which zone a registrant may pick from
    some other way instead (e.g. a separate paid zone-upgrade add-on), which
    is entirely your host's own business rule to enforce before calling
    create_purchase_request, not something this module knows about.

    `adminOverrideMaxSeats` defaults to `false` and is entirely your host's
    own business rule too (this module never enforces a per-registration
    seat cap itself) — it only ever means something to a host's own
    admin-allocation flow: `false` (the default) means that flow should cap
    an admin's allocation at whatever per-registration seat allowance the
    host computes (e.g. one seat per eligible attendee); `true` lets an
    admin allocate any number of seats regardless of that cap.

    `announcementClosingNote` defaults to `""` (omitted) — free text an
    admin can set to end the "seat selection is open" announcement email
    with a short, event-specific closing line (e.g. "We look forward to
    celebrating this milestone with you." doesn't fit every event, so it's
    opt-in per event rather than baked into the email's fixed copy).

    `adminNotificationsEnabled` defaults to `true` (this module's original
    always-notify behavior) — an admin sets it `false` to stop the "Seat
    purchase requested/paid/cancelled/..." emails ADMIN_EMAIL otherwise gets
    for every purchase event on this event, e.g. once seat volume makes
    them more noise than signal. Registrant-facing emails are unaffected;
    see seat_purchase_emailer.send_admin_notification, the only sender this
    gates.
    """
    empty = {
        "enabled": False,
        "pricingEnabled": True,
        "adminOverrideMaxSeats": False,
        "paymentMethods": list(SEAT_SELECTION_PAYMENT_METHODS),
        "startDate": "",
        "endDate": "",
        "announcementClosingNote": "",
        "adminNotificationsEnabled": True,
    }
    raw = (event_info or {}).get("seatSelection")
    if not isinstance(raw, dict):
        return empty

    methods = raw.get("paymentMethods")
    if isinstance(methods, list):
        methods = [m for m in methods if m in SEAT_SELECTION_PAYMENT_METHODS]
    if not methods:
        methods = list(SEAT_SELECTION_PAYMENT_METHODS)
    return {
        "enabled": raw.get("enabled") is True,
        "pricingEnabled": raw.get("pricingEnabled") is not False,
        "adminOverrideMaxSeats": raw.get("adminOverrideMaxSeats") is True,
        "paymentMethods": methods,
        "startDate": raw.get("startDate") if isinstance(raw.get("startDate"), str) else "",
        "endDate": raw.get("endDate") if isinstance(raw.get("endDate"), str) else "",
        "announcementClosingNote": (
            raw.get("announcementClosingNote").strip()
            if isinstance(raw.get("announcementClosingNote"), str)
            else ""
        ),
        "adminNotificationsEnabled": raw.get("adminNotificationsEnabled") is not False,
    }


def seat_selection_window_status(config: dict) -> str:
    """"not_started" | "open" | "ended", from today's UTC date against the
    admin-configured startDate/endDate (both optional, inclusive)."""
    today = datetime.now(timezone.utc).date().isoformat()
    start = config.get("startDate") or ""
    end = config.get("endDate") or ""
    if start and today < start:
        return "not_started"
    if end and today > end:
        return "ended"
    return "open"


def get_occupied_seats(event_type: str) -> dict:
    """{seatId: item} for every booked/blocked seat. The table is sparse — an
    unlisted seat is available."""
    if not SEATS_TABLE:
        return {}
    table = dynamodb.Table(SEATS_TABLE)
    items = []
    query_kwargs = {"KeyConditionExpression": Key("eventType").eq(event_type)}
    while True:
        response = table.query(**query_kwargs)
        items.extend(response.get("Items", []))
        if "LastEvaluatedKey" not in response:
            break
        query_kwargs["ExclusiveStartKey"] = response["LastEvaluatedKey"]
    return {item["seatId"]: item for item in items}


def get_seats_for_registration(event_type: str, registration_id: str) -> list:
    """The currently-held seats for a caller-supplied `registration_id` (any
    opaque string identifying whoever holds these seats — empty if none, or
    if the table isn't configured)."""
    if not SEATS_TABLE or not registration_id:
        return []
    try:
        response = dynamodb.Table(SEATS_TABLE).query(
            IndexName="RegistrationIdIndex",
            KeyConditionExpression=Key("registrationId").eq(registration_id),
        )
        return [item for item in response.get("Items", []) if item.get("eventType") == event_type]
    except ClientError:
        return []


def claim_seats_atomic(
    event_type: str, registration_id: str, seat_ids: list, holder_name: str = ""
) -> list:
    """Atomically claim every seat in seat_ids for registration_id.

    One conditional Put per seat, all inside a single DynamoDB transaction, so
    a party's whole seat selection is either fully committed or not committed
    at all — no partial bookings, and no seat can be claimed twice (each
    Put's ConditionExpression is the optimistic lock).

    Raises ValueError if one or more seats are already taken, RuntimeError on
    any other failure.
    """
    if not SEATS_TABLE:
        raise RuntimeError("Seat selection is not configured")
    if not seat_ids:
        raise ValueError("At least one seat must be selected")
    if len(seat_ids) > MAX_SEATS_PER_REQUEST:
        raise ValueError(f"Cannot claim more than {MAX_SEATS_PER_REQUEST} seats in one request")

    timestamp = now_iso()
    transact_items = [
        {
            "Put": {
                "TableName": SEATS_TABLE,
                "Item": {
                    "eventType": event_type,
                    "seatId": seat_id,
                    "registrationId": registration_id,
                    "holderName": holder_name or "",
                    "status": "BOOKED",
                    "bookedAt": timestamp,
                    "updatedAt": timestamp,
                },
                "ConditionExpression": "attribute_not_exists(seatId)",
            }
        }
        for seat_id in seat_ids
    ]

    try:
        dynamodb_client.transact_write_items(TransactItems=transact_items)
    except ClientError as exc:
        if exc.response.get("Error", {}).get("Code") == "TransactionCanceledException":
            occupied = get_occupied_seats(event_type)
            unavailable = [seat_id for seat_id in seat_ids if seat_id in occupied]
            names = ", ".join(unavailable) if unavailable else ", ".join(seat_ids)
            raise ValueError(f"Seat(s) already taken: {names}") from exc
        raise RuntimeError(f"Could not reserve seats: {exc}") from exc

    return seat_ids


def release_seats(event_type: str, seat_ids: list) -> None:
    """Unconditionally release seats (cancellation/reassignment). Best-effort
    per seat — a seat that was never claimed is simply a no-op delete."""
    if not SEATS_TABLE or not seat_ids:
        return
    table = dynamodb.Table(SEATS_TABLE)
    for seat_id in seat_ids:
        try:
            table.delete_item(Key={"eventType": event_type, "seatId": seat_id})
        except ClientError:
            continue


def block_seat(event_type: str, seat_id: str, reason: str = "", block_category: str = "") -> dict:
    """Admin: mark a seat unavailable without attaching it to a registration
    (e.g. a broken chair, an AV blind spot, a VIP reservation —
    block_category="" — or a camera platform / projector booth —
    block_category is one of NON_BOOKABLE_CATEGORIES). Either way the seat
    becomes unselectable, and block_category only changes which icon a
    frontend shows for it.

    registrationId is omitted entirely rather than set to "" — it's the
    RegistrationIdIndex GSI's key attribute, and DynamoDB rejects an empty
    string there (ValidationException: "The AttributeValue for a key
    attribute cannot contain an empty string value"). Omitting it just
    means the item doesn't appear in that index, which is correct: a
    blocked seat isn't anyone's registration.
    """
    if not SEATS_TABLE:
        raise RuntimeError("Seat selection is not configured")
    if block_category and block_category not in NON_BOOKABLE_CATEGORIES:
        raise ValueError(f"block_category must be one of: {', '.join(NON_BOOKABLE_CATEGORIES)}")
    timestamp = now_iso()
    item = {
        "eventType": event_type,
        "seatId": seat_id,
        "status": "BLOCKED",
        "reason": reason,
        "blockCategory": block_category,
        "bookedAt": timestamp,
        "updatedAt": timestamp,
    }
    try:
        dynamodb.Table(SEATS_TABLE).put_item(
            Item=item, ConditionExpression="attribute_not_exists(seatId)"
        )
    except ClientError as exc:
        if _is_conditional_check_failed(exc):
            raise ValueError(f"Seat {seat_id} is already taken") from exc
        raise RuntimeError(f"Could not block seat: {exc}") from exc
    return item


def unblock_seat(event_type: str, seat_id: str) -> None:
    """Admin: reset a seat back to its hall default — releases a BLOCKED
    seat (generic/camera/projector) and drops any zone1/2/3/regular
    override, whichever applies (harmless no-op for whichever doesn't).
    Refuses to touch a seat that's actually booked by a registration —
    release those through the registration flow instead, so its
    registrationId link isn't silently dropped."""
    if SEATS_TABLE:
        try:
            dynamodb.Table(SEATS_TABLE).delete_item(
                Key={"eventType": event_type, "seatId": seat_id},
                ConditionExpression="attribute_not_exists(registrationId)",
            )
        except ClientError as exc:
            if _is_conditional_check_failed(exc):
                raise ValueError(
                    f"Seat {seat_id} is booked by a registration; release it from there instead"
                ) from exc
            raise RuntimeError(f"Could not unblock seat: {exc}") from exc
    clear_category_override(event_type, seat_id)
