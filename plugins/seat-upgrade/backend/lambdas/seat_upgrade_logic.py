"""
Seat Upgrade Capacity Management Utilities

Shared business logic for the Celebrity Event seat-upgrade add-on: admin-defined
paid tiers (e.g. "Premium", "VIP") with limited capacity, and the purchase
requests registrants make against them.

Deliberately independent of the registrations table/entry-slots feature — an
upgrade only ever references a registrationId, the same way entry_allocations
references one, so it can be added, changed or removed without touching the
original registration record.

Capacity accounting follows the same atomic-counter pattern as
entry_slots_logic: each tier carries an `upgradedSeats` counter, incremented
by a conditional update that also enforces the tier's capacity, and
decremented on cancellation.
"""

import os
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from typing import Optional

import boto3
from botocore.exceptions import ClientError
from boto3.dynamodb.conditions import Key, Attr

dynamodb = boto3.resource("dynamodb", region_name=os.environ.get("REGION"))
SEAT_UPGRADE_TIERS_TABLE = os.environ.get("SEAT_UPGRADE_TIERS_TABLE", "")
SEAT_UPGRADES_TABLE = os.environ.get("SEAT_UPGRADES_TABLE", "")
SEAT_UPGRADE_EMAIL_TEMPLATES_TABLE = os.environ.get("SEAT_UPGRADE_EMAIL_TEMPLATES_TABLE", "")

# The seat-selection plugin's own fixed bookable categories (zone1/zone2/
# zone3 — see plugins/seat-selection/backend/seat_selection_logic.py's
# ZONE_OVERRIDE_CATEGORIES), duplicated here as plain strings rather than
# imported: the two plugins are independent, with no cross-plugin import
# (same "copied module" convention used elsewhere in this codebase). A tier
# optionally names one of these so a host that's turned seat-selection's own
# pricing off (eventInfo.seatSelection.pricingEnabled=false) can instead gate
# who may pick from that seat-map zone by who's paid for this tier — see
# this field's own docstring below on create_tier for the full picture; this
# module itself never reads or acts on it beyond storing/denormalizing it.
SEAT_MAP_CATEGORIES = ("zone1", "zone2", "zone3")

# Keys for the four registrant-facing emails an admin can customize — see
# seat_upgrade_emailer.py's DEFAULT_TEMPLATES for what each field means and
# its built-in default. Deliberately not "requested"/"refund"/etc. — these
# match the upgrade lifecycle's own status values 1:1 except "request",
# which doesn't correspond to a status (the email fires on creation, while
# status is still "pending").
EMAIL_TEMPLATE_KEYS = {"request", "paid", "refund_requested", "cancelled"}
EMAIL_TEMPLATE_FIELDS = {"subject", "title", "tone", "bannerText", "introText", "ctaLabel"}
VALID_EMAIL_TEMPLATE_TONES = {"success", "pending", "danger"}

VALID_TIER_STATUSES = {"ACTIVE", "INACTIVE"}
# "refund_requested" sits between paid and cancelled: a registrant cancelling
# an already-paid upgrade lands here first (see cancel_upgrade_request) and
# still holds its tier capacity — only an admin finalizing it to "cancelled"
# (once the refund has actually been confirmed, automatically or by hand)
# frees the seat back up. This mirrors the same "admin confirms" pattern the
# rest of the app already uses for bank-transfer payment.
VALID_UPGRADE_STATUSES = {"pending", "paid", "cancelled", "refund_requested"}
ACTIVE_UPGRADE_STATUSES = {"pending", "paid", "refund_requested"}  # statuses that occupy tier capacity


def _is_conditional_check_failed(exc: ClientError) -> bool:
    return exc.response.get("Error", {}).get("Code") == "ConditionalCheckFailedException"


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def decimal_to_python(obj):
    if isinstance(obj, Decimal):
        return int(obj) if obj == obj.to_integral_value() else float(obj)
    raise TypeError(f"Object of type {type(obj)} is not JSON serializable")


# ── Tiers ─────────────────────────────────────────────────────────────────────


def get_tier(event_type: str, tier_id: str) -> Optional[dict]:
    if not SEAT_UPGRADE_TIERS_TABLE:
        return None
    try:
        table = dynamodb.Table(SEAT_UPGRADE_TIERS_TABLE)
        return table.get_item(Key={"eventType": event_type, "tierId": tier_id}).get("Item")
    except ClientError as e:
        raise RuntimeError(f"Error fetching zone upgrade tier: {e}")


def list_tiers_for_event(event_type: str) -> list:
    """All tiers for an event (any status), oldest first."""
    if not SEAT_UPGRADE_TIERS_TABLE:
        return []
    try:
        table = dynamodb.Table(SEAT_UPGRADE_TIERS_TABLE)
        response = table.query(KeyConditionExpression=Key("eventType").eq(event_type))
        items = response.get("Items", [])
        items.sort(key=lambda x: x.get("createdAt", ""))
        return items
    except ClientError as e:
        raise RuntimeError(f"Error listing zone upgrade tiers: {e}")


