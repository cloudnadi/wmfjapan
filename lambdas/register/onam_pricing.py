"""
Server-authoritative pricing for the Onam Celebration 2026 registration.

The SPA computes and displays a total, but the amount owed is recomputed here
from trusted inputs (option, attendee counts, NPO discount tier, join/renew
choice) so a tampered client payload can never change what's recorded. The base
price table below MUST stay in sync with the OPTIONS constant in the frontend
(frontend/src/OnamRegistration.jsx).

The NPO discount is decided PER ADULT: every adult in the party is priced by
their OWN membership tier (the registrant plus each additional adult, verified
by email against the members table). Adults sharing a discount rate are grouped
into one line item.

Business rules (NPO discount, adults only — kids always pay the base rate,
kids under 6 are free):

  - Not an NPO member (and not joining): no discount.
  - Active member who does NOT renew:
      * Onam Only               → 10% off adults
      * Onam + Celebrity Event  → 10% off adults (combo includes Onam)
      * Celebrity Event Only    → no discount
  - Member who RENEWS, or non-member who JOINS, for 2026-27:
      * Onam Only               → 10% off adults
      * Celebrity Event Only    → no discount
      * Onam + Celebrity Event  → 20% off adults (10% + additional 10%, applied separately to each adult)
  - Joining or renewing NPO membership adds a one-time ¥1000 fee.

These three states map to the discount tiers "member" / "full" / "none".
"""

import re

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

# One-time NPO membership join/renewal fee (JPY).
NPO_FEE = 1000

# Base (non-member) unit prices in JPY: {option: {category: price}}.
PRICES = {
    "onam": {
        "adult": 2500,
        "kids914": 1500,
        "kids68": 1000,
        "kidsUnder6": 0,
    },
    "event": {
        "adult": 3000,
        "kids914": 2000,
        "kids68": 2000,
        "kidsUnder6": 0,
    },
    "combo": {
        "adult": 4950,
        "kids914": 3150,
        "kids68": 2700,
        "kidsUnder6": 0,
    },
}

# Adults-only discount percentage by (tier, option). Kids never get a discount.
DISCOUNT_PCT = {
    "none":   {"onam": 0,  "event": 0,  "combo": 0},
    "member": {"onam": 10, "event": 0,  "combo": 10},
    "full":   {"onam": 10, "event": 0, "combo": 10},
}

VALID_TIERS = set(DISCOUNT_PCT)

OPTION_LABELS = {
    "onam": "Onam Only",
    "event": "Celebrity Event Only",
    "combo": "Onam + Celebrity Event",
}

# Map incoming count keys -> internal category keys.
CATEGORY_COUNT_FIELDS = {
    "adult": "adults",
    "kids914": "kids914",
    "kids68": "kids68",
    "kidsUnder6": "kidsUnder6",
}

CATEGORY_LABELS = {
    "adult": "Adults",
    "kids914": "Kids (9–14)",
    "kids68": "Kids (6–8)",
    "kidsUnder6": "Kids (under 6)",
}


def _int(value, minimum=0):
    """Coerce to a non-negative int; None on invalid input."""
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed >= minimum else None


def resolve_discount_tier(*, valid_now, covers_renewal, do_join_or_renew):
    """Map membership state to a discount tier.

    Args:
        valid_now: True if the member has an NPO period covering today.
        covers_renewal: True if an NPO period already covers the 2026-27 span
            (i.e. they've effectively already renewed/joined for the target year).
        do_join_or_renew: True if the user is joining/renewing NPO now.

    Returns "full" | "member" | "none".
    """
    if covers_renewal or do_join_or_renew:
        return "full"
    if valid_now:
        return "member"
    return "none"


