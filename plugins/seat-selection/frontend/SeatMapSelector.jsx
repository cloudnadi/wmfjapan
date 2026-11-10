// Presentational Celebrity Event seat picker — the hall layout matches
// 葛西区民館ホール's own printed seating chart exactly (19 rows, narrower
// at the stage end, two raked "preferred" zones). No internal fetch —
// driven entirely by props, same as EntrySlotSelector, so it fits the same
// "parent owns the data" pattern used across this app.

import { rowLabelFor, seatDisplayLabel } from "./rowLabels";

const ZONE_LABELS = { zone1: "Zone 1", zone2: "Zone 2", zone3: "Zone 3", regular: "Regular" };

// Zone 1/2/3 are the hall's raked "preferred" zones — shown with a crown
// badge. Camera/projector seats aren't selectable (they're admin-blocked,
// same as any other blocked seat) but get their own icon instead of the
// plain gray "blocked" swatch, via the specialSeats prop ({seatId: category}).
const ZONE_ICONS = { zone1: "👑", zone2: "👑", zone3: "👑" };
const SPECIAL_ICONS = { camera: "📷", projector: "🎥" };
const SPECIAL_LABELS = { camera: "Camera", projector: "Projector" };

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

// zonePricing (optional): {category: {displayName, pricePerFamily,
// pricePerPerson, maxFamilySize, status}} — when given, the legend shows
// each priced zone's rate instead of just its name.
function zonePriceLabel(category, zonePricing) {
  const price = zonePricing?.[category];
  if (!price || price.status !== "ACTIVE") return null;
  const person = `¥${Number(price.pricePerPerson || 0).toLocaleString()}/person`;
  if (!price.pricePerFamily) return person;
  const cap = Number(price.maxFamilySize || 0);
  const family = `¥${Number(price.pricePerFamily).toLocaleString()}/family${cap ? ` up to ${cap}` : ""}`;
  return `${family} · ${person}`;
}

