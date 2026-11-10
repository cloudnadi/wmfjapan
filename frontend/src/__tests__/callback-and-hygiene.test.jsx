import { readFileSync, readdirSync, statSync } from "node:fs";
import { join, relative } from "node:path";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { fakeIdToken } from "./fixtures";

vi.mock("@aws-sdk/client-cognito-identity-provider", async () => (await import("./sdkMock")).mockSdk());
vi.mock("../api", () => ({ getSiteConfig: vi.fn(async () => ({ config: {} })), getMembershipStatus: vi.fn(async () => ({})) }));

import AuthCallback from "../auth/AuthCallback";
import { PENDING_KEY, startSocialSignIn } from "../auth/social";
import * as session from "../auth/session";
import SignInPage from "../SignInPage";

const SRC = join(__dirname, "..");
const ROOT = join(SRC, "..");

describe("/auth/callback page", () => {
  it("completes sign-in and returns to the page the visitor came from", async () => {
    await startSocialSignIn("Google", "/events/sample/register", vi.fn());
    const pending = JSON.parse(window.sessionStorage.getItem(PENDING_KEY));
    window.history.replaceState({}, "", `/auth/callback?code=abc&state=${pending.state}`);
    vi.stubGlobal("fetch", vi.fn(async () => ({ ok: true, json: async () => ({ id_token: fakeIdToken({ nonce: pending.nonce }), access_token: "a", refresh_token: "r" }) })));
    const navigateTo = vi.fn();
    render(<AuthCallback navigateTo={navigateTo} />);
    expect(screen.getByRole("status").textContent).toMatch(/signing you in/i);
    await waitFor(() => expect(navigateTo).toHaveBeenCalledWith("/events/sample/register", { replace: true }));
    expect(session.isSignedIn()).toBe(true);
  });

  it("explains a cancelled social sign-in and offers a way back", async () => {
    await startSocialSignIn("Google", "/events/sample/register", vi.fn());
    const pending = JSON.parse(window.sessionStorage.getItem(PENDING_KEY));
    window.history.replaceState({}, "", `/auth/callback?error=access_denied&state=${pending.state}`);
    const navigateTo = vi.fn();
    render(<AuthCallback navigateTo={navigateTo} />);
    expect((await screen.findByRole("alert")).textContent).toMatch(/cancelled/i);
    fireEvent.click(screen.getByRole("button", { name: /back to sign in/i }));
    expect(navigateTo).toHaveBeenCalledWith("/events/sample/register", { replace: true });
  });

  it("a forged callback is stopped", async () => {
    window.history.replaceState({}, "", "/auth/callback?code=abc&state=forged");
    render(<AuthCallback navigateTo={vi.fn()} />);
    expect((await screen.findByRole("alert")).textContent).toMatch(/expired/i);
  });
});

describe("sign-in page", () => {
  it("only returns to allow-listed paths", async () => {
    window.history.replaceState({}, "", `/signin?returnTo=${encodeURIComponent("https://evil.example/steal")}`);
    session.establishSession({ IdToken: fakeIdToken(), AccessToken: "a", RefreshToken: "r" });
    const navigateTo = vi.fn();
    render(<SignInPage navigateTo={navigateTo} />);
    fireEvent.click(await screen.findByRole("button", { name: /^continue$/i }));
    expect(navigateTo).toHaveBeenCalledWith("/my/events");       // the safe default, not the attacker's URL
  });
});

function walk(dir, out = []) {
  for (const name of readdirSync(dir)) {
    if (name === "node_modules" || name === "dist") continue;
    const full = join(dir, name);
    statSync(full).isDirectory() ? walk(full, out) : out.push(full);
  }
  return out;
}

