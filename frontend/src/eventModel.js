/**
 * Event data model for the public site: the shapes the event platform returns, safe defaults for older
 * records, and the derived state the pages render from. Pages never read raw flags themselves.
 *
 * API contract (GET /public/events, GET /public/events/{slug}); fields marked * are optional on old servers:
 *
 * @typedef {{enabled:boolean}} Toggle
 * @typedef {Object} Capabilities
 * @property {Toggle} guestCheckout                 anyone may register without an account
 * @property {{required:boolean}} authentication    a signed-in account is required to register
 * @property {Toggle} seatReservation
 * @property {Toggle} zoneUpgrade
 * @property {Toggle} entrySlots
 * @property {Toggle} celebrityDinner
 *
 * @typedef {Object} PublicEvent
 * @property {string} eventId
 * @property {string} slug
 * @property {string} title
 * @property {string} status                        published | unlisted | closed (| archived, cancelled on other servers)
 * @property {string} [source]                      "native" (event platform) or "legacy" (original pipeline)
 * @property {Capabilities} [capabilities]*         see normalizeCapabilities() for the defaults used when absent
 * @property {Array}  [tickets]
 * @property {{window:string, allowed:boolean, reason:string, waitlist:boolean}} [registration]
 */

export const ADDON_KEYS = ["seatReservation", "zoneUpgrade", "entrySlots", "celebrityDinner"];

/**
 * Capabilities with the documented defaults for records that predate them:
 *   - guest checkout follows the access settings (`access.guestsAllowed`, `membersOnly`, or the listing's
 *     `guestsAllowed` / `membersOnly`): missing means guests are allowed, as before;
 *   - add-ons are OFF unless the server says they are on; the one exception is `entrySlots` on events from
 *     the original pipeline, which could not be switched before and stay on.
 * @returns {Capabilities}
 */
export function normalizeCapabilities(event) {
  const given = event?.capabilities && typeof event.capabilities === "object" ? event.capabilities : {};
  const access = event?.access || {};
  const membersOnly = access.membersOnly ?? event?.membersOnly ?? false;
  const guestsAllowed = access.guestsAllowed ?? event?.guestsAllowed ?? true;
  const derivedGuests = !membersOnly && guestsAllowed !== false;
  const guests = typeof given.guestCheckout?.enabled === "boolean" ? given.guestCheckout.enabled && !membersOnly : derivedGuests;
  const out = {
    guestCheckout: { enabled: guests },
    authentication: { required: typeof given.authentication?.required === "boolean" ? given.authentication.required || !guests : !guests },
  };
  ADDON_KEYS.forEach((key) => {
    const flag = given[key]?.enabled;
    out[key] = { enabled: typeof flag === "boolean" ? flag : key === "entrySlots" && event?.source === "legacy" };
  });
  return out;
}

export function enabledAddons(event) {
  const caps = normalizeCapabilities(event);
  return ADDON_KEYS.filter((key) => caps[key].enabled);
}

/** Fill the pieces older or partial responses (e.g. original-pipeline events) leave out. */
export function normalizeEvent(raw) {
  if (!raw) return raw;
  return {
    tickets: [],
    design: {},
    access: {},
    tags: [],
    location: {},
    longDescription: "",
    viewer: { loggedIn: false, email: "", name: "", memberPricing: false, system: {} },
    paymentMethods: [],
    ...raw,
    registration: { window: "open", allowed: true, reason: "ok", waitlist: false, ...(raw.registration || {}) },
    capabilities: normalizeCapabilities(raw),
  };
}

/**
 * What the visitor can do with this event right now. One of:
 *   cancelled | unavailable | closed | not_open | sold_out | sign_in_required | members_only | renewal_required | open | waitlist
 * `unavailable` covers events registered somewhere else (the original pipeline). A draft/unpublished event is
 * never served by the API at all (404), which the pages show as "not found".
 */
export function getEventState(event) {
  const ev = normalizeEvent(event);
  const reg = ev.registration;
  if (["cancelled", "canceled", "archived"].includes(ev.status)) return "cancelled";
  if (ev.source === "legacy" || ev.legacy) return "unavailable";
  if (ev.status === "closed" || reg.window === "closed") return "closed";
  if (reg.window === "not_open") return "not_open";
  if (!reg.allowed) {
    if (reg.reason === "members_only") return "members_only";
    if (reg.reason === "renewal_required") return "renewal_required";
    return "sign_in_required";
  }
  const everyTicketSoldOut = ev.tickets.length > 0 && ev.tickets.every((x) => x.remaining === 0);
  if ((ev.availability?.full || everyTicketSoldOut) && !reg.waitlist) return "sold_out";
  return reg.waitlist ? "waitlist" : "open";
}

/** Is a "register" button warranted for this state? */
export const canStartRegistration = (state) => ["open", "waitlist", "sign_in_required"].includes(state);
