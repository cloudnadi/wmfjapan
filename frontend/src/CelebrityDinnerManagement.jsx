import { useEffect, useMemo, useState } from "react";
import {
  downloadCSV,
  getCelebrityDinnerConfig,
  getCelebrityDinnerRegistrations,
  saveCelebrityDinnerConfig,
  updateCelebrityDinnerRegistrationStatus,
} from "./api";
// The host importing from a plugin's own frontend/api.js — same sanctioned
// direction as MyRegistrations.jsx's getMySeatUpgrades import — lets one
// button send a single email covering both offers, without the seat-upgrade
// plugin's Lambda ever needing to know the Dinner with Celebrity tables exist
// (see manage_seat_upgrades/handler.py's _normalize_announcement_celebrity_dinner).
import { sendSeatUpgradeAnnouncement } from "@plugins/seat-upgrade/frontend/api";

const yen = (value) => `¥${Number(value || 0).toLocaleString("en-US")}`;

// Mirrors AdminDashboard.jsx's own csvEscape — not exported from there, so
// duplicated here rather than reaching into that file for a 3-line helper.
function csvEscape(value) {
  const text = String(value ?? "");
  return /[",\n]/.test(text) ? `"${text.replace(/"/g, '""')}"` : text;
}

const PAYMENT_METHODS = [
  { key: "paypay", label: "PayPay" },
  { key: "square", label: "Square (Credit Card)" },
  { key: "bank", label: "Bank Transfer" },
];
const DEFAULT_PAYMENT_METHODS = PAYMENT_METHODS.map((m) => m.key);

const REGISTRATION_STATUS_LABELS = {
  pending: "Payment Pending",
  paid: "Paid",
  refund_requested: "Refund Requested",
  cancelled: "Cancelled",
};

const REFUND_STATUS_LABELS = {
  manual_pending: "Needs manual refund",
  provider_pending: "Refund pending with provider",
  provider_completed: "Refund completed",
  provider_rejected: "Refund rejected by provider",
  provider_failed: "Refund attempt failed",
};

const INITIAL_FORM = {
  registrationStartsAt: "",
  registrationEndsAt: "",
  dinnerDate: "",
  venue: "",
  venueAddress: "",
  googleMapUrl: "",
  dinnerPriceAdults: "0",
  dinnerPriceKids914: "0",
  dinnerPriceKids68: "0",
  currency: "JPY",
  conditions: "",
  notes: "",
  addOnEnabled: false,
  addOnDiscountPercent: "0",
  paymentMethods: DEFAULT_PAYMENT_METHODS,
  cancellationEnabled: true,
  refundWindowEndsAt: "",
  maxTotalGuests: "0",
};

function toLocalDateTimeValue(value) {
  if (!value) return "";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "";
  const offsetMs = date.getTimezoneOffset() * 60 * 1000;
  return new Date(date.getTime() - offsetMs).toISOString().slice(0, 16);
}

function toIsoString(value) {
  if (!value) return "";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? "" : date.toISOString();
}

// Mirrors the backend's registration_window_status: the event itself is
// already enabled (Events admin form) at this point, so what's worth
// showing here is whether the registration window is actually open.
function getWindowStatus(form) {
  const now = new Date().toISOString();
  const starts = toIsoString(form.registrationStartsAt);
  const ends = toIsoString(form.registrationEndsAt);
  if (starts && now < starts) return "not_open";
  if (ends && now > ends) return "closed";
  return "open";
}

const WINDOW_STATUS_LABELS = {
  not_open: "Not Open Yet",
  open: "Open",
  closed: "Closed",
};

function formToPayload(form) {
  return {
    registrationStartsAt: toIsoString(form.registrationStartsAt),
    registrationEndsAt: toIsoString(form.registrationEndsAt),
    dinnerDate: toIsoString(form.dinnerDate),
    venue: form.venue.trim(),
    venueAddress: form.venueAddress.trim(),
    googleMapUrl: form.googleMapUrl.trim(),
    dinnerPriceAdults: Number(form.dinnerPriceAdults || 0),
    dinnerPriceKids914: Number(form.dinnerPriceKids914 || 0),
    dinnerPriceKids68: Number(form.dinnerPriceKids68 || 0),
    currency: form.currency.trim() || "JPY",
    conditions: form.conditions.trim(),
    notes: form.notes.trim(),
    addOnEnabled: form.addOnEnabled === true,
    addOnDiscountPercent: Number(form.addOnDiscountPercent || 0),
    paymentMethods: form.paymentMethods,
    cancellationEnabled: form.cancellationEnabled === true,
    refundWindowEndsAt: toIsoString(form.refundWindowEndsAt),
    maxTotalGuests: Number(form.maxTotalGuests || 0),
  };
}

function configToForm(config) {
  return {
    registrationStartsAt: toLocalDateTimeValue(config.registrationStartsAt),
    registrationEndsAt: toLocalDateTimeValue(config.registrationEndsAt),
    dinnerDate: toLocalDateTimeValue(config.dinnerDate),
    venue: config.venue || "",
    venueAddress: config.venueAddress || "",
    googleMapUrl: config.googleMapUrl || "",
    dinnerPriceAdults: String(config.dinnerPriceAdults ?? 0),
    dinnerPriceKids914: String(config.dinnerPriceKids914 ?? 0),
    dinnerPriceKids68: String(config.dinnerPriceKids68 ?? 0),
    currency: config.currency || "JPY",
    conditions: config.conditions || "",
    notes: config.notes || "",
    addOnEnabled: config.addOnEnabled === true,
    addOnDiscountPercent: String(config.addOnDiscountPercent ?? 0),
    paymentMethods:
      Array.isArray(config.paymentMethods) && config.paymentMethods.length
        ? config.paymentMethods.filter((m) => DEFAULT_PAYMENT_METHODS.includes(m))
        : DEFAULT_PAYMENT_METHODS,
    cancellationEnabled: config.cancellationEnabled !== false,
    refundWindowEndsAt: toLocalDateTimeValue(config.refundWindowEndsAt),
    maxTotalGuests: String(config.maxTotalGuests ?? 0),
  };
}

export default function CelebrityDinnerManagement({ events = [], initialEventType = "" }) {
  const selectableEvents = useMemo(
    () =>
      events
        .filter((event) => event.eventType)
        .sort((a, b) => String(a.eventName || a.eventType).localeCompare(String(b.eventName || b.eventType))),
    [events]
  );
  const fallbackEventType = selectableEvents[0]?.eventType || "";
  const [eventType, setEventType] = useState(initialEventType || fallbackEventType);
  const [form, setForm] = useState(INITIAL_FORM);
  const [registrations, setRegistrations] = useState([]);
  const [stats, setStats] = useState({
    count: 0,
    activeCount: 0,
    cancelledCount: 0,
    totalGuests: 0,
    activeGuests: 0,
    cancelledGuests: 0,
    totalAmount: 0,
    maxTotalGuests: 0,
    remainingCapacity: null,
  });
  const [isLoading, setIsLoading] = useState(false);
  const [isSaving, setIsSaving] = useState(false);
  const [busyRegistrationId, setBusyRegistrationId] = useState("");
  const [error, setError] = useState("");
  const [success, setSuccess] = useState("");
  const [isSendingAnnouncement, setIsSendingAnnouncement] = useState(false);
  const [lastAnnouncement, setLastAnnouncement] = useState(null); // { sent, failed, total, eventType, at } | null
  const [dinnerEnabledForEvent, setDinnerEnabledForEvent] = useState(false);

  useEffect(() => {
    if (!eventType && fallbackEventType) {
      setEventType(fallbackEventType);
    }
  }, [eventType, fallbackEventType]);

  useEffect(() => {
    if (initialEventType && initialEventType !== eventType) {
      setEventType(initialEventType);
    }
  }, [initialEventType, eventType]);

  useEffect(() => {
    if (eventType) {
      loadAll(eventType);
    } else {
      setForm(INITIAL_FORM);
      setRegistrations([]);
      setDinnerEnabledForEvent(false);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [eventType]);

  async function loadAll(nextEventType = eventType) {
    if (!nextEventType) return;
    setIsLoading(true);
    setError("");
    try {
      const [config, regData] = await Promise.all([
        getCelebrityDinnerConfig(nextEventType),
        getCelebrityDinnerRegistrations(nextEventType),
      ]);
      setForm(configToForm(config || {}));
      setDinnerEnabledForEvent(config?.enabled === true);
      setRegistrations(regData?.registrations || []);
      setStats({
        count: regData?.count || 0,
        activeCount: regData?.activeCount || 0,
        cancelledCount: regData?.cancelledCount || 0,
        totalGuests: regData?.totalGuests || 0,
        activeGuests: regData?.activeGuests || 0,
        cancelledGuests: regData?.cancelledGuests || 0,
        totalAmount: regData?.totalAmount || 0,
        maxTotalGuests: regData?.maxTotalGuests || 0,
        remainingCapacity: regData?.remainingCapacity ?? null,
      });
    } catch (err) {
      setError(err.message || "Failed to load the Dinner with Celebrity setup.");
      setForm(INITIAL_FORM);
      setRegistrations([]);
      setDinnerEnabledForEvent(false);
    } finally {
      setIsLoading(false);
    }
  }

  function handleInputChange(event) {
    const { name, value, type, checked } = event.target;
    setForm((prev) => ({ ...prev, [name]: type === "checkbox" ? checked : value }));
    setError("");
    setSuccess("");
  }

  function handlePaymentMethodToggle(methodKey, checked) {
    setForm((prev) => ({
      ...prev,
      paymentMethods: checked
        ? [...prev.paymentMethods, methodKey]
        : prev.paymentMethods.filter((m) => m !== methodKey),
    }));
    setError("");
    setSuccess("");
  }

  async function handleUpdateRegistrationStatus(reg, status) {
    setBusyRegistrationId(reg.dinnerRegistrationId);
    setError("");
    setSuccess("");
    try {
      const updated = await updateCelebrityDinnerRegistrationStatus(eventType, reg.dinnerRegistrationId, status);
      setRegistrations((prev) =>
        prev.map((item) => (item.dinnerRegistrationId === updated.dinnerRegistrationId ? { ...item, ...updated } : item))
      );
      setSuccess(`Marked as ${REGISTRATION_STATUS_LABELS[status] || status}.`);
    } catch (err) {
      setError(err.message || "Failed to update the Dinner with Celebrity sign-up.");
    } finally {
      setBusyRegistrationId("");
    }
  }

  // One row per sign-up, standalone and Zone Upgrade bundled bookings alike
  // (see loadAll's regData.registrations) — guest counts broken out by
  // category so an admin can pivot/filter them in a spreadsheet. Same
  // convention as AdminDashboard.jsx's Sadhya Slot Allocations export.
  function handleExportRegistrationsCSV() {
    const header = [
      "Signed Up", "Name", "Email", "Source",
      "Adults Veg", "Adults Non-Veg",
      "Kids 9-14 Veg", "Kids 9-14 Non-Veg",
      "Kids 6-8 Veg", "Kids 6-8 Non-Veg",
      "Kids Under 6 Veg", "Kids Under 6 Non-Veg",
      "Amount", "Payment Method", "Transaction ID", "Status",
    ];
    const lines = [header, ...registrations.map((reg) => [
      reg.submittedAt ? new Date(reg.submittedAt).toLocaleString() : "",
      reg.name || "",
      reg.email || "",
      reg.source === "zoneUpgrade" ? `Via Zone Upgrade${reg.tierName ? ` (${reg.tierName})` : ""}` : "Standalone",
      reg.numberOfAdultsVeg || 0,
      reg.numberOfAdultsNonVeg || 0,
      reg.numberOfKids914Veg || 0,
      reg.numberOfKids914NonVeg || 0,
      reg.numberOfKids68Veg || 0,
      reg.numberOfKids68NonVeg || 0,
      reg.numberOfKidsUnder6Veg || 0,
      reg.numberOfKidsUnder6NonVeg || 0,
      reg.totalAmount || 0,
      reg.paymentMethod || "",
      reg.paymentReference || reg.providerPaymentId || "",
      REGISTRATION_STATUS_LABELS[reg.status] || reg.status || "",
    ])].map((line) => line.map(csvEscape).join(","));
    const timestamp = new Date().toISOString().split("T")[0];
    downloadCSV(new Blob([lines.join("\n")], { type: "text/csv" }), `celebrity-dinner-signups-${eventType}-${timestamp}.csv`);
  }

  async function handleSave(event) {
    event.preventDefault();
    setError("");
    setSuccess("");

    if (!eventType) {
      setError("Select an event first.");
      return;
    }

    const payload = formToPayload(form);
    if (payload.dinnerPriceAdults < 0 || payload.dinnerPriceKids914 < 0 || payload.dinnerPriceKids68 < 0) {
      setError("Dinner prices must be 0 or greater.");
      return;
    }
    if (payload.addOnDiscountPercent < 0 || payload.addOnDiscountPercent > 100) {
      setError("Add-on discount must be between 0 and 100.");
      return;
    }
    if (payload.maxTotalGuests < 0) {
      setError("Maximum total guests must be 0 or greater.");
      return;
    }
    if (
      payload.registrationStartsAt &&
      payload.registrationEndsAt &&
      payload.registrationStartsAt > payload.registrationEndsAt
    ) {
      setError("Registration start must be before the registration end.");
      return;
    }
    if (!payload.paymentMethods || payload.paymentMethods.length === 0) {
      setError("Select at least one payment method.");
      return;
    }

    setIsSaving(true);
    try {
      const saved = await saveCelebrityDinnerConfig(eventType, payload);
      setForm(configToForm(saved));
      setSuccess("Dinner with Celebrity settings saved.");
    } catch (err) {
      setError(err.message || "Failed to save the Dinner with Celebrity setup.");
    } finally {
      setIsSaving(false);
    }
  }

  // Sends the seat-upgrade plugin's own "Premium Seating Now Available"
  // broadcast, adding a Dinner with Celebrity section to it when this event has
  // Dinner with Celebrity configured — one email, both offers, instead of two
  // separate admin actions. Requires the event to have at least one active
  // seating zone (the plugin's own endpoint enforces that); the Celebrity
  // Dinner part is purely additive.
  async function handleSendAnnouncement() {
    const verb = lastAnnouncement?.eventType === eventType ? "Resend" : "Send";
    if (
      !window.confirm(
        `${verb} the "Premium Seating${dinnerEnabledForEvent ? " & Dinner with Celebrity" : ""}" email to every ` +
          "paid registrant on this event? This emails everyone eligible right now, including anyone already notified."
      )
    ) {
      return;
    }
    setIsSendingAnnouncement(true);
    setError("");
    setSuccess("");
    try {
      const celebrityDinner = dinnerEnabledForEvent ? formToPayload(form) : undefined;
      const result = await sendSeatUpgradeAnnouncement(eventType, { celebrityDinner });
      setLastAnnouncement({ ...result, eventType, at: new Date().toISOString() });
      setSuccess(
        result.total === 0
          ? result.message || "No paid event/combo registrants to notify yet."
          : `Announcement sent to ${result.sent} of ${result.total} registrant(s)${
              result.failed ? ` (${result.failed} failed — check CloudWatch logs)` : ""
            }.`
      );
    } catch (err) {
      setError(err.message || "Failed to send the announcement.");
    } finally {
      setIsSendingAnnouncement(false);
    }
  }

  const announcementForThisEvent =
    lastAnnouncement && lastAnnouncement.eventType === eventType ? lastAnnouncement : null;

  return (
    <>
      <div className="admin-stats">
        <div className="stat-box">
          <div className="stat-value">{WINDOW_STATUS_LABELS[getWindowStatus(form)]}</div>
          <div className="stat-label">Registration</div>
        </div>
        <div className="stat-box">
          <div className="stat-value">{stats.count}</div>
          <div className="stat-label">Sign-ups</div>
          <div className="stat-split">
            {stats.activeCount} active · {stats.cancelledCount} cancelled
          </div>
        </div>
        <div className="stat-box">
          <div className="stat-value">{stats.totalGuests}</div>
          <div className="stat-label">Guests</div>
          <div className="stat-split">
            {stats.activeGuests} active · {stats.cancelledGuests} cancelled
          </div>
        </div>
        {stats.maxTotalGuests > 0 ? (
          <div className="stat-box">
            <div className="stat-value">{stats.remainingCapacity ?? "—"}</div>
            <div className="stat-label">Seats Left (of {stats.maxTotalGuests})</div>
          </div>
        ) : null}
        <div className="stat-box">
          <div className="stat-value">
            {form.currency} {stats.totalAmount}
          </div>
          <div className="stat-label">Expected Revenue</div>
        </div>
      </div>

      <div className="admin-controls">
        <select
          value={eventType}
          onChange={(event) => setEventType(event.target.value)}
          className="search-input"
          aria-label="Select event for Dinner with Celebrity"
        >
          <option value="">Select event...</option>
          {selectableEvents.map((event) => (
            <option key={event.eventType} value={event.eventType}>
              {event.eventName || event.eventType}
            </option>
          ))}
        </select>
        <button className="button secondary" type="button" onClick={() => loadAll()} disabled={!eventType || isLoading}>
          {isLoading ? "Refreshing..." : "Refresh"}
        </button>
        <button
          className="button primary"
          type="button"
          onClick={handleSendAnnouncement}
          disabled={!eventType || isSendingAnnouncement}
          title="Emails every paid registrant on this event that premium seating (and, if configured, the Dinner with Celebrity) is open."
        >
          {isSendingAnnouncement
            ? "Sending..."
            : announcementForThisEvent
              ? "Resend Announcement"
              : "Send Announcement"}
        </button>
      </div>

      {announcementForThisEvent ? (
        <p className="field-note" style={{ marginTop: "-8px", marginBottom: "16px" }}>
          Last sent {new Date(announcementForThisEvent.at).toLocaleString()}: {announcementForThisEvent.sent} of{" "}
          {announcementForThisEvent.total} registrant(s) notified
          {announcementForThisEvent.failed ? `, ${announcementForThisEvent.failed} failed` : ""}.
        </p>
      ) : null}
      {error ? <div className="error-banner" style={{ marginBottom: "16px" }}>{error}</div> : null}
      {success ? <div className="success-banner" style={{ marginBottom: "16px" }}>{success}</div> : null}

      <div className="partner-management-grid entry-slot-grid">
        <form className="partner-panel partner-form-panel" onSubmit={handleSave}>
          <div className="panel-heading">
            <div>
              <h3>Dinner with Celebrity Setup</h3>
              <p>
                Set the registration window and details for this event. To turn the Dinner with Celebrity
                on or off entirely, use the "Dinner with Celebrity" field on the event's own edit form under Events.
              </p>
            </div>
          </div>

          <div className="field-row">
            <div className="field">
              <label htmlFor="dinnerRegStarts">Registration Starts</label>
              <input
                id="dinnerRegStarts"
                name="registrationStartsAt"
                type="datetime-local"
                value={form.registrationStartsAt}
                onChange={handleInputChange}
                disabled={isSaving}
              />
            </div>
            <div className="field">
              <label htmlFor="dinnerRegEnds">Registration Ends</label>
              <input
                id="dinnerRegEnds"
                name="registrationEndsAt"
                type="datetime-local"
                value={form.registrationEndsAt}
                onChange={handleInputChange}
                disabled={isSaving}
              />
            </div>
          </div>

          <div className="field" style={{ marginBottom: "20px" }}>
            <label htmlFor="dinnerDate">Dinner Date</label>
            <input
              id="dinnerDate"
              name="dinnerDate"
              type="datetime-local"
              value={form.dinnerDate}
              onChange={handleInputChange}
              disabled={isSaving}
            />
            <span className="field-note">
              When the Dinner with Celebrity itself takes place — separate from the registration window above.
              Shown to registrants and included in their confirmation emails.
            </span>
          </div>

          <div className="field" style={{ marginBottom: "20px" }}>
            <label htmlFor="dinnerMaxTotalGuests">Maximum Total Guests</label>
            <input
              id="dinnerMaxTotalGuests"
              name="maxTotalGuests"
              type="number"
              min="0"
              value={form.maxTotalGuests}
              onChange={handleInputChange}
              disabled={isSaving}
            />
            <span className="field-note">
              Overall headcount cap across every booking for this dinner — standalone sign-ups and Zone
              Upgrade add-on bookings combined. 0 = unlimited. New bookings are refused once this is reached.
            </span>
          </div>

          <div className="field-row">
            <div className="field">
              <label htmlFor="dinnerVenue">Venue</label>
              <input
                id="dinnerVenue"
                name="venue"
                type="text"
                value={form.venue}
                onChange={handleInputChange}
                placeholder="Grand Ballroom"
                disabled={isSaving}
              />
            </div>
            <div className="field">
              <label htmlFor="dinnerVenueAddress">Venue Address</label>
              <input
                id="dinnerVenueAddress"
                name="venueAddress"
                type="text"
                value={form.venueAddress}
                onChange={handleInputChange}
                disabled={isSaving}
              />
            </div>
          </div>

          <div className="field" style={{ marginBottom: "20px" }}>
            <label htmlFor="dinnerGoogleMapUrl">Google Map URL</label>
            <input
              id="dinnerGoogleMapUrl"
              name="googleMapUrl"
              type="url"
              value={form.googleMapUrl}
              onChange={handleInputChange}
              placeholder="https://maps.google.com/..."
              disabled={isSaving}
            />
          </div>

          <div className="field-row">
            <div className="field">
              <label htmlFor="dinnerPriceAdults">Price — Adult</label>
              <input
                id="dinnerPriceAdults"
                name="dinnerPriceAdults"
                type="number"
                min="0"
                value={form.dinnerPriceAdults}
                onChange={handleInputChange}
                disabled={isSaving}
              />
            </div>
            <div className="field">
              <label htmlFor="dinnerPriceKids914">Price — Kid (9-14)</label>
              <input
                id="dinnerPriceKids914"
                name="dinnerPriceKids914"
                type="number"
                min="0"
                value={form.dinnerPriceKids914}
                onChange={handleInputChange}
                disabled={isSaving}
              />
            </div>
          </div>

          <div className="field-row">
            <div className="field">
              <label htmlFor="dinnerPriceKids68">Price — Kid (6-8)</label>
              <input
                id="dinnerPriceKids68"
                name="dinnerPriceKids68"
                type="number"
                min="0"
                value={form.dinnerPriceKids68}
                onChange={handleInputChange}
                disabled={isSaving}
              />
            </div>
            <div className="field">
              <label htmlFor="dinnerCurrency">Currency</label>
              <input
                id="dinnerCurrency"
                name="currency"
                type="text"
                value={form.currency}
                onChange={handleInputChange}
                disabled={isSaving}
              />
            </div>
          </div>

          <p className="field-note" style={{ marginTop: "-10px", marginBottom: "20px" }}>
            Kids under 6 are never priced — they're counted for headcount only, at no charge.
          </p>

          <div className="field" style={{ marginBottom: "20px" }}>
            <label>Payment Methods</label>
            <div className="btn-row">
              {PAYMENT_METHODS.map((method) => (
                <label key={method.key} style={{ display: "flex", alignItems: "center", gap: "6px", fontWeight: 400 }}>
                  <input
                    type="checkbox"
                    checked={form.paymentMethods.includes(method.key)}
                    onChange={(event) => handlePaymentMethodToggle(method.key, event.target.checked)}
                    disabled={isSaving}
                  />
                  {method.label}
                </label>
              ))}
            </div>
            <span className="field-note">
              Which payment methods registrants can use for a standalone Dinner with Celebrity sign-up. Bank transfer
              bookings must be marked paid by hand once the transfer is confirmed.
            </span>
          </div>

          <div className="field" style={{ marginBottom: "20px" }}>
            <label style={{ display: "flex", alignItems: "center", gap: "8px", fontWeight: 400 }}>
              <input
                type="checkbox"
                name="cancellationEnabled"
                checked={form.cancellationEnabled}
                onChange={handleInputChange}
                disabled={isSaving}
              />
              Allow registrants to cancel / request a refund
            </label>
            <span className="field-note" style={{ marginTop: "6px" }}>
              Turns off the Cancel button on a registrant's own booking entirely. You can still change any
              booking's status by hand below.
            </span>
            {form.cancellationEnabled ? (
              <div className="field" style={{ marginTop: "14px" }}>
                <label htmlFor="dinnerRefundWindowEndsAt">Refund Window Ends</label>
                <input
                  id="dinnerRefundWindowEndsAt"
                  name="refundWindowEndsAt"
                  type="datetime-local"
                  value={form.refundWindowEndsAt}
                  onChange={handleInputChange}
                  disabled={isSaving}
                />
                <span className="field-note">
                  After this time, the Cancel button disappears even though cancellations are allowed above.
                  Leave blank to allow cancellation any time.
                </span>
              </div>
            ) : null}
          </div>

          <div className="field" style={{ marginBottom: "20px" }}>
            <label style={{ display: "flex", alignItems: "center", gap: "8px", fontWeight: 400 }}>
              <input
                type="checkbox"
                name="addOnEnabled"
                checked={form.addOnEnabled}
                onChange={handleInputChange}
                disabled={isSaving}
              />
              Available as a Zone Upgrade add-on
            </label>
            <span className="field-note" style={{ marginTop: "6px" }}>
              Lets registrants bundle this dinner into a Zone Upgrade request, instead of only signing up
              separately above. Priced using the same age-category prices set above — the add-on itself is
              never discounted.
            </span>
            {form.addOnEnabled ? (
              <div className="field" style={{ marginTop: "14px" }}>
                <label htmlFor="addOnDiscountPercent">Add-On Discount % (reserved, not yet applied)</label>
                <input
                  id="addOnDiscountPercent"
                  name="addOnDiscountPercent"
                  type="number"
                  min="0"
                  max="100"
                  value={form.addOnDiscountPercent}
                  onChange={handleInputChange}
                  disabled={isSaving}
                />
                <span className="field-note">
                  Stored for a future add-on-specific discount. Not currently applied to any price — the
                  add-on is charged at the same age-category prices as the standalone booking above.
                </span>
              </div>
            ) : null}
          </div>

          <div className="field" style={{ marginBottom: "20px" }}>
            <label htmlFor="dinnerConditions">Conditions</label>
            <textarea
              id="dinnerConditions"
              name="conditions"
              rows={3}
              value={form.conditions}
              onChange={handleInputChange}
              placeholder="e.g. Advance payment required. No refunds within 7 days of the event."
              disabled={isSaving}
            />
          </div>

          <div className="field">
            <label htmlFor="dinnerNotes">Admin Notes (internal)</label>
            <textarea
              id="dinnerNotes"
              name="notes"
              rows={2}
              value={form.notes}
              onChange={handleInputChange}
              disabled={isSaving}
            />
          </div>

          {error ? <div className="error-banner">{error}</div> : null}
          {success ? <div className="success-banner">{success}</div> : null}

          <div className="change-password-actions partner-actions-row">
            <button type="submit" className="button primary" disabled={isSaving || !eventType}>
              {isSaving ? "Saving..." : "Save Settings"}
            </button>
          </div>
        </form>

        <div className="partner-panel partner-list-panel">
          <div className="panel-heading">
            <div>
              <h3>Sign-ups</h3>
              <p>Registrants who requested Dinner with Celebrity guests for this event.</p>
            </div>
            <button
              type="button"
              className="button secondary"
              onClick={handleExportRegistrationsCSV}
              disabled={!registrations.length}
            >
              📥 Export as CSV
            </button>
          </div>

          {!eventType ? (
            <div className="empty-state">Select an event to view sign-ups.</div>
          ) : isLoading ? (
            <div className="loading">Loading sign-ups...</div>
          ) : registrations.length === 0 ? (
            <div className="empty-state">No Dinner with Celebrity sign-ups yet</div>
          ) : (
            <div className="entry-slot-list">
              {registrations.map((reg) => {
                // One line item per non-empty veg/non-veg cell, priced at
                // that category's snapshot rate — mirrors the registrant's
                // own receipt table (see CelebrityDinnerSignup.jsx). Kids
                // under 6 are never priced.
                const lineItems = [
                  { key: "adultsVeg", label: "Adults — Veg", qty: reg.numberOfAdultsVeg || 0, price: reg.pricePerAdult || 0 },
                  { key: "adultsNonVeg", label: "Adults — Non-Veg", qty: reg.numberOfAdultsNonVeg || 0, price: reg.pricePerAdult || 0 },
                  { key: "kids914Veg", label: "Kids (9-14) — Veg", qty: reg.numberOfKids914Veg || 0, price: reg.pricePerKids914 || 0 },
                  { key: "kids914NonVeg", label: "Kids (9-14) — Non-Veg", qty: reg.numberOfKids914NonVeg || 0, price: reg.pricePerKids914 || 0 },
                  { key: "kids68Veg", label: "Kids (6-8) — Veg", qty: reg.numberOfKids68Veg || 0, price: reg.pricePerKids68 || 0 },
                  { key: "kids68NonVeg", label: "Kids (6-8) — Non-Veg", qty: reg.numberOfKids68NonVeg || 0, price: reg.pricePerKids68 || 0 },
                  { key: "kidsUnder6Veg", label: "Kids (under 6) — Veg", qty: reg.numberOfKidsUnder6Veg || 0, price: 0 },
                  { key: "kidsUnder6NonVeg", label: "Kids (under 6) — Non-Veg", qty: reg.numberOfKidsUnder6NonVeg || 0, price: 0 },
                ].filter((item) => item.qty > 0);
                const totalGuests = lineItems.reduce((sum, item) => sum + item.qty, 0);
                return (
                <article className="entry-slot-card" key={reg.dinnerRegistrationId}>
                  <div className="partner-card-top">
                    <div>
                      <h4>
                        {reg.name || reg.email}{" "}
                        {reg.source === "zoneUpgrade" ? (
                          <span className="onam-chip" title={`Booked as part of a Zone Upgrade request${reg.tierName ? ` (${reg.tierName})` : ""}`}>
                            Via Zone Upgrade
                          </span>
                        ) : reg.registrationId ? null : (
                          <span className="onam-chip" title="No underlying event registration">Dinner Only</span>
                        )}
                      </h4>
                    </div>
                    <span className="partner-status active">{totalGuests} guest{totalGuests === 1 ? "" : "s"}</span>
                  </div>
                  <div className="onam-review-grid" style={{ marginTop: "8px" }}>
                    <div className="onam-review-row">
                      <span className="onam-review-label">Email</span>
                      <span className="onam-review-value">{reg.email || "—"}</span>
                    </div>
                    <div className="onam-review-row">
                      <span className="onam-review-label">Payment Method</span>
                      <span className="onam-review-value">{reg.paymentMethod || "—"}</span>
                    </div>
                    <div className="onam-review-row">
                      <span className="onam-review-label">Payment Status</span>
                      <span className="onam-review-value">
                        <span className={`payment-badge ${reg.status}`}>{REGISTRATION_STATUS_LABELS[reg.status] || reg.status}</span>
                      </span>
                    </div>
                    <div className="onam-review-row">
                      <span className="onam-review-label">Signed Up</span>
                      <span className="onam-review-value">{reg.submittedAt ? new Date(reg.submittedAt).toLocaleString() : "—"}</span>
                    </div>
                  </div>
                  {lineItems.length > 0 ? (
                    <div className="onam-price-table" style={{ marginTop: "10px" }}>
                      <div className="onam-price-head">
                        <div>Item</div>
                        <div>Qty</div>
                        <div>Price</div>
                        <div>Total</div>
                      </div>
                      {lineItems.map((item) => (
                        <div className="onam-price-row" key={item.key}>
                          <div>{item.label}</div>
                          <div>{item.qty}</div>
                          <div>{yen(item.price)}</div>
                          <div>{yen(item.price * item.qty)}</div>
                        </div>
                      ))}
                      <div className="onam-price-foot">
                        <div>Total</div>
                        <div />
                        <div />
                        <div>{yen(reg.totalAmount)}</div>
                      </div>
                    </div>
                  ) : null}
                  <div className="partner-meta" style={{ marginTop: "8px" }}>
                    <span>Txn: {reg.paymentReference || reg.providerPaymentId || "—"}</span>
                    {reg.status === "refund_requested" && reg.refundStatus ? (
                      <span className="onam-summary-sub">
                        {REFUND_STATUS_LABELS[reg.refundStatus] || reg.refundStatus}
                        {reg.refundReference ? ` (${reg.refundReference})` : ""}
                      </span>
                    ) : null}
                  </div>
                  {reg.source === "zoneUpgrade" ? (
                    <p className="field-note" style={{ marginTop: "10px" }}>
                      Manage this booking from the Zone Upgrades tab — it's part of that request, not a
                      standalone sign-up.
                    </p>
                  ) : (
                  <div className="btn-row" style={{ marginTop: "10px" }}>
                    {reg.status === "pending" ? (
                      <button
                        type="button"
                        className="button secondary"
                        disabled={busyRegistrationId === reg.dinnerRegistrationId}
                        onClick={() => handleUpdateRegistrationStatus(reg, "paid")}
                      >
                        Mark Paid
                      </button>
                    ) : null}
                    {reg.status !== "cancelled" ? (
                      <button
                        type="button"
                        className="button secondary"
                        disabled={busyRegistrationId === reg.dinnerRegistrationId}
                        onClick={() => handleUpdateRegistrationStatus(reg, "cancelled")}
                      >
                        {reg.status === "refund_requested" ? "Confirm Refund Done" : "Cancel"}
                      </button>
                    ) : null}
                  </div>
                  )}
                </article>
                );
              })}
            </div>
          )}
        </div>
      </div>
    </>
  );
}
