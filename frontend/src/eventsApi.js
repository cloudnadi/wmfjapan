// Public + member API for the event platform (/public/*). Always the real API.
import { getRegistrantToken, handleRegistrantUnauthorized } from "./registrantAuth";

const BASE = import.meta.env.VITE_API_BASE_URL;
const enc = encodeURIComponent;

async function call(path, { method = "GET", body, auth = "optional", query } = {}) {
  const token = getRegistrantToken();
  if (auth === "required" && !token) {
    const err = new Error("Your session has expired. Please sign in again.");
    err.status = 401;
    err.code = "session_expired";
    throw err;
  }
  const qs = query ? `?${new URLSearchParams(Object.entries(query).filter(([, v]) => v)).toString()}` : "";
  const response = await fetch(`${BASE}/public/${path}${qs}`, {
    method,
    headers: { "Content-Type": "application/json", ...(token && auth !== "never" ? { Authorization: token } : {}) },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  let data = {};
  try {
    data = await response.json();
  } catch {
    data = {};
  }
  if (!response.ok) {
    if (response.status === 401) handleRegistrantUnauthorized(401);
    const err = new Error(data.error || "Request failed");
    err.status = response.status;
    err.fields = data.fields || {};
    err.code = data.code || "";
    throw err;
  }
  return data;
}

export const listPublicEvents = (filter, category) => call("events", { query: { filter, category } });
export const getPublicEvent = (slug) => call(`events/${enc(slug)}`);
export const quoteTickets = (slug, tickets) => call(`events/${enc(slug)}/quote`, { method: "POST", body: { tickets } });
export const registerForEvent = (slug, payload) => call(`events/${enc(slug)}/register`, { method: "POST", body: payload });

export async function uploadEventFile(slug, fieldKey, file) {
  const data = await new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result).split(",")[1] || "");
    reader.onerror = () => reject(new Error("Could not read the file"));
    reader.readAsDataURL(file);
  });
  return call(`events/${enc(slug)}/upload`, { method: "POST", body: { fieldKey, name: file.name, contentType: file.type, data } });
}

// The check-in token in the link is the registrant's proof of ownership (also for guests).
export const getRegistrationByToken = (id, token) =>
  call(`registrations/${enc(id)}`, { query: { token }, auth: "never" });
export const payRegistration = (id, token, paymentMethod) =>
  call(`registrations/${enc(id)}/pay`, { method: "POST", query: { token }, body: { paymentMethod }, auth: "never" });

export const myEventRegistrations = () => call("my/registrations", { auth: "required" });
export const myEventRegistration = (id) => call(`my/registrations/${enc(id)}`, { auth: "required" });
export const editMyEventRegistration = (id, answers) =>
  call(`my/registrations/${enc(id)}`, { method: "PUT", body: { answers }, auth: "required" });
export const cancelMyEventRegistration = (id) => call(`my/registrations/${enc(id)}`, { method: "DELETE", auth: "required" });
