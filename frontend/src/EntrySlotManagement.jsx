import { useEffect, useMemo, useState } from "react";
import {
  deleteEntrySlot,
  getEntrySlots,
  releaseEntrySlotBuffer,
  saveEntrySlot,
} from "./api";

const INITIAL_SLOT_FORM = {
  name: "",
  nickname: "",
  bookableCapacity: "0",
  bufferCapacity: "0",
  status: "ACTIVE",
  bookingStartsAt: "",
  bookingEndsAt: "",
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

function formatDateTime(value) {
  if (!value) return "Always open";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString(undefined, {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

function getCapacityPercent(slot) {
  const bookable = Number(slot.bookableCapacity) || 0;
  if (bookable <= 0) return 0;
  const occupied = Number(slot.occupiedSeats) || 0;
  return Math.min(100, Math.round((occupied / bookable) * 100));
}

export default function EntrySlotManagement({ events = [], initialEventType = "" }) {
  const selectableEvents = useMemo(
    () => events.filter((event) => event.eventType).sort((a, b) => String(a.eventName || a.eventType).localeCompare(String(b.eventName || b.eventType))),
    [events]
  );
  const fallbackEventType = selectableEvents[0]?.eventType || "";
  const [eventType, setEventType] = useState(initialEventType || fallbackEventType);
  const [slots, setSlots] = useState([]);
  const [slotForm, setSlotForm] = useState(INITIAL_SLOT_FORM);
  const [editingSlotId, setEditingSlotId] = useState("");
  const [isLoading, setIsLoading] = useState(false);
  const [isSaving, setIsSaving] = useState(false);
  const [error, setError] = useState("");
  const [success, setSuccess] = useState("");

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
      loadSlots(eventType);
    } else {
      setSlots([]);
    }
  }, [eventType]);

  const slotStats = useMemo(() => {
    const totalBookable = slots.reduce((sum, slot) => sum + (Number(slot.bookableCapacity) || 0), 0);
    const totalBuffer = slots.reduce((sum, slot) => sum + (Number(slot.bufferCapacity) || 0), 0);
    const occupied = slots.reduce((sum, slot) => sum + (Number(slot.occupiedSeats) || 0), 0);
    const remaining = slots.reduce((sum, slot) => sum + (Number(slot.remainingCapacity) || 0), 0);
    return {
      totalSlots: slots.length,
      activeSlots: slots.filter((slot) => slot.status === "ACTIVE").length,
      totalBookable,
      totalBuffer,
      occupied,
      remaining,
    };
  }, [slots]);

  async function loadSlots(nextEventType = eventType) {
    if (!nextEventType) return;
    setIsLoading(true);
    setError("");
    try {
      setSlots(await getEntrySlots(nextEventType));
    } catch (err) {
      setError(err.message || "Failed to load entry slots.");
      setSlots([]);
    } finally {
      setIsLoading(false);
    }
  }

  function resetForm() {
    setSlotForm(INITIAL_SLOT_FORM);
    setEditingSlotId("");
    setError("");
  }

  function handleInputChange(event) {
    const { name, value } = event.target;
    setSlotForm((prev) => ({ ...prev, [name]: value }));
    setError("");
    setSuccess("");
  }

  function editSlot(slot) {
    setEditingSlotId(slot.slotId);
    setSlotForm({
      name: slot.name || "",
      nickname: slot.nickname || "",
      bookableCapacity: String(slot.bookableCapacity ?? 0),
      bufferCapacity: String(slot.bufferCapacity ?? 0),
      status: slot.status || "ACTIVE",
      bookingStartsAt: toLocalDateTimeValue(slot.bookingStartsAt),
      bookingEndsAt: toLocalDateTimeValue(slot.bookingEndsAt),
    });
    setError("");
    setSuccess("");
  }

  async function handleSaveSlot(event) {
    event.preventDefault();
    setError("");
    setSuccess("");

    if (!eventType) {
      setError("Select an event before saving slots.");
      return;
    }

    const payload = {
      name: slotForm.name.trim(),
      nickname: slotForm.nickname.trim(),
      bookableCapacity: Number(slotForm.bookableCapacity || 0),
      bufferCapacity: Number(slotForm.bufferCapacity || 0),
      status: slotForm.status,
      bookingStartsAt: toIsoString(slotForm.bookingStartsAt),
      bookingEndsAt: toIsoString(slotForm.bookingEndsAt),
    };

    if (!payload.name || !payload.nickname) {
      setError("Slot name and nickname are required.");
      return;
    }
    if (payload.bookableCapacity < 0 || payload.bufferCapacity < 0) {
      setError("Capacity values must be 0 or greater.");
      return;
    }
    if (payload.bookingStartsAt && payload.bookingEndsAt && payload.bookingStartsAt > payload.bookingEndsAt) {
      setError("Booking start must be before booking end.");
      return;
    }

    setIsSaving(true);
    try {
      const savedSlot = await saveEntrySlot(eventType, payload, editingSlotId);
      setSlots((prev) => {
        const next = prev.filter((slot) => slot.slotId !== savedSlot.slotId);
        return [...next, savedSlot].sort((a, b) => String(a.createdAt || "").localeCompare(String(b.createdAt || "")));
      });
      setSuccess(editingSlotId ? "Entry slot updated." : "Entry slot created.");
      resetForm();
    } catch (err) {
      setError(err.message || "Failed to save entry slot.");
    } finally {
      setIsSaving(false);
    }
  }

  async function handleDeleteSlot(slot) {
    if (!window.confirm(`Delete entry slot ${slot.name || slot.slotId}? Slots with reservations cannot be deleted.`)) {
      return;
    }

    setError("");
    setSuccess("");
    try {
      await deleteEntrySlot(eventType, slot.slotId);
      setSlots((prev) => prev.filter((item) => item.slotId !== slot.slotId));
      if (editingSlotId === slot.slotId) resetForm();
      setSuccess("Entry slot deleted.");
    } catch (err) {
      setError(err.message || "Failed to delete entry slot.");
    }
  }

  async function handleReleaseBuffer(slot) {
    const value = window.prompt(`Release how many buffer seats for ${slot.name}?`, "1");
    if (value == null) return;
    const numberOfSeats = Number(value);
    if (!Number.isInteger(numberOfSeats) || numberOfSeats <= 0) {
      setError("Enter a positive whole number of buffer seats to release.");
      return;
    }

    setError("");
    setSuccess("");
    try {
      const updatedSlot = await releaseEntrySlotBuffer(eventType, slot.slotId, numberOfSeats);
      setSlots((prev) => prev.map((item) => (item.slotId === updatedSlot.slotId ? { ...item, ...updatedSlot } : item)));
      setSuccess(`Released ${numberOfSeats} buffer seat${numberOfSeats === 1 ? "" : "s"}.`);
    } catch (err) {
      setError(err.message || "Failed to release buffer seats.");
    }
  }

  return (
    <>
      <div className="admin-stats entry-slot-stats">
        <div className="stat-box">
          <div className="stat-value">{slotStats.totalSlots}</div>
          <div className="stat-label">Slots</div>
        </div>
        <div className="stat-box">
          <div className="stat-value">{slotStats.activeSlots}</div>
          <div className="stat-label">Active</div>
        </div>
        <div className="stat-box">
          <div className="stat-value">{slotStats.occupied}</div>
          <div className="stat-label">Occupied</div>
        </div>
        <div className="stat-box">
          <div className="stat-value">{slotStats.remaining}</div>
          <div className="stat-label">Remaining</div>
        </div>
        <div className="stat-box">
          <div className="stat-value">{slotStats.totalBuffer}</div>
          <div className="stat-label">Buffer</div>
        </div>
      </div>

      <div className="admin-controls">
        <select
          value={eventType}
          onChange={(event) => {
            setEventType(event.target.value);
            resetForm();
          }}
          className="search-input"
          aria-label="Select event for entry slots"
        >
          <option value="">Select event...</option>
          {selectableEvents.map((event) => (
            <option key={event.eventType} value={event.eventType}>
              {event.eventName || event.eventType}
            </option>
          ))}
        </select>
        <button className="button secondary" type="button" onClick={() => loadSlots()} disabled={!eventType || isLoading}>
          {isLoading ? "Refreshing..." : "Refresh Slots"}
        </button>
      </div>

      <div className="partner-management-grid entry-slot-grid">
        <form className="partner-panel partner-form-panel" onSubmit={handleSaveSlot}>
          <div className="panel-heading">
            <div>
              <h3>{editingSlotId ? "Edit Entry Slot" : "Add Entry Slot"}</h3>
              <p>Capacity is counted in seats. Kids below 6 do not consume seats.</p>
            </div>
            <button type="button" className="button secondary" onClick={resetForm} disabled={isSaving}>
              Clear
            </button>
          </div>

          <div className="field-row">
            <div className="field">
              <label htmlFor="entrySlotName">Slot Name</label>
              <input
                id="entrySlotName"
                name="name"
                type="text"
                value={slotForm.name}
                onChange={handleInputChange}
                placeholder="11:00 AM - 11:30 AM"
                disabled={isSaving}
              />
            </div>
            <div className="field">
              <label htmlFor="entrySlotNickname">Short Label</label>
              <input
                id="entrySlotNickname"
                name="nickname"
                type="text"
                value={slotForm.nickname}
                onChange={handleInputChange}
                placeholder="Early entry"
                disabled={isSaving}
              />
            </div>
          </div>

          <div className="field-row">
            <div className="field">
              <label htmlFor="entrySlotBookable">Bookable Seats</label>
              <input
                id="entrySlotBookable"
                name="bookableCapacity"
                type="number"
                min="0"
                value={slotForm.bookableCapacity}
                onChange={handleInputChange}
                disabled={isSaving}
              />
            </div>
            <div className="field">
              <label htmlFor="entrySlotBuffer">Buffer Seats</label>
              <input
                id="entrySlotBuffer"
                name="bufferCapacity"
                type="number"
                min="0"
                value={slotForm.bufferCapacity}
                onChange={handleInputChange}
                disabled={isSaving}
              />
            </div>
          </div>

          <div className="field-row">
            <div className="field">
              <label htmlFor="entrySlotStartsAt">Booking Starts</label>
              <input
                id="entrySlotStartsAt"
                name="bookingStartsAt"
                type="datetime-local"
                value={slotForm.bookingStartsAt}
                onChange={handleInputChange}
                disabled={isSaving}
              />
            </div>
            <div className="field">
              <label htmlFor="entrySlotEndsAt">Booking Ends</label>
              <input
                id="entrySlotEndsAt"
                name="bookingEndsAt"
                type="datetime-local"
                value={slotForm.bookingEndsAt}
                onChange={handleInputChange}
                disabled={isSaving}
              />
            </div>
          </div>

          <div className="field">
            <label htmlFor="entrySlotStatus">Status</label>
            <select id="entrySlotStatus" name="status" value={slotForm.status} onChange={handleInputChange} disabled={isSaving}>
              <option value="ACTIVE">Active</option>
              <option value="INACTIVE">Inactive</option>
            </select>
          </div>

          {error ? <div className="error-banner">{error}</div> : null}
          {success ? <div className="success-banner">{success}</div> : null}

          <div className="change-password-actions partner-actions-row">
            <button type="submit" className="button primary" disabled={isSaving || !eventType}>
              {isSaving ? "Saving..." : editingSlotId ? "Update Slot" : "Add Slot"}
            </button>
          </div>
        </form>

        <div className="partner-panel partner-list-panel">
          <div className="panel-heading">
            <div>
              <h3>Slot Occupancy</h3>
              <p>Confirmed and held reservations are counted against bookable capacity.</p>
            </div>
          </div>

          {!eventType ? (
            <div className="empty-state">Select an event to manage entry slots.</div>
          ) : isLoading ? (
            <div className="loading">Loading entry slots...</div>
          ) : slots.length === 0 ? (
            <div className="empty-state">No entry slots configured for this event</div>
          ) : (
            <div className="entry-slot-list">
              {slots.map((slot) => {
                const percent = getCapacityPercent(slot);
                const bookable = Number(slot.bookableCapacity) || 0;
                const occupied = Number(slot.occupiedSeats) || 0;
                const remaining = Number(slot.remainingCapacity) || 0;
                const buffer = Number(slot.bufferCapacity) || 0;
                return (
                  <article className={`entry-slot-card ${slot.status !== "ACTIVE" ? "entry-slot-card-inactive" : ""}`} key={slot.slotId}>
                    <div className="partner-card-top">
                      <div>
                        <h4>{slot.name}</h4>
                        <p>{slot.nickname || slot.slotId}</p>
                      </div>
                      <span className={`partner-status ${slot.status === "ACTIVE" ? "active" : "inactive"}`}>
                        {slot.status || "INACTIVE"}
                      </span>
                    </div>

                    <div className="entry-slot-capacity-row">
                      <div>
                        <strong>{occupied}</strong>
                        <span>occupied</span>
                      </div>
                      <div>
                        <strong>{remaining}</strong>
                        <span>remaining</span>
                      </div>
                      <div>
                        <strong>{bookable}</strong>
                        <span>bookable</span>
                      </div>
                      <div>
                        <strong>{buffer}</strong>
                        <span>buffer</span>
                      </div>
                    </div>

                    <div className="entry-slot-meter" aria-label={`${percent}% occupied`}>
                      <span style={{ width: `${percent}%` }} />
                    </div>

                    <div className="partner-meta">
                      <span>Opens {formatDateTime(slot.bookingStartsAt)}</span>
                      <span>Closes {formatDateTime(slot.bookingEndsAt)}</span>
                    </div>

                    <div className="partner-card-actions">
                      <button className="button secondary" type="button" onClick={() => editSlot(slot)}>
                        Edit
                      </button>
                      <button className="button secondary" type="button" onClick={() => handleReleaseBuffer(slot)} disabled={buffer <= 0}>
                        Release Buffer
                      </button>
                      <button className="button secondary" type="button" onClick={() => handleDeleteSlot(slot)}>
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
  );
}