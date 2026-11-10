import { getRegistrantToken, getRegistrantEmail, handleRegistrantUnauthorized } from "./registrantAuth";
import { getStoredAdminEmail, notifyAdminUnauthorized } from "./adminSession";

const API_BASE_URL = import.meta.env.VITE_API_BASE_URL;
const VISIT_LOG_DEDUPE_WINDOW_MS = 1500;
const recentVisitLogs = new Map();

function getStoredValue(getter) {
  try {
    return getter() || "";
  } catch {
    return "";
  }
}

function getOrCreateVisitorId() {
  const storageKey = "nk-visitor-id";
  let visitorId = getStoredValue(() => localStorage.getItem(storageKey));
  if (visitorId) return visitorId;

  visitorId = globalThis.crypto?.randomUUID?.() || `visitor-${Date.now()}-${Math.random().toString(16).slice(2)}`;
  try {
    localStorage.setItem(storageKey, visitorId);
  } catch {
    // no-op
  }
  return visitorId;
}

function shouldSkipVisitLog(signature) {
  const now = Date.now();
  const lastLoggedAt = recentVisitLogs.get(signature);
  if (lastLoggedAt && now - lastLoggedAt < VISIT_LOG_DEDUPE_WINDOW_MS) {
    return true;
  }
  recentVisitLogs.set(signature, now);
  return false;
}

export async function logPageVisit({ path, referrer = "" } = {}) {
  const normalizedPath = String(path || window.location.pathname || "/");
  const visitorId = getOrCreateVisitorId();
  const registrantEmail = getStoredValue(getRegistrantEmail);
  const adminEmail = getStoredValue(getStoredAdminEmail);
  const principalEmail = adminEmail || registrantEmail;
  const visitorType = adminEmail ? "admin" : registrantEmail ? "registrant" : "anonymous";
  const signature = `${visitorId}:${principalEmail}:${normalizedPath}`;

  if (shouldSkipVisitLog(signature)) return;

  const payload = {
    path: normalizedPath,
    referrer: String(referrer || document.referrer || ""),
    title: document.title || "",
    visitorId,
    visitorType,
    principalEmail,
    userAgent: navigator.userAgent || "",
    viewport: {
      width: window.innerWidth,
      height: window.innerHeight,
    },
  };

  const body = JSON.stringify(payload);
  const endpoint = `${API_BASE_URL.replace(/\/$/, "")}/visits`;

  try {
    await fetch(endpoint, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body,
      mode: "cors",
      credentials: "omit",
      keepalive: true,
    });
  } catch {
    // Logging must never block navigation.
  }
}

export async function submitRegistration(eventType, payload) {
  const response = await fetch(`${API_BASE_URL}/events/${eventType}/register`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });

  let data = {};
  try {
    data = await response.json();
  } catch {
    data = {};
  }

  if (!response.ok) {
    const err = new Error(data.error || "Registration failed");
    err.fields = data.fields || {};
    err.status = response.status;
    throw err;
  }

  return data;
}

export async function getAvailableEntrySlots(eventType, numberOfSeats = 1) {
  const query = new URLSearchParams({
    eventType,
  });

  const response = await fetch(`${API_BASE_URL}/registrations/available-slots?${query.toString()}`, {
    method: "GET",
    headers: { "Content-Type": "application/json" },
  });

  let data = {};
  try {
    data = await response.json();
  } catch {
    data = {};
  }

  if (!response.ok) {
    const err = new Error(data.error || "Could not load entry slots");
    err.status = response.status;
    throw err;
  }

  return data.availableSlots || [];
}

// Celebrity Event seat map (public). Distinct from entry slots: this returns
// individually identified seats ("event"/"combo" registrations only), not an
// aggregate capacity pool.
export async function getAvailableSeats(eventType) {
  const response = await fetch(`${API_BASE_URL}/events/${encodeURIComponent(eventType)}/seats`, {
    method: "GET",
    headers: { "Content-Type": "application/json" },
  });

  let data = {};
  try {
    data = await response.json();
  } catch {
    data = {};
  }

  if (!response.ok) {
    const err = new Error(data.error || "Could not load the seat map");
    err.status = response.status;
    throw err;
  }

  return {
    totalSeats: data.totalSeats || 0,
    layout: data.layout || [],
    bookedSeatIds: data.bookedSeatIds || [],
    zoneTotals: data.zoneTotals || null,
    zoneAvailable: data.zoneAvailable || null,
    zonePricing: data.zonePricing || null,
    specialSeats: data.specialSeats || {},
    walkwayAfterRow: data.walkwayAfterRow ?? 10,
    available: Boolean(data.available),
  };
}

// ── Public registration login (email OTP) ──────────────────────────────────
//
// While the auth endpoints aren't deployed, mock mode resolves locally so the
// login flow is usable end-to-end. In mock mode the accepted code is "000000".
// Set VITE_ONAM_MOCK="false" once /auth/request-otp & /auth/verify-otp are live.
const AUTH_MOCK = import.meta.env.VITE_ONAM_MOCK !== "false";
const MOCK_OTP_CODE = "000000";

export async function requestLoginOtp(email) {
  if (AUTH_MOCK) {
    // Simulate the round-trip; the code is fixed in mock mode.
    await new Promise((resolve) => setTimeout(resolve, 400));
    // Set localStorage "nk-mock-unknown" to any email to exercise the
    // not-registered path locally.
    let unknown = "";
    try {
      unknown = localStorage.getItem("nk-mock-unknown") || "";
    } catch {
      unknown = "";
    }
    if (unknown && unknown.trim().toLowerCase() === email.trim().toLowerCase()) {
      const err = new Error("We couldn't find a membership for this email. Please register first.");
      err.status = 404;
      err.notRegistered = true;
      err.registerUrl = "https://www.wmfjapan.com/en/member/register";
      throw err;
    }
    return { message: "A verification code has been sent (mock: use 000000).", cooldown: 30, mock: true };
  }

  const response = await fetch(`${API_BASE_URL}/auth/request-otp`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ email }),
  });

  let data = {};
  try {
    data = await response.json();
  } catch {
    data = {};
  }

  if (!response.ok) {
    const err = new Error(data.error || "Could not send verification code");
    err.status = response.status;
    err.notRegistered = !!data.notRegistered;
    err.registerUrl = data.registerUrl || "";
    throw err;
  }

  return data;
}

