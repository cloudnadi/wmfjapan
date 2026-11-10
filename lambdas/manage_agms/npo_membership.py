"""
Shared NPO-membership period logic.

Membership is stored as dated transactions in the npo_membership table, keyed by
(memberid, transaction_no). A member is "valid" when any of their transactions'
[membDateFrom, membDateTo] range covers a reference date.

This module is duplicated verbatim across the register / membership /
manage_registrations Lambdas because Lambda packages are per-directory. Keep the
copies in sync.
"""

import re
from datetime import date, datetime, timezone


def _to_date(value):
    """Parse a CSV/ISO date-ish string into a date; None if unparseable."""
    if value is None:
        return None
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if isinstance(value, datetime):
        return value.date()
    text = str(value).strip()
    if not text:
        return None
    # Accept "YYYY-MM-DD HH:MM:SS", ISO 8601, or plain "YYYY-MM-DD".
    text = text.replace("T", " ")
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            return datetime.strptime(text[: len(fmt) + 2].strip(), fmt).date()
        except ValueError:
            continue
    # Last resort: take the first 10 chars as YYYY-MM-DD.
    try:
        return datetime.strptime(text[:10], "%Y-%m-%d").date()
    except ValueError:
        return None


def year_span(year_str):
    """Turn a membership-year code into its November-to-October date range.

    Accepts deployed ``YYYY-YY`` settings and legacy ``npoYearYY`` values.
    Returns (None, None) if the code can't be parsed.
    """
    value = str(year_str or "").strip()
    match = re.fullmatch(r"(\d{4})-\d{2}", value)
    if match:
        start_year = int(match.group(1))
    else:
        match = re.fullmatch(r"npoYear(\d{2})", value)
        if not match:
            return None, None
        start_year = 2000 + int(match.group(1))
    return date(start_year, 11, 1), date(start_year + 1, 10, 31)


def today(as_of=None):
    """Reference 'today' as a date; honors an explicit override string."""
    parsed = _to_date(as_of) if as_of else None
    return parsed or datetime.now(timezone.utc).date()


def _period_covers(record, ref_date):
    start = _to_date(record.get("membDateFrom"))
    end = _to_date(record.get("membDateTo"))
    if not start or not end:
        return False
    return start <= ref_date <= end


def evaluate(records, *, as_of=None, renewal_year="2026-27"):
    """Summarize a member's transactions against reference dates.

    Args:
        records: iterable of npo_membership transaction dicts for one member.
        as_of: optional reference date for the "valid now" check.
        renewal_year: membership-year code the join/renew campaign enrolls into.

    Returns a dict:
        validNow:       any period covers today
        coversRenewal:  any period covers the renewal year (already renewed)
        latestTo:       ISO date string of the furthest membDateTo (or "")
    """
    ref = today(as_of)
    renewal_start, _ = year_span(renewal_year)

    valid_now = False
    covers_renewal = False
    latest_to = None

    for record in records or []:
        if _period_covers(record, ref):
            valid_now = True
        if renewal_start and _period_covers(record, renewal_start):
            covers_renewal = True
        end = _to_date(record.get("membDateTo"))
        if end and (latest_to is None or end > latest_to):
            latest_to = end

    return {
        "validNow": valid_now,
        "coversRenewal": covers_renewal,
        "latestTo": latest_to.isoformat() if latest_to else "",
    }
