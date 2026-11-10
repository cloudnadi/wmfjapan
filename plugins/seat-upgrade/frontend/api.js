// Seat Upgrade plugin — its own API client, deliberately independent of the
// host app's frontend/src/api.js internals (which aren't exported for reuse
// anyway). The only things this plugin borrows from the host are its auth
// primitives — a registrant's reg-token and an admin's Cognito id token —
// via the `@app` alias (see ../README.md for the full integration surface).

import { getRegistrantToken, handleRegistrantUnauthorized } from "@app/registrantAuth";
import { notifyAdminUnauthorized } from "@app/adminSession";

const API_BASE_URL = import.meta.env.VITE_API_BASE_URL;

async function requestRegistrantJson(path, options = {}) {
  const token = getRegistrantToken();
  if (!token) {
    const err = new Error("Your session has expired. Please sign in again.");
    err.status = 401;
    err.fields = {};
    throw err;
  }
  const response = await fetch(`${API_BASE_URL}${path}`, {
    ...options,
    headers: {
      Authorization: token,
      "Content-Type": "application/json",
      ...(options.headers || {}),
    },
  });

  let data = {};
  try {
    data = await response.json();
  } catch {
    data = {};
  }

  if (!response.ok) {
    handleRegistrantUnauthorized(response.status);
    // Server-side 500s (e.g. "Failed to update seat upgrade tier") include a
    // `detail` field with the actual exception message — surfacing it here
    // means an admin gets the real cause immediately instead of having to
    // ask someone to pull CloudWatch logs.
    const message = data.detail ? `${data.error || "Request failed"} (${data.detail})` : data.error || "Request failed";
    const err = new Error(message);
    err.fields = data.fields || {};
    err.status = response.status;
    throw err;
  }

  return data;
}

// api.js is imported by every plugin page, but only admin calls need
// Cognito — and it drags in the AWS SDK. Importing it lazily keeps it out of
// the registrant-facing entry bundle, same convention as the host's api.js.
async function getValidIdToken() {
  const cognito = await import("@app/cognito");
  return cognito.getValidIdToken();
}

async function requestAdminJson(path, options = {}) {
  const idToken = await getValidIdToken();

  const response = await fetch(`${API_BASE_URL}${path}`, {
    ...options,
    headers: {
      Authorization: idToken,
      "Content-Type": "application/json",
      ...(options.headers || {}),
    },
  });

  let data = {};
  try {
    data = await response.json();
  } catch {
    data = {};
  }

  if (!response.ok) {
    if (response.status === 401) notifyAdminUnauthorized();
    // Server-side 500s (e.g. "Failed to update seat upgrade tier") include a
    // `detail` field with the actual exception message — surfacing it here
    // means an admin gets the real cause immediately instead of having to
    // ask someone to pull CloudWatch logs.
    const message = data.detail ? `${data.error || "Request failed"} (${data.detail})` : data.error || "Request failed";
    const err = new Error(message);
    err.fields = data.fields || {};
    err.status = response.status;
    throw err;
  }

  return data;
}

// ── Registrant self-service ──────────────────────────────────────────────

export async function getMySeatUpgrades() {
  const data = await requestRegistrantJson("/my/seat-upgrades", { method: "GET" });
  return data.registrations || [];
}

// `picks` is [{tierId, count}, ...] — one entry per zone the registrant
// chose a quantity for; several zones in one purchase is how the Family
// Upgrade Discount's "valid in all zones" is exercised.
export async function requestSeatUpgrade({
  registrationId,
  picks,
  paymentMethod,
  dinnerAdultVegGuests,
  dinnerAdultNonVegGuests,
  dinnerKids914VegGuests,
  dinnerKids914NonVegGuests,
  dinnerKids68VegGuests,
  dinnerKids68NonVegGuests,
  dinnerKidsUnder6VegGuests,
  dinnerKidsUnder6NonVegGuests,
}) {
  const data = await requestRegistrantJson("/my/seat-upgrades", {
    method: "POST",
    body: JSON.stringify({
      registrationId,
      picks,
      paymentMethod,
      dinnerAdultVegGuests,
      dinnerAdultNonVegGuests,
      dinnerKids914VegGuests,
      dinnerKids914NonVegGuests,
      dinnerKids68VegGuests,
      dinnerKids68NonVegGuests,
      dinnerKidsUnder6VegGuests,
      dinnerKidsUnder6NonVegGuests,
    }),
  });
  return data.upgrade;
}

// Cancels a pending request outright, or — for an already-paid one — starts
// a refund and returns it in "refund_requested" status (see
// seat_upgrade_logic.cancel_upgrade_request for why capacity stays held
// until an admin finalizes it).
export async function cancelSeatUpgrade(upgradeId) {
  const data = await requestRegistrantJson(
    `/my/seat-upgrades/${encodeURIComponent(upgradeId)}`,
    { method: "DELETE" }
  );
  return data.upgrade;
}

// Start a hosted checkout with a payment provider for a pending request.
// Returns { checkoutUrl, provider } — the caller redirects the browser there.
// `provider` is "paypay" or "square"; bank transfer has no checkout step,
// it's just account details shown in the UI and confirmed by an admin.
export async function createSeatUpgradeCheckout(upgradeId, provider) {
  const data = await requestRegistrantJson(
    `/my/seat-upgrades/${encodeURIComponent(upgradeId)}/checkout`,
    { method: "POST", body: JSON.stringify({ provider }) }
  );
  return data;
}