export async function verifyLoginOtp(email, code) {
  if (AUTH_MOCK) {
    await new Promise((resolve) => setTimeout(resolve, 400));
    if (code !== MOCK_OTP_CODE) {
      const err = new Error("Incorrect code. In mock mode the code is 000000.");
      err.status = 400;
      throw err;
    }
    return { token: `mock.${btoa(email)}`, email, expiresIn: 86400, mock: true };
  }

  const response = await fetch(`${API_BASE_URL}/auth/verify-otp`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ email, code }),
  });

  let data = {};
  try {
    data = await response.json();
  } catch {
    data = {};
  }

  if (!response.ok) {
    const err = new Error(data.error || "Verification failed");
    err.status = response.status;
    throw err;
  }

  return data;
}

// ── NPO membership status ───────────────────────────────────────────────────
//
// After login, the registration form asks the backend whether the signed-in
// email is a current NPO member. The reg access token (from OTP verify) rides
// in the Authorization header; the membership Lambda verifies it and returns
// only that email's record.
//
// Mock mode short-circuits with a canned response. To exercise the member and
// renewal paths locally without a backend, set localStorage "nk-mock-npo" to:
//   "member"  → valid member whose period covers today (member pricing + renew)
//   "renewed" → valid member already covered for the renewal year (no upsell)
//   anything else / unset → non-member (offer to join)
function mockMembershipStatus() {
  let scenario = "none";
  try {
    scenario = localStorage.getItem("nk-mock-npo") || "none";
  } catch {
    scenario = "none";
  }
  const renewalYear = "2026-27";
  if (scenario === "member") {
    return {
      isMember: true,
      npoFlagActive: true,
      memberId: "1001",
      validUntil: "2026-06-30",
      coversRenewal: false,
      memberName: "",
      organization: "",
      renewalYear,
      canJoin: false,
      canRenew: true,
      mock: true,
    };
  }
  if (scenario === "renewed") {
    return {
      isMember: true,
      npoFlagActive: true,
      memberId: "1001",
      validUntil: "2027-06-30",
      coversRenewal: true,
      memberName: "",
      organization: "",
      renewalYear,
      canJoin: false,
      canRenew: false,
      mock: true,
    };
  }
  return {
    isMember: false,
    npoFlagActive: false,
    memberId: null,
    validUntil: null,
    coversRenewal: false,
    memberName: "",
    organization: "",
    renewalYear,
    canJoin: true,
    canRenew: false,
    mock: true,
  };
}

// The navbar and the page both ask for the member's status on load, and the
// page can start the request before its code has even finished loading
// (prefetchMembershipStatus). One in-flight/fresh request is shared for a few
// seconds; anything that changes membership data clears it.
const STATUS_SHARE_MS = 5000;
let statusShare = { promise: null, at: 0 };

function clearMembershipStatusCache() {
  statusShare = { promise: null, at: 0 };
}

export function prefetchMembershipStatus() {
  getMembershipStatus().catch(() => {});
}

export function getMembershipStatus({ fresh = false } = {}) {
  if (fresh) clearMembershipStatusCache();
  const now = Date.now();
  if (statusShare.promise && now - statusShare.at < STATUS_SHARE_MS) return statusShare.promise;
  const promise = fetchMembershipStatus();
  statusShare = { promise, at: now };
  promise.catch(() => {
    if (statusShare.promise === promise) clearMembershipStatusCache();
  });
  return promise;
}

async function fetchMembershipStatus() {
  if (AUTH_MOCK) {
    await new Promise((resolve) => setTimeout(resolve, 300));
    return {
      ...mockMembershipStatus(),
      agm: {
        enabled: true, eligible: true, agmId: "agm-2026", title: "WMF Japan AGM 2026", date: "2026-11-15",
        startTime: "18:00", endTime: "21:00", venue: "Tobu Kuminkan, Edogawa-ku", venueJa: "江戸川区 東部区民館",
        access: "5-minute walk from Mizue Station (Toei Shinjuku Line)", mapUrl: "https://maps.app.goo.gl/kfPxK8nd5YHrrfiA9",
        remoteNote: "Remote participation will also be available.", refreshmentsNote: "Free refreshments for those attending in person.",
        notes: "", eligibility: "member", registration: null,
      },
      pendingPayment: (() => {
        try {
          return String(localStorage.getItem("nk-mock-npo")).startsWith("pending")
            ? { action: "join", fee: 1000, reportedAt: localStorage.getItem("nk-mock-reported") || "", attemptFailed: localStorage.getItem("nk-mock-npo") === "pending-failed", paymentMethod: { key: "square", label: "Credit / Debit Card", url: "https://example.com/pay", instructions: "" } }
            : null;
        } catch {
          return null;
        }
      })(),
      fee: 1000,
      renewalFrom: "2026-11-01",
      renewalTo: "2027-10-31",
      paymentMethods: [
        { key: "square", label: "Credit / Debit Card", url: "", instructions: "" },
        { key: "bank", label: "Bank Transfer", url: "", instructions: "Example Bank, Branch 001\nAccount 1234567" },
      ],
    };
  }

  const token = getRegistrantToken();
  if (!token) throw sessionExpiredError();
  const response = await fetch(`${API_BASE_URL}/membership/status`, {
    method: "GET",
    headers: { Authorization: token },
  });

  let data = {};
  try {
    data = await response.json();
  } catch {
    data = {};
  }

  if (!response.ok) {
    handleRegistrantUnauthorized(response.status);
    const err = new Error(data.error || "Could not load membership status");
    err.status = response.status;
    throw err;
  }

  return data;
}

// My Page: join or renew NPO membership with one of the admin-enabled payment
// methods. The server re-derives eligibility and the fee; only the method is sent.
export async function applyNpoMembership(paymentMethod, { spouseEmail = "", agm = null, consent = null } = {}) {
  clearMembershipStatusCache();
  if (AUTH_MOCK) {
    await new Promise((resolve) => setTimeout(resolve, 400));
    return {
      ok: true, year: "2026-27", fee: spouseEmail ? 2000 : 1000,
      lines: [{ label: "Your membership (new)", amount: 1000 }, ...(spouseEmail ? [{ label: `Spouse membership (${spouseEmail})`, amount: 1000 }] : [])],
      paymentMethod: { key: paymentMethod, label: paymentMethod, url: "", instructions: "" },
      agmSaved: agm ? true : null,
    };
  }
  return postRegistrant("/membership/apply", { paymentMethod, spouseEmail, agm, consent }, "Could not complete your membership request");
}

