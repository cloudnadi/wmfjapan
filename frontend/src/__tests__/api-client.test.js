import { beforeEach, describe, expect, it, vi } from "vitest";
import { cognitoError, sdkState } from "./sdkMock";
import { fakeIdToken } from "./fixtures";

vi.mock("@aws-sdk/client-cognito-identity-provider", async () => (await import("./sdkMock")).mockSdk());

import * as session from "../auth/session";
import { getPublicEvent, myEventRegistrations, registerForEvent } from "../eventsApi";

const reply = (status, body) => ({ ok: status < 400, status, json: async () => body });
const lastHeaders = (fetchMock, i = -1) => fetchMock.mock.calls.at(i)[1].headers;

beforeEach(() => {
  sdkState.calls = [];
  sdkState.respond = null;
  session.__resetSessionForTests();
});

describe("event platform client: identity", () => {
  it("sends no Authorization header for a guest", async () => {
    const fetchMock = vi.fn(async () => reply(200, { event: {} }));
    vi.stubGlobal("fetch", fetchMock);
    await getPublicEvent("sample-gathering");
    expect(fetchMock.mock.calls[0][0]).toBe("https://api.test/prod/public/events/sample-gathering");
    expect(lastHeaders(fetchMock)).not.toHaveProperty("Authorization");
  });

  it("sends the Cognito ID token (never the access or refresh token) when signed in", async () => {
    const idToken = fakeIdToken();
    session.establishSession({ IdToken: idToken, AccessToken: "access-opaque", RefreshToken: "refresh-opaque" });
    const fetchMock = vi.fn(async () => reply(200, { registration: {} }));
    vi.stubGlobal("fetch", fetchMock);
    await registerForEvent("sample-gathering", { tickets: { adult: 1 } });
    expect(lastHeaders(fetchMock).Authorization).toBe(idToken);
    expect(JSON.stringify(fetchMock.mock.calls)).not.toMatch(/access-opaque|refresh-opaque/);
  });

  it("protected calls fail locally, without a request, when nobody is signed in", async () => {
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);
    await expect(myEventRegistrations()).rejects.toMatchObject({ status: 401, code: "session_expired" });
    expect(fetchMock).not.toHaveBeenCalled();
  });
});

describe("event platform client: expiry and errors", () => {
  it("refreshes once and retries when the server rejects an expired ID token", async () => {
    session.establishSession({ IdToken: fakeIdToken(), AccessToken: "a", RefreshToken: "r" });
    const fresh = fakeIdToken({ exp: Math.floor(Date.now() / 1000) + 7200 });
    sdkState.respond = () => ({ AuthenticationResult: { IdToken: fresh, AccessToken: "a2" } });
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(reply(401, { error: "Your session has expired.", code: "session_expired" }))
      .mockResolvedValueOnce(reply(200, { registrations: [] }));
    vi.stubGlobal("fetch", fetchMock);
    await expect(myEventRegistrations()).resolves.toEqual({ registrations: [] });
    expect(fetchMock).toHaveBeenCalledTimes(2);
    expect(lastHeaders(fetchMock).Authorization).toBe(fresh);
  });

  it("ends the session when even a refreshed token is rejected", async () => {
    session.establishSession({ IdToken: fakeIdToken(), AccessToken: "a", RefreshToken: "r" });
    sdkState.respond = (name) => (name === "InitiateAuthCommand" ? { AuthenticationResult: { IdToken: fakeIdToken({ sub: "sub-123", jti: "2" }), AccessToken: "a2" } } : {});
    vi.stubGlobal("fetch", vi.fn(async () => reply(401, { error: "Your session has expired. Please sign in again.", code: "session_expired" })));
    await expect(myEventRegistrations()).rejects.toMatchObject({ status: 401, code: "session_expired" });
    expect(session.isSignedIn()).toBe(false);
  });

  it("a revoked refresh token also ends the session", async () => {
    session.establishSession({ IdToken: fakeIdToken(), AccessToken: "a", RefreshToken: "r" });
    sdkState.respond = (name) => { if (name === "InitiateAuthCommand") throw cognitoError("NotAuthorizedException"); return {}; };
    vi.stubGlobal("fetch", vi.fn(async () => reply(401, { error: "expired", code: "session_expired" })));
    await expect(myEventRegistrations()).rejects.toMatchObject({ status: 401 });
    expect(session.isSignedIn()).toBe(false);
  });

  it("an unverified email is not an expired session: the account stays signed in", async () => {
    session.establishSession({ IdToken: fakeIdToken({ email_verified: false }), AccessToken: "a", RefreshToken: "r" });
    vi.stubGlobal("fetch", vi.fn(async () => reply(401, { error: "Please verify your email address before registering.", code: "email_unverified" })));
    await expect(registerForEvent("x", {})).rejects.toMatchObject({ code: "email_unverified" });
    expect(session.isSignedIn()).toBe(true);
  });

  it("carries the server's field errors and codes through", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => reply(400, { error: "Some options are not available for this event", code: "feature_disabled", fields: { "addons.entrySlots": "Entry slots is not available for this event" } })));
    await expect(registerForEvent("x", { addons: { entrySlots: { slotId: "s" } } })).rejects.toMatchObject({
      status: 400, code: "feature_disabled", fields: { "addons.entrySlots": expect.stringContaining("not available") },
    });
  });

  it("survives a response that is not JSON", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => ({ ok: false, status: 502, json: async () => { throw new SyntaxError("bad"); } })));
    await expect(getPublicEvent("x")).rejects.toMatchObject({ status: 502, message: "Request failed" });
  });
});
