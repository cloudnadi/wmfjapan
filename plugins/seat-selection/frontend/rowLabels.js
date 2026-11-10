// The hall's own row lettering (per the venue's printed seating chart) is
// the classical iroha ordering — い/ろ/は/... — not plain row numbers.
// seat.seatId (e.g. "R04C12") stays the real, stored identifier everywhere
// (claims, purchases, QR tokens); this is a display-only relabeling, shared
// by every component/screen (plugin and host alike) that shows a seat ID to
// a person, so they never drift out of sync with each other.
export const ROW_LABELS = ["い", "ろ", "は", "に", "ほ", "へ", "と", "ち", "り", "ぬ", "る", "を", "わ", "か", "よ", "た", "れ", "そ", "つ"];

export function rowLabelFor(rowNumber) {
  return ROW_LABELS[rowNumber - 1] || String(rowNumber);
}

// The venue's own printed numbers differ from the internal column index
// (seatId's "C08" etc., which just counts 1..N continuously per row) in two
// ways, confirmed against the venue's own seating chart:
//
// 1. The Left block's own last seat (nearest the aisle) is always printed
//    "9" — the digit 8 is never printed anywhere. The Center block's own
//    first seat is *also* printed "9" (its own independent local count,
//    always 9-19 regardless of row width) — two different physical seats
//    sharing a printed number, disambiguated by which side of the aisle
//    they're on, not a collision.
// 2. Rows narrower than the full 8-seat Left/Right blocks (rows 1-3) drop
//    seats off the *low* end of the numbering, not the end nearest the
//    aisle — e.g. row い's Left block is only 4,5,6,7,9 (5 seats), not
//    1,2,3,4,5.
//
// This mirrors seat_selection_logic.py's _ROW_SPECS exactly (only rows 1-3
// are narrower than the 8-seat default) — see that module for the venue's
// full per-row shape.
const CENTER_COUNT = 11;
const LEFT_COUNT_OVERRIDES = { 1: 5, 2: 6, 3: 7 };

function leftCountForRow(rowNumber) {
  return LEFT_COUNT_OVERRIDES[rowNumber] ?? 8;
}

export function seatDisplayLabel(seatId) {
  const match = /^R(\d+)C(\d+)$/.exec(seatId || "");
  if (!match) return seatId;
  const row = Number(match[1]);
  const col = Number(match[2]);
  const leftCount = leftCountForRow(row);

  let number;
  if (col <= leftCount) {
    // Left block: last seat is always "9"; earlier seats count backward
    // from "7" (dropping the lowest numbers first on a narrower row).
    number = col === leftCount ? 9 : col + 8 - leftCount;
  } else if (col <= leftCount + CENTER_COUNT) {
    // Center block: always 9-19, independent of row width.
    number = col - leftCount + 8;
  } else {
    // Right block: always starts at 20, narrower rows just end earlier.
    number = col - leftCount - CENTER_COUNT + 19;
  }
  return `${rowLabelFor(row)}-${number}`;
}