// Re-fetch one request's current status — used by the post-checkout return
// page to reconcile immediately rather than waiting on the webhook alone.
export async function getSeatUpgradeStatus(upgradeId) {
  const data = await requestRegistrantJson(
    `/my/seat-upgrades/${encodeURIComponent(upgradeId)}`,
    { method: "GET" }
  );
  return data.upgrade;
}

// ── Admin ─────────────────────────────────────────────────────────────────

export async function getSeatUpgradeTiers(eventType) {
  if (!eventType) return [];
  const data = await requestAdminJson(
    `/admin/events/${encodeURIComponent(eventType)}/seat-upgrade-tiers`,
    { method: "GET" }
  );
  return data.tiers || [];
}

export async function saveSeatUpgradeTier(eventType, payload, tierId = "") {
  const suffix = tierId ? `/${encodeURIComponent(tierId)}` : "";
  const data = await requestAdminJson(
    `/admin/events/${encodeURIComponent(eventType)}/seat-upgrade-tiers${suffix}`,
    {
      method: tierId ? "PATCH" : "POST",
      body: JSON.stringify(payload),
    }
  );
  return data.tier;
}

export async function deleteSeatUpgradeTier(eventType, tierId) {
  return requestAdminJson(
    `/admin/events/${encodeURIComponent(eventType)}/seat-upgrade-tiers/${encodeURIComponent(tierId)}`,
    { method: "DELETE" }
  );
}

// Uploads a seat map image for an event (shown on the registrant-facing
// /my/seat-upgrade page — see SeatUpgrade.jsx) and returns its public URL.
// Called from the HOST's Events form (AdminDashboard.jsx) via the @plugins
// alias when an admin enables the plugin, not from this plugin's own admin
// component — the image lives on the event, so its capture belongs on the
// Events form, same place Payment Links and the other seatUpgrade fields do.
export async function uploadSeatUpgradeSeatMap(eventType, file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = async () => {
      try {
        const base64 = reader.result.split(",")[1];
        const data = await requestAdminJson(
          `/admin/events/${encodeURIComponent(eventType)}/seat-upgrade-seat-map`,
          { method: "POST", body: JSON.stringify({ contentType: file.type, data: base64 }) }
        );
        resolve(data); // { imageUrl }
      } catch (err) {
        reject(err);
      }
    };
    reader.onerror = () => reject(new Error("Failed to read image file"));
    reader.readAsDataURL(file);
  });
}

export async function getSeatUpgradeRequests(eventType) {
  if (!eventType) return [];
  const data = await requestAdminJson(
    `/admin/events/${encodeURIComponent(eventType)}/seat-upgrades`,
    { method: "GET" }
  );
  return data.upgrades || [];
}

export async function updateSeatUpgradeStatus(eventType, upgradeId, status) {
  const data = await requestAdminJson(
    `/admin/events/${encodeURIComponent(eventType)}/seat-upgrades/${encodeURIComponent(upgradeId)}`,
    {
      method: "PATCH",
      body: JSON.stringify({ status }),
    }
  );
  return data.upgrade;
}

// Emails every paid event/combo registrant that premium seating is
// available. Re-triggerable — safe to call again later (e.g. as a
// reminder, or once new people have registered); it always sends to the
// full current list rather than tracking who's already been sent.
// `celebrityDinner`, when given, is mentioned in the same email — see
// manage_seat_upgrades/handler.py's _normalize_announcement_celebrity_dinner
// for the shape (this Lambda has no access to the Celebrity Dinner tables
// itself, so the caller supplies whatever it wants said about it).
// Returns { sent, failed, total, eventName, message? }.
export async function sendSeatUpgradeAnnouncement(eventType, { celebrityDinner } = {}) {
  return requestAdminJson(
    `/admin/events/${encodeURIComponent(eventType)}/seat-upgrade-announcement`,
    { method: "POST", body: JSON.stringify({ celebrityDinner }) }
  );
}

// ── Email templates ──────────────────────────────────────────────────────
//
// Global to the plugin (not per-event) — see
// manage_seat_upgrades/handler.py's handle_email_templates docstring for
// why these still ride the /admin/events/{eventType}/... mount point.
// "_global" is a placeholder the server ignores for these routes only, so
// the Email Templates tab works without an event selected.
const EMAIL_TEMPLATES_EVENT_PLACEHOLDER = "_global";

export async function getSeatUpgradeEmailTemplates() {
  const data = await requestAdminJson(
    `/admin/events/${EMAIL_TEMPLATES_EVENT_PLACEHOLDER}/seat-upgrade-email-templates`,
    { method: "GET" }
  );
  return data.templates || [];
}

export async function saveSeatUpgradeEmailTemplate(templateKey, fields) {
  const data = await requestAdminJson(
    `/admin/events/${EMAIL_TEMPLATES_EVENT_PLACEHOLDER}/seat-upgrade-email-templates/${encodeURIComponent(templateKey)}`,
    { method: "PATCH", body: JSON.stringify(fields) }
  );
  return data.template;
}

export async function resetSeatUpgradeEmailTemplate(templateKey) {
  const data = await requestAdminJson(
    `/admin/events/${EMAIL_TEMPLATES_EVENT_PLACEHOLDER}/seat-upgrade-email-templates/${encodeURIComponent(templateKey)}`,
    { method: "DELETE" }
  );
  return data.template;
}

// Renders draft (not-yet-saved) field values against sample data — nothing
// is persisted. Returns { subject, html }.
export async function previewSeatUpgradeEmailTemplate(templateKey, fields) {
  return requestAdminJson(
    `/admin/events/${EMAIL_TEMPLATES_EVENT_PLACEHOLDER}/seat-upgrade-email-templates/${encodeURIComponent(templateKey)}/preview`,
    { method: "POST", body: JSON.stringify(fields) }
  );
}
