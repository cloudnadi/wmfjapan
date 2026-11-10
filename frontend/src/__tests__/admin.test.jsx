import { useState } from "react";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("../cognito", () => ({
  loginUser: vi.fn(), completeNewPasswordChallenge: vi.fn(), requestPasswordResetCode: vi.fn(), confirmPasswordReset: vi.fn(),
  logout: vi.fn(), getValidIdToken: vi.fn(async () => "admin-id-token"),
}));
vi.mock("../eventPlatformApi", () => ({ getPaymentMethods: vi.fn(), savePaymentMethods: vi.fn() }));

import AdminLogin from "../AdminLogin";
import EventAddons from "../EventAddons";
import PaymentSettings from "../PaymentSettings";
import * as cognito from "../cognito";
import { getPaymentMethods, savePaymentMethods } from "../eventPlatformApi";

const type = (label, value) => fireEvent.change(screen.getByLabelText(label), { target: { value } });

describe("organiser sign-in", () => {
  beforeEach(() => vi.clearAllMocks());

  it("signs in and reports success", async () => {
    cognito.loginUser.mockResolvedValue("token");
    const onSignedIn = vi.fn();
    render(<AdminLogin onSignedIn={onSignedIn} />);
    type(/email address/i, "org@example.com");
    type(/^password/i, "Passw0rd!-test");
    fireEvent.click(screen.getByRole("button", { name: /^sign in$/i }));
    await waitFor(() => expect(onSignedIn).toHaveBeenCalled());
    expect(cognito.loginUser).toHaveBeenCalledWith("org@example.com", "Passw0rd!-test");
  });

  it("wrong credentials give one generic message", async () => {
    cognito.loginUser.mockRejectedValue(Object.assign(new Error("x"), { name: "UserNotFoundException" }));
    render(<AdminLogin onSignedIn={vi.fn()} />);
    type(/email address/i, "org@example.com");
    type(/^password/i, "nope");
    fireEvent.click(screen.getByRole("button", { name: /^sign in$/i }));
    expect((await screen.findByRole("alert")).textContent).toBe("The email or password is incorrect.");
  });

  it("an invited organiser must choose a new password on first sign-in", async () => {
    cognito.loginUser.mockRejectedValue(Object.assign(new Error("x"), { code: "NEW_PASSWORD_REQUIRED", session: "sess-1" }));
    cognito.completeNewPasswordChallenge.mockResolvedValue("token");
    const onSignedIn = vi.fn();
    render(<AdminLogin onSignedIn={onSignedIn} />);
    type(/email address/i, "org@example.com");
    type(/^password/i, "Temp-pass-1");
    fireEvent.click(screen.getByRole("button", { name: /^sign in$/i }));
    expect(await screen.findByText(/choose a new password to finish/i)).toBeTruthy();
    type(/^new password/i, "A-much-better-Passw0rd!");
    fireEvent.click(screen.getByRole("button", { name: /set password/i }));
    await waitFor(() => expect(onSignedIn).toHaveBeenCalled());
    expect(cognito.completeNewPasswordChallenge).toHaveBeenCalledWith("sess-1", "org@example.com", "A-much-better-Passw0rd!");
  });

  it("password reset: code by email, then a new password, then back to sign-in", async () => {
    cognito.requestPasswordResetCode.mockResolvedValue("CODE_SENT");
    cognito.confirmPasswordReset.mockResolvedValue("SUCCESS");
    render(<AdminLogin onSignedIn={vi.fn()} />);
    fireEvent.click(screen.getByRole("button", { name: /forgot your password/i }));
    type(/email address/i, "org@example.com");
    fireEvent.click(screen.getByRole("button", { name: /send reset code/i }));
    expect(await screen.findByText(/if that account exists/i)).toBeTruthy();
    type(/verification code/i, "123456");
    type(/^new password/i, "A-much-better-Passw0rd!");
    fireEvent.click(screen.getByRole("button", { name: /set password/i }));
    expect(await screen.findByText(/password changed/i)).toBeTruthy();
    expect(cognito.confirmPasswordReset).toHaveBeenCalledWith("org@example.com", "123456", "A-much-better-Passw0rd!");
  });
});

function AddonsHarness({ initial = {}, errors = {} }) {
  const [draft, setDraft] = useState({ capabilities: {}, slots: [], upgrades: [], seatMap: { sections: [] }, ...initial });
  const set = (key, value) => setDraft((d) => ({ ...d, [key]: value }));
  return (
    <>
      <EventAddons draft={draft} set={set} readOnly={false} errors={errors} />
      <output data-testid="draft">{JSON.stringify(draft)}</output>
    </>
  );
}
const draftOf = () => JSON.parse(screen.getByTestId("draft").textContent);

