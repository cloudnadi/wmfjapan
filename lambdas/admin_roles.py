"""Admin roles for the event platform, read from the Cognito authorizer claims.

Legacy admin Lambdas do not call this (any pool user is a full admin there);
the event-platform Lambdas do. Roles come from Cognito groups:

  Admins         -> super_admin   (everything, including destructive actions)
  EventManagers  -> event_manager (manage events/forms/registrations, not members or settings)

A pool user in neither group gets no access to event-platform routes.
"""

import re

SUPER_ADMIN = "super_admin"
EVENT_MANAGER = "event_manager"

GROUP_ROLES = {"Admins": SUPER_ADMIN, "EventManagers": EVENT_MANAGER}


def claims_of(event: dict) -> dict:
    return ((event.get("requestContext") or {}).get("authorizer") or {}).get("claims") or {}


def groups_of(event: dict) -> set:
    """REST API Cognito authorizers flatten the groups claim to "a", "[a b]" or "a,b"."""
    raw = claims_of(event).get("cognito:groups", "")
    if isinstance(raw, (list, tuple, set)):
        return {str(g).strip() for g in raw if str(g).strip()}
    return {g for g in re.split(r"[\s,\[\]]+", str(raw)) if g}


def roles_of(event: dict) -> set:
    return {GROUP_ROLES[g] for g in groups_of(event) if g in GROUP_ROLES}


def actor_of(event: dict) -> str:
    claims = claims_of(event)
    return str(claims.get("email") or claims.get("cognito:username") or claims.get("sub") or "unknown")


def has_role(event: dict, *allowed: str) -> bool:
    return bool(roles_of(event) & set(allowed))