def list_active_tiers_for_event(event_type: str) -> list:
    """ACTIVE tiers only, for the public/self-service picker."""
    return [t for t in list_tiers_for_event(event_type) if str(t.get("status", "")).upper() == "ACTIVE"]


# Sentinel distinguishing "field not provided, don't touch it" from
# "explicitly clear it to None" on update_tier's map_region — plain
# Optional[dict]=None can't tell those apart the way it can for a string
# field (where None already always means "not sent").
UNSET = object()


def _normalize_region(region) -> Optional[dict]:
    """Validate an admin-drawn map hotspot and convert it to DynamoDB-safe
    Decimals. `region` is {x, y, width, height} — fractions (0-1) of the
    event's seat map image's width/height, drawn in
    SeatUpgradeManagement.jsx's region editor and rendered as a clickable
    overlay on /my/seat-upgrade (see SeatUpgrade.jsx). None means "no
    spatial hotspot for this tier" — it still shows in the plain zone-card
    list either way.
    """
    if region is None:
        return None
    if not isinstance(region, dict):
        raise ValueError("mapRegion must be an object with x, y, width, and height")
    try:
        x = Decimal(str(region["x"]))
        y = Decimal(str(region["y"]))
        width = Decimal(str(region["width"]))
        height = Decimal(str(region["height"]))
    except (KeyError, TypeError, ValueError, ArithmeticError):
        raise ValueError("mapRegion.x/y/width/height must all be numbers")
    if not (0 <= x <= 1 and 0 <= y <= 1):
        raise ValueError("mapRegion.x/y must be between 0 and 1")
    if width <= 0 or height <= 0:
        raise ValueError("mapRegion.width/height must be greater than 0")
    if x + width > 1 or y + height > 1:
        raise ValueError("mapRegion must stay within the image bounds")
    return {"x": x, "y": y, "width": width, "height": height}


def create_tier(
    *,
    event_type: str,
    name: str,
    description: str = "",
    upgrade_price: int,
    capacity: int,
    status: str = "ACTIVE",
    display_order: int = 0,
    map_region: Optional[dict] = None,
    seat_map_category: str = "",
) -> dict:
    """`seat_map_category` ("" | one of SEAT_MAP_CATEGORIES) links this tier
    to one of the seat-selection plugin's fixed bookable categories. It's
    purely informational here — recorded on the tier, and snapshotted onto
    each upgrade request's zoneBreakdown entry at purchase time (see
    price_seat_picks) — this module never enforces anything with it. A host
    running both plugins reads it back (e.g. wmfjapan's
    lambdas/my_registrations/handler.py) to let a paid upgrade into this
    tier unlock free seat-picking from the matching seat-map zone once
    that host has turned seat-selection's own pricing off. "" (the default)
    means this tier unlocks nothing there — it still works as a plain paid
    zone upgrade either way.
    """
    if not SEAT_UPGRADE_TIERS_TABLE:
        raise RuntimeError("Zone upgrade tiers table not configured")
    if upgrade_price < 0:
        raise ValueError("Price must be non-negative")
    if capacity < 0:
        raise ValueError("Capacity must be non-negative")
    if seat_map_category and seat_map_category not in SEAT_MAP_CATEGORIES:
        raise ValueError(f"seat_map_category must be one of: {', '.join(SEAT_MAP_CATEGORIES)}")
    region = _normalize_region(map_region)

    tier_id = str(uuid.uuid4())
    now = now_iso()
    tier = {
        "eventType": event_type,
        "tierId": tier_id,
        "name": name,
        "description": description or "",
        # Same price for every person upgrading into this zone, whether
        # they're the family's 1st, 2nd or discounted 3rd+ upgrade — see
        # create_upgrade_request's family-discount docstring for how the
        # charged amount can differ from this.
        "upgradePrice": upgrade_price,
        "capacity": capacity,
        "upgradedSeats": 0,
        "status": status.upper(),
        "displayOrder": display_order,
        "seatMapCategory": seat_map_category or "",
        "createdAt": now,
        "updatedAt": now,
    }
    if region is not None:
        tier["mapRegion"] = region
    try:
        dynamodb.Table(SEAT_UPGRADE_TIERS_TABLE).put_item(Item=tier)
        return tier
    except ClientError as e:
        raise RuntimeError(f"Error creating zone upgrade tier: {e}")


