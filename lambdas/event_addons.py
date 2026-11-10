"""Native add-ons for platform events: entry slots, zone upgrades and seat reservation (pure Python).

All three are configured on the event item and switched on per event with ``capabilities.<name>.enabled``
(see event_capabilities.py). Every unit of capacity is an atomic counter in the same DynamoDB transaction
that creates the registration, so concurrent sign-ups cannot oversell a slot, an upgrade or a seat:

    entrySlots       event.slots     = [{slotId, name, startsAt?, endsAt?, capacity}]
                     registrant picks ONE slot; every ticket consumes one place in it   (counter SLOT#<event>#<slot>)
    zoneUpgrade      event.upgrades  = [{upgradeId, name, description?, priceJpy, limit?}]
                     registrant buys 0..n of each, at most one per ticket                (counter UPGRADE#<event>#<id>)
    seatReservation  event.seatMap   = {sections: [{sectionId, name, rows, seatsPerRow, extraPriceJpy}]}
                     registrant picks exactly one seat per ticket                        (counter SEAT#<event>#<seat>, limit 1)

Registration payload (``addons``) and the record stored on the registration:

    addons: {"entrySlots": {"slotId": "ab12cd34"},
             "zoneUpgrade": {"<upgradeId>": 2},
             "seatReservation": {"seats": ["main-A1", "main-A2"]}}

    registration.addons    = {"entrySlot": {...}, "upgrades": [...], "seats": [...]}
    registration.addonJpy  = whole-yen total added to the ticket total (the server prices everything)
    registration.addonOps  = [[counter_key, amount], ...] so cancel/expiry return exactly what was taken

Prices are never read from the request; unknown ids, duplicates and counts that do not match the tickets are errors.
"""

import re
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

import event_capabilities as caps

_JST = timezone(timedelta(hours=9))

MAX_SLOTS = 50
MAX_UPGRADES = 20
MAX_SEATS = 1000
MAX_SECTIONS = 20
ID_RE = re.compile(r"^[a-z0-9]{1,12}$")
ROW_LETTERS = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"

KEY_PREFIXES = ("SLOT#", "UPGRADE#", "SEAT#")


def is_addon_key(key: str) -> bool:
    return str(key).startswith(KEY_PREFIXES)


def _parse_iso(value: Any) -> Optional[datetime]:
    """ISO-8601 -> aware datetime (naive = JST); None if absent or invalid. (Same rule as _parse_iso.)"""
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=_JST)


def _int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _new_id() -> str:
    return uuid.uuid4().hex[:8]


# ── seat map geometry ─────────────────────────────────────────────────────────

def seat_ids(section: dict) -> list:
    """Every seat id of a section, row by row: <sectionId>-<row letter><number>, e.g. main-B7."""
    return [f"{section['sectionId']}-{ROW_LETTERS[r]}{n}" for r in range(int(section["rows"]))
            for n in range(1, int(section["seatsPerRow"]) + 1)]


def seat_index(event: dict) -> dict:
    """{seatId: section} for the whole map."""
    index = {}
    for section in (event.get("seatMap") or {}).get("sections", []):
        for seat in seat_ids(section):
            index[seat] = section
    return index


# ── admin configuration ───────────────────────────────────────────────────────

