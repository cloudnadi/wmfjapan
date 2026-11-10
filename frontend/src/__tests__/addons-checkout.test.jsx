import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { ADDON_FIXTURE, makeEvent } from "./fixtures";

vi.mock("@aws-sdk/client-cognito-identity-provider", async () => (await import("./sdkMock")).mockSdk());
vi.mock("../eventsApi", () => ({
  getPublicEvent: vi.fn(), registerForEvent: vi.fn(), uploadEventFile: vi.fn(), listPublicEvents: vi.fn(),
  getRegistrationByToken: vi.fn(), payRegistration: vi.fn(),
}));

import EventRegister from "../EventRegister";
import { addonErrors, addonsPayload, addonsTotal, EMPTY_ADDONS } from "../AddonsStep";
import { getPublicEvent, registerForEvent } from "../eventsApi";

const withAddons = (over = {}) => makeEvent({ ...ADDON_FIXTURE, ...over });
const click = (target) => fireEvent.click(target instanceof HTMLElement ? target : screen.getAllByRole("button", { name: target }).at(-1));
const qty = (label, n) => fireEvent.change(screen.getByRole("spinbutton", { name: label }), { target: { value: String(n) } });

async function toOptions(event, tickets = 2) {
  getPublicEvent.mockResolvedValue({ event });
  render(<EventRegister navigateTo={vi.fn()} slug="sample-gathering" />);
  click(await screen.findByRole("button", { name: /continue as a guest/i }));
  qty("Adult", tickets);
  click(/^continue$/i);
  return screen.findByRole("heading", { name: "Options" });
}

beforeEach(() => {
  getPublicEvent.mockReset();
  registerForEvent.mockReset();
});

describe("add-on helpers", () => {
  const ev = withAddons();

  it("builds the request only from what is enabled", () => {
    const value = { slotId: "am", upgrades: { vip: 1, lounge: 0 }, seats: ["main-A2", "front-A1"] };
    expect(addonsPayload(ev, value)).toEqual({ entrySlots: { slotId: "am" }, zoneUpgrade: { vip: 1 }, seatReservation: { seats: ["main-A2", "front-A1"] } });
    expect(addonsPayload(makeEvent(), value)).toEqual({});
    expect(addonsPayload(withAddons({ capabilities: { entrySlots: { enabled: true } } }), value)).toEqual({ entrySlots: { slotId: "am" } });
  });

  it("totals upgrades and per-seat extras for display", () => {
    expect(addonsTotal(ev, { ...EMPTY_ADDONS, upgrades: { vip: 2, lounge: 1 }, seats: ["main-A2", "front-A1"] })).toBe(2 * 500 + 300 + 700);
  });

  it("requires a slot and exactly one seat per ticket", () => {
    expect(addonErrors(ev, EMPTY_ADDONS, 2)).toEqual({ "addons.entrySlots": "Choose a time slot.", "addons.seatReservation": "Choose exactly 2 seat(s)." });
    expect(addonErrors(ev, { ...EMPTY_ADDONS, slotId: "am", seats: ["main-A2", "main-A3"] }, 2)).toEqual({});
  });
});