export default function SeatMapSelector({
  layout = [],
  bookedSeatIds = [],
  specialSeats = {},
  walkwayAfterRow = 10,
  zoneAvailable = null,
  zonePricing = null,
  categoryCaps = null,
  maxSeats = Infinity,
  loading,
  loaded,
  error,
  selectedSeatIds = [],
  onSelectionChange,
  // mySeatIds (optional): seats this viewer already holds — highlighted in
  // their own colour even though they're in bookedSeatIds. readOnly turns
  // the map into a plain viewer (nothing is clickable, no pick counter).
  mySeatIds = [],
  readOnly = false,
}) {
  const bookedSet = new Set(bookedSeatIds);
  const mySet = new Set(mySeatIds);
  const selectedSet = new Set(selectedSeatIds);

  // categoryCaps (optional): {category: maxSelectable} — a per-category cap
  // independent of maxSeats' overall total, for a host that's gated *which*
  // zone a party may pick from rather than (or in addition to) how many
  // seats overall. Only meaningful for zone1/zone2/zone3 — "regular" is
  // never restricted this way. A category simply absent from the dict is
  // fully locked (0), not uncapped — see wmfjapan's own
  // eventInfo.seatSelection.pricingEnabled=false flow, the one caller that
  // passes this today. null (the default) disables the whole mechanism, so
  // every category behaves exactly as before this prop existed.
  const categoryBySeatId = {};
  for (const row of layout) {
    for (const seat of row.seats || []) {
      categoryBySeatId[seat.seatId] = seat.category;
    }
  }
  const selectedCountByCategory = {};
  if (categoryCaps) {
    for (const seatId of selectedSeatIds) {
      const category = categoryBySeatId[seatId];
      if (category) selectedCountByCategory[category] = (selectedCountByCategory[category] || 0) + 1;
    }
  }

  const isCategoryFull = (seatId) => {
    if (!categoryCaps) return false;
    const category = categoryBySeatId[seatId];
    if (!category || category === "regular") return false;
    return (selectedCountByCategory[category] || 0) >= (categoryCaps[category] || 0);
  };

  const toggleSeat = (seatId) => {
    if (readOnly) return;
    if (bookedSet.has(seatId) && !selectedSet.has(seatId)) return;
    if (!selectedSet.has(seatId) && isCategoryFull(seatId)) return;
    const next = selectedSet.has(seatId)
      ? selectedSeatIds.filter((id) => id !== seatId)
      : selectedSeatIds.length >= maxSeats
        ? selectedSeatIds
        : [...selectedSeatIds, seatId];
    onSelectionChange?.(next);
  };

  return (
    <div className="seat-map-selector">
      <div className="section-label">Seat Selection</div>
      <p className="field-note">
        {readOnly
          ? "Your seats are highlighted on the map."
          : Number.isFinite(maxSeats)
          ? `Choose up to ${maxSeats} seat${maxSeats === 1 ? "" : "s"}.`
          : "Choose your seats below."}
      </p>

      {loading ? (
        <div className="entry-slot-selector-state">Loading the seat map...</div>
      ) : error ? (
        <div className="entry-slot-selector-state error">{error}</div>
      ) : loaded && layout.length === 0 ? (
        <div className="entry-slot-selector-state error">
          Seat selection isn't set up for this event yet. Please check back later.
        </div>
      ) : (
        <>
          {zoneAvailable ? (
            <div className="seat-map-zone-stats">
              {Object.keys(ZONE_LABELS).map((zone) => (
                <div className="seat-map-zone-stat" key={zone}>
                  <span className="seat-map-zone-stat-value">{zoneAvailable[zone] ?? 0}</span>
                  <span className="seat-map-zone-stat-label">{ZONE_LABELS[zone]} Available</span>
                </div>
              ))}
            </div>
          ) : null}

          <div className="seat-map-legend">
            <span className="seat-map-legend-item"><i className="seat-swatch zone1">👑</i> Zone 1{zonePricing ? ` — ${zonePriceLabel("zone1", zonePricing) || "not for sale"}` : ""}</span>
            <span className="seat-map-legend-item"><i className="seat-swatch zone2">👑</i> Zone 2{zonePricing ? ` — ${zonePriceLabel("zone2", zonePricing) || "not for sale"}` : ""}</span>
            <span className="seat-map-legend-item"><i className="seat-swatch zone3">👑</i> Zone 3{zonePricing ? ` — ${zonePriceLabel("zone3", zonePricing) || "not for sale"}` : ""}</span>
            <span className="seat-map-legend-item"><i className="seat-swatch regular" /> Regular</span>
            <span className="seat-map-legend-item"><i className="seat-swatch selected" /> Selected</span>
            {mySeatIds.length > 0 ? (
              <span className="seat-map-legend-item"><i className="seat-swatch mine" /> Your seat</span>
            ) : null}
            <span className="seat-map-legend-item"><i className="seat-swatch booked" /> Booked</span>
            <span className="seat-map-legend-item"><i className="seat-swatch special">📷</i> Camera</span>
            <span className="seat-map-legend-item"><i className="seat-swatch special">🎥</i> Projector</span>
            {categoryCaps ? (
              <span className="seat-map-legend-item"><i className="seat-swatch not-entitled" /> Requires a zone upgrade</span>
            ) : null}
          </div>

          <div className="seat-map-stage">STAGE</div>

          <div className="seat-map-grid">
            {layout.map((row, index) => (
              <div key={row.row}>
                {index === walkwayAfterRow ? <div className="seat-map-walkway">WALKWAY</div> : null}
                <div className="seat-map-row">
                  <span className="seat-map-row-label">{rowLabelFor(row.row)}</span>
                  {splitRowByAisles(row.seats).map((block, blockIndex, blocks) => (
                    <div className="seat-map-row-block-wrap" key={blockIndex}>
                      {blockIndex > 0 ? <div className="seat-map-aisle" /> : null}
                      <div className="seat-map-row-seats">
                        {blockIndex === 0 ? blankCells(row.blankEdges, "lead") : null}
                        {block.map((seat) => {
                          const isSelected = selectedSet.has(seat.seatId);
                          const isMine = mySet.has(seat.seatId) && !isSelected;
                          const isBooked = bookedSet.has(seat.seatId) && !isSelected && !isMine;
                          const special = specialSeats[seat.seatId];
                          // Regular is always free/bookable, regardless of
                          // zonePricing — only zone1/2/3 can ever be "not
                          // for sale" (see seat_selection_logic.PRICEABLE_CATEGORIES).
                          const isUnpriced =
                            !isSelected &&
                            !isBooked &&
                            seat.category !== "regular" &&
                            zonePricing &&
                            (!zonePricing[seat.category] || zonePricing[seat.category].status !== "ACTIVE");
                          // Not "not for sale" (isUnpriced) but "not yours to
                          // pick" — this party hasn't paid for this zone
                          // (or has used up what it did pay for). See
                          // categoryCaps' own docstring above.
                          const isNotEntitled =
                            !isSelected && !isBooked && !isUnpriced && isCategoryFull(seat.seatId);
                          const categoryClass = isSelected
                            ? "selected"
                            : isMine
                              ? "mine"
                              : isBooked
                              ? special
                                ? `booked special-${special}`
                                : "booked"
                              : isUnpriced
                                ? "unpriced"
                                : isNotEntitled
                                  ? "not-entitled"
                                  : seat.category;
                          // A not-entitled seat still shows its zone's own
                          // crown rather than a generic lock — the dimmed,
                          // diagonally-striped .not-entitled background
                          // (see seat-map.css) is what actually marks it
                          // unselectable. Regular has no icon of its own and
                          // is never not-entitled (see isCategoryFull above),
                          // so it just renders blank either way.
                          const icon = isSelected || isMine || (isBooked && !special) ? "" : special ? SPECIAL_ICONS[special] : ZONE_ICONS[seat.category] || "";
                          return (
                            <button
                              key={seat.seatId}
                              type="button"
                              className={`seat-map-seat ${categoryClass}`}
                              disabled={readOnly || isBooked || isUnpriced || isNotEntitled || loading}
                              onClick={() => toggleSeat(seat.seatId)}
                              aria-pressed={isSelected}
                              aria-label={`Seat ${seatDisplayLabel(seat.seatId)}${isMine ? " (your seat)" : isBooked ? " (booked)" : ""}`}
                              title={
                                special
                                  ? `${seatDisplayLabel(seat.seatId)} · ${SPECIAL_LABELS[special]}`
                                  : isNotEntitled
                                    ? `${seatDisplayLabel(seat.seatId)} · ${ZONE_LABELS[seat.category]} · requires a zone upgrade`
                                    : `${seatDisplayLabel(seat.seatId)} · ${ZONE_LABELS[seat.category] || "Regular"}${
                                      seat.category === "regular"
                                        ? " · included"
                                        : zonePricing
                                          ? ` · ${zonePriceLabel(seat.category, zonePricing) || "not for sale"}`
                                          : ""
                                    }`
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
      {readOnly ? null : (
        <p className="field-note">
          Selected: {selectedSeatIds.length}
          {Number.isFinite(maxSeats) ? ` / ${maxSeats}` : ""} seat{selectedSeatIds.length === 1 ? "" : "s"}
        </p>
      )}
    </div>
  );
}