def validate_config(payload: dict) -> dict:
    """Validate slots / upgrades / seatMap and the rule that an enabled add-on has something to offer."""
    errors: dict = {}
    slots, upgrades, seat_map = payload.get("slots"), payload.get("upgrades"), payload.get("seatMap")

    if slots is not None:
        if not isinstance(slots, list) or len(slots) > MAX_SLOTS:
            errors["slots"] = f"Up to {MAX_SLOTS} time slots"
        else:
            seen = set()
            for i, slot in enumerate(slots):
                where = f"slots[{i}]"
                if not isinstance(slot, dict) or not str(slot.get("name", "")).strip() or len(str(slot["name"])) > 80:
                    errors[where] = "Give each time slot a name (up to 80 characters)"
                    continue
                if not _int(slot.get("capacity")) or slot["capacity"] < 1:
                    errors[where] = "Capacity must be a whole number, 1 or greater"
                starts, ends = _parse_iso(slot.get("startsAt")), _parse_iso(slot.get("endsAt"))
                for key, parsed in (("startsAt", starts), ("endsAt", ends)):
                    if slot.get(key) and parsed is None:
                        errors[where] = "Use ISO date-times with a +09:00 offset"
                if starts and ends and ends < starts:
                    errors[where] = "A slot cannot end before it starts"
                sid = slot.get("slotId")
                if sid is not None and (not isinstance(sid, str) or not ID_RE.match(sid) or sid in seen):
                    errors[where] = "Invalid or duplicate slot id"
                seen.add(sid)

    if upgrades is not None:
        if not isinstance(upgrades, list) or len(upgrades) > MAX_UPGRADES:
            errors["upgrades"] = f"Up to {MAX_UPGRADES} upgrades"
        else:
            seen = set()
            for i, up in enumerate(upgrades):
                where = f"upgrades[{i}]"
                if not isinstance(up, dict) or not str(up.get("name", "")).strip() or len(str(up["name"])) > 80:
                    errors[where] = "Give each upgrade a name (up to 80 characters)"
                    continue
                if not _int(up.get("priceJpy")) or up["priceJpy"] < 0:
                    errors[where] = "Price must be whole yen, 0 or greater"
                if up.get("limit") is not None and (not _int(up["limit"]) or up["limit"] < 1):
                    errors[where] = "Limit must be a whole number, 1 or greater"
                if len(str(up.get("description", ""))) > 200:
                    errors[where] = "Keep the description under 200 characters"
                uid = up.get("upgradeId")
                if uid is not None and (not isinstance(uid, str) or not ID_RE.match(uid) or uid in seen):
                    errors[where] = "Invalid or duplicate upgrade id"
                seen.add(uid)

    if seat_map is not None:
        sections = seat_map.get("sections") if isinstance(seat_map, dict) else None
        if not isinstance(sections, list) or len(sections) > MAX_SECTIONS:
            errors["seatMap"] = f"Use {{\"sections\": [...]}} with up to {MAX_SECTIONS} sections"
        else:
            total, seen = 0, set()
            for i, sec in enumerate(sections):
                where = f"seatMap.sections[{i}]"
                if not isinstance(sec, dict) or not str(sec.get("name", "")).strip() or len(str(sec["name"])) > 60:
                    errors[where] = "Give each section a name (up to 60 characters)"
                    continue
                if not _int(sec.get("rows")) or not 1 <= sec["rows"] <= 26 or not _int(sec.get("seatsPerRow")) or not 1 <= sec["seatsPerRow"] <= 60:
                    errors[where] = "Use 1-26 rows and 1-60 seats per row"
                    continue
                if not _int(sec.get("extraPriceJpy", 0)) or sec.get("extraPriceJpy", 0) < 0:
                    errors[where] = "Extra price must be whole yen, 0 or greater"
                total += sec["rows"] * sec["seatsPerRow"]
                sid = sec.get("sectionId")
                if sid is not None and (not isinstance(sid, str) or not ID_RE.match(sid) or sid in seen):
                    errors[where] = "Invalid or duplicate section id"
                seen.add(sid)
            if total > MAX_SEATS:
                errors["seatMap"] = f"At most {MAX_SEATS} seats per event"

    # An enabled add-on must have something to offer.
    raw_caps = payload.get("capabilities") if isinstance(payload.get("capabilities"), dict) else {}
    enabled = {name: (raw_caps.get(name) or {}).get("enabled") is True if isinstance(raw_caps.get(name), dict) else False
               for name in caps.ADDONS}
    for name, present in (("entrySlots", bool(slots)), ("zoneUpgrade", bool(upgrades)),
                          ("seatReservation", bool((seat_map or {}).get("sections")) if isinstance(seat_map, dict) else False)):
        if enabled[name] and not present:
            what = {"entrySlots": "a time slot", "zoneUpgrade": "an upgrade", "seatReservation": "a seat section"}[name]
            errors[f"capabilities.{name}"] = f"Add {what} before switching {caps.ADDON_LABELS[name]} on"
    return errors


