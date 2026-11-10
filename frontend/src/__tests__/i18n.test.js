import { afterEach, describe, expect, it } from "vitest";
import en from "../locales/en.json";
import { availableLocales, formatJpy, getLocale, setLocale, t } from "../i18n";

afterEach(() => setLocale("en"));

describe("i18n", () => {
  it("ships English and a Japanese catalog slot", () => {
    expect(availableLocales()).toEqual(expect.arrayContaining(["en", "ja"]));
  });

  it("falls back to English for keys a locale lacks, then to the key", () => {
    setLocale("ja");
    expect(getLocale()).toBe("ja");
    expect(document.documentElement.lang).toBe("ja");
    expect(t("home.upcoming")).toBe(en["home.upcoming"]);
    expect(t("no.such.key")).toBe("no.such.key");
  });

  it("fills placeholders and ignores unknown locales", () => {
    expect(t("auth.signedInAs", { email: "pat@example.com" })).toBe("Signed in as pat@example.com");
    setLocale("xx");
    expect(getLocale()).toBe("en");
  });

  it("formats yen without decimals", () => {
    expect(formatJpy(12500)).toBe("¥12,500");
  });

  it("has no organisation-specific or event-specific wording in the new site/home/auth strings", () => {
    const own = Object.entries(en).filter(([k]) => /^(site|home|auth)\./.test(k));
    expect(own.length).toBeGreaterThan(50);
    own.forEach(([k, v]) => expect(v, k).not.toMatch(/onam|aavani|kairali/i));
  });
});
