"""Resolve a member's membership for event pricing and access (shared by the public and admin Lambdas)."""

import npo_membership


def membership_for_email(store, email: str, *, as_of: str, renewal_year: str) -> dict:
    """The member context used by event_registrations: identity plus an npo_membership summary.

    Same rule as the legacy flow: a member counts as valid only when the NPO flag is set AND a
    membership period covers the reference date.
    """
    email = str(email).strip().lower()
    record = store.get_member(email) or {}
    memberid = str(record.get("memberid", ""))
    transactions = store.get_membership_transactions(memberid) if memberid else []
    summary = npo_membership.evaluate(transactions, as_of=as_of, renewal_year=renewal_year)
    summary["validNow"] = bool(record.get("npoMembershipActive")) and summary["validNow"]
    return {"loggedIn": True, "email": email, "memberId": memberid, "name": record.get("fullname", ""),
            "summary": summary, "known": bool(record)}