// Shared POST for the signed-in member routes below. Only the choices are sent;
// the server re-derives eligibility, fees and totals.
async function postRegistrant(path, payload, fallbackMessage) {
  const token = getRegistrantToken();
  if (!token) throw sessionExpiredError();
  const response = await fetch(`${API_BASE_URL}${path}`, {
    method: "POST",
    headers: { Authorization: token, "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  let data = {};
  try {
    data = await response.json();
  } catch {
    data = {};
  }
  if (!response.ok) {
    handleRegistrantUnauthorized(response.status);
    const err = new Error(data.error || fallbackMessage);
    err.status = response.status;
    throw err;
  }
  return data;
}

// PayPay / bank transfer: tell us "I've paid" (or take it back). The committee still
// confirms the payment; this only changes what the member sees and flags the order.
export async function reportNpoPayment(reported) {
  clearMembershipStatusCache();
  if (AUTH_MOCK) {
    await new Promise((resolve) => setTimeout(resolve, 300));
    try {
      localStorage.setItem("nk-mock-reported", reported ? new Date().toISOString() : "");
    } catch {
      // mock only
    }
    return { ok: true };
  }
  return postRegistrant("/membership/apply", { reportPaid: reported }, "Could not save that. Please try again.");
}

// Start a fresh Square checkout for a card payment that failed. → { checkoutUrl } or { alreadyPaid }
export async function retryNpoPayment() {
  clearMembershipStatusCache();
  if (AUTH_MOCK) {
    await new Promise((resolve) => setTimeout(resolve, 300));
    return { ok: true, checkoutUrl: "https://example.com/pay-again" };
  }
  return postRegistrant("/membership/apply", { retry: true }, "Could not restart your card payment");
}

// Register / update / cancel ({ cancel: true }) the member's AGM attendance.
export async function saveAgmRegistration(payload) {
  clearMembershipStatusCache();
  if (AUTH_MOCK) {
    await new Promise((resolve) => setTimeout(resolve, 300));
    if (payload.cancel) return { ok: true, registration: null };
    const adultEmails = payload.adultEmails || [];
    const young = ["teens", "kids", "preschoolers", "toddlers"].reduce((n, k) => n + Number(payload[k] || 0), 0);
    return {
      ok: true,
      registration: {
        ...payload,
        adults: 1 + adultEmails.length,
        adultAttendees: adultEmails.map((email) => ({ email, name: email.split("@")[0], status: "member" })),
        totalAttendees: 1 + adultEmails.length + young,
      },
    };
  }
  return postRegistrant("/membership/agm", payload, "Could not save your AGM registration");
}

// Is this email an NPO member who may attend the AGM as an additional adult?
// Mock: "unknown*" = not an NK member, "lapsed*" = not an NPO member, "late*" = payment pending.
export async function checkAgmAdult(email) {
  if (AUTH_MOCK) {
    await new Promise((resolve) => setTimeout(resolve, 250));
    const e = String(email || "").trim().toLowerCase();
    if (e.startsWith("unknown")) return { valid: false, status: "not_nk_member", message: "This email isn't a WMF Japan member. Please ask them to become an NK member and join the NPO first." };
    if (e.startsWith("lapsed")) return { valid: false, status: "not_member", message: "This person isn't an NPO member for the AGM date. They need to join or renew first." };
    return { valid: true, status: e.startsWith("late") ? "pending" : "member" };
  }
  return postRegistrant("/membership/check", { email, detail: true, forAgm: true }, "Could not check that email");
}

// What would adding this spouse email do? { status, valid, fee, message }
export async function checkSpouseMembership(email) {
  if (AUTH_MOCK) {
    await new Promise((resolve) => setTimeout(resolve, 250));
    const e = String(email || "").trim().toLowerCase();
    if (!/^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(e)) return { valid: false, status: "invalid", message: "Enter a valid email address for your spouse." };
    if (e.startsWith("unknown")) {
      return {
        email: e, valid: false, status: "not_member", registerUrl: "https://www.wmfjapan.com/en/member/register",
        message: "This email isn't registered as a WMF Japan member yet. Please request your spouse to become an NK member first, then add them here.",
      };
    }
    return { email: e, valid: true, status: e.startsWith("member") ? "renew" : "join", fee: 1000 };
  }
  return postRegistrant("/membership/check", { email, detail: true }, "Could not check that email");
}

// Public, unauthenticated: is a given email a current NPO member? Used to
// preview the per-adult discount for additional adults during registration.
// Returns only a boolean; the register Lambda re-checks every email server-side.
// In mock mode, emails listed (comma/space separated) in localStorage
// "nk-mock-npo-emails" are treated as members.
export async function checkNpoMembership(email) {
  const normalized = String(email || "").trim().toLowerCase();
  if (!normalized) return false;

  if (AUTH_MOCK) {
    await new Promise((resolve) => setTimeout(resolve, 200));
    let raw = "";
    try {
      raw = localStorage.getItem("nk-mock-npo-emails") || "";
    } catch {
      raw = "";
    }
    const members = raw
      .split(/[\s,]+/)
      .map((entry) => entry.trim().toLowerCase())
      .filter(Boolean);
    return members.includes(normalized);
  }

  try {
    const response = await fetch(`${API_BASE_URL}/membership/check`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ email: normalized }),
    });
    if (!response.ok) return false;
    const data = await response.json();
    return !!data.isMember;
  } catch {
    return false;
  }
}

// ── Registrant self-service (own registrations, reg-token gated) ─────────────
//
// The reg access token (from OTP verify) rides in the Authorization header; the
// my_registrations Lambda verifies it and scopes every result to the caller's
// own email. In mock mode these resolve against localStorage so the manage flow
// (list / edit / delete) is usable end-to-end without a backend.
const MY_REG_MOCK_KEY = "nk-mock-registrations";

function readMockRegistrations() {
  try {
    const raw = localStorage.getItem(MY_REG_MOCK_KEY);
    const parsed = raw ? JSON.parse(raw) : [];
    return Array.isArray(parsed) ? parsed : [];
  } catch {
    return [];
  }
}

function writeMockRegistrations(list) {
  try {
    localStorage.setItem(MY_REG_MOCK_KEY, JSON.stringify(list));
  } catch {
    // storage disabled — mock persistence is best-effort only.
  }
}