def clean_config(merged: dict) -> dict:
    """Sanitised slots / upgrades / seatMap (only the keys present), with ids assigned to new entries."""
    out: dict = {}
    if isinstance(merged.get("slots"), list):
        out["slots"] = [_drop_none({
            "slotId": s.get("slotId") or _new_id(), "name": str(s["name"]).strip(), "capacity": int(s["capacity"]),
            "startsAt": s.get("startsAt") or None, "endsAt": s.get("endsAt") or None}) for s in merged["slots"]]
    if isinstance(merged.get("upgrades"), list):
        out["upgrades"] = [_drop_none({
            "upgradeId": u.get("upgradeId") or _new_id(), "name": str(u["name"]).strip(),
            "description": str(u.get("description", "")).strip() or None, "priceJpy": int(u["priceJpy"]),
            "limit": int(u["limit"]) if u.get("limit") is not None else None}) for u in merged["upgrades"]]
    if isinstance(merged.get("seatMap"), dict):
        out["seatMap"] = {"sections": [{
            "sectionId": s.get("sectionId") or _new_id()[:6], "name": str(s["name"]).strip(), "rows": int(s["rows"]),
            "seatsPerRow": int(s["seatsPerRow"]), "extraPriceJpy": int(s.get("extraPriceJpy", 0))}
            for s in merged["seatMap"].get("sections", [])]}
    return out


def _drop_none(d: dict) -> dict:
    return {k: v for k, v in d.items() if v is not None}


# ── counters ──────────────────────────────────────────────────────────────────

def counter_keys(event: dict) -> list:
    """Every counter the public page needs to show remaining places for the enabled add-ons."""
    eid, keys = event["eventId"], []
    if caps.addon_enabled(event, "entrySlots"):
        keys += [f"SLOT#{eid}#{s['slotId']}" for s in event.get("slots") or []]
    if caps.addon_enabled(event, "zoneUpgrade"):
        keys += [f"UPGRADE#{eid}#{u['upgradeId']}" for u in event.get("upgrades") or []]
    if caps.addon_enabled(event, "seatReservation"):
        keys += [f"SEAT#{eid}#{seat}" for seat in seat_index(event)]
    return keys


def limit_for_key(event: dict, key: str) -> Optional[int]:
    """The capacity the event's configuration sets for an add-on counter (None = unlimited)."""
    eid = event["eventId"]
    kind, _, rest = key.partition("#")
    ident = rest[len(eid) + 1:] if rest.startswith(eid + "#") else ""
    if kind == "SLOT":
        slot = next((s for s in event.get("slots") or [] if s["slotId"] == ident), None)
        return int(slot["capacity"]) if slot else 0
    if kind == "UPGRADE":
        up = next((u for u in event.get("upgrades") or [] if u["upgradeId"] == ident), None)
        return (int(up["limit"]) if up and up.get("limit") is not None else None) if up else 0
    if kind == "SEAT":
        return 1 if ident in seat_index(event) else 0
    return None


def ops_for_record(event: dict, reg: dict) -> list:
    """[(key, amount, limit)] to take again the add-on places a stored registration needs (promotion, late payment)."""
    return [(k, int(n), limit_for_key(event, k)) for k, n in reg.get("addonOps", [])]


def release_ops(reg: dict) -> list:
    return [(k, int(n)) for k, n in reg.get("addonOps", [])]


# ── public view ───────────────────────────────────────────────────────────────

def public_view(event: dict, counters: dict) -> dict:
    """What the event page may show about each enabled add-on, with live remaining places."""
    eid, out = event["eventId"], {}
    if caps.addon_enabled(event, "entrySlots"):
        out["entrySlots"] = {"slots": [
            _drop_none({"slotId": s["slotId"], "name": s["name"], "startsAt": s.get("startsAt"), "endsAt": s.get("endsAt"),
                        "capacity": s["capacity"],
                        "remaining": max(0, int(s["capacity"]) - int(counters.get(f"SLOT#{eid}#{s['slotId']}", 0)))})
            for s in event.get("slots") or []]}
    if caps.addon_enabled(event, "zoneUpgrade"):
        rows = []
        for u in event.get("upgrades") or []:
            used = int(counters.get(f"UPGRADE#{eid}#{u['upgradeId']}", 0))
            rows.append({**_drop_none({"upgradeId": u["upgradeId"], "name": u["name"], "description": u.get("description"),
                                       "priceJpy": u["priceJpy"]}),
                         "remaining": None if u.get("limit") is None else max(0, int(u["limit"]) - used)})   # null = unlimited
        out["zoneUpgrade"] = {"upgrades": rows}
    if caps.addon_enabled(event, "seatReservation"):
        sections = (event.get("seatMap") or {}).get("sections", [])
        taken = [seat for seat in seat_index(event) if int(counters.get(f"SEAT#{eid}#{seat}", 0)) >= 1]
        out["seatReservation"] = {"sections": sections, "taken": taken}
    return out


