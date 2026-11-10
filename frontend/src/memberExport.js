// Members tab → "Export CSV". Pure helpers so the file format can be checked on its own.

// The year shown first until the admin picks another one (the current renewal year).
export const DEFAULT_WHATSAPP_YEAR = "2026-27";
export const yearLabel = (year) => String(year).replace("-", "–");
export const WHATSAPP_LABELS = { joined: "Joined", not_joined: "Not joined" };

function columns(year) {
  return [
    ["Member ID", (m) => m.memberId],
    ["Name", (m) => m.name],
    ["Email", (m) => m.email],
    ["Phone", (m) => m.phone],
    ["NPO status 2025–26", (m) => m.npoStatus2025 || "Non-member"],
    ["NPO status 2026–27", (m) => m.npoStatus2026 || "Non-member"],
    ["Last active from", (m) => m.lastActivePeriod?.from],
    ["Last active to", (m) => m.lastActivePeriod?.to],
    [`WhatsApp group ${yearLabel(year)}`, (m) => WHATSAPP_LABELS[m.whatsappGroup?.[year]] || ""],
  ];
}

// A cell that starts with = @ (or + / - followed by anything other than a plain phone number) would be
// run as a formula by Excel / Sheets, so such text (member names are free text) is prefixed with a quote.
function csvCell(value) {
  let text = String(value ?? "");
  const isPlainNumber = /^\+[0-9][0-9\s().-]*$/.test(text);
  if (/^[=@\t\r]/.test(text) || (/^[+-]/.test(text) && !isPlainNumber)) text = `'${text}`;
  return `"${text.replace(/"/g, '""')}"`;
}

/** CSV text (with a BOM so Excel reads Japanese names correctly) for the given members; the WhatsApp column is for `year`. */
export function membersToCsv(members, year = DEFAULT_WHATSAPP_YEAR) {
  const cols = columns(year);
  const lines = [cols.map(([header]) => csvCell(header)).join(",")];
  for (const member of members) {
    lines.push(cols.map(([, get]) => csvCell(get(member))).join(","));
  }
  return `﻿${lines.join("\r\n")}\r\n`;
}

export function membersCsvFilename(filtered, today = new Date()) {
  return `members-${today.toISOString().slice(0, 10)}${filtered ? "-filtered" : ""}.csv`;
}
