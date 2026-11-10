// Per-page <title> and social/SEO metadata. index.html carries the generic site
// defaults (what crawlers that don't run JavaScript see); this keeps the tab
// title, description, canonical URL and robots hint in step with the page the
// visitor is on.

const SITE = "WMF Japan";
const ORIGIN = "https://wmfjapan.cloudnadi.com";

export const DEFAULT_DESCRIPTION =
  "WMF Japan is the Malayali community association in Japan. Join or renew your NPO membership, register for events and celebrations, and stay connected with the community.";

// path → [title, description?, indexable?]. Private (signed-in / staff) pages are noindex.
const PAGES = {
  "/": [`${SITE} | Malayali Community in Japan`, DEFAULT_DESCRIPTION, true],
  "/onam/register": [
    `Event Registration | ${SITE}`,
    "Register online for WMF Japan celebrations in Tokyo — Onam (Aavani 2026) and the 40th Anniversary.",
    true,
  ],
  "/member/profile": [`Member Profile | ${SITE}`, "View your WMF Japan member profile, NPO membership status and payment history.", false],
  "/member/membership": [`Join / Renew NPO Membership | ${SITE}`, "Join or renew your WMF Japan NPO membership, add your spouse and register for the AGM.", false],
  "/my/registrations": [`My Registrations | ${SITE}`, "View and manage your WMF Japan event registrations.", false],
  "/my/hall-seat-selection": [`Hall Seat Selection | ${SITE}`, "Choose your seats for the WMF Japan celebration.", false],
  "/my/seat-upgrade": [`Zone Upgrade | ${SITE}`, "Upgrade your seating zone for the WMF Japan celebration.", false],
  "/celebrity-dinner": [`Dinner with Celebrity | ${SITE}`, "Sign up for the WMF Japan dinner with our celebrity guest.", false],
  "/events": [`Events | ${SITE}`, "Upcoming and past WMF Japan events — register online.", true],
  "/my/events": [`My Event Registrations | ${SITE}`, "View and manage your event registrations.", false],
  "/admin": [`Admin | ${SITE}`, DEFAULT_DESCRIPTION, false],
  "/admin/check-in": [`Event check-in | ${SITE}`, DEFAULT_DESCRIPTION, false],
  "/admin/event-platform": [`Event management | ${SITE}`, DEFAULT_DESCRIPTION, false],
  "/check-in": [`Check-in | ${SITE}`, DEFAULT_DESCRIPTION, false],
  "/seat-check-in": [`Seat Check-in | ${SITE}`, DEFAULT_DESCRIPTION, false],
  "/terms": [`Terms & Conditions | ${SITE}`, "Terms and conditions for WMF Japan events and membership.", true],
  "/privacy": [`Privacy Policy | ${SITE}`, "How WMF Japan collects and uses your personal information.", true],
  "/cancellation": [`Cancellation Policy | ${SITE}`, "Cancellation and refund policy for WMF Japan event tickets and NPO membership.", true],
  "/sponsors": [`Sponsors & Partners | ${SITE}`, "The sponsors and partners who support WMF Japan and its community events.", true],
  "/sadhya-slot-guide": [`Sadhya Slot Guide | ${SITE}`, "How Sadhya slot selection works at WMF Japan celebrations.", true],
  "/seat-selection-guide": [`Seat Selection Guide | ${SITE}`, "How hall seat selection works at WMF Japan celebrations.", true],
  "/zone-upgrade-guide": [`Zone Upgrade Guide | ${SITE}`, "How seating zone upgrades work at WMF Japan celebrations.", true],
};

function setMeta(selector, attribute, value, create) {
  let el = document.head.querySelector(selector);
  if (!el && create) {
    el = document.createElement(create.tag);
    Object.entries(create.attrs).forEach(([k, v]) => el.setAttribute(k, v));
    document.head.appendChild(el);
  }
  if (el) el.setAttribute(attribute, value);
}

export function applyPageMeta(path) {
  const [title, description, indexable] = PAGES[path] || [SITE, DEFAULT_DESCRIPTION, false];
  const url = `${ORIGIN}${path === "/" ? "/" : path}`;
  document.title = title;
  setMeta('meta[name="description"]', "content", description);
  setMeta('meta[property="og:title"]', "content", title);
  setMeta('meta[property="og:description"]', "content", description);
  setMeta('meta[property="og:url"]', "content", url);
  setMeta('meta[name="twitter:title"]', "content", title);
  setMeta('meta[name="twitter:description"]', "content", description);
  setMeta('link[rel="canonical"]', "href", url);
  setMeta('meta[name="robots"]', "content", indexable ? "index, follow, max-image-preview:large" : "noindex, nofollow");
}