def update_tier(
    *,
    event_type: str,
    tier_id: str,
    name: Optional[str] = None,
    description: Optional[str] = None,
    upgrade_price: Optional[int] = None,
    capacity: Optional[int] = None,
    status: Optional[str] = None,
    display_order: Optional[int] = None,
    map_region=UNSET,
    seat_map_category: Optional[str] = None,
) -> dict:
    """seat_map_category: None leaves it untouched; "" clears it back to
    "unlocks nothing"; otherwise one of SEAT_MAP_CATEGORIES. See create_tier's
    docstring for what it's for."""
    if not SEAT_UPGRADE_TIERS_TABLE:
        raise RuntimeError("Zone upgrade tiers table not configured")
    if upgrade_price is not None and upgrade_price < 0:
        raise ValueError("Price must be non-negative")
    if capacity is not None and capacity < 0:
        raise ValueError("Capacity must be non-negative")
    if seat_map_category and seat_map_category not in SEAT_MAP_CATEGORIES:
        raise ValueError(f"seat_map_category must be one of: {', '.join(SEAT_MAP_CATEGORIES)}")
    # UNSET means "field absent from the request, leave it alone"; a
    # present-but-None map_region means "explicitly clear the hotspot" —
    # see _normalize_region and the UNSET sentinel's own docstring above.
    region = _normalize_region(map_region) if map_region is not UNSET else UNSET

    updates = []
    removes = []
    values = {}
    names = {}

    # Every field is aliased via ExpressionAttributeNames, not just the ones
    # already known to collide (name, status, and — the one that actually
    # broke in production — capacity, which is easy to miss since it isn't
    # as obviously "keyword-shaped" as NAME/STATUS/SIZE/ORDER). DynamoDB's
    # ~570-word reserved list isn't worth memorizing field-by-field; aliasing
    # unconditionally is free and closes the door on hitting another one.
    def _set(field: str, value) -> None:
        alias = f"#{field}"
        updates.append(f"{alias} = :{field}")
        names[alias] = field
        values[f":{field}"] = value

    def _remove(field: str) -> None:
        alias = f"#{field}"
        removes.append(alias)
        names[alias] = field

    if name is not None:
        _set("name", name)
    if description is not None:
        _set("description", description)
    if upgrade_price is not None:
        _set("upgradePrice", upgrade_price)
    if capacity is not None:
        _set("capacity", capacity)
    if status is not None:
        _set("status", status.upper())
    if display_order is not None:
        _set("displayOrder", display_order)
    if seat_map_category is not None:
        _set("seatMapCategory", seat_map_category)
    if region is not UNSET:
        if region is None:
            _remove("mapRegion")
        else:
            _set("mapRegion", region)

    if not updates and not removes:
        return get_tier(event_type, tier_id) or {}

    updates.append("updatedAt = :now")
    values[":now"] = now_iso()

    condition_expression = None
    if capacity is not None:
        condition_expression = (
            Attr("upgradedSeats").not_exists() | Attr("upgradedSeats").lte(capacity)
        )

    update_expression = "SET " + ", ".join(updates)
    if removes:
        update_expression += " REMOVE " + ", ".join(removes)

    try:
        table = dynamodb.Table(SEAT_UPGRADE_TIERS_TABLE)
        kwargs = {
            "Key": {"eventType": event_type, "tierId": tier_id},
            "UpdateExpression": update_expression,
            "ExpressionAttributeValues": values,
            "ReturnValues": "ALL_NEW",
        }
        if names:
            kwargs["ExpressionAttributeNames"] = names
        if condition_expression is not None:
            kwargs["ConditionExpression"] = condition_expression
        response = table.update_item(**kwargs)
        return response.get("Attributes", {})
    except ClientError as e:
        if _is_conditional_check_failed(e):
            current = get_tier(event_type, tier_id) or {}
            sold = int(current.get("upgradedSeats", 0) or 0)
            raise ValueError(f"Capacity cannot be set below seats already sold ({sold})")
        raise RuntimeError(f"Error updating zone upgrade tier: {e}")


def delete_tier(event_type: str, tier_id: str) -> None:
    if not SEAT_UPGRADE_TIERS_TABLE:
        raise RuntimeError("Zone upgrade tiers table not configured")
    try:
        dynamodb.Table(SEAT_UPGRADE_TIERS_TABLE).delete_item(
            Key={"eventType": event_type, "tierId": tier_id}
        )
    except ClientError as e:
        raise RuntimeError(f"Error deleting zone upgrade tier: {e}")


