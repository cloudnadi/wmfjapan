/** PKCE (RFC 7636) and random-value helpers. Uses only the Web Crypto API. */

const base64Url = (bytes) =>
  btoa(String.fromCharCode(...bytes)).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");

export function randomString(byteLength = 32) {
  const bytes = new Uint8Array(byteLength);
  globalThis.crypto.getRandomValues(bytes);
  return base64Url(bytes);
}

/** 43–128 URL-safe characters, as the spec requires. */
export const createCodeVerifier = () => randomString(48);

export async function createCodeChallenge(verifier) {
  const digest = await globalThis.crypto.subtle.digest("SHA-256", new TextEncoder().encode(verifier));
  return base64Url(new Uint8Array(digest));
}