describe("the options step", () => {
  it("is skipped entirely when the event offers no add-ons", async () => {
    getPublicEvent.mockResolvedValue({ event: makeEvent() });
    render(<EventRegister navigateTo={vi.fn()} slug="sample-gathering" />);
    click(await screen.findByRole("button", { name: /continue as a guest/i }));
    qty("Adult", 1);
    click(/^continue$/i);
    expect(await screen.findByRole("heading", { name: "Your details" })).toBeTruthy();
    expect(screen.queryByRole("heading", { name: "Options" })).toBeNull();
  });

  it("shows only the add-ons the event has switched on", async () => {
    await toOptions(withAddons({ capabilities: { zoneUpgrade: { enabled: true } } }));
    expect(screen.getByText("Zone upgrade")).toBeTruthy();
    expect(screen.queryByText("Entry slots")).toBeNull();
    expect(screen.queryByText("Seat reservation")).toBeNull();
  });

  it("will not continue without a slot and the right number of seats", async () => {
    await toOptions(withAddons());
    click(/^continue$/i);
    expect(await screen.findByText("Choose a time slot.")).toBeTruthy();
    expect(screen.getByText("Choose exactly 2 seat(s).")).toBeTruthy();
    expect(screen.queryByRole("heading", { name: "Your details" })).toBeNull();
  });

  it("full slots and taken seats cannot be chosen, and seats stop at one per ticket", async () => {
    await toOptions(withAddons());
    expect(screen.getByRole("radio", { name: /afternoon/i }).disabled).toBe(true);          // 1 place left, 2 people
    expect(screen.getByRole("button", { name: /main hall A1 \(taken\)/i }).disabled).toBe(true);
    fireEvent.click(screen.getByRole("radio", { name: /morning/i }));
    fireEvent.click(screen.getByRole("button", { name: /^main hall A2$/i }));
    fireEvent.click(screen.getByRole("button", { name: /^main hall A3$/i }));
    expect(screen.getByRole("button", { name: /^main hall B1$/i }).disabled).toBe(true);    // two tickets, two seats
    fireEvent.click(screen.getByRole("button", { name: /^main hall A3$/i }));               // deselect frees one
    expect(screen.getByRole("button", { name: /^main hall B1$/i }).disabled).toBe(false);
  });

  it("upgrades cannot exceed the number of tickets", async () => {
    await toOptions(withAddons({ capabilities: { zoneUpgrade: { enabled: true } } }), 1);
    fireEvent.click(screen.getByRole("button", { name: /more VIP zone/i }));
    expect(screen.getByRole("button", { name: /more Lounge/i }).disabled).toBe(true);        // one ticket, one upgrade
    expect(screen.getByRole("button", { name: /more VIP zone/i }).disabled).toBe(true);
  });

  it("the order summary and total include the add-ons, and the request carries only ids and choices", async () => {
    registerForEvent.mockResolvedValue({ registration: { registrationId: "R-1", checkInToken: "t", status: "confirmed" }, payment: {} });
    await toOptions(withAddons());
    fireEvent.click(screen.getByRole("radio", { name: /morning/i }));
    fireEvent.click(screen.getByRole("button", { name: /^front A1$/i }));
    fireEvent.click(screen.getByRole("button", { name: /^main hall A2$/i }));
    fireEvent.click(screen.getByRole("button", { name: /more VIP zone/i }));
    const summary = screen.getByRole("complementary");
    expect(within(summary).getByText("¥5,200")).toBeTruthy();                                // 2 × ¥2,000 + ¥700 (front seat) + ¥500 (VIP)
    click(/^continue$/i);
    fireEvent.change(await screen.findByLabelText(/full name/i), { target: { value: "Pat Example" } });
    fireEvent.change(screen.getByLabelText(/^email/i), { target: { value: "pat@example.com" } });
    fireEvent.change(screen.getByLabelText(/phone/i), { target: { value: "+81 90 0000 0000" } });
    fireEvent.click(screen.getByLabelText(/privacy policy/i));
    fireEvent.click(screen.getByLabelText(/terms and conditions/i));
    click(/^continue$/i);
    expect(await screen.findByText(/Time slot: Morning/)).toBeTruthy();
    fireEvent.click(screen.getByRole("radio", { name: /bank transfer/i }));
    click(/register and pay/i);
    await waitFor(() => expect(registerForEvent).toHaveBeenCalled());
    const payload = registerForEvent.mock.calls[0][1];
    expect(payload.addons).toEqual({ entrySlots: { slotId: "am" }, zoneUpgrade: { vip: 1 }, seatReservation: { seats: ["front-A1", "main-A2"] } });
    expect(JSON.stringify(payload)).not.toMatch(/priceJpy|totalJpy|extraPriceJpy/);
  });

  it("when someone else takes a choice first, the attendee is returned to the options with fresh availability", async () => {
    registerForEvent.mockRejectedValue(Object.assign(new Error("Something you chose was just taken. Please choose again."), {
      status: 409, code: "addon_unavailable", fields: { "addons.seatReservation": "No longer available" } }));
    await toOptions(withAddons({ capabilities: { seatReservation: { enabled: true } } }));
    fireEvent.click(screen.getByRole("button", { name: /^main hall A2$/i }));
    fireEvent.click(screen.getByRole("button", { name: /^main hall A3$/i }));
    click(/^continue$/i);
    fireEvent.change(await screen.findByLabelText(/full name/i), { target: { value: "Pat Example" } });
    fireEvent.change(screen.getByLabelText(/^email/i), { target: { value: "pat@example.com" } });
    fireEvent.change(screen.getByLabelText(/phone/i), { target: { value: "+81 90 0000 0000" } });
    fireEvent.click(screen.getByLabelText(/privacy policy/i));
    fireEvent.click(screen.getByLabelText(/terms and conditions/i));
    click(/^continue$/i);
    fireEvent.click(await screen.findByRole("radio", { name: /bank transfer/i }));
    const loads = getPublicEvent.mock.calls.length;
    click(/register and pay/i);
    expect(await screen.findByRole("heading", { name: "Options" })).toBeTruthy();
    expect(screen.getByText(/was just taken/i)).toBeTruthy();
    await waitFor(() => expect(getPublicEvent.mock.calls.length).toBe(loads + 1));
  });
});
