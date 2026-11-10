/**
 * Admin (Cognito) session bookkeeping that doesn't need the AWS SDK, so the
 * shared api.js can react to an expired admin session without pulling
 * cognito.js (and the SDK) into the entry bundle.
 *
 * Admin and registrant sessions live under different localStorage keys and
 * travel in the Authorization header of different endpoints, so they never
 * overwrite or stand in for each other.
 *
 * The session also ends after an hour without user activity (sessionActivity.js).
 */
import { isIdleExpired, markActivity } from "./sessionActivity";

const ID_TOKEN_KEY = "adminIdToken";
const ACCESS_TOKEN_KEY = "adminAccessToken";
const REFRESH_TOKEN_KEY = "adminRefreshToken";
const EMAIL_KEY = "adminEmail";

export const ADMIN_SESSION_EVENT = "wmf-admin-session";

function read(key) {
  try {
    return localStorage.getItem(key);
  } catch {
    return null;
  }
}

function notifySessionChange() {
  setTimeout(() => {
    try {
      window.dispatchEvent(new Event(ADMIN_SESSION_EVENT));
    } catch {
      // no-op
    }
  }, 0);
}

function isJwtExpired(token) {
  try {
    const base64 = token.split(".")[1].replace(/-/g, "+").replace(/_/g, "/");
    const payload = JSON.parse(atob(base64));
    return Date.now() >= payload.exp * 1000;
  } catch {
    return true;
  }
}

/**
 * True when there is an id token that is still valid, or one that has expired
 * but can be renewed with a stored refresh token. (Whether the refresh token
 * itself is still accepted is only known once Cognito is asked — a rejection
 * lands in notifyAdminUnauthorized().)
 */
export function hasAdminSession() {
  const idToken = read(ID_TOKEN_KEY);
  if (!idToken) return false;
  // An hour without activity ends the session even though the refresh token
  // could still renew it.
  if (isIdleExpired()) {
    clearAdminSession();
    return false;
  }
  return !isJwtExpired(idToken) || !!read(REFRESH_TOKEN_KEY);
}

/** Call right after a successful admin sign-in to start the inactivity clock. */
export function markAdminSignIn() {
  markActivity({ force: true });
}

export function getStoredAdminEmail() {
  return hasAdminSession() ? read(EMAIL_KEY) : null;
}

export function clearAdminSession() {
  const hadSession = !!read(ID_TOKEN_KEY);
  try {
    localStorage.removeItem(ID_TOKEN_KEY);
    localStorage.removeItem(ACCESS_TOKEN_KEY);
    localStorage.removeItem(REFRESH_TOKEN_KEY);
    localStorage.removeItem(EMAIL_KEY);
  } catch {
    // no-op
  }
  if (hadSession) notifySessionChange();
}

/** The API (or Cognito) rejected the admin's credentials: sign them out locally. */
export function notifyAdminUnauthorized() {
  clearAdminSession();
}

/** Subscribe to admin sign-out in this tab or another tab. Returns an unsubscribe. */
export function onAdminSessionChange(callback) {
  const onStorage = (event) => {
    if (event.key === null || event.key === ID_TOKEN_KEY) callback();
  };
  window.addEventListener(ADMIN_SESSION_EVENT, callback);
  window.addEventListener("storage", onStorage);
  return () => {
    window.removeEventListener(ADMIN_SESSION_EVENT, callback);
    window.removeEventListener("storage", onStorage);
  };
}