// Persist a submission locally in mock mode so the one-per-member gate and the
// manage page have something to read. Called by the registration flow.
export function saveMockRegistration(record) {
  if (!AUTH_MOCK) return;
  const email = String(record.email || "").trim().toLowerCase();
  const list = readMockRegistrations().filter(
    (r) => String(r.email || "").trim().toLowerCase() !== email
  );
  list.push({
    ...record,
    email,
    registrationId: record.registrationId || `mock-${btoa(email)}`,
    submittedAt: record.submittedAt || new Date().toISOString(),
    updatedAt: new Date().toISOString(),
  });
  writeMockRegistrations(list);
}

function sessionExpiredError() {
  const err = new Error("Your session has expired. Please sign in again.");
  err.status = 401;
  err.fields = {};
  return err;
}

async function requestRegistrantJson(path, options = {}) {
  const token = getRegistrantToken();
  // No (or an expired) token can only earn a 401 — skip the round trip.
  if (!token) throw sessionExpiredError();
  const response = await fetch(`${API_BASE_URL}${path}`, {
    ...options,
    headers: {
      Authorization: token,
      "Content-Type": "application/json",
      ...(options.headers || {}),
    },
  });

  let data = {};
  try {
    data = await response.json();
  } catch {
    data = {};
  }

  if (!response.ok) {
    handleRegistrantUnauthorized(response.status);
    const err = new Error(data.error || "Request failed");
    err.fields = data.fields || {};
    err.status = response.status;
    throw err;
  }

  return data;
}

export async function getMyRegistrations() {
  if (AUTH_MOCK) {
    await new Promise((resolve) => setTimeout(resolve, 250));
    const email = (getRegistrantEmail() || "").trim().toLowerCase();
    return readMockRegistrations()
      .filter((r) => String(r.email || "").trim().toLowerCase() === email)
      .sort((a, b) => String(b.submittedAt || "").localeCompare(String(a.submittedAt || "")));
  }
  const data = await requestRegistrantJson("/my/registrations", { method: "GET" });
  return data.registrations || [];
}

export async function updateMyRegistration(registrationId, payload) {
  if (AUTH_MOCK) {
    await new Promise((resolve) => setTimeout(resolve, 250));
    const list = readMockRegistrations();
    const idx = list.findIndex((r) => r.registrationId === registrationId);
    if (idx === -1) {
      const err = new Error("Registration not found");
      err.status = 404;
      throw err;
    }
    list[idx] = { ...list[idx], ...payload, registrationId, updatedAt: new Date().toISOString() };
    writeMockRegistrations(list);
    return list[idx];
  }
  const data = await requestRegistrantJson(
    `/my/registrations/${encodeURIComponent(registrationId)}`,
    { method: "PUT", body: JSON.stringify(payload) }
  );
  return data.registration;
}

// Change the entry-slot allocation(s) on the caller's own registration. Allowed
// regardless of paymentStatus — unlike the full edit flow, this never reprices
// the order. `allocations` is a list of { slotId, seatCount }.
export async function changeMyRegistrationAllocations(registrationId, allocations) {
  if (AUTH_MOCK) {
    await new Promise((resolve) => setTimeout(resolve, 250));
    const list = readMockRegistrations();
    const idx = list.findIndex((r) => r.registrationId === registrationId);
    if (idx === -1) {
      const err = new Error("Registration not found");
      err.status = 404;
      throw err;
    }
    list[idx] = {
      ...list[idx],
      slotId: allocations[0]?.slotId || "",
      allocations,
      updatedAt: new Date().toISOString(),
    };
    writeMockRegistrations(list);
    return list[idx];
  }
  const data = await requestRegistrantJson(
    `/my/registrations/${encodeURIComponent(registrationId)}/slot`,
    { method: "PUT", body: JSON.stringify({ allocations }) }
  );
  return data.registration;
}

// Celebrity Event seat *purchases* on the caller's own registration — seats
// are individually identified chairs a registrant pays for (family/person
// pricing, PayPay/Square/bank checkout), not an aggregate slot pool, and
// only apply to "event"/"combo" registrations. Like getAvailableSeats(),
// deliberately NO mock branch: pricing/checkout genuinely need the real
// zone-pricing and payment-provider backend, so there's nothing meaningful
// to simulate against localStorage.

export async function getMySeatPurchases(registrationId) {
  return requestRegistrantJson(
    `/my/registrations/${encodeURIComponent(registrationId)}/seat-purchases`,
    { method: "GET" }
  );
}

export async function createMySeatPurchase(registrationId, { seatIds, pricingMode, paymentMethod }) {
  const data = await requestRegistrantJson(
    `/my/registrations/${encodeURIComponent(registrationId)}/seat-purchases`,
    { method: "POST", body: JSON.stringify({ seatIds, pricingMode, paymentMethod }) }
  );
  return data.purchase;
}

export async function getMySeatPurchase(registrationId, purchaseId) {
  const data = await requestRegistrantJson(
    `/my/registrations/${encodeURIComponent(registrationId)}/seat-purchases/${encodeURIComponent(purchaseId)}`,
    { method: "GET" }
  );
  return data.purchase;
}

export async function cancelMySeatPurchase(registrationId, purchaseId) {
  const data = await requestRegistrantJson(
    `/my/registrations/${encodeURIComponent(registrationId)}/seat-purchases/${encodeURIComponent(purchaseId)}`,
    { method: "DELETE" }
  );
  return data.purchase;
}

// Returns {checkoutUrl, provider} — caller redirects window.location to it.
export async function checkoutMySeatPurchase(registrationId, purchaseId, provider) {
  return requestRegistrantJson(
    `/my/registrations/${encodeURIComponent(registrationId)}/seat-purchases/${encodeURIComponent(purchaseId)}/checkout`,
    { method: "POST", body: JSON.stringify({ provider }) }
  );
}

// Celebrity Dinner add-on tied to one of the caller's own registrations.
export async function getMyCelebrityDinner(registrationId) {
  return requestRegistrantJson(
    `/my/registrations/${encodeURIComponent(registrationId)}/celebrity-dinner`,
    { method: "GET" }
  );
}