def compute_order(option, counts, *, discount_tier="none", adult_tiers=None, apply_npo_fee=False):
    """Recompute the Onam order from trusted inputs.

    The NPO discount is decided PER ADULT: each adult's own membership tier
    determines their percentage off (kids always pay the base rate). Adults that
    share a discount percentage are grouped into a single line item.

    Args:
        option: "onam" | "event" | "combo"
        counts: dict with keys adults/kids914/kids68/kidsUnder6 (any missing = 0)
        discount_tier: registrant's tier ("none" | "member" | "full"). Recorded
            as the order's headline pricingTier, and used as the fallback tier
            for every adult when `adult_tiers` is not supplied.
        adult_tiers: optional list of per-adult tiers, one entry per adult (the
            first is the registrant). Any missing entries fall back to
            `discount_tier`. Extra entries beyond the adult count are ignored.
        apply_npo_fee: True to add the one-time ¥1000 join/renew fee

    Returns:
        (result, error). `result` is a dict with the computed line items and
        totals; `error` is a message string if inputs are invalid (then result
        is None).
    """
    if option not in PRICES:
        return None, f"Unknown registration option: {option!r}"
    if discount_tier not in VALID_TIERS:
        return None, f"Unknown discount tier: {discount_tier!r}"

    counts = counts or {}

    adult_qty = _int(counts.get(CATEGORY_COUNT_FIELDS["adult"], 0))
    if adult_qty is None:
        return None, "Attendee counts must be whole numbers 0 or greater"

    # Resolve each adult's tier, defaulting to the registrant tier, and validate.
    resolved_tiers = list(adult_tiers or [])
    per_adult_tiers = [
        (resolved_tiers[i] if i < len(resolved_tiers) else discount_tier)
        for i in range(adult_qty)
    ]
    for tier in per_adult_tiers:
        if tier not in VALID_TIERS:
            return None, f"Unknown discount tier: {tier!r}"

    line_items = []
    tickets_total = 0
    discount = 0
    total_attendees = 0

    # ── Adults: group by discount percentage so each rate is its own line. ──
    normal_adult = PRICES[option]["adult"]
    adults_by_pct = {}
    for tier in per_adult_tiers:
        pct = DISCOUNT_PCT[tier][option]
        adults_by_pct[pct] = adults_by_pct.get(pct, 0) + 1

    # Highest discount first for a stable, friendly ordering.
    for pct in sorted(adults_by_pct, reverse=True):
        qty = adults_by_pct[pct]
        unit = round(normal_adult * (100 - pct) / 100)
        line_total = unit * qty
        tickets_total += line_total
        discount += (normal_adult - unit) * qty
        total_attendees += qty
        label = f"{CATEGORY_LABELS['adult']} (NPO −{pct}%)" if pct else CATEGORY_LABELS["adult"]
        line_items.append({
            "category": "adult",
            "label": label,
            "quantity": qty,
            "unitPrice": unit,
            "normalUnitPrice": normal_adult,
            "discountPercent": pct,
            "total": line_total,
        })

    # ── Kids: never discounted. ──
    for category, count_field in CATEGORY_COUNT_FIELDS.items():
        if category == "adult":
            continue
        qty = _int(counts.get(count_field, 0))
        if qty is None:
            return None, "Attendee counts must be whole numbers 0 or greater"
        if qty == 0:
            continue

        normal_unit = PRICES[option][category]
        line_total = normal_unit * qty
        tickets_total += line_total
        total_attendees += qty

        line_items.append({
            "category": category,
            "label": CATEGORY_LABELS[category],
            "quantity": qty,
            "unitPrice": normal_unit,
            "normalUnitPrice": normal_unit,
            "discountPercent": 0,
            "total": line_total,
        })

    npo_fee = NPO_FEE if apply_npo_fee else 0
    grand_total = tickets_total + npo_fee

    return {
        "option": option,
        "optionLabel": OPTION_LABELS[option],
        "pricingTier": discount_tier,
        "lineItems": line_items,
        "totalAttendees": total_attendees,
        "ticketsAmount": tickets_total,
        "discountApplied": discount,
        "npoFee": npo_fee,
        "totalAmount": grand_total,
    }, None


def resolve_additional_adults(raw_emails, adult_count, registrant_tier, *,
                              registrant_email="", is_member=None):
    """Validate the additional-adult emails and resolve each one's discount tier.

    Adults 2…N (the registrant is adult 1) each carry their own email. Every one
    must be a valid email, distinct from the registrant and from each other — a
    single membership can't be reused to discount two seats. On any violation we
    reject the whole request rather than silently dropping the discount, so the
    saved record always reflects exactly what the user entered.

    Args:
        raw_emails: the client-supplied list of additional adult emails.
        adult_count: total adults in the party (adult 1 is the registrant).
        registrant_tier: the registrant's resolved discount tier (adult 1).
        registrant_email: the registrant's (already-normalized) email; used to
            reject an additional adult reusing the primary address.
        is_member: optional callable(email) -> bool that reports whether an email
            is a current NPO member. When None, additional adults are never given
            a member discount (used where per-adult verification isn't wired up).

    Returns:
        (adult_tiers, additional_adults, error). On success `error` is None,
        `adult_tiers` is a per-adult tier list (adult 1 first), and
        `additional_adults` is a sanitized [{email, isMember}] list for the saved
        record. On failure the first two are None and `error` is
        {"error": msg, "fields": {...}} ready to return to the client.
    """
    expected = max(0, (adult_count or 0) - 1)
    emails = list(raw_emails or [])

    adult_tiers = [registrant_tier]
    additional_adults = []
    seen_emails = {registrant_email} if registrant_email else set()

    for index in range(expected):
        addl = str(emails[index] if index < len(emails) else "").strip().lower()
        field = f"adultEmail{index}"
        # Email is optional — blank means no membership check, standard price.
        if not addl:
            adult_tiers.append("none")
            additional_adults.append({"email": "", "isMember": False})
            continue
        if not EMAIL_RE.match(addl):
            return None, None, {
                "error": "Enter a valid email for each additional adult",
                "fields": {field: "Enter a valid email address"},
            }
        if addl in seen_emails:
            return None, None, {
                "error": "Each additional adult must use a different email address",
                "fields": {field: "This email is already used by another attendee"},
            }
        seen_emails.add(addl)
        member = bool(is_member(addl)) if is_member else False
        adult_tiers.append("member" if member else "none")
        additional_adults.append({"email": addl, "isMember": member})

    return adult_tiers, additional_adults, None
