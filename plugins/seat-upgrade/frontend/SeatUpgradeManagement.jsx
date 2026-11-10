import { useEffect, useMemo, useState } from "react";
import {
  deleteSeatUpgradeTier,
  getSeatUpgradeEmailTemplates,
  getSeatUpgradeRequests,
  getSeatUpgradeTiers,
  previewSeatUpgradeEmailTemplate,
  resetSeatUpgradeEmailTemplate,
  saveSeatUpgradeEmailTemplate,
  saveSeatUpgradeTier,
  sendSeatUpgradeAnnouncement,
  updateSeatUpgradeStatus,
} from "./api";
import MapRegionEditor from "./MapRegionEditor";

const INITIAL_TIER_FORM = {
  name: "",
  description: "",
  upgradePrice: "0",
  capacity: "0",
  status: "ACTIVE",
  displayOrder: "0",
  mapRegion: null,
  seatMapCategory: "",
};

// Only meaningful to the host's own Celebrity Event Seat Selection feature
// (a separate plugin) once its admin has turned that feature's own pricing
// off — see that plugin's eventInfo.seatSelection.pricingEnabled. Linking a
// tier to one of these lets a paid upgrade into it unlock free picking from
// the matching seat-map zone there instead of just being a standalone paid
// add-on. "" means this tier doesn't unlock anything there.
const SEAT_MAP_CATEGORY_LABELS = { zone1: "Zone 1", zone2: "Zone 2", zone3: "Zone 3" };

// events still carries each event's full eventInfo (see AdminDashboard.jsx's
// seatUpgradeEvents — a filter, not a map), so the seat map image for the
// region editor is read straight out of it rather than needing a new prop.
function getSeatMapImageUrl(events, eventType) {
  const evt = events.find((e) => e.eventType === eventType);
  const eventInfo = evt && typeof evt.eventInfo === "object" ? evt.eventInfo : null;
  const raw = eventInfo?.seatUpgrade;
  return raw && typeof raw === "object" && typeof raw.seatMapImageUrl === "string" ? raw.seatMapImageUrl : "";
}

const yen = (value) => `¥${Number(value || 0).toLocaleString("en-US")}`;