// Each age category splits into veg/non-veg sub-counts — guestCounts carries
// keys like "adultsVeg"/"adultsNonVeg", mapped here onto the numberOf{Category}
// {Veg,NonVeg} fields the backend expects (see my_registrations/handler.py's
// _parse_dinner_guest_counts).
function dinnerGuestCountsToBody(guestCounts) {
  return {
    numberOfAdultsVeg: guestCounts.adultsVeg,
    numberOfAdultsNonVeg: guestCounts.adultsNonVeg,
    numberOfKids914Veg: guestCounts.kids914Veg,
    numberOfKids914NonVeg: guestCounts.kids914NonVeg,
    numberOfKids68Veg: guestCounts.kids68Veg,
    numberOfKids68NonVeg: guestCounts.kids68NonVeg,
    numberOfKidsUnder6Veg: guestCounts.kidsUnder6Veg,
    numberOfKidsUnder6NonVeg: guestCounts.kidsUnder6NonVeg,
  };
}

export async function saveMyCelebrityDinner(registrationId, guestCounts, paymentMethod) {
  const data = await requestRegistrantJson(
    `/my/registrations/${encodeURIComponent(registrationId)}/celebrity-dinner`,
    {
      method: "PUT",
      body: JSON.stringify({
        ...dinnerGuestCountsToBody(guestCounts),
        paymentMethod,
      }),
    }
  );
  return data.registration;
}

export async function checkoutMyCelebrityDinner(registrationId, provider) {
  return requestRegistrantJson(
    `/my/registrations/${encodeURIComponent(registrationId)}/celebrity-dinner/checkout`,
    { method: "POST", body: JSON.stringify({ provider }) }
  );
}

export async function cancelMyCelebrityDinner(registrationId) {
  return requestRegistrantJson(
    `/my/registrations/${encodeURIComponent(registrationId)}/celebrity-dinner`,
    { method: "DELETE" }
  );
}

// ── Celebrity Dinner — guest booking (no underlying event registration) ────

export async function getMyGuestCelebrityDinner(eventType) {
  return requestRegistrantJson(
    `/my/celebrity-dinner/${encodeURIComponent(eventType)}`,
    { method: "GET" }
  );
}

export async function saveMyGuestCelebrityDinner(eventType, name, guestCounts, paymentMethod) {
  const data = await requestRegistrantJson(
    `/my/celebrity-dinner/${encodeURIComponent(eventType)}`,
    {
      method: "PUT",
      body: JSON.stringify({
        name,
        ...dinnerGuestCountsToBody(guestCounts),
        paymentMethod,
      }),
    }
  );
  return data.registration;
}

export async function checkoutMyGuestCelebrityDinner(eventType, provider) {
  return requestRegistrantJson(
    `/my/celebrity-dinner/${encodeURIComponent(eventType)}/checkout`,
    { method: "POST", body: JSON.stringify({ provider }) }
  );
}

export async function cancelMyGuestCelebrityDinner(eventType) {
  return requestRegistrantJson(
    `/my/celebrity-dinner/${encodeURIComponent(eventType)}`,
    { method: "DELETE" }
  );
}

export async function deleteMyRegistration(registrationId) {
  if (AUTH_MOCK) {
    await new Promise((resolve) => setTimeout(resolve, 250));
    writeMockRegistrations(
      readMockRegistrations().filter((r) => r.registrationId !== registrationId)
    );
    return { deleted: true, registrationId };
  }
  return requestRegistrantJson(
    `/my/registrations/${encodeURIComponent(registrationId)}`,
    { method: "DELETE" }
  );
}

// api.js is imported by every page, but only admin calls need Cognito — and it
// drags in the AWS SDK. Importing it here keeps it out of the entry bundle.
async function getValidIdToken() {
  const cognito = await import("./cognito");
  return cognito.getValidIdToken();
}

export async function requestAdminJson(path, options = {}) {
  const idToken = await getValidIdToken();

  const response = await fetch(`${API_BASE_URL}${path}`, {
    ...options,
    headers: {
      Authorization: idToken,
      "Content-Type": "application/json",
      ...(options.headers || {}),
    },
  });

  let data = {};
  try {
    data = await response.json();
  } catch {
    data = {};
  }

  if (!response.ok) {
    // API Gateway's Cognito authorizer answers a rejected token with 401.
    if (response.status === 401) notifyAdminUnauthorized();
    const err = new Error(data.error || (response.status === 401 ? "Your admin session has expired. Please sign in again." : "Request failed"));
    err.fields = data.fields || {};
    err.status = response.status;
    throw err;
  }

  return data;
}

// ── Admin Functions ────────────────────────────────────────────────────────

export async function getRegistrations() {
  const allRegistrations = [];
  let lastEvaluatedKey = "";

  do {
    const query = new URLSearchParams({ limit: "500" });
    if (lastEvaluatedKey) {
      query.set("lastEvaluatedKey", lastEvaluatedKey);
    }

    const data = await requestAdminJson(`/admin/registrations?${query.toString()}`, {
      method: "GET",
    });

    allRegistrations.push(...(data.registrations || data.Items || []));
    lastEvaluatedKey = data.lastEvaluatedKey || "";
  } while (lastEvaluatedKey);

  return allRegistrations;
}

export async function exportRegistrationsCSV() {
  const idToken = await getValidIdToken();

  const response = await fetch(`${API_BASE_URL}/admin/export`, {
    method: "GET",
    headers: {
      "Authorization": idToken,
    },
  });

  if (!response.ok) {
    if (response.status === 401) notifyAdminUnauthorized();
    throw new Error("Failed to export registrations");
  }

  const blob = await response.blob();
  return blob;
}

export async function getPartners() {
  const data = await requestAdminJson("/admin/partners", { method: "GET" });
  return data.partners || [];
}

// Public, unauthenticated list of active partners for display on the site.
export async function getPublicPartners() {
  try {
    const response = await fetch(`${API_BASE_URL}/partners`, { method: "GET" });
    if (!response.ok) return [];
    const data = await response.json();
    return data.partners || [];
  } catch {
    return [];
  }
}

export async function savePartner(payload) {
  const data = await requestAdminJson("/admin/partners", {
    method: "POST",
    body: JSON.stringify(payload),
  });

  return data.partner;
}

export async function deletePartner(partnerId) {
  return requestAdminJson(`/admin/partners/${encodeURIComponent(partnerId)}`, {
    method: "DELETE",
  });
}

// ── Admin: Events ──────────────────────────────────────────────────────────

export async function getEvents() {
  const data = await requestAdminJson("/admin/events", { method: "GET" });
  return data.events || [];
}

export async function saveEvent(payload) {
  // POST upserts: the backend preserves createdAt when the eventType exists,
  // so create and update use the same method (no PUT route/CORS entry exists).
  const data = await requestAdminJson("/admin/events", {
    method: "POST",
    body: JSON.stringify(payload),
  });
  return data.event;
}

