import { describe, expect, it } from "vitest";
import { ADDON_KEYS, canStartRegistration, enabledAddons, getEventState, normalizeCapabilities, normalizeEvent } from "../eventModel";
import { makeEvent, makeListing } from "./fixtures";

describe("capability defaults for records that predate capabilities", () => {
  it("native event with no capabilities: guests allowed, every add-on off", () => {
    const caps = normalizeCapabilities({ source: "native", access: { guestsAllowed: true } });
    expect(caps.guestCheckout.enabled).toBe(true);
    expect(caps.authentication.required).toBe(false);
    ADDON_KEYS.forEach((key) => expect(caps[key].enabled).toBe(false));
  });

  it("an event with nothing at all is safe too (guests allowed, no add-ons)", () => {
    const caps = normalizeCapabilities({});
    expect(caps.guestCheckout.enabled).toBe(true);
    expect(enabledAddons({})).toEqual([]);
  });

  it("derives guest checkout from access settings or the listing flags", () => {
    expect(normalizeCapabilities({ access: { guestsAllowed: false } }).guestCheckout.enabled).toBe(false);
    expect(normalizeCapabilities({ access: { membersOnly: true } }).guestCheckout.enabled).toBe(false);
    expect(normalizeCapabilities({ guestsAllowed: false }).authentication.required).toBe(true);
    expect(normalizeCapabilities({ membersOnly: true }).authentication.required).toBe(true);
  });

  it("members-only always needs an account, even if capabilities say otherwise", () => {
    const caps = normalizeCapabilities({ access: { membersOnly: true }, capabilities: { guestCheckout: { enabled: true } } });
    expect(caps.guestCheckout.enabled).toBe(false);
    expect(caps.authentication.required).toBe(true);
  });

  it("original-pipeline events keep entry slots on by default, but nothing else", () => {
    const caps = normalizeCapabilities({ source: "legacy" });
    expect(caps.entrySlots.enabled).toBe(true);
    expect(caps.seatReservation.enabled || caps.zoneUpgrade.enabled || caps.celebrityDinner.enabled).toBe(false);
  });

  it("explicit values from the platform win, and junk values are ignored", () => {
    const caps = normalizeCapabilities({ source: "legacy", capabilities: { entrySlots: { enabled: false }, zoneUpgrade: { enabled: true }, seatReservation: { enabled: "yes" } } });
    expect(caps.entrySlots.enabled).toBe(false);
    expect(caps.zoneUpgrade.enabled).toBe(true);
    expect(caps.seatReservation.enabled).toBe(false);
  });

  it("a new server's flags are used as given", () => {
    expect(enabledAddons({ capabilities: { seatReservation: { enabled: true }, celebrityDinner: { enabled: true } } })).toEqual(["seatReservation", "celebrityDinner"]);
  });
});

describe("event state", () => {
  it.each([
    ["open", makeEvent(), "open"],
    ["waitlist", makeEvent({ registration: { waitlist: true }, availability: { full: true } }), "waitlist"],
    ["sold out (event full)", makeEvent({ availability: { full: true } }), "sold_out"],
    ["sold out (every ticket)", makeEvent({ tickets: [{ ticketTypeId: "a", name: "A", remaining: 0, yourPriceJpy: 1, memberPriceJpy: 1, nonMemberPriceJpy: 1 }] }), "sold_out"],
    ["closed (status)", makeEvent({ status: "closed" }), "closed"],
    ["closed (window)", makeEvent({ registration: { window: "closed" } }), "closed"],
    ["not open yet", makeEvent({ registration: { window: "not_open" } }), "not_open"],
    ["cancelled", makeEvent({ status: "cancelled" }), "cancelled"],
    ["archived", makeEvent({ status: "archived" }), "cancelled"],
    ["registered elsewhere", makeEvent({ source: "legacy" }), "unavailable"],
    ["sign in needed", makeEvent({ registration: { allowed: false, reason: "login_required" } }), "sign_in_required"],
    ["members only", makeEvent({ registration: { allowed: false, reason: "members_only" } }), "members_only"],
    ["renewal needed", makeEvent({ registration: { allowed: false, reason: "renewal_required" } }), "renewal_required"],
  ])("%s", (_label, event, expected) => {
    expect(getEventState(event)).toBe(expected);
  });

  it("only offers registration where it can actually start", () => {
    expect(["open", "waitlist", "sign_in_required"].every(canStartRegistration)).toBe(true);
    expect(["closed", "sold_out", "cancelled", "unavailable", "not_open"].some(canStartRegistration)).toBe(false);
  });

  it("fills in what partial records leave out", () => {
    const ev = normalizeEvent(makeListing());
    expect(ev.tickets).toEqual([]);
    expect(ev.registration.window).toBe("open");
    expect(ev.capabilities.guestCheckout.enabled).toBe(true);
  });
});
