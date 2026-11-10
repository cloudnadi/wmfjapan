import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { cognitoError, sdkState } from "./sdkMock";
import { fakeIdToken, makeEvent } from "./fixtures";

vi.mock("@aws-sdk/client-cognito-identity-provider", async () => (await import("./sdkMock")).mockSdk());
vi.mock("../auth/social", async (importOriginal) => ({ ...(await importOriginal()), startSocialSignIn: vi.fn(async () => {}) }));
vi.mock("../eventsApi", () => ({
  getPublicEvent: vi.fn(),
  registerForEvent: vi.fn(),
  uploadEventFile: vi.fn(),
  listPublicEvents: vi.fn(),
  getRegistrationByToken: vi.fn(),
  payRegistration: vi.fn(),
}));
vi.mock("../api", () => ({ getSiteConfig: vi.fn(async () => ({ config: {} })), getMembershipStatus: vi.fn(async () => ({})) }));

import EventRegister, { takeCheckoutDraft } from "../EventRegister";
import * as session from "../auth/session";
import { startSocialSignIn } from "../auth/social";
import { getPublicEvent, registerForEvent } from "../eventsApi";

const SLUG = "sample-gathering";
const signedIn = (over = {}) => makeEvent({
  viewer: { loggedIn: true, email: "pat@example.com", name: "Pat Example" }, ...over,
});
const authResult = () => ({ AuthenticationResult: { IdToken: fakeIdToken(), AccessToken: "a", RefreshToken: "r" } });
const show = (navigateTo = vi.fn()) => render(<EventRegister navigateTo={navigateTo} slug={SLUG} />);
const click = (target) => fireEvent.click(target instanceof HTMLElement ? target : screen.getAllByRole("button", { name: target }).at(-1));
const submitForm = (name) => fireEvent.click(screen.getAllByRole("button", { name }).find((b) => b.type === "submit"));
const setQty = (label, n) => fireEvent.change(screen.getByLabelText(label), { target: { value: String(n) } });

async function fillDetailsAndReview() {
  fireEvent.change(await screen.findByLabelText(/full name/i), { target: { value: "Pat Example" } });
  const email = screen.getByLabelText(/^email/i);
  if (!email.readOnly) fireEvent.change(email, { target: { value: "pat@example.com" } });
  fireEvent.change(screen.getByLabelText(/phone/i), { target: { value: "+81 90 0000 0000" } });
  fireEvent.click(screen.getByLabelText(/privacy policy/i));
  fireEvent.click(screen.getByLabelText(/terms and conditions/i));
  click(/^continue$/i);
}

beforeEach(() => {
  getPublicEvent.mockReset();
  registerForEvent.mockReset();
  startSocialSignIn.mockClear();
  sdkState.calls = [];
  sdkState.respond = () => authResult();
  session.__resetSessionForTests();
});