function formatDateTime(value) {
  if (!value) return "";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString(undefined, {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

// Copied from the host app's AdminDashboard.jsx — same "copied component"
// convention this plugin already uses elsewhere (see this file's own
// STATUS_LABELS/PAYMENT_METHODS, or SeatUpgrade.jsx's EventFooter) —
// deliberately independent of the host's frontend/src/api.js internals.
function csvEscape(value) {
  const text = String(value ?? "");
  return /[",\n]/.test(text) ? `"${text.replace(/"/g, '""')}"` : text;
}

function downloadCSV(blob, filename) {
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  document.body.appendChild(link);
  link.click();
  document.body.removeChild(link);
  URL.revokeObjectURL(url);
}

const REQUEST_STATUS_LABELS = {
  pending: "Pending",
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

const EMAIL_TEMPLATE_TABS = [
  { key: "request", label: "Requested" },
  { key: "paid", label: "Confirmed" },
  { key: "refund_requested", label: "Refund Requested" },
  { key: "cancelled", label: "Cancelled" },
];

const EMAIL_TEMPLATE_TONES = [
  { key: "success", label: "Success (green)" },
  { key: "pending", label: "Pending (amber)" },
  { key: "danger", label: "Danger (red)" },
];

export default function SeatUpgradeManagement({ events = [], initialEventType = "" }) {
  const selectableEvents = useMemo(
    () =>
      events
        .filter((event) => event.eventType)
        .sort((a, b) => String(a.eventName || a.eventType).localeCompare(String(b.eventName || b.eventType))),
    [events]
  );
  const fallbackEventType = selectableEvents[0]?.eventType || "";
  const [eventType, setEventType] = useState(initialEventType || fallbackEventType);
  const [tab, setTab] = useState("tiers"); // "tiers" | "requests"

  const [tiers, setTiers] = useState([]);
  const [tierForm, setTierForm] = useState(INITIAL_TIER_FORM);
  const [editingTierId, setEditingTierId] = useState("");
  const [isLoadingTiers, setIsLoadingTiers] = useState(false);
  const [isSavingTier, setIsSavingTier] = useState(false);

  const [requests, setRequests] = useState([]);
  const [isLoadingRequests, setIsLoadingRequests] = useState(false);
  const [busyUpgradeId, setBusyUpgradeId] = useState("");
  const [requestSearch, setRequestSearch] = useState("");

  const [isSendingAnnouncement, setIsSendingAnnouncement] = useState(false);
  const [lastAnnouncement, setLastAnnouncement] = useState(null); // { sent, failed, total, eventType, at } | null

  const [error, setError] = useState("");
  const [success, setSuccess] = useState("");

  useEffect(() => {
    if (!eventType && fallbackEventType) setEventType(fallbackEventType);
  }, [eventType, fallbackEventType]);

  useEffect(() => {
    if (initialEventType && initialEventType !== eventType) setEventType(initialEventType);
  }, [initialEventType, eventType]);

  useEffect(() => {
    if (!eventType) {
      setTiers([]);
      setRequests([]);
      return;
    }
    loadTiers(eventType);
    loadRequests(eventType);
  }, [eventType]);

  const tierStats = useMemo(() => {
    const totalCapacity = tiers.reduce((sum, tier) => sum + (Number(tier.capacity) || 0), 0);
    const sold = tiers.reduce((sum, tier) => sum + (Number(tier.upgradedSeats) || 0), 0);
    return {
      tierCount: tiers.length,
      activeTiers: tiers.filter((tier) => tier.status === "ACTIVE").length,
      totalCapacity,
      sold,
      remaining: Math.max(totalCapacity - sold, 0),
    };
  }, [tiers]);

  const requestStats = useMemo(() => {
    const revenue = requests
      .filter((r) => r.status === "paid")
      .reduce((sum, r) => sum + (Number(r.totalAmount) || 0), 0);
    // Excludes cancelled requests, same as the tiers' own "Sold" count
    // (seat_upgrade_logic.ACTIVE_UPGRADE_STATUSES) — otherwise a cancelled
    // request's guests would stay counted here forever.
    const dinnerGuests = requests
      .filter((r) => r.status !== "cancelled")
      .reduce(
        (sum, r) =>
          sum +
          (Number(r.dinnerAdultVegGuests) || 0) +
          (Number(r.dinnerAdultNonVegGuests) || 0) +
          (Number(r.dinnerKids914VegGuests) || 0) +
          (Number(r.dinnerKids914NonVegGuests) || 0) +
          (Number(r.dinnerKids68VegGuests) || 0) +
          (Number(r.dinnerKids68NonVegGuests) || 0) +
          (Number(r.dinnerKidsUnder6VegGuests) || 0) +
          (Number(r.dinnerKidsUnder6NonVegGuests) || 0),
        0
      );
    const dinnerRevenue = requests
      .filter((r) => r.status === "paid")
      .reduce((sum, r) => sum + (Number(r.dinnerAmount) || 0), 0);
    return {
      total: requests.length,
      pending: requests.filter((r) => r.status === "pending").length,
      paid: requests.filter((r) => r.status === "paid").length,
      refundRequested: requests.filter((r) => r.status === "refund_requested").length,
      cancelled: requests.filter((r) => r.status === "cancelled").length,
      revenue,
      dinnerGuests,
      dinnerRevenue,
    };
  }, [requests]);

  const filteredRequests = useMemo(() => {
    const search = requestSearch.trim().toLowerCase();
    if (!search) return requests;
    return requests.filter(
      (r) =>
        (r.name?.toLowerCase() || "").includes(search) ||
        (r.email?.toLowerCase() || "").includes(search)
    );
  }, [requests, requestSearch]);

  // Exports whatever's currently visible (respects the name/email search),
  // same convention as AdminDashboard.jsx's Sadhya Slot Allocations export —
  // one row per zone upgrade request, guest counts broken out by category so
  // an admin can pivot/filter them in a spreadsheet.
  function handleExportRequestsCSV() {
    const header = [
      "Requested At", "Name", "Email", "Zone(s)", "Seats",
      "Dinner Adults Veg", "Dinner Adults Non-Veg",
      "Dinner Kids 9-14 Veg", "Dinner Kids 9-14 Non-Veg",
      "Dinner Kids 6-8 Veg", "Dinner Kids 6-8 Non-Veg",
      "Dinner Kids Under 6 Veg", "Dinner Kids Under 6 Non-Veg",
      "Amount", "Payment Method", "Transaction ID", "Status",
    ];
    const lines = [header, ...filteredRequests.map((r) => [
      formatDateTime(r.requestedAt),
      r.name || "",
      r.email || "",
      (r.zoneBreakdown || []).map((z) => `${z.tierName} x${z.count}`).join("; "),
      r.seatCount || 0,
      r.dinnerAdultVegGuests || 0,
      r.dinnerAdultNonVegGuests || 0,
      r.dinnerKids914VegGuests || 0,
      r.dinnerKids914NonVegGuests || 0,
      r.dinnerKids68VegGuests || 0,
      r.dinnerKids68NonVegGuests || 0,
      r.dinnerKidsUnder6VegGuests || 0,
      r.dinnerKidsUnder6NonVegGuests || 0,
      r.totalAmount || 0,
      r.paymentMethod || "",
      r.paymentReference || r.providerPaymentId || "",
      REQUEST_STATUS_LABELS[r.status] || r.status || "",
    ])].map((line) => line.map(csvEscape).join(","));
    const timestamp = new Date().toISOString().split("T")[0];
    downloadCSV(new Blob([lines.join("\n")], { type: "text/csv" }), `zone-upgrade-requests-${eventType}-${timestamp}.csv`);
  }

  async function loadTiers(nextEventType = eventType) {
    if (!nextEventType) return;
    setIsLoadingTiers(true);
    setError("");
    try {
      setTiers(await getSeatUpgradeTiers(nextEventType));
    } catch (err) {
      setError(err.message || "Failed to load zone upgrade tiers.");
      setTiers([]);
    } finally {
      setIsLoadingTiers(false);
    }
  }

  async function loadRequests(nextEventType = eventType) {
    if (!nextEventType) return;
    setIsLoadingRequests(true);
    try {
      setRequests(await getSeatUpgradeRequests(nextEventType));
    } catch (err) {
      setError(err.message || "Failed to load zone upgrade requests.");
      setRequests([]);
    } finally {
      setIsLoadingRequests(false);
    }
  }

  function resetTierForm() {
    setTierForm(INITIAL_TIER_FORM);
    setEditingTierId("");
    setError("");
  }

  function handleTierInputChange(event) {
    const { name, value } = event.target;
    setTierForm((prev) => ({ ...prev, [name]: value }));
    setError("");
    setSuccess("");
  }

  function editTier(tier) {
    setEditingTierId(tier.tierId);
    setTierForm({
      name: tier.name || "",
      description: tier.description || "",
      upgradePrice: String(tier.upgradePrice ?? 0),
      capacity: String(tier.capacity ?? 0),
      status: tier.status || "ACTIVE",
      displayOrder: String(tier.displayOrder ?? 0),
      mapRegion: tier.mapRegion || null,
      seatMapCategory: tier.seatMapCategory || "",
    });
    setError("");
    setSuccess("");
  }

  async function handleSaveTier(event) {
    event.preventDefault();
    setError("");
    setSuccess("");

    if (!eventType) {
      setError("Select an event before saving zone upgrade tiers.");
      return;
    }

    const payload = {
      name: tierForm.name.trim(),
      description: tierForm.description.trim(),
      upgradePrice: Number(tierForm.upgradePrice || 0),
      capacity: Number(tierForm.capacity || 0),
      status: tierForm.status,
      displayOrder: Number(tierForm.displayOrder || 0),
      mapRegion: tierForm.mapRegion || null,
      seatMapCategory: tierForm.seatMapCategory || "",
    };

    if (!payload.name) {
      setError("Tier name is required.");
      return;
    }
    if (payload.upgradePrice < 0 || payload.capacity < 0) {
      setError("Upgrade price and capacity must be 0 or greater.");
      return;
    }

    setIsSavingTier(true);
    try {
      const savedTier = await saveSeatUpgradeTier(eventType, payload, editingTierId);
      setTiers((prev) => {
        const next = prev.filter((tier) => tier.tierId !== savedTier.tierId);
        return [...next, savedTier].sort((a, b) => String(a.createdAt || "").localeCompare(String(b.createdAt || "")));
      });
      setSuccess(editingTierId ? "Zone upgrade tier updated." : "Zone upgrade tier created.");
      resetTierForm();
    } catch (err) {
      setError(err.message || "Failed to save zone upgrade tier.");
    } finally {
      setIsSavingTier(false);
    }
  }

  async function handleDeleteTier(tier) {
    if (!window.confirm(`Delete zone "${tier.name}"? Zones with existing purchases cannot be deleted.`)) {
      return;
    }
    setError("");
    setSuccess("");
    try {
      await deleteSeatUpgradeTier(eventType, tier.tierId);
      setTiers((prev) => prev.filter((item) => item.tierId !== tier.tierId));
      if (editingTierId === tier.tierId) resetTierForm();
      setSuccess("Zone upgrade tier deleted.");
    } catch (err) {
      setError(err.message || "Failed to delete zone upgrade tier.");
    }
  }

  async function handleUpdateRequestStatus(upgrade, status) {
    setBusyUpgradeId(upgrade.upgradeId);
    setError("");
    setSuccess("");
    try {
      const updated = await updateSeatUpgradeStatus(eventType, upgrade.upgradeId, status);
      setRequests((prev) => prev.map((item) => (item.upgradeId === updated.upgradeId ? { ...item, ...updated } : item)));
      // Capacity may have changed (e.g. a cancellation frees seats).
      loadTiers(eventType);
      setSuccess(`Marked as ${REQUEST_STATUS_LABELS[status] || status}.`);
    } catch (err) {
      setError(err.message || "Failed to update zone upgrade request.");
    } finally {
      setBusyUpgradeId("");
    }
  }

  async function handleSendAnnouncement() {
    const verb = lastAnnouncement?.eventType === eventType ? "Resend" : "Send";
    if (
      !window.confirm(
        `${verb} the "Premium Seating Now Available" email to every paid registrant on this event? ` +
          "This emails everyone eligible right now, including anyone already notified."
      )
    ) {
      return;
    }
    setIsSendingAnnouncement(true);
    setError("");
    setSuccess("");
    try {
      const result = await sendSeatUpgradeAnnouncement(eventType);
      setLastAnnouncement({ ...result, eventType, at: new Date().toISOString() });
      setSuccess(
        result.total === 0
          ? result.message || "No paid event/combo registrants to notify yet."
          : `Announcement sent to ${result.sent} of ${result.total} registrant(s)${
              result.failed ? ` (${result.failed} failed — check CloudWatch logs)` : ""
            }.`
      );
    } catch (err) {
      setError(err.message || "Failed to send the zone upgrade announcement.");
    } finally {
      setIsSendingAnnouncement(false);
    }
  }

  const announcementForThisEvent =
    lastAnnouncement && lastAnnouncement.eventType === eventType ? lastAnnouncement : null;

  return (
    <>
      <div className="admin-controls">
        <select
          value={eventType}
          onChange={(event) => {
            setEventType(event.target.value);
            resetTierForm();
          }}
          className="search-input"
          aria-label="Select event for zone upgrades"
        >
          <option value="">Select event...</option>
          {selectableEvents.map((event) => (
            <option key={event.eventType} value={event.eventType}>
              {event.eventName || event.eventType}
            </option>
          ))}
        </select>
        <button
          className="button secondary"
          type="button"
          onClick={() => {
            loadTiers();
            loadRequests();
          }}
          disabled={!eventType || isLoadingTiers || isLoadingRequests}
        >
          {isLoadingTiers || isLoadingRequests ? "Refreshing..." : "Refresh"}
        </button>
        <button
          className="button primary"
          type="button"
          onClick={handleSendAnnouncement}
          disabled={!eventType || isSendingAnnouncement}
          title="Emails every paid registrant on this event that premium seating is available."
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
          Last sent {formatDateTime(announcementForThisEvent.at)}: {announcementForThisEvent.sent} of{" "}
          {announcementForThisEvent.total} registrant(s) notified
          {announcementForThisEvent.failed ? `, ${announcementForThisEvent.failed} failed` : ""}.
        </p>
      ) : null}

      <div className="admin-tabs">
        <button
          type="button"
          className={`admin-tab ${tab === "tiers" ? "active" : ""}`}
          onClick={() => setTab("tiers")}
        >
          Seating Zones
        </button>
        <button
          type="button"
          className={`admin-tab ${tab === "requests" ? "active" : ""}`}
          onClick={() => setTab("requests")}
        >
          Purchase Requests {requestStats.pending ? `(${requestStats.pending} pending)` : ""}
        </button>
        <button
          type="button"
          className={`admin-tab ${tab === "emailTemplates" ? "active" : ""}`}
          onClick={() => setTab("emailTemplates")}
        >
          Email Templates
        </button>
      </div>

      {error ? <div className="error-banner">{error}</div> : null}
      {success ? <div className="success-banner">{success}</div> : null}

      {tab === "tiers" ? (
        <>
          <div className="admin-stats entry-slot-stats">
            <div className="stat-box">
              <div className="stat-value">{tierStats.tierCount}</div>
              <div className="stat-label">Zones</div>
            </div>
            <div className="stat-box">
              <div className="stat-value">{tierStats.activeTiers}</div>
              <div className="stat-label">Active</div>
            </div>
            <div className="stat-box">
              <div className="stat-value">{tierStats.sold}</div>
              <div className="stat-label">Sold</div>
            </div>
            <div className="stat-box">
              <div className="stat-value">{tierStats.remaining}</div>
              <div className="stat-label">Remaining</div>
            </div>
          </div>

          <div className="partner-management-grid entry-slot-grid">
            <form className="partner-panel partner-form-panel" onSubmit={handleSaveTier}>
              <div className="panel-heading">
                <div>
                  <h3>{editingTierId ? "Edit Seating Zone" : "Add Seating Zone"}</h3>
                  <p>
                    One price per person upgrading into this zone — the Family Upgrade Discount (buy 2, get 30%
                    off any more) is applied automatically at checkout.
                  </p>
                </div>
                <button type="button" className="button secondary" onClick={resetTierForm} disabled={isSavingTier}>
                  Clear
                </button>
              </div>

              <div className="field-row">
                <div className="field">
                  <label htmlFor="tierName">Zone Name</label>
                  <input
                    id="tierName"
                    name="name"
                    type="text"
                    value={tierForm.name}
                    onChange={handleTierInputChange}
                    placeholder="Zone 1"
                    disabled={isSavingTier}
                  />
                </div>
                <div className="field">
                  <label htmlFor="tierDisplayOrder">Display Order</label>
                  <input
                    id="tierDisplayOrder"
                    name="displayOrder"
                    type="number"
                    min="0"
                    value={tierForm.displayOrder}
                    onChange={handleTierInputChange}
                    disabled={isSavingTier}
                  />
                </div>
              </div>

              <div className="field">
                <label htmlFor="tierDescription">Description</label>
                <input
                  id="tierDescription"
                  name="description"
                  type="text"
                  value={tierForm.description}
                  onChange={handleTierInputChange}
                  placeholder="Front-row reserved seating"
                  disabled={isSavingTier}
                />
              </div>

              <div className="field-row">
                <div className="field">
                  <label htmlFor="tierUpgradePrice">Upgrade Price (¥ per person)</label>
                  <input
                    id="tierUpgradePrice"
                    name="upgradePrice"
                    type="number"
                    min="0"
                    value={tierForm.upgradePrice}
                    onChange={handleTierInputChange}
                    disabled={isSavingTier}
                  />
                </div>
                <div className="field">
                  <label htmlFor="tierCapacity">Capacity (seats)</label>
                  <input
                    id="tierCapacity"
                    name="capacity"
                    type="number"
                    min="0"
                    value={tierForm.capacity}
                    onChange={handleTierInputChange}
                    disabled={isSavingTier}
                  />
                </div>
              </div>

              <div className="field">
                <label htmlFor="tierStatus">Status</label>
                <select id="tierStatus" name="status" value={tierForm.status} onChange={handleTierInputChange} disabled={isSavingTier}>
                  <option value="ACTIVE">Active</option>
                  <option value="INACTIVE">Inactive</option>
                </select>
              </div>

              <div className="field">
                <label htmlFor="tierSeatMapCategory">Seat Map Zone (optional)</label>
                <select
                  id="tierSeatMapCategory"
                  name="seatMapCategory"
                  value={tierForm.seatMapCategory}
                  onChange={handleTierInputChange}
                  disabled={isSavingTier}
                >
                  <option value="">None — doesn't unlock seat picking</option>
                  {Object.entries(SEAT_MAP_CATEGORY_LABELS).map(([category, label]) => (
                    <option key={category} value={category}>{label}</option>
                  ))}
                </select>
                <span className="field-note">
                  Only used when Celebrity Event Seat Selection's own pricing is off (Events tab). Linking this
                  tier to a seat-map zone lets whoever pays for it pick their own specific seats from that zone
                  for free instead of just holding an abstract upgrade.
                </span>
              </div>

              <div className="field">
                <label>Map Region (optional)</label>
                <MapRegionEditor
                  imageUrl={getSeatMapImageUrl(events, eventType)}
                  region={tierForm.mapRegion}
                  onChange={(mapRegion) => setTierForm((prev) => ({ ...prev, mapRegion }))}
                  otherTiers={tiers.filter((t) => t.tierId !== editingTierId)}
                  activeLabel={tierForm.name || "this zone"}
                />
                <span className="field-note">
                  Draws a clickable hotspot for this zone on the registrant-facing seat map. Leave undrawn to
                  offer the zone through the card picker only.
                </span>
              </div>

              <div className="change-password-actions partner-actions-row">
                <button type="submit" className="button primary" disabled={isSavingTier || !eventType}>
                  {isSavingTier ? "Saving..." : editingTierId ? "Update Zone" : "Add Zone"}
                </button>
              </div>
            </form>

            <div className="partner-panel partner-list-panel">
              <div className="panel-heading">
                <div>
                  <h3>Seating Zones</h3>
                  <p>Seats sold count both pending and paid purchase requests.</p>
                </div>
              </div>

              {!eventType ? (
                <div className="empty-state">Select an event to manage upgrade zones.</div>
              ) : isLoadingTiers ? (
                <div className="loading">Loading zones...</div>
              ) : tiers.length === 0 ? (
                <div className="empty-state">No upgrade zones configured for this event</div>
              ) : (
                <div className="entry-slot-list">
                  {tiers.map((tier) => {
                    const capacity = Number(tier.capacity) || 0;
                    const sold = Number(tier.upgradedSeats) || 0;
                    const percent = capacity > 0 ? Math.min(100, Math.round((sold / capacity) * 100)) : 0;
                    return (
                      <article className={`entry-slot-card ${tier.status !== "ACTIVE" ? "entry-slot-card-inactive" : ""}`} key={tier.tierId}>
                        <div className="partner-card-top">
                          <div>
                            <h4>{tier.name}</h4>
                            <p>{tier.description || `${yen(tier.upgradePrice)}/person`}</p>
                          </div>
                          <div style={{ display: "flex", flexDirection: "column", alignItems: "flex-end", gap: "4px" }}>
                            <span className={`partner-status ${tier.status === "ACTIVE" ? "active" : "inactive"}`}>
                              {tier.status || "INACTIVE"}
                            </span>
                            {tier.mapRegion ? (
                              <span className="onam-summary-sub" title="Has a clickable region on the seat map">
                                📍 Mapped
                              </span>
                            ) : null}
                          </div>
                        </div>

                        <div className="entry-slot-capacity-row">
                          <div>
                            <strong>{yen(tier.upgradePrice)}</strong>
                            <span>per person</span>
                          </div>
                          <div>
                            <strong>{sold}</strong>
                            <span>sold</span>
                          </div>
                          <div>
                            <strong>{Math.max(capacity - sold, 0)}</strong>
                            <span>remaining</span>
                          </div>
                          <div>
                            <strong>{capacity}</strong>
                            <span>capacity</span>
                          </div>
                        </div>

                        <div className="entry-slot-meter" aria-label={`${percent}% sold`}>
                          <span style={{ width: `${percent}%` }} />
                        </div>

                        <div className="partner-card-actions">
                          <button className="button secondary" type="button" onClick={() => editTier(tier)}>
                            Edit
                          </button>
                          <button className="button secondary" type="button" onClick={() => handleDeleteTier(tier)}>
                            Delete
                          </button>
                        </div>
                      </article>
                    );
                  })}
                </div>
              )}
            </div>
          </div>
        </>
      ) : tab === "emailTemplates" ? (
        <EmailTemplatesPanel />
      ) : (
        <>
          <div className="admin-stats entry-slot-stats">
            <div className="stat-box">
              <div className="stat-value">{requestStats.total}</div>
              <div className="stat-label">Requests</div>
            </div>
            <div className="stat-box">
              <div className="stat-value">{requestStats.pending}</div>
              <div className="stat-label">Pending</div>
            </div>
            <div className="stat-box">
              <div className="stat-value">{requestStats.paid}</div>
              <div className="stat-label">Paid</div>
            </div>
            <div className="stat-box">
              <div className="stat-value">{requestStats.refundRequested}</div>
              <div className="stat-label">Refund Requested</div>
            </div>
            <div className="stat-box">
              <div className="stat-value">{requestStats.cancelled}</div>
              <div className="stat-label">Cancelled</div>
            </div>
            <div className="stat-box">
              <div className="stat-value">{yen(requestStats.revenue)}</div>
              <div className="stat-label">Revenue</div>
            </div>
            <div className="stat-box">
              <div className="stat-value">{requestStats.dinnerGuests}</div>
              <div className="stat-label">Dinner Guests</div>
            </div>
            <div className="stat-box">
              <div className="stat-value">{yen(requestStats.dinnerRevenue)}</div>
              <div className="stat-label">Dinner Revenue</div>
            </div>
          </div>

          {!eventType ? (
            <div className="empty-state">Select an event to review zone upgrade requests.</div>
          ) : isLoadingRequests ? (
            <div className="loading">Loading requests...</div>
          ) : requests.length === 0 ? (
            <div className="empty-state">No zone upgrade requests for this event yet</div>
          ) : (
            <>
              <div className="admin-controls">
                <input
                  type="text"
                  placeholder="Search by name or email..."
                  value={requestSearch}
                  onChange={(e) => setRequestSearch(e.target.value)}
                  className="search-input"
                  aria-label="Search purchase requests by name or email"
                />
                <button
                  type="button"
                  className="button secondary"
                  onClick={handleExportRequestsCSV}
                  disabled={!filteredRequests.length}
                >
                  📥 Export as CSV
                </button>
              </div>
              {filteredRequests.length === 0 ? (
                <div className="empty-state">No matches found</div>
              ) : (
                <div className="table-container">
                  <table className="registrations-table">
                    <thead>
                      <tr>
                        <th>Requested</th>
                        <th>Registrant</th>
                        <th>Zones</th>
                        <th>Seats</th>
                        <th>Dinner</th>
                        <th>Amount</th>
                        <th>Payment</th>
                        <th>Transaction ID</th>
                        <th>Status</th>
                        <th>Actions</th>
                      </tr>
                    </thead>
                    <tbody>
                      {filteredRequests.map((req) => (
                        <tr key={req.upgradeId}>
                          <td>{formatDateTime(req.requestedAt)}</td>
                          <td>
                            {req.name}
                            <br />
                            <span className="onam-summary-sub">{req.email}</span>
                          </td>
                          <td>
                            <div className="onam-price-table onam-price-table-compact">
                              {(req.zoneBreakdown || []).map((z) => (
                                <div className="onam-price-row" key={z.tierId}>
                                  <div>{z.tierName}</div>
                                  <div>{z.count}</div>
                                  <div>{yen(z.regularUnitPrice)}</div>
                                  <div>{yen(z.subtotal)}</div>
                                </div>
                              ))}
                            </div>
                          </td>
                          <td>{req.seatCount}</td>
                          <td>
                            {(() => {
                              const dinnerLineItems = [
                                { key: "adultVeg", label: "Adults — Veg", qty: req.dinnerAdultVegGuests || 0, price: req.dinnerPriceAdults || 0 },
                                { key: "adultNonVeg", label: "Adults — Non-Veg", qty: req.dinnerAdultNonVegGuests || 0, price: req.dinnerPriceAdults || 0 },
                                { key: "kids914Veg", label: "Kids 9-14 — Veg", qty: req.dinnerKids914VegGuests || 0, price: req.dinnerPriceKids914 || 0 },
                                { key: "kids914NonVeg", label: "Kids 9-14 — Non-Veg", qty: req.dinnerKids914NonVegGuests || 0, price: req.dinnerPriceKids914 || 0 },
                                { key: "kids68Veg", label: "Kids 6-8 — Veg", qty: req.dinnerKids68VegGuests || 0, price: req.dinnerPriceKids68 || 0 },
                                { key: "kids68NonVeg", label: "Kids 6-8 — Non-Veg", qty: req.dinnerKids68NonVegGuests || 0, price: req.dinnerPriceKids68 || 0 },
                                { key: "kidsUnder6Veg", label: "Kids under 6 — Veg", qty: req.dinnerKidsUnder6VegGuests || 0, price: 0 },
                                { key: "kidsUnder6NonVeg", label: "Kids under 6 — Non-Veg", qty: req.dinnerKidsUnder6NonVegGuests || 0, price: 0 },
                              ].filter((item) => item.qty > 0);
                              return dinnerLineItems.length > 0 ? (
                                <div className="onam-price-table onam-price-table-compact">
                                  {dinnerLineItems.map((item) => (
                                    <div className="onam-price-row" key={item.key}>
                                      <div>{item.label}</div>
                                      <div>{item.qty}</div>
                                      <div>{yen(item.price)}</div>
                                      <div>{yen(item.price * item.qty)}</div>
                                    </div>
                                  ))}
                                </div>
                              ) : (
                                "—"
                              );
                            })()}
                          </td>
                          <td>{yen(req.totalAmount)}</td>
                          <td>{req.paymentMethod || "—"}</td>
                          <td>{req.paymentReference || req.providerPaymentId || "—"}</td>
                          <td>
                            <span className={`payment-badge ${req.status}`}>
                              {REQUEST_STATUS_LABELS[req.status] || req.status}
                            </span>
                            {req.status === "refund_requested" && req.refundStatus ? (
                              <div className="onam-summary-sub" style={{ marginTop: "4px" }}>
                                {REFUND_STATUS_LABELS[req.refundStatus] || req.refundStatus}
                                {req.refundReference ? ` (${req.refundReference})` : ""}
                                {req.refundError ? (
                                  <div title={req.refundError} style={{ color: "var(--danger, #b3453f)" }}>
                                    {req.refundError.length > 80 ? `${req.refundError.slice(0, 80)}…` : req.refundError}
                                  </div>
                                ) : null}
                              </div>
                            ) : null}
                          </td>
                          <td>
                            <div className="btn-row">
                              {req.status === "pending" ? (
                                <button
                                  type="button"
                                  className="button secondary"
                                  disabled={busyUpgradeId === req.upgradeId}
                                  onClick={() => handleUpdateRequestStatus(req, "paid")}
                                >
                                  Mark Paid
                                </button>
                              ) : null}
                              {req.status !== "cancelled" ? (
                                <button
                                  type="button"
                                  className="button secondary"
                                  disabled={busyUpgradeId === req.upgradeId}
                                  onClick={() => handleUpdateRequestStatus(req, "cancelled")}
                                >
                                  {req.status === "refund_requested" ? "Confirm Refund Done" : "Cancel"}
                                </button>
                              ) : null}
                            </div>
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </>
          )}
        </>
      )}
    </>
  );
}


function EmailTemplatesPanel() {
  const [templates, setTemplates] = useState([]);
  const [selectedKey, setSelectedKey] = useState(EMAIL_TEMPLATE_TABS[0].key);
  const [form, setForm] = useState(null);
  const [isLoading, setIsLoading] = useState(false);
  const [isSaving, setIsSaving] = useState(false);
  const [isResetting, setIsResetting] = useState(false);
  const [isPreviewing, setIsPreviewing] = useState(false);
  const [error, setError] = useState("");
  const [success, setSuccess] = useState("");
  const [preview, setPreview] = useState(null); // { subject, html } | null

  async function loadTemplates() {
    setIsLoading(true);
    setError("");
    try {
      setTemplates(await getSeatUpgradeEmailTemplates());
    } catch (err) {
      setError(err.message || "Failed to load email templates.");
    } finally {
      setIsLoading(false);
    }
  }

  useEffect(() => {
    loadTemplates();
  }, []);

  useEffect(() => {
    const current = templates.find((t) => t.templateKey === selectedKey);
    if (current) {
      setForm({
        subject: current.subject || "",
        title: current.title || "",
        tone: current.tone || "success",
        bannerText: current.bannerText || "",
        introText: current.introText || "",
        ctaLabel: current.ctaLabel || "",
      });
      setPreview(null);
      setSuccess("");
    }
  }, [selectedKey, templates]);

  const current = templates.find((t) => t.templateKey === selectedKey);

  function handleFieldChange(field, value) {
    setForm((prev) => ({ ...prev, [field]: value }));
    setSuccess("");
  }

  async function handleSave() {
    if (!form) return;
    setIsSaving(true);
    setError("");
    setSuccess("");
    try {
      const saved = await saveSeatUpgradeEmailTemplate(selectedKey, form);
      setTemplates((prev) => prev.map((t) => (t.templateKey === selectedKey ? saved : t)));
      setSuccess("Template saved.");
    } catch (err) {
      setError(err.message || "Failed to save email template.");
    } finally {
      setIsSaving(false);
    }
  }

  async function handleReset() {
    if (!window.confirm("Reset this email to its built-in default? Your customizations will be lost.")) {
      return;
    }
    setIsResetting(true);
    setError("");
    setSuccess("");
    try {
      const reset = await resetSeatUpgradeEmailTemplate(selectedKey);
      setTemplates((prev) => prev.map((t) => (t.templateKey === selectedKey ? reset : t)));
      setSuccess("Reset to default.");
    } catch (err) {
      setError(err.message || "Failed to reset email template.");
    } finally {
      setIsResetting(false);
    }
  }

  async function handlePreview() {
    if (!form) return;
    setIsPreviewing(true);
    setError("");
    try {
      setPreview(await previewSeatUpgradeEmailTemplate(selectedKey, form));
    } catch (err) {
      setError(err.message || "Failed to render preview.");
    } finally {
      setIsPreviewing(false);
    }
  }

  return (
    <>
      <p className="section-desc">
        Customize the copy for each registrant-facing seat-upgrade email. Layout, the WMF Japan logo, and
        colors stay consistent — these fields control the words and accent color only. Leave a field blank to
        use its default. Available placeholders in every field: <code>{"{eventName}"}</code>,{" "}
        <code>{"{tierName}"}</code>, <code>{"{seats}"}</code>, <code>{"{amount}"}</code>.
      </p>

      {error ? <div className="error-banner">{error}</div> : null}
      {success ? <div className="success-banner">{success}</div> : null}

      <div className="admin-tabs" style={{ marginBottom: "16px" }}>
        {EMAIL_TEMPLATE_TABS.map((t) => {
          const tpl = templates.find((x) => x.templateKey === t.key);
          return (
            <button
              key={t.key}
              type="button"
              className={`admin-tab ${selectedKey === t.key ? "active" : ""}`}
              onClick={() => setSelectedKey(t.key)}
            >
              {t.label}
              {tpl?.isCustomized ? " •" : ""}
            </button>
          );
        })}
      </div>

      {isLoading || !form ? (
        <div className="loading">Loading email templates...</div>
      ) : (
        <div className="partner-management-grid entry-slot-grid">
          <div className="partner-panel partner-form-panel">
            <div className="panel-heading">
              <div>
                <h3>{EMAIL_TEMPLATE_TABS.find((t) => t.key === selectedKey)?.label} Email</h3>
                <p>{current?.isCustomized ? "Customized" : "Using the built-in default"}</p>
              </div>
              {current?.isCustomized ? (
                <button type="button" className="button secondary" onClick={handleReset} disabled={isResetting}>
                  {isResetting ? "Resetting..." : "Reset to Default"}
                </button>
              ) : null}
            </div>

            <div className="field">
              <label htmlFor="templateSubject">Subject Line</label>
              <input
                id="templateSubject"
                type="text"
                value={form.subject}
                onChange={(e) => handleFieldChange("subject", e.target.value)}
                disabled={isSaving}
              />
            </div>

            <div className="field">
              <label htmlFor="templateTitle">Email Title</label>
              <input
                id="templateTitle"
                type="text"
                value={form.title}
                onChange={(e) => handleFieldChange("title", e.target.value)}
                disabled={isSaving}
              />
              <span className="field-note">The large heading inside the email, below the logo.</span>
            </div>

            <div className="field-row">
              <div className="field">
                <label htmlFor="templateTone">Accent Color</label>
                <select
                  id="templateTone"
                  value={form.tone}
                  onChange={(e) => handleFieldChange("tone", e.target.value)}
                  disabled={isSaving}
                >
                  {EMAIL_TEMPLATE_TONES.map((t) => (
                    <option key={t.key} value={t.key}>{t.label}</option>
                  ))}
                </select>
              </div>
              <div className="field">
                <label htmlFor="templateBanner">Status Banner Text</label>
                <input
                  id="templateBanner"
                  type="text"
                  value={form.bannerText}
                  onChange={(e) => handleFieldChange("bannerText", e.target.value)}
                  disabled={isSaving}
                />
              </div>
            </div>

            <div className="field">
              <label htmlFor="templateIntro">Message</label>
              <textarea
                id="templateIntro"
                rows={4}
                value={form.introText}
                onChange={(e) => handleFieldChange("introText", e.target.value)}
                disabled={isSaving}
              />
            </div>

            <div className="field">
              <label htmlFor="templateCta">Button Label (blank = no button)</label>
              <input
                id="templateCta"
                type="text"
                value={form.ctaLabel}
                onChange={(e) => handleFieldChange("ctaLabel", e.target.value)}
                disabled={isSaving}
                placeholder="e.g. View My Zone Upgrade"
              />
            </div>

            <div className="change-password-actions partner-actions-row">
              <button type="button" className="button primary" onClick={handleSave} disabled={isSaving}>
                {isSaving ? "Saving..." : "Save"}
              </button>
              <button type="button" className="button secondary" onClick={handlePreview} disabled={isPreviewing}>
                {isPreviewing ? "Rendering..." : "Preview"}
              </button>
            </div>
          </div>

          <div className="partner-panel partner-list-panel">
            <div className="panel-heading">
              <div>
                <h3>Preview</h3>
                <p>{preview ? `Subject: ${preview.subject}` : "Click Preview to render this email with sample data."}</p>
              </div>
            </div>
            {preview ? (
              <iframe
                title="Email preview"
                srcDoc={preview.html}
                style={{ width: "100%", height: "700px", border: "1px solid var(--border)", borderRadius: "6px" }}
              />
            ) : (
              <div className="empty-state">No preview yet</div>
            )}
          </div>
        </div>
      )}
    </>
  );
}
