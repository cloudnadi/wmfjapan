/**
 * Organisation / site settings in one place, so nothing org-specific is scattered through the UI.
 * Every value can be overridden at build time with a VITE_* variable (see frontend/.env.example).
 *
 * Contact details and legal text are NOT invented here: `contactEmail` keeps the value the repository already
 * shipped with and must be confirmed by WMF Japan (docs/WMF_EVENTS_SITE.md).
 */

const env = import.meta.env;
const trimSlash = (value) => String(value || "").replace(/\/+$/, "");

export const siteConfig = {
  name: env.VITE_SITE_NAME || "WMF Japan",
  shortTitle: env.VITE_SITE_TITLE || "WMF Japan Events",
  // Canonical origin of this site (no trailing slash).
  siteUrl: trimSlash(env.VITE_SITE_URL || "https://wmfevents.cloudnadi.com"),
  // The organisation's own website, linked from the header and footer.
  orgUrl: env.VITE_ORG_URL || "https://www.wmfjapan.org/",
  orgName: env.VITE_ORG_NAME || "WMF Japan",
  description:
    env.VITE_SITE_DESCRIPTION ||
    "Find and register for WMF Japan events. Sign in to manage your registrations, or register as a guest.",
  // PLACEHOLDER (pre-existing value): confirm with WMF Japan. Set to an empty string to hide contact links.
  contactEmail: env.VITE_CONTACT_EMAIL ?? "info@wmfjapan.com",
  // No logo is bundled for the header: set VITE_LOGO_URL to the official WMF Japan logo to show it, otherwise a text
  // wordmark is used. public/logo.png and public/logo-sm.png are plain-text PLACEHOLDERS (used by e-mails, PDFs and link
  // previews): replace the files with the official artwork.
  logo: { src: env.VITE_LOGO_URL || "", alt: `${env.VITE_SITE_NAME || "WMF Japan"} logo` },
  defaultLocale: env.VITE_DEFAULT_LOCALE || "en",
};

/** Absolute URL on this site for a path ("/events" → https://…/events). */
export function absoluteUrl(path = "/") {
  return `${siteConfig.siteUrl}${path === "/" ? "/" : path}`;
}