describe("guest checkout", () => {
  it("lets a visitor register without an account, and sends only what the server needs", async () => {
    getPublicEvent.mockResolvedValue({ event: makeEvent() });
    registerForEvent.mockResolvedValue({ registration: { registrationId: "R-1", checkInToken: "tok", status: "confirmed" }, payment: {} });
    const navigateTo = vi.fn();
    show(navigateTo);

    expect(await screen.findByRole("heading", { name: /how would you like to register/i })).toBeTruthy();
    expect(screen.getByText(/no account is created for guests/i)).toBeTruthy();   // the guest path is explicit
    click(/continue as a guest/i);

    setQty("Child", 1);
    click(/^continue$/i);
    await fillDetailsAndReview();
    click(/^register$/i);

    await waitFor(() => expect(registerForEvent).toHaveBeenCalledTimes(1));
    const [slug, payload] = registerForEvent.mock.calls[0];
    expect(slug).toBe(SLUG);
    expect(payload.tickets).toEqual({ child: 1 });                       // quantities only: never a price or total
    expect(payload.answers).toMatchObject({ fullName: "Pat Example", email: "pat@example.com" });
    expect(payload).not.toHaveProperty("totalJpy");
    expect(payload).not.toHaveProperty("addons");                         // nothing is offered at checkout that the event doesn't have
    expect(typeof payload.idempotencyKey).toBe("string");
    expect(sdkState.calls).toHaveLength(0);                               // no Cognito account was created or contacted
    await waitFor(() => expect(navigateTo).toHaveBeenCalledWith(expect.stringContaining(`/events/${SLUG}/registered?reg=R-1&token=tok`), { replace: true }));
  });

  it("requires the fields the event needs before submitting", async () => {
    getPublicEvent.mockResolvedValue({ event: makeEvent() });
    show();
    click(await screen.findByRole("button", { name: /continue as a guest/i }));
    setQty("Child", 1);
    click(/^continue$/i);
    click(await screen.findByRole("button", { name: /^continue$/i }));
    expect((await screen.findAllByRole("alert")).length).toBeGreaterThan(0);
    expect(screen.getByText(/fix the highlighted/i)).toBeTruthy();
    expect(registerForEvent).not.toHaveBeenCalled();
  });

  it("shows server-side field errors from the API", async () => {
    getPublicEvent.mockResolvedValue({ event: makeEvent() });
    registerForEvent.mockRejectedValue(Object.assign(new Error("Please check the highlighted answers"), { status: 400, fields: { phone: "Enter a valid phone number" }, code: "" }));
    show();
    click(await screen.findByRole("button", { name: /continue as a guest/i }));
    setQty("Child", 1);
    click(/^continue$/i);
    await fillDetailsAndReview();
    click(/^register$/i);
    expect((await screen.findAllByText(/Enter a valid phone number/)).length).toBe(2);   // summary banner + the field itself
    expect(screen.getByLabelText(/phone/i).value).toBe("+81 90 0000 0000");                 // and nothing typed was lost
  });

  it("is refused clearly when the event does not offer an option the request used", async () => {
    getPublicEvent.mockResolvedValue({ event: makeEvent() });
    registerForEvent.mockRejectedValue(Object.assign(new Error("Some options are not available for this event"), { status: 400, code: "feature_disabled", fields: { "addons.seatReservation": "Seat reservation is not available for this event" } }));
    show();
    click(await screen.findByRole("button", { name: /continue as a guest/i }));
    setQty("Child", 1);
    click(/^continue$/i);
    await fillDetailsAndReview();
    click(/^register$/i);
    expect((await screen.findAllByText(/not available for this event/i)).length).toBeGreaterThan(0);
  });

  it("a sold-out race is reported and the page refreshes the event", async () => {
    getPublicEvent.mockResolvedValue({ event: makeEvent() });
    registerForEvent.mockRejectedValue(Object.assign(new Error("Sorry, that ticket type is sold out"), { status: 409, code: "sold_out", fields: {} }));
    show();
    click(await screen.findByRole("button", { name: /continue as a guest/i }));
    setQty("Child", 1);
    click(/^continue$/i);
    await fillDetailsAndReview();
    const loads = getPublicEvent.mock.calls.length;
    click(/^register$/i);
    expect(await screen.findByText(/sold out/i)).toBeTruthy();
    await waitFor(() => expect(getPublicEvent.mock.calls.length).toBe(loads + 1));
  });

  it("an event that is closed, sold out or cancelled shows a message instead of a form", async () => {
    for (const [event, text] of [
      [makeEvent({ status: "closed" }), /registration is closed/i],
      [makeEvent({ availability: { full: true } }), /sold out/i],
      [makeEvent({ status: "cancelled" }), /cancelled/i],
    ]) {
      getPublicEvent.mockResolvedValue({ event });
      const { unmount } = show();
      expect(await screen.findByRole("alert")).toBeTruthy();
      expect(screen.getByText(text)).toBeTruthy();
      expect(screen.queryByRole("button", { name: /continue as a guest/i })).toBeNull();
      unmount();
    }
  });
});

