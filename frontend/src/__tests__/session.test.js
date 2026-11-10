import { beforeEach, describe, expect, it, vi } from "vitest";
import { cognitoError, sdkState } from "./sdkMock";
import { fakeIdToken } from "./fixtures";

vi.mock("@aws-sdk/client-cognito-identity-provider", async () => (await import("./sdkMock")).mockSdk());

import * as api from "../auth/cognitoApi";
import * as session from "../auth/session";

const result = (claims = {}, extra = {}) => ({ IdToken: fakeIdToken(claims), AccessToken: "access-opaque", RefreshToken: "refresh-opaque", ...extra });

beforeEach(() => {
  sdkState.calls = [];
  sdkState.respond = null;
  session.__resetSessionForTests();
});

describe("error mapping", () => {
  it.each([
    ["UsernameExistsException", "account_exists"],
    ["UserNotConfirmedException", "user_not_confirmed"],
    ["NotAuthorizedException", "bad_credentials"],
    ["UserNotFoundException", "bad_credentials"], // same answer: no account enumeration
    ["CodeMismatchException", "code_invalid"],
    ["ExpiredCodeException", "code_expired"],
    ["InvalidPasswordException", "password_policy"],
    ["InvalidParameterException", "invalid_input"],
    ["LimitExceededException", "rate_limited"],
    ["TooManyRequestsException", "rate_limited"],
    ["SomethingNew", "unknown"],
  ])("%s → %s", (name, code) => {
    expect(api.mapAuthError(cognitoError(name))).toBe(code);
  });

  it("maps network failures and never leaks the raw message", () => {
    expect(api.mapAuthError(new TypeError("Failed to fetch"))).toBe("network");
    expect(new api.AuthError(cognitoError("NotAuthorizedException", "Incorrect username or password.")).message).toBe("bad_credentials");
  });
});

describe("Cognito calls", () => {
  it("signs up with the lower-cased email and optional name", async () => {
    sdkState.respond = () => ({ UserConfirmed: false });
    expect(await api.signUp({ email: " Pat@Example.com ", password: "Passw0rd-test", name: " Pat " })).toEqual({ confirmed: false });
    expect(sdkState.calls[0].input).toMatchObject({
      ClientId: "test-client-id", Username: "pat@example.com", UserAttributes: [{ Name: "email", Value: "pat@example.com" }, { Name: "name", Value: "Pat" }],
    });
  });

  it("surfaces a duplicate account as account_exists", async () => {
    sdkState.respond = () => { throw cognitoError("UsernameExistsException"); };
    await expect(api.signUp({ email: "a@example.com", password: "x" })).rejects.toMatchObject({ code: "account_exists" });
  });

  it("signs in with USER_PASSWORD_AUTH and rejects unsupported challenges", async () => {
    sdkState.respond = () => ({ AuthenticationResult: result() });
    await api.signInWithPassword("Pat@Example.com", "pw");
    expect(sdkState.calls[0].input).toMatchObject({ AuthFlow: "USER_PASSWORD_AUTH", AuthParameters: { USERNAME: "pat@example.com", PASSWORD: "pw" } });
    sdkState.respond = () => ({ ChallengeName: "SMS_MFA" });
    await expect(api.signInWithPassword("a@example.com", "pw")).rejects.toMatchObject({ code: "unknown" });
  });

  it("never includes a client secret in any request", async () => {
    sdkState.respond = () => ({ AuthenticationResult: result(), UserConfirmed: true });
    await api.signUp({ email: "a@example.com", password: "p" });
    await api.confirmSignUp("a@example.com", "123456");
    await api.signInWithPassword("a@example.com", "p");
    await api.requestPasswordReset("a@example.com");
    expect(JSON.stringify(sdkState.calls)).not.toMatch(/secret/i);
  });
});

