import { useEffect, useMemo, useState } from "react";
import { rowLabelFor, seatDisplayLabel } from "./rowLabels";

// Zone 1/2/3 are the hall's raked "preferred" zones (crown badge), each an
// admin-carved cosmetic category — a per-seat override of the row-based
// default that stays fully bookable. Camera/projector are NOT bookable —
// same as any other admin-blocked seat, just tagged with their own icon
// (seat_selection_logic.NON_BOOKABLE_CATEGORIES). A seat that needs to be
// held back for another reason (VIP, reserved, ...) uses the plain generic
// "Block (other reason)" option instead of a dedicated category.
const ZONE_LABELS = { zone1: "Zone 1", zone2: "Zone 2", zone3: "Zone 3", regular: "Regular" };
const ZONE_ICONS = { zone1: "👑", zone2: "👑", zone3: "👑" };
const ZONE_OVERRIDE_CATEGORIES = ["zone1", "zone2", "zone3", "regular"];
// Regular is the hall's baseline seating — always free, never an
// admin-priced upsell (see seat_selection_logic.PRICEABLE_CATEGORIES) — so
// it's excluded from the pricing table below, though it stays a valid
// "bookable zone" choice when recategorizing a seat.
const PRICEABLE_CATEGORIES = ["zone1", "zone2", "zone3"];
const NON_BOOKABLE_CATEGORIES = ["camera", "projector"];
const SPECIAL_ICONS = { camera: "📷", projector: "🎥" };
const SPECIAL_LABELS = { camera: "Camera", projector: "Projector" };

// Admin view of the seat map. There's no capacity form here: the hall
// layout is fixed (see seat_selection_logic.py). This screen only shows
// occupancy and lets an admin block/unblock individual seats (a broken
// chair, an AV blind spot, ...). A seat actually booked by a registration
// is released through the host app's own delete/reassign flow, not from
// here.
//
// This component is host-agnostic: it never calls a network API directly.
// getEventSeats/blockEventSeat/unblockEventSeat are injected as props so
// any host app can supply its own fetch/auth plumbing (see this repo's
// frontend/src/api.js for an example, wired up in AdminDashboard.jsx).

// Vertical walkways split every row into Left / Center / Right blocks —
// matches the reference hall's aisle layout. Block sizes differ per row
// (e.g. row 1's Left is 6 seats, row 3's is 8), so which block a seat is
// in comes straight from the backend (seat.block) rather than a fixed
// column-number threshold, which would misplace seats on the narrower rows.
function splitRowByAisles(seats) {
  const left = seats.filter((seat) => seat.block === "left");
  const center = seats.filter((seat) => seat.block === "center");
  const right = seats.filter((seat) => seat.block === "right");
  return [left, center, right];
}

// Some front rows are physically narrower than the hall's full width — there
// is no seat at all past each end, not just an unavailable one. Render that
// as blank space the same size as a seat, so every row's columns still line
// up visually.
function blankCells(count, keyPrefix) {
  return Array.from({ length: count || 0 }, (_, i) => (
    <span key={`${keyPrefix}-${i}`} className="seat-map-seat-blank" aria-hidden="true" />
  ));
}
const EMPTY_PRICE_FORM = { displayName: "", pricePerFamily: "", pricePerPerson: "", maxFamilySize: "", status: "ACTIVE" };

function priceFormFromZone(category, zonePricing) {
  const price = zonePricing?.[category];
  if (!price) return { ...EMPTY_PRICE_FORM, displayName: ZONE_LABELS[category] };
  return {
    displayName: price.displayName || ZONE_LABELS[category],
    pricePerFamily: price.pricePerFamily ? String(price.pricePerFamily) : "",
    pricePerPerson: price.pricePerPerson ? String(price.pricePerPerson) : "",
    maxFamilySize: price.maxFamilySize ? String(price.maxFamilySize) : "",
    status: price.status || "ACTIVE",
  };
}