export async function deleteEvent(eventType) {
  return requestAdminJson(`/admin/events/${encodeURIComponent(eventType)}`, {
    method: "DELETE",
  });
}

// ── Admin: Entry slots ─────────────────────────────────────────────────────

export async function getEntrySlots(eventType) {
  if (!eventType) return [];
  const data = await requestAdminJson(
    `/admin/events/${encodeURIComponent(eventType)}/entry-slots`,
    { method: "GET" }
  );
  return data.slots || [];
}

export async function saveEntrySlot(eventType, payload, slotId = "") {
  const suffix = slotId ? `/${encodeURIComponent(slotId)}` : "";
  const data = await requestAdminJson(
    `/admin/events/${encodeURIComponent(eventType)}/entry-slots${suffix}`,
    {
      method: slotId ? "PATCH" : "POST",
      body: JSON.stringify(payload),
    }
  );
  return data.slot;
}

export async function deleteEntrySlot(eventType, slotId) {
  return requestAdminJson(
    `/admin/events/${encodeURIComponent(eventType)}/entry-slots/${encodeURIComponent(slotId)}`,
    { method: "DELETE" }
  );
}

export async function releaseEntrySlotBuffer(eventType, slotId, numberOfSeats) {
  const data = await requestAdminJson(
    `/admin/events/${encodeURIComponent(eventType)}/entry-slots/${encodeURIComponent(slotId)}/release-buffer`,
    {
      method: "POST",
      body: JSON.stringify({ numberOfSeats }),
    }
  );
  return data.slot;
}

// ── Admin: Celebrity Event seats ──────────────────────────────────────────────

export async function getEventSeats(eventType) {
  if (!eventType) return null;
  return requestAdminJson(`/admin/events/${encodeURIComponent(eventType)}/seats`, {
    method: "GET",
  });
}

// category: "" (generic block) | "camera" | "projector" | "zone1" |
// "zone2" | "zone3" | "regular" (the last four are cosmetic-only overrides —
// the backend keeps the seat bookable, see manage_seats/handler.py).
export async function blockEventSeat(eventType, seatId, reason = "", category = "") {
  const data = await requestAdminJson(`/admin/events/${encodeURIComponent(eventType)}/seats`, {
    method: "POST",
    body: JSON.stringify({ seatId, reason, category }),
  });
  return data.seat;
}

export async function unblockEventSeat(eventType, seatId) {
  return requestAdminJson(
    `/admin/events/${encodeURIComponent(eventType)}/seats/${encodeURIComponent(seatId)}`,
    { method: "DELETE" }
  );
}

// ── Admin: Celebrity Event seat zone pricing ─────────────────────────────────

export async function getZonePricing(eventType) {
  const data = await requestAdminJson(`/admin/events/${encodeURIComponent(eventType)}/seat-pricing`, {
    method: "GET",
  });
  return data.pricing || {};
}

// price: {displayName, pricePerFamily, pricePerPerson, maxFamilySize, status}
export async function saveZonePrice(eventType, category, price) {
  const data = await requestAdminJson(
    `/admin/events/${encodeURIComponent(eventType)}/seat-pricing/${encodeURIComponent(category)}`,
    { method: "PUT", body: JSON.stringify(price) }
  );
  return data.price;
}

export async function deleteZonePrice(eventType, category) {
  return requestAdminJson(
    `/admin/events/${encodeURIComponent(eventType)}/seat-pricing/${encodeURIComponent(category)}`,
    { method: "DELETE" }
  );
}

// ── Admin: Celebrity Event seat purchase management ──────────────────────────

export async function getEventSeatPurchases(eventType) {
  const data = await requestAdminJson(`/admin/events/${encodeURIComponent(eventType)}/seat-purchases`, {
    method: "GET",
  });
  return data.purchases || [];
}

// status: "pending" | "paid" | "refund_requested" | "cancelled"
export async function updateSeatPurchaseStatus(eventType, purchaseId, status) {
  const data = await requestAdminJson(
    `/admin/events/${encodeURIComponent(eventType)}/seat-purchases/${encodeURIComponent(purchaseId)}`,
    { method: "PATCH", body: JSON.stringify({ status }) }
  );
  return data.purchase;
}

// Emails every paid event/combo registrant that seat selection has opened.
// With { reminder: true }, only those who haven't selected any seats yet.
export async function sendSeatSelectionAnnouncement(eventType, { reminder = false } = {}) {
  return requestAdminJson("/admin/registrations/seat-selection-announcement", {
    method: "POST",
    body: JSON.stringify(reminder ? { eventType, reminder: true } : { eventType }),
  });
}

// ── Admin: Celebrity Dinner ────────────────────────────────────────────────

export async function getCelebrityDinnerConfig(eventType) {
  const data = await requestAdminJson(
    `/admin/events/${encodeURIComponent(eventType)}/celebrity-dinner`,
    { method: "GET" }
  );
  return data.config;
}

export async function saveCelebrityDinnerConfig(eventType, payload) {
  const data = await requestAdminJson(
    `/admin/events/${encodeURIComponent(eventType)}/celebrity-dinner`,
    { method: "PUT", body: JSON.stringify(payload) }
  );
  return data.config;
}

export async function getCelebrityDinnerRegistrations(eventType) {
  const data = await requestAdminJson(
    `/admin/events/${encodeURIComponent(eventType)}/celebrity-dinner/registrations`,
    { method: "GET" }
  );
  return data;
}

export async function updateCelebrityDinnerRegistrationStatus(eventType, registrationId, status) {
  const data = await requestAdminJson(
    `/admin/events/${encodeURIComponent(eventType)}/celebrity-dinner/registrations/${encodeURIComponent(registrationId)}`,
    { method: "PATCH", body: JSON.stringify({ status }) }
  );
  return data.registration;
}

// Admin: move a registration to different entry slot(s), regardless of payment
// status. `allocations` is a list of { slotId, seatCount }.
export async function reassignRegistrationSlot(registrationId, allocations) {
  const data = await requestAdminJson("/admin/registrations/reassign", {
    method: "POST",
    body: JSON.stringify({ registrationId, allocations }),
  });
  return data.registration;
}

