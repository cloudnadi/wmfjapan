// Admin API client for the event platform (/admin/event-platform/*).
import { requestAdminJson } from "./api";

const BASE = "/admin/event-platform";
const enc = encodeURIComponent;
const json = (method, body) => ({ method, body: JSON.stringify(body ?? {}) });

export const getMe = () => requestAdminJson(`${BASE}/me`);
export const listEvents = (status) =>
  requestAdminJson(`${BASE}/events${status ? `?status=${enc(status)}` : ""}`);
export const getEvent = (id) => requestAdminJson(`${BASE}/events/${enc(id)}`);
export const createEvent = (payload) => requestAdminJson(`${BASE}/events`, json("POST", payload));
export const updateEvent = (id, payload) => requestAdminJson(`${BASE}/events/${enc(id)}`, json("PUT", payload));
export const deleteEvent = (id) => requestAdminJson(`${BASE}/events/${enc(id)}`, { method: "DELETE" });
export const setEventStatus = (id, status) =>
  requestAdminJson(`${BASE}/events/${enc(id)}/status`, json("POST", { status }));
export const duplicateEvent = (id) => requestAdminJson(`${BASE}/events/${enc(id)}/duplicate`, json("POST"));
export const createTicket = (id, payload) =>
  requestAdminJson(`${BASE}/events/${enc(id)}/tickets`, json("POST", payload));
export const updateTicket = (id, ticketId, payload) =>
  requestAdminJson(`${BASE}/events/${enc(id)}/tickets/${enc(ticketId)}`, json("PUT", payload));
export const deleteTicket = (id, ticketId) =>
  requestAdminJson(`${BASE}/events/${enc(id)}/tickets/${enc(ticketId)}`, { method: "DELETE" });
export const uploadEventImage = (id, contentType, data) =>
  requestAdminJson(`${BASE}/events/${enc(id)}/images`, json("POST", { contentType, data }));
export const getEventAudit = (id) => requestAdminJson(`${BASE}/events/${enc(id)}/audit`);

// ── Registration form builder ────────────────────────────────────────────────
export const getEventForm = (id) => requestAdminJson(`${BASE}/events/${enc(id)}/form`);
export const saveEventForm = (id, fields, baseVersion) =>
  requestAdminJson(`${BASE}/events/${enc(id)}/form`, json("PUT", { fields, baseVersion }));
export const getEventFormVersion = (id, version) =>
  requestAdminJson(`${BASE}/events/${enc(id)}/form/versions/${enc(version)}`);
export const validateEventForm = (id, answers, asMember) =>
  requestAdminJson(`${BASE}/events/${enc(id)}/form/validate`, json("POST", { answers, asMember }));
export const applyFormSource = (id, source, baseVersion) =>
  requestAdminJson(`${BASE}/events/${enc(id)}/form/apply`, json("POST", { ...source, baseVersion }));
export const listFormTemplates = () => requestAdminJson(`${BASE}/form-templates`);
export const createFormTemplate = (name, fields) =>
  requestAdminJson(`${BASE}/form-templates`, json("POST", { name, fields }));
export const deleteFormTemplate = (id) => requestAdminJson(`${BASE}/form-templates/${enc(id)}`, { method: "DELETE" });

// ── Registration management ──────────────────────────────────────────────────
const qs = (params) => {
  const entries = Object.entries(params || {}).filter(([, v]) => v !== "" && v !== undefined && v !== null);
  return entries.length ? `?${new URLSearchParams(entries).toString()}` : "";
};
export const getRegDashboard = (id) => requestAdminJson(`${BASE}/events/${enc(id)}/dashboard`);
export const listRegistrations = (id, params) => requestAdminJson(`${BASE}/events/${enc(id)}/registrations${qs(params)}`);
export const getRegistration = (id, rid) => requestAdminJson(`${BASE}/events/${enc(id)}/registrations/${enc(rid)}`);
export const addRegistration = (id, payload) =>
  requestAdminJson(`${BASE}/events/${enc(id)}/registrations`, json("POST", payload));
export const editRegistration = (id, rid, payload) =>
  requestAdminJson(`${BASE}/events/${enc(id)}/registrations/${enc(rid)}`, json("PUT", payload));
export const registrationAction = (id, rid, action, payload) =>
  requestAdminJson(`${BASE}/events/${enc(id)}/registrations/${enc(rid)}/${action}`, json("POST", payload));
export const getRegistrationFileLink = (id, rid, fileId) =>
  requestAdminJson(`${BASE}/events/${enc(id)}/registrations/${enc(rid)}/files/${enc(fileId)}`);
export const scanCheckIn = (id, code, undo) =>
  requestAdminJson(`${BASE}/events/${enc(id)}/check-in`, json("POST", { code, undo: !!undo }));
export const exportRegistrations = (id, params) =>
  requestAdminJson(`${BASE}/events/${enc(id)}/export${qs(params)}`);
export const emailRegistrants = (id, payload) =>
  requestAdminJson(`${BASE}/events/${enc(id)}/email`, json("POST", payload));

/** Save an export response ({filename, contentType, base64}) through the browser's download flow. */
export function downloadExport(file) {
  const bytes = Uint8Array.from(atob(file.base64), (c) => c.charCodeAt(0));
  const url = URL.createObjectURL(new Blob([bytes], { type: file.contentType }));
  const link = document.createElement("a");
  link.href = url;
  link.download = file.filename;
  document.body.appendChild(link);
  link.click();
  link.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