def _adjust_tier_seats(event_type: str, tier_id: str, delta: int) -> dict:
    """Atomically adjust a tier's upgradedSeats counter.

    A positive delta is capacity-checked against the tier's capacity; a
    negative delta (release) is floored at zero.
    """
    if not SEAT_UPGRADE_TIERS_TABLE:
        raise RuntimeError("Zone upgrade tiers table not configured")

    table = dynamodb.Table(SEAT_UPGRADE_TIERS_TABLE)

    if delta > 0:
        tier = get_tier(event_type, tier_id)
        if not tier:
            raise RuntimeError(f"Zone upgrade tier {tier_id} not found for event {event_type}")
        capacity = max(int(tier.get("capacity", 0) or 0), 0)
        max_sold_before = capacity - delta
        if max_sold_before < 0:
            raise RuntimeError(
                f"Insufficient capacity in tier {tier_id}: requested {delta}, capacity {capacity}"
            )
        try:
            response = table.update_item(
                Key={"eventType": event_type, "tierId": tier_id},
                UpdateExpression=(
                    "SET upgradedSeats = if_not_exists(upgradedSeats, :zero) + :delta, "
                    "updatedAt = :now"
                ),
                ConditionExpression=(
                    Attr("status").eq("ACTIVE")
                    & (Attr("upgradedSeats").not_exists() | Attr("upgradedSeats").lte(max_sold_before))
                ),
                ExpressionAttributeValues={":zero": 0, ":delta": delta, ":now": now_iso()},
                ReturnValues="ALL_NEW",
            )
            return response.get("Attributes", {})
        except ClientError as e:
            if _is_conditional_check_failed(e):
                current = get_tier(event_type, tier_id) or tier
                sold = int(current.get("upgradedSeats", 0) or 0)
                available = max(capacity - sold, 0)
                raise RuntimeError(
                    f"Insufficient capacity in tier {tier_id}: requested {delta}, available {available}"
                )
            raise RuntimeError(f"Error reserving zone upgrade capacity: {e}")

    seats = -delta
    if seats <= 0:
        return get_tier(event_type, tier_id) or {}
    try:
        response = table.update_item(
            Key={"eventType": event_type, "tierId": tier_id},
            UpdateExpression="SET upgradedSeats = upgradedSeats - :seats, updatedAt = :now",
            ConditionExpression=Attr("upgradedSeats").gte(seats),
            ExpressionAttributeValues={":seats": seats, ":now": now_iso()},
            ReturnValues="ALL_NEW",
        )
        return response.get("Attributes", {})
    except ClientError as e:
        if _is_conditional_check_failed(e):
            response = table.update_item(
                Key={"eventType": event_type, "tierId": tier_id},
                UpdateExpression="SET upgradedSeats = :zero, updatedAt = :now",
                ExpressionAttributeValues={":zero": 0, ":now": now_iso()},
                ReturnValues="ALL_NEW",
            )
            return response.get("Attributes", {})
        raise RuntimeError(f"Error releasing zone upgrade capacity: {e}")


# ── Upgrade requests ─────────────────────────────────────────────────────────


def get_upgrade(upgrade_id: str) -> Optional[dict]:
    if not SEAT_UPGRADES_TABLE:
        return None
    try:
        return dynamodb.Table(SEAT_UPGRADES_TABLE).get_item(Key={"upgradeId": upgrade_id}).get("Item")
    except ClientError as e:
        raise RuntimeError(f"Error fetching zone upgrade: {e}")


def list_upgrades_for_registration(registration_id: str) -> list:
    if not SEAT_UPGRADES_TABLE or not registration_id:
        return []
    try:
        table = dynamodb.Table(SEAT_UPGRADES_TABLE)
        response = table.query(
            IndexName="RegistrationIdIndex",
            KeyConditionExpression=Key("registrationId").eq(registration_id),
        )
        items = response.get("Items", [])
        items.sort(key=lambda x: x.get("requestedAt", ""), reverse=True)
        return items
    except ClientError as e:
        raise RuntimeError(f"Error listing zone upgrades for registration: {e}")


def list_upgrades_for_event(event_type: str) -> list:
    if not SEAT_UPGRADES_TABLE or not event_type:
        return []
    try:
        table = dynamodb.Table(SEAT_UPGRADES_TABLE)
        response = table.query(
            IndexName="EventTypeIndex",
            KeyConditionExpression=Key("eventType").eq(event_type),
        )
        items = response.get("Items", [])
        items.sort(key=lambda x: x.get("requestedAt", ""), reverse=True)
        return items
    except ClientError as e:
        raise RuntimeError(f"Error listing zone upgrades for event: {e}")


# The family discount: a registration's first 2 upgraded seats (counted
# cumulatively across every active purchase it's ever made, not just this
# one) are at full zone price; the 3rd and every one after that are at
# FAMILY_DISCOUNT_PERCENT off. Applies to seating only — never to dinner.
FAMILY_DISCOUNT_PERCENT = 30
FULL_PRICE_SEAT_COUNT = 2


def get_active_upgraded_unit_count(registration_id: str) -> int:
    """How many seats this registration has already upgraded across every
    still-active (pending/paid/refund_requested) purchase — the running
    total create_upgrade_request needs to know whether a new purchase's
    seats land in the free-standing "first 2" or the discounted "3rd+".
    """
    total = 0
    for u in list_upgrades_for_registration(registration_id):
        if u.get("status") in ACTIVE_UPGRADE_STATUSES:
            total += int(u.get("seatCount", 0) or 0)
    return total


DINNER_GUEST_FIELDS = (
    "dinnerAdultVegGuests", "dinnerAdultNonVegGuests",
    "dinnerKids914VegGuests", "dinnerKids914NonVegGuests",
    "dinnerKids68VegGuests", "dinnerKids68NonVegGuests",
    "dinnerKidsUnder6VegGuests", "dinnerKidsUnder6NonVegGuests",
)


def get_active_dinner_guest_counts(registration_id: str) -> dict:
    """{"dinnerAdultVegGuests": ..., "dinnerAdultNonVegGuests": ..., ...} —
    every DINNER_GUEST_FIELDS key, summed across every still-active purchase
    for this registration."""
    totals = {field: 0 for field in DINNER_GUEST_FIELDS}
    for u in list_upgrades_for_registration(registration_id):
        if u.get("status") in ACTIVE_UPGRADE_STATUSES:
            for field in DINNER_GUEST_FIELDS:
                totals[field] += int(u.get(field, 0) or 0)
    return totals