// Admin: what the "Allocate Seats" modal needs before it opens — this
// registration's own currently-editable seats (its still-open free
// allocation, if any), any other seats it holds that aren't editable here
// (already checked in, or genuinely paid for), the per-registration seat
// allowance, and whether this event's adminOverrideMaxSeats setting lifts
// that cap. { eventType, editableSeatIds, lockedSeatIds, seatsAllowed,
// adminOverrideMaxSeats }.
export async function getRegistrationSeatAllocation(registrationId) {
  return requestAdminJson(
    `/admin/registrations/allocate-seats?registrationId=${encodeURIComponent(registrationId)}`,
    { method: "GET" }
  );
}

// Admin: directly assign specific Celebrity Event hall seats to a
// registration (event/combo only) — bypasses the registrant's own
// self-service booking window/payment-method checks and zone-upgrade
// entitlement gate; `seatIds` is the FULL desired set of editable seats
// (see getRegistrationSeatAllocation), not just seats to add — deselecting
// a previously-allocated seat releases it. The seats are claimed at ¥0 and
// the registrant gets the normal "paid" confirmation email/QR/check-in PDF
// ticket. See SEAT_SELECTION.md's Known Limitations for why this exists.
export async function allocateRegistrationSeats(registrationId, seatIds) {
  const data = await requestAdminJson("/admin/registrations/allocate-seats", {
    method: "POST",
    body: JSON.stringify({ registrationId, seatIds }),
  });
  return data.purchase;
}

export async function resolveEntryPass(token) {
  const data = await requestAdminJson("/admin/check-in", {
    method: "POST",
    body: JSON.stringify({ action: "resolve", token }),
  });
  return data;
}

export async function checkInEntryPass(token) {
  const data = await requestAdminJson("/admin/check-in", {
    method: "POST",
    body: JSON.stringify({ action: "check_in", token }),
  });
  return data;
}

// Celebrity Event seat-purchase check-in — a wholly separate ticket/QR from
// the entry pass above (see plugins/seat-selection/backend/seat_purchase_qr.py).
export async function resolveSeatCheckIn(token) {
  return requestAdminJson("/admin/seat-check-in", {
    method: "POST",
    body: JSON.stringify({ action: "resolve", token }),
  });
}

export async function checkInSeatPurchase(token) {
  return requestAdminJson("/admin/seat-check-in", {
    method: "POST",
    body: JSON.stringify({ action: "check_in", token }),
  });
}

// Admin manual check-in: no QR pass token needed, e.g. the attendee lost
// their pass or it never arrived.
export async function checkInRegistrationById(registrationId) {
  const data = await requestAdminJson("/admin/check-in", {
    method: "POST",
    body: JSON.stringify({ action: "check_in", registrationId }),
  });
  return data;
}

// ── Admin: Registration management ───────────────────────────────────────────

export async function saveRegistration(payload) {
  const data = await requestAdminJson("/admin/registration", {
    method: "POST",
    body: JSON.stringify(payload),
  });
  return data.registration;
}

export async function deleteRegistration(registrationId, { force = false } = {}) {
  // force overrides the backend's check-in lock. The seats already admitted stay
  // reserved on the entry slot either way — they've been used and can't be resold.
  const query = force ? "?force=true" : "";
  return requestAdminJson(
    `/admin/registration/${encodeURIComponent(registrationId)}${query}`,
    { method: "DELETE" }
  );
}

export async function processBulkRegistrations(action, registrationIds) {
  return requestAdminJson("/admin/registrations/bulk", {
    method: "POST",
    body: JSON.stringify({ action, registrationIds }),
  });
}

// ── Admin: Members (master list + NPO status) ────────────────────────────────

export async function getMembers() {
  const data = await requestAdminJson("/admin/users", { method: "GET" });
  return data.members || [];
}

export async function saveMember(data) {
  const result = await requestAdminJson("/admin/members", {
    method: "POST",
    body: JSON.stringify(data),
  });
  return result.member;
}

export async function updateMember(email, data) {
  const result = await requestAdminJson(`/admin/members/${encodeURIComponent(email)}`, {
    method: "PUT",
    body: JSON.stringify(data),
  });
  return result.member;
}

export async function deleteMember(email) {
  return requestAdminJson(`/admin/members/${encodeURIComponent(email)}`, { method: "DELETE" });
}

export async function getNpoTransactions(memberid) {
  const data = await requestAdminJson(
    `/admin/npo-membership/${encodeURIComponent(memberid)}`,
    { method: "GET" }
  );
  return data.transactions || [];
}

export async function saveNpoTransaction(memberid, data) {
  const result = await requestAdminJson(
    `/admin/npo-membership/${encodeURIComponent(memberid)}`,
    { method: "POST", body: JSON.stringify(data) }
  );
  return { ...result.transaction, emailSent: result.emailed === true };
}

export async function updateNpoTransaction(memberid, transactionNo, data) {
  const result = await requestAdminJson(
    `/admin/npo-membership/${encodeURIComponent(memberid)}/${encodeURIComponent(transactionNo)}`,
    { method: "PUT", body: JSON.stringify(data) }
  );
  return { ...result.transaction, emailSent: result.emailed === true };
}

// Cancel a paid membership order and optionally refund it.
// data: { refund: "square" | "manual" | "none", refundAmount, reason, notify }
export async function cancelNpoTransaction(memberid, transactionNo, data) {
  const result = await requestAdminJson(
    `/admin/npo-membership/${encodeURIComponent(memberid)}/${encodeURIComponent(transactionNo)}`,
    { method: "PUT", body: JSON.stringify({ action: "cancel", ...data }) }
  );
  return result;
}

export async function deleteNpoTransaction(memberid, transactionNo) {
  return requestAdminJson(
    `/admin/npo-membership/${encodeURIComponent(memberid)}/${encodeURIComponent(transactionNo)}`,
    { method: "DELETE" }
  );
}

export async function getVisitSummary(days = 30) {
  const safeDays = Math.max(1, Math.min(90, Number(days) || 30));
  const data = await requestAdminJson(`/admin/visits-summary?days=${safeDays}`, { method: "GET" });
  return {
    days: data.days || safeDays,
    range: data.range || { from: "", to: "" },
    totalVisits: data.totalVisits || 0,
    uniqueVisitors: data.uniqueVisitors || 0,
    daily: data.daily || [],
    byPath: data.byPath || [],
    byVisitorType: data.byVisitorType || [],
  };
}

export function downloadCSV(blob, filename = "registrations.csv") {
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  document.body.appendChild(link);
  link.click();
  document.body.removeChild(link);
  URL.revokeObjectURL(url);
}

// ── Site config (carousel images) ─────────────────────────────────────────────