describe("guest checkout switched off for an event", () => {
  const noGuests = () => makeEvent({
    access: { guestsAllowed: false },
    capabilities: { guestCheckout: { enabled: false }, authentication: { required: true } },
    registration: { allowed: false, reason: "login_required" },
  });

  it("asks for an account and offers no guest option", async () => {
    getPublicEvent.mockResolvedValue({ event: noGuests() });
    show();
    expect(await screen.findByRole("heading", { name: /^sign in$/i })).toBeTruthy();
    expect(screen.getByText(/needs a signed-in account/i)).toBeTruthy();
    expect(screen.queryByRole("button", { name: /continue as a guest/i })).toBeNull();
  });

  it("after signing in the visitor can register", async () => {
    getPublicEvent.mockResolvedValueOnce({ event: noGuests() }).mockResolvedValue({ event: signedIn({ registration: { allowed: true } }) });
    show();
    fireEvent.change(await screen.findByLabelText(/email address/i), { target: { value: "pat@example.com" } });
    fireEvent.change(screen.getByLabelText(/^password/i), { target: { value: "Passw0rd-test" } });
    submitForm(/^sign in$/i);
    expect(await screen.findByRole("heading", { name: "Tickets" })).toBeTruthy();
  });
});

describe("signed-in checkout", () => {
  it("skips the account choice, locks the email to the account and registers", async () => {
    getPublicEvent.mockResolvedValue({ event: signedIn() });
    registerForEvent.mockResolvedValue({ registration: { registrationId: "R-2", checkInToken: "t2", status: "confirmed" }, payment: {} });
    const navigateTo = vi.fn();
    show(navigateTo);
    expect(await screen.findByText(/signed in as pat@example.com/i)).toBeTruthy();
    expect(screen.queryByRole("button", { name: /continue as a guest/i })).toBeNull();
    click(/^continue$/i);
    setQty("Child", 1);
    click(/^continue$/i);
    expect(screen.getByLabelText(/^email/i).readOnly).toBe(true);
    await fillDetailsAndReview();
    click(/^register$/i);
    await waitFor(() => expect(registerForEvent).toHaveBeenCalled());
    expect(registerForEvent.mock.calls[0][1].answers.email).toBe("pat@example.com");
  });

  it("a session that ended while the form was open asks for sign-in and keeps everything entered", async () => {
    getPublicEvent.mockResolvedValueOnce({ event: signedIn() });
    registerForEvent.mockRejectedValue(Object.assign(new Error("Your session has expired. Please sign in again."), { status: 401, code: "session_expired", fields: {} }));
    show();
    click(await screen.findByRole("button", { name: /^continue$/i }));
    setQty("Child", 1);
    click(/^continue$/i);
    await fillDetailsAndReview();
    getPublicEvent.mockResolvedValue({ event: makeEvent() });                    // the refreshed page now sees a guest
    click(/^register$/i);
    expect(await screen.findByRole("heading", { name: /^sign in$/i })).toBeTruthy();
    expect(screen.getByText(/session has expired/i)).toBeTruthy();

    getPublicEvent.mockResolvedValue({ event: signedIn() });
    fireEvent.change(screen.getByLabelText(/email address/i), { target: { value: "pat@example.com" } });
    fireEvent.change(screen.getByLabelText(/^password/i), { target: { value: "Passw0rd-test" } });
    submitForm(/^sign in$/i);
    expect(await screen.findByRole("heading", { name: /review/i })).toBeTruthy();   // resumes exactly where it stopped
    expect(screen.getByText(/Child × 1/)).toBeTruthy();
  });

  it("an unverified email is explained, not treated as an expired session", async () => {
    getPublicEvent.mockResolvedValue({ event: signedIn() });
    registerForEvent.mockRejectedValue(Object.assign(new Error("Please verify your email address before registering."), { status: 401, code: "email_unverified", fields: {} }));
    show();
    click(await screen.findByRole("button", { name: /^continue$/i }));
    setQty("Child", 1);
    click(/^continue$/i);
    await fillDetailsAndReview();
    click(/^register$/i);
    expect(await screen.findByText(/verify your email address/i)).toBeTruthy();
    expect(screen.queryByRole("heading", { name: /^sign in$/i })).toBeNull();
  });
});