describe("session", () => {
  it("is signed out until tokens are installed, then exposes only a token-free view", () => {
    expect(session.getSession()).toBeNull();
    session.establishSession(result());
    const view = session.getSession();
    expect(view).toMatchObject({ sub: "sub-123", email: "pat@example.com", emailVerified: true, name: "Pat Example" });
    expect(JSON.stringify(view)).not.toMatch(/opaque|IdToken|eyJ/);
  });

  it("keeps ID/access tokens in memory and only the refresh token in storage", () => {
    session.establishSession(result());
    const stored = JSON.stringify({ ...window.localStorage, ...window.sessionStorage });
    expect(stored).toContain("refresh-opaque");
    expect(stored).not.toContain("access-opaque");
    expect(stored).not.toContain(result().IdToken.slice(0, 20));
  });

  it("returns the current ID token, and refreshes it shortly before it expires", async () => {
    session.establishSession(result({ exp: Math.floor(Date.now() / 1000) + 30 })); // inside the refresh-ahead window
    const fresh = fakeIdToken({ exp: Math.floor(Date.now() / 1000) + 3600, sub: "sub-123" });
    sdkState.respond = (name, input) => {
      expect(input.AuthFlow).toBe("REFRESH_TOKEN_AUTH");
      expect(input.AuthParameters.REFRESH_TOKEN).toBe("refresh-opaque");
      return { AuthenticationResult: { IdToken: fresh, AccessToken: "access-2" } };
    };
    expect(await session.getIdToken()).toBe(fresh);
    expect(window.localStorage.getItem(session.REFRESH_KEY)).toBe("refresh-opaque"); // kept (not rotated)
  });

  it("does not refresh a token that is still comfortably valid", async () => {
    session.establishSession(result());
    sdkState.respond = () => { throw new Error("unexpected refresh"); };
    expect(await session.getIdToken()).toBeTruthy();
    expect(sdkState.calls).toHaveLength(0);
  });

  it("restores the session after a reload from the stored refresh token", async () => {
    window.localStorage.setItem(session.REFRESH_KEY, "refresh-opaque");
    sdkState.respond = () => ({ AuthenticationResult: { IdToken: fakeIdToken(), AccessToken: "a" } });
    const restored = await session.initSession();
    expect(restored).toMatchObject({ email: "pat@example.com" });
    expect(session.isSignedIn()).toBe(true);
  });

  it("ends the session with an 'expired' reason when the refresh token is rejected", async () => {
    window.localStorage.setItem(session.REFRESH_KEY, "stale");
    sdkState.respond = () => { throw cognitoError("NotAuthorizedException"); };
    const listener = vi.fn();
    session.onAuthChange(listener);
    expect(await session.initSession()).toBeNull();
    expect(window.localStorage.getItem(session.REFRESH_KEY)).toBeNull();
    expect(session.consumeEndReason()).toBe("expired");
    expect(session.consumeEndReason()).toBe(""); // reported once
    expect(await session.getIdToken()).toBeNull();
  });

  it("keeps the stored session through a temporary network failure", async () => {
    window.localStorage.setItem(session.REFRESH_KEY, "refresh-opaque");
    sdkState.respond = () => { throw new TypeError("Failed to fetch"); };
    expect(await session.initSession()).toBeNull();
    expect(window.localStorage.getItem(session.REFRESH_KEY)).toBe("refresh-opaque");
    expect(session.consumeEndReason()).toBe("");
  });

  it("notifies listeners on sign-in and sign-out", async () => {
    const listener = vi.fn();
    session.onAuthChange(listener);
    session.establishSession(result());
    expect(listener).toHaveBeenLastCalledWith(expect.objectContaining({ email: "pat@example.com" }));
    sdkState.respond = () => ({});
    await session.signOut();
    expect(listener).toHaveBeenLastCalledWith(null);
  });

  it("signs out everywhere (GlobalSignOut) and clears local state", async () => {
    session.establishSession(result());
    sdkState.respond = () => ({});
    const { hostedLogoutUrl } = await session.signOut();
    expect(sdkState.calls.at(-1)).toMatchObject({ name: "GlobalSignOutCommand", input: { AccessToken: "access-opaque" } });
    expect(session.isSignedIn()).toBe(false);
    expect(window.localStorage.getItem(session.REFRESH_KEY)).toBeNull();
    expect(hostedLogoutUrl).toBe(""); // password sign-in: no Hosted UI session to end
  });

  it("also ends the Hosted UI session after a social sign-in", async () => {
    session.establishSession(result(), { federated: true });
    sdkState.respond = () => ({});
    const { hostedLogoutUrl } = await session.signOut();
    const url = new URL(hostedLogoutUrl);
    expect(url.origin + url.pathname).toBe("https://auth.example.test/logout");
    expect(url.searchParams.get("client_id")).toBe("test-client-id");
    expect(url.searchParams.get("logout_uri")).toBe(`${window.location.origin}/`);
  });

  it("still signs out locally when the server call fails", async () => {
    session.establishSession(result());
    sdkState.respond = () => { throw new TypeError("offline"); };
    await session.signOut();
    expect(session.isSignedIn()).toBe(false);
  });

  it("follows a sign-out in another tab", () => {
    session.establishSession(result());
    const listener = vi.fn();
    session.onAuthChange(listener);
    window.dispatchEvent(new StorageEvent("storage", { key: session.REFRESH_KEY, newValue: null }));
    expect(session.isSignedIn()).toBe(false);
    expect(listener).toHaveBeenCalledWith(null);
  });

  it("rejects an ID token it cannot read", () => {
    expect(() => session.establishSession({ IdToken: "not-a-jwt" })).toThrow();
  });
});
