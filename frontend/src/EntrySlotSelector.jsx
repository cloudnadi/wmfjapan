function formatSlotWindow(slot) {
  const starts = slot.bookingStartsAt ? new Date(slot.bookingStartsAt) : null;
  const ends = slot.bookingEndsAt ? new Date(slot.bookingEndsAt) : null;
  const format = (date) => (
    date && !Number.isNaN(date.getTime())
      ? date.toLocaleString(undefined, { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" })
      : ""
  );

  if (starts && ends) return `${format(starts)} - ${format(ends)}`;
  if (starts) return `Opens ${format(starts)}`;
  if (ends) return `Closes ${format(ends)}`;
  return "Available now";
}

function capacityLevel(slot) {
  const remaining = Number(slot.remainingCapacity) || 0;
  const bookable = Number(slot.bookableCapacity) || 0;
  if (bookable <= 0) return "low";
  const ratio = remaining / bookable;
  if (ratio <= 0.15) return "low";
  if (ratio <= 0.35) return "medium";
  return "good";
}

export default function EntrySlotSelector({
  slots = [],
  requiredSeats,
  loading,
  loaded,
  error,
  allocations = [],
  onAllocationsChange,
}) {
  const allocatedSeats = allocations.reduce((sum, allocation) => sum + Number(allocation.seatCount || 0), 0);
  const updateAllocation = (slotId, seatCount) => {
    const next = allocations.filter((allocation) => allocation.slotId !== slotId);
    if (seatCount > 0) next.push({ slotId, seatCount });
    onAllocationsChange?.(next);
  };

  return (
    <div className="entry-slot-selector">
      <div className="section-label">Entry Slot</div>
      <p className="field-note">
        Assign {requiredSeats} seated people across one or more entry slots.
      </p>

      {loading ? (
        <div className="entry-slot-selector-state">Loading available entry slots...</div>
      ) : error ? (
        <div className="entry-slot-selector-state error">{error}</div>
      ) : loaded && slots.length === 0 ? (
        <div className="entry-slot-selector-state error">No entry slots are currently available for this registration size.</div>
      ) : (
        <div className="entry-slot-selector-grid" role="radiogroup" aria-label="Entry slot selection">
          {slots.map((slot) => {
            const allocation = allocations.find((item) => item.slotId === slot.slotId);
            const selected = Boolean(allocation);
            const level = capacityLevel(slot);
            return (
              <div
                key={slot.slotId}
                className={`entry-slot-option ${selected ? "selected" : ""}`}
              >
                <span className="entry-slot-option-main">
                  <span className="entry-slot-option-name">{slot.name}</span>
                  <span className="entry-slot-option-window">{formatSlotWindow(slot)}</span>
                </span>
                <span className={`entry-slot-option-capacity ${level}`}>
                  {slot.remainingCapacity} left
                </span>
                <label>
                  People
                  <input
                    type="number"
                    min="0"
                    max={Math.min(Number(slot.remainingCapacity) || requiredSeats, requiredSeats)}
                    value={allocation?.seatCount || ""}
                    onChange={(event) => updateAllocation(slot.slotId, Math.max(0, Number(event.target.value) || 0))}
                    aria-label={`People assigned to ${slot.name}`}
                  />
                </label>
              </div>
            );
          })}
        </div>
      )}
      <p className="field-note">Assigned: {allocatedSeats} / {requiredSeats}</p>
    </div>
  );
}