describe("add-ons tab", () => {
  it("each add-on is switched on separately", () => {
    render(<AddonsHarness />);
    fireEvent.click(screen.getByLabelText(/offer this at registration/i, { selector: "#ad-slots-on" }));
    expect(draftOf().capabilities).toEqual({ entrySlots: { enabled: true } });
    fireEvent.click(screen.getByLabelText(/offer this at registration/i, { selector: "#ad-seat-on" }));
    expect(draftOf().capabilities).toEqual({ entrySlots: { enabled: true }, seatReservation: { enabled: true } });
    fireEvent.click(screen.getByLabelText(/offer this at registration/i, { selector: "#ad-slots-on" }));
    expect(draftOf().capabilities.entrySlots.enabled).toBe(false);
  });

  it("adds, edits and removes time slots, upgrades and seat sections", () => {
    render(<AddonsHarness />);
    fireEvent.click(screen.getByRole("button", { name: /add a time slot/i }));
    fireEvent.change(screen.getByLabelText("Name", { selector: "#slot-0-name" }), { target: { value: "Morning" } });
    fireEvent.change(screen.getByLabelText("Places", { selector: "#slot-0-cap" }), { target: { value: "40" } });
    expect(draftOf().slots[0]).toMatchObject({ name: "Morning", capacity: 40 });
    expect(draftOf().slots[0].slotId).toMatch(/^[0-9a-f]{8}$/);                              // a stable id from the start

    fireEvent.click(screen.getByRole("button", { name: /add an upgrade/i }));
    fireEvent.change(screen.getByLabelText("Name", { selector: "#up-0-name" }), { target: { value: "VIP" } });
    fireEvent.change(screen.getByLabelText("Price (JPY)", { selector: "#up-0-price" }), { target: { value: "500" } });
    fireEvent.change(screen.getByLabelText("Limit", { selector: "#up-0-limit" }), { target: { value: "10" } });
    expect(draftOf().upgrades[0]).toMatchObject({ name: "VIP", priceJpy: 500, limit: 10 });
    fireEvent.change(screen.getByLabelText("Limit", { selector: "#up-0-limit" }), { target: { value: "" } });
    expect(draftOf().upgrades[0].limit).toBeNull();                                           // empty = unlimited

    fireEvent.click(screen.getByRole("button", { name: /add a section/i }));
    fireEvent.change(screen.getByLabelText("Name", { selector: "#sec-0-name" }), { target: { value: "Hall" } });
    fireEvent.change(screen.getByLabelText(/^Rows/, { selector: "#sec-0-rows" }), { target: { value: "4" } });
    fireEvent.change(screen.getByLabelText("Seats per row", { selector: "#sec-0-per" }), { target: { value: "5" } });
    expect(draftOf().seatMap.sections[0]).toMatchObject({ name: "Hall", rows: 4, seatsPerRow: 5 });
    expect(screen.getByText("20 seats")).toBeTruthy();

    fireEvent.click(screen.getAllByRole("button", { name: /^remove$/i })[0]);
    expect(draftOf().slots).toEqual([]);
  });

  it("shows the server's validation messages next to the right add-on", () => {
    render(<AddonsHarness errors={{ "capabilities.zoneUpgrade": "Add an upgrade before switching Zone upgrade on", "slots[0]": "Capacity must be a whole number, 1 or greater" }}
      initial={{ slots: [{ slotId: "a", name: "Early", capacity: 0 }] }} />);
    expect(screen.getByText(/add an upgrade before switching/i)).toBeTruthy();
    expect(screen.getByText(/capacity must be a whole number/i)).toBeTruthy();
  });
});

describe("payment methods", () => {
  const data = { methods: { square: { enabled: true }, bank: { enabled: true, url: "", instructions: "Account 123" }, paypay: { enabled: false, url: "", instructions: "" } },
    labels: { square: "Credit / Debit Card", paypay: "PayPay", bank: "Bank Transfer" }, cardConfigured: false };

  beforeEach(() => { vi.clearAllMocks(); getPaymentMethods.mockResolvedValue(data); });

  it("lets a super admin change and save them", async () => {
    savePaymentMethods.mockImplementation(async (m) => ({ ...data, methods: m }));
    render(<PaymentSettings roles={["super_admin"]} />);
    fireEvent.change(await screen.findByLabelText(/instructions shown/i, { selector: "#pay-bank-ins" }), { target: { value: "New account 999" } });
    fireEvent.click(screen.getByLabelText(/offer this method/i, { selector: "#pay-paypay-on" }));
    fireEvent.click(screen.getByRole("button", { name: /^save$/i }));
    expect(await screen.findByText(/payment settings saved/i)).toBeTruthy();
    expect(savePaymentMethods.mock.calls[0][0].bank.instructions).toBe("New account 999");
    expect(savePaymentMethods.mock.calls[0][0].paypay.enabled).toBe(true);
    expect(screen.getByText(/square is not configured/i)).toBeTruthy();
  });

  it("is read-only for event managers", async () => {
    render(<PaymentSettings roles={["event_manager"]} />);
    expect(await screen.findByText(/only a super admin can change/i)).toBeTruthy();
    expect(screen.queryByRole("button", { name: /^save$/i })).toBeNull();
    expect(screen.getByLabelText(/instructions shown/i, { selector: "#pay-bank-ins" }).matches(":disabled")).toBe(true);   // inside a disabled fieldset
  });

  it("shows the server's refusal", async () => {
    savePaymentMethods.mockRejectedValue(Object.assign(new Error("Please check the payment settings"), { fields: { "paypay.url": "Use an http:// or https:// link" } }));
    render(<PaymentSettings roles={["super_admin"]} />);
    await screen.findByLabelText(/instructions shown/i, { selector: "#pay-bank-ins" });
    fireEvent.click(screen.getByRole("button", { name: /^save$/i }));
    expect((await screen.findAllByText(/use an http/i)).length).toBeGreaterThan(0);
  });
});
