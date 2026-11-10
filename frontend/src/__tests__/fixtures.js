// Neutral event fixtures shaped like GET /public/events and GET /public/events/{slug}. Nothing here is a real
// event, person, account or credential.

export const FORM_FIELDS = [
  { key: "fullName", type: "text", label: "Full name", section: "member", source: "profile", required: true, locked: true },
  { key: "email", type: "email", label: "Email", section: "member", source: "profile", required: true, locked: true },
  { key: "phone", type: "phone", label: "Phone", section: "member", source: "profile", required: true, locked: true },
  { key: "consentPrivacy", type: "consent", label: "I agree to the Privacy Policy", section: "consent", source: "user", required: true, locked: true },
  { key: "consentTerms", type: "consent", label: "I agree to the Terms and Conditions", section: "consent", source: "user", required: true, locked: true },
];

export const TICKETS = [
  { ticketTypeId: "adult", name: "Adult", description: "", memberPriceJpy: 1500, nonMemberPriceJpy: 2000, yourPriceJpy: 2000, remaining: 20, perPersonCap: 5 },
  { ticketTypeId: "child", name: "Child", description: "", memberPriceJpy: 0, nonMemberPriceJpy: 0, yourPriceJpy: 0, remaining: null },
];

const capabilities = (over = {}) => ({
  guestCheckout: { enabled: true },
  authentication: { required: false },
  seatReservation: { enabled: false },
  zoneUpgrade: { enabled: false },
  entrySlots: { enabled: false },
  celebrityDinner: { enabled: false },
  ...over,
});

/** A full public event page payload (what GET /public/events/{slug} returns under `event`). */
export function makeEvent(over = {}) {
  const { capabilities: caps, registration, viewer, ...rest } = over;
  return {
    eventId: "ev-1",
    slug: "sample-gathering",
    title: "Sample Gathering",
    status: "published",
    source: "native",
    startsAt: "2026-12-20T10:00:00+09:00",
    endsAt: "2026-12-20T16:00:00+09:00",
    location: { venueName: "Sample Hall", address: "Tokyo" },
    shortDescription: "A neutral sample event.",
    longDescription: "<p>Details go here.</p>",
    category: "Community",
    tags: [],
    design: { layout: "classic", accentColor: "#1a7f5a", sections: [], gallery: [] },
    access: { membersOnly: false, guestsAllowed: true, requireRenewalCover: true },
    waitlistEnabled: false,
    tickets: TICKETS,
    availability: { full: false, eventRemaining: null },
    registration: { window: "open", allowed: true, reason: "ok", renewUrl: false, waitlist: false, ...(registration || {}) },
    viewer: { loggedIn: false, email: "", name: "", memberPricing: false, system: {}, provider: "", ...(viewer || {}) },
    paymentMethods: [{ key: "bank", label: "Bank transfer" }],
    form: { formId: "f1", version: 1, fields: FORM_FIELDS },
    capabilities: capabilities(caps),
    ...rest,
  };
}

/** An event as it appears in the listing. */
export function makeListing(over = {}) {
  const { capabilities: caps, ...rest } = over;
  return {
    eventId: "ev-1", slug: "sample-gathering", title: "Sample Gathering", status: "published", source: "native",
    startsAt: "2026-12-20T10:00:00+09:00", location: { venueName: "Sample Hall" }, category: "Community", tags: [],
    shortDescription: "A neutral sample event.", membersOnly: false, guestsAllowed: true, legacyPath: null,
    capabilities: capabilities(caps), ...rest,
  };
}

/** A harvest-festival style event, like the one Onam would be if an admin created it in the platform. */
export const FESTIVAL = makeEvent({
  eventId: "ev-fest", slug: "autumn-festival", title: "Autumn Festival 2027", category: "Festival",
  tickets: [{ ticketTypeId: "feast", name: "Festival feast", memberPriceJpy: 2250, nonMemberPriceJpy: 2500, yourPriceJpy: 2500, remaining: 40 }],
});

/** Build a fake id token (unsigned: the browser never verifies signatures; the server does). */
export function fakeIdToken(claims = {}) {
  const b64 = (o) => btoa(JSON.stringify(o)).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
  const now = Math.floor(Date.now() / 1000);
  return `${b64({ alg: "none" })}.${b64({
    sub: "sub-123", email: "pat@example.com", email_verified: true, name: "Pat Example",
    aud: "test-client-id", iss: "https://cognito-idp.ap-northeast-1.amazonaws.com/ap-northeast-1_TESTPOOL",
    iat: now, exp: now + 3600, ...claims,
  })}.sig`;
}
