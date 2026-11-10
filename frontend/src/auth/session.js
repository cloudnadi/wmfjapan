/**
 * Attendee session: who is signed in, and a valid ID token for API calls.
 *
 * Token storage (documented trade-off — this is a client-only SPA, so there is no server to hold an
 * HTTP-only cookie):
 *   - ID and access tokens live in memory only. They vanish on reload and are never written to storage.
 *   - The refresh token is kept in localStorage so the session survives reloads and restarts, and is
 *     used to mint fresh tokens (REFRESH_TOKEN_AUTH). Script running on this origin could read it, which is
 *     why the app avoids innerHTML on user data, ships no third-party scripts, and the refresh token is
 *     revocable (sign-out calls GlobalSignOut; the app client has token revocation on).
 * No token, code or password is ever logged.
 */
import { AuthError, globalSignOut, refreshAuthResult } from "./cognitoApi";
import { getAuthConfig } from "./config";
import { deviceStore } from "./storage";

export const REFRESH_KEY = "wmf-auth.refresh";
export const META_KEY = "wmf-auth.meta";
const REFRESH_AHEAD_MS = 60 * 1000; // renew a minute before the ID token expires

let state = null; // { idToken, accessToken, expiresAt, user }
let inflight = null;
let initPromise = null;
let endReason = "";
const listeners = new Set();

export function decodeJwt(token) {
  try {
    const part = String(token).split(".")[1];
    const base64 = part.replace(/-/g, "+").replace(/_/g, "/");
    const json = decodeURIComponent(
      atob(base64 + "=".repeat((4 - (base64.length % 4)) % 4))
        .split("")
        .map((c) => `%${c.charCodeAt(0).toString(16).padStart(2, "0")}`)
        .join("")
    );
    return JSON.parse(json);
  } catch {
    return null;
  }
}

function emit() {
  listeners.forEach((listener) => {
    try {
      listener(getSession());
    } catch {
      // a broken listener must not break the others
    }
  });
}

function readMeta() {
  try {
    return JSON.parse(deviceStore.get(META_KEY) || "{}") || {};
  } catch {
    return {};
  }
}

/** Public, token-free view of the session, or null when signed out. */
export function getSession() {
  if (!state) return null;
  return { ...state.user, federated: !!readMeta().federated };
}

export const isSignedIn = () => !!state;

/**
 * Install tokens from a Cognito AuthenticationResult.
 * @param {{IdToken:string, AccessToken?:string, RefreshToken?:string}} result
 */
export function establishSession(result, { federated = false } = {}) {
  const claims = decodeJwt(result?.IdToken);
  if (!claims || !claims.sub) throw new AuthError({ name: "UnsupportedChallenge" });
  state = {
    idToken: result.IdToken,
    accessToken: result.AccessToken || state?.accessToken || "",
    expiresAt: Number(claims.exp) * 1000,
    user: {
      sub: claims.sub,
      email: String(claims.email || "").toLowerCase(),
      emailVerified: String(claims.email_verified) === "true",
      name: claims.name || "",
    },
  };
  if (result.RefreshToken) {
    deviceStore.set(REFRESH_KEY, result.RefreshToken);
    deviceStore.set(META_KEY, JSON.stringify({ federated }));
  }
  endReason = "";
  emit();
  return getSession();
}

function clearLocal(reason = "") {
  const had = !!state || !!deviceStore.get(REFRESH_KEY);
  state = null;
  deviceStore.remove(REFRESH_KEY);
  deviceStore.remove(META_KEY);
  if (reason) endReason = reason;
  if (had) emit();
}

/** Why the last session ended on its own ("expired"), once; used to show a message on the sign-in screen. */
export function consumeEndReason() {
  const reason = endReason;
  endReason = "";
  return reason;
}

async function refresh() {
  const refreshToken = deviceStore.get(REFRESH_KEY);
  if (!refreshToken) return false;
  try {
    const result = await refreshAuthResult(refreshToken);
    establishSession(result, { federated: !!readMeta().federated });
    return true;
  } catch (error) {
    // A rejected refresh token (expired, revoked, user disabled) ends the session. A network blip does not.
    if (error?.code === "network") return false;
    clearLocal("expired");
    return false;
  }
}

function refreshOnce() {
  inflight ||= refresh().finally(() => {
    inflight = null;
  });
  return inflight;
}

/** Restore the session from the stored refresh token (call once at start-up; safe to call again). */
export function initSession() {
  const needsRestore = !state && getAuthConfig().clientId && deviceStore.get(REFRESH_KEY); // already signed in on this page: nothing to restore
  initPromise ||= (needsRestore ? refreshOnce() : Promise.resolve(false)).then(() => getSession());
  return initPromise;
}

/**
 * A valid ID token for the API, or null when signed out. Refreshes shortly before expiry, or on demand.
 */
export async function getIdToken({ forceRefresh = false } = {}) {
  await initSession();
  if (!state && deviceStore.get(REFRESH_KEY)) await refreshOnce(); // a network blip at start-up: try again now
  if (!state) return null;
  if (forceRefresh || state.expiresAt - Date.now() < REFRESH_AHEAD_MS) {
    const ok = await refreshOnce();
    if (!ok && (!state || state.expiresAt <= Date.now())) return null;
  }
  return state ? state.idToken : null;
}

/**
 * Sign out on this device. Returns {hostedLogoutUrl} when the sign-in went through the Hosted UI, so the
 * caller can also end that session by navigating there.
 */
export async function signOut() {
  const accessToken = state?.accessToken;
  const federated = !!readMeta().federated;
  clearLocal();
  initPromise = null;
  if (accessToken) {
    try {
      await globalSignOut(accessToken); // revokes the refresh token everywhere; best effort
    } catch {
      // already signed out locally; the token still expires on its own
    }
  }
  const config = getAuthConfig();
  if (federated && config.domain && config.clientId) {
    const url = new URL(`https://${config.domain}/logout`);
    url.searchParams.set("client_id", config.clientId);
    url.searchParams.set("logout_uri", config.logoutUri);
    return { hostedLogoutUrl: url.toString() };
  }
  return { hostedLogoutUrl: "" };
}

export function onAuthChange(listener) {
  listeners.add(listener);
  const onStorage = (event) => {
    if (event.key !== REFRESH_KEY) return;
    if (event.newValue === null) {
      // Signed out in another tab.
      if (state) {
        state = null;
        emit();
      }
    } else if (!state) {
      initPromise = null;
      initSession().then(emit);
    }
  };
  window.addEventListener("storage", onStorage);
  return () => {
    listeners.delete(listener);
    window.removeEventListener("storage", onStorage);
  };
}

/** Test helper: forget everything in memory. */
export function __resetSessionForTests() {
  state = null;
  inflight = null;
  initPromise = null;
  endReason = "";
  listeners.clear();
}
