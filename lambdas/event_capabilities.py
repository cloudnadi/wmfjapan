"""Per-event capabilities: which optional features an event offers, with safe defaults.

One small, pure module (no AWS, no imports from the rest of the platform) so the public API, the admin
API, the legacy mirror and the tests all share a single definition.

Public contract (``event.capabilities`` in the public API) — every key is always present:

    guestCheckout     {enabled}    anyone may register without an account
    authentication    {required}   a signed-in account is needed to register (always ``not guestCheckout``)
    seatReservation   {enabled}    hall seat selection (legacy ``eventInfo.seatSelection.enabled``)
    zoneUpgrade       {enabled}    seating-zone upgrades (legacy ``eventInfo.seatUpgrade.enabled``)
    entrySlots        {enabled}    timed entry slots (legacy ``eventInfo.entrySlots.enabled``)
    celebrityDinner   {enabled}    the dinner add-on (legacy ``eventInfo.celebrityDinner.enabled``)

Sources of truth (nothing is duplicated into the frontend):

  * ``guestCheckout`` / ``authentication`` are *derived* from ``event.access`` (``membersOnly`` and
    ``guestsAllowed``), which the admin already edits and which ``event_pricing.access_decision``
    enforces. ``access.guestsAllowed`` missing means guests are allowed (the existing default).
  * the four add-ons are stored on the event as ``capabilities.<name>.enabled``. Events mirrored from
    the legacy ``events`` table get them from ``eventInfo`` (see ``from_legacy_event_info``).

Defaults for records that predate this module (documented in docs/WMF_EVENTS_SITE.md):

  * any add-on that is missing or not exactly ``true`` is OFF — the same convention the seat-selection,
    zone-upgrade and dinner plugins already use;
  * ``entrySlots`` on a legacy-sourced event is ON unless ``eventInfo.entrySlots.enabled`` is exactly
    ``false``, because entry slots were never switchable before and existing events rely on them.

Add-ons on platform-native events: the add-on fulfilment code (seat map, upgrades, slots, dinner)
works on the legacy ``events``/``registrations`` pipeline. Native registrations cannot book them yet,
so ``NATIVE_ADDONS_SUPPORTED`` is empty and the admin API refuses to enable an add-on on a native
event rather than showing a feature that does nothing. When native fulfilment ships, add the key here.
"""

from typing import Any, Optional

ADDONS = ("seatReservation", "zoneUpgrade", "entrySlots", "celebrityDinner")
LEGACY_INFO_KEY = {"seatReservation": "seatSelection", "zoneUpgrade": "seatUpgrade",
                   "entrySlots": "entrySlots", "celebrityDinner": "celebrityDinner"}
NATIVE_ADDONS_SUPPORTED: frozenset = frozenset()
_LEGACY_DEFAULT_ON = frozenset({"entrySlots"})

ADDON_LABELS = {"seatReservation": "Seat reservation", "zoneUpgrade": "Zone upgrade",
                "entrySlots": "Entry slots", "celebrityDinner": "Celebrity dinner"}


class FeatureDisabled(Exception):
    """A request used a feature this event does not offer."""

    def __init__(self, feature: str):
        super().__init__(f"{ADDON_LABELS.get(feature, feature)} is not available for this event")
        self.feature = feature


def _enabled_flag(block: Any) -> Optional[bool]:
    """True/False when the block carries a real boolean `enabled`, else None (= unspecified)."""
    if isinstance(block, dict) and isinstance(block.get("enabled"), bool):
        return block["enabled"]
    return None


def is_legacy_sourced(event: dict) -> bool:
    return (event or {}).get("source") == "legacy"


def from_legacy_event_info(event_info: Any) -> dict:
    """Stored form of the add-on switches for a legacy event (`capabilities` on the mirror)."""
    info = event_info if isinstance(event_info, dict) else {}
    caps = {}
    for name in ADDONS:
        flag = _enabled_flag(info.get(LEGACY_INFO_KEY[name]))
        if name in _LEGACY_DEFAULT_ON:
            caps[name] = {"enabled": flag is not False}
        else:
            caps[name] = {"enabled": flag is True}
    return caps


def addon_enabled(event: dict, name: str) -> bool:
    """Is this add-on on for this event? Missing/malformed data falls back to the documented defaults."""
    if name not in ADDONS:
        return False
    stored = (event or {}).get("capabilities")
    flag = _enabled_flag(stored.get(name)) if isinstance(stored, dict) else None
    if is_legacy_sourced(event or {}):
        if flag is not None:
            return flag
        return name in _LEGACY_DEFAULT_ON
    # Native events: only an explicit, supported `true` counts.
    return flag is True and name in NATIVE_ADDONS_SUPPORTED


def guest_checkout_enabled(event: dict) -> bool:
    access = (event or {}).get("access") or {}
    if access.get("membersOnly"):
        return False
    return access.get("guestsAllowed", True) is not False


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
    used = []
    for key, value in raw.items():
        if value in (None, False, {}, [], 0, ""):
            continue
        used.append(key)
    return used


def reject_unavailable_addons(event: dict, body: Any) -> dict:
    """Field errors for add-ons the request uses but the event does not offer ({} when fine)."""
    errors = {}
    for key in requested_addons(body):
        if key not in ADDONS or not addon_enabled(event, key):
            label = ADDON_LABELS.get(key, "That option")
            errors[f"addons.{key}"] = f"{label} is not available for this event"
        elif key not in NATIVE_ADDONS_SUPPORTED and not is_legacy_sourced(event):
            errors[f"addons.{key}"] = f"{ADDON_LABELS[key]} cannot be booked at registration yet"
    return errors


def validate_input(raw: Any, *, legacy: bool = False) -> dict:
    """Admin payload check for `capabilities`; returns {field: message}."""
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        return {"capabilities": "Must be an object"}
    errors = {}
    for key, value in raw.items():
        if key not in ADDONS:
            errors[f"capabilities.{key}"] = "Unknown capability (guest checkout is controlled by Access settings)"
            continue
        if not isinstance(value, dict) or set(value) - {"enabled"} or not isinstance(value.get("enabled"), bool):
            errors[f"capabilities.{key}"] = "Use {\"enabled\": true|false}"
        elif value["enabled"] and not legacy and key not in NATIVE_ADDONS_SUPPORTED:
            errors[f"capabilities.{key}"] = (
                f"{ADDON_LABELS[key]} is not available on events created in the event platform yet; "
                "it only works for events that use the original registration pipeline")
    return errors


def clean_input(raw: Any) -> dict:
    """Canonical stored form of a validated admin payload (unknown keys dropped)."""
    if not isinstance(raw, dict):
        return {}
    return {name: {"enabled": bool(raw[name]["enabled"])} for name in ADDONS
            if isinstance(raw.get(name), dict) and isinstance(raw[name].get("enabled"), bool)}