export async function getSiteConfig(eventType = "") {
  try {
    const suffix = eventType ? `?eventType=${encodeURIComponent(eventType)}` : "";
    const response = await fetch(`${API_BASE_URL}/site-config${suffix}`);
    if (!response.ok) return { config: {} };
    const data = await response.json();
    return data;
  } catch {
    return { config: {} };
  }
}

// Reads the File as base64 and POSTs it through our own API Gateway → Lambda → S3.
// No direct cross-origin PUT to S3, so no CORS issue regardless of S3 bucket config.
export async function uploadCarouselImage(slot, file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = async () => {
      try {
        const base64 = reader.result.split(",")[1];
        const data = await requestAdminJson(
          `/admin/site-config/upload-url?slot=${encodeURIComponent(slot)}`,
          {
            method: "POST",
            body: JSON.stringify({ contentType: file.type, data: base64 }),
          }
        );
        resolve(data); // { imageUrl, configKey, updatedAt }
      } catch (err) {
        reject(err);
      }
    };
    reader.onerror = () => reject(new Error("Failed to read image file"));
    reader.readAsDataURL(file);
  });
}

export async function getAdminSiteConfig() {
  const data = await requestAdminJson("/admin/site-config", { method: "GET" });
  return data.config || {};
}

export async function updatePartnersDisplayMode(mode) {
  return requestAdminJson("/admin/site-config", {
    method: "PUT",
    body: JSON.stringify({ setting: "partners_display_mode", value: mode }),
  });
}

export async function updateAdminSiteSetting(setting, value) {
  return requestAdminJson("/admin/site-config", {
    method: "PUT",
    body: JSON.stringify({ setting, value }),
  });
}

// Admin: JSON-valued site settings (NPO fee, site-wide payment methods).
async function getJsonSiteSetting(key) {
  const cfg = await getAdminSiteConfig();
  try {
    const parsed = JSON.parse(cfg[key] || "{}");
    return parsed && typeof parsed === "object" ? parsed : {};
  } catch {
    return {};
  }
}

export const getNpoMembershipConfig = () => getJsonSiteSetting("npo_membership_config");
export const saveNpoMembershipConfig = (config) =>
  updateAdminSiteSetting("npo_membership_config", JSON.stringify(config));
export const getPaymentMethodsConfig = () => getJsonSiteSetting("payment_methods_config");
export const savePaymentMethodsConfig = (config) =>
  updateAdminSiteSetting("payment_methods_config", JSON.stringify(config));

// Admin: member announcements (AGM / join / renew). The send runs in the
// background; getMemberAnnouncements() reports progress per send.
export const previewMemberAnnouncement = (type) =>
  requestAdminJson("/admin/member-announcements", { method: "POST", body: JSON.stringify({ type, dryRun: true }) });
export const sendMemberAnnouncement = (type) =>
  requestAdminJson("/admin/member-announcements", { method: "POST", body: JSON.stringify({ type }) });
export async function getMemberAnnouncements() {
  const data = await requestAdminJson("/admin/member-announcements", { method: "GET" });
  return data.campaigns || [];
}

// Admin: AGMs (agms table) and their registrations (agm_registrations table).
export async function getAgms() {
  const data = await requestAdminJson("/admin/agms", { method: "GET" });
  return data.agms || [];
}

// → { agm, registrations, totals }; works for past AGMs too (read-only record).
export const getAgm = (agmId) => requestAdminJson(`/admin/agms/${encodeURIComponent(agmId)}`, { method: "GET" });

export async function createAgm(agm) {
  const data = await requestAdminJson("/admin/agms", { method: "POST", body: JSON.stringify(agm) });
  return data.agm;
}

// The server refuses edits once the AGM's date has passed.
export async function updateAgm(agmId, agm) {
  const data = await requestAdminJson(`/admin/agms/${encodeURIComponent(agmId)}`, {
    method: "PUT",
    body: JSON.stringify(agm),
  });
  return data.agm;
}

// ── Admin: Payment reconciliation ────────────────────────────────────────────

// Parses the CSV's headers + a sample of rows without persisting anything, so
// the admin can confirm/adjust the suggested column mapping before import.
export async function previewReconciliationTransactions(csvText) {
  return requestAdminJson("/admin/reconciliation/transactions/preview", {
    method: "POST",
    body: JSON.stringify({ csvText }),
  });
}

export async function importReconciliationTransactions({ eventType, sourceType, csvText, columnMapping }) {
  return requestAdminJson("/admin/reconciliation/transactions", {
    method: "POST",
    body: JSON.stringify({ eventType, sourceType, csvText, columnMapping }),
  });
}

export async function getReconciliationTransactions(eventType, sourceType = "") {
  const query = new URLSearchParams({ eventType });
  if (sourceType) query.set("sourceType", sourceType);
  const data = await requestAdminJson(`/admin/reconciliation/transactions?${query.toString()}`, {
    method: "GET",
  });
  return { transactions: data.transactions || [], batches: data.batches || [] };
}

export async function deleteReconciliationBatch(batchId, eventType) {
  const query = new URLSearchParams({ eventType });
  return requestAdminJson(
    `/admin/reconciliation/transactions/${encodeURIComponent(batchId)}?${query.toString()}`,
    { method: "DELETE" }
  );
}

export async function runReconciliation(eventType) {
  return requestAdminJson("/admin/reconciliation/runs", {
    method: "POST",
    body: JSON.stringify({ eventType }),
  });
}

export async function getReconciliationRuns(eventType) {
  const query = new URLSearchParams({ eventType });
  const data = await requestAdminJson(`/admin/reconciliation/runs?${query.toString()}`, {
    method: "GET",
  });
  return data.runs || [];
}

export async function getReconciliationRun(eventType, runId) {
  const query = new URLSearchParams({ eventType });
  return requestAdminJson(`/admin/reconciliation/runs/${encodeURIComponent(runId)}?${query.toString()}`, {
    method: "GET",
  });
}

export async function exportReconciliationRunCSV(eventType, runId) {
  const idToken = await getValidIdToken();
  const query = new URLSearchParams({ eventType, format: "csv" });

  const response = await fetch(
    `${API_BASE_URL}/admin/reconciliation/runs/${encodeURIComponent(runId)}?${query.toString()}`,
    {
      method: "GET",
      headers: { "Authorization": idToken },
    }
  );

  if (!response.ok) {
    if (response.status === 401) notifyAdminUnauthorized();
    throw new Error("Failed to export reconciliation report");
  }

  return response.blob();
}