export default function SeatMapManagement({
  events = [],
  initialEventType = "",
  getEventSeats,
  blockEventSeat,
  unblockEventSeat,
  saveZonePrice,
  deleteZonePrice,
  sendSeatSelectionAnnouncement,
}) {
  const selectableEvents = useMemo(
    () =>
      events
        .filter((event) => event.eventType)
        .sort((a, b) => String(a.eventName || a.eventType).localeCompare(String(b.eventName || b.eventType))),
    [events]
  );
  const fallbackEventType = selectableEvents[0]?.eventType || "";
  const [eventType, setEventType] = useState(initialEventType || fallbackEventType);
  const [seatData, setSeatData] = useState(null);
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState("");
  const [success, setSuccess] = useState("");
  const [busySeatId, setBusySeatId] = useState("");
  const [menuSeat, setMenuSeat] = useState(null);
  const [priceForms, setPriceForms] = useState({});
  const [busyPriceCategory, setBusyPriceCategory] = useState("");
  const [announcing, setAnnouncing] = useState(false);

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
      loadSeats(eventType);
    } else {
      setSeatData(null);
    }
  }, [eventType]);

  async function loadSeats(nextEventType = eventType) {
    if (!nextEventType) return;
    setIsLoading(true);
    setError("");
    try {
      const data = await getEventSeats(nextEventType);
      setSeatData(data);
      setPriceForms(
        Object.fromEntries(PRICEABLE_CATEGORIES.map((cat) => [cat, priceFormFromZone(cat, data?.zonePricing)]))
      );
    } catch (err) {
      setError(err.message || "Failed to load the seat map.");
      setSeatData(null);
    } finally {
      setIsLoading(false);
    }
  }

  function updatePriceForm(category, field, value) {
    setPriceForms((prev) => ({ ...prev, [category]: { ...prev[category], [field]: value } }));
  }

  async function savePrice(category) {
    const form = priceForms[category] || EMPTY_PRICE_FORM;
    setError("");
    setSuccess("");
    setBusyPriceCategory(category);
    try {
      await saveZonePrice(eventType, category, {
        displayName: form.displayName.trim() || ZONE_LABELS[category],
        pricePerFamily: Number(form.pricePerFamily || 0),
        pricePerPerson: Number(form.pricePerPerson || 0),
        maxFamilySize: Number(form.maxFamilySize || 0),
        status: form.status,
      });
      setSuccess(`${ZONE_LABELS[category]} pricing saved.`);
      await loadSeats(eventType);
    } catch (err) {
      setError(err.message || "Failed to save zone pricing.");
    } finally {
      setBusyPriceCategory("");
    }
  }

  async function unpriceZone(category) {
    if (!window.confirm(`Take ${ZONE_LABELS[category]} off sale? Registrants will no longer be able to buy these seats.`)) {
      return;
    }
    setError("");
    setSuccess("");
    setBusyPriceCategory(category);
    try {
      await deleteZonePrice(eventType, category);
      setSuccess(`${ZONE_LABELS[category]} is no longer for sale.`);
      await loadSeats(eventType);
    } catch (err) {
      setError(err.message || "Failed to unprice the zone.");
    } finally {
      setBusyPriceCategory("");
    }
  }

  async function announce(reminder = false) {
    if (
      !window.confirm(
        reminder
          ? "Email a reminder to every paid registrant of this event who hasn't selected any seats yet? This can be sent again later if needed."
          : "Email every paid registrant of this event that seat selection is open? This can be sent again later if needed."
      )
    ) {
      return;
    }
    setError("");
    setSuccess("");
    setAnnouncing(true);
    try {
      const result = await sendSeatSelectionAnnouncement(eventType, { reminder });
      setSuccess(result?.message || "Announcement sent.");
    } catch (err) {
      setError(err.message || "Failed to send the announcement.");
    } finally {
      setAnnouncing(false);
    }
  }

  function handleSeatClick(seat) {
    if (seat.status === "BOOKED") return;
    setError("");
    setSuccess("");
    setMenuSeat(seat);
  }

  async function applyCategory(seat, category) {
    // Zone1/2/3/Regular are silent (no reason needed — it's a cosmetic
    // reassignment, not a block). Camera/Projector and a plain generic
    // block still ask for an optional reason, same as before.
    let reason = "";
    if (!category || NON_BOOKABLE_CATEGORIES.includes(category)) {
      const label = category ? SPECIAL_LABELS[category] : "Block";
      reason = window.prompt(`${label} seat ${seatDisplayLabel(seat.seatId)} — reason (optional):`, seat.reason || "");
      if (reason == null) return;
    }
    setError("");
    setSuccess("");
    setBusySeatId(seat.seatId);
    try {
      await blockEventSeat(eventType, seat.seatId, reason, category);
      setSuccess(`Seat ${seatDisplayLabel(seat.seatId)} updated.`);
      setMenuSeat(null);
      await loadSeats(eventType);
    } catch (err) {
      setError(err.message || "Failed to update the seat.");
    } finally {
      setBusySeatId("");
    }
  }

  async function resetSeat(seat) {
    setError("");
    setSuccess("");
    setBusySeatId(seat.seatId);
    try {
      await unblockEventSeat(eventType, seat.seatId);
      setSuccess(`Seat ${seatDisplayLabel(seat.seatId)} reset to its hall default.`);
      setMenuSeat(null);
      await loadSeats(eventType);
    } catch (err) {
      setError(err.message || "Failed to reset the seat.");
    } finally {
      setBusySeatId("");
    }
  }

  const rows = seatData?.rows || [];

  return (
    <>
      <div className="admin-stats entry-slot-stats">
        <div className="stat-box">
          <div className="stat-value">{seatData?.totalSeats ?? 0}</div>
          <div className="stat-label">Total Seats</div>
        </div>
        <div className="stat-box">
          <div className="stat-value">{seatData?.bookedSeats ?? 0}</div>
          <div className="stat-label">Booked</div>
        </div>
        <div className="stat-box">
          <div className="stat-value">{seatData?.blockedSeats ?? 0}</div>
          <div className="stat-label">Blocked</div>
        </div>
        <div className="stat-box">
          <div className="stat-value">{seatData?.availableSeats ?? 0}</div>
          <div className="stat-label">Available</div>
        </div>
      </div>

      <div className="admin-controls">
        <select
          value={eventType}
          onChange={(event) => setEventType(event.target.value)}
          className="search-input"
          aria-label="Select event for seat map"
        >
          <option value="">Select event...</option>
          {selectableEvents.map((event) => (
            <option key={event.eventType} value={event.eventType}>
              {event.eventName || event.eventType}
            </option>
          ))}
        </select>
        <button className="button secondary" type="button" onClick={() => loadSeats()} disabled={!eventType || isLoading}>
          {isLoading ? "Refreshing..." : "Refresh Seat Map"}
        </button>
        {sendSeatSelectionAnnouncement ? (
          <>
            <button className="button secondary" type="button" onClick={() => announce(false)} disabled={!eventType || announcing}>
              {announcing ? "Sending..." : "📣 Announce Seat Selection"}
            </button>
            <button className="button secondary" type="button" onClick={() => announce(true)} disabled={!eventType || announcing}>
              {announcing ? "Sending..." : "⏰ Remind Those Without Seats"}
            </button>
          </>
        ) : null}
      </div>

      {error ? <div className="error-banner">{error}</div> : null}
      {success ? <div className="success-banner">{success}</div> : null}

      {eventType && saveZonePrice ? (
        <div className="admin-modal" style={{ margin: "16px 0", maxWidth: "none" }}>
          <div className="panel-heading">
            <div>
              <h3>Zone Pricing</h3>
              <p className="field-note">
                Set a price for each zone to put it on sale. An unpriced zone shows as "not for sale" to registrants.
              </p>
            </div>
          </div>
          <div style={{ overflowX: "auto" }}>
            <table className="registrations-table">
              <thead>
                <tr>
                  <th>Zone</th>
                  <th>Display Name</th>
                  <th>Family Price (¥)</th>
                  <th>Up To</th>
                  <th>Per-Person Price (¥)</th>
                  <th>Status</th>
                  <th>Actions</th>
                </tr>
              </thead>
              <tbody>
                {PRICEABLE_CATEGORIES.map((category) => {
                  const form = priceForms[category] || EMPTY_PRICE_FORM;
                  const busy = busyPriceCategory === category;
                  return (
                    <tr key={category}>
                      <td>{ZONE_ICONS[category] ? `${ZONE_ICONS[category]} ` : ""}{ZONE_LABELS[category]}</td>
                      <td>
                        <input
                          type="text"
                          className="search-input"
                          value={form.displayName}
                          onChange={(e) => updatePriceForm(category, "displayName", e.target.value)}
                          disabled={busy}
                        />
                      </td>
                      <td>
                        <input
                          type="number"
                          min="0"
                          className="search-input"
                          value={form.pricePerFamily}
                          onChange={(e) => updatePriceForm(category, "pricePerFamily", e.target.value)}
                          disabled={busy}
                          style={{ width: 110 }}
                        />
                      </td>
                      <td>
                        <input
                          type="number"
                          min="0"
                          className="search-input"
                          value={form.maxFamilySize}
                          onChange={(e) => updatePriceForm(category, "maxFamilySize", e.target.value)}
                          disabled={busy}
                          style={{ width: 80 }}
                          title="How many seats the family price covers (0 = unlimited)"
                        />
                      </td>
                      <td>
                        <input
                          type="number"
                          min="0"
                          className="search-input"
                          value={form.pricePerPerson}
                          onChange={(e) => updatePriceForm(category, "pricePerPerson", e.target.value)}
                          disabled={busy}
                          style={{ width: 110 }}
                        />
                      </td>
                      <td>
                        <select
                          className="search-input"
                          value={form.status}
                          onChange={(e) => updatePriceForm(category, "status", e.target.value)}
                          disabled={busy}
                        >
                          <option value="ACTIVE">Active</option>
                          <option value="INACTIVE">Inactive</option>
                        </select>
                      </td>
                      <td>
                        <div className="btn-row">
                          <button className="button secondary" type="button" onClick={() => savePrice(category)} disabled={busy}>
                            {busy ? "Saving..." : "Save"}
                          </button>
                          <button className="button danger" type="button" onClick={() => unpriceZone(category)} disabled={busy}>
                            Unprice
                          </button>
                        </div>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </div>
      ) : null}

      {!eventType ? (
        <div className="empty-state">Select an event to view its seat map.</div>
      ) : isLoading ? (
        <div className="loading">Loading seat map...</div>
      ) : (
        <>
          <div className="seat-map-legend">
            <span className="seat-map-legend-item"><i className="seat-swatch zone1">👑</i> Zone 1</span>
            <span className="seat-map-legend-item"><i className="seat-swatch zone2">👑</i> Zone 2</span>
            <span className="seat-map-legend-item"><i className="seat-swatch zone3">👑</i> Zone 3</span>
            <span className="seat-map-legend-item"><i className="seat-swatch regular" /> Regular</span>
            <span className="seat-map-legend-item"><i className="seat-swatch selected" /> Booked</span>
            <span className="seat-map-legend-item"><i className="seat-swatch booked" /> Blocked</span>
            <span className="seat-map-legend-item"><i className="seat-swatch special">📷</i> Camera</span>
            <span className="seat-map-legend-item"><i className="seat-swatch special">🎥</i> Projector</span>
          </div>
          <p className="field-note">Click any non-booked seat to set its zone or mark it Camera/Projector/Blocked.</p>
          <div className="seat-map-stage">STAGE</div>
          <div className="seat-map-grid">
            {rows.map((row, index) => (
              <div key={row.row}>
                {index === (seatData?.walkwayAfterRow ?? 10) ? (
                  <div className="seat-map-walkway">WALKWAY</div>
                ) : null}
                <div className="seat-map-row">
                  <span className="seat-map-row-label">{rowLabelFor(row.row)}</span>
                  {splitRowByAisles(row.seats).map((block, blockIndex, blocks) => (
                    <div className="seat-map-row-block-wrap" key={blockIndex}>
                      {blockIndex > 0 ? <div className="seat-map-aisle" /> : null}
                      <div className="seat-map-row-seats">
                        {blockIndex === 0 ? blankCells(row.blankEdges, "lead") : null}
                        {block.map((seat) => {
                          const special = seat.status === "BLOCKED" ? seat.blockCategory : "";
                          const categoryClass =
                            seat.status === "BOOKED"
                              ? "selected"
                              : seat.status === "BLOCKED"
                                ? special
                                  ? `booked special-${special}`
                                  : "booked"
                                : seat.category;
                          const icon = special ? SPECIAL_ICONS[special] : seat.status !== "BOOKED" ? ZONE_ICONS[seat.category] : "";
                          return (
                            <button
                              key={seat.seatId}
                              type="button"
                              className={`seat-map-seat ${categoryClass}`}
                              disabled={seat.status === "BOOKED" || busySeatId === seat.seatId}
                              onClick={() => handleSeatClick(seat)}
                              aria-label={`Seat ${seatDisplayLabel(seat.seatId)}`}
                              title={
                                seat.status === "BOOKED"
                                  ? `${seatDisplayLabel(seat.seatId)}: booked${seat.holderName ? ` by ${seat.holderName}` : ""}`
                                  : seat.status === "BLOCKED"
                                    ? `${seatDisplayLabel(seat.seatId)}: ${special ? SPECIAL_LABELS[special] : "blocked"}${seat.reason ? ` (${seat.reason})` : ""}`
                                    : `${seatDisplayLabel(seat.seatId)}: available (${ZONE_LABELS[seat.category] || seat.category})`
                              }
                            >
                              {icon}
                            </button>
                          );
                        })}
                        {blockIndex === blocks.length - 1 ? blankCells(row.blankEdges, "trail") : null}
                      </div>
                    </div>
                  ))}
                </div>
              </div>
            ))}
          </div>
        </>
      )}
      {renderSeatMenu()}
    </>
  );

  function renderSeatMenu() {
    const seat = menuSeat;
    if (!seat) return null;
    const busy = busySeatId === seat.seatId;
    const currentLabel =
      seat.status === "BLOCKED"
        ? seat.blockCategory
          ? SPECIAL_LABELS[seat.blockCategory]
          : "Blocked"
        : ZONE_LABELS[seat.category] || "Regular";
    return (
      <div className="admin-modal-overlay" role="dialog" aria-modal="true" onClick={() => setMenuSeat(null)}>
        <div className="admin-modal" style={{ maxWidth: 420 }} onClick={(event) => event.stopPropagation()}>
          <div className="panel-heading">
            <div>
              <h3>Seat {seatDisplayLabel(seat.seatId)}</h3>
              <p className="field-note">Currently: {currentLabel}</p>
            </div>
            <button type="button" className="button secondary" onClick={() => setMenuSeat(null)}>
              Close
            </button>
          </div>

          <div className="field-note" style={{ marginTop: 10, marginBottom: 6 }}>Bookable zone</div>
          <div className="seat-menu-grid">
            {ZONE_OVERRIDE_CATEGORIES.map((cat) => (
              <button
                key={cat}
                type="button"
                className="button secondary"
                disabled={busy}
                onClick={() => applyCategory(seat, cat)}
              >
                {ZONE_ICONS[cat] ? `${ZONE_ICONS[cat]} ` : ""}{ZONE_LABELS[cat]}
              </button>
            ))}
          </div>

          <div className="field-note" style={{ marginTop: 14, marginBottom: 6 }}>Not bookable</div>
          <div className="seat-menu-grid">
            {NON_BOOKABLE_CATEGORIES.map((cat) => (
              <button
                key={cat}
                type="button"
                className="button secondary"
                disabled={busy}
                onClick={() => applyCategory(seat, cat)}
              >
                {SPECIAL_ICONS[cat]} {SPECIAL_LABELS[cat]}
              </button>
            ))}
            <button type="button" className="button secondary" disabled={busy} onClick={() => applyCategory(seat, "")}>
              Block (other reason)
            </button>
          </div>

          <div className="btn-row" style={{ marginTop: 16 }}>
            <button type="button" className="button danger" disabled={busy} onClick={() => resetSeat(seat)}>
              {busy ? "Working…" : "Reset to Hall Default"}
            </button>
          </div>
        </div>
      </div>
    );
  }
}
