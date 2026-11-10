"""
AGM eligibility rules, shared by the membership Lambda (registration) and the
manage_agms Lambda (admin attendee list).

Only NPO members may attend the AGM — as adults (18+). Teens, children and
infants attend with a member and are counted, not checked. Membership is judged
on the AGM's own date, not today: the AGM on 15 November falls in the 2026-27
year that starts on 1 November, so a member whose 2025-26 membership lapsed on
31 October has to renew before they can attend.

A membership that has been requested but not paid yet ("pending") is accepted
provisionally; it's shown as such to the committee, who can confirm payment
before the meeting.
"""

from datetime import date, datetime, timezone

import npo_membership

# Statuses an adult attendee can have for a given AGM.
MEMBER = "member"            # an NPO membership period covers the AGM date
PENDING = "pending"          # renewal / joining requested, payment not confirmed yet
NOT_MEMBER = "not_member"    # a WMF Japan member, but no NPO membership for the AGM date
NOT_NK_MEMBER = "not_nk_member"  # not in the members list at all

ALLOWED = (MEMBER, PENDING)

# Age groups besides adults (labels and ranges as defined by the committee).
YOUNG_GROUPS = (
    ("teens", "Teenagers (13–17)"),
    ("kids", "School-age children (6–12)"),
    ("preschoolers", "Preschoolers (3–5)"),
    ("toddlers", "Toddlers (0–3)"),
)


def reference_date(agm_date: str) -> date:
    parsed = npo_membership._to_date(agm_date) if agm_date else None
    return parsed or datetime.now(timezone.utc).date()


def year_code_for(agm_date: str) -> str:
    """Membership-year code (Nov–Oct) that contains the AGM date, e.g. 2026-11-15 -> "2026-27"."""
    ref = reference_date(agm_date)
    start = ref.year if ref.month >= 11 else ref.year - 1
    return f"{start}-{(start + 1) % 100:02d}"


def status_from(member, transactions, agm_date: str, *, ordering: bool = False) -> str:
    """Status of one person for the AGM.

    member: their members-table item (None if they aren't in the list).
    transactions: their npo_membership items.
    ordering: they are being signed up for the AGM's membership year in the order
    being placed right now (so provisionally pending).
    """
    if not member:
        return NOT_NK_MEMBER
    ref = reference_date(agm_date)
    if any(npo_membership._period_covers(t, ref) for t in transactions or []):
        return MEMBER
    code = year_code_for(agm_date)
    if ordering or any(
        t.get("paymentStatus") == "pending" and t.get("membershipYear") == code for t in transactions or []
    ):
        return PENDING
    return NOT_MEMBER


MESSAGES = {
    NOT_NK_MEMBER: (
        "This email isn't registered as a WMF Japan member. Please ask them to become an NK member "
        "and an NPO member first — only NPO members can attend the AGM."
    ),
    NOT_MEMBER: (
        "This person isn't an NPO member for the AGM date. Only NPO members can attend the AGM — please "
        "ask them to join or renew first, then add them here."
    ),
}
