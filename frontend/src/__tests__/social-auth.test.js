import { beforeEach, describe, expect, it, vi } from "vitest";
import { PENDING_KEY, SocialAuthError, completeSocialSignIn, startSocialSignIn } from "../auth/social";
import { META_KEY, REFRESH_KEY, __resetSessionForTests, getSession, isSignedIn } from "../auth/session";
import { fakeIdToken } from "./fixtures";

const pending = () => JSON.parse(window.sessionStorage.getItem(PENDING_KEY));
const tokenResponse = (claims, extra = {}) => ({
  ok: true,
  json: async () => ({ id_token: fakeIdToken(claims), access_token: "access-opaque", refresh_token: "refresh-opaque", ...extra }),
});

beforeEach(() => __resetSessionForTests());

describe("starting social sign-in (authorization code + PKCE)", () => {
  it("sends the browser to the Hosted UI with a PKCE challenge, state and nonce, and never a secret", async () => {
    const navigate = vi.fn();
    await startSocialSignIn("Google", "/events/sample/register", navigate);
    const url = new URL(navigate.mock.calls[0][0]);
    const q = url.searchParams;
    expect(url.origin + url.pathname).toBe("https://auth.example.test/oauth2/authorize");
    expect(q.get("response_type")).toBe("code");
    expect(q.get("client_id")).toBe("test-client-id");
    expect(q.get("redirect_uri")).toBe(`${window.location.origin}/auth/callback`);
    expect(q.get("identity_provider")).toBe("Google");
    expect(q.get("code_challenge_method")).toBe("S256");
    expect(q.get("scope")).toBe("openid email profile");
    expect(q.get("state")).toHaveLength(32);
    expect(q.get("nonce")).toHaveLength(32);
    expect(url.search).not.toMatch(/secret|password/i);
    const saved = pending();
    expect(saved.returnTo).toBe("/events/sample/register");
    expect(saved.state).toBe(q.get("state"));
    expect(q.get("code_challenge")).not.toBe(saved.verifier); // only the hash leaves the browser
  });

  it("uses fresh state and verifier every time", async () => {
    const navigate = vi.fn();
    await startSocialSignIn("Google", "/", navigate);
    const first = pending();
    await startSocialSignIn("Google", "/", navigate);
    expect(pending().state).not.toBe(first.state);
    expect(pending().verifier).not.toBe(first.verifier);
  });

  it("refuses providers that are not configured, and an unsafe return path becomes the home page", async () => {
    const navigate = vi.fn();
    await expect(startSocialSignIn("Facebook", "/", navigate)).rejects.toMatchObject({ code: "not_configured" });
    expect(navigate).not.toHaveBeenCalled();
    await startSocialSignIn("Google", "https://evil.example/", navigate);
    expect(pending().returnTo).toBe("/");
  });
});

