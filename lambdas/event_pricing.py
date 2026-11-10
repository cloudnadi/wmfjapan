"""Pricing, access and availability rules for event-platform registrations (pure Python).

The server is the only authority on prices: the browser sends ticket quantities,
never amounts. All money is whole JPY, all times are timezone-aware.
"""

from typing import Optional

import event_model

MAX_TICKETS_PER_REGISTRATION = 50

WINDOW_NOT_OPEN = "not_open"
WINDOW_OPEN = "open"
WINDOW_CLOSED = "closed"


def registration_window(event: dict, now) -> str:
    """open | not_open | closed, from the registration window, the event status and the event's end."""
    if event.get("status") not in ("published", "unlisted"):
        return WINDOW_CLOSED
    opens = event_model.parse_iso(event.get("registrationOpensAt"))
    closes = event_model.parse_iso(event.get("registrationClosesAt"))
    ends = event_model.parse_iso(event.get("endsAt")) or event_model.parse_iso(event.get("startsAt"))
    if opens and now < opens:
        return WINDOW_NOT_OPEN
    if closes and now > closes:
        return WINDOW_CLOSED
    if not closes and ends and now > ends:
        return WINDOW_CLOSED
    return WINDOW_OPEN


def is_publicly_visible(event: dict) -> bool:
    return event.get("status") in ("published", "unlisted", "closed")


def is_listed(event: dict) -> bool:
    return event.get("status") in ("published", "closed")


def access_decision(event: dict, member: Optional[dict]) -> dict:
    """May this visitor register? member is None for guests, else the resolved membership context.

    Returns {"allowed": bool, "reason": str, "renewUrl": bool}. Reasons: ok, login_required,
    members_only, renewal_required.
    """
    access = event.get("access") or {}
    logged_in = bool(member and member.get("loggedIn"))
    if access.get("membersOnly"):
        if not logged_in:
            return {"allowed": False, "reason": "login_required", "renewUrl": False}
        summary = member.get("summary") or {}
        if not summary.get("validNow"):
            return {"allowed": False, "reason": "members_only", "renewUrl": True}
        if access.get("requireRenewalCover", True) and not summary.get("coversRenewal"):
            return {"allowed": False, "reason": "renewal_required", "renewUrl": True}
        return {"allowed": True, "reason": "ok", "renewUrl": False}
    if not logged_in and not access.get("guestsAllowed", True):
        return {"allowed": False, "reason": "login_required", "renewUrl": False}
    return {"allowed": True, "reason": "ok", "renewUrl": False}


def is_member_price_eligible(member: Optional[dict]) -> bool:
    """Member pricing needs an NPO membership that is valid now (same rule as the legacy flow)."""
    return bool(member and member.get("loggedIn") and (member.get("summary") or {}).get("validNow"))


def unit_price(ticket: dict, member_eligible: bool, now) -> tuple:
    """(price_jpy, early_bird_applied). Early-bird pricing is for NPO members only."""
    if not member_eligible:
        return int(ticket["nonMemberPriceJpy"]), False
    price = int(ticket["memberPriceJpy"])
    early = ticket.get("earlyBirdMemberPriceJpy")
    cutoff = event_model.parse_iso(ticket.get("earlyBirdCutoff"))
    if early is not None and cutoff and now < cutoff and int(early) < price:
        return int(early), True
    return price, False


def quote(tickets: list, selection, member_eligible: bool, now) -> tuple:
    """Price a ticket selection {ticketTypeId: quantity}.

    Returns (lines, total_jpy, errors). `errors` maps "tickets" or a ticketTypeId to a message.
    """
    errors: dict = {}
    if not isinstance(selection, dict) or not selection:
        return [], 0, {"tickets": "Choose at least one ticket"}
    by_id = {t["ticketTypeId"]: t for t in tickets}
    lines, total_qty = [], 0
    for ticket_id, qty in selection.items():
        ticket = by_id.get(ticket_id)
        if ticket is None or ticket.get("active") is False:
            errors[str(ticket_id)] = "This ticket type is not available"
            continue
        if not isinstance(qty, int) or isinstance(qty, bool) or qty < 0:
            errors[ticket_id] = "Quantity must be a whole number"
            continue
        if qty == 0:
            continue
        cap = ticket.get("perPersonCap")
        if cap is not None and qty > int(cap):
            errors[ticket_id] = f"You can book at most {int(cap)} of this ticket"
            continue
        price, early = unit_price(ticket, member_eligible, now)
        total_qty += qty
        lines.append({"ticketTypeId": ticket_id, "name": ticket["name"], "qty": qty, "unitJpy": price,
                      "subtotalJpy": price * qty, "earlyBird": early})
    if not lines and not errors:
        errors["tickets"] = "Choose at least one ticket"
    if total_qty > MAX_TICKETS_PER_REGISTRATION:
        errors["tickets"] = f"At most {MAX_TICKETS_PER_REGISTRATION} tickets per registration"
    if errors:
        return [], 0, errors
    lines.sort(key=lambda l: (by_id[l["ticketTypeId"]].get("sortOrder", 0), l["name"]))
    return lines, sum(l["subtotalJpy"] for l in lines), {}


def seat_ops(event: dict, lines: list) -> list:
    """Capacity counters a selection consumes: [(counter_key, amount, limit_or_None)]."""
    event_id = event["eventId"]
    total = sum(l["qty"] for l in lines)
    ops = [(f"EVENT#{event_id}", total, int(event["capacity"]) if event.get("capacity") else None)]
    for line in lines:
        ops.append((f"TICKET#{event_id}#{line['ticketTypeId']}", line["qty"], line.get("limit")))
    return ops


def attach_limits(lines: list, tickets: list) -> list:
    by_id = {t["ticketTypeId"]: t for t in tickets}
    out = []
    for line in lines:
        limit = by_id[line["ticketTypeId"]].get("quantityLimit")
        out.append({**line, "limit": int(limit) if limit is not None else None})
    return out


def availability(event: dict, tickets: list, counters: dict) -> dict:
    """Remaining seats (None = unlimited) for the event and per ticket type, given {counterKey: sold}."""
    event_id = event["eventId"]
    cap = int(event["capacity"]) if event.get("capacity") else None
    sold = int(counters.get(f"EVENT#{event_id}", 0))
    per_ticket = {}
    for t in tickets:
        limit = t.get("quantityLimit")
        used = int(counters.get(f"TICKET#{event_id}#{t['ticketTypeId']}", 0))
        remaining = None if limit is None else max(0, int(limit) - used)
        if cap is not None:
            remaining = max(0, cap - sold) if remaining is None else min(remaining, max(0, cap - sold))
        per_ticket[t["ticketTypeId"]] = remaining
    return {"eventRemaining": None if cap is None else max(0, cap - sold), "tickets": per_ticket,
            "full": cap is not None and sold >= cap or (bool(per_ticket) and all(v == 0 for v in per_ticket.values()))}
