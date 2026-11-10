/**
 * Minimal i18n: string catalogs keyed by dotted ids, `{name}` placeholders.
 *
 * English is complete. Japanese is registered with an EMPTY catalog (locales/ja.json): every key it lacks falls
 * back to English, so translations can be added key by key, with no component changes, and be reviewed by
 * WMF Japan before they ship. Missing keys fall back to English, then to the key itself.
 */
import en from "./locales/en.json";
import ja from "./locales/ja.json";

const CATALOGS = { en, ja };
const STORAGE_KEY = "wmf-locale";

let locale = "en";
try {
  const saved = localStorage.getItem(STORAGE_KEY);
  if (saved && CATALOGS[saved]) locale = saved;
} catch {
  // storage unavailable: stay on English
}

export const availableLocales = () => Object.keys(CATALOGS);

function applyDocumentLanguage() {
  try {
    document.documentElement.setAttribute("lang", locale);
  } catch {
    // not in a browser
  }
}

export function setLocale(next) {
  if (!CATALOGS[next]) return;
  locale = next;
  applyDocumentLanguage();
  try {
    localStorage.setItem(STORAGE_KEY, next);
  } catch {
    // no-op
  }
}

applyDocumentLanguage();

export function getLocale() {
  return locale;
}

export function t(key, vars) {
  const text = (CATALOGS[locale] && CATALOGS[locale][key]) ?? CATALOGS.en[key] ?? key;
  if (!vars) return text;
  return text.replace(/\{(\w+)\}/g, (match, name) => (name in vars ? String(vars[name]) : match));
}

/** Whole yen, e.g. ¥1,500. */
export function formatJpy(amount) {
  if (amount === null || amount === undefined || amount === "") return "";
  return new Intl.NumberFormat("en-US", { style: "currency", currency: "JPY", maximumFractionDigits: 0 }).format(amount);
}

const JST_PARTS = new Intl.DateTimeFormat("en-CA", {
  timeZone: "Asia/Tokyo",
  year: "numeric",
  month: "2-digit",
  day: "2-digit",
  hour: "2-digit",
  minute: "2-digit",
  hourCycle: "h23",
});

function jstParts(iso) {
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return null;
  const parts = Object.fromEntries(JST_PARTS.formatToParts(date).map((p) => [p.type, p.value]));
  return parts;
}

/** "2026-10-10 10:00 JST" for display. */
export function formatJst(iso) {
  const p = iso && jstParts(iso);
  return p ? `${p.year}-${p.month}-${p.day} ${p.hour}:${p.minute} JST` : "";
}

/** ISO string -> value for <input type="datetime-local">, in JST. */
export function isoToJstInput(iso) {
  const p = iso && jstParts(iso);
  return p ? `${p.year}-${p.month}-${p.day}T${p.hour}:${p.minute}` : "";
}

/** <input type="datetime-local"> value (read as JST) -> ISO string with +09:00. */
export function jstInputToIso(value) {
  return value ? `${value}:00+09:00` : "";
}