def price_seat_picks(picks: list, existing_upgraded_count: int) -> dict:
    """Price a cart of zone picks against the family discount.

    `picks` is [{"tier": <tier dict>, "count": <int>}, ...] — one entry per
    zone the registrant chose a quantity for. Expands to one "unit" per
    seat, sorted most-expensive-first so the family's free-standing first 2
    seats (across ALL active purchases, not just this one — see
    `existing_upgraded_count`) land on the pricier zones and the discount
    lands on the cheaper ones, same convention as a typical "buy 2, cheaper
    extras discounted" bundle.

    Returns {"zoneBreakdown": [...], "seatCount": int, "upgradeAmount": int,
    "tierSummary": str} — zoneBreakdown groups the priced units back up by
    zone for capacity reservation and admin/email display.
    """
    units = []
    for pick in picks:
        tier = pick["tier"]
        count = int(pick["count"])
        price = int(tier.get("upgradePrice", 0) or 0)
        units.extend([(tier["tierId"], tier.get("name", ""), price, tier.get("seatMapCategory", ""))] * count)

    units.sort(key=lambda u: u[2], reverse=True)

    remaining_full = max(FULL_PRICE_SEAT_COUNT - existing_upgraded_count, 0)
    zone_order = []
    zones = {}
    for i, (tier_id, tier_name, price, seat_map_category) in enumerate(units):
        charged = price if i < remaining_full else round(price * (100 - FAMILY_DISCOUNT_PERCENT) / 100)
        if tier_id not in zones:
            zones[tier_id] = {
                "tierId": tier_id,
                "tierName": tier_name,
                "count": 0,
                "regularUnitPrice": price,
                "subtotal": 0,
                # Snapshotted from the tier at request time (see create_tier's
                # docstring) so a later admin change to the tier's mapping
                # doesn't retroactively change what an already-paid upgrade
                # entitles its holder to.
                "seatMapCategory": seat_map_category or "",
            }
            zone_order.append(tier_id)
        zones[tier_id]["count"] += 1
        zones[tier_id]["subtotal"] += charged

    zone_breakdown = [zones[tid] for tid in zone_order]
    upgrade_amount = sum(z["subtotal"] for z in zone_breakdown)
    tier_summary = ", ".join(
        f"{z['tierName']} ×{z['count']}" if z["count"] > 1 else z["tierName"]
        for z in zone_breakdown
    )
    return {
        "zoneBreakdown": zone_breakdown,
        "seatCount": len(units),
        "upgradeAmount": upgrade_amount,
        "tierSummary": tier_summary,
    }


