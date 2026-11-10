"""Per-event capabilities: which optional features an event offers, with safe defaults.

One small, pure module (no AWS) shared by the public API, the admin API and the tests.

Public contract (``event.capabilities`` in the public API) — every key is always present:

    guestCheckout     {enabled}    anyone may register without an account
    authentication    {required}   a signed-in account is needed to register (always ``not guestCheckout``)
    seatReservation   {enabled}    the registrant picks specific seats from the event's seat map
    zoneUpgrade       {enabled}    optional paid upgrades (e.g. a premium zone) bought per ticket
    entrySlots        {enabled}    the registrant books an arrival time slot with its own capacity

Sources of truth (nothing is duplicated in the frontend):

  * ``guestCheckout`` / ``authentication`` are *derived* from ``event.access.guestsAllowed`` (default: allowed),
    which the admin edits and ``event_pricing.access_decision`` enforces.
  * the three add-ons are switched on per event with ``capabilities.<name>.enabled`` and configured on the event
    itself (``slots``, ``upgrades``, ``seatMap``; see ``event_addons.py``). An add-on that is missing or not
    exactly ``true`` is OFF, so older events behave exactly as before.
"""

from typing import Any

ADDONS = ("seatReservation", "zoneUpgrade", "entrySlots")
ADDON_LABELS = {"seatReservation": "Seat reservation", "zoneUpgrade": "Zone upgrade", "entrySlots": "Entry slots"}
NATIVE_ADDONS_SUPPORTED = frozenset(ADDONS)


class FeatureDisabled(Exception):
    """A request used a feature this event does not offer."""

    def __init__(self, feature: str):
        super().__init__(f"{ADDON_LABELS.get(feature, feature)} is not available for this event")
        self.feature = feature


def addon_enabled(event: dict, name: str) -> bool:
    """Is this add-on on for this event? Anything but an explicit boolean true is off."""
    if name not in ADDONS:
        return False
    stored = (event or {}).get("capabilities")
    block = stored.get(name) if isinstance(stored, dict) else None
    return isinstance(block, dict) and block.get("enabled") is True


def guest_checkout_enabled(event: dict) -> bool:
    return ((event or {}).get("access") or {}).get("guestsAllowed", True) is not False


def normalize(event: dict) -> dict:
    """The full public capability object for an event (always every key)."""
    guests = guest_checkout_enabled(event)
    out = {"guestCheckout": {"enabled": guests}, "authentication": {"required": not guests}}
    for name in ADDONS:
        out[name] = {"enabled": addon_enabled(event, name)}
    return out


def require_addon(event: dict, name: str) -> None:
    """Server-side gate: raise FeatureDisabled unless the event offers this add-on."""
    if not addon_enabled(event, name):
        raise FeatureDisabled(name)


def requested_addons(body: Any) -> list:
    """Add-on keys a registration request tries to use (`addons: {name: {...}}`; null/false/{} = not used)."""
    raw = body.get("addons") if isinstance(body, dict) else None
    if raw in (None, False, {}, []):
        return []
    if not isinstance(raw, dict):
        return ["addons"]                                  # malformed: treated as an unknown add-on
    return [key for key, value in raw.items() if value not in (None, False, {}, [], 0, "")]


def reject_unavailable_addons(event: dict, body: Any) -> dict:
    """Field errors for add-ons the request uses but the event does not offer ({} when fine)."""
    errors = {}
    for key in requested_addons(body):
        if key not in ADDONS or not addon_enabled(event, key):
            errors[f"addons.{key}"] = f"{ADDON_LABELS.get(key, 'That option')} is not available for this event"
    return errors


def validate_input(raw: Any) -> dict:
    """Admin payload check for `capabilities`; returns {field: message}. (Config presence is checked in event_addons.)"""
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        return {"capabilities": "Must be an object"}
    errors = {}
    for key, value in raw.items():
        if key not in ADDONS:
            errors[f"capabilities.{key}"] = "Unknown capability (guest checkout is controlled by Access settings)"
        elif not isinstance(value, dict) or set(value) - {"enabled"} or not isinstance(value.get("enabled"), bool):
            errors[f"capabilities.{key}"] = "Use {\"enabled\": true|false}"
    return errors


def clean_input(raw: Any) -> dict:
    """Canonical stored form of a validated admin payload (unknown keys dropped)."""
    if not isinstance(raw, dict):
        return {}
    return {name: {"enabled": bool(raw[name]["enabled"])} for name in ADDONS
            if isinstance(raw.get(name), dict) and isinstance(raw[name].get("enabled"), bool)}
