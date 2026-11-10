/**
 * Auth for the public registration gate.
 *
 * A verified registrant's access token lives in localStorage, so it's shared
 * across every tab/window of the same browser and survives closing/reopening
 * it — matches the normal "I'm signed in" expectation, including surviving a
 * PayPay/Square hosted-checkout redirect that hands control to a different
 * browsing context on the way back. The token itself is minted server-side
 * (HMAC, see auth_otp/handler.py) with its own 24h expiry, and there's an
 * explicit Log out action, so this doesn't depend on the tab ever closing.
 *
 * The token's payload half is plain base64url JSON ({ email, exp }), so the
 * SPA can tell an expired session apart from a live one without a round trip.
 * The signature is only checked server-side; a stale token here just means the
 * page gate shows the sign-in screen instead of a page whose API calls 401.
 *
 * The session also ends after an hour without user activity (sessionActivity.js).
 */
import { isIdleExpired, markActivity } from "./sessionActivity";

const TOKEN_KEY = "nk-reg-token";
const EMAIL_KEY = "nk-reg-email";

// Fired on window whenever the registrant session is saved or cleared in this
// tab (other tabs learn about it via the native "storage" event).
export const REGISTRANT_SESSION_EVENT = "nk-registrant-session";

// Treat a token as expired slightly early so a request doesn't race the
// server-side expiry check.
const EXPIRY_SKEW_MS = 30 * 1000;

// Dispatched asynchronously: getRegistrantToken() can clear an expired session
// while a component is rendering, and listeners update React state.
function notifySessionChange() {
  setTimeout(() => {
    try {
      window.dispatchEvent(new Event(REGISTRANT_SESSION_EVENT));
    } catch {
      // no-op (non-browser environment)
    }
  }, 0);
}

function decodeTokenPayload(token) {
  try {
    const payloadPart = String(token).split(".")[0];
    const base64 = payloadPart.replace(/-/g, "+").replace(/_/g, "/");
    const padded = base64 + "=".repeat((4 - (base64.length % 4)) % 4);
    return JSON.parse(atob(padded));
  } catch {
    return null;
  }
}

function isTokenExpired(token) {
  const payload = decodeTokenPayload(token);
  // An unreadable token can't be valid; let the user sign in again.
  if (!payload || !payload.exp) return true;
  return Date.now() >= Number(payload.exp) * 1000 - EXPIRY_SKEW_MS;
}

export function saveRegistrantSession(email, token) {
  markActivity({ force: true });
  try {
    localStorage.setItem(TOKEN_KEY, token);
    localStorage.setItem(EMAIL_KEY, email);
  } catch {
    // Private-mode / storage-disabled: fall back to in-memory only.
  }
  notifySessionChange();
}

function readStoredToken() {
  try {
    return localStorage.getItem(TOKEN_KEY);
  } catch {
    return null;
  }
}

/**
 * The current, unexpired token — or null. An expired token is cleared on read
 * so every caller (page gates, API clients, plugins) sees the same answer.
 */
export function getRegistrantToken() {
  const token = readStoredToken();
  if (!token) return null;
  if (isTokenExpired(token) || isIdleExpired()) {
    clearRegistrantSession();
    return null;
  }
  return token;
}

export function getRegistrantEmail() {
  if (!getRegistrantToken()) return null;
  try {
    return localStorage.getItem(EMAIL_KEY);
  } catch {
    return null;
  }
}

export function isRegistrantAuthenticated() {
  return !!getRegistrantToken();
}

export function clearRegistrantSession() {
  const hadSession = !!readStoredToken();
  try {
    localStorage.removeItem(TOKEN_KEY);
    localStorage.removeItem(EMAIL_KEY);
  } catch {
    // no-op
  }
  if (hadSession) notifySessionChange();
}

/**
 * Call when the server rejects the registrant token (HTTP 401). Clears the
 * local session so the page gate falls back to the sign-in screen.
 */
export function handleRegistrantUnauthorized(status) {
  if (status === 401) clearRegistrantSession();
}

/**
 * Subscribe to registrant sign-in/sign-out, in this tab or any other tab of
 * the same browser. Returns an unsubscribe function.
 */
export function onRegistrantSessionChange(callback) {
  const onStorage = (event) => {
    if (event.key === null || event.key === TOKEN_KEY) callback();
  };
  window.addEventListener(REGISTRANT_SESSION_EVENT, callback);
  window.addEventListener("storage", onStorage);
  return () => {
    window.removeEventListener(REGISTRANT_SESSION_EVENT, callback);
    window.removeEventListener("storage", onStorage);
  };
}