def create_upgrade_request(
    *,
    event_type: str,
    picks: list,
    registration_id: str,
    email: str,
    name: str,
    existing_upgraded_count: int,
    payment_method: str = "",
    dinner_guest_counts: Optional[dict] = None,
    dinner_price_adults: int = 0,
    dinner_price_kids914: int = 0,
    dinner_price_kids68: int = 0,
) -> dict:
    """Reserve seats across every zone in `picks` and record the purchase.

    `picks` is [{"tier": <tier dict>, "count": <int>}, ...] — a registrant
    can upgrade several seats into several different zones in one purchase
    (the Family Upgrade Discount's "valid in all zones"). Pricing comes from
    price_seat_picks; capacity is reserved per zone, with any zone already
    reserved in this call released again if a later zone in the same call
    turns out to be full, so a request that can't be fully honored never
    leaves partial seats held.

    `dinner_guest_counts` is a dict keyed by DINNER_GUEST_FIELDS (0 for every
    key = not opted in) — the Celebrity Dinner add-on's veg/non-veg headcount
    per age category, never discounted, and never holding tier capacity. The
    `dinner_price_*` args come from the Celebrity Dinner plugin's own unified
    age-tiered config (this module owns none of that pricing — see
    seat_upgrade/handler.py's get_celebrity_dinner_addon_config), the same
    prices the standalone Celebrity Dinner booking charges. There is no
    dinner_price_kids_under6 — that category is never priced, only counted.

    Raises RuntimeError if any zone lacks capacity, ValueError on bad input.
    """
    if not SEAT_UPGRADES_TABLE:
        raise RuntimeError("Zone upgrades table not configured")
    if not picks:
        raise ValueError("At least one zone pick is required")
    for pick in picks:
        if int(pick["count"]) <= 0:
            raise ValueError("Each zone pick's count must be at least 1")
    dinner_guest_counts = {field: int((dinner_guest_counts or {}).get(field, 0) or 0) for field in DINNER_GUEST_FIELDS}
    if any(v < 0 for v in dinner_guest_counts.values()):
        raise ValueError("Dinner guest counts must be non-negative")

    priced = price_seat_picks(picks, existing_upgraded_count)
    dinner_adults_total = dinner_guest_counts["dinnerAdultVegGuests"] + dinner_guest_counts["dinnerAdultNonVegGuests"]
    dinner_kids914_total = dinner_guest_counts["dinnerKids914VegGuests"] + dinner_guest_counts["dinnerKids914NonVegGuests"]
    dinner_kids68_total = dinner_guest_counts["dinnerKids68VegGuests"] + dinner_guest_counts["dinnerKids68NonVegGuests"]
    dinner_amount = (
        dinner_adults_total * dinner_price_adults
        + dinner_kids914_total * dinner_price_kids914
        + dinner_kids68_total * dinner_price_kids68
        # kidsUnder6 is never priced — a headcount only.
    )
    total_amount = priced["upgradeAmount"] + dinner_amount
    total_dinner_guests = sum(dinner_guest_counts.values())

    # Reserve capacity zone by zone — the request record is only written
    # once every zone is actually held, so a failed reservation never leaves
    # an orphaned partial hold on the zones that DID succeed.
    reserved = []
    try:
        for zone in priced["zoneBreakdown"]:
            _adjust_tier_seats(event_type, zone["tierId"], zone["count"])
            reserved.append(zone)
    except RuntimeError:
        for zone in reserved:
            try:
                _adjust_tier_seats(event_type, zone["tierId"], -zone["count"])
            except Exception:
                pass
        raise

    now = now_iso()
    upgrade = {
        "upgradeId": str(uuid.uuid4()),
        "eventType": event_type,
        "registrationId": registration_id,
        "email": email,
        "name": name,
        "zoneBreakdown": priced["zoneBreakdown"],
        "seatCount": priced["seatCount"],
        "tierName": priced["tierSummary"],
        "upgradeAmount": priced["upgradeAmount"],
        "totalAmount": total_amount,
        "status": "pending",
        "paymentMethod": payment_method or "",
        **dinner_guest_counts,
        # Snapshot of the Celebrity Dinner plugin's per-category price at
        # request time, so a later admin change to it doesn't rewrite what
        # this registrant was actually charged. No dinnerPriceKidsUnder6 —
        # that category is never priced.
        "dinnerPriceAdults": dinner_price_adults if total_dinner_guests else 0,
        "dinnerPriceKids914": dinner_price_kids914 if total_dinner_guests else 0,
        "dinnerPriceKids68": dinner_price_kids68 if total_dinner_guests else 0,
        "dinnerAmount": dinner_amount,
        "requestedAt": now,
        "updatedAt": now,
    }

    try:
        dynamodb.Table(SEAT_UPGRADES_TABLE).put_item(Item=upgrade)
    except ClientError as e:
        # Release every zone we just reserved so a write failure never
        # strands seats as sold.
        for zone in priced["zoneBreakdown"]:
            try:
                _adjust_tier_seats(event_type, zone["tierId"], -zone["count"])
            except Exception:
                pass
        raise RuntimeError(f"Error creating zone upgrade request: {e}")

    return upgrade


def update_upgrade_status(upgrade_id: str, status: str) -> dict:
    """Admin transition: pending -> paid, or either -> cancelled.

    Cancelling releases the tier capacity the request was holding.

    Conditioned on the status still being `prior_status` (the value this
    same call just read) — without it, two concurrent callers (e.g. a
    registrant double-cancelling a paid request from two tabs) could both
    read "paid" before either write lands, both transition to
    "refund_requested", and both go on to call the payment provider's
    refund API for the same upgrade. The second of those calls would try to
    refund an amount no longer available on an already-refunded payment
    (Square: REFUND_AMOUNT_INVALID) — see attempt_refund in
    seat_upgrade/handler.py. The loser here gets a clear "status just
    changed" error instead of racing ahead into a second refund attempt.
    """
    if status not in VALID_UPGRADE_STATUSES:
        raise ValueError(f"Unknown status: {status!r}")

    existing = get_upgrade(upgrade_id)
    if not existing:
        raise ValueError("Zone upgrade request not found")

    prior_status = existing.get("status", "pending")
    if prior_status == status:
        return existing

    try:
        table = dynamodb.Table(SEAT_UPGRADES_TABLE)
        response = table.update_item(
            Key={"upgradeId": upgrade_id},
            UpdateExpression="SET #status = :status, updatedAt = :now",
            ConditionExpression=Attr("status").eq(prior_status),
            ExpressionAttributeNames={"#status": "status"},
            ExpressionAttributeValues={":status": status, ":now": now_iso()},
            ReturnValues="ALL_NEW",
        )
        updated = response.get("Attributes", {})
    except ClientError as e:
        if _is_conditional_check_failed(e):
            raise ValueError("This request's status just changed — reload and try again.")
        raise RuntimeError(f"Error updating zone upgrade status: {e}")

    # A cancellation frees the seats it was holding, whichever state it moved
    # from (pending or paid) — across every zone this purchase touched.
    if status == "cancelled" and prior_status in ACTIVE_UPGRADE_STATUSES:
        for zone in existing.get("zoneBreakdown", []):
            try:
                _adjust_tier_seats(existing["eventType"], zone["tierId"], -int(zone.get("count", 0) or 0))
            except Exception:
                pass

    return updated


