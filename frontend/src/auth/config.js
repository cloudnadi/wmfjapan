/**
 * Attendee sign-in configuration (Amazon Cognito), read from VITE_AUTH_* variables.
 *
 * These are the PUBLIC identifiers of a secret-less SPA app client. There is deliberately no client
 * secret anywhere in the browser code. The admin dashboard has its own pool/client (VITE_COGNITO_*).
 * Read lazily (functions, not constants) so tests can stub the environment.
 */

const csv = (value) =>
  String(value || "")
    .split(",")
    .map((item) => item.trim())
    .filter(Boolean);

export function getAuthConfig() {
  const env = import.meta.env;
  const domain = String(env.VITE_AUTH_COGNITO_DOMAIN || "").replace(/^https?:\/\//, "").replace(/\/+$/, "");
  return {
    region: env.VITE_AUTH_REGION || "ap-northeast-1",
    userPoolId: env.VITE_AUTH_USER_POOL_ID || "",
    clientId: env.VITE_AUTH_CLIENT_ID || "",
    domain,
    scopes: csv(env.VITE_AUTH_SCOPES || "openid,email,profile"),
    // Names must match the Cognito identity-provider names (e.g. Google, Facebook, LoginWithAmazon).
    socialProviders: csv(env.VITE_AUTH_SOCIAL_PROVIDERS),
    // The page Cognito sends the browser back to. Must be listed as a callback URL on the app client.
    redirectUri: `${window.location.origin}/auth/callback`,
    // Where Hosted UI sends the browser after sign-out. Must be listed as a sign-out URL.
    logoutUri: `${window.location.origin}/`,
  };
}

/** Email/password forms need only the client id. */
export function isAuthConfigured() {
  return !!getAuthConfig().clientId;
}

/** Social sign-in needs the Hosted UI domain and at least one provider. */
export function isSocialConfigured() {
  const config = getAuthConfig();
  return !!(config.clientId && config.domain && config.socialProviders.length);
}

export const PROVIDER_LABELS = {
  Google: "Google",
  Facebook: "Facebook",
  LoginWithAmazon: "Amazon",
  SignInWithApple: "Apple",
};
