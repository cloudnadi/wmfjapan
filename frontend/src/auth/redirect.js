/**
 * Post-sign-in redirect safety. A return path comes from a query string or storage, i.e. from
 * outside, so it is only ever used if it is a same-site path under an allowed prefix. Anything else
 * (absolute URLs, //host, backslashes, javascript:, control characters, unknown areas) becomes the fallback.
 */

// Areas a visitor may be sent back to after signing in.
export const ALLOWED_RETURN_PREFIXES = ["/events", "/my/events", "/signin"];

export function sanitizeReturnPath(value, fallback = "/") {
  if (typeof value !== "string" || !value || value.length > 512) return fallback;
  // eslint-disable-next-line no-control-regex
  if (/[\u0000-\u001f\u007f\\]/.test(value)) return fallback;
  if (!value.startsWith("/") || value.startsWith("//")) return fallback;
  let url;
  try {
    url = new URL(value, "https://same-site.invalid");
  } catch {
    return fallback;
  }
  if (url.origin !== "https://same-site.invalid") return fallback;
  const path = url.pathname;
  if (path === "/") return `/${url.search}${url.hash}`;
  const allowed = ALLOWED_RETURN_PREFIXES.some((prefix) => path === prefix || path.startsWith(`${prefix}/`));
  if (!allowed || path.includes("/../") || path.endsWith("/..")) return fallback;
  return `${path}${url.search}${url.hash}`;
}