def cancel_upgrade_request(upgrade_id: str, *, registration_id: str) -> dict:
    """Self-service cancel: only the owning registration may act, and only
    while the request is still active.

    A pending (unpaid) request is cancelled outright — nothing to refund.
    A paid request instead moves to "refund_requested": the caller (see
    seat_upgrade/handler.py's handle_delete) is responsible for attempting a
    provider refund and notifying everyone; an admin still has to finalize it
    to "cancelled" once the money is actually back with the registrant.
    """
    existing = get_upgrade(upgrade_id)
    if not existing:
        raise ValueError("Zone upgrade request not found")
    if existing.get("registrationId") != registration_id:
        raise PermissionError("You can only cancel your own zone upgrade requests")
    status = existing.get("status")
    if status == "pending":
        return update_upgrade_status(upgrade_id, "cancelled")
    if status == "paid":
        return update_upgrade_status(upgrade_id, "refund_requested")
    raise ValueError("This zone upgrade request can no longer be cancelled here")


# ── Online payment (PayPay / Square hosted checkout) ────────────────────────
#
# `status` stays the admin-facing pending/paid/cancelled tri-state (bank
# transfer still ends there via an admin's "Mark Paid"). These fields track
# the online-checkout attempt underneath it without changing that contract.

PAYMENT_PROVIDERS = {"paypay", "square"}


def record_checkout(upgrade_id: str, *, provider: str, provider_payment_id: str, checkout_url: str) -> dict:
    """Stamp a request with the checkout session just created for it.

    Only allowed while still pending — once paid or cancelled there's nothing
    left to check out. Re-callable (e.g. the registrant abandons PayPay and
    retries with Square): the latest attempt simply overwrites the fields, and
    a webhook or status check always uses `providerPaymentId` fresh off the
    record it's updating, never a value the caller passed in.
    """
    if provider not in PAYMENT_PROVIDERS:
        raise ValueError(f"Unknown payment provider: {provider!r}")

    existing = get_upgrade(upgrade_id)
    if not existing:
        raise ValueError("Zone upgrade request not found")
    if existing.get("status") != "pending":
        raise ValueError("Only a pending zone upgrade request can start a checkout")

    try:
        table = dynamodb.Table(SEAT_UPGRADES_TABLE)
        response = table.update_item(
            Key={"upgradeId": upgrade_id},
            UpdateExpression=(
                "SET paymentProvider = :provider, providerPaymentId = :ppid, "
                "checkoutUrl = :url, checkoutCreatedAt = :now, updatedAt = :now"
            ),
            ExpressionAttributeValues={
                ":provider": provider,
                ":ppid": provider_payment_id,
                ":url": checkout_url,
                ":now": now_iso(),
            },
            ReturnValues="ALL_NEW",
        )
        return response.get("Attributes", {})
    except ClientError as e:
        raise RuntimeError(f"Error recording checkout: {e}")


def get_upgrade_by_provider_payment_id(provider_payment_id: str) -> Optional[dict]:
    if not SEAT_UPGRADES_TABLE or not provider_payment_id:
        return None
    try:
        table = dynamodb.Table(SEAT_UPGRADES_TABLE)
        response = table.query(
            IndexName="ProviderPaymentIdIndex",
            KeyConditionExpression=Key("providerPaymentId").eq(provider_payment_id),
            Limit=1,
        )
        items = response.get("Items", [])
        return items[0] if items else None
    except ClientError as e:
        raise RuntimeError(f"Error looking up zone upgrade by provider payment id: {e}")


def mark_paid_from_provider(provider_payment_id: str, *, payment_reference: str = "") -> tuple:
    """Idempotent webhook/return-page transition: pending -> paid.

    Returns (record, was_newly_paid). `record` is the (possibly already-paid)
    upgrade, or None if no request matches `provider_payment_id`.
    `was_newly_paid` is True only for the call that actually performed the
    pending->paid transition — callers use that to decide whether to fire a
    "payment received" email, so a webhook that fires twice, or a webhook
    racing the return-page reconcile call, only ever sends it once.
    """
    existing = get_upgrade_by_provider_payment_id(provider_payment_id)
    if not existing:
        return None, False
    if existing.get("status") == "paid":
        return existing, False  # already processed — most likely a duplicate webhook delivery

    updated = update_upgrade_status(existing["upgradeId"], "paid")
    if payment_reference:
        try:
            table = dynamodb.Table(SEAT_UPGRADES_TABLE)
            response = table.update_item(
                Key={"upgradeId": existing["upgradeId"]},
                UpdateExpression="SET paymentReference = :ref",
                ExpressionAttributeValues={":ref": payment_reference},
                ReturnValues="ALL_NEW",
            )
            updated = response.get("Attributes", updated)
        except ClientError:
            pass  # the status transition already succeeded; the reference is best-effort metadata
    return updated, True