describe("completing social sign-in on /auth/callback", () => {
  const begin = async (returnTo = "/events/sample/register") => {
    await startSocialSignIn("Google", returnTo, vi.fn());
    return pending();
  };

  it("exchanges the code with the verifier (no client secret), checks the nonce and installs the session", async () => {
    const p = await begin();
    const fetchImpl = vi.fn(async () => tokenResponse({ nonce: p.nonce }));
    const result = await completeSocialSignIn(`?code=abc123&state=${p.state}`, fetchImpl);
    expect(result.returnTo).toBe("/events/sample/register");

    const [url, init] = fetchImpl.mock.calls[0];
    expect(url).toBe("https://auth.example.test/oauth2/token");
    const body = new URLSearchParams(init.body);
    expect(Object.fromEntries(body)).toEqual({
      grant_type: "authorization_code", client_id: "test-client-id", code: "abc123",
      redirect_uri: `${window.location.origin}/auth/callback`, code_verifier: p.verifier,
    });
    expect(init.body).not.toMatch(/secret/i);

    expect(isSignedIn()).toBe(true);
    expect(getSession()).toMatchObject({ email: "pat@example.com", sub: "sub-123", federated: true });
    expect(window.localStorage.getItem(REFRESH_KEY)).toBe("refresh-opaque");
    expect(JSON.parse(window.localStorage.getItem(META_KEY))).toEqual({ federated: true });
    // ID/access tokens are never persisted, only the refresh token.
    expect(JSON.stringify({ ...window.localStorage })).not.toContain("access-opaque");
    expect(window.sessionStorage.getItem(PENDING_KEY)).toBeNull(); // single use
    expect(window.location.search).toBe(""); // ?code= removed from the address bar
  });

  it("treats the user cancelling at the provider as a cancelled sign-in", async () => {
    const p = await begin();
    await expect(completeSocialSignIn(`?error=access_denied&error_description=cancelled&state=${p.state}`, vi.fn()))
      .rejects.toMatchObject({ name: "SocialAuthError", code: "cancelled", returnTo: "/events/sample/register" });
    expect(isSignedIn()).toBe(false);
  });

  it("reports other provider errors", async () => {
    const p = await begin();
    await expect(completeSocialSignIn(`?error=server_error&state=${p.state}`, vi.fn())).rejects.toMatchObject({ code: "provider_error" });
  });

  it("rejects a mismatched or missing state without calling the token endpoint", async () => {
    const p = await begin();
    const fetchImpl = vi.fn();
    await expect(completeSocialSignIn("?code=abc&state=wrong", fetchImpl)).rejects.toMatchObject({ code: "state_mismatch" });
    expect(fetchImpl).not.toHaveBeenCalled();
    await begin();
    await expect(completeSocialSignIn("?code=abc", fetchImpl)).rejects.toMatchObject({ code: "state_mismatch" });
    expect(p).toBeTruthy();
  });

  it("rejects a callback that no sign-in started here (forged or replayed link)", async () => {
    const fetchImpl = vi.fn();
    await expect(completeSocialSignIn("?code=abc&state=zzz", fetchImpl)).rejects.toMatchObject({ code: "expired" });
    expect(fetchImpl).not.toHaveBeenCalled();
  });

  it("cannot be replayed: the pending record is consumed", async () => {
    const p = await begin();
    const fetchImpl = vi.fn(async () => tokenResponse({ nonce: p.nonce }));
    await completeSocialSignIn(`?code=abc&state=${p.state}`, fetchImpl);
    await expect(completeSocialSignIn(`?code=abc&state=${p.state}`, fetchImpl)).rejects.toMatchObject({ code: "expired" });
    expect(fetchImpl).toHaveBeenCalledTimes(1);
  });

  it("rejects a sign-in that took too long", async () => {
    const p = await begin();
    window.sessionStorage.setItem(PENDING_KEY, JSON.stringify({ ...p, startedAt: Date.now() - 11 * 60 * 1000 }));
    await expect(completeSocialSignIn(`?code=abc&state=${p.state}`, vi.fn())).rejects.toMatchObject({ code: "expired" });
  });

  it.each([
    ["wrong nonce", { nonce: "someone-elses" }],
    ["wrong audience", { aud: "another-client" }],
    ["wrong issuer", { iss: "https://cognito-idp.ap-northeast-1.amazonaws.com/other-pool" }],
    ["expired token", { exp: Math.floor(Date.now() / 1000) - 10 }],
  ])("rejects an ID token with a %s", async (_name, bad) => {
    const p = await begin();
    const fetchImpl = vi.fn(async () => tokenResponse({ nonce: p.nonce, ...bad }));
    await expect(completeSocialSignIn(`?code=abc&state=${p.state}`, fetchImpl)).rejects.toMatchObject({ code: "exchange_failed" });
    expect(isSignedIn()).toBe(false);
    expect(window.localStorage.getItem(REFRESH_KEY)).toBeNull();
  });

  it("reports a failed token exchange (refused code or network failure)", async () => {
    let p = await begin();
    await expect(completeSocialSignIn(`?code=abc&state=${p.state}`, vi.fn(async () => ({ ok: false, json: async () => ({}) }))))
      .rejects.toMatchObject({ code: "exchange_failed" });
    p = await begin();
    await expect(completeSocialSignIn(`?code=abc&state=${p.state}`, vi.fn(async () => { throw new TypeError("network"); })))
      .rejects.toMatchObject({ code: "exchange_failed" });
  });

  it("never sends the visitor to a return path outside the allow-list", async () => {
    const p = await begin();
    window.sessionStorage.setItem(PENDING_KEY, JSON.stringify({ ...p, returnTo: "https://evil.example/" }));
    const fetchImpl = vi.fn(async () => tokenResponse({ nonce: p.nonce }));
    const { returnTo } = await completeSocialSignIn(`?code=abc&state=${p.state}`, fetchImpl);
    expect(returnTo).toBe("/");
  });

  it("is an error object the UI can translate", () => {
    expect(new SocialAuthError("cancelled")).toBeInstanceOf(Error);
  });
});
