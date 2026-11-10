import { useState, useEffect, useMemo, useRef } from "react";
import { DEFAULT_WHATSAPP_YEAR, membersToCsv, membersCsvFilename, yearLabel } from "./memberExport";
import {
  getRegistrations,
  exportRegistrationsCSV,
  downloadCSV,
  getPartners,
  savePartner,
  deletePartner,
  getEvents,
  saveEvent,
  deleteEvent,
  getMembers,
  getNpoMembershipConfig,
  saveMember,
  updateMember,
  deleteMember,
  getNpoTransactions,
  saveNpoTransaction,
  updateNpoTransaction,
  deleteNpoTransaction,
  cancelNpoTransaction,
  getVisitSummary,
  saveRegistration,
  deleteRegistration,
  processBulkRegistrations,
  getAdminSiteConfig,
  uploadCarouselImage,
  updatePartnersDisplayMode,
  getEntrySlots,
  reassignRegistrationSlot,
  getAvailableSeats,
  getRegistrationSeatAllocation,
  allocateRegistrationSeats,
  getEventSeats,
  blockEventSeat,
  unblockEventSeat,
  saveZonePrice,
  deleteZonePrice,
  getEventSeatPurchases,
  updateSeatPurchaseStatus,
  sendSeatSelectionAnnouncement,
  checkInRegistrationById,
  previewReconciliationTransactions,
  importReconciliationTransactions,
  getReconciliationTransactions,
  deleteReconciliationBatch,
  runReconciliation,
  getReconciliationRuns,
  getReconciliationRun,
  exportReconciliationRunCSV,
} from "./api";
import {
  loginUser,
  completeNewPasswordChallenge,
  requestPasswordResetCode,
  confirmPasswordReset,
  changePasswordAuthenticated,
  logout as cognitoLogout,
  getAdminEmail,
  isAuthenticated as hasValidAdminSession,
} from "./cognito";
import { onAdminSessionChange } from "./adminSession";
import Navbar from "./Navbar";
import EntrySlotManagement from "./EntrySlotManagement";
import SeatUpgradeManagement from "@plugins/seat-upgrade/frontend/SeatUpgradeManagement";
import { uploadSeatUpgradeSeatMap } from "@plugins/seat-upgrade/frontend/api";
import EntrySlotSelector from "./EntrySlotSelector";
import { SeatMapManagement, SeatPurchaseManagement, SeatMapSelector } from "seat-selection-plugin";
import CelebrityDinnerManagement from "./CelebrityDinnerManagement";
import NpoMembershipSettings, { PaymentMethodsSettings } from "./NpoMembershipSettings";
import AgmSettings from "./AgmSettings";
import MemberAnnouncements from "./MemberAnnouncements";

const HOME_PATH = "/";

const RESEND_COOLDOWN_SECONDS = 30;
const CHANGE_PASSWORD_SUCCESS_CLOSE_MS = 2000;
const PARTNER_SCOPE_OPTIONS = [
  { value: "all", label: "All events" },
];

const PARTNER_TIER_OPTIONS = [
  { value: "emerald",  label: "Emerald"  },
  { value: "platinum", label: "Platinum" },
  { value: "gold",     label: "Gold"     },
  { value: "silver",   label: "Silver"   },
  { value: "bronze",   label: "Bronze"   },
];

const INITIAL_PARTNER_FORM = {
  partnerId: "",
  name: "",
  websiteUrl: "",
  logoUrl: "",
  description: "",
  tier: "gold",
  scope: "all",
  displayOrder: "0",
  isActive: true,
};

const EVENT_STATUS_OPTIONS = [
  { value: "active", label: "Active (accepting registrations)" },
  { value: "inactive", label: "Inactive (hidden / closed)" },
  { value: "archived", label: "Archived" },
];

const INITIAL_EVENT_FORM = {
  eventType: "",
  eventName: "",
  eventDate: "",
  hostId: "",
  status: "inactive",
  description: "",
  venue: "",
  venueAddress: "",
  eventNote: "",
  registrationPath: "",
  capacity: "0",
  registrationClosesOn: "",
  cancellationEnabled: true,
  onamRegistrationClosed: false,
  celebrityRegistrationClosed: false,
  sadhyaCapacity: "",
  celebrityCapacity: "",
  sadhyaSlotStartDate: "",
  sadhyaSlotEndDate: "",
  paymentLinkCredit: "",
  paymentLinkPayPal: "",
  paymentLinkBank: "",
  seatSelectionEnabled: false,
  seatUpgradeEnabled: false,
  seatUpgradeUrl: "",
  seatUpgradeSeatMapImageUrl: "",
  seatUpgradeStartDate: "",
  seatUpgradeEndDate: "",
  seatUpgradePaymentPaypay: true,
  seatUpgradePaymentSquare: true,
  seatUpgradePaymentBank: true,
  celebrityDinnerEnabled: false,
  eventInfo: {},
};

const SEAT_SELECTION_PAYMENT_METHOD_LABELS = { paypay: "PayPay", square: "Credit/Debit Card (Square)", bank: "Bank Transfer" };

const SEAT_UPGRADE_PAYMENT_METHODS = [
  { key: "paypay", formKey: "seatUpgradePaymentPaypay", label: "PayPay" },
  { key: "square", formKey: "seatUpgradePaymentSquare", label: "Square (Credit Card)" },
  { key: "bank", formKey: "seatUpgradePaymentBank", label: "Bank Transfer" },
];

const INITIAL_MEMBER_FORM = {
  email: "",
  fullname: "",
  phone: "",
  memberid: "",
  npoFlagActive: false,
  npoMembInterested: false,
  renewedForNextYear: false,
};

const INITIAL_NPO_TXN_FORM = {
  membDateFrom: "",
  membDateTo: "",
  paidDate: "",
  membershipAmountPaid: "",
  paidVia: "",
  paymentStatus: "",
  email: "",
  notes: "",
};

// Pre-filled on new and pending NPO transactions (the 2026-27 membership year).
const NPO_DEFAULT_FROM = "2026-11-01";
const NPO_DEFAULT_TO = "2027-10-31";
const NPO_DEFAULT_AMOUNT = "1000";

// Values stored in npo_membership.paidVia; legacy imports may hold others, which
// the dropdown keeps as an extra option so editing never silently changes them.
// Members tab: filter by NPO status. Statuses come from the server per membership year
// (2025–26: Member / Non-member; 2026–27: Renewed / Returning Member / New Member / Non-member).
const npoNone = (value) => !value || value === "Non-member";
const whatsappStatus = (member, year) => member.whatsappGroup?.[year] || "";
// NPO membership is known for these two years only (the statuses the server computes).
const coveredInYear = (member, year) =>
  year === "2025-26" ? !npoNone(member.npoStatus2025) : year === "2026-27" ? !npoNone(member.npoStatus2026) : null;
const NPO_STATUS_FILTER_GROUPS = [{ label: "All" }, { label: "2026–27" }, { label: "2025–26" }];
const NPO_STATUS_FILTERS = [
  { group: "All", value: "all", label: "All members", test: () => true },
  { group: "2026–27", value: "2026:member", label: "Any member (new, returning or renewed)", test: (m) => !npoNone(m.npoStatus2026) },
  { group: "2026–27", value: "2026:new", label: "New Member (first time)", test: (m) => m.npoStatus2026 === "New Member" },
  {
    group: "2026–27",
    value: "2026:returning",
    label: "Returning Member (back after a gap)",
    test: (m) => m.npoStatus2026 === "Returning Member",
  },
  { group: "2026–27", value: "2026:renewed", label: "Renewed", test: (m) => m.npoStatus2026 === "Renewed" },
  { group: "2026–27", value: "2026:none", label: "Non-member", test: (m) => npoNone(m.npoStatus2026) },
  {
    group: "2026–27",
    value: "2026:to-renew",
    label: "Yet to renew (2025–26 members)",
    test: (m) => !npoNone(m.npoStatus2025) && npoNone(m.npoStatus2026),
  },
  {
    group: "2026–27",
    value: "2026:lapsed",
    label: "Lapsed (earlier members, not in 2025–26 or 2026–27)",
    test: (m) => npoNone(m.npoStatus2025) && npoNone(m.npoStatus2026) && !!m.lastActivePeriod?.to,
  },
  { group: "2025–26", value: "2025:member", label: "Member", test: (m) => !npoNone(m.npoStatus2025) },
  { group: "2025–26", value: "2025:none", label: "Non-member", test: (m) => npoNone(m.npoStatus2025) },
];

// Members tab: WhatsApp group filters, for the year chosen in the year selector.
const WHATSAPP_FILTERS = [
  { value: "wa:joined", label: () => "Joined", test: (m, y) => whatsappStatus(m, y) === "joined" },
  { value: "wa:not_joined", label: () => "Not joined", test: (m, y) => whatsappStatus(m, y) === "not_joined" },
  { value: "wa:unset", label: () => "Not recorded", test: (m, y) => !whatsappStatus(m, y) },
  {
    value: "wa:to-invite",
    label: (y) => `${yearLabel(y)} members not yet in the group`,
    supported: (y) => coveredInYear({}, y) !== null,
    test: (m, y) => coveredInYear(m, y) === true && whatsappStatus(m, y) !== "joined",
  },
];

// NPO transactions list: newest membership year first (2026–27 at the top). The year is the
// start of the membership period; a pending order has no dates yet, so its membershipYear
// ("2026-27" → 1 Nov 2026) is used, then paid / created dates as a last resort. Ties:
// higher transaction number first.
function npoTxnPeriodStart(txn) {
  const day = (value) => String(value || "").slice(0, 10);
  if (day(txn.membDateFrom)) return day(txn.membDateFrom);
  if (day(txn.cancelledMembDateFrom)) return day(txn.cancelledMembDateFrom);
  const year = String(txn.membershipYear || "").match(/^(\d{4})/);
  if (year) return `${year[1]}-11-01`;
  return day(txn.paidDate) || day(txn.createdAt);
}

function compareNpoTxnsNewestFirst(a, b) {
  const byPeriod = npoTxnPeriodStart(b).localeCompare(npoTxnPeriodStart(a));
  if (byPeriod !== 0) return byPeriod;
  return (Number(b.transactionNo) || 0) - (Number(a.transactionNo) || 0);
}

const NPO_PAID_VIA_OPTIONS = [
  { value: "bank", label: "Bank transfer" },
  { value: "paypay", label: "PayPay" },
  { value: "square", label: "Card (Square)" },
];

function normalizePaymentMethodKey(key) {
  const value = String(key || "").trim().toLowerCase();
  if (["credit", "card", "creditcard", "credit_card"].includes(value)) return "credit";
  if (["paypal", "paypay"].includes(value)) return "paypay";
  if (["bank", "banktransfer", "bank_transfer", "transfer"].includes(value)) return "bank";
  return value;
}

function extractPaymentLinkValue(rawValue) {
  if (typeof rawValue === "string") {
    return rawValue.trim();
  }
  if (rawValue && typeof rawValue === "object") {
    const keys = ["url", "link", "href", "paymentUrl", "paymentLink"];
    for (const key of keys) {
      if (typeof rawValue[key] === "string") {
        return rawValue[key].trim();
      }
    }
  }
  return "";
}

function getPaymentLinksFromEventInfo(eventInfo) {
  const source = eventInfo && typeof eventInfo === "object"
    ? (eventInfo.paymentLinks && typeof eventInfo.paymentLinks === "object"
      ? eventInfo.paymentLinks
      : eventInfo.payment && typeof eventInfo.payment === "object"
        ? eventInfo.payment
        : {})
    : {};

  const links = { credit: "", paypay: "", bank: "" };
  Object.entries(source).forEach(([rawKey, rawValue]) => {
    const method = normalizePaymentMethodKey(rawKey);
    const url = extractPaymentLinkValue(rawValue);
    if (method in links) {
      links[method] = url;
    }
  });
  return links;
}

// eventInfo.seatSelection — same convention lambdas/my_registrations reads
// server-side (get_seat_selection_config): {enabled, paymentMethods,
// startDate, endDate}. Defaults to all three payment methods offered.
// adminOverrideMaxSeats defaults to false, mirroring seat_selection_logic.
// parse_seat_selection_config's own default — off means the admin
// "Allocate Seats" flow caps at the per-registration seat allowance, same
// as self-service; on lifts that cap entirely.
function getSeatSelectionFromEventInfo(eventInfo) {
  const raw = eventInfo && typeof eventInfo === "object" ? eventInfo.seatSelection : null;
  const source = raw && typeof raw === "object" ? raw : {};
  const methods = Array.isArray(source.paymentMethods)
    ? source.paymentMethods.filter((m) => ["paypay", "square", "bank"].includes(m))
    : [];
  return {
    enabled: source.enabled === true,
    pricingEnabled: source.pricingEnabled !== false,
    adminOverrideMaxSeats: source.adminOverrideMaxSeats === true,
    startDate: typeof source.startDate === "string" ? source.startDate : "",
    endDate: typeof source.endDate === "string" ? source.endDate : "",
    // Optional closing line for the "seat selection is open" announcement
    // email (e.g. "We look forward to celebrating this milestone with
    // you.") -- omitted from the email entirely when blank, since a
    // generic line wouldn't fit every event.
    announcementClosingNote: typeof source.announcementClosingNote === "string" ? source.announcementClosingNote : "",
    // Defaults to true, mirroring seat_selection_logic.parse_seat_selection_
    // config's own default — off stops the admin "Seat purchase
    // requested/paid/cancelled/..." notification emails for this event
    // (registrant-facing emails are unaffected).
    adminNotificationsEnabled: source.adminNotificationsEnabled !== false,
    paymentMethods: methods.length ? methods : ["paypay", "square", "bank"],
  };
}

// The seat-upgrade plugin (plugins/seat-upgrade/) is opt-in per event: an
// event with no `eventInfo.seatUpgrade` block, or `enabled` not exactly
// `true`, has the feature off entirely — the "Seat Upgrades" admin tab and
// the registrant-facing upgrade flow both stay hidden. Once enabled, an
// event that has never had `paymentMethods` configured before defaults to
// offering all three, matching the plugin's original always-on behavior.
function getSeatUpgradeConfigFromEventInfo(eventInfo) {
  const source = eventInfo && typeof eventInfo === "object" && eventInfo.seatUpgrade && typeof eventInfo.seatUpgrade === "object"
    ? eventInfo.seatUpgrade
    : {};
  const hasStoredMethods = Array.isArray(source.paymentMethods);
  const validMethods = ["paypay", "square", "bank"];
  const paymentMethods = hasStoredMethods
    ? source.paymentMethods.filter((m) => validMethods.includes(m))
    : validMethods;
  return {
    enabled: source.enabled === true,
    url: typeof source.url === "string" ? source.url : "",
    seatMapImageUrl: typeof source.seatMapImageUrl === "string" ? source.seatMapImageUrl : "",
    startDate: typeof source.startDate === "string" ? source.startDate : "",
    endDate: typeof source.endDate === "string" ? source.endDate : "",
    paymentMethods: paymentMethods.length ? paymentMethods : validMethods,
  };
}

// The standalone Dinner with Celebrity add-on is opt-in per event, same shape as
// the seat-upgrade plugin above: an event with no `eventInfo.celebrityDinner`
// block, or `enabled` not exactly `true`, has the feature off entirely — the
// "Dinner with Celebrity" admin tab and the registrant-facing sign-up on My
// Registrations both stay hidden. The registration window, venue, price and
// conditions are managed separately in the Dinner with Celebrity tab itself
// (backed by its own admin API) — this flag only turns the feature on/off.
function getCelebrityDinnerConfigFromEventInfo(eventInfo) {
  const source = eventInfo && typeof eventInfo === "object" && eventInfo.celebrityDinner && typeof eventInfo.celebrityDinner === "object"
    ? eventInfo.celebrityDinner
    : {};
  return {
    enabled: source.enabled === true,
  };
}

// Onam attendee categories used by the admin registration editor. Mirrors the
// backend onam_pricing categories (kids under 6 are free; NPO discount is
// applied server-side, so no prices are computed in the client).
const ONAM_OPTIONS = [
  { value: "onam", label: "Onam Only" },
  { value: "event", label: "Celebrity Event Only" },
  { value: "combo", label: "Onam + Celebrity Event" },
];

const ONAM_CATEGORIES = [
  { key: "adults", label: "Adults" },
  { key: "kids914", label: "Kids (9–14)" },
  { key: "kids68", label: "Kids (6–8)" },
  { key: "kidsUnder6", label: "Kids (under 6)" },
];

const ONAM_TABLE_COLUMNS = [
  { key: "adults", label: "Adults" },
  { key: "kids914", label: "Kids 9–14" },
  { key: "kids68", label: "Kids 6–8" },
  { key: "kidsUnder6", label: "Under 6" },
];

const MEMBERSHIP_ACTION_OPTIONS = [
  { value: "none", label: "No change" },
  { value: "join", label: "Join NPO (+¥1000)" },
  { value: "renew", label: "Renew NPO (+¥1000)" },
];

// Attendees who occupy an entry-slot seat, mirroring the backend's seat math
// across both schemas. Under-6s / toddlers take no seat, so they stay editable
// after check-in — changing them can't put the record out of step with the pass.
const SEATED_COUNT_KEYS = new Set(["adults", "kids914", "kids68"]);

const STANDARD_CATEGORIES = [
  { key: "adults", label: "Adults" },
  { key: "kids914", label: "Kids (9–14)" },
  { key: "kids68", label: "Kids (6–8)" },
  { key: "kidsUnder6", label: "Kids (under 6)" },
];

// Admin-controlled payment lifecycle. Marking a record "paid" also triggers a
// payment-received email to the registrant (server-side).
const PAYMENT_STATUSES = [
  { key: "pending", label: "Payment Pending" },
  { key: "paid", label: "Paid" },
  { key: "refunded", label: "Refunded" },
];

const BULK_REGISTRATION_ACTIONS = [
  { key: "default", label: "default" },
  { key: "mark_paid", label: "Mark paid and send payment confirmation" },
  { key: "send_payment_pending", label: "Send payment-pending email" },
  { key: "send_payment_confirmation", label: "Resend payment confirmation" },
  { key: "send_sadhya_slot_reminder", label: "Send Sadhya slot-selection reminder (paid Onam/Combo, no slot yet)" },
  { key: "send_seat_selection_announcement", label: "Send seat selection announcement (paid Celebrity Event/Combo)" },
  { key: "send_seat_selection_reminder", label: "Send seat selection reminder (paid Celebrity Event/Combo, no seats yet)" },
  { key: "send_celebrity_dinner_announcement", label: "Send Celebrity Dinner invite (eligible guests, not yet booked)" },
];

// Base (non-member) unit prices and the adults-only NPO discount percentage,
// mirroring lambdas/manage_registrations/onam_pricing.py — used only to show
// a live "estimated total" preview while editing; the server recomputes the
// authoritative amount on save.
const ONAM_BASE_PRICES = {
  onam: { adult: 2500, kids914: 1500, kids68: 1000, kidsUnder6: 0 },
  event: { adult: 3000, kids914: 2000, kids68: 2000, kidsUnder6: 0 },
  combo: { adult: 4950, kids914: 3150, kids68: 2700, kidsUnder6: 0 },
};
const ONAM_DISCOUNT_PCT = {
  none: { onam: 0, event: 0, combo: 0 },
  member: { onam: 10, event: 0, combo: 10 },
  full: { onam: 10, event: 0, combo: 10 },
};
const ONAM_NPO_FEE = 1000;

// Canonical payment method keys, matching the public and self-service Onam
// forms (and what the backend's _normalize_payment_method resolves to).
const ONAM_PAYMENT_METHODS = [
  { key: "credit", label: "Credit Card" },
  { key: "paypay", label: "PayPay" },
  { key: "bank", label: "Bank Transfer" },
];

// Older registrations may carry a different spelling/casing of a known method
// (the pre-rename "paypal", or a label like "PayPay"/"Credit Card" typed into
// the old free-text field) — recognize those aliases case-insensitively so
// the matching pill still lights up. Mirrors the backend's
// _normalize_payment_method. Anything else (e.g. "cash") is returned as-is
// so its original wording still shows in the "Other" field.
function normalizeOnamPaymentMethod(value) {
  const raw = String(value || "");
  const v = raw.trim().toLowerCase();
  if (["credit", "card", "creditcard", "credit_card", "credit card"].includes(v)) return "credit";
  if (["paypal", "paypay"].includes(v)) return "paypay";
  if (["bank", "banktransfer", "bank_transfer", "bank transfer", "transfer"].includes(v)) return "bank";
  return raw;
}

const INITIAL_REGISTRATION_FORM = {
  registrationId: "",
  eventType: "",
  registrationType: "standard",
  name: "",
  email: "",
  phone: "",
  npoName: "",
  paymentMethod: "",
  paymentStatus: "pending",
  paidAmount: 0,
  totalAmount: 0,
  registrationOption: "combo",
  membershipAction: "none",
  // The registrant's (adult 1) NPO tier as of the last save — used only to
  // seed the live total preview; re-verified authoritatively on the server.
  pricingTier: "none",
  attendeeCounts: { adults: 0, kids914: 0, kids68: 0, kidsUnder6: 0 },
  // Emails of adults 2…N (the registrant is adult 1). Onam only.
  additionalAdultEmails: [],
  // Seated attendee counts are frozen once the party has been admitted.
  checkedIn: false,
};

const VISIT_RANGE_OPTIONS = [7, 14, 30, 60, 90];

const EMAIL_REGEX = /^[^@\s]+@[^@\s]+\.[^@\s]+$/;

// Outstanding balance for a registration: positive = registrant owes more,
// negative = a refund is due. Only meaningful once money has been recorded
// (paidAmount > 0) — an unpaid record has no "balance", just a total. Prefers
// the server-computed value, falling back to total − paid for older records.
function getBalanceDue(reg) {
  const paid = Number(reg.paidAmount) || 0;
  if (paid <= 0) {
    return 0;
  }
  if (reg.balanceDue != null) {
    return Number(reg.balanceDue) || 0;
  }
  return (Number(reg.totalAmount) || 0) - paid;
}

function getAmountToPay(reg) {
  const total = Number(reg.totalAmount);
  if (!Number.isFinite(total) || total <= 0) {
    return null;
  }
  const paid = Number(reg.paidAmount) || 0;
  return paid > 0 ? getBalanceDue(reg) : total;
}

// Entry slots seat the Onam Sadhya, so only the Sadhya-inclusive options
// (Onam-only and the combo) need one — the Celebrity-Event-only option
// skips the meal entirely. Mirrors the backend's bulk-action gating.
function registrationNeedsSadhyaSlot(reg) {
  return reg.registrationOption === "onam" || reg.registrationOption === "combo";
}

function registrationHasSadhyaSlot(reg) {
  return Boolean((Array.isArray(reg.allocations) && reg.allocations.length) || reg.slotId);
}

function maskEmailAddress(email) {
  const [localPart, domain] = String(email || "").split("@");
  if (!localPart || !domain) {
    return "your email";
  }

  if (localPart.length <= 2) {
    return `${localPart[0]}***@${domain}`;
  }

  return `${localPart.slice(0, 2)}***@${domain}`;
}

function getRegistrationTotalAttendees(registration) {
  const attendee_cnts = registration?.attendeeCounts || {};
  return Object.values(attendee_cnts).reduce((sum, count) => sum + (Number(count) || 0), 0);
}

function buildRegistrationReport(registrations) {
  const report = {
    registrations: registrations.length,
    people: 0,
    seatedPeople: 0,
    paid: 0,
    pending: 0,
    refunded: 0,
    onamOnlyRegistrations: 0,
    eventOnlyRegistrations: 0,
    onamAndEventRegistrations: 0,
    amountReceived: 0,
    amountToCollect: 0,
    totalAmount: 0,
    allocated: 0,
    checkedIn: 0,
    remaining: 0,
    rows: [],
  };

  registrations.forEach((registration) => {
    const counts = registration.attendeeCounts || {};
    const seatedPeople = registration.registrationType === "onam"
      ? ["adults", "kids914", "kids68", "kidsUnder6"].reduce((sum, key) => sum + (Number(counts[key]) || 0), 0)
      : ["adult", "teen", "child", "toddlers"].reduce((sum, key) => sum + (Number(counts[key]) || 0), 0);
    const allocations = Array.isArray(registration.allocations) ? registration.allocations : [];
    const allocated = allocations.reduce((sum, allocation) => sum + (Number(allocation.seatCount) || 0), 0);
    const checkedIn = allocations.reduce((sum, allocation) => sum + (Number(allocation.checkedInCount) || 0), 0);
    const status = String(registration.paymentStatus || "pending").toLowerCase();

    report.people += getRegistrationTotalAttendees(registration);
    report.seatedPeople += seatedPeople;
    report.allocated += allocated;
    report.checkedIn += checkedIn;
    report.remaining += Math.max(allocated - checkedIn, 0);
    if (status === "paid") report.paid += 1;
    else if (status === "refunded") report.refunded += 1;
    else report.pending += 1;
    if (registration.registrationOption === "onam") report.onamOnlyRegistrations += 1;
    else if (registration.registrationOption === "combo") report.onamAndEventRegistrations += 1;
    else report.eventOnlyRegistrations += 1;
    report.totalAmount += Number(registration.totalAmount) || 0;
    report.amountReceived += Number(registration.paidAmount) || 0;
    report.amountToCollect += Math.max(getAmountToPay(registration) || 0, 0);

    report.rows.push({
      id: registration.registrationId,
      name: registration.name || `${registration.firstName || ""} ${registration.lastName || ""}`.trim() || "Unnamed",
      email: registration.email || "—",
      payment: status,
      allocated,
      checkedIn,
      remaining: Math.max(allocated - checkedIn, 0),
      slots: allocations.map((allocation) => allocation.slotName || allocation.entrySlotId).filter(Boolean),
    });
  });

  return report;
}

// One row per Onam/Combo registrant showing their entry-slot allocation and
// check-in progress — the data the Sadhya Slot Allocations report is built
// from (used for both the on-screen table and its CSV export).
function buildSadhyaSlotAllocationReport(registrations) {
  return registrations
    .filter(registrationNeedsSadhyaSlot)
    .map((registration) => {
      const allocations = Array.isArray(registration.allocations) ? registration.allocations : [];
      const slots = allocations.map((allocation) => ({
        name: allocation.slotName || allocation.entrySlotId || "Unassigned",
        seats: Number(allocation.seatCount) || 0,
        checkedIn: Number(allocation.checkedInCount) || 0,
      }));
      const totalSeats = slots.reduce((sum, slot) => sum + slot.seats, 0);
      const checkedInSeats = allocations.reduce((sum, a) => sum + (Number(a.checkedInCount) || 0), 0);

      let checkInCategory = "not_checked_in";
      let checkInStatus = "Not checked in";
      if (totalSeats === 0) {
        checkInCategory = "no_slot";
        checkInStatus = "No slot selected";
      } else if (checkedInSeats >= totalSeats) {
        checkInCategory = "checked_in";
        checkInStatus = "Checked in";
      } else if (checkedInSeats > 0) {
        checkInCategory = "partial";
        checkInStatus = `Partially checked in (${checkedInSeats}/${totalSeats})`;
      }

      return {
        id: registration.registrationId,
        name: registration.name || `${registration.firstName || ""} ${registration.lastName || ""}`.trim() || "Unnamed",
        email: registration.email || "—",
        registrationOptionKey: registration.registrationOption || "",
        registrationOption: registration.registrationOptionLabel || registration.registrationOption || "—",
        seatsBooked: getRegistrationTotalAttendees(registration),
        slots,
        totalSeats,
        checkedInSeats,
        checkInCategory,
        checkInStatus,
      };
    });
}

const SADHYA_CHECKIN_FILTERS = [
  { value: "all", label: "All check-in statuses" },
  { value: "no_slot", label: "No slot selected" },
  { value: "not_checked_in", label: "Not checked in" },
  { value: "partial", label: "Partially checked in" },
  { value: "checked_in", label: "Checked in" },
];

