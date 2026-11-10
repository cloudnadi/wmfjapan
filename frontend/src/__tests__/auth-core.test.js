import { beforeEach, describe, expect, it, vi } from "vitest";
import { ALLOWED_RETURN_PREFIXES, sanitizeReturnPath } from "../auth/redirect";
import { createCodeChallenge, createCodeVerifier, randomString } from "../auth/pkce";
import { getAuthConfig, isAuthConfigured, isSocialConfigured } from "../auth/config";

describe("post-login redirect allow-list", () => {
  it("keeps same-site paths under allowed areas, with query and hash", () => {
    expect(sanitizeReturnPath("/events/sample/register")).toBe("/events/sample/register");
    expect(sanitizeReturnPath("/my/events?x=1#top")).toBe("/my/events?x=1#top");
    expect(sanitizeReturnPath("/", "/elsewhere")).toBe("/");
  });

  it.each([
    "https://evil.example/events",
    "//evil.example/events",
    "/\\evil.example",
    "javascript:alert(1)",
    "/events/../admin",
    "/admin",
    "/member/profile",
    "/events\r\nSet-Cookie: a=b",
    "events/sample",
    "",
    "/events/" + "a".repeat(600),
  ])("rejects %s", (bad) => {
    expect(sanitizeReturnPath(bad, "/fallback")).toBe("/fallback");
  });

  it("rejects non-strings and falls back to the default", () => {
    expect(sanitizeReturnPath(null)).toBe("/");
    expect(sanitizeReturnPath(undefined, "/my/events")).toBe("/my/events");
    expect(sanitizeReturnPath({ toString: () => "/events" })).toBe("/");
  });

  it("only allows the documented prefixes", () => {
    expect(ALLOWED_RETURN_PREFIXES).toEqual(["/events", "/my/events", "/signin"]);
    expect(sanitizeReturnPath("/eventsXYZ", "/f")).toBe("/f");
  });
});

describe("PKCE", () => {
  it("matches the RFC 7636 appendix B test vector", async () => {
    expect(await createCodeChallenge("dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk")).toBe("E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM");
  });

  it("creates URL-safe verifiers of valid length that differ every time", () => {
    const a = createCodeVerifier();
    const b = createCodeVerifier();
    expect(a).toMatch(/^[A-Za-z0-9_-]{43,128}$/);
    expect(a).not.toBe(b);
    expect(randomString(24)).toMatch(/^[A-Za-z0-9_-]+$/);
  });
});

describe("auth configuration", () => {
  beforeEach(() => vi.unstubAllEnvs());

  it("reads the public identifiers and derives the callback from the current origin", () => {
    vi.stubEnv("VITE_AUTH_CLIENT_ID", "cid");
    vi.stubEnv("VITE_AUTH_COGNITO_DOMAIN", "https://auth.example.test/");
    vi.stubEnv("VITE_AUTH_SOCIAL_PROVIDERS", " Google , Facebook ,");
    const config = getAuthConfig();
    expect(config.domain).toBe("auth.example.test");
    expect(config.socialProviders).toEqual(["Google", "Facebook"]);
    expect(config.redirectUri).toBe(`${window.location.origin}/auth/callback`);
    expect(config.scopes).toEqual(["openid", "email", "profile"]);
    expect(isAuthConfigured() && isSocialConfigured()).toBe(true);
  });

  it("reports what is missing instead of guessing", () => {
    vi.stubEnv("VITE_AUTH_CLIENT_ID", "");
    expect(isAuthConfigured()).toBe(false);
    vi.stubEnv("VITE_AUTH_CLIENT_ID", "cid");
    vi.stubEnv("VITE_AUTH_SOCIAL_PROVIDERS", "");
    expect(isAuthConfigured()).toBe(true);
    expect(isSocialConfigured()).toBe(false);
  });

  it("exposes no client-secret setting", () => {
    expect(Object.keys(getAuthConfig()).join(" ")).not.toMatch(/secret/i);
  });
});
