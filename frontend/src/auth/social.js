/**
 * Social sign-in through the Cognito Hosted UI: OAuth 2.0 authorization-code flow with PKCE.
 *
 *   startSocialSignIn()  → builds the /oauth2/authorize URL with a fresh PKCE challenge, `state` and
 *                           `nonce`, remembers the verifier in this tab, and navigates there.
 *   completeSocialSignIn() → on /auth/callback: rejects a missing/mismatched `state`, exchanges the code
 *                           (with the verifier, never a client secret) at /oauth2/token, checks the ID
 *                           token's nonce/audience/issuer, and installs the session.
 *
 * The pending-flow record lives in sessionStorage (per tab, gone when the tab closes) and is deleted the
 * moment the callback is read, success or not. The `code` is removed from the address bar before anything else
 * so it cannot leak through history or a Referer header. Codes and tokens are never logged.
 */
import { getAuthConfig, isSocialConfigured } from "./config";
import { createCodeChallenge, createCodeVerifier, randomString } from "./pkce";
import { sanitizeReturnPath } from "./redirect";
import { decodeJwt, establishSession } from "./session";
import { tabStore } from "./storage";

export const PENDING_KEY = "wmf-auth.pending";
const PENDING_MAX_AGE_MS = 10 * 60 * 1000;

export class SocialAuthError extends Error {
  /** @param {"cancelled"|"state_mismatch"|"expired"|"exchange_failed"|"provider_error"|"not_configured"} code */
  constructor(code, returnTo = "/") {
    super(code);
    this.name = "SocialAuthError";
    this.code = code;
    this.returnTo = returnTo;
  }
}

export function buildAuthorizeUrl({ provider, state, nonce, challenge }) {
  const config = getAuthConfig();
  const url = new URL(`https://${config.domain}/oauth2/authorize`);
  const params = {
    response_type: "code",
    client_id: config.clientId,
    redirect_uri: config.redirectUri,
    scope: config.scopes.join(" "),
    state,
    nonce,
    code_challenge: challenge,
    code_challenge_method: "S256",
    identity_provider: provider,
  };
  Object.entries(params).forEach(([key, value]) => url.searchParams.set(key, value));
  return url.toString();
}

/** Begin social sign-in. `navigate` is injectable for tests; the default leaves the page. */
export async function startSocialSignIn(provider, returnTo = "/", navigate = (url) => window.location.assign(url)) {
  const config = getAuthConfig();
  if (!isSocialConfigured() || !config.socialProviders.includes(provider)) throw new SocialAuthError("not_configured");
  const verifier = createCodeVerifier();
  const state = randomString(24);
  const nonce = randomString(24);
  tabStore.set(
    PENDING_KEY,
    JSON.stringify({ verifier, state, nonce, provider, returnTo: sanitizeReturnPath(returnTo), startedAt: Date.now() })
  );
  navigate(buildAuthorizeUrl({ provider, state, nonce, challenge: await createCodeChallenge(verifier) }));
}

function takePending() {
  const raw = tabStore.get(PENDING_KEY);
  tabStore.remove(PENDING_KEY); // single use
  try {
    return raw ? JSON.parse(raw) : null;
  } catch {
    return null;
  }
}

const safeEqual = (a, b) => {
  if (typeof a !== "string" || typeof b !== "string" || a.length !== b.length) return false;
  let diff = 0;
  for (let i = 0; i < a.length; i += 1) diff |= a.charCodeAt(i) ^ b.charCodeAt(i);
  return diff === 0;
};

/**
 * Finish a Hosted UI sign-in from the callback URL's query string.
 * @returns {Promise<{returnTo:string}>}
 * @throws {SocialAuthError}
 */
export async function completeSocialSignIn(search = window.location.search, fetchImpl = (...a) => fetch(...a)) {
  const params = new URLSearchParams(search);
  const code = params.get("code");
  const state = params.get("state");
  const providerError = params.get("error");
  const pending = takePending();
  const returnTo = sanitizeReturnPath(pending?.returnTo, "/");

  // Drop ?code=…&state=… from the address bar immediately.
  try {
    window.history.replaceState({}, "", window.location.pathname);
  } catch {
    // not fatal
  }

  if (!pending) throw new SocialAuthError(providerError ? "provider_error" : "expired", returnTo);
  if (providerError) {
    // The user pressing "cancel" at the provider arrives as access_denied.
    throw new SocialAuthError(providerError === "access_denied" ? "cancelled" : "provider_error", returnTo);
  }
  if (!code || !safeEqual(state, pending.state)) throw new SocialAuthError("state_mismatch", returnTo);
  if (Date.now() - Number(pending.startedAt || 0) > PENDING_MAX_AGE_MS) throw new SocialAuthError("expired", returnTo);

  const config = getAuthConfig();
  let tokens;
  try {
    const response = await fetchImpl(`https://${config.domain}/oauth2/token`, {
      method: "POST",
      headers: { "Content-Type": "application/x-www-form-urlencoded" },
      body: new URLSearchParams({
        grant_type: "authorization_code",
        client_id: config.clientId,
        code,
        redirect_uri: config.redirectUri,
        code_verifier: pending.verifier,
      }).toString(),
    });
    if (!response.ok) throw new Error("token endpoint refused the code");
    tokens = await response.json();
  } catch {
    throw new SocialAuthError("exchange_failed", returnTo);
  }

  const claims = decodeJwt(tokens?.id_token);
  const valid =
    claims &&
    claims.aud === config.clientId &&
    safeEqual(String(claims.nonce || ""), pending.nonce) &&
    claims.iss === `https://cognito-idp.${config.region}.amazonaws.com/${config.userPoolId}` &&
    Number(claims.exp) * 1000 > Date.now();
  if (!valid) throw new SocialAuthError("exchange_failed", returnTo);

  establishSession(
    { IdToken: tokens.id_token, AccessToken: tokens.access_token, RefreshToken: tokens.refresh_token },
    { federated: true }
  );
  return { returnTo };
}