function csvEscape(value) {
  const text = String(value ?? "");
  return /[",\n]/.test(text) ? `"${text.replace(/"/g, '""')}"` : text;
}

function ReportMetric({ label, value, tone = "" }) {
  return (
    <div className={`registration-report-metric ${tone}`}>
      <span>{label}</span>
      <strong>{value.toLocaleString()}</strong>
    </div>
  );
}

function RegistrationReport({ report, updatedAt, onRefresh, onExport, isLoading }) {
  return (
    <section className="registration-report" aria-label="Detailed registration report">
      <div className="registration-report-heading">
        <div>
          <h3>Registration Report</h3>
          <p className="field-note">
            Last updated: {updatedAt ? updatedAt.toLocaleString() : "Not loaded"}
          </p>
        </div>
        <div className="registration-report-actions">
          <button type="button" className="button secondary small" onClick={onRefresh} disabled={isLoading}>
            {isLoading ? "Refreshing..." : "Refresh"}
          </button>
        </div>
      </div>
      <div className="registration-report-metrics">
        <ReportMetric label="Onam only registrations" value={report.onamOnlyRegistrations} />
        <ReportMetric label="Event only registrations" value={report.eventOnlyRegistrations} />
        <ReportMetric label="Onam & Event registrations" value={report.onamAndEventRegistrations} />
        <ReportMetric label="Amount received" value={`¥${report.amountReceived.toLocaleString()}`} />
        <ReportMetric label="To be collected" value={`¥${report.amountToCollect.toLocaleString()}`} tone="warning" />
        <ReportMetric label="Total amount" value={`¥${report.totalAmount.toLocaleString()}`} tone="positive" />
        <ReportMetric label="Total attendees" value={report.people} />
        <ReportMetric label="Checked in" value={report.checkedIn} tone="positive" />
        <ReportMetric label="Remaining" value={report.remaining} tone="warning" />
      </div>

      <div className="registration-report-status-line">
        <span>Payment status</span>
        <strong>{report.paid} paid</strong>
        <strong>{report.pending} pending</strong>
        <strong>{report.refunded} refunded</strong>
        <span>{report.registrations.toLocaleString()} total records</span>
      </div>
    </section>
  );
}

const SADHYA_OPTION_FILTERS = [{ value: "all", label: "All options" }, ...ONAM_OPTIONS.filter((o) => o.value !== "event")];

function SadhyaSlotAllocationReport({ rows, onCheckIn }) {
  const [optionFilter, setOptionFilter] = useState("all");
  const [checkInFilter, setCheckInFilter] = useState("all");
  const [checkingInId, setCheckingInId] = useState("");
  const [checkInError, setCheckInError] = useState("");

  const handleCheckIn = async (row) => {
    if (checkingInId) return;
    setCheckInError("");
    setCheckingInId(row.id);
    try {
      await onCheckIn(row.id);
    } catch (err) {
      setCheckInError(err?.message || `Could not check in ${row.name}.`);
    } finally {
      setCheckingInId("");
    }
  };

  const filteredRows = rows.filter(
    (row) =>
      (optionFilter === "all" || row.registrationOptionKey === optionFilter) &&
      (checkInFilter === "all" || row.checkInCategory === checkInFilter)
  );

  const totalSeatsBooked = filteredRows.reduce((sum, row) => sum + row.seatsBooked, 0);
  const totalSeatsAllocated = filteredRows.reduce((sum, row) => sum + row.totalSeats, 0);

  const slotSummary = new Map();
  filteredRows.forEach((row) => {
    row.slots.forEach((slot) => {
      const current = slotSummary.get(slot.name) || { name: slot.name, allocated: 0, checkedIn: 0 };
      current.allocated += slot.seats;
      current.checkedIn += slot.checkedIn;
      slotSummary.set(slot.name, current);
    });
  });
  const slotSummaryRows = [...slotSummary.values()];

  const handleExport = () => {
    const header = ["Name", "Email", "Option", "Seats Booked", "Allocated Slots & Seats", "Seats Allocated", "Check-in Status"];
    const lines = [header, ...filteredRows.map((row) => [
      row.name,
      row.email,
      row.registrationOption,
      row.seatsBooked,
      row.slots.map((slot) => `${slot.name} (${slot.seats} seat${slot.seats === 1 ? "" : "s"})`).join("; "),
      row.totalSeats,
      row.checkInStatus,
    ])].map((line) => line.map(csvEscape).join(","));
    const timestamp = new Date().toISOString().split("T")[0];
    downloadCSV(new Blob([lines.join("\n")], { type: "text/csv" }), `sadhya-slot-allocations-${timestamp}.csv`);
  };

  return (
    <section className="registration-report" aria-label="Sadhya slot allocations by registrant">
      <div className="registration-report-heading">
        <div>
          <h3>Sadhya Slot Allocations</h3>
          <p className="field-note">
            Onam &amp; Combo registrants — {filteredRows.length} of {rows.length} registrant{rows.length === 1 ? "" : "s"}.
          </p>
        </div>
        <div className="registration-report-actions">
          <button type="button" className="button secondary small" onClick={handleExport} disabled={!filteredRows.length}>
            📥 Export as CSV
          </button>
        </div>
      </div>

      <div className="registration-report-metrics">
        <ReportMetric label="Total Seats Booked" value={totalSeatsBooked} />
        <ReportMetric label="Total Seats Allocated" value={totalSeatsAllocated} />
      </div>

      <div className="registration-report-columns">
        <div>
          <h4>By entry slot</h4>
          {slotSummaryRows.length ? (
            <div className="registration-report-slot-list">
              {slotSummaryRows.map((slot) => (
                <div className="registration-report-slot" key={slot.name}>
                  <strong>{slot.name}</strong>
                  <span>{slot.allocated} allocated</span>
                  <span>{slot.checkedIn} checked in</span>
                  <span>{Math.max(slot.allocated - slot.checkedIn, 0)} remaining</span>
                </div>
              ))}
            </div>
          ) : <p className="field-note">No slot allocations match the selected filters.</p>}
        </div>
      </div>

      <div className="admin-controls" aria-label="Sadhya slot allocation filters">
        <select
          value={optionFilter}
          onChange={(e) => setOptionFilter(e.target.value)}
          className="search-input"
          aria-label="Filter by registration option"
        >
          {SADHYA_OPTION_FILTERS.map((option) => (
            <option key={option.value} value={option.value}>{option.label}</option>
          ))}
        </select>
        <select
          value={checkInFilter}
          onChange={(e) => setCheckInFilter(e.target.value)}
          className="search-input"
          aria-label="Filter by check-in status"
        >
          {SADHYA_CHECKIN_FILTERS.map((option) => (
            <option key={option.value} value={option.value}>{option.label}</option>
          ))}
        </select>
      </div>

      {checkInError ? <div className="error-banner">{checkInError}</div> : null}

      {filteredRows.length ? (
        <div className="table-container">
          <table className="registrations-table">
            <thead>
              <tr>
                <th>Name</th>
                <th>Email</th>
                <th>Option</th>
                <th>Seats Booked</th>
                <th>Allocated Slots &amp; Seats</th>
                <th>Seats Allocated</th>
                <th>Check-in Status</th>
                <th>Actions</th>
              </tr>
            </thead>
            <tbody>
              {filteredRows.map((row) => {
                const canCheckIn = onCheckIn && row.checkInCategory !== "checked_in" && row.checkInCategory !== "no_slot";
                return (
                  <tr key={row.id}>
                    <td>{row.name}</td>
                    <td>{row.email}</td>
                    <td>{row.registrationOption}</td>
                    <td>{row.seatsBooked}</td>
                    <td>
                      {row.slots.length
                        ? row.slots.map((slot) => `${slot.name} (${slot.seats} seat${slot.seats === 1 ? "" : "s"})`).join(", ")
                        : "—"}
                    </td>
                    <td>{row.totalSeats}</td>
                    <td>{row.checkInStatus}</td>
                    <td>
                      {canCheckIn ? (
                        <button
                          type="button"
                          className="button secondary small"
                          onClick={() => handleCheckIn(row)}
                          disabled={Boolean(checkingInId)}
                        >
                          {checkingInId === row.id ? "Checking in..." : "Check in"}
                        </button>
                      ) : null}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      ) : <p className="field-note">No registrants match the selected filters.</p>}
    </section>
  );
}

// A consistent header for each opt-in plugin's block on the event form
// (Seat Selection / Zone Upgrade / Dinner with Celebrity today) — same icon +
// name + enabled/disabled pill treatment for all three, so admins can tell
// at a glance which plugins are on for this event without reading every
// field. Purely presentational; the actual enable/disable control still
// lives inside each block, right below this header.
function PluginSectionHeader({ icon, title, enabled }) {
  return (
    <div className="plugin-section-header">
      <span className="plugin-section-title">
        <span aria-hidden="true">{icon}</span> {title}
      </span>
      <span className={`plugin-status-pill${enabled ? " on" : ""}`}>{enabled ? "Enabled" : "Disabled"}</span>
    </div>
  );
}

// Booking window, pricing, and payment methods for the Celebrity Event Seat
// Selection plugin — split out of the Events form into its own tab so
// configuring one plugin's details doesn't mean wading through the giant
// per-event form to find them (the Events tab keeps only the Enable/Disable
// toggle, same as Dinner with Celebrity's). Saves by round-tripping the event's
// full record through saveEvent() — eventInfo.seatSelection is the only
// part of it this panel ever touches, and it preserves `enabled` exactly as
// the Events tab left it, since that toggle isn't editable here.
function SeatSelectionConfigPanel({ events, initialEventType, onSaved }) {
  const selectableEvents = useMemo(
    () =>
      events
        .filter((event) => event.eventType)
        .sort((a, b) => String(a.eventName || a.eventType).localeCompare(String(b.eventName || b.eventType))),
    [events]
  );
  const fallbackEventType = selectableEvents[0]?.eventType || "";
  const [eventType, setEventType] = useState(initialEventType || fallbackEventType);
  useEffect(() => {
    if (!eventType && fallbackEventType) setEventType(fallbackEventType);
  }, [eventType, fallbackEventType]);

  const selectedEvent = selectableEvents.find((event) => event.eventType === eventType) || null;
  const config = useMemo(() => getSeatSelectionFromEventInfo(selectedEvent?.eventInfo), [selectedEvent]);

  const [form, setForm] = useState(config);
  useEffect(() => {
    setForm(config);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [eventType]);

  const [isSaving, setIsSaving] = useState(false);
  const [error, setError] = useState("");
  const [success, setSuccess] = useState("");

  const handleSave = async (e) => {
    e.preventDefault();
    setSuccess("");
    if (!selectedEvent) {
      setError("Select an event first.");
      return;
    }
    if (form.pricingEnabled && form.paymentMethods.length === 0) {
      setError("Select at least one payment method, or turn pricing off.");
      return;
    }
    setError("");
    setIsSaving(true);
    try {
      const existingEventInfo = selectedEvent.eventInfo && typeof selectedEvent.eventInfo === "object"
        ? selectedEvent.eventInfo
        : {};
      await saveEvent({
        ...selectedEvent,
        eventInfo: {
          ...existingEventInfo,
          seatSelection: {
            enabled: config.enabled,
            pricingEnabled: form.pricingEnabled,
            adminOverrideMaxSeats: form.adminOverrideMaxSeats,
            paymentMethods: form.paymentMethods,
            startDate: form.startDate,
            endDate: form.endDate,
            announcementClosingNote: form.announcementClosingNote,
            adminNotificationsEnabled: form.adminNotificationsEnabled,
          },
        },
      });
      setSuccess("Seat selection configuration saved.");
      onSaved?.();
    } catch (err) {
      setError(err.message || "Failed to save seat selection configuration.");
    } finally {
      setIsSaving(false);
    }
  };

  return (
    <section className="partner-panel partner-form-panel">
      <div className="panel-heading">
        <div>
          <h3>Seat Selection Configuration</h3>
          <p>Booking window, pricing, and payment methods for the Celebrity Event seat picker.</p>
        </div>
      </div>

      <div className="field">
        <label htmlFor="seatSelectionConfigEvent">Event</label>
        <select id="seatSelectionConfigEvent" value={eventType} onChange={(e) => setEventType(e.target.value)}>
          <option value="">Select event...</option>
          {selectableEvents.map((event) => (
            <option key={event.eventType} value={event.eventType}>
              {event.eventName || event.eventType}
            </option>
          ))}
        </select>
      </div>

      {!eventType ? (
        <p className="field-note">Select an event above to configure its seat selection settings.</p>
      ) : !config.enabled ? (
        <p className="field-note">
          Seat selection is disabled for this event. Enable it from the Events tab, then come back here to
          configure its booking window, pricing, and payment methods.
        </p>
      ) : (
        <form onSubmit={handleSave} className="field full-width-field">
          <div className="field-row">
            <div className="field">
              <label htmlFor="seatSelectionConfigStartDate">Booking Opens</label>
              <input
                id="seatSelectionConfigStartDate"
                type="date"
                value={form.startDate}
                onChange={(e) => setForm((prev) => ({ ...prev, startDate: e.target.value }))}
                disabled={isSaving}
              />
            </div>
            <div className="field">
              <label htmlFor="seatSelectionConfigEndDate">Booking Closes</label>
              <input
                id="seatSelectionConfigEndDate"
                type="date"
                value={form.endDate}
                onChange={(e) => setForm((prev) => ({ ...prev, endDate: e.target.value }))}
                disabled={isSaving}
              />
            </div>
          </div>

          <label>
            <input
              type="checkbox"
              checked={form.pricingEnabled}
              onChange={(e) => setForm((prev) => ({ ...prev, pricingEnabled: e.target.checked }))}
              disabled={isSaving}
            />{" "}
            Charge for seat selection (zone pricing)
          </label>

          <label>
            <input
              type="checkbox"
              checked={form.adminOverrideMaxSeats}
              onChange={(e) => setForm((prev) => ({ ...prev, adminOverrideMaxSeats: e.target.checked }))}
              disabled={isSaving}
            />{" "}
            Allow admins to exceed the per-registration seat limit
          </label>
          <span className="field-note">
            When off (default), an admin using "Allocate Seats" on the Registrations tab is capped
            at one seat per eligible attendee (adults + kids 9-14 + kids 6-8), same as
            self-service. Turn this on to let an admin allocate any number of seats to a
            registration for this event.
          </span>

          <label>
            <input
              type="checkbox"
              checked={form.adminNotificationsEnabled}
              onChange={(e) => setForm((prev) => ({ ...prev, adminNotificationsEnabled: e.target.checked }))}
              disabled={isSaving}
            />{" "}
            Send admin notification emails for seat purchases
          </label>
          <span className="field-note">
            On (default), the admin inbox gets a "Seat purchase requested/paid/cancelled/..."
            email for every seat purchase event on this event. Turn this off to stop those —
            registrants' own confirmation emails are unaffected either way.
          </span>

          <div className="field">
            <label htmlFor="seatSelectionAnnouncementClosingNote">
              Announcement email closing note (optional)
            </label>
            <input
              id="seatSelectionAnnouncementClosingNote"
              type="text"
              value={form.announcementClosingNote}
              onChange={(e) => setForm((prev) => ({ ...prev, announcementClosingNote: e.target.value }))}
              placeholder="e.g. We look forward to celebrating this milestone with you."
              disabled={isSaving}
            />
          </div>
          <span className="field-note">
            Shown as a short line below the button in the "seat selection is open" announcement
            email (both the "Announce Seat Selection" broadcast and the Registrations tab's
            selective bulk action). Left blank, the email simply omits it.
          </span>

          {form.pricingEnabled ? (
            <>
              <label>Payment methods offered</label>
              <div className="btn-row">
                {Object.entries(SEAT_SELECTION_PAYMENT_METHOD_LABELS).map(([method, label]) => (
                  <label key={method} className="checkbox-inline">
                    <input
                      type="checkbox"
                      checked={form.paymentMethods.includes(method)}
                      onChange={(e) =>
                        setForm((prev) => ({
                          ...prev,
                          paymentMethods: e.target.checked
                            ? [...prev.paymentMethods, method]
                            : prev.paymentMethods.filter((m) => m !== method),
                        }))
                      }
                      disabled={isSaving}
                    />{" "}
                    {label}
                  </label>
                ))}
              </div>
              <span className="field-note">
                Leave both dates blank to accept purchases indefinitely once enabled. Configure zone prices and
                send the announcement email from the Seat Map sub-tab.
              </span>
            </>
          ) : (
            <span className="field-note">
              Pricing is off — seat selection is free. Each registrant may only pick Regular seats, plus any zone
              they've separately paid for via a Zone Upgrade tier whose "Seat Map Zone" is set to that zone (Zone
              Upgrades tab). Leave both dates blank to accept free selections indefinitely once enabled.
            </span>
          )}

          {error ? <div className="error-banner">{error}</div> : null}
          {success ? <div className="success-banner">{success}</div> : null}

          <div className="change-password-actions partner-actions-row">
            <button type="submit" className="button primary" disabled={isSaving}>
              {isSaving ? "Saving..." : "Save Configuration"}
            </button>
          </div>
        </form>
      )}
    </section>
  );
}

// Wraps the three seat-selection-plugin admin surfaces (this host's own
// Configuration panel above, plus the plugin's own SeatMapManagement/
// SeatPurchaseManagement) behind one "Seat Selection" tab with its own
// sub-navigation — same nested admin-tabs convention SeatUpgradeManagement
// already uses for its Seating Zones/Purchase Requests/Email Templates.
function SeatSelectionTab({
  events,
  initialEventType,
  onEventsChanged,
  getEventSeats,
  blockEventSeat,
  unblockEventSeat,
  saveZonePrice,
  deleteZonePrice,
  sendSeatSelectionAnnouncement,
  getEventSeatPurchases,
  updateSeatPurchaseStatus,
}) {
  const [subTab, setSubTab] = useState("configuration"); // "configuration" | "seatMap" | "seatPurchases"

  return (
    <>
      <div className="admin-tabs">
        <button
          type="button"
          className={`admin-tab ${subTab === "configuration" ? "active" : ""}`}
          onClick={() => setSubTab("configuration")}
        >
          Configuration
        </button>
        <button
          type="button"
          className={`admin-tab ${subTab === "seatMap" ? "active" : ""}`}
          onClick={() => setSubTab("seatMap")}
        >
          Seat Map
        </button>
        <button
          type="button"
          className={`admin-tab ${subTab === "seatPurchases" ? "active" : ""}`}
          onClick={() => setSubTab("seatPurchases")}
        >
          Seat Purchases
        </button>
      </div>

      {subTab === "configuration" ? (
        <SeatSelectionConfigPanel events={events} initialEventType={initialEventType} onSaved={onEventsChanged} />
      ) : subTab === "seatMap" ? (
        <SeatMapManagement
          events={events}
          initialEventType={initialEventType}
          getEventSeats={getEventSeats}
          blockEventSeat={blockEventSeat}
          unblockEventSeat={unblockEventSeat}
          saveZonePrice={saveZonePrice}
          deleteZonePrice={deleteZonePrice}
          sendSeatSelectionAnnouncement={sendSeatSelectionAnnouncement}
        />
      ) : (
        <SeatPurchaseManagement
          events={events}
          initialEventType={initialEventType}
          getEventSeatPurchases={getEventSeatPurchases}
          updateSeatPurchaseStatus={updateSeatPurchaseStatus}
        />
      )}
    </>
  );
}

export default function AdminDashboard({ onLogout, embedded = false, onAuthenticated }) {
  // Login form state
  const [loginEmail, setLoginEmail] = useState("");
  const [loginPassword, setLoginPassword] = useState("");
  const [newPassword, setNewPassword] = useState("");
  const [confirmNewPassword, setConfirmNewPassword] = useState("");
  const [challengeUser, setChallengeUser] = useState(null);
  const [requiresNewPassword, setRequiresNewPassword] = useState(false);
  const [isSettingPassword, setIsSettingPassword] = useState(false);
  const [forgotPasswordMode, setForgotPasswordMode] = useState(false);
  const [resetCodeSent, setResetCodeSent] = useState(false);
  const [resetCode, setResetCode] = useState("");
  const [forgotNewPassword, setForgotNewPassword] = useState("");
  const [forgotConfirmPassword, setForgotConfirmPassword] = useState("");
  const [isRequestingResetCode, setIsRequestingResetCode] = useState(false);
  const [isResettingPassword, setIsResettingPassword] = useState(false);
  const [resendCooldown, setResendCooldown] = useState(0);
  const [isLoggingIn, setIsLoggingIn] = useState(false);
  const [loginError, setLoginError] = useState("");
  const [loginSuccess, setLoginSuccess] = useState("");

  // Dashboard state
  const [isLoggedIn, setIsLoggedIn] = useState(!!getAdminEmail());
  const [registrations, setRegistrations] = useState([]);
  const [reportUpdatedAt, setReportUpdatedAt] = useState(null);
  const [partners, setPartners] = useState([]);
  const [isLoading, setIsLoading] = useState(false);
  const [isLoadingPartners, setIsLoadingPartners] = useState(false);
  const [error, setError] = useState("");
  const [partnerError, setPartnerError] = useState("");
  const [partnerSuccess, setPartnerSuccess] = useState("");
  const [activeSection, setActiveSection] = useState("registrations");

  // ── Carousel state ────────────────────────────────────────────────────────
  const [carouselConfig, setCarouselConfig] = useState({});
  const [carouselUpload, setCarouselUpload] = useState({
    slot: null,       // "onam" | "anniversary"
    file: null,
    preview: null,    // object URL for local preview
    uploading: false,
    error: null,
    success: null,
  });

  // ── Partners display mode ─────────────────────────────────────────────────
  const [partnersDisplayMode, setPartnersDisplayMode] = useState("flat");
  const [savingDisplayMode, setSavingDisplayMode] = useState(false);
  const [showChangePassword, setShowChangePassword] = useState(false);
  const [currentPassword, setCurrentPassword] = useState("");
  const [dashboardNewPassword, setDashboardNewPassword] = useState("");
  const [dashboardConfirmPassword, setDashboardConfirmPassword] = useState("");
  const [changePasswordError, setChangePasswordError] = useState("");
  const [changePasswordSuccess, setChangePasswordSuccess] = useState("");
  const [isChangingPassword, setIsChangingPassword] = useState(false);
  const [sortConfig, setSortConfig] = useState({ key: "submittedAt", direction: "desc" });
  const [searchTerm, setSearchTerm] = useState("");
  const [eventDateFilter, setEventDateFilter] = useState("all");
  const [paymentStatusFilter, setPaymentStatusFilter] = useState("all");
  const [registrationOptionFilter, setRegistrationOptionFilter] = useState("all");
  const [sadhyaSlotFilter, setSadhyaSlotFilter] = useState("all");
  const [partnerForm, setPartnerForm] = useState(INITIAL_PARTNER_FORM);
  const [isSavingPartner, setIsSavingPartner] = useState(false);
  const [editingPartnerId, setEditingPartnerId] = useState("");

  // Events management
  const [events, setEvents] = useState([]);
  const seatUpgradeEvents = useMemo(
    () => events.filter((evt) => getSeatUpgradeConfigFromEventInfo(evt.eventInfo).enabled),
    [events]
  );
  const celebrityDinnerEvents = useMemo(
    () => events.filter((evt) => getCelebrityDinnerConfigFromEventInfo(evt.eventInfo).enabled),
    [events]
  );
  // The Seat Upgrades / Dinner with Celebrity tabs only exist while at least one
  // event has the feature enabled — if a tab disappears out from under an
  // admin who's on it (the last enabled event got disabled, or events just
  // finished loading), fall back to a tab that's always there.
  useEffect(() => {
    if (activeSection === "seatUpgrades" && seatUpgradeEvents.length === 0) {
      setActiveSection("registrations");
    }
    if (activeSection === "celebrityDinner" && celebrityDinnerEvents.length === 0) {
      setActiveSection("registrations");
    }
  }, [activeSection, seatUpgradeEvents, celebrityDinnerEvents]);
  const [isLoadingEvents, setIsLoadingEvents] = useState(false);
  const [eventError, setEventError] = useState("");
  const [eventSuccess, setEventSuccess] = useState("");
  const [eventForm, setEventForm] = useState(INITIAL_EVENT_FORM);
  const [editingEventType, setEditingEventType] = useState("");
  const [isSavingEvent, setIsSavingEvent] = useState(false);
  const [seatMapUpload, setSeatMapUpload] = useState({ uploading: false, error: "" });

  // Members (master list + NPO status)
  const [members, setMembers] = useState([]);
  const [isLoadingMembers, setIsLoadingMembers] = useState(false);
  const [memberError, setMemberError] = useState("");
  const [memberSearch, setMemberSearch] = useState("");
  const [memberNpoFilter, setMemberNpoFilter] = useState("all");

  // Member add/edit modal
  const [memberForm, setMemberForm] = useState(INITIAL_MEMBER_FORM);
  const [showMemberForm, setShowMemberForm] = useState(false);
  const [editingMemberEmail, setEditingMemberEmail] = useState(null);
  const [isSavingMember, setIsSavingMember] = useState(false);
  const [memberFormError, setMemberFormError] = useState("");
  const [memberSuccess, setMemberSuccess] = useState("");

  // NPO membership management panel
  const [npoPanel, setNpoPanel] = useState(null); // { email, memberid, name, transactions }
  const [isLoadingNpoTxns, setIsLoadingNpoTxns] = useState(false);
  const [npoTxnForm, setNpoTxnForm] = useState(INITIAL_NPO_TXN_FORM);
  const [showNpoTxnForm, setShowNpoTxnForm] = useState(false);
  const [editingTxnNo, setEditingTxnNo] = useState(null);
  const [isSavingNpoTxn, setIsSavingNpoTxn] = useState(false);
  const [npoTxnFormError, setNpoTxnFormError] = useState("");
  const [npoNotify, setNpoNotify] = useState(null); // null → ticked only when the period is current
  const [npoTxnNote, setNpoTxnNote] = useState("");
  const [cancelTxn, setCancelTxn] = useState(null); // the paid transaction being cancelled
  const [cancelForm, setCancelForm] = useState({ refund: "none", refundAmount: "", reason: "", notify: true });
  const [isCancellingTxn, setIsCancellingTxn] = useState(false);
  const [cancelError, setCancelError] = useState("");

  // Visit analytics
  const [visitDays, setVisitDays] = useState(30);
  const [visitSummary, setVisitSummary] = useState({
    days: 30,
    range: { from: "", to: "" },
    totalVisits: 0,
    uniqueVisitors: 0,
    daily: [],
    byPath: [],
    byVisitorType: [],
  });
  const [isLoadingVisits, setIsLoadingVisits] = useState(false);
  const [visitError, setVisitError] = useState("");

  // Registration editing (per-event management)
  const [selectedEventType, setSelectedEventType] = useState("all");
  const [registrationForm, setRegistrationForm] = useState(INITIAL_REGISTRATION_FORM);
  const [showRegistrationForm, setShowRegistrationForm] = useState(false);
  const [isSavingRegistration, setIsSavingRegistration] = useState(false);
  const [registrationFormError, setRegistrationFormError] = useState("");
  const [selectedRegistrationIds, setSelectedRegistrationIds] = useState(() => new Set());
  const [bulkRegistrationAction, setBulkRegistrationAction] = useState("default");
  const [isProcessingBulkAction, setIsProcessingBulkAction] = useState(false);
  const [bulkActionMessage, setBulkActionMessage] = useState("");

  // Slot reassignment (change a registration's entry slot, any payment status)
  const [slotReassignTarget, setSlotReassignTarget] = useState(null);
  const [slotReassignOptions, setSlotReassignOptions] = useState([]);
  const [slotReassignAllocations, setSlotReassignAllocations] = useState([]);
  const [slotReassignLoading, setSlotReassignLoading] = useState(false);
  const [slotReassignSaving, setSlotReassignSaving] = useState(false);
  const [slotReassignError, setSlotReassignError] = useState("");

  // Admin seat allocation (assign specific Celebrity Event hall seats to an
  // event/combo registration directly, bypassing self-service payment/window
  // checks — see SEAT_SELECTION.md's Known Limitations for why this exists)
  const [seatAllocationTarget, setSeatAllocationTarget] = useState(null);
  const [seatAllocationMap, setSeatAllocationMap] = useState(null);
  const [seatAllocationInfo, setSeatAllocationInfo] = useState(null);
  const [seatAllocationSelected, setSeatAllocationSelected] = useState([]);
  const [seatAllocationLoading, setSeatAllocationLoading] = useState(false);
  const [seatAllocationSaving, setSeatAllocationSaving] = useState(false);
  const [seatAllocationError, setSeatAllocationError] = useState("");

  // Payment reconciliation (admin uploads CSV payment statements per event,
  // compares them against that event's registrations).
  const [reconEventType, setReconEventType] = useState("");
  const [reconSourceType, setReconSourceType] = useState("bank_transfer");
  const [reconCsvText, setReconCsvText] = useState("");
  const [reconFileName, setReconFileName] = useState("");
  const [reconPreview, setReconPreview] = useState(null);
  const [reconMapping, setReconMapping] = useState({ date: "", amount: "", description: "", payerName: "", payerEmail: "", reference: "" });
  const [isPreviewingRecon, setIsPreviewingRecon] = useState(false);
  const [isImportingRecon, setIsImportingRecon] = useState(false);
  const [reconImportResult, setReconImportResult] = useState(null);
  const [reconError, setReconError] = useState("");
  const [reconBatches, setReconBatches] = useState([]);
  const [reconTransactionCount, setReconTransactionCount] = useState(0);
  const [isLoadingReconTransactions, setIsLoadingReconTransactions] = useState(false);
  const [isRunningRecon, setIsRunningRecon] = useState(false);
  const [reconReport, setReconReport] = useState(null);
  const [reconRuns, setReconRuns] = useState([]);
  const [isLoadingReconRuns, setIsLoadingReconRuns] = useState(false);
  const [reconResultView, setReconResultView] = useState("matched");

  const RECONCILIATION_SOURCE_TYPES = [
    { value: "bank_transfer", label: "Bank Transfer" },
    { value: "credit_card", label: "Credit Card" },
    { value: "paypay", label: "PayPay" },
  ];

  const loadReconTransactions = async (eventType) => {
    if (!eventType) return;
    setIsLoadingReconTransactions(true);
    try {
      const { transactions, batches } = await getReconciliationTransactions(eventType);
      setReconBatches(batches);
      setReconTransactionCount(transactions.length);
    } catch (err) {
      setReconError(err.message || "Failed to load uploaded transactions");
    } finally {
      setIsLoadingReconTransactions(false);
    }
  };

  const loadReconRuns = async (eventType) => {
    if (!eventType) return;
    setIsLoadingReconRuns(true);
    try {
      setReconRuns(await getReconciliationRuns(eventType));
    } catch (err) {
      setReconError(err.message || "Failed to load reconciliation history");
    } finally {
      setIsLoadingReconRuns(false);
    }
  };

  const resetReconUpload = () => {
    setReconCsvText("");
    setReconFileName("");
    setReconPreview(null);
    setReconMapping({ date: "", amount: "", description: "", payerName: "", payerEmail: "", reference: "" });
  };

  const handleReconFileSelect = (file) => {
    if (!file) return;
    setReconError("");
    setReconImportResult(null);
    setReconFileName(file.name);
    const reader = new FileReader();
    reader.onload = async () => {
      const text = String(reader.result || "");
      setReconCsvText(text);
      setIsPreviewingRecon(true);
      try {
        const data = await previewReconciliationTransactions(text);
        setReconPreview(data);
        setReconMapping((prev) => ({ ...prev, ...(data.suggestedMapping || {}) }));
      } catch (err) {
        setReconError(err.message || "Failed to parse CSV");
      } finally {
        setIsPreviewingRecon(false);
      }
    };
    reader.onerror = () => setReconError("Failed to read the file");
    reader.readAsText(file);
  };

  const handleImportReconTransactions = async () => {
    if (!reconEventType) {
      setReconError("Select an event first");
      return;
    }
    if (!reconMapping.amount) {
      setReconError("Map an Amount column before importing");
      return;
    }
    setIsImportingRecon(true);
    setReconError("");
    try {
      const result = await importReconciliationTransactions({
        eventType: reconEventType,
        sourceType: reconSourceType,
        csvText: reconCsvText,
        columnMapping: reconMapping,
      });
      setReconImportResult(result);
      resetReconUpload();
      loadReconTransactions(reconEventType);
    } catch (err) {
      setReconError(err.message || "Failed to import transactions");
    } finally {
      setIsImportingRecon(false);
    }
  };

  const handleDeleteReconBatch = async (batchId) => {
    if (!window.confirm("Delete this uploaded batch? This cannot be undone.")) return;
    try {
      await deleteReconciliationBatch(batchId, reconEventType);
      loadReconTransactions(reconEventType);
    } catch (err) {
      setReconError(err.message || "Failed to delete batch");
    }
  };

  const handleRunReconciliation = async () => {
    if (!reconEventType) {
      setReconError("Select an event first");
      return;
    }
    setIsRunningRecon(true);
    setReconError("");
    try {
      const report = await runReconciliation(reconEventType);
      setReconReport(report);
      setReconResultView("matched");
      loadReconRuns(reconEventType);
    } catch (err) {
      setReconError(err.message || "Failed to run reconciliation");
    } finally {
      setIsRunningRecon(false);
    }
  };

  const handleViewReconRun = async (runId) => {
    try {
      setReconReport(await getReconciliationRun(reconEventType, runId));
      setReconResultView("matched");
    } catch (err) {
      setReconError(err.message || "Failed to load run detail");
    }
  };

  const handleExportReconRun = async (runId) => {
    try {
      const blob = await exportReconciliationRunCSV(reconEventType, runId);
      downloadCSV(blob, `reconciliation_${reconEventType}_${runId}.csv`);
    } catch (err) {
      setReconError(err.message || "Failed to export report");
    }
  };

  // Lazy-load reconciliation data for the selected event the first time the
  // Reconciliation tab is opened, and whenever the event selector changes.
  useEffect(() => {
    if (isLoggedIn && activeSection === "reconciliation" && reconEventType) {
      loadReconTransactions(reconEventType);
      loadReconRuns(reconEventType);
      setReconReport(null);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [isLoggedIn, activeSection, reconEventType]);

  const formatEventDate = (value) => {
    if (!value) {
      return "Unscheduled";
    }

    const parsed = new Date(value);
    if (Number.isNaN(parsed.getTime())) {
      return value;
    }

    return parsed.toLocaleDateString(undefined, {
      year: "numeric",
      month: "short",
      day: "numeric",
    });
  };

  const getSortValue = (registration, key) => {
    if (key === "eventDate" || key === "submittedAt") {
      const rawValue = registration[key];
      if (!rawValue) {
        return key === "eventDate" ? "9999-12-31" : "";
      }
      const parsed = new Date(rawValue);
      return Number.isNaN(parsed.getTime()) ? String(rawValue) : parsed.toISOString();
    }

    return String(registration[key] || "").toLowerCase();
  };

  const isLoggedInRef = useRef(isLoggedIn);
  isLoggedInRef.current = isLoggedIn;

  // Drop back to the login form when the session ends outside this component:
  // the API rejected the token (api.js clears it on 401), the refresh token
  // expired, or the admin signed out in another tab.
  useEffect(
    () =>
      onAdminSessionChange(() => {
        if (hasValidAdminSession() || !isLoggedInRef.current) return;
        setIsLoggedIn(false);
        setLoginError("Your admin session has expired. Please sign in again.");
        setRegistrations([]);
        setPartners([]);
      }),
    []
  );

  // Load registrations when logged in
  useEffect(() => {
    if (isLoggedIn) {
      loadRegistrations();
      loadPartners();
      loadEvents();
    }
  }, [isLoggedIn]);

  // Lazy-load members the first time the Members tab is opened.
  useEffect(() => {
    if (isLoggedIn && activeSection === "members" && members.length === 0 && !isLoadingMembers) {
      loadMembers();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [isLoggedIn, activeSection]);

  // Lazy-load carousel config the first time the Carousel tab is opened.
  useEffect(() => {
    if (isLoggedIn && activeSection === "carousel" && Object.keys(carouselConfig).length === 0) {
      getAdminSiteConfig().then(setCarouselConfig).catch(() => {});
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [isLoggedIn, activeSection]);

  // Lazy-load partners display mode when the Partners tab is opened.
  useEffect(() => {
    if (isLoggedIn && activeSection === "partners") {
      getAdminSiteConfig().then((cfg) => {
        setPartnersDisplayMode(cfg.partners_display_mode ?? "flat");
      }).catch(() => {});
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [isLoggedIn, activeSection]);

  // Lazy-load visits analytics the first time the Visits tab is opened.
  useEffect(() => {
    if (isLoggedIn && activeSection === "visits" && !isLoadingVisits && visitSummary.daily.length === 0) {
      loadVisitSummary();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [isLoggedIn, activeSection]);

  useEffect(() => {
    if (resendCooldown <= 0) {
      return undefined;
    }

    const timer = setInterval(() => {
      setResendCooldown((prev) => (prev > 0 ? prev - 1 : 0));
    }, 1000);

    return () => clearInterval(timer);
  }, [resendCooldown]);

  useEffect(() => {
    if (!showChangePassword || !changePasswordSuccess) {
      return undefined;
    }

    const timer = setTimeout(() => {
      setShowChangePassword(false);
      setChangePasswordSuccess("");
      setChangePasswordError("");
    }, CHANGE_PASSWORD_SUCCESS_CLOSE_MS);

    return () => clearTimeout(timer);
  }, [showChangePassword, changePasswordSuccess]);

  const loadRegistrations = async () => {
    setIsLoading(true);
    setError("");
    try {
      const data = await getRegistrations();
      setRegistrations(data || []);
      setReportUpdatedAt(new Date());
    } catch (err) {
      setError(err.message || "Failed to load registrations");
      setRegistrations([]);
    } finally {
      setIsLoading(false);
    }
  };

  // Manual check-in for an admin working from the report table, not a QR
  // scan — e.g. the attendee lost their pass or it never arrived.
  const handleAdminCheckIn = async (registrationId) => {
    await checkInRegistrationById(registrationId);
    await loadRegistrations();
  };

  const loadPartners = async () => {
    setIsLoadingPartners(true);
    setPartnerError("");
    try {
      const data = await getPartners();
      setPartners(data || []);
    } catch (err) {
      setPartnerError(err.message || "Failed to load partners");
      setPartners([]);
    } finally {
      setIsLoadingPartners(false);
    }
  };

  const loadEvents = async () => {
    setIsLoadingEvents(true);
    setEventError("");
    try {
      const data = await getEvents();
      setEvents(data || []);
    } catch (err) {
      setEventError(err.message || "Failed to load events");
      setEvents([]);
    } finally {
      setIsLoadingEvents(false);
    }
  };

  // Members tab: set a member's WhatsApp group status for the year (saved straight away).
  const [whatsappSaving, setWhatsappSaving] = useState("");
  // Which year's WhatsApp group the Members tab shows / edits / exports. Years come from the links
  // set in the NPO Membership tab and from statuses already recorded; the latest is the default.
  const [waLinkYears, setWaLinkYears] = useState([]);
  const [waYearChoice, setWaYearChoice] = useState("");
  const waYears = useMemo(() => {
    const years = new Set([DEFAULT_WHATSAPP_YEAR, ...waLinkYears]);
    for (const m of members) Object.keys(m.whatsappGroup || {}).forEach((y) => years.add(y));
    return [...years].sort().reverse();
  }, [members, waLinkYears]);
  const waYear = waYears.includes(waYearChoice) ? waYearChoice : waYears[0];
  // A filter that only makes sense for some years falls back to "All" when the year changes.
  useEffect(() => {
    const waFilter = WHATSAPP_FILTERS.find((f) => f.value === memberNpoFilter);
    if (waFilter && waFilter.supported?.(waYear) === false) setMemberNpoFilter("all");
  }, [waYear, memberNpoFilter]);
  const handleWhatsappStatus = async (member, status) => {
    const previous = member.whatsappGroup || {};
    const optimistic = { ...previous };
    const year = waYear;
    if (status) optimistic[year] = status;
    else delete optimistic[year];
    setMemberError("");
    setWhatsappSaving(member.email);
    setMembers((prev) => prev.map((m) => (m.email === member.email ? { ...m, whatsappGroup: optimistic } : m)));
    try {
      await updateMember(member.email, { whatsappGroup: { [year]: status } });
    } catch (err) {
      setMembers((prev) => prev.map((m) => (m.email === member.email ? { ...m, whatsappGroup: previous } : m)));
      setMemberError(err.message || "Could not save the WhatsApp group status.");
    } finally {
      setWhatsappSaving("");
    }
  };

  const exportMembers = (rows, filtered) => {
    const blob = new Blob([membersToCsv(rows, waYear)], { type: "text/csv;charset=utf-8" });
    downloadCSV(blob, membersCsvFilename(filtered));
  };

  const loadMembers = async () => {
    setIsLoadingMembers(true);
    setMemberError("");
    try {
      const data = await getMembers();
      setMembers(data || []);
      // Best effort: the years that have a group link set (adds a year before anyone is recorded for it).
      getNpoMembershipConfig()
        .then((config) => setWaLinkYears(Object.keys(config.whatsappLinks || {})))
        .catch(() => {});
    } catch (err) {
      setMemberError(err.message || "Failed to load members");
      setMembers([]);
    } finally {
      setIsLoadingMembers(false);
    }
  };

  const openAddMember = () => {
    setMemberForm(INITIAL_MEMBER_FORM);
    setEditingMemberEmail(null);
    setMemberFormError("");
    setShowMemberForm(true);
  };

  const openEditMember = (member) => {
    setMemberForm({
      email: member.email || "",
      fullname: member.name || member.fullname || "",
      phone: member.phone || "",
      memberid: member.memberId || member.memberid || "",
      npoFlagActive: member.npoFlagActive || false,
      npoMembInterested: member.npoMembInterested || false,
      renewedForNextYear: member.renewedForNextYear || false,
    });
    setEditingMemberEmail(member.email);
    setMemberFormError("");
    setShowMemberForm(true);
  };

  const handleSaveMember = async (e) => {
    e.preventDefault();
    setMemberFormError("");
    const email = memberForm.email.trim().toLowerCase();
    if (!email) { setMemberFormError("Email is required."); return; }
    setIsSavingMember(true);
    try {
      const payload = { ...memberForm, email };
      if (editingMemberEmail) {
        await updateMember(editingMemberEmail, payload);
      } else {
        await saveMember(payload);
      }
      setMemberSuccess(editingMemberEmail ? "Member updated." : "Member added.");
      setShowMemberForm(false);
      await loadMembers();
    } catch (err) {
      setMemberFormError(err.message || "Failed to save member.");
    } finally {
      setIsSavingMember(false);
    }
  };

  const handleDeleteMember = async (email) => {
    if (!window.confirm(`Delete member ${email}? This only removes them from the members table.`)) return;
    try {
      await deleteMember(email);
      setMembers((prev) => prev.filter((m) => m.email !== email));
      setMemberSuccess("Member deleted.");
    } catch (err) {
      setMemberError(err.message || "Failed to delete member.");
    }
  };

  const openNpoPanel = async (member) => {
    const memberid = member.memberId || member.memberid || member.email;
    setNpoPanel({ email: member.email, memberid, name: member.name || member.email, transactions: [] });
    setShowNpoTxnForm(false);
    setNpoTxnFormError("");
    setIsLoadingNpoTxns(true);
    try {
      const txns = await getNpoTransactions(memberid);
      setNpoPanel({ email: member.email, memberid, name: member.name || member.email, transactions: txns });
    } catch {
      setNpoPanel((prev) => prev ? { ...prev, transactions: [] } : null);
    } finally {
      setIsLoadingNpoTxns(false);
    }
  };

  const openAddNpoTxn = () => {
    setNpoTxnForm({
      ...INITIAL_NPO_TXN_FORM,
      membDateFrom: NPO_DEFAULT_FROM,
      membDateTo: NPO_DEFAULT_TO,
      membershipAmountPaid: NPO_DEFAULT_AMOUNT,
      email: npoPanel?.email || "",
    });
    setEditingTxnNo(null);
    setNpoTxnFormError("");
    setNpoNotify(null);
    setNpoTxnNote("");
    setShowNpoTxnForm(true);
  };

  const openEditNpoTxn = (txn) => {
    setNpoTxnForm({
      // Pending sign-ups have no dates yet — default them to the membership year.
      membDateFrom: txn.membDateFrom || NPO_DEFAULT_FROM,
      membDateTo: txn.membDateTo || NPO_DEFAULT_TO,
      // Only a completed Square payment has a real paid date to show.
      paidDate: (txn.paidDate || "").slice(0, 10),
      membershipAmountPaid: txn.membershipAmountPaid != null ? String(txn.membershipAmountPaid) : NPO_DEFAULT_AMOUNT,
      paidVia: txn.paidVia || "",
      paymentStatus: txn.paymentStatus || "",
      email: txn.email || npoPanel?.email || "",
      notes: txn.notes || "",
    });
    setEditingTxnNo(txn.transactionNo);
    setNpoTxnFormError("");
    setShowNpoTxnForm(true);
  };

  // New-transaction "email the member" checkbox: ticked by default only for a current/future period,
  // so back-filling an old year doesn't email anyone.
  const currentNpoNotify = () =>
    npoNotify !== null ? npoNotify : (npoTxnForm.membDateTo || "") >= new Date().toISOString().slice(0, 10);

  const handleSaveNpoTxn = async (e) => {
    e.preventDefault();
    if (!npoPanel) return;
    setNpoTxnFormError("");
    setIsSavingNpoTxn(true);
    try {
      const payload = {
        ...npoTxnForm,
        membershipAmountPaid: npoTxnForm.membershipAmountPaid ? Number(npoTxnForm.membershipAmountPaid) : undefined,
        paymentStatus: npoTxnForm.paymentStatus || undefined,
      };
      let saved;
      setNpoTxnNote("");
      if (editingTxnNo) {
        saved = await updateNpoTransaction(npoPanel.memberid, editingTxnNo, payload);
        if (saved.emailSent) setNpoTxnNote(`Marked paid — a confirmation email was sent to ${saved.email}.`);
      } else {
        const notify = Boolean(npoTxnForm.email) && currentNpoNotify();
        saved = await saveNpoTransaction(npoPanel.memberid, { ...payload, notify });
        setNpoTxnNote(
          saved.emailSent
            ? `Transaction added — a confirmation email was sent to ${saved.email}.`
            : notify
              ? "Transaction added, but the confirmation email could not be sent."
              : "Transaction added (no email sent)."
        );
      }
      setNpoPanel((prev) => {
        if (!prev) return prev;
        const txns = editingTxnNo
          ? prev.transactions.map((t) => t.transactionNo === editingTxnNo ? saved : t)
          : [...prev.transactions, saved];
        return { ...prev, transactions: txns };
      });
      setShowNpoTxnForm(false);
      await loadMembers();
    } catch (err) {
      setNpoTxnFormError(err.message || "Failed to save transaction.");
    } finally {
      setIsSavingNpoTxn(false);
    }
  };

  // A My Page sign-up stays "pending" (no membership dates) until payment is
  // confirmed here; marking it paid starts the membership.
  const handleMarkNpoTxnPaid = async (txn) => {
    if (!npoPanel) return;
    if (!window.confirm(`Confirm payment received for transaction #${txn.transactionNo}? Membership will start.`)) return;
    try {
      const saved = await updateNpoTransaction(npoPanel.memberid, txn.transactionNo, { paymentStatus: "paid" });
      setNpoTxnNote(
        saved.emailSent
          ? `Marked paid — a confirmation email was sent to ${saved.email}.`
          : "Marked paid. (No confirmation email could be sent — check the member's email address.)"
      );
      setNpoPanel((prev) =>
        prev ? { ...prev, transactions: prev.transactions.map((t) => (t.transactionNo === txn.transactionNo ? saved : t)) } : prev
      );
      await loadMembers();
    } catch (err) {
      setNpoTxnFormError(err.message || "Failed to mark as paid.");
    }
  };

  const openCancelTxn = (txn) => {
    const total = Number(txn.orderTotal || txn.membershipAmountPaid || 0);
    setCancelTxn(txn);
    setCancelForm({
      refund: txn.paidVia === "square" && txn.paymentReference ? "square" : "manual",
      refundAmount: String(total || ""),
      reason: "",
      notify: true,
    });
    setCancelError("");
    setShowNpoTxnForm(false);
  };

  const handleConfirmCancelTxn = async (e) => {
    e.preventDefault();
    if (!npoPanel || !cancelTxn) return;
    const total = Number(cancelTxn.orderTotal || cancelTxn.membershipAmountPaid || 0);
    const refunding = cancelForm.refund !== "none";
    const amount = Number(cancelForm.refundAmount);
    if (refunding && !(amount >= 1 && amount <= total)) {
      setCancelError(`Enter a refund between ¥1 and ¥${total.toLocaleString()}.`);
      return;
    }
    const summary =
      cancelForm.refund === "square"
        ? `Refund ¥${amount.toLocaleString()} to the member's card and cancel`
        : cancelForm.refund === "manual"
          ? `Record a ¥${amount.toLocaleString()} manual refund and cancel`
          : "Cancel (no refund)";
    if (!window.confirm(`${summary} transaction #${cancelTxn.transactionNo}? The membership ends immediately${(cancelTxn.linked || []).length ? ", including the spouse's" : ""}. This can't be undone.`)) return;
    setIsCancellingTxn(true);
    setCancelError("");
    try {
      const result = await cancelNpoTransaction(npoPanel.memberid, cancelTxn.transactionNo, {
        refund: cancelForm.refund,
        refundAmount: refunding ? amount : undefined,
        reason: cancelForm.reason.trim(),
        notify: cancelForm.notify,
      });
      setCancelTxn(null);
      // The spouse's linked record changed too, so reload the whole list.
      const txns = await getNpoTransactions(npoPanel.memberid);
      setNpoPanel((prev) => (prev ? { ...prev, transactions: txns } : prev));
      await loadMembers();
      return result;
    } catch (err) {
      setCancelError(err.message || "Could not cancel this membership.");
    } finally {
      setIsCancellingTxn(false);
    }
  };

  const handleDeleteNpoTxn = async (txn) => {
    if (!npoPanel) return;
    if (!window.confirm(`Delete transaction #${txn.transactionNo}?`)) return;
    try {
      await deleteNpoTransaction(npoPanel.memberid, txn.transactionNo);
      setNpoPanel((prev) =>
        prev ? { ...prev, transactions: prev.transactions.filter((t) => t.transactionNo !== txn.transactionNo) } : prev
      );
      await loadMembers();
    } catch (err) {
      setNpoTxnFormError(err.message || "Failed to delete transaction.");
    }
  };

  const loadVisitSummary = async (days = visitDays) => {
    setIsLoadingVisits(true);
    setVisitError("");
    try {
      const data = await getVisitSummary(days);
      setVisitSummary(data);
    } catch (err) {
      setVisitError(err.message || "Failed to load visit analytics");
      setVisitSummary({
        days,
        range: { from: "", to: "" },
        totalVisits: 0,
        uniqueVisitors: 0,
        daily: [],
        byPath: [],
        byVisitorType: [],
      });
    } finally {
      setIsLoadingVisits(false);
    }
  };

  const resetNewPasswordState = () => {
    setRequiresNewPassword(false);
    setChallengeUser(null);
    setNewPassword("");
    setConfirmNewPassword("");
    setLoginSuccess("");
  };

  const resetForgotPasswordState = () => {
    setForgotPasswordMode(false);
    setResetCodeSent(false);
    setResetCode("");
    setForgotNewPassword("");
    setForgotConfirmPassword("");
    setResendCooldown(0);
    setLoginSuccess("");
  };

  const handleLogin = async (e) => {
    e.preventDefault();
    setLoginError("");
    setLoginSuccess("");
    setIsLoggingIn(true);

    if (!loginEmail.trim() || !loginPassword.trim()) {
      setLoginError("Please enter email and password");
      setIsLoggingIn(false);
      return;
    }

    try {
      await loginUser(loginEmail.trim(), loginPassword.trim());
      resetNewPasswordState();
      resetForgotPasswordState();
      setIsLoggedIn(true);
      onAuthenticated?.();
      setLoginEmail("");
      setLoginPassword("");
      setLoginError("");
    } catch (err) {
      if (err?.code === "NEW_PASSWORD_REQUIRED") {
        resetForgotPasswordState();
        setRequiresNewPassword(true);
        setChallengeUser(err.session || null);
        setLoginError("First-time login detected. Please set a new password to continue.");
        return;
      }
      setLoginError(err.message || "Login failed. Please check your credentials.");
      console.error("Login error:", err);
    } finally {
      setIsLoggingIn(false);
    }
  };

  const handleSetNewPassword = async (e) => {
    e.preventDefault();
    setLoginError("");
    setLoginSuccess("");

    if (!newPassword.trim() || !confirmNewPassword.trim()) {
      setLoginError("Please enter and confirm your new password.");
      return;
    }

    if (newPassword !== confirmNewPassword) {
      setLoginError("New password and confirm password do not match.");
      return;
    }

    if (newPassword.length < 8) {
      setLoginError("New password must be at least 8 characters long.");
      return;
    }

    setIsSettingPassword(true);
    try {
      await completeNewPasswordChallenge(challengeUser, loginEmail.trim(), newPassword);
      resetNewPasswordState();
      setLoginPassword("");
      setLoginError("");
      setIsLoggedIn(true);
      onAuthenticated?.();
    } catch (err) {
      setLoginError(err.message || "Failed to set new password. Please try again.");
    } finally {
      setIsSettingPassword(false);
    }
  };

  const handleRequestResetCode = async (e) => {
    e.preventDefault();
    setLoginError("");
    setLoginSuccess("");

    if (!loginEmail.trim()) {
      setLoginError("Please enter your email address.");
      return;
    }

    setIsRequestingResetCode(true);
    try {
      await requestPasswordResetCode(loginEmail.trim());
      setResetCodeSent(true);
      setResendCooldown(RESEND_COOLDOWN_SECONDS);
      setLoginError("");
      setLoginSuccess("Verification code sent. Check your email inbox.");
    } catch (err) {
      setLoginSuccess("");
      setLoginError(err.message || "Unable to send reset code.");
    } finally {
      setIsRequestingResetCode(false);
    }
  };

  const handleResendResetCode = async () => {
    if (resendCooldown > 0 || isRequestingResetCode) {
      return;
    }

    setLoginError("");
    setLoginSuccess("");
    setIsRequestingResetCode(true);
    try {
      await requestPasswordResetCode(loginEmail.trim());
      setResendCooldown(RESEND_COOLDOWN_SECONDS);
      setLoginSuccess("A new verification code has been sent.");
    } catch (err) {
      setLoginError(err.message || "Unable to resend code.");
    } finally {
      setIsRequestingResetCode(false);
    }
  };

  const handleConfirmPasswordReset = async (e) => {
    e.preventDefault();
    setLoginError("");
    setLoginSuccess("");

    if (!resetCode.trim()) {
      setLoginError("Please enter the verification code.");
      return;
    }

    if (!forgotNewPassword.trim() || !forgotConfirmPassword.trim()) {
      setLoginError("Please enter and confirm your new password.");
      return;
    }

    if (forgotNewPassword !== forgotConfirmPassword) {
      setLoginError("New password and confirm password do not match.");
      return;
    }

    if (forgotNewPassword.length < 8) {
      setLoginError("New password must be at least 8 characters long.");
      return;
    }

    setIsResettingPassword(true);
    try {
      await confirmPasswordReset(loginEmail.trim(), resetCode.trim(), forgotNewPassword);
      setLoginError("");
      setLoginPassword("");
      resetForgotPasswordState();
      setLoginSuccess("Password reset successful. Please sign in with your new password.");
    } catch (err) {
      setLoginSuccess("");
      setLoginError(err.message || "Unable to reset password.");
    } finally {
      setIsResettingPassword(false);
    }
  };

  const handleLogout = () => {
    cognitoLogout();
    resetNewPasswordState();
    resetForgotPasswordState();
    setShowChangePassword(false);
    setCurrentPassword("");
    setDashboardNewPassword("");
    setDashboardConfirmPassword("");
    setChangePasswordError("");
    setChangePasswordSuccess("");
    setIsLoggedIn(false);
    setRegistrations([]);
    setPartners([]);
    setPartnerForm(INITIAL_PARTNER_FORM);
    setEditingPartnerId("");
    setPartnerError("");
    setPartnerSuccess("");
    setActiveSection("registrations");
    setLoginEmail("");
    setLoginPassword("");
    if (onLogout) {
      onLogout();
    }
  };

  const resetPartnerForm = () => {
    setPartnerForm(INITIAL_PARTNER_FORM);
    setEditingPartnerId("");
    setPartnerError("");
  };

  const handlePartnerInputChange = (event) => {
    const { name, value, type, checked } = event.target;
    setPartnerForm((prev) => ({
      ...prev,
      [name]: type === "checkbox" ? checked : value,
    }));
    setPartnerError("");
    setPartnerSuccess("");
  };

  const handleEditPartner = (partner) => {
    setEditingPartnerId(partner.partnerId);
    setPartnerForm({
      partnerId: partner.partnerId,
      name: partner.name || "",
      websiteUrl: partner.websiteUrl || "",
      logoUrl: partner.logoUrl || "",
      description: partner.description || "",
      tier: partner.tier || "gold",
      scope: partner.scope || "all",
      displayOrder: String(partner.displayOrder ?? 0),
      isActive: partner.isActive !== false,
    });
    setPartnerError("");
    setPartnerSuccess("");
    setActiveSection("partners");
  };

  const handleSavePartner = async (event) => {
    event.preventDefault();
    setPartnerError("");
    setPartnerSuccess("");

    const payload = {
      ...partnerForm,
      name: partnerForm.name.trim(),
      websiteUrl: partnerForm.websiteUrl.trim(),
      logoUrl: partnerForm.logoUrl.trim(),
      description: partnerForm.description.trim(),
      tier: partnerForm.tier || "gold",
      scope: partnerForm.scope,
      displayOrder: Number(partnerForm.displayOrder || 0),
      isActive: !!partnerForm.isActive,
    };

    if (!payload.name || !payload.websiteUrl) {
      setPartnerError("Please enter a partner name and website URL.");
      return;
    }

    setIsSavingPartner(true);
    try {
      const savedPartner = await savePartner(payload);
      setPartners((prev) => {
        const next = prev.filter((item) => item.partnerId !== savedPartner.partnerId);
        return [...next, savedPartner].sort((a, b) => {
          if ((a.displayOrder || 0) !== (b.displayOrder || 0)) {
            return (a.displayOrder || 0) - (b.displayOrder || 0);
          }
          return String(a.name || "").localeCompare(String(b.name || ""));
        });
      });
      setPartnerSuccess(editingPartnerId ? "Partner updated successfully." : "Partner added successfully.");
      resetPartnerForm();
    } catch (err) {
      setPartnerError(err.message || "Failed to save partner");
    } finally {
      setIsSavingPartner(false);
    }
  };

  const handleDeletePartner = async (partnerId) => {
    const confirmDelete = window.confirm("Delete this partner? This cannot be undone.");
    if (!confirmDelete) {
      return;
    }

    setPartnerError("");
    setPartnerSuccess("");
    try {
      await deletePartner(partnerId);
      setPartners((prev) => prev.filter((item) => item.partnerId !== partnerId));
      if (editingPartnerId === partnerId) {
        resetPartnerForm();
      }
      setPartnerSuccess("Partner deleted.");
    } catch (err) {
      setPartnerError(err.message || "Failed to delete partner");
    }
  };

  const handleSetDisplayMode = async (mode) => {
    if (mode === partnersDisplayMode || savingDisplayMode) return;
    setSavingDisplayMode(true);
    try {
      await updatePartnersDisplayMode(mode);
      setPartnersDisplayMode(mode);
    } catch {
      // silent — the toggle will just not update visually
    } finally {
      setSavingDisplayMode(false);
    }
  };

  // ── Events handlers ─────────────────────────────────────────────────────
  const resetEventForm = () => {
    setEventForm(INITIAL_EVENT_FORM);
    setEditingEventType("");
    setEventError("");
    setSeatMapUpload({ uploading: false, error: "" });
  };

  const handleEventInputChange = (event) => {
    const { name, value } = event.target;
    setEventForm((prev) => ({ ...prev, [name]: value }));
    setEventError("");
    setEventSuccess("");
  };

  const handleSeatMapFileSelect = async (file) => {
    if (!file) return;
    if (!file.type.startsWith("image/")) {
      setSeatMapUpload({ uploading: false, error: "Only image files are supported." });
      return;
    }
    if (!eventForm.eventType.trim()) {
      setSeatMapUpload({ uploading: false, error: "Set the event id before uploading a seat map." });
      return;
    }
    setSeatMapUpload({ uploading: true, error: "" });
    try {
      const { imageUrl } = await uploadSeatUpgradeSeatMap(eventForm.eventType.trim(), file);
      setEventForm((prev) => ({ ...prev, seatUpgradeSeatMapImageUrl: imageUrl }));
      setSeatMapUpload({ uploading: false, error: "" });
    } catch (err) {
      setSeatMapUpload({ uploading: false, error: err.message || "Upload failed." });
    }
  };

  const handleEditEvent = (evt) => {
    const eventInfo = evt.eventInfo && typeof evt.eventInfo === "object" ? evt.eventInfo : {};
    const registration = evt.registration && typeof evt.registration === "object" ? evt.registration : {};
    const paymentLinks = getPaymentLinksFromEventInfo(eventInfo);
    const seatSelection = getSeatSelectionFromEventInfo(eventInfo);
    const seatUpgrade = getSeatUpgradeConfigFromEventInfo(eventInfo);
    const celebrityDinner = getCelebrityDinnerConfigFromEventInfo(eventInfo);
    setEditingEventType(evt.eventType);
    setEventForm({
      eventType: evt.eventType || "",
      eventName: evt.eventName || "",
      eventDate: evt.eventDate || "",
      hostId: evt.hostId || "",
      status: evt.status || "inactive",
      description: evt.description || "",
      venue: evt.venue || "",
      venueAddress: evt.venueAddress || "",
      eventNote: evt.eventNote || "",
      registrationPath: evt.registrationPath || "",
      capacity: String(evt.capacity ?? 0),
      registrationClosesOn: registration.closesOn || "",
      cancellationEnabled: registration.cancellationEnabled !== false,
      onamRegistrationClosed: registration.onamRegistrationClosed === true,
      celebrityRegistrationClosed: registration.celebrityRegistrationClosed === true,
      sadhyaCapacity: registration.sadhyaCapacity === undefined ? "" : String(registration.sadhyaCapacity),
      celebrityCapacity: registration.celebrityCapacity === undefined ? "" : String(registration.celebrityCapacity),
      sadhyaSlotStartDate: registration.sadhyaSlotStartDate || "",
      sadhyaSlotEndDate: registration.sadhyaSlotEndDate || "",
      paymentLinkCredit: paymentLinks.credit,
      paymentLinkPayPal: paymentLinks.paypay,
      paymentLinkBank: paymentLinks.bank,
      seatSelectionEnabled: seatSelection.enabled,
      seatUpgradeEnabled: seatUpgrade.enabled,
      seatUpgradeUrl: seatUpgrade.url,
      seatUpgradeSeatMapImageUrl: seatUpgrade.seatMapImageUrl,
      seatUpgradeStartDate: seatUpgrade.startDate,
      seatUpgradeEndDate: seatUpgrade.endDate,
      seatUpgradePaymentPaypay: seatUpgrade.paymentMethods.includes("paypay"),
      seatUpgradePaymentSquare: seatUpgrade.paymentMethods.includes("square"),
      seatUpgradePaymentBank: seatUpgrade.paymentMethods.includes("bank"),
      celebrityDinnerEnabled: celebrityDinner.enabled,
      eventInfo,
    });
    setEventError("");
    setEventSuccess("");
    setActiveSection("events");
  };

  const handleSaveEvent = async (e) => {
    e.preventDefault();
    setEventError("");
    setEventSuccess("");

    const payload = {
      ...eventForm,
      eventType: eventForm.eventType.trim(),
      eventName: eventForm.eventName.trim(),
      hostId: eventForm.hostId.trim(),
      description: eventForm.description.trim(),
      venue: eventForm.venue.trim(),
      venueAddress: eventForm.venueAddress.trim(),
      eventNote: eventForm.eventNote.trim(),
      registrationPath: eventForm.registrationPath.trim(),
      capacity: Number(eventForm.capacity || 0),
      registration: {
        closesOn: eventForm.registrationClosesOn,
        cancellationEnabled: eventForm.cancellationEnabled,
        onamRegistrationClosed: eventForm.onamRegistrationClosed,
        celebrityRegistrationClosed: eventForm.celebrityRegistrationClosed,
        ...(eventForm.sadhyaCapacity !== "" ? { sadhyaCapacity: Number(eventForm.sadhyaCapacity) } : {}),
        ...(eventForm.celebrityCapacity !== "" ? { celebrityCapacity: Number(eventForm.celebrityCapacity) } : {}),
        sadhyaSlotStartDate: eventForm.sadhyaSlotStartDate,
        sadhyaSlotEndDate: eventForm.sadhyaSlotEndDate,
      },
    };

    const paymentLinkCredit = (eventForm.paymentLinkCredit || "").trim();
    const paymentLinkPayPal = (eventForm.paymentLinkPayPal || "").trim();
    const paymentLinkBank = (eventForm.paymentLinkBank || "").trim();

    const paymentLinkCandidates = [
      ["Credit Card", paymentLinkCredit],
      ["PayPay", paymentLinkPayPal],
      ["Bank Transfer", paymentLinkBank],
    ];

    const invalidPaymentLink = paymentLinkCandidates.find(([, link]) =>
      link && !/^https?:\/\//i.test(link)
    );

    if (invalidPaymentLink) {
      setEventError(`${invalidPaymentLink[0]} payment link must start with http:// or https://`);
      return;
    }

    const paymentLinks = {};
    if (paymentLinkCredit) paymentLinks.credit = paymentLinkCredit;
    if (paymentLinkPayPal) paymentLinks.paypay = paymentLinkPayPal;
    if (paymentLinkBank) paymentLinks.bank = paymentLinkBank;

    const seatUpgradeUrl = (eventForm.seatUpgradeUrl || "").trim();
    if (seatUpgradeUrl && !/^https?:\/\//i.test(seatUpgradeUrl) && !seatUpgradeUrl.startsWith("/")) {
      setEventError("Zone Upgrade URL must start with http://, https://, or /");
      return;
    }
    const seatUpgradePaymentMethods = SEAT_UPGRADE_PAYMENT_METHODS
      .filter((m) => eventForm[m.formKey])
      .map((m) => m.key);
    if (eventForm.seatUpgradeEnabled && seatUpgradePaymentMethods.length === 0) {
      setEventError("Select at least one payment method for zone upgrades, or disable the plugin for this event.");
      return;
    }
    const seatMapImageUrl = (eventForm.seatUpgradeSeatMapImageUrl || "").trim();
    if (eventForm.seatUpgradeEnabled && !seatMapImageUrl) {
      setEventError("Upload a seat map image before enabling the zone upgrade plugin.");
      return;
    }
    if (seatMapUpload.uploading) {
      setEventError("Wait for the seat map image to finish uploading before saving.");
      return;
    }
    const seatUpgradeStartDate = (eventForm.seatUpgradeStartDate || "").trim();
    const seatUpgradeEndDate = (eventForm.seatUpgradeEndDate || "").trim();
    if (seatUpgradeStartDate && seatUpgradeEndDate && seatUpgradeEndDate < seatUpgradeStartDate) {
      setEventError("Zone upgrade end date must be on or after the start date.");
      return;
    }
    if (
      eventForm.sadhyaSlotStartDate &&
      eventForm.sadhyaSlotEndDate &&
      eventForm.sadhyaSlotEndDate < eventForm.sadhyaSlotStartDate
    ) {
      setEventError("Sadhya slot booking end date must be on or after the start date.");
      return;
    }

    // Everything else about seat selection (booking window, pricing,
    // payment methods) is now managed from its own Seat Selection tab, not
    // this form — preserve whatever's already there and only touch `enabled`.
    const existingSeatSelection = eventForm.eventInfo
      && typeof eventForm.eventInfo === "object"
      && eventForm.eventInfo.seatSelection
      && typeof eventForm.eventInfo.seatSelection === "object"
      ? eventForm.eventInfo.seatSelection
      : {};

    const baseEventInfo = eventForm.eventInfo && typeof eventForm.eventInfo === "object"
      ? { ...eventForm.eventInfo }
      : {};
    delete baseEventInfo.paymentLinks;
    delete baseEventInfo.payment;
    delete baseEventInfo.seatSelection;
    delete baseEventInfo.seatUpgrade;
    delete baseEventInfo.celebrityDinner;

    payload.eventInfo = {
      ...baseEventInfo,
      ...(Object.keys(paymentLinks).length ? { paymentLinks } : {}),
      seatSelection: {
        ...existingSeatSelection,
        enabled: eventForm.seatSelectionEnabled,
      },
      seatUpgrade: {
        enabled: eventForm.seatUpgradeEnabled === true,
        url: seatUpgradeUrl,
        seatMapImageUrl,
        startDate: seatUpgradeStartDate,
        endDate: seatUpgradeEndDate,
        paymentMethods: seatUpgradePaymentMethods,
      },
      celebrityDinner: {
        enabled: eventForm.celebrityDinnerEnabled === true,
      },
    };

    delete payload.paymentLinkCredit;
    delete payload.paymentLinkPayPal;
    delete payload.paymentLinkBank;
    delete payload.seatSelectionEnabled;
    delete payload.seatUpgradeEnabled;
    delete payload.seatUpgradeUrl;
    delete payload.seatUpgradeSeatMapImageUrl;
    delete payload.seatUpgradeStartDate;
    delete payload.seatUpgradeEndDate;
    delete payload.seatUpgradePaymentPaypay;
    delete payload.seatUpgradePaymentSquare;
    delete payload.seatUpgradePaymentBank;
    delete payload.celebrityDinnerEnabled;
    delete payload.registrationClosesOn;
    delete payload.cancellationEnabled;
    delete payload.onamRegistrationClosed;
    delete payload.celebrityRegistrationClosed;
    delete payload.sadhyaCapacity;
    delete payload.celebrityCapacity;
    delete payload.sadhyaSlotStartDate;
    delete payload.sadhyaSlotEndDate;

    if (!payload.eventType || !payload.eventName || !payload.hostId) {
      setEventError("Event id, event name, and host id are required.");
      return;
    }

    setIsSavingEvent(true);
    try {
      const saved = await saveEvent(payload);
      setEvents((prev) => {
        const next = prev.filter((item) => item.eventType !== saved.eventType);
        return [...next, saved].sort((a, b) =>
          String(a.eventDate || "").localeCompare(String(b.eventDate || ""))
        );
      });
      setEventSuccess(editingEventType ? "Event updated." : "Event added.");
      resetEventForm();
    } catch (err) {
      setEventError(err.message || "Failed to save event");
    } finally {
      setIsSavingEvent(false);
    }
  };

  const handleDeleteEvent = async (eventType) => {
    if (!window.confirm(`Delete event "${eventType}"? This cannot be undone. Existing registrations are not removed.`)) {
      return;
    }
    setEventError("");
    setEventSuccess("");
    try {
      await deleteEvent(eventType);
      setEvents((prev) => prev.filter((item) => item.eventType !== eventType));
      if (editingEventType === eventType) {
        resetEventForm();
      }
      setEventSuccess("Event deleted.");
    } catch (err) {
      setEventError(err.message || "Failed to delete event");
    }
  };

  // ── Registration handlers (add / edit / delete) ──────────────────────────
  const openNewRegistrationForm = () => {
    const eventType = selectedEventType !== "all" ? selectedEventType : "";
    const eventRecord = events.find((evt) => evt.eventType === eventType);
    const isOnam = /onam/i.test(eventType);
    setRegistrationForm({
      ...INITIAL_REGISTRATION_FORM,
      eventType,
      eventDate: eventRecord?.eventDate || "",
      registrationType: isOnam ? "onam" : "standard",
      attendeeCounts: { adults: 0, kids914: 0, kids68: 0, kidsUnder6: 0 },
      additionalAdultEmails: [],
    });
    setRegistrationFormError("");
    setShowRegistrationForm(true);
  };

  const openEditRegistrationForm = (reg) => {
    const isOnam = reg.registrationType === "onam";
    // Seed the additional-adult email slots from the stored record, padded to
    // (adults − 1) so legacy records without additionalAdults still get inputs.
    const storedAdultEmails = (Array.isArray(reg.additionalAdults) ? reg.additionalAdults : []).map(
      (a) => a?.email || ""
    );
    const neededAdultEmails = Math.max(0, (Number(reg.attendeeCounts?.adults) || 0) - 1);
    const additionalAdultEmails = Array.from(
      { length: neededAdultEmails },
      (_, i) => storedAdultEmails[i] || ""
    );
    setRegistrationForm({
      registrationId: reg.registrationId || "",
      eventType: reg.eventType || "",
      eventDate: reg.eventDate || "",
      registrationType: reg.registrationType || "standard",
      name: reg.name || `${reg.firstName || ""} ${reg.lastName || ""}`.trim(),
      email: reg.email || "",
      phone: reg.phone || reg.whatsapp || "",
      npoName: reg.npoName || "",
      paymentMethod: normalizeOnamPaymentMethod(reg.paymentMethod) || "",
      paymentStatus: reg.paymentStatus || "pending",
      paidAmount: Number(reg.paidAmount) || 0,
      totalAmount: Number(reg.totalAmount) || 0,
      registrationOption: reg.registrationOption || "combo",
      membershipAction: "none",
      pricingTier: reg.pricingTier || "none",
      attendeeCounts: {
        adults: reg.attendeeCounts?.adults ?? 0,
        kids914: reg.attendeeCounts?.kids914 ?? 0,
        kids68: reg.attendeeCounts?.kids68 ?? 0,
        kidsUnder6: reg.attendeeCounts?.kidsUnder6 ?? 0,
      },
      additionalAdultEmails,
      checkedIn: isCheckedIn(reg),
    });
    setRegistrationFormError("");
    setShowRegistrationForm(true);
    void isOnam;
  };

  const handleRegistrationFieldChange = (event) => {
    const { name, value } = event.target;
    setRegistrationForm((prev) => ({ ...prev, [name]: value }));
    setRegistrationFormError("");
  };

  const handleOnamCountChange = (key, value) => {
    setRegistrationForm((prev) => {
      const nextCount = Math.max(0, Number(value) || 0);
      const next = { ...prev, attendeeCounts: { ...prev.attendeeCounts, [key]: nextCount } };
      // Keep the additional-adult email slots (adults 2…N) in sync with the count.
      if (key === "adults") {
        const needed = Math.max(0, nextCount - 1);
        const emails = prev.additionalAdultEmails || [];
        next.additionalAdultEmails =
          emails.length > needed
            ? emails.slice(0, needed)
            : [...emails, ...Array.from({ length: needed - emails.length }, () => "")];
      }
      return next;
    });
  };

  const handleAdditionalAdultEmailChange = (index, value) => {
    setRegistrationForm((prev) => {
      const emails = [...(prev.additionalAdultEmails || [])];
      emails[index] = value;
      return { ...prev, additionalAdultEmails: emails };
    });
    setRegistrationFormError("");
  };

  const handleStandardCountChange = (key, value) => {
    setRegistrationForm((prev) => ({
      ...prev,
      attendeeCounts: { ...prev.attendeeCounts, [key]: Math.max(0, Number(value) || 0) },
    }));
  };

  const getAdminCapacityError = () => {
    const event = events.find((item) => item.eventType === registrationForm.eventType);
    if (!event) return "";

    const registration = event.registration && typeof event.registration === "object"
      ? event.registration
      : {};
    const isOnam = registrationForm.registrationType === "onam";
    const option = registrationForm.registrationOption;
    const pools = isOnam
      ? [
        ["sadhyaCapacity", ["onam", "combo"], "Onam Sadhya"],
        ["celebrityCapacity", ["event", "combo"], "Celebrity Event"],
      ]
      : [["capacity", [], event.eventName || "Event"]];
    const countKeys = ["adults", "kids914", "kids68"]
    const requestedSlots = countKeys.reduce((total, key) => total + (Number(
      (isOnam ? registrationForm.attendeeCounts : registrationForm.attendeeCounts)[key]
    ) || 0), 0);

    for (const [capacityKey, options, label] of pools) {
      if (options.length && !options.includes(option)) continue;
      const capacity = Number(capacityKey === "capacity" ? event.capacity : registration[capacityKey]) || 0;
      if (capacity <= 0) continue;
      const usedSlots = registrations
        .filter((item) => item.eventType === event.eventType
          && item.registrationId !== registrationForm.registrationId
          && (!options.length || options.includes(item.registrationOption)))
        .reduce((total, item) => total + countKeys.reduce(
          (count, key) => count + (Number(item.attendeeCounts?.[key]) || 0), 0
        ), 0);
      const availableSlots = Math.max(capacity - usedSlots, 0);
      if (usedSlots + requestedSlots > capacity) {
        return `${label} has ${availableSlots} available slot(s). This registration requires ${requestedSlots} slot(s).`;
      }
    }
    return "";
  };

  const handleSaveRegistration = async (e) => {
    e.preventDefault();
    // Belt-and-suspenders against a double-fire (e.g. a fast double click
    // beating the disabled-button re-render): the button's disabled state is
    // the primary guard, this is the backstop against a duplicate save —
    // and a duplicate payment-received email — landing anyway.
    if (isSavingRegistration) return;
    setRegistrationFormError("");

    const isOnam = registrationForm.registrationType === "onam";
    const payload = {
      registrationId: registrationForm.registrationId || undefined,
      eventType: registrationForm.eventType.trim(),
      eventDate: registrationForm.eventDate,
      name: registrationForm.name.trim(),
      email: registrationForm.email.trim(),
      phone: registrationForm.phone.trim(),
      paymentStatus: registrationForm.paymentStatus || "pending",
    };

    if (!payload.name || !payload.email) {
      setRegistrationFormError("Name and email are required.");
      return;
    }

    const capacityError = getAdminCapacityError();
    if (capacityError) {
      setRegistrationFormError(capacityError);
      return;
    }

    if (isOnam) {
      // Adults 2…N: email is optional; if provided it must be valid and unique.
      const adultCount = Math.max(0, Number(registrationForm.attendeeCounts.adults) || 0);
      const needed = Math.max(0, adultCount - 1);
      const emails = (registrationForm.additionalAdultEmails || [])
        .slice(0, needed)
        .map((e) => e.trim().toLowerCase());
      const seen = new Set([payload.email.toLowerCase()]);
      for (let i = 0; i < needed; i += 1) {
        const value = emails[i] || "";
        if (!value) continue; // optional — skip blank
        if (!EMAIL_REGEX.test(value)) {
          setRegistrationFormError(`Enter a valid email for adult ${i + 2}.`);
          return;
        }
        if (seen.has(value)) {
          setRegistrationFormError(`Adult ${i + 2}'s email is already used by another attendee.`);
          return;
        }
        seen.add(value);
      }

      payload.registrationOption = registrationForm.registrationOption;
      payload.membershipAction = registrationForm.membershipAction;
      payload.npoName = registrationForm.npoName.trim();
      payload.paymentMethod = registrationForm.paymentMethod.trim();
      payload.attendeeCounts = registrationForm.attendeeCounts;
      payload.additionalAdultEmails = emails;
    } else {
      payload.registrationType = registrationForm.registrationType || "standard";
      payload.attendeeCounts = registrationForm.attendeeCounts;
    }

    setIsSavingRegistration(true);
    try {
      const saved = await saveRegistration(payload);
      setRegistrations((prev) => {
        const next = prev.filter((item) => item.registrationId !== saved.registrationId);
        return [saved, ...next];
      });
      setShowRegistrationForm(false);
      setRegistrationForm(INITIAL_REGISTRATION_FORM);
    } catch (err) {
      setRegistrationFormError(err.message || "Failed to save registration");
    } finally {
      setIsSavingRegistration(false);
    }
  };

  const handleDeleteRegistration = async (registrationId) => {
    // A checked-in registration is locked server-side; deleting it needs the
    // explicit override, so spell out what it costs before asking for it.
    const reg = registrations.find((item) => item.registrationId === registrationId);
    const force = Boolean(reg && isCheckedIn(reg));
    const message = force
      ? "This registration has already been checked in at the venue. Its entry-slot " +
        "seats have been used, so deleting it will NOT free them up — the slot will " +
        "show fewer seats available than registrations on file.\n\nDelete anyway? " +
        "This cannot be undone."
      : "Delete this registration? This cannot be undone.";
    if (!window.confirm(message)) {
      return;
    }
    setError("");
    try {
      await deleteRegistration(registrationId, { force });
      setRegistrations((prev) => prev.filter((item) => item.registrationId !== registrationId));
      setSelectedRegistrationIds((prev) => {
        const next = new Set(prev);
        next.delete(registrationId);
        return next;
      });
    } catch (err) {
      setError(err.message || "Failed to delete registration");
    }
  };

  // Mirrors the backend's onam/standard attendeeCounts mapping; used only to
  // show a seats hint here, the server is the source of truth for capacity.
  const getRequiredSeatsForRegistration = (reg) => {
    const attendee_cnts = reg.attendeeCounts || {};
    if (reg.registrationType === "onam" || Object.keys(attendee_cnts).length > 0) {
      return (
        (Number(attendee_cnts.adults) || 0) +
        (Number(attendee_cnts.kids914) || 0) +
        (Number(attendee_cnts.kids68) || 0) +
        (Number(attendee_cnts.kidsUnder6) || 0)
      );
    }
    return (Number(attendee_cnts.adults) || 0) + (Number(attendee_cnts.kids914) || 0) + (Number(attendee_cnts.kids68) || 0) + (Number(attendee_cnts.kidsUnder6) || 0);
  };

  // Entry slots are frozen once anyone on the registration has been admitted.
  // Check-in writes the counters back onto the record, so the list already has
  // what we need; the backend rejects a reassignment either way.
  const isCheckedIn = (reg) =>
    (Array.isArray(reg.allocations) ? reg.allocations : []).some(
      (a) => (Number(a.checkedInCount) || 0) > 0
    );

  // Seed the reassignment picker from the registration's current allocation(s),
  // falling back to the legacy single-slot fields for older records.
  const getInitialAllocations = (reg) => {
    if (Array.isArray(reg.allocations) && reg.allocations.length) {
      return reg.allocations.map((a) => ({ slotId: a.entrySlotId, seatCount: Number(a.seatCount) || 0 }));
    }
    if (reg.slotId) {
      return [{ slotId: reg.slotId, seatCount: getRequiredSeatsForRegistration(reg) }];
    }
    return [];
  };

  const openSlotReassign = async (reg) => {
    setSlotReassignTarget(reg);
    setSlotReassignOptions([]);
    setSlotReassignAllocations(getInitialAllocations(reg));
    setSlotReassignError("");
    setSlotReassignLoading(true);
    try {
      const slots = await getEntrySlots(reg.eventType);
      setSlotReassignOptions(slots || []);
    } catch (err) {
      setSlotReassignError(err.message || "Failed to load entry slots.");
    } finally {
      setSlotReassignLoading(false);
    }
  };

  const closeSlotReassign = () => {
    setSlotReassignTarget(null);
    setSlotReassignOptions([]);
    setSlotReassignAllocations([]);
    setSlotReassignError("");
  };

  const handleSaveSlotReassign = async () => {
    if (!slotReassignTarget || slotReassignSaving) return;
    const requiredSeats = getRequiredSeatsForRegistration(slotReassignTarget);
    const allocatedSeats = slotReassignAllocations.reduce((sum, a) => sum + Number(a.seatCount || 0), 0);
    if (slotReassignAllocations.length === 0) {
      setSlotReassignError("Please assign at least one entry slot.");
      return;
    }
    if (allocatedSeats !== requiredSeats) {
      setSlotReassignError(`Allocate all ${requiredSeats} seated attendee${requiredSeats === 1 ? "" : "s"} across the selected entry slots.`);
      return;
    }
    setSlotReassignSaving(true);
    setSlotReassignError("");
    try {
      const updated = await reassignRegistrationSlot(slotReassignTarget.registrationId, slotReassignAllocations);
      setRegistrations((prev) =>
        prev.map((item) =>
          item.registrationId === slotReassignTarget.registrationId ? { ...item, ...updated } : item
        )
      );
      closeSlotReassign();
    } catch (err) {
      setSlotReassignError(err.message || "Failed to change entry slot.");
    } finally {
      setSlotReassignSaving(false);
    }
  };

  // Seats an event/combo registration is eligible to be assigned — same
  // "event"/"combo"-only gate my_registrations enforces for self-service
  // seat purchases.
  const isSeatEligible = (reg) => {
    const option = String(reg.registrationOption || "").toLowerCase();
    return option === "event" || option === "combo";
  };

  const openSeatAllocation = async (reg) => {
    setSeatAllocationTarget(reg);
    setSeatAllocationMap(null);
    setSeatAllocationInfo(null);
    setSeatAllocationSelected([]);
    setSeatAllocationError("");
    setSeatAllocationLoading(true);
    try {
      const [map, info] = await Promise.all([
        getAvailableSeats(reg.eventType),
        getRegistrationSeatAllocation(reg.registrationId),
      ]);
      setSeatAllocationMap(map);
      setSeatAllocationInfo(info);
      // Pre-select whatever this registration already holds in its own
      // editable (still-open, free) allocation, so the admin sees it
      // highlighted and can freely deselect/reselect rather than only
      // ever being able to add more.
      setSeatAllocationSelected(info.editableSeatIds || []);
    } catch (err) {
      setSeatAllocationError(err.message || "Failed to load the seat map.");
    } finally {
      setSeatAllocationLoading(false);
    }
  };

  const closeSeatAllocation = () => {
    setSeatAllocationTarget(null);
    setSeatAllocationMap(null);
    setSeatAllocationInfo(null);
    setSeatAllocationSelected([]);
    setSeatAllocationError("");
  };

  const handleSaveSeatAllocation = async () => {
    if (!seatAllocationTarget || seatAllocationSaving) return;
    if (seatAllocationSelected.length === 0) {
      setSeatAllocationError("Please select at least one seat.");
      return;
    }
    setSeatAllocationSaving(true);
    setSeatAllocationError("");
    try {
      await allocateRegistrationSeats(seatAllocationTarget.registrationId, seatAllocationSelected);
      closeSeatAllocation();
    } catch (err) {
      setSeatAllocationError(err.message || "Failed to allocate seats.");
    } finally {
      setSeatAllocationSaving(false);
    }
  };

  const toggleRegistrationSelection = (registrationId) => {
    setSelectedRegistrationIds((prev) => {
      const next = new Set(prev);
      if (next.has(registrationId)) {
        next.delete(registrationId);
      } else {
        next.add(registrationId);
      }
      return next;
    });
    setBulkActionMessage("");
  };

  const toggleVisibleRegistrationSelection = (checked) => {
    setSelectedRegistrationIds((prev) => {
      const next = new Set(prev);
      filteredRegistrations.forEach((registration) => {
        if (checked) {
          next.add(registration.registrationId);
        } else {
          next.delete(registration.registrationId);
        }
      });
      return next;
    });
    setBulkActionMessage("");
  };

  const handleBulkRegistrationAction = async () => {
    const registrationIds = [...selectedRegistrationIds];
    if (!registrationIds.length) {
      setError("Select at least one registration for a bulk action.");
      return;
    }
    const action = BULK_REGISTRATION_ACTIONS.find((option) => option.key === bulkRegistrationAction);
    if (!window.confirm(`${action?.label || "Run bulk action"} for ${registrationIds.length} selected registration(s)?`)) {
      return;
    }

    setError("");
    setBulkActionMessage("");
    setIsProcessingBulkAction(true);
    try {
      const result = await processBulkRegistrations(bulkRegistrationAction, registrationIds);
      const completed = result.registrations?.length || 0;
      const failures = result.failed || [];
      setBulkActionMessage(
        failures.length
          ? `${completed} registration(s) processed; ${failures.length} could not be processed.`
          : `${completed} registration(s) processed successfully.`
      );
      setSelectedRegistrationIds(new Set());
      await loadRegistrations();
    } catch (err) {
      setError(err.message || "Failed to process bulk registration action.");
    } finally {
      setIsProcessingBulkAction(false);
    }
  };

  const renderCarouselSection = () => {
    const SLOTS = [
      { key: "onam", label: "Onam Poster", description: "Aavani 2026 – September 12, 2026" },
      { key: "anniversary", label: "Anniversary Poster", description: "40th Anniversary – October 3, 2026" },
    ];

    const handleFileSelect = (slot, file) => {
      if (!file) return;
      if (file.size > 3 * 1024 * 1024) {
        setCarouselUpload((s) => ({ ...s, error: "File must be 3 MB or smaller." }));
        return;
      }
      if (!file.type.startsWith("image/")) {
        setCarouselUpload((s) => ({ ...s, error: "Only image files are supported." }));
        return;
      }
      const preview = URL.createObjectURL(file);
      setCarouselUpload({ slot, file, preview, uploading: false, error: null, success: null });
    };

    const handleCancel = () => {
      if (carouselUpload.preview) URL.revokeObjectURL(carouselUpload.preview);
      setCarouselUpload({ slot: null, file: null, preview: null, uploading: false, error: null, success: null });
    };

    const handleConfirmUpload = async () => {
      const { slot, file } = carouselUpload;
      if (!slot || !file) return;
      setCarouselUpload((s) => ({ ...s, uploading: true, error: null }));
      try {
        // Encodes to base64 and POSTs through our API — no direct cross-origin S3 PUT.
        const result = await uploadCarouselImage(slot, file);
        setCarouselConfig((prev) => ({
          ...prev,
          [slot]: { imageUrl: result.imageUrl, updatedAt: result.updatedAt },
        }));
        if (carouselUpload.preview) URL.revokeObjectURL(carouselUpload.preview);
        setCarouselUpload({ slot: null, file: null, preview: null, uploading: false, error: null, success: `${slot} poster updated.` });
      } catch (err) {
        setCarouselUpload((s) => ({ ...s, uploading: false, error: err.message || "Upload failed." }));
      }
    };

    return (
      <div className="carousel-admin-section">
        <p className="section-desc">
          Upload event posters for the homepage carousel. Images are served via CloudFront.
          Supported formats: JPEG, PNG, WebP (max 3 MB).
        </p>
        {carouselUpload.success && (
          <div className="form-success" style={{ marginBottom: 16 }}>{carouselUpload.success}</div>
        )}
        <div className="carousel-slots">
          {SLOTS.map(({ key: slot, label, description }) => {
            const current = carouselConfig[slot];
            const isPreviewing = carouselUpload.slot === slot;
            return (
              <div key={slot} className="carousel-slot-card">
                <div className="carousel-slot-header">
                  <div>
                    <strong>{label}</strong>
                    <span className="carousel-slot-desc">{description}</span>
                  </div>
                  {current?.updatedAt && (
                    <span className="carousel-slot-updated">
                      Updated {new Date(current.updatedAt).toLocaleDateString()}
                    </span>
                  )}
                </div>

                {/* Current / preview image */}
                <div className="carousel-slot-preview">
                  {isPreviewing && carouselUpload.preview ? (
                    <img src={carouselUpload.preview} alt={`Preview of new ${label}`} />
                  ) : current?.imageUrl ? (
                    <img src={current.imageUrl} alt={`Current ${label}`} />
                  ) : (
                    <div className="carousel-slot-empty">No image uploaded</div>
                  )}
                </div>

                {/* Actions */}
                {isPreviewing ? (
                  <div className="carousel-slot-actions">
                    {carouselUpload.error && (
                      <p className="field-error">{carouselUpload.error}</p>
                    )}
                    <div className="carousel-slot-action-row">
                      <button
                        type="button"
                        className="button primary"
                        onClick={handleConfirmUpload}
                        disabled={carouselUpload.uploading}
                      >
                        {carouselUpload.uploading ? "Uploading…" : "Confirm & Publish"}
                      </button>
                      <button
                        type="button"
                        className="button secondary"
                        onClick={handleCancel}
                        disabled={carouselUpload.uploading}
                      >
                        Cancel
                      </button>
                    </div>
                  </div>
                ) : (
                  <label className="carousel-slot-upload-btn button secondary">
                    Choose new image
                    <input
                      type="file"
                      accept="image/jpeg,image/png,image/webp"
                      style={{ display: "none" }}
                      onChange={(e) => handleFileSelect(slot, e.target.files?.[0] ?? null)}
                      disabled={carouselUpload.uploading}
                    />
                  </label>
                )}
              </div>
            );
          })}
        </div>
      </div>
    );
  };

  const partnerStats = {
    totalPartners: partners.length,
    activePartners: partners.filter((partner) => partner.isActive !== false).length,
    scopedPartners: partners.filter((partner) => partner.scope && partner.scope !== "all").length,
  };

  const renderPartnersSection = () => (
    <>
      <div className="admin-stats partner-stats">
        <div className="stat-box">
          <div className="stat-value">{partnerStats.totalPartners}</div>
          <div className="stat-label">Partners</div>
        </div>
        <div className="stat-box">
          <div className="stat-value">{partnerStats.activePartners}</div>
          <div className="stat-label">Active</div>
        </div>
        <div className="stat-box">
          <div className="stat-value">{partnerStats.scopedPartners}</div>
          <div className="stat-label">Event Specific</div>
        </div>
      </div>

      {/* Public sponsors page display mode */}
      <div className="partners-display-toggle">
        <span className="partners-display-label">Public sponsors page:</span>
        <div className="toggle-group" role="group" aria-label="Sponsors page display mode">
          <button
            type="button"
            className={`toggle-option${partnersDisplayMode === "categorized" ? " active" : ""}`}
            onClick={() => handleSetDisplayMode("categorized")}
            disabled={savingDisplayMode}
          >
            Categorized by Tier
          </button>
          <button
            type="button"
            className={`toggle-option${partnersDisplayMode === "flat" ? " active" : ""}`}
            onClick={() => handleSetDisplayMode("flat")}
            disabled={savingDisplayMode}
          >
            All Sponsors (flat)
          </button>
        </div>
      </div>

      <div className="partner-management-grid">
        <form className="partner-panel partner-form-panel" onSubmit={handleSavePartner}>
          <div className="panel-heading">
            <div>
              <h3>{editingPartnerId ? "Edit Partner" : "Add Partner"}</h3>
              <p>Manage website links, display order, logos, and whether the partner is visible.</p>
            </div>
            <button type="button" className="button secondary" onClick={resetPartnerForm}>
              Clear
            </button>
          </div>

          <div className="field-row">
            <div className="field">
              <label htmlFor="partnerName">Partner Name</label>
              <input
                id="partnerName"
                name="name"
                type="text"
                value={partnerForm.name}
                onChange={handlePartnerInputChange}
                placeholder="Partner name"
                disabled={isSavingPartner}
              />
            </div>
            <div className="field">
              <label htmlFor="partnerWebsiteUrl">Website URL</label>
              <input
                id="partnerWebsiteUrl"
                name="websiteUrl"
                type="url"
                value={partnerForm.websiteUrl}
                onChange={handlePartnerInputChange}
                placeholder="https://example.com"
                disabled={isSavingPartner}
              />
            </div>
          </div>

          <div className="field-row">
            <div className="field">
              <label htmlFor="partnerLogoUrl">Logo URL</label>
              <input
                id="partnerLogoUrl"
                name="logoUrl"
                type="url"
                value={partnerForm.logoUrl}
                onChange={handlePartnerInputChange}
                placeholder="https://example.com/logo.png"
                disabled={isSavingPartner}
              />
            </div>
            <div className="field">
              <label htmlFor="partnerTier">Sponsor Tier</label>
              <select
                id="partnerTier"
                name="tier"
                value={partnerForm.tier}
                onChange={handlePartnerInputChange}
                disabled={isSavingPartner}
              >
                {PARTNER_TIER_OPTIONS.map((option) => (
                  <option key={option.value} value={option.value}>
                    {option.label}
                  </option>
                ))}
              </select>
            </div>
            <div className="field">
              <label htmlFor="partnerScope">Visible On</label>
              <select
                id="partnerScope"
                name="scope"
                value={partnerForm.scope}
                onChange={handlePartnerInputChange}
                disabled={isSavingPartner}
              >
                {PARTNER_SCOPE_OPTIONS.map((option) => (
                  <option key={option.value} value={option.value}>
                    {option.label}
                  </option>
                ))}
              </select>
            </div>
          </div>

          <div className="field-row">
            <div className="field">
              <label htmlFor="partnerDisplayOrder">Display Order</label>
              <input
                id="partnerDisplayOrder"
                name="displayOrder"
                type="number"
                min="0"
                value={partnerForm.displayOrder}
                onChange={handlePartnerInputChange}
                disabled={isSavingPartner}
              />
            </div>
            <div className="field partner-checkbox-field">
              <label htmlFor="partnerIsActive">Partner Visible</label>
              <label className="toggle-row" htmlFor="partnerIsActive">
                <input
                  id="partnerIsActive"
                  name="isActive"
                  type="checkbox"
                  checked={partnerForm.isActive}
                  onChange={handlePartnerInputChange}
                  disabled={isSavingPartner}
                />
                <span>{partnerForm.isActive ? "Shown on platform" : "Hidden from platform"}</span>
              </label>
            </div>
          </div>

          <div className="field full-width-field">
            <label htmlFor="partnerDescription">Description</label>
            <textarea
              id="partnerDescription"
              name="description"
              value={partnerForm.description}
              onChange={handlePartnerInputChange}
              placeholder="Short note about the partner or offer"
              rows={4}
              disabled={isSavingPartner}
            />
          </div>

          {partnerError ? <div className="error-banner">{partnerError}</div> : null}
          {partnerSuccess ? <div className="success-banner">{partnerSuccess}</div> : null}

          <div className="change-password-actions partner-actions-row">
            <button type="submit" className="button primary" disabled={isSavingPartner}>
              {isSavingPartner ? "Saving..." : editingPartnerId ? "Update Partner" : "Add Partner"}
            </button>
            <button type="button" className="button secondary" onClick={loadPartners} disabled={isLoadingPartners}>
              {isLoadingPartners ? "Refreshing..." : "Refresh List"}
            </button>
          </div>
        </form>

        <div className="partner-panel partner-list-panel">
          <div className="panel-heading">
            <div>
              <h3>Partner Directory</h3>
              <p>Keep a live catalog of organizations, site links, and where they appear.</p>
            </div>
          </div>

          {isLoadingPartners ? (
            <div className="loading">Loading partners...</div>
          ) : partners.length === 0 ? (
            <div className="empty-state">No partners yet</div>
          ) : (
            <div className="partner-list">
              {partners.map((partner) => (
                <article className={`partner-card ${partner.isActive === false ? "partner-card-inactive" : ""}`} key={partner.partnerId}>
                  <div className="partner-card-top">
                    <div>
                      <h4>{partner.name}</h4>
                      <p>{partner.description || "No description added yet."}</p>
                    </div>
                    <span className={`partner-status ${partner.isActive === false ? "inactive" : "active"}`}>
                      {partner.isActive === false ? "Hidden" : "Visible"}
                    </span>
                  </div>

                  <div className="partner-meta">
                    <span className={`tier-badge tier-badge--${partner.tier || "bronze"}`}>
                      {PARTNER_TIER_OPTIONS.find((o) => o.value === (partner.tier || "bronze"))?.label || "Bronze"}
                    </span>
                    <span>Order {partner.displayOrder ?? 0}</span>
                    <span>{PARTNER_SCOPE_OPTIONS.find((option) => option.value === partner.scope)?.label || "All events"}</span>
                  </div>

                  <a className="partner-link" href={partner.websiteUrl} target="_blank" rel="noreferrer">
                    Visit website
                  </a>

                  {partner.logoUrl ? (
                    <div className="partner-logo-row">
                      <img src={partner.logoUrl} alt={`${partner.name} logo`} />
                    </div>
                  ) : null}

                  <div className="partner-card-actions">
                    <button className="button secondary" type="button" onClick={() => handleEditPartner(partner)}>
                      Edit
                    </button>
                    <button className="button secondary" type="button" onClick={() => handleDeletePartner(partner.partnerId)}>
                      Delete
                    </button>
                  </div>
                </article>
              ))}
            </div>
          )}
        </div>
      </div>
    </>
  );

  const eventStats = {
    totalEvents: events.length,
    activeEvents: events.filter((evt) => evt.status === "active").length,
    archivedEvents: events.filter((evt) => evt.status === "archived").length,
  };

  const renderEventsSection = () => (
    <>
      <div className="admin-stats partner-stats">
        <div className="stat-box">
          <div className="stat-value">{eventStats.totalEvents}</div>
          <div className="stat-label">Events</div>
        </div>
        <div className="stat-box">
          <div className="stat-value">{eventStats.activeEvents}</div>
          <div className="stat-label">Active</div>
        </div>
        <div className="stat-box">
          <div className="stat-value">{eventStats.archivedEvents}</div>
          <div className="stat-label">Archived</div>
        </div>
      </div>

      <div className="partner-management-grid">
        <form className="partner-panel partner-form-panel" onSubmit={handleSaveEvent}>
          <div className="panel-heading">
            <div>
              <h3>{editingEventType ? "Edit Event" : "Add Event"}</h3>
              <p>Only events with status “Active” accept public registrations.</p>
            </div>
            <button type="button" className="button secondary" onClick={resetEventForm}>
              Clear
            </button>
          </div>

          <div className="field-row">
            <div className="field">
              <label htmlFor="eventType">Event ID</label>
              <input
                id="eventType"
                name="eventType"
                type="text"
                value={eventForm.eventType}
                onChange={handleEventInputChange}
                placeholder="e.g. onam_2026"
                disabled={isSavingEvent || !!editingEventType}
              />
              {editingEventType ? <span className="field-note">ID can’t be changed after creation.</span> : null}
            </div>
            <div className="field">
              <label htmlFor="eventHostId">Host ID</label>
              <input
                id="eventHostId"
                name="hostId"
                type="text"
                value={eventForm.hostId}
                onChange={handleEventInputChange}
                placeholder="wmfjapan"
                disabled={isSavingEvent}
              />
            </div>
          </div>

          <div className="field-row">
            <div className="field">
              <label htmlFor="eventRegistrationClosesOn">Registration Closes On</label>
              <input
                id="eventRegistrationClosesOn"
                name="registrationClosesOn"
                type="date"
                value={eventForm.registrationClosesOn}
                onChange={handleEventInputChange}
                disabled={isSavingEvent}
              />
            </div>
            <div className="field">
              <label htmlFor="eventCancellationEnabled">User Cancellation</label>
              <select
                id="eventCancellationEnabled"
                name="cancellationEnabled"
                value={eventForm.cancellationEnabled ? "enabled" : "disabled"}
                onChange={(event) => setEventForm((previous) => ({
                  ...previous,
                  cancellationEnabled: event.target.value === "enabled",
                }))}
                disabled={isSavingEvent}
              >
                <option value="enabled">Enabled</option>
                <option value="disabled">Disabled</option>
              </select>
              <span className="field-note">Disabled registrations can only be cancelled by an administrator.</span>
            </div>
            <div className="field">
              <label htmlFor="eventSadhyaCapacity">Sadhya Capacity</label>
              <input
                id="eventSadhyaCapacity"
                name="sadhyaCapacity"
                type="number"
                min="0"
                value={eventForm.sadhyaCapacity}
                onChange={handleEventInputChange}
                disabled={isSavingEvent}
              />
              <span className="field-note">Shared by Onam-only and combo registrations. Leave blank when not applicable.</span>
            </div>
            <div className="field">
              <label htmlFor="eventSadhyaSlotStartDate">Sadhya Slot Booking Opens</label>
              <input
                id="eventSadhyaSlotStartDate"
                name="sadhyaSlotStartDate"
                type="date"
                value={eventForm.sadhyaSlotStartDate}
                onChange={handleEventInputChange}
                disabled={isSavingEvent}
              />
              <span className="field-note">Leave blank to let registrants pick a Sadhya slot as soon as they're paid.</span>
            </div>
            <div className="field">
              <label htmlFor="eventSadhyaSlotEndDate">Sadhya Slot Booking Closes</label>
              <input
                id="eventSadhyaSlotEndDate"
                name="sadhyaSlotEndDate"
                type="date"
                value={eventForm.sadhyaSlotEndDate}
                onChange={handleEventInputChange}
                disabled={isSavingEvent}
              />
              <span className="field-note">
                Leave blank to keep slot selection open indefinitely. Doesn't affect a slot already picked.
              </span>
            </div>
            <div className="field">
              <label htmlFor="eventCelebrityCapacity">Celebrity Capacity</label>
              <input
                id="eventCelebrityCapacity"
                name="celebrityCapacity"
                type="number"
                min="0"
                value={eventForm.celebrityCapacity}
                onChange={handleEventInputChange}
                disabled={isSavingEvent}
              />
              <span className="field-note">Shared by celebrity-only and combo registrations. Leave blank when not applicable.</span>
            </div>
          </div>

          <div className="field-row">
            <div className="field">
              <label htmlFor="eventOnamRegistrationClosed">Onam Registration</label>
              <select
                id="eventOnamRegistrationClosed"
                name="onamRegistrationClosed"
                value={eventForm.onamRegistrationClosed ? "closed" : "open"}
                onChange={(event) => setEventForm((previous) => ({
                  ...previous,
                  onamRegistrationClosed: event.target.value === "closed",
                }))}
                disabled={isSavingEvent}
              >
                <option value="open">Open</option>
                <option value="closed">Closed</option>
              </select>
              <span className="field-note">
                Closing stops Onam-only and combo registrations regardless of remaining Sadhya seats.
              </span>
            </div>
            <div className="field">
              <label htmlFor="eventCelebrityRegistrationClosed">Celebrity Event Registration</label>
              <select
                id="eventCelebrityRegistrationClosed"
                name="celebrityRegistrationClosed"
                value={eventForm.celebrityRegistrationClosed ? "closed" : "open"}
                onChange={(event) => setEventForm((previous) => ({
                  ...previous,
                  celebrityRegistrationClosed: event.target.value === "closed",
                }))}
                disabled={isSavingEvent}
              >
                <option value="open">Open</option>
                <option value="closed">Closed</option>
              </select>
              <span className="field-note">
                Closing stops celebrity-only and combo registrations regardless of remaining seats.
              </span>
            </div>
          </div>

          <div className="field-row">
            <div className="field">
              <label htmlFor="eventName">Event Name</label>
              <input
                id="eventName"
                name="eventName"
                type="text"
                value={eventForm.eventName}
                onChange={handleEventInputChange}
                placeholder="Onam Celebration 2026"
                disabled={isSavingEvent}
              />
            </div>
            <div className="field">
              <label htmlFor="eventStatus">Status</label>
              <select
                id="eventStatus"
                name="status"
                value={eventForm.status}
                onChange={handleEventInputChange}
                disabled={isSavingEvent}
              >
                {EVENT_STATUS_OPTIONS.map((option) => (
                  <option key={option.value} value={option.value}>
                    {option.label}
                  </option>
                ))}
              </select>
            </div>
          </div>

          <div className="field-row">
            <div className="field">
              <label htmlFor="eventDate">Event Date</label>
              <input
                id="eventDate"
                name="eventDate"
                type="date"
                value={eventForm.eventDate}
                onChange={handleEventInputChange}
                disabled={isSavingEvent}
              />
            </div>
            <div className="field">
              <label htmlFor="eventCapacity">Capacity</label>
              <input
                id="eventCapacity"
                name="capacity"
                type="number"
                min="0"
                value={eventForm.capacity}
                onChange={handleEventInputChange}
                disabled={isSavingEvent}
              />
            </div>
          </div>

          <div className="field-row">
            <div className="field">
              <label htmlFor="eventVenue">Venue</label>
              <input
                id="eventVenue"
                name="venue"
                type="text"
                value={eventForm.venue}
                onChange={handleEventInputChange}
                placeholder="Venue name / address"
                disabled={isSavingEvent}
              />
            </div>
            <div className="field">
              <label htmlFor="eventVenueAddress">Venue Address</label>
              <input
                id="eventVenueAddress"
                name="venueAddress"
                type="text"
                value={eventForm.venueAddress}
                onChange={handleEventInputChange}
                placeholder="Full venue address"
                disabled={isSavingEvent}
              />
            </div>
          </div>

          <div className="field">
            <label htmlFor="eventRegistrationPath">Registration Path</label>
            <input
              id="eventRegistrationPath"
              name="registrationPath"
              type="text"
              value={eventForm.registrationPath}
              onChange={handleEventInputChange}
              placeholder="/onam/register"
              disabled={isSavingEvent}
            />
          </div>

          <div className="field full-width-field">
            <label>Payment Links (included in confirmation email)</label>
            <div className="field-row">
              <div className="field">
                <label htmlFor="eventPaymentLinkCredit">Credit Card URL</label>
                <input
                  id="eventPaymentLinkCredit"
                  name="paymentLinkCredit"
                  type="url"
                  value={eventForm.paymentLinkCredit}
                  onChange={handleEventInputChange}
                  placeholder="https://..."
                  disabled={isSavingEvent}
                />
              </div>
              <div className="field">
                <label htmlFor="eventPaymentLinkPayPal">PayPay URL</label>
                <input
                  id="eventPaymentLinkPayPal"
                  name="paymentLinkPayPal"
                  type="url"
                  value={eventForm.paymentLinkPayPal}
                  onChange={handleEventInputChange}
                  placeholder="https://..."
                  disabled={isSavingEvent}
                />
              </div>
            </div>
            <div className="field">
              <label htmlFor="eventPaymentLinkBank">Bank Transfer URL</label>
              <input
                id="eventPaymentLinkBank"
                name="paymentLinkBank"
                type="url"
                value={eventForm.paymentLinkBank}
                onChange={handleEventInputChange}
                placeholder="https://..."
                disabled={isSavingEvent}
              />
            </div>
            <span className="field-note">
              Leave fields blank to remove event payment links. These URLs are shown in registration confirmation emails.
            </span>
          </div>

          <div className="section-label" style={{ marginTop: "8px" }}>Event Plugins</div>

          <div className={`field full-width-field plugin-section${eventForm.seatSelectionEnabled ? " enabled" : ""}`}>
            <PluginSectionHeader
              icon="🪑"
              title="Celebrity Event Seat Selection"
              enabled={eventForm.seatSelectionEnabled}
            />
            <div className="field">
              <label htmlFor="eventSeatSelectionEnabled">Status</label>
              <select
                id="eventSeatSelectionEnabled"
                value={eventForm.seatSelectionEnabled ? "enabled" : "disabled"}
                onChange={(e) => setEventForm((prev) => ({ ...prev, seatSelectionEnabled: e.target.value === "enabled" }))}
                disabled={isSavingEvent}
              >
                <option value="disabled">Disabled</option>
                <option value="enabled">Enabled</option>
              </select>
              <span className="field-note">
                When disabled, the Seat Selection admin tab and the registrant-facing seat picker on My
                Registrations are both hidden for this event's Celebrity Event/Combo registrants. Once enabled,
                manage its booking window, pricing, and payment methods from the Seat Selection tab.
              </span>
            </div>
          </div>

          <div className={`field full-width-field plugin-section${eventForm.seatUpgradeEnabled ? " enabled" : ""}`}>
            <PluginSectionHeader icon="⬆️" title="Zone Upgrade" enabled={eventForm.seatUpgradeEnabled} />
            <div className="field">
              <label htmlFor="eventSeatUpgradeEnabled">Status</label>
              <select
                id="eventSeatUpgradeEnabled"
                name="seatUpgradeEnabled"
                value={eventForm.seatUpgradeEnabled ? "enabled" : "disabled"}
                onChange={(event) => setEventForm((previous) => ({
                  ...previous,
                  seatUpgradeEnabled: event.target.value === "enabled",
                }))}
                disabled={isSavingEvent}
              >
                <option value="disabled">Disabled</option>
                <option value="enabled">Enabled</option>
              </select>
              <span className="field-note">
                When disabled, the Zone Upgrades admin tab and the registrant-facing upgrade page are both hidden for this event.
              </span>
            </div>

            {eventForm.seatUpgradeEnabled ? (
              <>
                <div className="field" style={{ marginTop: "10px" }}>
                  <label htmlFor="eventSeatUpgradeUrl">Zone Upgrade URL</label>
                  <input
                    id="eventSeatUpgradeUrl"
                    name="seatUpgradeUrl"
                    type="text"
                    value={eventForm.seatUpgradeUrl}
                    onChange={handleEventInputChange}
                    placeholder="/my/seat-upgrade (default) or https://..."
                    disabled={isSavingEvent}
                  />
                  <span className="field-note">
                    Where the "Upgrade My Zone" button in My Registrations links to. Leave blank to use the
                    built-in zone upgrade page; set an external URL to point elsewhere instead.
                  </span>
                </div>

                <div className="field-row">
                  <div className="field">
                    <label htmlFor="eventSeatUpgradeStartDate">Booking Opens</label>
                    <input
                      id="eventSeatUpgradeStartDate"
                      name="seatUpgradeStartDate"
                      type="date"
                      value={eventForm.seatUpgradeStartDate}
                      onChange={handleEventInputChange}
                      disabled={isSavingEvent}
                    />
                    <span className="field-note">Leave blank to open as soon as the plugin is enabled.</span>
                  </div>
                  <div className="field">
                    <label htmlFor="eventSeatUpgradeEndDate">Booking Closes</label>
                    <input
                      id="eventSeatUpgradeEndDate"
                      name="seatUpgradeEndDate"
                      type="date"
                      value={eventForm.seatUpgradeEndDate}
                      onChange={handleEventInputChange}
                      disabled={isSavingEvent}
                    />
                    <span className="field-note">Leave blank to stay open indefinitely.</span>
                  </div>
                </div>

                <div className="field">
                  <label htmlFor="eventSeatMapImage">Seat Map Image (required)</label>
                  {eventForm.seatUpgradeSeatMapImageUrl ? (
                    <div style={{ marginBottom: "8px" }}>
                      <img
                        src={eventForm.seatUpgradeSeatMapImageUrl}
                        alt="Seat map preview"
                        style={{ maxWidth: "280px", maxHeight: "200px", display: "block", borderRadius: "4px" }}
                      />
                    </div>
                  ) : null}
                  <label className="button secondary" style={{ display: "inline-block", cursor: "pointer" }}>
                    {seatMapUpload.uploading
                      ? "Uploading…"
                      : eventForm.seatUpgradeSeatMapImageUrl
                        ? "Replace image"
                        : "Choose image"}
                    <input
                      id="eventSeatMapImage"
                      type="file"
                      accept="image/jpeg,image/png,image/webp"
                      style={{ display: "none" }}
                      onChange={(e) => handleSeatMapFileSelect(e.target.files?.[0] ?? null)}
                      disabled={isSavingEvent || seatMapUpload.uploading}
                    />
                  </label>
                  {seatMapUpload.error ? <p className="field-error">{seatMapUpload.error}</p> : null}
                  <span className="field-note">
                    Shown to registrants on the zone upgrade page so they can see which seats each zone refers to.
                    Required to enable this plugin. JPEG, PNG, or WebP, max 5 MB.
                  </span>
                </div>

                <div className="field">
                  <label>Payment Methods</label>
                  <div className="btn-row">
                    {SEAT_UPGRADE_PAYMENT_METHODS.map((method) => (
                      <label key={method.key} style={{ display: "flex", alignItems: "center", gap: "6px", fontWeight: 400 }}>
                        <input
                          type="checkbox"
                          checked={!!eventForm[method.formKey]}
                          onChange={(event) => setEventForm((previous) => ({
                            ...previous,
                            [method.formKey]: event.target.checked,
                          }))}
                          disabled={isSavingEvent}
                        />
                        {method.label}
                      </label>
                    ))}
                  </div>
                  <span className="field-note">Which payment methods registrants can use to pay for a zone upgrade on this event.</span>
                </div>

                <p className="field-note" style={{ marginTop: "10px" }}>
                  Registrants can also bundle the Dinner with Celebrity into a Zone Upgrade, at a discount — but that
                  add-on's price, discount, and availability are all configured from the Dinner with Celebrity tab below,
                  not here. Turn on "Available as a Zone Upgrade add-on" there once the Dinner with Celebrity itself is
                  enabled for this event.
                </p>
              </>
            ) : null}
          </div>

          <div className={`field full-width-field plugin-section${eventForm.celebrityDinnerEnabled ? " enabled" : ""}`}>
            <PluginSectionHeader icon="🍽️" title="Dinner with Celebrity" enabled={eventForm.celebrityDinnerEnabled} />
            <div className="field">
              <label htmlFor="eventCelebrityDinnerEnabled">Status</label>
              <select
                id="eventCelebrityDinnerEnabled"
                name="celebrityDinnerEnabled"
                value={eventForm.celebrityDinnerEnabled ? "enabled" : "disabled"}
                onChange={(event) => setEventForm((previous) => ({
                  ...previous,
                  celebrityDinnerEnabled: event.target.value === "enabled",
                }))}
                disabled={isSavingEvent}
              >
                <option value="disabled">Disabled</option>
                <option value="enabled">Enabled</option>
              </select>
              <span className="field-note">
                When disabled, the Dinner with Celebrity admin tab and the registrant-facing sign-up on My Registrations
                are both hidden for this event. Once enabled, manage its registration window, venue, price, and
                conditions from the Dinner with Celebrity tab.
              </span>
            </div>
          </div>

          <div className="field full-width-field">
            <label htmlFor="eventDescription">Description</label>
            <textarea
              id="eventDescription"
              name="description"
              value={eventForm.description}
              onChange={handleEventInputChange}
              placeholder="Short description of the event"
              rows={4}
              disabled={isSavingEvent}
            />
          </div>

          <div className="field full-width-field">
            <label htmlFor="eventNote">Event Note</label>
            <textarea
              id="eventNote"
              name="eventNote"
              value={eventForm.eventNote}
              onChange={handleEventInputChange}
              placeholder="Onam falls within the current NPO membership year; the Event falls in the next membership year."
              rows={3}
              disabled={isSavingEvent}
            />
          </div>

          {eventError ? <div className="error-banner">{eventError}</div> : null}
          {eventSuccess ? <div className="success-banner">{eventSuccess}</div> : null}

          <div className="change-password-actions partner-actions-row">
            <button type="submit" className="button primary" disabled={isSavingEvent}>
              {isSavingEvent ? "Saving..." : editingEventType ? "Update Event" : "Add Event"}
            </button>
            <button type="button" className="button secondary" onClick={loadEvents} disabled={isLoadingEvents}>
              {isLoadingEvents ? "Refreshing..." : "Refresh List"}
            </button>
          </div>
        </form>

        <div className="partner-panel partner-list-panel">
          <div className="panel-heading">
            <div>
              <h3>Event Directory</h3>
              <p>All configured events. Manage each event’s registrations from the Registrations tab.</p>
            </div>
          </div>

          {isLoadingEvents ? (
            <div className="loading">Loading events...</div>
          ) : events.length === 0 ? (
            <div className="empty-state">No events yet</div>
          ) : (
            <div className="partner-list">
              {events.map((evt) => {
                const paymentLinks = getPaymentLinksFromEventInfo(evt.eventInfo);
                const linkEntries = [
                  ["Credit", paymentLinks.credit],
                  ["PayPay", paymentLinks.paypay],
                  ["Bank", paymentLinks.bank],
                ];
                const configuredCount = linkEntries.filter(([, url]) => !!url).length;
                const seatUpgrade = getSeatUpgradeConfigFromEventInfo(evt.eventInfo);

                return (
                  <article className={`partner-card ${evt.status !== "active" ? "partner-card-inactive" : ""}`} key={evt.eventType}>
                    <div className="partner-card-top">
                      <div>
                        <h4>{evt.eventName}</h4>
                        <p>{evt.description || "No description added yet."}</p>
                      </div>
                      <span className={`partner-status ${evt.status === "active" ? "active" : "inactive"}`}>
                        {evt.status}
                      </span>
                    </div>

                    <div className="partner-meta">
                      <span>ID: {evt.eventType}</span>
                      <span>{evt.eventDate ? formatEventDate(evt.eventDate) : "Unscheduled"}</span>
                      {evt.capacity ? <span>Cap {evt.capacity}</span> : null}
                    </div>

                    {evt.venue ? <div className="partner-meta"><span>{evt.venue}</span></div> : null}

                    <div className="partner-meta">
                      <span>Payment links: {configuredCount}/3 configured</span>
                      {linkEntries.map(([label, url]) => (
                        url ? (
                          <a key={label} href={url} target="_blank" rel="noreferrer">
                            {label}
                          </a>
                        ) : (
                          <span key={label}>{label}: Missing</span>
                        )
                      ))}
                    </div>

                    <div className="partner-meta">
                      <span>
                        Zone upgrade: {seatUpgrade.enabled ? `Enabled (${seatUpgrade.paymentMethods.join(", ")})` : "Disabled"}
                        {seatUpgrade.enabled && (seatUpgrade.startDate || seatUpgrade.endDate)
                          ? ` · ${seatUpgrade.startDate || "any time"} – ${seatUpgrade.endDate || "no end date"}`
                          : ""}
                      </span>
                    </div>

                    <div className="partner-card-actions">
                      <button className="button secondary" type="button" onClick={() => handleEditEvent(evt)}>
                        Edit
                      </button>
                      <button
                        className="button secondary"
                        type="button"
                        onClick={() => {
                          setSelectedEventType(evt.eventType);
                          setActiveSection("registrations");
                        }}
                      >
                        View Registrations
                      </button>
                      <button className="button secondary" type="button" onClick={() => handleDeleteEvent(evt.eventType)}>
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

  const filteredMembers = members.filter((member) => {
    const statusFilter = NPO_STATUS_FILTERS.find((f) => f.value === memberNpoFilter);
    const waFilter = WHATSAPP_FILTERS.find((f) => f.value === memberNpoFilter);
    if (statusFilter && !statusFilter.test(member)) return false;
    if (waFilter && waFilter.supported?.(waYear) !== false && !waFilter.test(member, waYear)) return false;
    const search = memberSearch.toLowerCase();
    if (!search) return true;
    return (
      (member.name?.toLowerCase() || "").includes(search) ||
      (member.email?.toLowerCase() || "").includes(search)
    );
  });

  const memberStats = {
    total: members.length,
    active: members.filter((m) => m.isMember).length,
    renewed: members.filter((m) => m.renewedForNextYear).length,
  };

  const formatActivePeriod = (period) => {
    if (!period || (!period.from && !period.to)) return "—";
    return `${formatEventDate(period.from) || "?"} – ${formatEventDate(period.to) || "?"}`;
  };

  const renderMembersSection = () => (
    <>
      <div className="admin-stats">
        <div className="stat-box">
          <div className="stat-value">{memberStats.total}</div>
          <div className="stat-label">Members</div>
        </div>
        <div className="stat-box">
          <div className="stat-value">{memberStats.active}</div>
          <div className="stat-label">Active NPO Members</div>
        </div>
        <div className="stat-box">
          <div className="stat-value">{memberStats.renewed}</div>
          <div className="stat-label">Renewed (next year)</div>
        </div>
      </div>

      <MemberAnnouncements />

      <div className="admin-controls">
        <input
          type="text"
          placeholder="Search by name or email..."
          value={memberSearch}
          onChange={(e) => setMemberSearch(e.target.value)}
          className="search-input"
        />
        <select
          value={memberNpoFilter}
          onChange={(e) => setMemberNpoFilter(e.target.value)}
          className="search-input"
          style={{ maxWidth: 320 }}
          aria-label="Filter members by NPO status or WhatsApp group"
        >
          {NPO_STATUS_FILTER_GROUPS.map((group) => (
            <optgroup key={group.label} label={group.label}>
              {NPO_STATUS_FILTERS.filter((f) => f.group === group.label).map((f) => (
                <option key={f.value} value={f.value}>{f.label}</option>
              ))}
            </optgroup>
          ))}
          <optgroup label={`WhatsApp group (${yearLabel(waYear)})`}>
            {WHATSAPP_FILTERS.filter((f) => f.supported?.(waYear) !== false).map((f) => (
              <option key={f.value} value={f.value}>{f.label(waYear)}</option>
            ))}
          </optgroup>
        </select>
        <select
          value={waYear}
          onChange={(e) => setWaYearChoice(e.target.value)}
          className="search-input"
          style={{ maxWidth: 190 }}
          aria-label="WhatsApp group year"
          title="Year shown in the WhatsApp group column, its filters and the CSV export"
        >
          {waYears.map((y) => (
            <option key={y} value={y}>WhatsApp group {yearLabel(y)}</option>
          ))}
        </select>
        <button className="button primary" onClick={openAddMember}>+ Add Member</button>
        {memberSearch || memberNpoFilter !== "all" ? (
          <>
            <button className="button secondary" onClick={() => exportMembers(filteredMembers, true)} disabled={!filteredMembers.length}>
              ⬇ Export filtered ({filteredMembers.length})
            </button>
            <button className="button secondary" onClick={() => exportMembers(members, false)} disabled={!members.length}>
              ⬇ Export all ({members.length})
            </button>
          </>
        ) : (
          <button className="button secondary" onClick={() => exportMembers(members, false)} disabled={!members.length}>
            ⬇ Export CSV ({members.length})
          </button>
        )}
        <button className="button secondary" onClick={loadMembers} disabled={isLoadingMembers}>
          {isLoadingMembers ? "Loading..." : "🔄 Refresh"}
        </button>
      </div>

      {memberError && <div className="error-banner">{memberError}</div>}
      {memberSuccess && <div className="success-banner">{memberSuccess}</div>}

      {isLoadingMembers ? (
        <div className="loading">Loading members...</div>
      ) : filteredMembers.length === 0 ? (
        <div className="empty-state">
          {memberSearch || memberNpoFilter !== "all" ? "No matches found" : "No members yet"}
        </div>
      ) : (
        <div className="table-container">
          {memberSearch || memberNpoFilter !== "all" ? (
            <p className="field-note" style={{ margin: "0 0 8px" }}>
              Showing {filteredMembers.length} of {members.length} members
            </p>
          ) : null}
          <table className="registrations-table">
            <thead>
              <tr>
                <th>Name</th>
                <th>Email</th>
                <th>Phone</th>
                <th>NPO Status</th>
                <th>Last Active Period</th>
                <th>WhatsApp group<br /><small>{yearLabel(waYear)}</small></th>
                <th>Actions</th>
              </tr>
            </thead>
            <tbody>
              {filteredMembers.map((member) => (
                <tr key={member.email}>
                  <td><strong>{member.name || "—"}</strong></td>
                  <td>{member.email}</td>
                  <td>{member.phone || "—"}</td>
                  <td>
                    {[
                      ["2025–26", member.npoStatus2025],
                      ["2026–27", member.npoStatus2026],
                    ].map(([label, value]) => (
                      <div key={label} style={{ marginBottom: 4, whiteSpace: "nowrap" }}>
                        <small>{label}</small>{" "}
                        <span className={`npo-badge ${value && value !== "Non-member" ? "npo-member" : "npo-nonmember"}`}>
                          {value || "Non-member"}
                        </span>
                      </div>
                    ))}
                  </td>
                  <td>{formatActivePeriod(member.lastActivePeriod)}</td>
                  <td>
                    <select
                      value={whatsappStatus(member, waYear)}
                      onChange={(e) => handleWhatsappStatus(member, e.target.value)}
                      disabled={whatsappSaving === member.email}
                      aria-label={`WhatsApp group status for ${member.name || member.email}`}
                      className={`wa-status wa-status--${whatsappStatus(member, waYear) || "unset"}`}
                    >
                      <option value="">Not recorded</option>
                      <option value="joined">Joined</option>
                      <option value="not_joined">Not joined</option>
                    </select>
                  </td>
                  <td>
                    <div className="row-actions">
                      <button className="button secondary small" onClick={() => openEditMember(member)}>Edit</button>
                      <button className="button secondary small" onClick={() => openNpoPanel(member)}>NPO</button>
                      <button className="button danger small" onClick={() => handleDeleteMember(member.email)}>Delete</button>
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </>
  );

  const renderMemberModal = () => (
    <div className="admin-modal-overlay" role="dialog" aria-modal="true">
      <div className="admin-modal">
        <form onSubmit={handleSaveMember}>
          <div className="panel-heading">
            <div>
              <h3>{editingMemberEmail ? "Edit Member" : "Add Member"}</h3>
            </div>
            <button type="button" className="button secondary" onClick={() => setShowMemberForm(false)}>
              Close
            </button>
          </div>

          <div className="field-row">
            <div className="field">
              <label htmlFor="mEmail">Email *</label>
              <input
                id="mEmail"
                type="email"
                value={memberForm.email}
                onChange={(e) => setMemberForm((p) => ({ ...p, email: e.target.value }))}
                disabled={!!editingMemberEmail}
                placeholder="member@email.com"
              />
            </div>
            <div className="field">
              <label htmlFor="mFullname">Full Name</label>
              <input
                id="mFullname"
                type="text"
                value={memberForm.fullname}
                onChange={(e) => setMemberForm((p) => ({ ...p, fullname: e.target.value }))}
                placeholder="Full name"
              />
            </div>
          </div>

          <div className="field-row">
            <div className="field">
              <label htmlFor="mPhone">Phone</label>
              <input
                id="mPhone"
                type="tel"
                value={memberForm.phone}
                onChange={(e) => setMemberForm((p) => ({ ...p, phone: e.target.value }))}
                placeholder="+81 90-XXXX-XXXX"
              />
            </div>
            <div className="field">
              <label htmlFor="mMemberid">Member ID</label>
              <input
                id="mMemberid"
                type="text"
                value={memberForm.memberid}
                onChange={(e) => setMemberForm((p) => ({ ...p, memberid: e.target.value }))}
                placeholder="NPO member number"
              />
            </div>
          </div>

          <div className="field">
            <label className="onam-check">
              <input
                type="checkbox"
                checked={memberForm.npoFlagActive}
                onChange={(e) => setMemberForm((p) => ({ ...p, npoFlagActive: e.target.checked }))}
              />
              <span>NPO membership active</span>
            </label>
          </div>

          {memberFormError && <div className="error-banner">{memberFormError}</div>}

          <div className="change-password-actions">
            <button type="submit" className="button primary" disabled={isSavingMember}>
              {isSavingMember ? "Saving..." : editingMemberEmail ? "Update Member" : "Add Member"}
            </button>
            <button type="button" className="button secondary" onClick={() => setShowMemberForm(false)} disabled={isSavingMember}>
              Cancel
            </button>
          </div>
        </form>
      </div>
    </div>
  );

  const renderNpoModal = () => {
    if (!npoPanel) return null;
    const txns = [...(npoPanel.transactions || [])].sort(compareNpoTxnsNewestFirst);
    return (
      <div className="admin-modal-overlay" role="dialog" aria-modal="true">
        <div className="admin-modal" style={{ maxWidth: 920, maxHeight: "92vh", overflowY: "auto" }}>
          <div className="panel-heading">
            <div>
              <h3>NPO Membership — {npoPanel.name}</h3>
              <p className="field-note">Member ID: {npoPanel.memberid} · {npoPanel.email}</p>
            </div>
            <button type="button" className="button secondary" onClick={() => { setNpoPanel(null); setShowNpoTxnForm(false); }}>
              Close
            </button>
          </div>

          {npoTxnFormError && <div className="error-banner">{npoTxnFormError}</div>}
          {npoTxnNote && <div className="success-banner">{npoTxnNote}</div>}

          {isLoadingNpoTxns ? (
            <div className="loading">Loading transactions…</div>
          ) : (
            <>
              {txns.length === 0 ? (
                <div className="empty-state">No transactions yet</div>
              ) : (
                <div className="table-container" style={{ marginBottom: 12 }}>
                  <table className="registrations-table" style={{ minWidth: 0 }}>
                    <thead>
                      <tr>
                        <th>#</th>
                        <th>From</th>
                        <th>To</th>
                        <th>Paid Date</th>
                        <th>Amount</th>
                        <th>Via</th>
                        <th>Notes</th>
                        <th>Actions</th>
                      </tr>
                    </thead>
                    <tbody>
                      {txns.map((txn) => (
                        <tr key={txn.transactionNo}>
                          <td>
                            {txn.transactionNo}
                            {txn.legacyTransactionId ? <small style={{ display: "block", opacity: 0.8 }}>legacy #{txn.legacyTransactionId}</small> : null}
                          </td>
                          <td>{txn.membDateFrom || "—"}</td>
                          <td>{txn.membDateTo || "—"}</td>
                          <td>{txn.paidDate || "—"}</td>
                          <td>{txn.membershipAmountPaid != null ? `¥${Number(txn.membershipAmountPaid).toLocaleString()}` : "—"}</td>
                          <td>
                            {txn.paidVia || "—"}
                            {txn.paymentStatus === "pending" ? (
                              <strong>
                                {" "}
                                (awaiting payment
                                {txn.paymentReportedAt ? ` · member says paid ${String(txn.paymentReportedAt).slice(0, 10)}` : ""})
                              </strong>
                            ) : null}
                            {txn.paymentStatus === "cancelled" ? (
                              <strong>
                                {" "}
                                (cancelled
                                {txn.refundStatus === "processing" ? ` · ¥${Number(txn.refundAmount).toLocaleString()} refund processing` : ""}
                                {["completed", "refunded-manually"].includes(txn.refundStatus) ? ` · ¥${Number(txn.refundAmount).toLocaleString()} refunded` : ""}
                                {txn.refundStatus === "included-in-order-refund" ? " · refunded with payer's order" : ""}
                                {txn.refundStatus === "none" ? " · no refund" : ""})
                              </strong>
                            ) : null}
                          </td>
                          <td className="npo-txn-notes">{txn.notes || "—"}</td>
                          <td>
                            <div className="row-actions">
                              {txn.paymentStatus === "pending" ? (
                                <button className="button primary small" onClick={() => handleMarkNpoTxnPaid(txn)}>Mark paid</button>
                              ) : null}
                              {!["pending", "cancelled"].includes(txn.paymentStatus) && !(txn.paidByEmail && !(txn.linked || []).length) ? (
                                <button className="button secondary small" onClick={() => openCancelTxn(txn)}>Cancel / refund</button>
                              ) : null}
                              {txn.paymentStatus !== "cancelled" ? (
                                <button className="button secondary small" onClick={() => openEditNpoTxn(txn)}>Edit</button>
                              ) : null}
                              <button className="button danger small" onClick={() => handleDeleteNpoTxn(txn)}>Delete</button>
                            </div>
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}

              {cancelTxn ? (
                <form onSubmit={handleConfirmCancelTxn} className="npo-cancel-form">
                  <div className="panel-heading" style={{ marginBottom: 8 }}>
                    <strong>Cancel transaction #{cancelTxn.transactionNo}</strong>
                    <button type="button" className="button secondary small" onClick={() => setCancelTxn(null)} disabled={isCancellingTxn}>
                      Keep membership
                    </button>
                  </div>
                  <p className="field-note">
                    Policy: memberships cancelled within 3 days of payment are accepted and refunded.
                    {cancelTxn.paidDate ? ` Paid on ${cancelTxn.paidDate.slice(0, 10)}.` : ""}
                    {(cancelTxn.linked || []).length ? " The spouse's membership in this order is cancelled too." : ""}
                  </p>
                  {cancelError ? <div className="error-banner">{cancelError}</div> : null}
                  <fieldset className="npo-cancel-options">
                    <legend>Refund</legend>
                    {cancelTxn.paidVia === "square" && cancelTxn.paymentReference ? (
                      <label>
                        <input type="radio" name="npo-refund" checked={cancelForm.refund === "square"} onChange={() => setCancelForm((p) => ({ ...p, refund: "square" }))} />{" "}
                        Refund to the member's card (Square)
                      </label>
                    ) : null}
                    <label>
                      <input type="radio" name="npo-refund" checked={cancelForm.refund === "manual"} onChange={() => setCancelForm((p) => ({ ...p, refund: "manual" }))} />{" "}
                      I have refunded (or will refund) it myself — bank / PayPay
                    </label>
                    <label>
                      <input type="radio" name="npo-refund" checked={cancelForm.refund === "none"} onChange={() => setCancelForm((p) => ({ ...p, refund: "none" }))} />{" "}
                      Cancel without a refund
                    </label>
                  </fieldset>
                  {cancelForm.refund !== "none" ? (
                    <div className="field">
                      <label>Refund amount (¥) — up to ¥{Number(cancelTxn.orderTotal || cancelTxn.membershipAmountPaid || 0).toLocaleString()}</label>
                      <input type="number" min="1" value={cancelForm.refundAmount} onChange={(e) => setCancelForm((p) => ({ ...p, refundAmount: e.target.value }))} />
                    </div>
                  ) : null}
                  <div className="field">
                    <label>Reason (optional — included in the email)</label>
                    <input type="text" value={cancelForm.reason} onChange={(e) => setCancelForm((p) => ({ ...p, reason: e.target.value }))} placeholder="e.g. Requested within 3 days of payment" />
                  </div>
                  <label className="checkbox-inline">
                    <input type="checkbox" checked={cancelForm.notify} onChange={(e) => setCancelForm((p) => ({ ...p, notify: e.target.checked }))} /> Email the member about the cancellation
                  </label>
                  <div className="change-password-actions">
                    <button type="submit" className="button danger" disabled={isCancellingTxn}>
                      {isCancellingTxn ? "Cancelling…" : "Cancel membership"}
                    </button>
                  </div>
                </form>
              ) : null}

              {!showNpoTxnForm && !cancelTxn ? (
                <button className="button primary" onClick={openAddNpoTxn}>+ Add Transaction</button>
              ) : (
                <form onSubmit={handleSaveNpoTxn} style={{ marginTop: 12 }}>
                  <div className="panel-heading" style={{ marginBottom: 8 }}>
                    <strong>{editingTxnNo ? `Edit Transaction #${editingTxnNo}` : "New Transaction"}</strong>
                    <button type="button" className="button secondary small" onClick={() => setShowNpoTxnForm(false)}>Cancel</button>
                  </div>
                  <div className="field-row">
                    <div className="field">
                      <label>From Date</label>
                      <input type="date" value={npoTxnForm.membDateFrom} onChange={(e) => setNpoTxnForm((p) => ({ ...p, membDateFrom: e.target.value }))} />
                    </div>
                    <div className="field">
                      <label>To Date</label>
                      <input type="date" value={npoTxnForm.membDateTo} onChange={(e) => setNpoTxnForm((p) => ({ ...p, membDateTo: e.target.value }))} />
                    </div>
                  </div>
                  <div className="field-row">
                    <div className="field">
                      <label>Paid Date</label>
                      <input type="date" value={npoTxnForm.paidDate} onChange={(e) => setNpoTxnForm((p) => ({ ...p, paidDate: e.target.value }))} />
                    </div>
                    <div className="field">
                      <label>Amount Paid (¥)</label>
                      <input type="number" min="0" value={npoTxnForm.membershipAmountPaid} onChange={(e) => setNpoTxnForm((p) => ({ ...p, membershipAmountPaid: e.target.value }))} placeholder="1000" />
                    </div>
                  </div>
                  <div className="field-row">
                    <div className="field">
                      <label>Paid Via</label>
                      <select value={npoTxnForm.paidVia} onChange={(e) => setNpoTxnForm((p) => ({ ...p, paidVia: e.target.value }))}>
                        <option value="">Select…</option>
                        {NPO_PAID_VIA_OPTIONS.map((o) => (
                          <option key={o.value} value={o.value}>{o.label}</option>
                        ))}
                        {npoTxnForm.paidVia && !NPO_PAID_VIA_OPTIONS.some((o) => o.value === npoTxnForm.paidVia) ? (
                          <option value={npoTxnForm.paidVia}>{npoTxnForm.paidVia} (existing)</option>
                        ) : null}
                      </select>
                    </div>
                    <div className="field">
                      <label>Member Email</label>
                      <input type="email" value={npoTxnForm.email} onChange={(e) => setNpoTxnForm((p) => ({ ...p, email: e.target.value }))} placeholder="member@email.com" />
                    </div>
                  </div>
                  {npoTxnForm.paymentStatus ? (
                    <div className="field">
                      <label>Payment status</label>
                      <select value={npoTxnForm.paymentStatus} onChange={(e) => setNpoTxnForm((p) => ({ ...p, paymentStatus: e.target.value }))}>
                        <option value="pending">Awaiting payment (membership not started)</option>
                        <option value="paid">Paid (starts the membership)</option>
                        {npoTxnForm.paymentStatus === "cancelled" ? <option value="cancelled">Cancelled</option> : null}
                      </select>
                    </div>
                  ) : null}
                  {!editingTxnNo ? (
                    <label className="checkbox-inline" style={{ display: "block", marginBottom: 12 }}>
                      <input
                        type="checkbox"
                        checked={currentNpoNotify() && Boolean(npoTxnForm.email)}
                        disabled={!npoTxnForm.email}
                        onChange={(e) => setNpoNotify(e.target.checked)}
                      />{" "}
                      Email the member a confirmation (leave unticked when back-filling an old year)
                    </label>
                  ) : null}
                  <div className="field">
                    <label>Notes</label>
                    <input type="text" value={npoTxnForm.notes} onChange={(e) => setNpoTxnForm((p) => ({ ...p, notes: e.target.value }))} placeholder="Optional notes" />
                  </div>
                  <div className="change-password-actions">
                    <button type="submit" className="button primary" disabled={isSavingNpoTxn}>
                      {isSavingNpoTxn ? "Saving..." : editingTxnNo ? "Update" : "Add Transaction"}
                    </button>
                    <button type="button" className="button secondary" onClick={() => setShowNpoTxnForm(false)} disabled={isSavingNpoTxn}>Cancel</button>
                  </div>
                </form>
              )}
            </>
          )}
        </div>
      </div>
    );
  };

  const renderSlotReassignModal = () => {
    const reg = slotReassignTarget;
    if (!reg) return null;
    const requiredSeats = getRequiredSeatsForRegistration(reg);
    return (
      <div className="admin-modal-overlay" role="dialog" aria-modal="true">
        <div className="admin-modal">
          <div className="panel-heading">
            <div>
              <h3>Change Entry Slot</h3>
              <p className="field-note">
                {reg.name || reg.email} · {reg.eventType} · {requiredSeats} seat{requiredSeats === 1 ? "" : "s"}
                {reg.slotName || reg.slotId ? ` · Current slot: ${reg.slotName || reg.slotId}` : ""}
              </p>
            </div>
            <button type="button" className="button secondary" onClick={closeSlotReassign} disabled={slotReassignSaving}>
              Close
            </button>
          </div>

          {slotReassignError ? <div className="error-banner">{slotReassignError}</div> : null}

          <EntrySlotSelector
            slots={slotReassignOptions}
            allocations={slotReassignAllocations}
            onAllocationsChange={setSlotReassignAllocations}
            requiredSeats={requiredSeats}
            loading={slotReassignLoading}
            loaded={!slotReassignLoading}
            error=""
          />

          <div className="btn-row" style={{ marginTop: "10px" }}>
            <button type="button" className="button secondary" onClick={closeSlotReassign} disabled={slotReassignSaving}>
              Cancel
            </button>
            <button
              type="button"
              className="button primary"
              onClick={handleSaveSlotReassign}
              disabled={slotReassignSaving || slotReassignLoading}
            >
              {slotReassignSaving ? "Saving…" : "Save"}
            </button>
          </div>
        </div>
      </div>
    );
  };

  const renderSeatAllocationModal = () => {
    const reg = seatAllocationTarget;
    if (!reg) return null;
    // Seats this registration already holds in its own editable allocation
    // are real "booked" rows in the hall's own seats table (that's how a
    // claim works), so the raw seat map lists them in bookedSeatIds too —
    // strip those out here so they render as normal, togglable "selected"
    // seats instead of disabled ones. Anything in lockedSeatIds (checked
    // in, or a genuinely paid purchase) stays booked/disabled, same as any
    // other registration's seat.
    const editableSeatIds = seatAllocationInfo?.editableSeatIds || [];
    const effectiveBookedSeatIds = seatAllocationMap
      ? seatAllocationMap.bookedSeatIds.filter((seatId) => !editableSeatIds.includes(seatId))
      : [];
    const adminOverrideMaxSeats = Boolean(seatAllocationInfo?.adminOverrideMaxSeats);
    const seatsAllowed = Number(seatAllocationInfo?.seatsAllowed) || 0;
    const maxSeats = adminOverrideMaxSeats ? Infinity : seatsAllowed;
    return (
      <div className="admin-modal-overlay" role="dialog" aria-modal="true">
        <div className="admin-modal" style={{ maxWidth: 900 }}>
          <div className="panel-heading">
            <div>
              <h3>Allocate Seats</h3>
              <p className="field-note">{reg.name || reg.email} · {reg.eventType}</p>
            </div>
            <button type="button" className="button secondary" onClick={closeSeatAllocation} disabled={seatAllocationSaving}>
              Close
            </button>
          </div>

          <p className="field-note">
            Assign specific hall seats to this registration directly. The registrant is not
            charged and gets the normal confirmation email with QR code and check-in ticket
            right away. Seats already assigned are shown selected — deselect to remove one, or
            pick more to add.
          </p>

          {seatAllocationInfo ? (
            <p className="field-note">
              {adminOverrideMaxSeats
                ? "No seat limit — admin override is enabled for this event."
                : `This registration is allowed up to ${seatsAllowed} seat${seatsAllowed === 1 ? "" : "s"} (one per adult/kid 9-14/kid 6-8).`}
            </p>
          ) : null}

          {seatAllocationError ? <div className="error-banner">{seatAllocationError}</div> : null}

          {seatAllocationMap ? (
            <SeatMapSelector
              layout={seatAllocationMap.layout}
              bookedSeatIds={effectiveBookedSeatIds}
              specialSeats={seatAllocationMap.specialSeats}
              zoneAvailable={seatAllocationMap.zoneAvailable}
              // Deliberately not the event's real zonePricing — this is an
              // admin bypass, so no zone should show as "not for sale" here,
              // and there's no zone-upgrade entitlement to gate against
              // (categoryCaps below), unlike the registrant's own picker.
              zonePricing={null}
              categoryCaps={null}
              walkwayAfterRow={seatAllocationMap.walkwayAfterRow}
              maxSeats={maxSeats}
              loading={seatAllocationLoading}
              loaded={!seatAllocationLoading}
              error=""
              selectedSeatIds={seatAllocationSelected}
              onSelectionChange={setSeatAllocationSelected}
            />
          ) : seatAllocationLoading ? (
            <div className="entry-slot-selector-state">Loading the seat map...</div>
          ) : null}

          <div className="btn-row" style={{ marginTop: "10px" }}>
            <button type="button" className="button secondary" onClick={closeSeatAllocation} disabled={seatAllocationSaving}>
              Cancel
            </button>
            <button
              type="button"
              className="button primary"
              onClick={handleSaveSeatAllocation}
              disabled={seatAllocationSaving || seatAllocationLoading || !seatAllocationMap}
            >
              {seatAllocationSaving
                ? "Saving…"
                : `Save (${seatAllocationSelected.length} Seat${seatAllocationSelected.length === 1 ? "" : "s"})`}
            </button>
          </div>
        </div>
      </div>
    );
  };

  const renderVisitsSection = () => (
    <>
      <div className="admin-stats">
        <div className="stat-box">
          <div className="stat-value">{visitSummary.totalVisits}</div>
          <div className="stat-label">Visits ({visitSummary.days}d)</div>
        </div>
        <div className="stat-box">
          <div className="stat-value">{visitSummary.uniqueVisitors}</div>
          <div className="stat-label">Unique Visitors</div>
        </div>
        <div className="stat-box">
          <div className="stat-value">{visitSummary.byPath.length}</div>
          <div className="stat-label">Unique Paths</div>
        </div>
      </div>

      <div className="admin-controls">
        <label htmlFor="visitDays" className="field" style={{ minWidth: 180 }}>
          <span>Window</span>
          <select
            id="visitDays"
            value={visitDays}
            onChange={(e) => {
              const days = Number(e.target.value) || 30;
              setVisitDays(days);
              loadVisitSummary(days);
            }}
            className="search-input"
          >
            {VISIT_RANGE_OPTIONS.map((days) => (
              <option key={days} value={days}>{days} days</option>
            ))}
          </select>
        </label>
        <button className="button secondary" onClick={() => loadVisitSummary()} disabled={isLoadingVisits}>
          {isLoadingVisits ? "Loading..." : "🔄 Refresh"}
        </button>
      </div>

      {visitSummary.range?.from && visitSummary.range?.to ? (
        <p className="field-note" style={{ marginBottom: 12 }}>
          Date range: {formatEventDate(visitSummary.range.from)} to {formatEventDate(visitSummary.range.to)}
        </p>
      ) : null}

      {visitError ? <div className="error-banner">{visitError}</div> : null}

      {isLoadingVisits ? (
        <div className="loading">Loading visit analytics...</div>
      ) : (
        <>
          <div className="table-container" style={{ marginBottom: 16 }}>
            <table className="registrations-table">
              <thead>
                <tr>
                  <th>Date</th>
                  <th>Visits</th>
                  <th>Unique Visitors</th>
                </tr>
              </thead>
              <tbody>
                {visitSummary.daily.map((row) => (
                  <tr key={row.date}>
                    <td>{formatEventDate(row.date)}</td>
                    <td>{row.visits}</td>
                    <td>{row.uniqueVisitors}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          <div className="table-container" style={{ marginBottom: 16 }}>
            <table className="registrations-table">
              <thead>
                <tr>
                  <th>Path</th>
                  <th>Visits</th>
                  <th>Unique Visitors</th>
                </tr>
              </thead>
              <tbody>
                {(visitSummary.byPath || []).slice(0, 20).map((row) => (
                  <tr key={row.path}>
                    <td><strong>{row.path}</strong></td>
                    <td>{row.visits}</td>
                    <td>{row.uniqueVisitors}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          <div className="table-container">
            <table className="registrations-table">
              <thead>
                <tr>
                  <th>Visitor Type</th>
                  <th>Visits</th>
                  <th>Unique Visitors</th>
                </tr>
              </thead>
              <tbody>
                {(visitSummary.byVisitorType || []).map((row) => (
                  <tr key={row.visitorType}>
                    <td>{row.visitorType}</td>
                    <td>{row.visits}</td>
                    <td>{row.uniqueVisitors}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}
    </>
  );

  const formatYen = (value) => `¥${Number(value || 0).toLocaleString()}`;

  const RECON_MAPPING_FIELDS = [
    { key: "amount", label: "Amount", required: true },
    { key: "date", label: "Date", required: false },
    { key: "payerName", label: "Payer Name", required: false },
    { key: "payerEmail", label: "Payer Email", required: false },
    { key: "description", label: "Description", required: false },
    { key: "reference", label: "Reference", required: false },
  ];

  const renderReconciliationSection = () => {
    const summary = reconReport?.summary || null;

    return (
      <>
        <p className="section-desc">
          Upload payment statements (credit card, bank transfer, PayPay) for an event, then run
          reconciliation to compare them against that event's registrations.
        </p>

        <div className="admin-controls">
          <label htmlFor="reconEvent" className="field" style={{ minWidth: 260 }}>
            <span>Event</span>
            <select
              id="reconEvent"
              className="search-input"
              value={reconEventType}
              onChange={(e) => {
                setReconEventType(e.target.value);
                setReconReport(null);
                resetReconUpload();
                setReconImportResult(null);
                setReconError("");
              }}
            >
              <option value="">Select an event…</option>
              {events.map((evt) => (
                <option key={evt.eventType} value={evt.eventType}>
                  {evt.eventName || evt.eventType}
                </option>
              ))}
            </select>
          </label>
        </div>

        {reconError ? <div className="error-banner">{reconError}</div> : null}

        {!reconEventType ? (
          <p className="field-note">Select an event above to upload payment statements and run reconciliation.</p>
        ) : (
          <>
            {/* Upload */}
            <div className="form-card" style={{ marginBottom: 16 }}>
              <h3>Upload payment statement</h3>
              <div className="admin-controls">
                <label htmlFor="reconSourceType" className="field" style={{ minWidth: 200 }}>
                  <span>Source</span>
                  <select
                    id="reconSourceType"
                    className="search-input"
                    value={reconSourceType}
                    onChange={(e) => setReconSourceType(e.target.value)}
                  >
                    {RECONCILIATION_SOURCE_TYPES.map((opt) => (
                      <option key={opt.value} value={opt.value}>{opt.label}</option>
                    ))}
                  </select>
                </label>
                <label className="button secondary" style={{ alignSelf: "flex-end" }}>
                  Choose CSV file
                  <input
                    type="file"
                    accept=".csv,text/csv"
                    style={{ display: "none" }}
                    onChange={(e) => handleReconFileSelect(e.target.files?.[0] ?? null)}
                    disabled={isPreviewingRecon || isImportingRecon}
                  />
                </label>
                {reconFileName ? <span className="field-note">{reconFileName}</span> : null}
              </div>

              {isPreviewingRecon ? <div className="loading">Parsing CSV…</div> : null}

              {reconPreview ? (
                <>
                  <p className="field-note" style={{ marginTop: 8 }}>
                    {reconPreview.totalRows} row(s) found. Map the CSV columns below (Amount is required),
                    then import.
                  </p>
                  <div className="recon-mapping-grid" style={{ display: "flex", flexWrap: "wrap", gap: 12, marginBottom: 12 }}>
                    {RECON_MAPPING_FIELDS.map((field) => (
                      <label key={field.key} className="field" style={{ minWidth: 180 }}>
                        <span>{field.label}{field.required ? " *" : ""}</span>
                        <select
                          className="search-input"
                          value={reconMapping[field.key] || ""}
                          onChange={(e) => setReconMapping((prev) => ({ ...prev, [field.key]: e.target.value }))}
                        >
                          <option value="">— none —</option>
                          {reconPreview.headers.map((header) => (
                            <option key={header} value={header}>{header}</option>
                          ))}
                        </select>
                      </label>
                    ))}
                  </div>

                  {reconPreview.sampleRows.length > 0 ? (
                    <div className="table-container" style={{ marginBottom: 12 }}>
                      <table className="registrations-table">
                        <thead>
                          <tr>
                            {reconPreview.headers.map((header) => <th key={header}>{header}</th>)}
                          </tr>
                        </thead>
                        <tbody>
                          {reconPreview.sampleRows.map((row, idx) => (
                            <tr key={idx}>
                              {reconPreview.headers.map((header) => <td key={header}>{row[header]}</td>)}
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                  ) : null}

                  <div className="admin-controls">
                    <button className="button primary" onClick={handleImportReconTransactions} disabled={isImportingRecon}>
                      {isImportingRecon ? "Importing…" : "Import"}
                    </button>
                    <button className="button secondary" onClick={resetReconUpload} disabled={isImportingRecon}>
                      Cancel
                    </button>
                  </div>
                </>
              ) : null}

              {reconImportResult ? (
                <div className="form-success" style={{ marginTop: 12 }}>
                  Imported {reconImportResult.imported} of {reconImportResult.totalRows} row(s)
                  {reconImportResult.skipped ? ` — ${reconImportResult.skipped} skipped` : ""}.
                </div>
              ) : null}
            </div>

            {/* Uploaded batches */}
            <div style={{ marginBottom: 16 }}>
              <h3>Uploaded batches</h3>
              {isLoadingReconTransactions ? (
                <div className="loading">Loading uploaded transactions…</div>
              ) : reconBatches.length === 0 ? (
                <p className="field-note">No payment transactions uploaded for this event yet.</p>
              ) : (
                <div className="table-container">
                  <table className="registrations-table">
                    <thead>
                      <tr>
                        <th>Source</th>
                        <th>Uploaded</th>
                        <th>By</th>
                        <th>Rows</th>
                        <th>Total Amount</th>
                        <th></th>
                      </tr>
                    </thead>
                    <tbody>
                      {reconBatches.map((batch) => (
                        <tr key={batch.batchId}>
                          <td>{RECONCILIATION_SOURCE_TYPES.find((o) => o.value === batch.sourceType)?.label || batch.sourceType}</td>
                          <td>{batch.uploadedAt ? new Date(batch.uploadedAt).toLocaleString() : ""}</td>
                          <td>{batch.uploadedBy}</td>
                          <td>{batch.count}</td>
                          <td>{formatYen(batch.totalAmount)}</td>
                          <td>
                            <button className="button secondary" onClick={() => handleDeleteReconBatch(batch.batchId)}>
                              Delete
                            </button>
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
              <p className="field-note" style={{ marginTop: 8 }}>{reconTransactionCount} transaction(s) uploaded in total for this event.</p>
            </div>

            {/* Run reconciliation */}
            <div className="admin-controls" style={{ marginBottom: 16 }}>
              <button className="button primary" onClick={handleRunReconciliation} disabled={isRunningRecon}>
                {isRunningRecon ? "Running…" : "Run Reconciliation"}
              </button>
            </div>

            {summary ? (
              <>
                <div className="admin-stats">
                  <div className="stat-box">
                    <div className="stat-value">{summary.totalMatched}</div>
                    <div className="stat-label">Matched</div>
                  </div>
                  <div className="stat-box">
                    <div className="stat-value">{summary.totalUnmatchedRegistrations}</div>
                    <div className="stat-label">Unmatched Registrations</div>
                  </div>
                  <div className="stat-box">
                    <div className="stat-value">{summary.totalUnmatchedTransactions}</div>
                    <div className="stat-label">Unmatched Transactions</div>
                  </div>
                  <div className="stat-box">
                    <div className="stat-value">{summary.totalAmountMismatches}</div>
                    <div className="stat-label">Amount Mismatches</div>
                  </div>
                  <div className="stat-box">
                    <div className="stat-value">{formatYen(summary.netDiscrepancyAmount)}</div>
                    <div className="stat-label">Net Discrepancy</div>
                  </div>
                </div>

                <p className="field-note">
                  Run at {reconReport.ranAt ? new Date(reconReport.ranAt).toLocaleString() : ""} by {reconReport.ranBy}.
                  {" "}
                  <button className="button secondary" onClick={() => handleExportReconRun(reconReport.runId)}>
                    Export CSV
                  </button>
                </p>

                <div className="admin-tabs" style={{ marginTop: 12 }}>
                  <button
                    type="button"
                    className={`admin-tab ${reconResultView === "matched" ? "active" : ""}`}
                    onClick={() => setReconResultView("matched")}
                  >
                    Matched ({reconReport.matchedPairs.length})
                  </button>
                  <button
                    type="button"
                    className={`admin-tab ${reconResultView === "unmatchedRegistrations" ? "active" : ""}`}
                    onClick={() => setReconResultView("unmatchedRegistrations")}
                  >
                    Unmatched Registrations ({reconReport.unmatchedRegistrations.length})
                  </button>
                  <button
                    type="button"
                    className={`admin-tab ${reconResultView === "unmatchedTransactions" ? "active" : ""}`}
                    onClick={() => setReconResultView("unmatchedTransactions")}
                  >
                    Unmatched Transactions ({reconReport.unmatchedTransactions.length})
                  </button>
                </div>

                {reconResultView === "matched" && (
                  <div className="table-container">
                    <table className="registrations-table">
                      <thead>
                        <tr>
                          <th>Name</th>
                          <th>Email</th>
                          <th>Registration Amount</th>
                          <th>Matched Amount</th>
                          <th>Difference</th>
                          <th>Confidence</th>
                          <th>Payment Status</th>
                          <th>Transaction ID(s)</th>
                        </tr>
                      </thead>
                      <tbody>
                        {reconReport.matchedPairs.map((pair) => (
                          <tr key={pair.registrationId}>
                            <td>{pair.name}</td>
                            <td>{pair.email}</td>
                            <td>{formatYen(pair.registrationAmount)}</td>
                            <td>{formatYen(pair.matchedAmount)}</td>
                            <td style={pair.difference !== 0 ? { color: "#b91c1c", fontWeight: 600 } : undefined}>
                              {formatYen(pair.difference)}
                            </td>
                            <td>{pair.matchConfidence}</td>
                            <td>{pair.paymentStatus}</td>
                            <td style={{ fontFamily: "monospace", fontSize: "0.85em" }}>
                              {(pair.transactionIds || []).join(", ")}
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                )}

                {reconResultView === "unmatchedRegistrations" && (
                  <div className="table-container">
                    <table className="registrations-table">
                      <thead>
                        <tr>
                          <th>Name</th>
                          <th>Email</th>
                          <th>Total Amount</th>
                          <th>Paid Amount</th>
                          <th>Payment Method</th>
                          <th>Payment Status</th>
                        </tr>
                      </thead>
                      <tbody>
                        {reconReport.unmatchedRegistrations.map((reg) => (
                          <tr key={reg.registrationId}>
                            <td>{reg.name}</td>
                            <td>{reg.email}</td>
                            <td>{formatYen(reg.totalAmount)}</td>
                            <td>{formatYen(reg.paidAmount)}</td>
                            <td>{normalizeOnamPaymentMethod(reg.paymentMethod)}</td>
                            <td>{reg.paymentStatus}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                )}

                {reconResultView === "unmatchedTransactions" && (
                  <div className="table-container">
                    <table className="registrations-table">
                      <thead>
                        <tr>
                          <th>Date</th>
                          <th>Source</th>
                          <th>Amount</th>
                          <th>Payer Name</th>
                          <th>Payer Email</th>
                          <th>Description</th>
                          <th>Reference</th>
                          <th>Transaction ID</th>
                        </tr>
                      </thead>
                      <tbody>
                        {reconReport.unmatchedTransactions.map((txn) => (
                          <tr key={txn.transactionId}>
                            <td>{txn.transactionDate}</td>
                            <td>{txn.sourceType}</td>
                            <td>{formatYen(txn.amount)}</td>
                            <td>{txn.payerName}</td>
                            <td>{txn.payerEmail}</td>
                            <td>{txn.description}</td>
                            <td>{txn.referenceId}</td>
                            <td style={{ fontFamily: "monospace", fontSize: "0.85em" }}>{txn.transactionId}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                )}
              </>
            ) : null}

            {/* Run history */}
            <div style={{ marginTop: 24 }}>
              <h3>Reconciliation history</h3>
              {isLoadingReconRuns ? (
                <div className="loading">Loading history…</div>
              ) : reconRuns.length === 0 ? (
                <p className="field-note">No reconciliation runs yet for this event.</p>
              ) : (
                <div className="table-container">
                  <table className="registrations-table">
                    <thead>
                      <tr>
                        <th>Ran At</th>
                        <th>By</th>
                        <th>Matched</th>
                        <th>Unmatched Regs</th>
                        <th>Unmatched Txns</th>
                        <th>Net Discrepancy</th>
                        <th></th>
                      </tr>
                    </thead>
                    <tbody>
                      {reconRuns.map((run) => (
                        <tr key={run.runId}>
                          <td>{run.ranAt ? new Date(run.ranAt).toLocaleString() : ""}</td>
                          <td>{run.ranBy}</td>
                          <td>{run.summary?.totalMatched ?? 0}</td>
                          <td>{run.summary?.totalUnmatchedRegistrations ?? 0}</td>
                          <td>{run.summary?.totalUnmatchedTransactions ?? 0}</td>
                          <td>{formatYen(run.summary?.netDiscrepancyAmount)}</td>
                          <td>
                            <button className="button secondary" onClick={() => handleViewReconRun(run.runId)}>View</button>{" "}
                            <button className="button secondary" onClick={() => handleExportReconRun(run.runId)}>Export</button>
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </div>
          </>
        )}
      </>
    );
  };

  const renderRegistrationModal = () => {
    const isOnam = registrationForm.registrationType === "onam";
    const editing = !!registrationForm.registrationId;

    // Offline/in-person registrations are sometimes paid by cash or another
    // method the three quick-select pills don't cover, so anything outside
    // credit/paypay/bank (including no selection yet) falls back to a
    // free-text "Other" pill instead of showing no selection at all.
    const isOtherPaymentMethod = !ONAM_PAYMENT_METHODS.some((m) => m.key === registrationForm.paymentMethod);

    // Live "estimated total" as the admin edits option/counts/membership —
    // mirrors the server's pricing rules, but additional adults' NPO status
    // isn't checked client-side, so they're estimated at the base rate; the
    // server verifies each one and applies their real discount on save.
    const estimatedOrder = (() => {
      if (!isOnam) return null;
      const base = ONAM_BASE_PRICES[registrationForm.registrationOption] || ONAM_BASE_PRICES.combo;
      const counts = registrationForm.attendeeCounts || {};
      const adultCount = Math.max(0, Number(counts.adults) || 0);
      const joiningOrRenewing =
        registrationForm.membershipAction === "join" || registrationForm.membershipAction === "renew";
      const registrantTier = joiningOrRenewing
        ? "full"
        : ONAM_DISCOUNT_PCT[registrationForm.pricingTier]
          ? registrationForm.pricingTier
          : "none";
      const pct = ONAM_DISCOUNT_PCT[registrantTier]?.[registrationForm.registrationOption] || 0;
      const registrantUnit = Math.round((base.adult * (100 - pct)) / 100);
      const adultsTotal =
        adultCount > 0 ? registrantUnit + base.adult * (adultCount - 1) : 0;
      const kidsTotal = ["kids914", "kids68", "kidsUnder6"].reduce(
        (sum, key) => sum + base[key] * (Number(counts[key]) || 0),
        0
      );
      const npoFee = joiningOrRenewing ? ONAM_NPO_FEE : 0;
      return { total: adultsTotal + kidsTotal + npoFee, npoFee };
    })();

    return (
      <div className="admin-modal-overlay" role="dialog" aria-modal="true">
        <div className="admin-modal">
          <form onSubmit={handleSaveRegistration}>
            <div className="panel-heading">
              <div>
                <h3>{editing ? "Edit Registration" : "Add Registration"}</h3>
                <p>{isOnam ? "Onam pricing (NPO discount, ¥1000 fee) is recomputed on the server." : "Standard event registration."}</p>
              </div>
              <button
                type="button"
                className="button secondary"
                onClick={() => setShowRegistrationForm(false)}
              >
                Close
              </button>
            </div>

            <div className="field-row">
              <div className="field">
                <label htmlFor="regEventType">Event</label>
                <select
                  id="regEventType"
                  name="eventType"
                  value={registrationForm.eventType}
                  onChange={(e) => {
                    const eventType = e.target.value;
                    const evtRecord = events.find((evt) => evt.eventType === eventType);
                    setRegistrationForm((prev) => ({
                      ...prev,
                      eventType,
                      eventDate: evtRecord?.eventDate || prev.eventDate,
                      registrationType: /onam/i.test(eventType) ? "onam" : (prev.registrationType === "onam" && !/onam/i.test(eventType) ? "standard" : prev.registrationType),
                    }));
                  }}
                  disabled={editing}
                >
                  <option value="">Select event…</option>
                  {events.map((evt) => (
                    <option key={evt.eventType} value={evt.eventType}>
                      {evt.eventName || evt.eventType}
                    </option>
                  ))}
                </select>
              </div>
              <div className="field">
                <label htmlFor="regType">Type</label>
                <select
                  id="regType"
                  name="registrationType"
                  value={registrationForm.registrationType}
                  onChange={handleRegistrationFieldChange}
                  disabled={editing}
                >
                  <option value="standard">Standard</option>
                  <option value="onam">Onam</option>
                </select>
              </div>
            </div>

            <div className="field-row">
              <div className="field">
                <label htmlFor="regName">Full Name</label>
                <input id="regName" name="name" type="text" value={registrationForm.name} onChange={handleRegistrationFieldChange} />
              </div>
              <div className="field">
                <label htmlFor="regEmail">Email</label>
                <input id="regEmail" name="email" type="email" value={registrationForm.email} onChange={handleRegistrationFieldChange} disabled={editing} />
              </div>
            </div>

            <div className="field-row">
              <div className="field">
                <label htmlFor="regPhone">Phone</label>
                <input id="regPhone" name="phone" type="tel" value={registrationForm.phone} onChange={handleRegistrationFieldChange} />
              </div>
              {isOnam ? (
                <div className="field">
                  <label htmlFor="regOption">Option</label>
                  <select id="regOption" name="registrationOption" value={registrationForm.registrationOption} onChange={handleRegistrationFieldChange}>
                    {ONAM_OPTIONS.map((o) => (
                      <option key={o.value} value={o.value}>{o.label}</option>
                    ))}
                  </select>
                </div>
              ) : null}
            </div>

            {registrationForm.checkedIn ? (
              <p className="field-note" style={{ margin: "4px 0 8px" }}>
                🔒 This party has been checked in, so the seated attendee counts are
                locked — the entry pass was issued and partly used against them, and
                the slot can no longer be reassigned. Contact details, payment and
                under-6s can still be changed.
              </p>
            ) : null}

            {isOnam ? (
              <>
                <div className="counter-grid">
                  {ONAM_CATEGORIES.map((cat) => (
                    <div className="field" key={cat.key}>
                      <label htmlFor={`onam-${cat.key}`}>{cat.label}</label>
                      <input
                        id={`onam-${cat.key}`}
                        type="number"
                        min="0"
                        value={registrationForm.attendeeCounts[cat.key]}
                        onChange={(e) => handleOnamCountChange(cat.key, e.target.value)}
                        disabled={registrationForm.checkedIn && SEATED_COUNT_KEYS.has(cat.key)}
                      />
                    </div>
                  ))}
                </div>
                {(registrationForm.additionalAdultEmails || []).length > 0 ? (
                  <>
                    <p className="field-note" style={{ margin: "4px 0 8px" }}>
                      Additional adult emails (optional) — NPO members are
                      discounted automatically (recomputed on the server).
                    </p>
                    <div className="counter-grid">
                      {registrationForm.additionalAdultEmails.map((email, index) => (
                        <div className="field" key={index}>
                          <label htmlFor={`onam-adult-${index}`}>Adult {index + 2} email</label>
                          <input
                            id={`onam-adult-${index}`}
                            type="email"
                            placeholder="adult@email.com"
                            value={email}
                            onChange={(e) => handleAdditionalAdultEmailChange(index, e.target.value)}
                          />
                        </div>
                      ))}
                    </div>
                  </>
                ) : null}
                <div className="field-row">
                  <div className="field">
                    <label htmlFor="regMembership">NPO Membership</label>
                    <select id="regMembership" name="membershipAction" value={registrationForm.membershipAction} onChange={handleRegistrationFieldChange}>
                      {MEMBERSHIP_ACTION_OPTIONS.map((o) => (
                        <option key={o.value} value={o.value}>{o.label}</option>
                      ))}
                    </select>
                  </div>
                  <div className="field">
                    <label htmlFor="regNpoName">Organization (optional)</label>
                    <input id="regNpoName" name="npoName" type="text" value={registrationForm.npoName} onChange={handleRegistrationFieldChange} />
                  </div>
                </div>
                {estimatedOrder ? (
                  <p className="field-note" style={{ margin: "4px 0 12px" }}>
                    <strong>Estimated total: ¥{estimatedOrder.total.toLocaleString()}</strong>
                    {estimatedOrder.npoFee
                      ? ` (includes ¥${estimatedOrder.npoFee.toLocaleString()} NPO membership fee)`
                      : ""}
                    {" — recalculated by the server on save; additional adults' NPO discounts are verified then."}
                  </p>
                ) : null}
                <div className="field">
                  <label>Payment Method</label>
                  <div className="onam-toggle-group">
                    {ONAM_PAYMENT_METHODS.map((m) => (
                      <button
                        type="button"
                        key={m.key}
                        className={`onam-toggle-pill ${registrationForm.paymentMethod === m.key ? "selected" : ""}`}
                        onClick={() =>
                          setRegistrationForm((prev) => ({ ...prev, paymentMethod: m.key }))
                        }
                      >
                        {m.label}
                      </button>
                    ))}
                    <button
                      type="button"
                      className={`onam-toggle-pill ${isOtherPaymentMethod ? "selected" : ""}`}
                      onClick={() => setRegistrationForm((prev) => ({ ...prev, paymentMethod: "" }))}
                    >
                      Other
                    </button>
                  </div>
                  {isOtherPaymentMethod ? (
                    <input
                      type="text"
                      style={{ marginTop: "8px" }}
                      value={registrationForm.paymentMethod}
                      onChange={(e) =>
                        setRegistrationForm((prev) => ({ ...prev, paymentMethod: e.target.value }))
                      }
                      placeholder="e.g. cash, cheque"
                    />
                  ) : null}
                </div>
              </>
            ) : (
              <>
                <div className="counter-grid">
                  {STANDARD_CATEGORIES.map((cat) => (
                    <div className="field" key={cat.key}>
                      <label htmlFor={`std-${cat.key}`}>{cat.label}</label>
                      <input
                        id={`std-${cat.key}`}
                        type="number"
                        min="0"
                        value={registrationForm.attendeeCounts[cat.key]}
                        onChange={(e) => handleStandardCountChange(cat.key, e.target.value)}
                        disabled={registrationForm.checkedIn && SEATED_COUNT_KEYS.has(cat.key)}
                      />
                    </div>
                  ))}
                </div>
                <div className="field">
                  <label htmlFor="regVeg">Vegetarian Count</label>
                  <input id="regVeg" name="vegetarianCount" type="number" min="0" value={registrationForm.vegetarianCount} onChange={handleRegistrationFieldChange} />
                </div>
              </>
            )}

            <div className="field">
              <label htmlFor="regPaymentStatus">Payment Status</label>
              <select
                id="regPaymentStatus"
                name="paymentStatus"
                value={registrationForm.paymentStatus || "pending"}
                onChange={handleRegistrationFieldChange}
              >
                {PAYMENT_STATUSES.map((s) => (
                  <option key={s.key} value={s.key}>{s.label}</option>
                ))}
              </select>
              <p className="field-hint">
                Marking as “Paid” emails the registrant a payment-received confirmation.
              </p>
            </div>

            {editing && Number(registrationForm.paidAmount) > 0 ? (
              <div className="payment-summary">
                <div className="payment-summary-row">
                  <span>Amount already paid</span>
                  <span>¥{Number(registrationForm.paidAmount).toLocaleString()}</span>
                </div>
                <div className="payment-summary-row">
                  <span>{estimatedOrder ? "Estimated new total" : "Current order total"}</span>
                  <span>¥{(estimatedOrder ? estimatedOrder.total : Number(registrationForm.totalAmount)).toLocaleString()}</span>
                </div>
                {(() => {
                  const diff =
                    (estimatedOrder ? estimatedOrder.total : Number(registrationForm.totalAmount) || 0) -
                    (Number(registrationForm.paidAmount) || 0);
                  if (!diff) {
                    return (
                      <div className="payment-summary-row settled">
                        <span>Balance</span>
                        <span>Settled</span>
                      </div>
                    );
                  }
                  return (
                    <div className="payment-summary-row outstanding">
                      <span>{diff > 0 ? "Balance due" : "Refund due"}</span>
                      <span>¥{Math.abs(diff).toLocaleString()}</span>
                    </div>
                  );
                })()}
                <p className="field-hint">
                  Saving changes that alter the total updates the balance. Collect or
                  refund the difference, then set the status accordingly.
                </p>
              </div>
            ) : null}

            {registrationFormError ? <div className="error-banner">{registrationFormError}</div> : null}

            <div className="change-password-actions">
              <button type="submit" className="button primary" disabled={isSavingRegistration}>
                {isSavingRegistration ? "Saving..." : editing ? "Update Registration" : "Add Registration"}
              </button>
              <button type="button" className="button secondary" onClick={() => setShowRegistrationForm(false)} disabled={isSavingRegistration}>
                Cancel
              </button>
            </div>
          </form>
        </div>
      </div>
    );
  };

  const resetChangePasswordForm = () => {
    setCurrentPassword("");
    setDashboardNewPassword("");
    setDashboardConfirmPassword("");
    setChangePasswordError("");
  };

  const handleChangePasswordSubmit = async (e) => {
    e.preventDefault();
    setChangePasswordError("");
    setChangePasswordSuccess("");

    if (!currentPassword.trim() || !dashboardNewPassword.trim() || !dashboardConfirmPassword.trim()) {
      setChangePasswordError("Please fill in current, new, and confirm password.");
      return;
    }

    if (dashboardNewPassword !== dashboardConfirmPassword) {
      setChangePasswordError("New password and confirm password do not match.");
      return;
    }

    if (dashboardNewPassword.length < 8) {
      setChangePasswordError("New password must be at least 8 characters long.");
      return;
    }

    setIsChangingPassword(true);
    try {
      await changePasswordAuthenticated(currentPassword, dashboardNewPassword);
      setChangePasswordSuccess("Password updated successfully.");
      setCurrentPassword("");
      setDashboardNewPassword("");
      setDashboardConfirmPassword("");
    } catch (err) {
      setChangePasswordError(err.message || "Unable to change password.");
    } finally {
      setIsChangingPassword(false);
    }
  };

  const handleExport = async () => {
    setIsLoading(true);
    try {
      const blob = await exportRegistrationsCSV();
      const timestamp = new Date().toISOString().split("T")[0];
      downloadCSV(blob, `registrations-${timestamp}.csv`);
    } catch (err) {
      setError(err.message || "Failed to export registrations");
    } finally {
      setIsLoading(false);
    }
  };

  const handleSort = (key) => {
    let direction = "asc";
    if (sortConfig.key === key && sortConfig.direction === "asc") {
      direction = "desc";
    }
    setSortConfig({ key, direction });
  };

  const registrationsForStats = registrations.filter(
    (reg) =>
      (eventDateFilter === "all" || (reg.eventDate || "") === eventDateFilter) &&
      (selectedEventType === "all" || (reg.eventType || "") === selectedEventType) &&
      (paymentStatusFilter === "all" || (reg.paymentStatus || "pending") === paymentStatusFilter) &&
      (registrationOptionFilter === "all" || (reg.registrationOption || "") === registrationOptionFilter) &&
      (sadhyaSlotFilter === "all" ||
        (sadhyaSlotFilter === "missing"
          ? registrationNeedsSadhyaSlot(reg) && !registrationHasSadhyaSlot(reg)
          : registrationNeedsSadhyaSlot(reg) && registrationHasSadhyaSlot(reg)))
  );

  const filteredRegistrations = registrationsForStats.filter((reg) => {
    const search = searchTerm.toLowerCase();
    return (
      (reg.firstName?.toLowerCase() || "").includes(search) ||
      (reg.lastName?.toLowerCase() || "").includes(search) ||
      (reg.email?.toLowerCase() || "").includes(search) ||
      (reg.whatsapp?.toLowerCase() || "").includes(search)
    );
  }).sort((a, b) => {
    const aVal = getSortValue(a, sortConfig.key);
    const bVal = getSortValue(b, sortConfig.key);

    if (typeof aVal === "number" && typeof bVal === "number") {
      return sortConfig.direction === "asc" ? aVal - bVal : bVal - aVal;
    }

    return sortConfig.direction === "asc"
      ? String(aVal).localeCompare(String(bVal))
      : String(bVal).localeCompare(String(aVal));
  });

  const eventDateOptions = Array.from(
    new Set(registrations.map((reg) => reg.eventDate).filter(Boolean))
  ).sort((a, b) => String(a).localeCompare(String(b)));

  // Calculate summary stats
  const stats = {
    totalRegistrations: registrationsForStats.length,
    totalPeople: registrationsForStats.reduce((sum, reg) => {
      return sum + getRegistrationTotalAttendees(reg);
    }, 0),
  };

  const visibleOnamTotals = ONAM_TABLE_COLUMNS.reduce((acc, column) => {
    acc[column.key] = filteredRegistrations.reduce(
      (sum, reg) => sum + (Number(reg.attendeeCounts?.[column.key]) || 0),
      0
    );
    return acc;
  }, {});

  const visibleRegistrationTotal = filteredRegistrations.reduce(
    (sum, reg) => sum + getRegistrationTotalAttendees(reg),
    0
  );

  const attendeeSummaryStats = [
    { label: "Adults", value: visibleOnamTotals.adults || 0 },
    { label: "Kids 9–14", value: visibleOnamTotals.kids914 || 0 },
    { label: "Kids 6–8", value: visibleOnamTotals.kids68 || 0 },
    { label: "Under 6", value: visibleOnamTotals.kidsUnder6 || 0 },
    { label: "Visible Total", value: visibleRegistrationTotal || 0 },
  ];

  // The Reports tab has no visible connection to the Registrations tab's
  // filter toolbar, so it reports over all loaded registrations rather than
  // silently reflecting filters the admin can no longer see.
  const detailedReport = buildRegistrationReport(registrations);
  const sadhyaSlotRows = buildSadhyaSlotAllocationReport(registrations);

  if (!isLoggedIn) {
    const loginContent = (
      <section className={`form-card admin-login ${embedded ? "admin-embedded-panel" : ""}`}>
        {embedded ? (
          <div className="admin-embedded-heading">
            <h2>Admin Login</h2>
            <p>Sign in to access registration details.</p>
          </div>
        ) : null}
          <div className="section-label">
            {requiresNewPassword
              ? "Set New Password"
              : forgotPasswordMode
                ? (resetCodeSent ? "Reset Password" : "Forgot Password")
                : "Admin Login"}
          </div>
          <p className="sub">
            {requiresNewPassword
              ? "Create your new password to finish first-time sign in."
              : forgotPasswordMode
                ? (resetCodeSent
                  ? "Enter the verification code from email and set your new password."
                  : "Enter your email and we'll send a verification code.")
                : "Log in with your admin credentials to view and manage event registrations."}
          </p>

          {requiresNewPassword ? (
            <form onSubmit={handleSetNewPassword}>
              <div className="field">
                <label htmlFor="challengeEmail">Email</label>
                <input id="challengeEmail" type="email" value={loginEmail} disabled />
              </div>

              <div className="field">
                <label htmlFor="newPassword">New Password</label>
                <input
                  id="newPassword"
                  type="password"
                  value={newPassword}
                  onChange={(e) => setNewPassword(e.target.value)}
                  placeholder="Enter new password"
                  disabled={isSettingPassword}
                />
              </div>

              <div className="field">
                <label htmlFor="confirmNewPassword">Confirm New Password</label>
                <input
                  id="confirmNewPassword"
                  type="password"
                  value={confirmNewPassword}
                  onChange={(e) => setConfirmNewPassword(e.target.value)}
                  placeholder="Re-enter new password"
                  disabled={isSettingPassword}
                />
                {loginError && <span className="field-error">{loginError}</span>}
                {loginSuccess && <div className="success-banner">{loginSuccess}</div>}
              </div>

              <button type="submit" className="button primary" disabled={isSettingPassword}>
                {isSettingPassword ? "Updating..." : "Set New Password"}
              </button>

              <button
                type="button"
                className="button secondary admin-secondary-action"
                onClick={resetNewPasswordState}
                disabled={isSettingPassword}
              >
                Back to Login
              </button>
            </form>
          ) : forgotPasswordMode ? (
            resetCodeSent ? (
              <form onSubmit={handleConfirmPasswordReset}>
                <div className="field">
                  <label htmlFor="forgotEmailConfirm">Email</label>
                  <input id="forgotEmailConfirm" type="email" value={loginEmail} disabled />
                  <span className="field-note">
                    We sent a code to {maskEmailAddress(loginEmail)}.
                  </span>
                </div>

                <div className="field">
                  <label htmlFor="resetCode">Verification Code</label>
                  <input
                    id="resetCode"
                    type="text"
                    value={resetCode}
                    onChange={(e) => setResetCode(e.target.value)}
                    placeholder="Enter code from email"
                    disabled={isResettingPassword}
                  />
                </div>

                <div className="field">
                  <label htmlFor="forgotNewPassword">New Password</label>
                  <input
                    id="forgotNewPassword"
                    type="password"
                    value={forgotNewPassword}
                    onChange={(e) => setForgotNewPassword(e.target.value)}
                    placeholder="Enter new password"
                    disabled={isResettingPassword}
                  />
                </div>

                <div className="field">
                  <label htmlFor="forgotConfirmPassword">Confirm New Password</label>
                  <input
                    id="forgotConfirmPassword"
                    type="password"
                    value={forgotConfirmPassword}
                    onChange={(e) => setForgotConfirmPassword(e.target.value)}
                    placeholder="Re-enter new password"
                    disabled={isResettingPassword}
                  />
                  {loginError && <span className="field-error">{loginError}</span>}
                  {loginSuccess && <div className="success-banner">{loginSuccess}</div>}
                </div>

                <button type="submit" className="button primary" disabled={isResettingPassword}>
                  {isResettingPassword ? "Resetting..." : "Reset Password"}
                </button>

                <div className="resend-code-row">
                  <button
                    type="button"
                    className="admin-access-link"
                    onClick={handleResendResetCode}
                    disabled={isRequestingResetCode || resendCooldown > 0}
                  >
                    {isRequestingResetCode
                      ? "Resending..."
                      : resendCooldown > 0
                        ? `Resend code in ${resendCooldown}s`
                        : "Resend code"}
                  </button>
                </div>

                <button
                  type="button"
                  className="button secondary admin-secondary-action"
                  onClick={resetForgotPasswordState}
                  disabled={isResettingPassword}
                >
                  Back to Login
                </button>
              </form>
            ) : (
              <form onSubmit={handleRequestResetCode}>
                <div className="field">
                  <label htmlFor="forgotEmail">Email</label>
                  <input
                    id="forgotEmail"
                    type="email"
                    value={loginEmail}
                    onChange={(e) => setLoginEmail(e.target.value)}
                    placeholder="admin@cloudnadi.com"
                    disabled={isRequestingResetCode}
                  />
                  {loginError && <span className="field-error">{loginError}</span>}
                  {loginSuccess && <div className="success-banner">{loginSuccess}</div>}
                </div>

                <button type="submit" className="button primary" disabled={isRequestingResetCode}>
                  {isRequestingResetCode ? "Sending..." : "Send Verification Code"}
                </button>

                <button
                  type="button"
                  className="button secondary admin-secondary-action"
                  onClick={resetForgotPasswordState}
                  disabled={isRequestingResetCode}
                >
                  Back to Login
                </button>
              </form>
            )
          ) : (
            <form onSubmit={handleLogin}>
              <div className="field">
                <label htmlFor="loginEmail">Email</label>
                <input
                  id="loginEmail"
                  type="email"
                  value={loginEmail}
                  onChange={(e) => setLoginEmail(e.target.value)}
                  placeholder="admin@cloudnadi.com"
                  disabled={isLoggingIn}
                />
              </div>

              <div className="field">
                <label htmlFor="loginPassword">Password</label>
                <input
                  id="loginPassword"
                  type="password"
                  value={loginPassword}
                  onChange={(e) => setLoginPassword(e.target.value)}
                  placeholder="Enter your password"
                  disabled={isLoggingIn}
                />
                {loginError && <span className="field-error">{loginError}</span>}
                {loginSuccess && <div className="success-banner">{loginSuccess}</div>}
              </div>

              <button type="submit" className="button primary" disabled={isLoggingIn}>
                {isLoggingIn ? "Signing in..." : "Sign In"}
              </button>

              <button
                type="button"
                className="admin-access-link admin-auth-link"
                onClick={() => {
                  setLoginError("");
                  setLoginSuccess("");
                  resetNewPasswordState();
                  setForgotPasswordMode(true);
                }}
                disabled={isLoggingIn}
              >
                Forgot password?
              </button>

              <div className="admin-help">
                <p>
                  <strong>First time setup?</strong>
                </p>
                <ol>
                  <li>Ask the event organizer for your initial credentials</li>
                  <li>You'll receive a temporary password via email</li>
                  <li>Sign in once with the temporary password to set a new one</li>
                </ol>
              </div>
            </form>
          )}
        </section>
    );

    if (embedded) {
      return loginContent;
    }

    return (
      <>
        <Navbar navigateTo={() => (window.location.pathname = HOME_PATH)} brand="WMF Japan Admin" showUserMenu={false} />
        <main className="page-wrap">
          <div className="event-header">
            <div className="banner-title">Admin Dashboard</div>
            <div className="sky-bar" />
          </div>
          {loginContent}
        </main>
      </>
    );
  }

  const dashboardContent = (
    <section className={`form-card admin-dashboard ${embedded ? "admin-embedded-panel" : ""}`}>
      {embedded ? (
        <div className="admin-embedded-heading">
          <h2>Super Admin Console</h2>
          <p>View, filter, and export registrations, then manage partners and links.</p>
        </div>
      ) : null}
        {/* Header with logout */}
        <div className="admin-header">
          <h2>Super Admin Platform</h2>
          <div className="admin-header-actions">
            <button
              className="button secondary"
              onClick={() => {
                setShowChangePassword((prev) => !prev);
                setChangePasswordSuccess("");
                setChangePasswordError("");
              }}
            >
              {showChangePassword ? "Close" : "Change Password"}
            </button>
            <button className="button secondary" onClick={handleLogout}>
              Logout
            </button>
          </div>
        </div>

        <div className="admin-tabs">
          <button
            type="button"
            className={`admin-tab ${activeSection === "registrations" ? "active" : ""}`}
            onClick={() => setActiveSection("registrations")}
          >
            Registrations
          </button>
          <button
            type="button"
            className={`admin-tab ${activeSection === "reports" ? "active" : ""}`}
            onClick={() => setActiveSection("reports")}
          >
            Reports
          </button>
          <button
            type="button"
            className={`admin-tab ${activeSection === "events" ? "active" : ""}`}
            onClick={() => setActiveSection("events")}
          >
            Events
          </button>
          <button
            type="button"
            className={`admin-tab ${activeSection === "entrySlots" ? "active" : ""}`}
            onClick={() => setActiveSection("entrySlots")}
          >
            Entry Slots
          </button>
          {seatUpgradeEvents.length > 0 ? (
            <button
              type="button"
              className={`admin-tab ${activeSection === "seatUpgrades" ? "active" : ""}`}
              onClick={() => setActiveSection("seatUpgrades")}
            >
              Zone Upgrades
            </button>
          ) : null}
          {celebrityDinnerEvents.length > 0 ? (
            <button
              type="button"
              className={`admin-tab ${activeSection === "celebrityDinner" ? "active" : ""}`}
              onClick={() => setActiveSection("celebrityDinner")}
            >
              Dinner with Celebrity
            </button>
          ) : null}
          <button
            type="button"
            className={`admin-tab ${activeSection === "seatSelection" ? "active" : ""}`}
            onClick={() => setActiveSection("seatSelection")}
          >
            Seat Selection
          </button>
          <button
            type="button"
            className={`admin-tab ${activeSection === "members" ? "active" : ""}`}
            onClick={() => setActiveSection("members")}
          >
            Members
          </button>
          <button
            type="button"
            className={`admin-tab ${activeSection === "npoPayments" ? "active" : ""}`}
            onClick={() => setActiveSection("npoPayments")}
          >
            NPO Membership
          </button>
          <button
            type="button"
            className={`admin-tab ${activeSection === "paymentMethods" ? "active" : ""}`}
            onClick={() => setActiveSection("paymentMethods")}
          >
            Payment Methods
          </button>
          <button
            type="button"
            className={`admin-tab ${activeSection === "agm" ? "active" : ""}`}
            onClick={() => setActiveSection("agm")}
          >
            AGM
          </button>
          <button
            type="button"
            className={`admin-tab ${activeSection === "partners" ? "active" : ""}`}
            onClick={() => setActiveSection("partners")}
          >
            Partners
          </button>
          <a className="admin-tab" href="/admin/event-platform">
            Event platform
          </a>
          <button
            type="button"
            className={`admin-tab ${activeSection === "carousel" ? "active" : ""}`}
            onClick={() => setActiveSection("carousel")}
          >
            Carousel
          </button>
          <button
            type="button"
            className={`admin-tab ${activeSection === "visits" ? "active" : ""}`}
            onClick={() => setActiveSection("visits")}
          >
            Visits
          </button>
          <button
            type="button"
            className={`admin-tab ${activeSection === "reconciliation" ? "active" : ""}`}
            onClick={() => setActiveSection("reconciliation")}
          >
            Reconciliation
          </button>
        </div>

        {showChangePassword && (
          <div className="change-password-panel">
            <form className="change-password-form" onSubmit={handleChangePasswordSubmit}>
              <div className="field">
                <label htmlFor="currentPassword">Current Password</label>
                <input
                  id="currentPassword"
                  type="password"
                  value={currentPassword}
                  onChange={(e) => setCurrentPassword(e.target.value)}
                  placeholder="Enter current password"
                  disabled={isChangingPassword}
                />
              </div>

              <div className="field-row">
                <div className="field">
                  <label htmlFor="dashboardNewPassword">New Password</label>
                  <input
                    id="dashboardNewPassword"
                    type="password"
                    value={dashboardNewPassword}
                    onChange={(e) => setDashboardNewPassword(e.target.value)}
                    placeholder="Enter new password"
                    disabled={isChangingPassword}
                  />
                </div>
                <div className="field">
                  <label htmlFor="dashboardConfirmPassword">Confirm New Password</label>
                  <input
                    id="dashboardConfirmPassword"
                    type="password"
                    value={dashboardConfirmPassword}
                    onChange={(e) => setDashboardConfirmPassword(e.target.value)}
                    placeholder="Re-enter new password"
                    disabled={isChangingPassword}
                  />
                </div>
              </div>

              {changePasswordError && <div className="error-banner">{changePasswordError}</div>}
              {changePasswordSuccess && <div className="success-banner">{changePasswordSuccess}</div>}

              <div className="change-password-actions">
                <button type="submit" className="button primary" disabled={isChangingPassword}>
                  {isChangingPassword ? "Updating..." : "Update Password"}
                </button>
                <button
                  type="button"
                  className="button secondary"
                  onClick={resetChangePasswordForm}
                  disabled={isChangingPassword}
                >
                  Clear
                </button>
              </div>
            </form>
          </div>
        )}

        {activeSection === "reports" ? (
          <>
            <RegistrationReport
              report={detailedReport}
              updatedAt={reportUpdatedAt}
              onRefresh={loadRegistrations}
              onExport={handleExport}
              isLoading={isLoading}
            />
            <SadhyaSlotAllocationReport rows={sadhyaSlotRows} onCheckIn={handleAdminCheckIn} />
          </>
        ) : activeSection === "events" ? (
          renderEventsSection()
        ) : activeSection === "entrySlots" ? (
          <EntrySlotManagement events={events} initialEventType={selectedEventType === "all" ? "" : selectedEventType} />
        ) : activeSection === "seatSelection" ? (
          <SeatSelectionTab
            events={events}
            initialEventType={selectedEventType === "all" ? "" : selectedEventType}
            onEventsChanged={loadEvents}
            getEventSeats={getEventSeats}
            blockEventSeat={blockEventSeat}
            unblockEventSeat={unblockEventSeat}
            saveZonePrice={saveZonePrice}
            deleteZonePrice={deleteZonePrice}
            sendSeatSelectionAnnouncement={sendSeatSelectionAnnouncement}
            getEventSeatPurchases={getEventSeatPurchases}
            updateSeatPurchaseStatus={updateSeatPurchaseStatus}
          />
        ) : activeSection === "celebrityDinner" ? (
          <CelebrityDinnerManagement events={celebrityDinnerEvents} initialEventType={selectedEventType === "all" ? "" : selectedEventType} />
        ) : activeSection === "seatUpgrades" ? (
          <SeatUpgradeManagement events={seatUpgradeEvents} initialEventType={selectedEventType === "all" ? "" : selectedEventType} />
        ) : activeSection === "members" ? (
          renderMembersSection()
        ) : activeSection === "npoPayments" ? (
          <NpoMembershipSettings />
        ) : activeSection === "paymentMethods" ? (
          <PaymentMethodsSettings />
        ) : activeSection === "agm" ? (
          <AgmSettings />
        ) : activeSection === "partners" ? (
          renderPartnersSection()
        ) : activeSection === "carousel" ? (
          renderCarouselSection()
        ) : activeSection === "visits" ? (
          renderVisitsSection()
        ) : activeSection === "reconciliation" ? (
          renderReconciliationSection()
        ) : (
          <>
        {/* Stats */}
        <div className="admin-stats">
          <div className="stat-box">
            <div className="stat-value">{stats.totalRegistrations}</div>
            <div className="stat-label">Registrations</div>
          </div>
          <div className="stat-box">
            <div className="stat-value">{stats.totalPeople}</div>
            <div className="stat-label">Total Attendees</div>
          </div>
        </div>

        <div className="attendee-summary-block">
          <div className="attendee-summary-heading">Visible attendee breakdown</div>
          <div className="admin-stats attendee-summary-stats">
            {attendeeSummaryStats.map((item) => (
              <div className="stat-box" key={item.label}>
                <div className="stat-value">{item.value}</div>
                <div className="stat-label">{item.label}</div>
              </div>
            ))}
          </div>
        </div>

        {/* Controls */}
        <div className="admin-controls">
          <input
            type="text"
            placeholder="Search by name, email, or phone..."
            value={searchTerm}
            onChange={(e) => setSearchTerm(e.target.value)}
            className="search-input"
          />
          <select
            value={selectedEventType}
            onChange={(e) => setSelectedEventType(e.target.value)}
            className="search-input"
            aria-label="Filter by event"
          >
            <option value="all">All events</option>
            {events.map((evt) => (
              <option key={evt.eventType} value={evt.eventType}>
                {evt.eventName || evt.eventType}
              </option>
            ))}
          </select>
          <select
            value={eventDateFilter}
            onChange={(e) => setEventDateFilter(e.target.value)}
            className="search-input"
            aria-label="Filter by event date"
          >
            <option value="all">All event dates</option>
            {eventDateOptions.map((eventDate) => (
              <option key={eventDate} value={eventDate}>
                {formatEventDate(eventDate)}
              </option>
            ))}
          </select>
          <select
            value={paymentStatusFilter}
            onChange={(e) => setPaymentStatusFilter(e.target.value)}
            className="search-input"
            aria-label="Filter by payment status"
          >
            <option value="all">All payment statuses</option>
            {PAYMENT_STATUSES.map((status) => (
              <option key={status.key} value={status.key}>
                {status.label}
              </option>
            ))}
          </select>
          <select
            value={registrationOptionFilter}
            onChange={(e) => setRegistrationOptionFilter(e.target.value)}
            className="search-input"
            aria-label="Filter by registration option"
          >
            <option value="all">All registration options</option>
            {ONAM_OPTIONS.map((option) => (
              <option key={option.value} value={option.value}>
                {option.label}
              </option>
            ))}
          </select>
          <select
            value={sadhyaSlotFilter}
            onChange={(e) => setSadhyaSlotFilter(e.target.value)}
            className="search-input"
            aria-label="Filter by Sadhya slot selection"
          >
            <option value="all">All Sadhya slot statuses</option>
            <option value="missing">Sadhya slot not selected</option>
            <option value="selected">Sadhya slot selected</option>
          </select>
          {/* <button className="button primary" onClick={openNewRegistrationForm}>
            ➕ Add Registration
          </button> */}
          <button
            className="button primary"
            onClick={handleExport}
            disabled={isLoading || registrations.length === 0}
          >
            {isLoading ? "Exporting..." : "📥 Export as CSV"}
          </button>
          <button
            className="button secondary"
            onClick={loadRegistrations}
            disabled={isLoading}
          >
            {isLoading ? "Loading..." : "🔄 Refresh"}
          </button>
        </div>

        <div className="admin-controls" aria-label="Bulk registration actions">
          <strong>{selectedRegistrationIds.size} selected</strong>
          <select
            value={bulkRegistrationAction}
            onChange={(e) => setBulkRegistrationAction(e.target.value)}
            className="search-input"
            aria-label="Bulk action"
            disabled={isProcessingBulkAction}
          >
            {BULK_REGISTRATION_ACTIONS.map((action) => (
              <option key={action.key} value={action.key}>{action.label}</option>
            ))}
          </select>
          <button
            className="button primary"
            type="button"
            onClick={handleBulkRegistrationAction}
            disabled={isProcessingBulkAction || selectedRegistrationIds.size === 0}
          >
            {isProcessingBulkAction ? "Processing..." : "Apply to selected"}
          </button>
        </div>

        {/* Error message */}
        {error && <div className="error-banner">{error}</div>}
        {bulkActionMessage && <div className="success-banner">{bulkActionMessage}</div>}

        {/* Registrations table */}
        {isLoading ? (
          <div className="loading">Loading registrations...</div>
        ) : filteredRegistrations.length === 0 ? (
          <div className="empty-state">
            {searchTerm ? "No matches found" : "No registrations yet"}
          </div>
        ) : (
          <div className="table-container">
            <table className="registrations-table">
              <thead>
                <tr>
                  <th className="registrations-table-count-head">
                    <input
                      type="checkbox"
                      aria-label="Select all visible registrations"
                      checked={filteredRegistrations.length > 0 && filteredRegistrations.every(
                        (registration) => selectedRegistrationIds.has(registration.registrationId)
                      )}
                      onChange={(e) => toggleVisibleRegistrationSelection(e.target.checked)}
                    />
                  </th>
                  <th className="registrations-sticky-col registrations-sticky-col-1" onClick={() => handleSort("firstName")}>
                    Name {sortConfig.key === "firstName" && (sortConfig.direction === "asc" ? "↑" : "↓")}
                  </th>
                  {ONAM_TABLE_COLUMNS.map((column) => (
                    <th key={column.key} className="registrations-table-count-head">{column.label}</th>
                  ))}
                  <th className="registrations-table-count-head">Total</th>
                  <th onClick={() => handleSort("paymentStatus")}>
                    Payment {sortConfig.key === "paymentStatus" && (sortConfig.direction === "asc" ? "↑" : "↓")}
                  </th>
                  <th className="registrations-table-count-head">Amount to pay</th>
                  <th onClick={() => handleSort("submittedAt")}>
                    Registered {sortConfig.key === "submittedAt" && (sortConfig.direction === "asc" ? "↑" : "↓")}
                  </th>
                  <th>Actions</th>
                </tr>
              </thead>
              <tbody>
                {filteredRegistrations.map((reg) => (
                  <tr key={reg.registrationId}>
                    <td className="registrations-table-count-cell">
                      <input
                        type="checkbox"
                        aria-label={`Select ${reg.name || reg.email || "registration"}`}
                        checked={selectedRegistrationIds.has(reg.registrationId)}
                        onChange={() => toggleRegistrationSelection(reg.registrationId)}
                      />
                    </td>
                    <td className="registrations-sticky-col registrations-sticky-col-1">
                      <strong>{reg.name || `${reg.firstName || ""} ${reg.lastName || ""}`.trim()}</strong>
                      <div className="registrations-contact-meta">{reg.email || "—"}</div>
                      <div className="registrations-contact-meta">{reg.whatsapp || reg.phone || "—"}</div>
                      {reg.registrationType === "onam" ? (
                        <span className="reg-type-badge onam">{reg.registrationOptionLabel || "Onam"}</span>
                      ) : null}
                      {reg.slotName || reg.slotId ? (
                        <div className="registrations-contact-meta">
                          🎟 {reg.slotName || reg.slotId}
                          {reg.slotSeats ? ` (${reg.slotSeats} seat${reg.slotSeats === 1 ? "" : "s"})` : ""}
                        </div>
                      ) : null}
                    </td>
                    {ONAM_TABLE_COLUMNS.map((column) => {
                      const value = Number(reg.attendeeCounts?.[column.key]);
                      const isOnam = reg.registrationType === "onam";
                      return (
                        <td key={column.key} className="registrations-table-count-cell">
                          {isOnam ? (Number.isFinite(value) ? value : 0) : "—"}
                        </td>
                      );
                    })}
                    <td className="registrations-table-count-cell">
                      {getRegistrationTotalAttendees(reg) || "—"}
                    </td>
                    <td>
                      {(() => {
                        const status = PAYMENT_STATUSES.find((s) => s.key === (reg.paymentStatus || "pending"));
                        const balance = getBalanceDue(reg);
                        return (
                          <div className="payment-cell">
                            <span className={`payment-badge ${status?.key || "pending"}`}>
                              {status?.label || "Pending"}
                            </span>
                            {balance ? (
                              <span className="balance-note">
                                {balance > 0
                                  ? `Balance ¥${balance.toLocaleString()}`
                                  : `Refund ¥${Math.abs(balance).toLocaleString()}`}
                              </span>
                            ) : null}
                          </div>
                        );
                      })()}
                    </td>
                    <td className="registrations-table-count-cell">
                      {(() => {
                        const amountToPay = getAmountToPay(reg);
                        if (amountToPay == null) return "—";
                        if (amountToPay === 0) return "Paid";
                        return amountToPay > 0
                          ? `¥${amountToPay.toLocaleString()}`
                          : `Refund ¥${Math.abs(amountToPay).toLocaleString()}`;
                      })()}
                    </td>
                    <td>
                      <time>{reg.submittedAt ? new Date(reg.submittedAt).toLocaleDateString() : "—"}</time>
                    </td>
                    <td>
                      <div className="row-actions">
                        <button className="button secondary small" type="button" onClick={() => openEditRegistrationForm(reg)}>
                          Edit
                        </button>
                        {reg.registrationOption === "event" ? null : (
                          <button
                            className="button secondary small"
                            type="button"
                            onClick={() => openSlotReassign(reg)}
                            disabled={isCheckedIn(reg)}
                            title={
                              isCheckedIn(reg)
                                ? "Already checked in — the entry slot can no longer be reassigned"
                                : undefined
                            }
                          >
                            Change Slot
                          </button>
                        )}
                        {isSeatEligible(reg) ? (
                          <button
                            className="button secondary small"
                            type="button"
                            onClick={() => openSeatAllocation(reg)}
                          >
                            Allocate Seats
                          </button>
                        ) : null}
                        <button
                          className="button secondary small"
                          type="button"
                          onClick={() => handleDeleteRegistration(reg.registrationId)}
                          title={
                            isCheckedIn(reg)
                              ? "Already checked in — deleting needs an override and will not free up the used seats"
                              : undefined
                          }
                        >
                          Delete
                        </button>
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
              <tfoot>
                <tr>
                  <td colSpan={2} className="registrations-table-total-label">
                    Visible totals
                  </td>
                  {ONAM_TABLE_COLUMNS.map((column) => (
                    <td key={column.key} className="registrations-table-count-cell registrations-table-total-cell">
                      {visibleOnamTotals[column.key] || 0}
                    </td>
                  ))}
                  <td className="registrations-table-count-cell registrations-table-total-cell">
                    {visibleRegistrationTotal || 0}
                  </td>
                  <td colSpan={4} className="registrations-table-total-note">
                    Based on the current filters.
                  </td>
                </tr>
              </tfoot>
            </table>
          </div>
        )}

        {/* Footer */}
        <div className="admin-footer">
          Showing {filteredRegistrations.length} of {stats.totalRegistrations} registrations
        </div>
          </>
        )}

        {showRegistrationForm ? renderRegistrationModal() : null}
        {showMemberForm ? renderMemberModal() : null}
        {npoPanel ? renderNpoModal() : null}
        {slotReassignTarget ? renderSlotReassignModal() : null}
        {seatAllocationTarget ? renderSeatAllocationModal() : null}
      </section>
  );

  if (embedded) {
    return dashboardContent;
  }

  return (
    <>
      <Navbar navigateTo={() => (window.location.pathname = HOME_PATH)} brand="WMF Japan Admin" showUserMenu={false} />
      <main className="page-wrap">
        <div className="event-header">
          <div className="banner-title">Admin Dashboard</div>
          <div className="sky-bar" />
        </div>

        {dashboardContent}
      </main>
    </>
  );
}