describe("repository hygiene", () => {
  const files = (filter) => walk(SRC).filter(filter);

  it("the public site has no hard-coded Onam event, route, content or image", () => {
    const publicFiles = [
      "App.jsx", "HomePage.jsx", "EventsList.jsx", "EventPage.jsx", "EventRegister.jsx", "EventRegistered.jsx", "EventShell.jsx",
      "MyEventRegistrations.jsx", "Navbar.jsx", "SiteFooter.jsx", "SignInPage.jsx", "pageMeta.js", "siteConfig.js", "AddonsStep.jsx", "EventAddons.jsx",
    ].map((f) => join(SRC, f)).concat(files((f) => f.includes(`${join("src", "auth")}/`)));
    const offenders = publicFiles.filter((f) => /onam|aavani|sadhya|onam_2026/i.test(readFileSync(f, "utf8")));
    expect(offenders.map((f) => relative(ROOT, f))).toEqual([]);
    for (const f of ["index.html", "public/sitemap.xml", "public/robots.txt"]) {
      expect(readFileSync(join(ROOT, f), "utf8")).not.toMatch(/onam|aavani/i);
    }
    expect(readdirSync(join(ROOT, "public")).filter((n) => /onam|badhra/i.test(n))).toEqual([]);
  });

  it("no route or event id for a specific event is hard-coded in the router", () => {
    const app = readFileSync(join(SRC, "App.jsx"), "utf8");
    expect(app).not.toMatch(/\/onam|EVENT_COMPLETED|UPCOMING_EVENTS/);
  });

  it("nothing about NPO membership, the AGM or the original registration pipeline is left in the app", () => {
    const code = files((f) => /\.(jsx?|html)$/.test(f) && !f.includes("__tests__") && !f.endsWith("StaticPages.jsx"));
    const offenders = code.filter((f) => /\bnpo\b|membership|memberPrice|nonMemberPrice|\bAGM\b|legacy|\/member\//i.test(readFileSync(f, "utf8")));
    expect(offenders.map((f) => relative(ROOT, f))).toEqual([]);
  });

  it("is a single light theme matching the organisation site: no theme switch, no dark mode", () => {
    expect(readFileSync(join(ROOT, "index.html"), "utf8")).not.toMatch(/data-theme|wmf-theme/);
    expect(readFileSync(join(SRC, "Navbar.jsx"), "utf8")).not.toMatch(/theme/i);
    const css = readFileSync(join(SRC, "styles.css"), "utf8");
    expect(css).not.toMatch(/\[data-theme/);
    expect(css).toContain("--wmf-green-brand: #009757");   // wmfjapan.org's green
    expect(css).toContain("--mint: #dbe8e6");
  });

  it("old organisation branding is gone", () => {
    const hits = files((f) => /\.(jsx?|html|css|md)$/.test(f) && !f.includes("__tests__"))
      .concat([join(ROOT, "index.html")])
      .filter((f) => /nihon\s*kairali|nihonkairali|kairali/i.test(readFileSync(f, "utf8")));
    expect(hits).toEqual([]);
  });

  it("the canonical URL and the organisation link are configured", () => {
    const html = readFileSync(join(ROOT, "index.html"), "utf8");
    expect(html).toContain('<link rel="canonical" href="https://wmfevents.cloudnadi.com/" />');
    expect(html).toContain("https://www.wmfjapan.org/");
    expect(html).not.toContain("wmfjapan.cloudnadi.com");
  });

  const SECRET_PATTERNS = [
    [/AKIA[0-9A-Z]{16}/, "AWS access key id"],
    [/-----BEGIN [A-Z ]*PRIVATE KEY-----/, "private key"],
    [/client_secret\s*[:=]\s*["'][^"']{6,}/i, "client secret assignment"],
    [/\b\d{12}\b.*amazonaws|arn:aws:[a-z0-9-]+:[a-z0-9-]*:\d{12}:/, "AWS account id"],
    [/\bap-northeast-1_[A-Za-z0-9]{9}\b/, "real-looking Cognito pool id"],
    [/(?:sk|pk)_(?:live|test)_[A-Za-z0-9]{10,}/, "payment key"],
    [/\bEAAA[A-Za-z0-9_-]{30,}/, "Square token"],
  ];

  it("test fixtures, examples and snapshots contain no secrets or real identifiers", () => {
    const targets = files((f) => f.includes("__tests__")).concat(
      [".env.example", "env.production.example", "env.preview.example"].map((f) => join(ROOT, f)),
      files((f) => f.endsWith(".snap")),
    );
    const found = [];
    for (const file of targets) {
      const text = readFileSync(file, "utf8");
      for (const [pattern, label] of SECRET_PATTERNS) {
        if (file.endsWith("callback-and-hygiene.test.jsx")) continue; // this file holds the patterns themselves
        if (pattern.test(text)) found.push(`${relative(ROOT, file)}: ${label}`);
      }
    }
    expect(found).toEqual([]);
  });

  it("browser code never reads or ships an OAuth client secret", () => {
    const code = files((f) => /\.(jsx?|html)$/.test(f) && !f.includes("__tests__"));
    const hits = code.filter((f) => /client_secret|VITE_[A-Z_]*SECRET|VITE_[A-Z_]*PASSWORD/i.test(readFileSync(f, "utf8")));
    expect(hits.map((f) => relative(ROOT, f))).toEqual([]);
  });

  it("tokens and codes are never written to the console", () => {
    const hits = files((f) => f.includes(`${join("src", "auth")}/`) || f.endsWith("eventsApi.js"))
      .filter((f) => /console\.(log|info|debug|warn|error)/.test(readFileSync(f, "utf8")));
    expect(hits.map((f) => relative(ROOT, f))).toEqual([]);
  });
});