def record_refund_attempt(
    upgrade_id: str, *, refund_status: str, refund_reference: str = "", refund_error: str = ""
) -> dict:
    """Stamp a "refund_requested" record with the outcome of attempting it
    against the payment provider (or a note that it needs manual handling).

    `refund_error` is the provider's own error detail when a
    provider_failed attempt raises (e.g. Square's API error body) — admin-
    only diagnostic text, shown in the admin dashboard's Purchase Requests
    table so a failure is actionable without pulling CloudWatch logs. Never
    surfaced to the registrant.

    Purely informational bookkeeping for the admin dashboard — it never
    changes `status` itself; an admin still finalizes the request to
    "cancelled" once the refund is actually confirmed (see
    cancel_upgrade_request's docstring for why capacity stays held until then).
    """
    if not SEAT_UPGRADES_TABLE:
        raise RuntimeError("Zone upgrades table not configured")
    updates = ["refundStatus = :status", "refundRequestedAt = if_not_exists(refundRequestedAt, :now)", "updatedAt = :now"]
    values = {":status": refund_status, ":now": now_iso()}
    if refund_reference:
        updates.append("refundReference = :ref")
        values[":ref"] = refund_reference
    if refund_error:
        updates.append("refundError = :err")
        values[":err"] = refund_error[:500]
    try:
        table = dynamodb.Table(SEAT_UPGRADES_TABLE)
        response = table.update_item(
            Key={"upgradeId": upgrade_id},
            UpdateExpression="SET " + ", ".join(updates),
            ExpressionAttributeValues=values,
            ReturnValues="ALL_NEW",
        )
        return response.get("Attributes", {})
    except ClientError as e:
        raise RuntimeError(f"Error recording refund attempt: {e}")


# ── Email template overrides ─────────────────────────────────────────────────
#
# Global to the plugin, not per-event: one row per EMAIL_TEMPLATE_KEYS entry,
# read by seat_upgrade_emailer.py's own copy of this table (a separate
# boto3 read, not a call into this module — see that file's docstring for
# why the copied-module convention keeps them independent) and merged onto
# its DEFAULT_TEMPLATES. A key with no row here just uses the default.


def get_email_template_override(template_key: str) -> Optional[dict]:
    if not SEAT_UPGRADE_EMAIL_TEMPLATES_TABLE:
        return None
    try:
        return dynamodb.Table(SEAT_UPGRADE_EMAIL_TEMPLATES_TABLE).get_item(
            Key={"templateKey": template_key}
        ).get("Item")
    except ClientError as e:
        raise RuntimeError(f"Error fetching email template: {e}")


def list_email_template_overrides() -> dict:
    """{templateKey: item} for whichever templates have a saved override."""
    if not SEAT_UPGRADE_EMAIL_TEMPLATES_TABLE:
        return {}
    try:
        response = dynamodb.Table(SEAT_UPGRADE_EMAIL_TEMPLATES_TABLE).scan()
        return {
            item["templateKey"]: item
            for item in response.get("Items", [])
            if item.get("templateKey") in EMAIL_TEMPLATE_KEYS
        }
    except ClientError as e:
        raise RuntimeError(f"Error listing email templates: {e}")


def save_email_template_override(template_key: str, fields: dict) -> dict:
    """Merge `fields` (a subset of EMAIL_TEMPLATE_FIELDS) into the override
    row for `template_key`, creating it if this is the first customization."""
    if template_key not in EMAIL_TEMPLATE_KEYS:
        raise ValueError(f"Unknown template key: {template_key!r}")
    if not SEAT_UPGRADE_EMAIL_TEMPLATES_TABLE:
        raise RuntimeError("Zone upgrade email templates table not configured")

    updates = []
    names = {}
    values = {}
    for field, value in fields.items():
        if field not in EMAIL_TEMPLATE_FIELDS:
            continue
        alias = f"#{field}"
        updates.append(f"{alias} = :{field}")
        names[alias] = field
        values[f":{field}"] = value

    if not updates:
        return get_email_template_override(template_key) or {}

    updates.append("updatedAt = :now")
    values[":now"] = now_iso()

    try:
        table = dynamodb.Table(SEAT_UPGRADE_EMAIL_TEMPLATES_TABLE)
        response = table.update_item(
            Key={"templateKey": template_key},
            UpdateExpression="SET " + ", ".join(updates),
            ExpressionAttributeNames=names,
            ExpressionAttributeValues=values,
            ReturnValues="ALL_NEW",
        )
        return response.get("Attributes", {})
    except ClientError as e:
        raise RuntimeError(f"Error saving email template: {e}")


def reset_email_template_override(template_key: str) -> None:
    """Delete the override row so the template falls back to its built-in default."""
    if template_key not in EMAIL_TEMPLATE_KEYS:
        raise ValueError(f"Unknown template key: {template_key!r}")
    if not SEAT_UPGRADE_EMAIL_TEMPLATES_TABLE:
        raise RuntimeError("Zone upgrade email templates table not configured")
    try:
        dynamodb.Table(SEAT_UPGRADE_EMAIL_TEMPLATES_TABLE).delete_item(Key={"templateKey": template_key})
    except ClientError as e:
        raise RuntimeError(f"Error resetting email template: {e}")