describe("the checkout is preserved when the visitor signs in", () => {
  async function chooseTicketsAsGuest() {
    getPublicEvent.mockResolvedValueOnce({ event: makeEvent() });
    const view = show();
    click(await screen.findByRole("button", { name: /continue as a guest/i }));
    setQty("Adult", 2);
    return view;
  }

  it("with email and password: tickets stay selected and the visitor lands on the same step", async () => {
    await chooseTicketsAsGuest();
    getPublicEvent.mockResolvedValue({ event: signedIn() });
    fireEvent.click(screen.getByRole("button", { name: /sign in or create an account/i }));
    fireEvent.change(await screen.findByLabelText(/email address/i), { target: { value: "pat@example.com" } });
    fireEvent.change(screen.getByLabelText(/^password/i), { target: { value: "Passw0rd-test" } });
    submitForm(/^sign in$/i);
    expect(await screen.findByRole("heading", { name: "Tickets" })).toBeTruthy();
    expect(screen.getByLabelText("Adult").value).toBe("2");
  });

  it("with social sign-in: the draft is saved before leaving and restored on return", async () => {
    await chooseTicketsAsGuest();
    fireEvent.click(screen.getByRole("button", { name: /sign in or create an account/i }));
    fireEvent.click(await screen.findByRole("button", { name: /continue with google/i }));
    await waitFor(() => expect(startSocialSignIn).toHaveBeenCalledWith("Google", `/events/${SLUG}/register`));
    const saved = JSON.parse(window.sessionStorage.getItem(`wmf-checkout.${SLUG}`));
    expect(saved).toMatchObject({ v: 1, qty: { adult: 2 }, step: "tickets" });
    expect(JSON.stringify(saved)).not.toMatch(/token|password|secret/i);

    // …the provider round trip happens, the page reloads with the visitor now signed in…
    document.body.innerHTML = "";
    getPublicEvent.mockResolvedValue({ event: signedIn() });
    show();
    expect(await screen.findByRole("heading", { name: "Tickets" })).toBeTruthy();
    expect(screen.getByLabelText("Adult").value).toBe("2");
    expect(screen.getByText(/we kept your selections/i)).toBeTruthy();
    expect(window.sessionStorage.getItem(`wmf-checkout.${SLUG}`)).toBeNull();    // consumed
  });

  it("ignores a stale or damaged draft, and drops tickets that no longer exist", async () => {
    window.sessionStorage.setItem(`wmf-checkout.${SLUG}`, JSON.stringify({ v: 1, savedAt: Date.now() - 3 * 60 * 60 * 1000, qty: { adult: 3 }, step: "tickets" }));
    expect(takeCheckoutDraft(SLUG)).toBeNull();
    window.sessionStorage.setItem(`wmf-checkout.${SLUG}`, "{not json");
    expect(takeCheckoutDraft(SLUG)).toBeNull();

    window.sessionStorage.setItem(`wmf-checkout.${SLUG}`, JSON.stringify({ v: 1, savedAt: Date.now(), qty: { adult: 1, gone: 4 }, step: "tickets" }));
    getPublicEvent.mockResolvedValue({ event: signedIn() });
    show();
    expect(await screen.findByRole("heading", { name: "Tickets" })).toBeTruthy();
    expect(screen.getByLabelText("Adult").value).toBe("1");
  });
});