# ── pricing a registration ────────────────────────────────────────────────────

def resolve(event: dict, body: dict, seats: int) -> tuple:
    """Check the requested add-ons against the event and the ticket count.

    Returns (record, addon_jpy, ops, errors): `record` is what to store on the registration, `ops` the
    [(counter_key, amount, limit)] to reserve with it. Disabled add-ons are rejected earlier
    (event_capabilities.reject_unavailable_addons); an enabled one that is required but missing is an error here.
    """
    raw = body.get("addons") if isinstance(body.get("addons"), dict) else {}
    eid, errors, ops, record, total = event["eventId"], {}, [], {}, 0

    if caps.addon_enabled(event, "entrySlots"):
        choice = raw.get("entrySlots") if isinstance(raw.get("entrySlots"), dict) else {}
        slot = next((s for s in event.get("slots") or [] if s["slotId"] == choice.get("slotId")), None)
        if slot is None:
            errors["addons.entrySlots"] = "Choose a time slot"
        else:
            ops.append((f"SLOT#{eid}#{slot['slotId']}", seats, int(slot["capacity"])))
            record["entrySlot"] = _drop_none({"slotId": slot["slotId"], "name": slot["name"],
                                             "startsAt": slot.get("startsAt"), "endsAt": slot.get("endsAt")})

    if caps.addon_enabled(event, "zoneUpgrade"):
        picked = raw.get("zoneUpgrade") if isinstance(raw.get("zoneUpgrade"), dict) else {}
        by_id = {u["upgradeId"]: u for u in event.get("upgrades") or []}
        lines, count = [], 0
        for uid, qty in picked.items():
            if uid not in by_id:
                errors["addons.zoneUpgrade"] = "That upgrade is not available"
            elif not _int(qty) or qty < 0:
                errors["addons.zoneUpgrade"] = "Upgrade quantities must be whole numbers"
            elif qty:
                up = by_id[uid]
                count += qty
                lines.append({"upgradeId": uid, "name": up["name"], "qty": qty, "unitJpy": int(up["priceJpy"]),
                              "subtotalJpy": int(up["priceJpy"]) * qty})
                ops.append((f"UPGRADE#{eid}#{uid}", qty, int(up["limit"]) if up.get("limit") is not None else None))
        if count > seats:
            errors["addons.zoneUpgrade"] = "You can upgrade at most one place per ticket"
        if lines:
            record["upgrades"] = lines
            total += sum(l["subtotalJpy"] for l in lines)

    if caps.addon_enabled(event, "seatReservation"):
        choice = raw.get("seatReservation") if isinstance(raw.get("seatReservation"), dict) else {}
        wanted = choice.get("seats") if isinstance(choice.get("seats"), list) else []
        index = seat_index(event)
        if len(wanted) != seats or len(set(wanted)) != len(wanted):
            errors["addons.seatReservation"] = f"Choose exactly {seats} different seat{'s' if seats != 1 else ''}"
        elif any(not isinstance(s, str) or s not in index for s in wanted):
            errors["addons.seatReservation"] = "One of the seats you chose does not exist"
        else:
            seats_rec = [{"seatId": s, "section": index[s]["name"], "extraJpy": int(index[s].get("extraPriceJpy", 0))} for s in wanted]
            record["seats"] = seats_rec
            total += sum(s["extraJpy"] for s in seats_rec)
            ops += [(f"SEAT#{eid}#{s}", 1, 1) for s in wanted]

    if errors:
        return {}, 0, [], errors
    return record, total, ops, {}


def describe(reg: dict) -> str:
    """One line for e-mails and exports: slot, upgrades, seats."""
    rec = reg.get("addons") or {}
    parts = []
    if rec.get("entrySlot"):
        parts.append(f"Time slot: {rec['entrySlot']['name']}")
    if rec.get("upgrades"):
        parts.append("Upgrades: " + ", ".join(f"{u['name']} × {u['qty']}" for u in rec["upgrades"]))
    if rec.get("seats"):
        parts.append("Seats: " + ", ".join(s["seatId"] for s in rec["seats"]))
    return "; ".join(parts)
