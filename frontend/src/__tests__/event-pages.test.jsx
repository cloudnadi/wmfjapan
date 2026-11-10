import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { FESTIVAL, makeEvent, makeListing } from "./fixtures";

vi.mock("../eventsApi", () => ({
  listPublicEvents: vi.fn(),
  getPublicEvent: vi.fn(),
  registerForEvent: vi.fn(),
  uploadEventFile: vi.fn(),
  quoteTickets: vi.fn(),
  getRegistrationByToken: vi.fn(),
  payRegistration: vi.fn(),
}));
vi.mock("../api", () => ({
  getSiteConfig: vi.fn(async () => ({ config: {} })),
  getMembershipStatus: vi.fn(async () => ({})),
}));

import EventPage from "../EventPage";
import EventsList from "../EventsList";
import HomePage from "../HomePage";
import { getPublicEvent, listPublicEvents } from "../eventsApi";

const nav = () => vi.fn();
const failure = (status, message = "boom") => Object.assign(new Error(message), { status });

beforeEach(() => {
  listPublicEvents.mockReset();
  getPublicEvent.mockReset();
});

describe("home page and event listing render platform data", () => {
  it("shows exactly the events the platform returns, with nothing hard-coded", async () => {
    listPublicEvents.mockResolvedValue({ events: [makeListing(), makeListing({ eventId: "ev-2", slug: "second", title: "Second Meetup" })], categories: ["Community"] });
    render(<HomePage navigateTo={nav()} />);
    expect(await screen.findByRole("heading", { name: "Sample Gathering" })).toBeTruthy();
    expect(screen.getByRole("heading", { name: "Second Meetup" })).toBeTruthy();
    expect(listPublicEvents).toHaveBeenCalledWith("upcoming");
  });

  it("by default no Onam event or content appears anywhere", async () => {
    listPublicEvents.mockResolvedValue({ events: [], categories: [] });
    const { container } = render(<HomePage navigateTo={nav()} />);
    expect(await screen.findByText(/no upcoming events/i)).toBeTruthy();
    expect(container.ownerDocument.body.textContent).not.toMatch(/onam|aavani|sadhya/i);
    expect(container.querySelector("img[src*='onam']")).toBeNull();
  });

  it("an Onam-style event created in the platform renders like any other event", async () => {
    const onam = makeListing({ eventId: "ev-o", slug: "onam-celebration", title: "Onam Celebration 2027", category: "Festival", capabilities: { entrySlots: { enabled: true } } });
    listPublicEvents.mockResolvedValue({ events: [onam], categories: ["Festival"] });
    const navigateTo = nav();
    render(<EventsList navigateTo={navigateTo} />);
    const heading = await screen.findByRole("heading", { name: "Onam Celebration 2027" });
    fireEvent.click(within(heading).getByRole("button"));
    expect(navigateTo).toHaveBeenCalledWith("/events/onam-celebration");   // the generic page, not a dedicated route
  });

  it("has loading, empty and error states, and the error can be retried", async () => {
    listPublicEvents.mockRejectedValueOnce(failure(500)).mockResolvedValue({ events: [], categories: [] });
    render(<HomePage navigateTo={nav()} />);
    expect(screen.getByText("Loading…")).toBeTruthy();
    expect((await screen.findByRole("alert")).textContent).toMatch(/couldn't load events/i);
    fireEvent.click(screen.getByRole("button", { name: /try again/i }));
    expect(await screen.findByText(/no upcoming events/i)).toBeTruthy();
  });

  it("list page: empty (upcoming and past) and error states", async () => {
    listPublicEvents.mockResolvedValue({ events: [], categories: [] });
    render(<EventsList navigateTo={nav()} />);
    expect(await screen.findByText(/no upcoming events/i)).toBeTruthy();
    listPublicEvents.mockRejectedValue(failure(503, "Service unavailable"));
    fireEvent.click(screen.getByRole("tab", { name: /past/i }));
    expect((await screen.findByRole("alert")).textContent).toBe("Service unavailable");
    expect(screen.getByRole("button", { name: /try again/i })).toBeTruthy();
  });

  it("badges follow capabilities: sign-in required and closed", async () => {
    listPublicEvents.mockResolvedValue({ events: [
      makeListing({ title: "Account Only", guestsAllowed: false, capabilities: { guestCheckout: { enabled: false }, authentication: { required: true } } }),
      makeListing({ eventId: "c", slug: "c", title: "Shut", status: "closed" }),
    ], categories: [] });
    render(<EventsList navigateTo={nav()} />);
    expect(await screen.findByText("Sign-in required")).toBeTruthy();
    expect(screen.getByText("Registration is closed")).toBeTruthy();
  });

  it("links to the organisation's site and shows no cloud-vendor promotion", async () => {
    listPublicEvents.mockResolvedValue({ events: [], categories: [] });
    render(<HomePage navigateTo={nav()} />);
    await screen.findByText(/no upcoming events/i);
    const links = screen.getAllByRole("link").filter((a) => a.getAttribute("href") === "https://www.wmfjapan.org/");
    expect(links.length).toBeGreaterThan(0);
    links.forEach((a) => expect(a.getAttribute("rel")).toContain("noopener"));
    expect(document.body.textContent).not.toMatch(/host your event/i);
  });
});

describe("event detail page", () => {
  const show = async (event, extra = {}) => {
    getPublicEvent.mockResolvedValue({ event, ...extra });
    const navigateTo = nav();
    render(<EventPage navigateTo={navigateTo} slug={event.slug} />);
    await screen.findByRole("heading", { level: 1, name: event.title });
    return navigateTo;
  };

  it("renders details, tickets, prices and the register button from platform data", async () => {
    const navigateTo = await show(FESTIVAL);
    expect(screen.getByText("Festival feast")).toBeTruthy();
    expect(screen.getAllByText("¥2,500").length).toBeGreaterThan(0);
    expect(screen.getByText(/Sample Hall/)).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: /^register$/i }));
    expect(navigateTo).toHaveBeenCalledWith("/events/autumn-festival/register");
    expect(document.title).toContain("Autumn Festival 2027");
  });

  it.each([
    ["sold out", makeEvent({ availability: { full: true } }), /sold out/i, false],
    ["closed", makeEvent({ status: "closed" }), /registration is closed/i, false],
    ["not open yet", makeEvent({ registration: { window: "not_open" }, registrationOpensAt: "2027-01-01T09:00:00+09:00" }), /registration opens/i, false],
    ["cancelled", makeEvent({ status: "cancelled" }), /has been cancelled/i, false],
    ["registered somewhere else", makeEvent({ source: "legacy", tickets: [] }), /not available on this website/i, false],
  ])("%s: explains why and offers no register button", async (_name, event, message) => {
    await show(event);
    expect(screen.getByText(message)).toBeTruthy();
    expect(screen.queryByRole("button", { name: /^register$/i })).toBeNull();
  });

  it("waiting list when full and enabled", async () => {
    await show(makeEvent({ availability: { full: true }, registration: { waitlist: true } }));
    expect(screen.getByRole("button", { name: /join the waiting list/i })).toBeTruthy();
  });

  it("an unpublished or unknown event is 'not found', never its details", async () => {
    getPublicEvent.mockRejectedValue(failure(404, "Event not found"));
    render(<EventPage navigateTo={nav()} slug="draft-event" />);
    expect((await screen.findByRole("alert")).textContent).toMatch(/couldn't find that event/i);
  });

  it("API errors are shown and can be retried", async () => {
    getPublicEvent.mockRejectedValueOnce(failure(500, "Request failed")).mockResolvedValue({ event: makeEvent() });
    render(<EventPage navigateTo={nav()} slug="sample-gathering" />);
    expect((await screen.findByRole("alert")).textContent).toBe("Request failed");
    fireEvent.click(screen.getByRole("button", { name: /try again/i }));
    expect(await screen.findByRole("heading", { level: 1, name: "Sample Gathering" })).toBeTruthy();
  });

  it("members-only events point to membership, sign-in-required events to sign-in", async () => {
    await show(makeEvent({ access: { membersOnly: true, guestsAllowed: false }, registration: { allowed: false, reason: "login_required" } }));
    expect(screen.getByRole("button", { name: /sign in to register/i })).toBeTruthy();
    expect(screen.getByRole("button", { name: /join or renew your membership/i })).toBeTruthy();
  });

  describe("per-event add-ons (seat reservation, zone upgrade, entry slots, dinner)", () => {
    const legacy = (caps) => makeEvent({ source: "legacy", tickets: [], capabilities: caps });
    const labels = { seatReservation: "Seat reservation", zoneUpgrade: "Zone upgrade", entrySlots: "Entry slots", celebrityDinner: "Dinner" };

    it.each(Object.keys(labels))("%s appears only for an event that enables it", async (key) => {
      const off = legacy({ seatReservation: { enabled: false }, zoneUpgrade: { enabled: false }, entrySlots: { enabled: false }, celebrityDinner: { enabled: false } });
      await show(off, { legacy: true });
      expect(screen.queryByText(labels[key])).toBeNull();
      expect(document.querySelector(`[data-addon="${key}"]`)).toBeNull();
    });

    it.each(Object.keys(labels))("%s is shown (and only it) when enabled", async (key) => {
      await show(legacy({ [key]: { enabled: true } }), { legacy: true });
      expect(document.querySelector(`[data-addon="${key}"]`)).toBeTruthy();
      expect(document.querySelectorAll("[data-addon]")).toHaveLength(1);
    });

    it("an event record from before capabilities existed shows no add-ons (entry slots excepted for the original pipeline)", async () => {
      const old = makeEvent({ capabilities: undefined });
      delete old.capabilities;
      await show(old);
      expect(document.querySelectorAll("[data-addon]")).toHaveLength(0);
    });

    it("a platform-native event never lists add-ons that are off", async () => {
      await show(FESTIVAL);
      expect(screen.queryByText(/options for registered attendees/i)).toBeNull();
    });
  });
});
