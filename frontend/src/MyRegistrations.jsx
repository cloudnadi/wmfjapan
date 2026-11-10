import { useEffect, useMemo, useRef, useState } from "react";
import { siteConfig } from "./siteConfig";
import QRCode from "qrcode";
import Navbar from "./Navbar";
import {
  getMyRegistrations,
  updateMyRegistration,
  deleteMyRegistration,
  checkNpoMembership,
  getAvailableEntrySlots,
  changeMyRegistrationAllocations,
  getMySeatPurchases,
  getMySeatPurchase,
  cancelMySeatPurchase,
  checkoutMySeatPurchase,
  getMyCelebrityDinner,
  saveMyCelebrityDinner,
  cancelMyCelebrityDinner,
  checkoutMyCelebrityDinner,
} from "./api";
import { getRegistrantEmail } from "./registrantAuth";
import EntrySlotSelector from "./EntrySlotSelector";
import { seatDisplayLabel } from "seat-selection-plugin";
import { getMySeatUpgrades } from "@plugins/seat-upgrade/frontend/api";

// Celebrity Event seat selection applies only to these registration options —
// the Onam-only option has no assigned seating for that event. The picker
// itself lives on its own page (see HallSeatSelection.jsx), not inline here.
const SEATED_OPTIONS = ["event", "combo"];
// Read by HallSeatSelection on mount so a click from a specific registration's
// card lands on that registration instead of making the visitor pick again.
const PREFERRED_SEAT_REGISTRATION_KEY = "wmf-seat-selection-registration-id";

const SEAT_PAYMENT_METHOD_LABELS = { paypay: "PayPay", square: "Credit/Debit Card (Square)", bank: "Bank Transfer" };
const SEAT_STATUS_LABELS = {
  pending: "Awaiting Payment",
  paid: "Paid",
  refund_requested: "Refund Requested",
  cancelled: "Cancelled",
};

const HOME_PATH = "/";
const EVENTS_PATH = "/events";
const HALL_SEAT_SELECTION_PATH = "/my/hall-seat-selection";

const EMAIL_REGEX = /^[^@\s]+@[^@\s]+\.[^@\s]+$/;
const PHONE_REGEX = /^\+\d{1,4}[\s-]?\d[\d\s-]{5,18}$/;

const yen = (value) => `¥${Number(value || 0).toLocaleString("en-US")}`;

// Copied from CelebrityDinnerSignup.jsx — same "copied constant" convention
// as the rest of this file's Dinner with Celebrity code.
function formatDinnerDate(value) {
  if (!value) return "";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "";
  return date.toLocaleString(undefined, {
    month: "long", day: "numeric", year: "numeric", hour: "numeric", minute: "2-digit",
  });
}

// Mirrors plugins/seat-upgrade/frontend/SeatUpgrade.jsx's STATUS_LABELS —
// duplicated rather than imported since that file isn't a shared module,
// same "copied constant" convention the plugin's own emailer.py files use.
const SEAT_UPGRADE_STATUS_LABELS = {
  pending: "Payment Pending",
  paid: "Paid",
  refund_requested: "Refund Requested",
};

// Mirrors plugins/seat-upgrade/frontend/SeatUpgrade.jsx's own PAYMENT_METHODS
// / STATUS_LABELS — same "online vs. bank transfer" checkout convention, for
// the standalone Dinner with Celebrity sign-up's own payment (the bundled
// seat-upgrade add-on above pays through that plugin's own checkout instead).
const DINNER_PAYMENT_METHODS = [
  { key: "paypay", label: "PayPay", online: true },
  { key: "square", label: "Credit Card", online: true },
  { key: "bank", label: "Bank Transfer", online: false },
];

const DINNER_STATUS_LABELS = {
  pending: "Payment Pending",
  paid: "Paid",
  refund_requested: "Refund Requested",
  cancelled: "Cancelled",
};

const MY_REGISTRATIONS_PATH = "/my/registrations";

function formatDateOnly(value) {
  if (!value) return "";
  const date = new Date(`${value}T00:00:00Z`);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleDateString(undefined, { month: "long", day: "numeric", year: "numeric", timeZone: "UTC" });
}

// Onam participation options (labels mirror lambdas/my_registrations/onam_pricing.py).
const ONAM_OPTIONS = [
  { key: "onam", label: "Onam Only" },
  { key: "event", label: "Celebrity Event Only" },
  { key: "combo", label: "Onam + Celebrity Event" },
];

const ONAM_COUNT_FIELDS = [
  { key: "adults", label: "Adults", sub: "15 years & above", min: 0 },
  { key: "kids914", label: "Kids (9–14)", sub: "9 to 14 years", min: 0 },
  { key: "kids68", label: "Kids (6–8)", sub: "6 to 8 years", min: 0 },
  { key: "kidsUnder6", label: "Kids (under 6)", sub: "Below 6 years, no separate seating for Celebrity Event", min: 0 },
];

// Base (non-member) unit prices and the adults-only NPO discount percentage,
// mirroring lambdas/my_registrations/onam_pricing.py — used only to show a
// live "estimated total" preview while editing; the server recomputes the
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

const STANDARD_COUNT_FIELDS = [
  { key: "adults", label: "Adults" },
  { key: "kids914", label: "Kids (9–14)" },
  { key: "kids68", label: "Kids (6–8)" },
  { key: "kidsUnder6", label: "Kids (under 6)" },
];

const PAYMENT_METHODS = [
  { key: "credit", label: "Credit Card" },
  { key: "paypay", label: "PayPay" },
  { key: "bank", label: "Bank Transfer" },
];

// Older records may carry a different spelling/casing of a known method (the
// pre-rename "paypal", or a label like "PayPay"/"Credit Card" from an admin's
// free-text entry) — recognize those aliases case-insensitively so the
// matching pill still lights up. Mirrors the backend's
// _normalize_payment_method. Anything else (e.g. "cash") is returned as-is.
function normalizePaymentMethod(value) {
  const raw = String(value || "");
  const v = raw.trim().toLowerCase();
  if (["credit", "card", "creditcard", "credit_card", "credit card"].includes(v)) return "credit";
  if (["paypal", "paypay"].includes(v)) return "paypay";
  if (["bank", "banktransfer", "bank_transfer", "bank transfer", "transfer"].includes(v)) return "bank";
  return raw;
}

const PAYMENT_STATUS_LABELS = {
  pending: "Payment Pending",
  paid: "Paid",
  refunded: "Refunded",
};

function PaymentStatusBadge({ status }) {
  const key = PAYMENT_STATUS_LABELS[status] ? status : "pending";
  return <span className={`payment-badge ${key}`}>{PAYMENT_STATUS_LABELS[key]}</span>;
}

// Amount received and any outstanding balance. A balance appears when an edit
// changed the total after payment was recorded, so the registrant sees the
// difference they owe (or are owed back).
function PaymentBalanceRows({ reg }) {
  const paid = Number(reg.paidAmount) || 0;
  // No payment recorded yet → nothing to reconcile; the Total line stands alone.
  if (paid <= 0) {
    return null;
  }
  const balance =
    reg.balanceDue != null
      ? Number(reg.balanceDue) || 0
      : (Number(reg.totalAmount) || 0) - paid;
  return (
    <>
      <div className="regform-price-row">
        <div>Amount paid</div>
        <div />
        <div />
        <div>{yen(paid)}</div>
      </div>
      {balance ? (
        <div className="regform-price-row balance-due">
          <div>{balance > 0 ? "Balance due" : "Refund due"}</div>
          <div />
          <div />
          <div>{yen(Math.abs(balance))}</div>
        </div>
      ) : null}
    </>
  );
}

const isOnam = (reg) =>
  reg?.registrationType === "onam" || !!reg?.registrationOption;

// Mirrors the backend's onam/standard attendeeCounts mapping to entry-slot seats.
function getRequiredSeatsForRegistration(reg) {
  const attendee_cnts = reg?.attendeeCounts || {};
  if (reg?.registrationType === "onam" || "adults" in attendee_cnts) {
    return (
      (Number(attendee_cnts.adults) || 0) +
      (Number(attendee_cnts.kids914) || 0) +
      (Number(attendee_cnts.kids68) || 0) +
      (Number(attendee_cnts.kidsUnder6) || 0)
    );
  }
  return (Number(attendee_cnts.adults) || 0) + (Number(attendee_cnts.kids914) || 0) + (Number(attendee_cnts.kids68) || 0) + (Number(attendee_cnts.kidsUnder6) || 0);
}

// Seed the slot picker from the registration's current allocation(s), falling
// back to the legacy single-slot fields for registrations predating them.
function getInitialAllocations(reg) {
  if (Array.isArray(reg.allocations) && reg.allocations.length) {
    return reg.allocations.map((a) => ({
      slotId: a.entrySlotId,
      seatCount: Number(a.seatCount) || 0,
    }));
  }
  if (reg.slotId) {
    return [{ slotId: reg.slotId, seatCount: getRequiredSeatsForRegistration(reg) }];
  }
  return [];
}

export default function MyRegistrations({ navigateTo, onLogout }) {
  const [registrations, setRegistrations] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [editing, setEditing] = useState(null); // registrationId being edited
  const [deletingId, setDeletingId] = useState(null); // registrationId pending delete confirm
  const [busyId, setBusyId] = useState(null); // registrationId with an in-flight save/delete
  // registrationId -> that registration's non-cancelled seat-upgrade
  // requests, fetched from the seat-upgrade plugin's own API (this page
  // has no other way to see a plugin-owned table — see
  // plugins/seat-upgrade/README.md for the module boundary).
  const [seatUpgradesByRegistration, setSeatUpgradesByRegistration] = useState({});

  const email = getRegistrantEmail() || "";

  const loadSeatUpgrades = () => {
    getMySeatUpgrades()
      .then((regs) => {
        const map = {};
        (regs || []).forEach((r) => {
          map[r.registrationId] = {
            upgrades: (r.upgrades || []).filter((u) => u.status !== "cancelled"),
            seatsInParty: Number(r.seatsInParty) || 0,
            seatsAlreadyUpgraded: Number(r.seatsAlreadyUpgraded) || 0,
          };
        });
        setSeatUpgradesByRegistration(map);
      })
      .catch(() => {
        // Best-effort: e.g. the plugin is disabled for every event this
        // registrant has. The main registrations list must not fail
        // because of it — a registration just shows no seat-upgrade info.
      });
  };

  const load = () => {
    setLoading(true);
    setError("");
    getMyRegistrations()
      .then((items) => setRegistrations(items || []))
      .catch((err) => setError(err?.message || "Could not load your registrations."))
      .finally(() => setLoading(false));
    loadSeatUpgrades();
  };

  useEffect(() => {
    load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const handleSave = async (registrationId, payload) => {
    setBusyId(registrationId);
    setError("");
    try {
      const updated = await updateMyRegistration(registrationId, payload);
      setRegistrations((prev) =>
        prev.map((r) => (r.registrationId === registrationId ? { ...r, ...updated } : r))
      );
      setEditing(null);
    } catch (err) {
      setError(err?.message || "Could not update your registration.");
    } finally {
      setBusyId(null);
    }
  };

  const handleDelete = async (registrationId) => {
    setBusyId(registrationId);
    setError("");
    try {
      await deleteMyRegistration(registrationId);
      setRegistrations((prev) => prev.filter((r) => r.registrationId !== registrationId));
      setDeletingId(null);
    } catch (err) {
      setError(err?.message || "Could not delete your registration.");
    } finally {
      setBusyId(null);
    }
  };

  // Changing the entry slot is allowed regardless of payment status, so it's
  // handled separately from the payment-gated full edit flow.
  const handleSlotChange = async (registrationId, allocations) => {
    const updated = await changeMyRegistrationAllocations(registrationId, allocations);
    setRegistrations((prev) =>
      prev.map((r) => (r.registrationId === registrationId ? { ...r, ...updated } : r))
    );
    return updated;
  };

  // Seat selection lives on its own page. Stash which registration was
  // clicked so HallSeatSelection can jump straight to it.
  const goToSeatSelection = (registrationId) => {
    try {
      sessionStorage.setItem(PREFERRED_SEAT_REGISTRATION_KEY, registrationId);
    } catch {
      // Private-mode / storage-disabled: HallSeatSelection falls back to its
      // own picker when there's more than one eligible registration.
    }
    navigateTo(HALL_SEAT_SELECTION_PATH);
  };

  const nav = (
    <Navbar
      navigateTo={navigateTo}
    />
  );

  return (
    <>
      {nav}
      <main className="page-wrap regform-reg">
        <header className="event-header">
          <div className="banner-title">My Registrations</div>
          <div className="banner-sub-title">
            {email ? `Signed in as ${email}` : "Manage your event registrations"}
          </div>
          <div className="sky-bar" />
        </header>

        {error ? <p className="error-banner">{error}</p> : null}

        {loading ? (
          <section className="form-card">
            <div className="form-body">
              <p className="field-note">Loading your registrations…</p>
            </div>
          </section>
        ) : registrations.length === 0 ? (
          <section className="form-card">
            <div className="form-body" style={{ textAlign: "center" }}>
              <p className="sub">You don't have any registrations yet.</p>
              <div className="btn-row" style={{ justifyContent: "center" }}>
                <button
                  type="button"
                  className="btn-primary"
                  onClick={() => navigateTo(EVENTS_PATH)}
                >
                  Browse events →
                </button>
              </div>
            </div>
          </section>
        ) : (
          <>
            {registrations.map((reg, index) => (
              <RegistrationCard
                key={reg.registrationId}
                reg={reg}
                isFirst={index === 0}
                isEditing={editing === reg.registrationId}
                isDeleting={deletingId === reg.registrationId}
                busy={busyId === reg.registrationId}
                onEdit={() => {
                  setDeletingId(null);
                  setEditing(reg.registrationId);
                }}
                onCancelEdit={() => setEditing(null)}
                onSave={(payload) => handleSave(reg.registrationId, payload)}
                onAskDelete={() => {
                  setEditing(null);
                  setDeletingId(reg.registrationId);
                }}
                onCancelDelete={() => setDeletingId(null)}
                onConfirmDelete={() => handleDelete(reg.registrationId)}
                onChangeSlot={(allocations) => handleSlotChange(reg.registrationId, allocations)}
                onGoToSeatSelection={() => goToSeatSelection(reg.registrationId)}
                navigateTo={navigateTo}
                seatUpgradeInfo={seatUpgradesByRegistration[reg.registrationId]}
              />
            ))}
            <div className="btn-row" style={{ justifyContent: "flex-end", marginTop: "10px" }}>
              <button
                type="button"
                className="btn-primary"
                onClick={() => navigateTo(EVENTS_PATH)}
              >
                + Register for another event
              </button>
            </div>
          </>
        )}
      </main>
    </>
  );
}

function RegistrationCard({
  reg,
  isFirst,
  isEditing,
  isDeleting,
  busy,
  onEdit,
  onCancelEdit,
  onSave,
  onAskDelete,
  onCancelDelete,
  onConfirmDelete,
  onChangeSlot,
  onGoToSeatSelection,
  navigateTo,
  seatUpgradeInfo,
}) {
  const onam = isOnam(reg);
  const isPaid = String(reg.paymentStatus || "").toLowerCase() === "paid";
  const seatUpgrades = seatUpgradeInfo?.upgrades || [];
  // Once every seat in the party already has an active upgrade (whether
  // from one "whole family" request or several "per person" ones), there's
  // nothing left to upgrade — same remainingSeats <= 0 gating
  // SeatUpgrade.jsx itself uses to hide its own request form. Undefined
  // (seat-upgrade data hasn't loaded yet, or the plugin has nothing for
  // this registration) is treated as "not fully upgraded" so the button
  // doesn't flash on then off once the fetch resolves.
  const seatsFullyUpgraded =
    !!seatUpgradeInfo && seatUpgradeInfo.seatsInParty > 0 &&
    seatUpgradeInfo.seatsAlreadyUpgraded >= seatUpgradeInfo.seatsInParty;
  // The seat-upgrade plugin is opt-in per event (Events tab admin config) —
  // reg.seatUpgrade is computed server-side by my_registrations/handler.py's
  // event_seat_upgrade_config, since a registrant here has no other way to
  // see the owning event's config.
  const canUpgradeSeat =
    isPaid && (reg.registrationOption === "event" || reg.registrationOption === "combo") && !!reg.seatUpgrade?.enabled;
  const seatUpgradeUrl = reg.seatUpgrade?.url || "/my/seat-upgrade";
  const seatUpgradeIsExternal = /^https?:\/\//i.test(seatUpgradeUrl);
  // Once its booking window has closed there's nothing useful behind the
  // button, so it's hidden entirely rather than shown disabled — same
  // treatment once every seat is already upgraded.
  const showUpgradeSeatButton =
    canUpgradeSeat && !!navigateTo && reg.seatUpgrade?.windowStatus !== "ended" && !seatsFullyUpgraded;
  // Entry slots seat the Onam Sadhya, so only the Sadhya-inclusive options
  // (Onam-only and the combo) need one — the Anniversary-only option skips
  // the meal entirely.
  const needsSadhyaSlot = reg.registrationOption !== "event";
  // Celebrity Event seat selection is the opposite split: only the options
  // that include the Celebrity Event need an assigned seat.
  const needsSeats = SEATED_OPTIONS.includes(reg.registrationOption);
  const sadhyaSlotNotStarted = reg.sadhyaSlotWindow?.status === "not_started";
  const showEntrySlotButton = isPaid && needsSadhyaSlot && reg.sadhyaSlotWindow?.status !== "ended";
  const title = onam
    ? reg.registrationOptionLabel || "Onam Registration"
    : reg.eventType || "Registration";

  // Seat purchase status/allowance shown right on the card — the picker
  // itself still lives on its own page (HallSeatSelection.jsx), but there's
  // no separate landing step there anymore to show this on.
  const [seatConfig, setSeatConfig] = useState(null);
  const [seatPurchases, setSeatPurchases] = useState([]);
  const [seatInfoLoading, setSeatInfoLoading] = useState(false);
  const [seatInfoError, setSeatInfoError] = useState("");

  const loadSeatInfo = () => {
    setSeatInfoLoading(true);
    setSeatInfoError("");
    getMySeatPurchases(reg.registrationId)
      .then(async (data) => {
        setSeatConfig({
          enabled: data.enabled,
          seatSelectionWindow: data.seatSelectionWindow || { status: "open" },
          seatsAllowed: data.seatsAllowed ?? 0,
          seatsClaimed: data.seatsClaimed ?? 0,
          seatsRemaining: data.seatsRemaining ?? 0,
        });
        let purchases = data.purchases || [];
        // A PayPay/Square payment can complete on the provider's side
        // without ever reaching us — the webhook may not have landed, and
        // the registrant may never have followed the redirect back to our
        // return URL (closing the browser/app on the provider's own
        // "payment complete" screen instead). Fetching any still-pending
        // online purchase re-checks it directly against the provider (see
        // get_my_seat_purchase's own reconcile) and self-heals, instead of
        // leaving it stuck on "Awaiting Payment" until the registrant
        // happens to revisit via that exact return link.
        const pendingOnline = purchases.filter(
          (p) => p.status === "pending" && (p.paymentProvider === "paypay" || p.paymentProvider === "square")
        );
        if (pendingOnline.length > 0) {
          const reconciled = await Promise.all(
            pendingOnline.map((p) => getMySeatPurchase(reg.registrationId, p.purchaseId).catch(() => null))
          );
          purchases = purchases.map((p) => reconciled.find((r) => r && r.purchaseId === p.purchaseId) || p);
        }
        setSeatPurchases(purchases);
      })
      .catch((err) => setSeatInfoError(err?.message || "Could not load seat selection status."))
      .finally(() => setSeatInfoLoading(false));
  };

  useEffect(() => {
    if (isPaid && needsSeats) loadSeatInfo();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [reg.registrationId, isPaid, needsSeats]);

  const handleCancelSeatPurchase = async (purchaseId) => {
    if (!window.confirm("Cancel this seat purchase? If it's already paid, we'll start a refund.")) return;
    setSeatInfoError("");
    try {
      const updated = await cancelMySeatPurchase(reg.registrationId, purchaseId);
      setSeatPurchases((prev) => prev.map((p) => (p.purchaseId === purchaseId ? updated : p)));
      loadSeatInfo();
    } catch (err) {
      setSeatInfoError(err?.message || "Could not cancel this seat purchase.");
    }
  };

  const handleResumeSeatCheckout = async (purchase) => {
    if (!purchase.paymentProvider) return;
    setSeatInfoError("");
    try {
      const { checkoutUrl } = await checkoutMySeatPurchase(reg.registrationId, purchase.purchaseId, purchase.paymentProvider);
      window.location.href = checkoutUrl;
    } catch (err) {
      setSeatInfoError(err?.message || "Could not resume checkout.");
    }
  };

  // Slot allocation is payment-gated and separate from the full edit flow.
  // It closes for good once anybody on the registration has been admitted to
  // the Sadhya — as do editing and deleting. The backend rejects all three;
  // this just doesn't offer a button that fails. Celebrity Event seat
  // selection is unrelated (there's no check-in tracking for it yet), so it
  // must not be gated on this.
  const sadhyaCheckedIn = (Array.isArray(reg.allocations) ? reg.allocations : []).some(
    (pass) => (Number(pass.checkedInCount) || 0) > 0
  );
  const [showSlotPicker, setShowSlotPicker] = useState(false);
  const [slotOptions, setSlotOptions] = useState([]);
  const [pendingAllocations, setPendingAllocations] = useState([]);
  const [slotLoading, setSlotLoading] = useState(false);
  const [slotSaving, setSlotSaving] = useState(false);
  const [slotError, setSlotError] = useState("");

  const openSlotPicker = async () => {
    setShowSlotPicker(true);
    setSlotError("");
    setSlotLoading(true);
    setPendingAllocations(getInitialAllocations(reg));
    try {
      const requiredSeats = getRequiredSeatsForRegistration(reg) || 1;
      setSlotOptions(await getAvailableEntrySlots(reg.eventType, requiredSeats));
    } catch (err) {
      setSlotError(err?.message || "Could not load entry slots.");
    } finally {
      setSlotLoading(false);
    }
  };

  const handleSaveSlot = async () => {
    if (slotSaving) return;
    const requiredSeats = getRequiredSeatsForRegistration(reg);
    const allocatedSeats = pendingAllocations.reduce((sum, a) => sum + Number(a.seatCount || 0), 0);
    if (pendingAllocations.length === 0) {
      setSlotError("Please assign at least one entry slot.");
      return;
    }
    if (allocatedSeats !== requiredSeats) {
      setSlotError(`Allocate all ${requiredSeats} seated attendee${requiredSeats === 1 ? "" : "s"} across the selected entry slots.`);
      return;
    }
    setSlotSaving(true);
    setSlotError("");
    try {
      await onChangeSlot(pendingAllocations);
      setShowSlotPicker(false);
    } catch (err) {
      setSlotError(err?.message || "Could not change your entry slot.");
    } finally {
      setSlotSaving(false);
    }
  };

  return (
    <section
      className={`form-card${isFirst ? "" : " standalone-card"}`}
      style={{ marginBottom: "18px" }}
    >
      <div className="form-body">
        <div className="regform-summary-meta" style={{ justifyContent: "space-between", alignItems: "center" }}>
          <div>
            <div className="section-label" style={{ marginBottom: "4px" }}>{title}</div>
          </div>
          {onam ? (
            <span className={`regform-chip ${Number(reg.discountApplied) > 0 ? "npo" : ""}`}>
              {Number(reg.discountApplied) > 0 ? "NPO Pricing" : "Normal Pricing"}
            </span>
          ) : null}
        </div>

        {isEditing ? (
          onam ? (
            <OnamEditForm reg={reg} busy={busy} onCancel={onCancelEdit} onSave={onSave} />
          ) : (
            <StandardEditForm reg={reg} busy={busy} onCancel={onCancelEdit} onSave={onSave} />
          )
        ) : (
          <>
            {onam ? <OnamDetails reg={reg} /> : <StandardDetails reg={reg} />}

            {/* Lock note sits right under the details it's about, above the
                seat/dinner/upgrade blocks, rather than between them. */}
            {isDeleting ? null : sadhyaCheckedIn ? (
              <div className="regform-note" style={{ marginTop: "16px" }}>
                🔒 You've been checked in at the venue, so your entry slot is fixed
                and this registration can no longer be edited or deleted. Please
                speak to a volunteer if something is wrong.
              </div>
            ) : isPaid ? (
              <div className="regform-note" style={{ marginTop: "16px" }}>
                🔒 This registration is paid and can no longer be edited. Please
                contact us if you need to make a change.
              </div>
            ) : null}

            {/* Celebrity Event seats are a separate event with no check-in
                gate of their own, so this section is independent of the
                Sadhya lock note / entry-slot / seat-upgrade block below. */}
            {isDeleting || !(isPaid && needsSeats) ? null : (
              <div className="event-block event-block-seats">
                <div className="section-label" style={{ marginTop: 0 }}>💺 Celebrity Event Seats</div>

                {seatInfoError ? <p className="error-banner">{seatInfoError}</p> : null}

                {seatInfoLoading ? (
                  <p className="field-note">Loading seat selection status…</p>
                ) : seatInfoError ? null : (
                  <>
                    {!seatConfig?.enabled ? (
                      <p className="field-note">
                        {seatPurchases.length > 0
                          ? "Seat selection is currently closed for this event — your seats are shown below for reference."
                          : "Seat selection isn't open for this event yet."}
                      </p>
                    ) : seatConfig.seatSelectionWindow?.status === "not_started" ? (
                      <p className="field-note">Seat selection opens on {seatConfig.seatSelectionWindow.startDate}.</p>
                    ) : seatConfig.seatSelectionWindow?.status === "ended" ? (
                      <p className="field-note">The seat selection window closed on {seatConfig.seatSelectionWindow.endDate}.</p>
                    ) : null}

                    {seatPurchases.length > 0 ? (
                      <div className="regform-review-grid" style={{ marginTop: "8px" }}>
                        {seatPurchases.map((purchase) => (
                          <div
                            key={purchase.purchaseId}
                            className={`form-card seat-purchase-card status-${purchase.status}`}
                            style={{ padding: "14px" }}
                          >
                            <div className="btn-row" style={{ justifyContent: "space-between", alignItems: "center" }}>
                              <strong>
                                {purchase.checkedIn ? "Checked In" : SEAT_STATUS_LABELS[purchase.status] || purchase.status}
                              </strong>
                              <span>{yen(purchase.totalAmount)}</span>
                            </div>
                            <p className="field-note" style={{ margin: "4px 0" }}>Seats</p>
                            <div className="seat-chip-list">
                              {(purchase.seatIds || []).length > 0 ? (
                                purchase.seatIds.map((seatId) => (
                                  <span
                                    key={seatId}
                                    className={`seat-chip${purchase.status === "cancelled" ? " inactive" : ""}`}
                                  >
                                    {seatDisplayLabel(seatId)}
                                  </span>
                                ))
                              ) : (
                                <span className="field-note">N/A</span>
                              )}
                            </div>
                            <p className="field-note" style={{ margin: "4px 0" }}>
                              Payment: {SEAT_PAYMENT_METHOD_LABELS[purchase.paymentMethod] || purchase.paymentMethod || "None needed"}
                            </p>
                            {purchase.checkedIn ? (
                              <p className="field-note" style={{ margin: "4px 0" }}>
                                ✅ Checked in at the venue — these seats can no longer be changed or cancelled.
                              </p>
                            ) : purchase.status === "refund_requested" ? (
                              <p className="field-note" style={{ margin: "4px 0" }}>
                                A refund is being processed — these seats stay held until it's confirmed.
                              </p>
                            ) : null}
                            {/* No changes (cancel / pay) while the Seat Selection plugin is off for this event. */}
                            {!purchase.checkedIn && seatConfig?.enabled ? (
                              <div className="btn-row" style={{ marginTop: "8px" }}>
                                {purchase.status === "pending" && purchase.checkoutUrl ? (
                                  <button type="button" className="btn-secondary" onClick={() => handleResumeSeatCheckout(purchase)}>
                                    Resume Payment
                                  </button>
                                ) : null}
                                {purchase.status === "pending" || purchase.status === "paid" ? (
                                  <button
                                    type="button"
                                    className="btn-secondary"
                                    onClick={() => handleCancelSeatPurchase(purchase.purchaseId)}
                                  >
                                    Cancel
                                  </button>
                                ) : null}
                              </div>
                            ) : null}
                          </div>
                        ))}
                      </div>
                    ) : (
                      <p className="field-note">No seats selected yet.</p>
                    )}

                    {seatConfig?.seatsAllowed ? (
                      <p className="field-note" style={{ margin: "6px 0 0" }}>
                        Seat allowance: {seatConfig.seatsClaimed} / {seatConfig.seatsAllowed} claimed
                        {seatConfig.seatsRemaining > 0 ? ` — ${seatConfig.seatsRemaining} remaining.` : " — fully claimed."}
                      </p>
                    ) : null}

                    {(() => {
                      const fullyClaimed = (seatConfig?.seatsRemaining ?? 0) <= 0;
                      // Seats this registration currently holds — cancelled
                      // purchases keep their seatIds but hold nothing.
                      const holdsSeats = seatPurchases.some(
                        (p) =>
                          ["pending", "paid", "refund_requested"].includes(p.status) && (p.seatIds || []).length > 0
                      );
                      // The "select more" button is hidden once fully
                      // claimed — for any reason (checked in, a refund still
                      // pending on a cancelled seat, or simply every allowed
                      // seat already bought) there's nothing it could do
                      // (the per-purchase notes above already explain why).
                      // Not fully claimed still shows it even if some other
                      // purchase on this registration is checked in — there
                      // may be remaining allowance left to pick more seats for.
                      // Viewing held seats on the map stays available either way.
                      if (!seatConfig?.enabled) return null; // plugin off: read-only
                      if (fullyClaimed && !holdsSeats) return null;
                      return (
                        <div className="btn-row" style={{ marginTop: "10px" }}>
                          {holdsSeats ? (
                            <button type="button" className="btn-secondary" onClick={onGoToSeatSelection} disabled={busy}>
                              🗺️ View My Seats on Map
                            </button>
                          ) : null}
                          {fullyClaimed ? null : (
                            <button
                              type="button"
                              className="btn-secondary"
                              onClick={onGoToSeatSelection}
                              disabled={
                                busy ||
                                !seatConfig?.enabled ||
                                (seatConfig.seatSelectionWindow?.status ?? "open") !== "open"
                              }
                            >
                              {seatPurchases.length > 0 ? "Select More Seats" : "Select Your Seats"}
                            </button>
                          )}
                        </div>
                      );
                    })()}
                  </>
                )}
              </div>
            )}

            {isDeleting ? (
              <div className="error-banner" style={{ marginTop: "16px" }}>
                <p style={{ margin: "0 0 10px" }}>
                  Delete this registration? This can't be undone.
                </p>
                <div className="btn-row">
                  <button type="button" className="btn-secondary" onClick={onCancelDelete} disabled={busy}>
                    Cancel
                  </button>
                  <button type="button" className="btn-danger" onClick={onConfirmDelete} disabled={busy}>
                    {busy ? "Deleting…" : "Yes, delete"}
                  </button>
                </div>
              </div>
            ) : sadhyaCheckedIn ? null : isPaid ? (
              <>
                {seatUpgrades.length > 0 ? (
                  <div style={{ marginTop: "30px" }}>
                    <div style={{ display: "flex" }}>
                      <div className="section-label" style={{ marginBottom: "10px" }}>Zone Upgrade</div>
                    </div>
                    {seatUpgrades.map((u) => {
                      const zoneItems = (u.zoneBreakdown || []).map((z) => ({
                        key: z.tierId,
                        label: z.count > 1 ? `${z.tierName} ×${z.count}` : z.tierName,
                        qty: z.count,
                        price: z.regularUnitPrice,
                        total: z.subtotal,
                      }));
                      const dinnerItems = [
                        { key: "adultVeg", label: "Dinner — Adults (Veg)", qty: u.dinnerAdultVegGuests || 0, price: u.dinnerPriceAdults || 0 },
                        { key: "adultNonVeg", label: "Dinner — Adults (Non-Veg)", qty: u.dinnerAdultNonVegGuests || 0, price: u.dinnerPriceAdults || 0 },
                        { key: "kids914Veg", label: "Dinner — Kids 9-14 (Veg)", qty: u.dinnerKids914VegGuests || 0, price: u.dinnerPriceKids914 || 0 },
                        { key: "kids914NonVeg", label: "Dinner — Kids 9-14 (Non-Veg)", qty: u.dinnerKids914NonVegGuests || 0, price: u.dinnerPriceKids914 || 0 },
                        { key: "kids68Veg", label: "Dinner — Kids 6-8 (Veg)", qty: u.dinnerKids68VegGuests || 0, price: u.dinnerPriceKids68 || 0 },
                        { key: "kids68NonVeg", label: "Dinner — Kids 6-8 (Non-Veg)", qty: u.dinnerKids68NonVegGuests || 0, price: u.dinnerPriceKids68 || 0 },
                        { key: "kidsUnder6Veg", label: "Dinner — Kids under 6 (Veg)", qty: u.dinnerKidsUnder6VegGuests || 0, price: 0 },
                        { key: "kidsUnder6NonVeg", label: "Dinner — Kids under 6 (Non-Veg)", qty: u.dinnerKidsUnder6NonVegGuests || 0, price: 0 },
                      ].filter((item) => item.qty > 0);
                      return (
                        <div key={u.upgradeId} style={{ marginBottom: "14px" }}>
                          <div className="regform-review-grid">
                            <DetailRow label="Name" value={reg.name} />
                            <DetailRow label="Email" value={reg.email} />
                            <DetailRow
                              label="Payment Method"
                              value={
                                DINNER_PAYMENT_METHODS.find((m) => m.key === (u.paymentProvider || u.paymentMethod))?.label ||
                                u.paymentMethod ||
                                "—"
                              }
                            />
                            <DetailRow
                              label="Payment Status"
                              value={<span className={`payment-badge ${u.status}`}>{SEAT_UPGRADE_STATUS_LABELS[u.status] || u.status}</span>}
                            />
                          </div>
                          <div className="regform-price-table" style={{ marginTop: "10px" }}>
                            <div className="regform-price-head">
                              <div>Item</div>
                              <div>Qty</div>
                              <div>Price</div>
                              <div>Total</div>
                            </div>
                            {zoneItems.map((z) => (
                              <div className="regform-price-row" key={z.key}>
                                <div>{z.label}</div>
                                <div>{z.qty}</div>
                                <div>{yen(z.price)}</div>
                                <div>{yen(z.total)}</div>
                              </div>
                            ))}
                            {dinnerItems.map((item) => (
                              <div className="regform-price-row" key={item.key}>
                                <div>{item.label}</div>
                                <div>{item.qty}</div>
                                <div>{yen(item.price)}</div>
                                <div>{yen(item.price * item.qty)}</div>
                              </div>
                            ))}
                            <div className="regform-price-foot">
                              <div>Total</div>
                              <div />
                              <div />
                              <div>{yen(u.totalAmount)}</div>
                            </div>
                          </div>
                        </div>
                      );
                    })}
                  </div>
                ) : null}

                {showEntrySlotButton || showUpgradeSeatButton ? (
                  <div style={{ marginTop: "12px" }}>
                    {showEntrySlotButton && sadhyaSlotNotStarted ? (
                      <p className="field-note" style={{ marginBottom: "8px" }}>
                        Sadhya slot selection opens {formatDateOnly(reg.sadhyaSlotWindow.startDate)}.
                      </p>
                    ) : null}
                    <div className="btn-row">
                      {showEntrySlotButton ? (
                        <button
                          type="button"
                          className="btn-secondary"
                          onClick={() => (showSlotPicker ? setShowSlotPicker(false) : openSlotPicker())}
                          disabled={busy || sadhyaSlotNotStarted}
                        >
                          {showSlotPicker ? "Cancel Slot Change" : "Change Entry Slot"}
                        </button>
                      ) : null}
                      {showUpgradeSeatButton ? (
                        <button
                          type="button"
                          className="btn-secondary"
                          onClick={() => {
                            if (seatUpgradeIsExternal) {
                              window.location.href = seatUpgradeUrl;
                            } else {
                              navigateTo(seatUpgradeUrl);
                            }
                          }}
                        >
                          Upgrade My Zone
                        </button>
                      ) : null}
                    </div>
                  </div>
                ) : null}

                {showSlotPicker ? (
                  <div style={{ marginTop: "12px" }}>
                    {slotError ? <p className="error-banner" style={{ marginTop: 10 }}>{slotError}</p> : null}
                    <EntrySlotSelector
                      slots={slotOptions}
                      allocations={pendingAllocations}
                      onAllocationsChange={setPendingAllocations}
                      requiredSeats={getRequiredSeatsForRegistration(reg)}
                      loading={slotLoading}
                      loaded={!slotLoading}
                      error=""
                    />
                    <div className="btn-row" style={{ marginTop: "10px" }}>
                      <button
                        type="button"
                        className="btn-secondary"
                        onClick={() => setShowSlotPicker(false)}
                        disabled={slotSaving}
                      >
                        Cancel
                      </button>
                      <button
                        type="button"
                        className="btn-primary"
                        onClick={handleSaveSlot}
                        disabled={slotSaving || slotLoading}
                      >
                        {slotSaving ? "Saving…" : "Save"}
                      </button>
                    </div>
                  </div>
                ) : null}
              </>
            ) : (
              <div className="btn-row" style={{ marginTop: "16px" }}>
                <button type="button" className="btn-secondary" onClick={onEdit}>
                  Edit
                </button>
                {reg.cancellationEnabled !== false ? (
                  <button type="button" className="btn-danger" onClick={onAskDelete}>
                    Cancel registration
                  </button>
                ) : null}
              </div>
            )}

            {isDeleting ? null : (
              <CelebrityDinnerSection
                registrationId={reg.registrationId}
                hasSeatUpgradeDinner={seatUpgrades.some(
                  (u) =>
                    Number(u.dinnerAdultVegGuests) > 0 ||
                    Number(u.dinnerAdultNonVegGuests) > 0 ||
                    Number(u.dinnerKids914VegGuests) > 0 ||
                    Number(u.dinnerKids914NonVegGuests) > 0 ||
                    Number(u.dinnerKids68VegGuests) > 0 ||
                    Number(u.dinnerKids68NonVegGuests) > 0 ||
                    Number(u.dinnerKidsUnder6VegGuests) > 0 ||
                    Number(u.dinnerKidsUnder6NonVegGuests) > 0
                )}
              />
            )}
          </>
        )}
      </div>
    </section>
  );
}

const DINNER_CATEGORY_LABELS = {
  adults: "adult",
  kids914: "kid (9-14)",
  kids68: "kid (6-8)",
  kidsUnder6: "kid (under 6)",
};

function CelebrityDinnerSection({ registrationId, hasSeatUpgradeDinner = false }) {
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [success, setSuccess] = useState("");
  const [saving, setSaving] = useState(false);
  const [config, setConfig] = useState(null);
  const [windowStatus, setWindowStatus] = useState("");
  const [eligibleGuestCounts, setEligibleGuestCounts] = useState({ adults: 0, kids914: 0, kids68: 0, kidsUnder6: 0 });
  const [dinnerReg, setDinnerReg] = useState(null);
  const [remainingCapacity, setRemainingCapacity] = useState(null);
  const [guestCounts, setGuestCounts] = useState({
    adultsVeg: "0",
    adultsNonVeg: "0",
    kids914Veg: "0",
    kids914NonVeg: "0",
    kids68Veg: "0",
    kids68NonVeg: "0",
    kidsUnder6Veg: "0",
    kidsUnder6NonVeg: "0",
  });
  const [paymentMethod, setPaymentMethod] = useState("");
  const [returnBanner, setReturnBanner] = useState(null); // {status} | null

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError("");
    getMyCelebrityDinner(registrationId)
      .then((data) => {
        if (cancelled) return;
        const availableMethods = Array.isArray(data.config?.paymentMethods) ? data.config.paymentMethods : [];
        setConfig(data.config || null);
        setWindowStatus(data.windowStatus || "");
        setEligibleGuestCounts({
          adults: Number(data.eligibleGuestCounts?.adults) || 0,
          kids914: Number(data.eligibleGuestCounts?.kids914) || 0,
          kids68: Number(data.eligibleGuestCounts?.kids68) || 0,
          kidsUnder6: Number(data.eligibleGuestCounts?.kidsUnder6) || 0,
        });
        setDinnerReg(data.registration || null);
        setRemainingCapacity(data.remainingCapacity ?? null);
        setGuestCounts({
          adultsVeg: String(data.registration?.numberOfAdultsVeg ?? 0),
          adultsNonVeg: String(data.registration?.numberOfAdultsNonVeg ?? 0),
          kids914Veg: String(data.registration?.numberOfKids914Veg ?? 0),
          kids914NonVeg: String(data.registration?.numberOfKids914NonVeg ?? 0),
          kids68Veg: String(data.registration?.numberOfKids68Veg ?? 0),
          kids68NonVeg: String(data.registration?.numberOfKids68NonVeg ?? 0),
          kidsUnder6Veg: String(data.registration?.numberOfKidsUnder6Veg ?? 0),
          kidsUnder6NonVeg: String(data.registration?.numberOfKidsUnder6NonVeg ?? 0),
        });
        setPaymentMethod(data.registration?.paymentMethod || availableMethods[0] || "");

        // A registrant lands back here with ?celebrityDinnerId=&provider= after
        // a PayPay or Square hosted checkout for THIS registration's dinner
        // sign-up — see my_registrations/handler.py's checkout_my_celebrity_dinner
        // redirect_url. getMyCelebrityDinner above already reconciled it against
        // the provider, so just read the fresh status back off it.
        const params = new URLSearchParams(window.location.search);
        const returningId = params.get("celebrityDinnerId");
        if (returningId && data.registration?.dinnerRegistrationId === returningId) {
          window.history.replaceState({}, "", MY_REGISTRATIONS_PATH);
          setReturnBanner({ status: data.registration?.status || "pending" });
        }
      })
      .catch((err) => {
        if (!cancelled) setError(err?.message || "Could not load the Dinner with Celebrity details.");
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [registrationId]);

  if (loading || !config) return null;
  // The Celebrity Dinner feature can be switched off after people have
  // registered: their booking stays visible (read-only), but a registration
  // that doesn't exist has nothing to show or start.
  const dinnerOpen = !!config.enabled;
  if (!dinnerOpen && !dinnerReg) return null;

  // A registration picks one dinner path or the other: if the Seat Upgrade
  // add-on above already has a Dinner with Celebrity bundled in (at 50% off),
  // this standalone full-price sign-up isn't offered — and vice versa, the
  // backend refuses this path once that add-on exists.
  if (hasSeatUpgradeDinner && !dinnerReg) {
    return (
      <section className="entry-passes event-block event-block-dinner" aria-label="Dinner with Celebrity">

        <div style={{ display: "flex" }}>
          <div className="section-label" style={{ marginBottom: "10px" }}>🍽️ Dinner with Celebrity</div>
        </div>
        <p className="field-note" style={{ marginTop: "6px" }}>
          Already included with your Zone Upgrade above — see the Zone Upgrade section for guest count and pricing.
        </p>
      </section>
    );
  }

  const totalEligibleGuests =
    eligibleGuestCounts.adults + eligibleGuestCounts.kids914 + eligibleGuestCounts.kids68 + eligibleGuestCounts.kidsUnder6;
  const canRegister = dinnerOpen && windowStatus === "open" && totalEligibleGuests > 0;
  const availableMethods = DINNER_PAYMENT_METHODS.filter((m) => (config.paymentMethods || []).includes(m.key));
  // Guest count/payment method can only be changed while nothing has been
  // paid or refunded yet — mirrors the backend's own guard in
  // my_registrations/handler.py's upsert_my_celebrity_dinner.
  const canEdit = dinnerOpen && (!dinnerReg || dinnerReg.status === "pending");
  const payableOnline = dinnerOpen && dinnerReg && ["paypay", "square"].includes(dinnerReg.paymentProvider || dinnerReg.paymentMethod);
  // Admin-configurable: the Cancel/Cancel & Request Refund button disappears
  // once cancellations are turned off, or once the refund window has closed
  // (empty refundWindowEndsAt = no cutoff) — see CelebrityDinnerManagement.jsx
  // and celebrity_dinner_logic.cancellation_allowed, which the backend
  // enforces independently of this — hiding the button is a UI convenience,
  // not the actual enforcement.
  const canCancelBooking =
    dinnerOpen && config.cancellationEnabled !== false && (!config.refundWindowEndsAt || new Date().toISOString() <= config.refundWindowEndsAt);

  const startCheckout = async (provider) => {
    const { checkoutUrl } = await checkoutMyCelebrityDinner(registrationId, provider);
    window.location.href = checkoutUrl;
  };

  const handleSave = async (event) => {
    event.preventDefault();
    setError("");
    setSuccess("");
    const counts = {};
    for (const key of Object.keys(guestCounts)) {
      counts[key] = Number(guestCounts[key]);
    }
    if (Object.values(counts).some((v) => !Number.isInteger(v) || v < 0)) {
      setError("Guest counts must be whole numbers, 0 or greater.");
      return;
    }
    const totalGuests = Object.values(counts).reduce((sum, v) => sum + v, 0);
    if (totalGuests < 1) {
      setError("Enter at least 1 guest.");
      return;
    }
    for (const [key, label] of Object.entries(DINNER_CATEGORY_LABELS)) {
      const categoryTotal = counts[`${key}Veg`] + counts[`${key}NonVeg`];
      if (categoryTotal > eligibleGuestCounts[key]) {
        setError(`This registration has ${eligibleGuestCounts[key]} ${label} guest(s) eligible; ${categoryTotal} requested.`);
        return;
      }
    }
    if (!paymentMethod) {
      setError("Select a payment method.");
      return;
    }
    setSaving(true);
    try {
      const saved = await saveMyCelebrityDinner(registrationId, counts, paymentMethod);
      setDinnerReg(saved);
      const method = DINNER_PAYMENT_METHODS.find((m) => m.key === paymentMethod);
      if (method?.online) {
        // Go straight to hosted checkout — no separate "Pay Now" click
        // needed for the common case (mirrors SeatUpgrade.jsx's own flow).
        await startCheckout(paymentMethod);
        return;
      }
      setSuccess(dinnerReg ? "Dinner with Celebrity registration updated." : "Registered for the Dinner with Celebrity.");
    } catch (err) {
      setError(err?.message || "Could not save your Dinner with Celebrity registration.");
    } finally {
      setSaving(false);
    }
  };

  const handlePayNow = async () => {
    setError("");
    setSuccess("");
    setSaving(true);
    try {
      await startCheckout(dinnerReg.paymentProvider || dinnerReg.paymentMethod);
    } catch (err) {
      setError(err?.message || "Could not start checkout.");
      setSaving(false);
    }
  };

  const handleCancel = async () => {
    const confirmMessage =
      dinnerReg.status === "paid"
        ? "Cancel your Dinner with Celebrity registration and request a refund?"
        : "Cancel your Dinner with Celebrity registration?";
    if (!window.confirm(confirmMessage)) return;
    setError("");
    setSuccess("");
    setSaving(true);
    try {
      const { registration } = await cancelMyCelebrityDinner(registrationId);
      if (registration?.status === "cancelled") {
        setDinnerReg(null);
        setGuestCounts({
          adultsVeg: "0",
          adultsNonVeg: "0",
          kids914Veg: "0",
          kids914NonVeg: "0",
          kids68Veg: "0",
          kids68NonVeg: "0",
          kidsUnder6Veg: "0",
          kidsUnder6NonVeg: "0",
        });
        setSuccess("Dinner with Celebrity registration cancelled.");
      } else {
        setDinnerReg(registration);
        setSuccess("Cancellation requested — your refund is being processed.");
      }
    } catch (err) {
      setError(err?.message || "Could not cancel your Dinner with Celebrity registration.");
    } finally {
      setSaving(false);
    }
  };

  const dinnerRegCategories = dinnerReg
    ? [
        { label: "adult", plural: "adults", veg: dinnerReg.numberOfAdultsVeg || 0, nonVeg: dinnerReg.numberOfAdultsNonVeg || 0 },
        { label: "kid (9-14)", plural: "kids (9-14)", veg: dinnerReg.numberOfKids914Veg || 0, nonVeg: dinnerReg.numberOfKids914NonVeg || 0 },
        { label: "kid (6-8)", plural: "kids (6-8)", veg: dinnerReg.numberOfKids68Veg || 0, nonVeg: dinnerReg.numberOfKids68NonVeg || 0 },
        { label: "kid (under 6)", plural: "kids (under 6)", veg: dinnerReg.numberOfKidsUnder6Veg || 0, nonVeg: dinnerReg.numberOfKidsUnder6NonVeg || 0 },
      ]
    : [];
  const dinnerRegTotalGuests = dinnerRegCategories.reduce((sum, c) => sum + c.veg + c.nonVeg, 0);
  // Once every eligible guest is already registered, or the booking is no
  // longer editable (paid / refund requested), the per-category price
  // breakdown is just clutter — the receipt box below already shows what
  // was actually charged. Show it again the moment there's room to add or
  // change guests.
  const allEligibleGuestsRegistered = totalEligibleGuests > 0 && dinnerRegTotalGuests >= totalEligibleGuests;
  const showDinnerPriceBreakdown = canEdit && !allEligibleGuestsRegistered;
  // One line item per non-empty veg/non-veg cell, priced at that category's
  // snapshot rate (dinnerReg.pricePerAdult/Kids914/Kids68 — captured at
  // booking time by celebrity_dinner_logic.upsert_dinner_registration, same
  // rate for veg and non-veg since diet never affects price). Kids under 6
  // have no price field at all — always free.
  const dinnerLineItems = dinnerReg
    ? [
        { key: "adultsVeg", label: "Adults — Veg", qty: dinnerReg.numberOfAdultsVeg || 0, price: dinnerReg.pricePerAdult || 0 },
        { key: "adultsNonVeg", label: "Adults — Non-Veg", qty: dinnerReg.numberOfAdultsNonVeg || 0, price: dinnerReg.pricePerAdult || 0 },
        { key: "kids914Veg", label: "Kids (9-14) — Veg", qty: dinnerReg.numberOfKids914Veg || 0, price: dinnerReg.pricePerKids914 || 0 },
        { key: "kids914NonVeg", label: "Kids (9-14) — Non-Veg", qty: dinnerReg.numberOfKids914NonVeg || 0, price: dinnerReg.pricePerKids914 || 0 },
        { key: "kids68Veg", label: "Kids (6-8) — Veg", qty: dinnerReg.numberOfKids68Veg || 0, price: dinnerReg.pricePerKids68 || 0 },
        { key: "kids68NonVeg", label: "Kids (6-8) — Non-Veg", qty: dinnerReg.numberOfKids68NonVeg || 0, price: dinnerReg.pricePerKids68 || 0 },
        { key: "kidsUnder6Veg", label: "Kids (under 6) — Veg", qty: dinnerReg.numberOfKidsUnder6Veg || 0, price: 0 },
        { key: "kidsUnder6NonVeg", label: "Kids (under 6) — Non-Veg", qty: dinnerReg.numberOfKidsUnder6NonVeg || 0, price: 0 },
      ].filter((item) => item.qty > 0)
    : [];

  return (
    <section className="entry-passes event-block event-block-dinner" aria-label="Dinner with Celebrity">
      <div style={{ display: "flex" }}>
        <div className="section-label" style={{ marginBottom: "10px" }}>🍽️ Dinner with Celebrity</div>
      </div>
      {config.dinnerDate ? (
        <div className="regform-review-row">
          <span className="regform-review-label">Date</span>
          <span className="regform-review-value">{formatDinnerDate(config.dinnerDate)}</span>
        </div>
      ) : null}
      <div className="regform-review-row">
        <span className="regform-review-label">Venue</span>
        <span className="regform-review-value">{config.venue || "—"}</span>
      </div>
      {dinnerOpen && showDinnerPriceBreakdown ? (
        <>
          <div className="regform-review-row">
            <span className="regform-review-label">Price — Adult</span>
            <span className="regform-review-value">
              {config.currency} {config.dinnerPriceAdults}
            </span>
          </div>
          <div className="regform-review-row">
            <span className="regform-review-label">Price — Kid (9-14)</span>
            <span className="regform-review-value">
              {config.currency} {config.dinnerPriceKids914}
            </span>
          </div>
          <div className="regform-review-row">
            <span className="regform-review-label">Price — Kid (6-8)</span>
            <span className="regform-review-value">
              {config.currency} {config.dinnerPriceKids68}
            </span>
          </div>
          <p className="field-note">Kids under 6 are never priced — they're counted for headcount only, at no charge.</p>
          <p className="field-note" style={{ marginTop: "6px" }}>{config.conditions}</p>
        </>
      ) : null}

      {returnBanner ? (
        <p className={returnBanner.status === "paid" ? "success-banner" : "field-note regform-capacity-note"}>
          {returnBanner.status === "paid"
            ? "Payment received — your Dinner with Celebrity registration is confirmed."
            : "We're still confirming your payment with the provider. This usually takes a moment — refresh in a bit if the status below doesn't update."}
        </p>
      ) : null}

      {!dinnerOpen ? (
        <p className="regform-note" style={{ marginTop: "10px" }}>
          The Dinner with Celebrity is currently closed for changes. Your booking is shown below for reference —
          please write to <a href={`mailto:${siteConfig.contactEmail}`}>{siteConfig.contactEmail}</a> if you need help with it.
        </p>
      ) : windowStatus === "not_open" ? (
        <p className="regform-note" style={{ marginTop: "10px" }}>
          Registration for the Dinner with Celebrity has not opened yet.
        </p>
      ) : windowStatus === "closed" && !dinnerReg ? (
        <p className="regform-note" style={{ marginTop: "10px" }}>
          Registration for the Dinner with Celebrity has closed.
        </p>
      ) : remainingCapacity === 0 && !dinnerReg ? (
        <p className="regform-note" style={{ marginTop: "10px" }}>
          The Dinner with Celebrity is fully booked.
        </p>
      ) : totalEligibleGuests <= 0 && !dinnerReg ? (
        <p className="regform-note" style={{ marginTop: "10px" }}>
          This registration has no attendees eligible for the Dinner with Celebrity.
        </p>
      ) : null}

      {error ? <div className="error-banner" style={{ marginTop: "10px" }}>{error}</div> : null}
      {success ? <div className="success-banner" style={{ marginTop: "10px" }}>{success}</div> : null}

      {dinnerReg ? (
        <>
          <div className="regform-review-grid" style={{ marginTop: "10px" }}>
            <DetailRow label="Name" value={dinnerReg.name} />
            <DetailRow label="Email" value={dinnerReg.email} />
            <DetailRow
              label="Payment Method"
              value={
                DINNER_PAYMENT_METHODS.find((m) => m.key === (dinnerReg.paymentProvider || dinnerReg.paymentMethod))?.label ||
                dinnerReg.paymentMethod ||
                "—"
              }
            />
            <DetailRow
              label="Payment Status"
              value={<span className={`payment-badge ${dinnerReg.status}`}>{DINNER_STATUS_LABELS[dinnerReg.status] || dinnerReg.status}</span>}
            />
          </div>
          <div className="regform-price-table" style={{ marginTop: "14px" }}>
            <div className="regform-price-head">
              <div>Item</div>
              <div>Qty</div>
              <div>Price</div>
              <div>Total</div>
            </div>
            {dinnerLineItems.map((item) => (
              <div className="regform-price-row" key={item.key}>
                <div>{item.label}</div>
                <div>{item.qty}</div>
                <div>{yen(item.price)}</div>
                <div>{yen(item.price * item.qty)}</div>
              </div>
            ))}
            <div className="regform-price-foot">
              <div>Total</div>
              <div />
              <div />
              <div>{yen(dinnerReg.totalAmount)}</div>
            </div>
          </div>
        </>
      ) : null}
      {dinnerReg?.status === "pending" && (dinnerReg.paymentProvider || dinnerReg.paymentMethod) === "bank" ? (
        <p className="field-note">
          We'll mark your bank transfer as paid once we see it arrive — no further action needed here.
        </p>
      ) : null}
      {dinnerReg?.status === "refund_requested" ? (
        <p className="field-note">
          Your refund is being processed — no further action needed here. Card and PayPay refunds are requested
          automatically; a bank transfer refund is sent by hand and may take a little longer.
        </p>
      ) : null}

      {canEdit && (canRegister || dinnerReg) ? (
        <form style={{ marginTop: "14px" }} onSubmit={handleSave}>
          <div className="field" style={{ marginBottom: "14px" }}>
            <label>Guests</label>
            <table className="dinner-signup-table">
              <thead>
                <tr>
                  <th>Category</th>
                  <th>Veg</th>
                  <th>Non-Veg</th>
                  <th>Total</th>
                </tr>
              </thead>
              <tbody>
                {[
                  { key: "adults", label: "Adults" },
                  { key: "kids914", label: "Kids (9-14)" },
                  { key: "kids68", label: "Kids (6-8)" },
                  { key: "kidsUnder6", label: "Kids (under 6)" },
                ].map(({ key, label }) => {
                  const veg = Number(guestCounts[`${key}Veg`]) || 0;
                  const nonVeg = Number(guestCounts[`${key}NonVeg`]) || 0;
                  return (
                    <tr key={key}>
                      <th scope="row">
                        {label}
                        <span className="dinner-signup-table-sub">Max {eligibleGuestCounts[key]}</span>
                      </th>
                      <td>
                        <input
                          type="number"
                          min="0"
                          aria-label={`${label} — Veg`}
                          value={guestCounts[`${key}Veg`]}
                          onChange={(event) => setGuestCounts((prev) => ({ ...prev, [`${key}Veg`]: event.target.value }))}
                          disabled={saving || windowStatus === "closed"}
                        />
                      </td>
                      <td>
                        <input
                          type="number"
                          min="0"
                          aria-label={`${label} — Non-Veg`}
                          value={guestCounts[`${key}NonVeg`]}
                          onChange={(event) => setGuestCounts((prev) => ({ ...prev, [`${key}NonVeg`]: event.target.value }))}
                          disabled={saving || windowStatus === "closed"}
                        />
                      </td>
                      <td className="dinner-signup-table-total">{veg + nonVeg}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
          <div className="field-row">
            <div className="field">
              <label htmlFor={`dinnerPaymentMethod-${registrationId}`}>Payment Method</label>
              <select
                id={`dinnerPaymentMethod-${registrationId}`}
                value={paymentMethod}
                onChange={(event) => setPaymentMethod(event.target.value)}
                disabled={saving}
              >
                {availableMethods.map((m) => (
                  <option key={m.key} value={m.key}>{m.label}</option>
                ))}
              </select>
            </div>
          </div>
          <div className="btn-row" style={{ marginTop: "10px" }}>
            {windowStatus !== "closed" ? (
              <button type="submit" className="btn-primary" disabled={saving}>
                {saving ? "Saving…" : dinnerReg ? "Update" : "Register"}
              </button>
            ) : null}
            {dinnerReg && canCancelBooking ? (
              <button type="button" className="btn-danger" onClick={handleCancel} disabled={saving}>
                Cancel
              </button>
            ) : null}
          </div>
        </form>
      ) : dinnerReg ? (
        <div className="btn-row" style={{ marginTop: "10px" }}>
          {dinnerReg.status === "pending" && payableOnline ? (
            <button type="button" className="btn-primary" onClick={handlePayNow} disabled={saving}>
              Pay Now
            </button>
          ) : null}
          {(dinnerReg.status === "pending" || dinnerReg.status === "paid") && canCancelBooking ? (
            <button type="button" className="btn-danger" onClick={handleCancel} disabled={saving}>
              {dinnerReg.status === "paid" ? "Cancel & Request Refund" : "Cancel"}
            </button>
          ) : null}
        </div>
      ) : null}
    </section>
  );
}

function DetailRow({ label, value }) {
  return (
    <div className="regform-review-row">
      <span className="regform-review-label">{label}</span>
      <span className="regform-review-value">{value || "—"}</span>
    </div>
  );
}

function EntryPasses({ reg }) {
  const passes = Array.isArray(reg.allocations) ? reg.allocations : [];
  if (!passes.length) return null;
  const allocated = passes.reduce((sum, pass) => sum + (Number(pass.seatCount) || 0), 0);
  const checkedIn = passes.reduce((sum, pass) => sum + (Number(pass.checkedInCount) || 0), 0);
  const remaining = Math.max(allocated - checkedIn, 0);
  return (
    <section className="entry-passes" aria-label="Entry passes">
      <div className="section-label">Entry Passes</div>
      <div className="entry-pass-list">
        {passes.map((pass, idx) => {
          const allocated = Number(pass.seatCount) || 0;
          const checkedIn = Number(pass.checkedInCount) || 0;
          const remaining = Math.max(allocated - checkedIn, 0);
          const fullyUsed = allocated > 0 && remaining === 0;
          return (
            <div className="entry-pass" key={pass.allocationId || idx}>
              <div className="entry-pass-name">{pass.slotName || `Entry Slot ${idx + 1}`}</div>
              <div className="entry-pass-seats">{allocated} people</div>
              <div className={`entry-pass-status ${fullyUsed ? "used" : ""}`}>
                {fullyUsed ? "Fully checked in" : `Checked in: ${checkedIn} / ${allocated}`}
              </div>
            </div>
          );
        })}
      </div>
        <div className="entry-pass-registration">
          {reg.registrationPassToken ? <EntryPassQr token={reg.registrationPassToken} slotName="Registration" /> : null}
        </div>
    </section>
  );
}

function EntryPassQr({ token, slotName }) {
  const [visible, setVisible] = useState(false);
  const canvasRef = useRef(null);
  const passUrl = `${window.location.origin}/check-in?token=${encodeURIComponent(token)}`;

  useEffect(() => {
    if (!visible || !canvasRef.current) return;
    QRCode.toCanvas(canvasRef.current, passUrl, {
      errorCorrectionLevel: "M",
      margin: 2,
      width: 220,
      color: { dark: "#17251f", light: "#ffffff" },
    });
  }, [passUrl, visible]);

  return (
    <>
      <button
        type="button"
        className="entry-pass-qr-button"
        onClick={() => setVisible((current) => !current)}
        aria-expanded={visible}
      >
        {visible ? "Hide QR" : "Show QR"}
      </button>
      {visible ? (
        <div className="entry-pass-qr" role="group" aria-label={`${slotName || "Entry"} QR pass`}>
          <canvas ref={canvasRef} />
          <span>Show this pass at entry</span>
        </div>
      ) : null}
    </>
  );
}

function OnamDetails({ reg }) {
  const lineItems = Array.isArray(reg.lineItems) ? reg.lineItems : [];
  const membership = reg.membership || {};
  const paymentLabel =
    PAYMENT_METHODS.find((p) => p.key === normalizePaymentMethod(reg.paymentMethod))?.label ||
    reg.paymentMethod ||
    "—";

  return (
    <>
      <div className="regform-review-grid" style={{ marginTop: "12px" }}>
        <DetailRow label="Name" value={reg.name} />
        <DetailRow label="Email" value={reg.email} />
        <DetailRow label="Phone" value={reg.phone} />
        <DetailRow label="Payment Method" value={paymentLabel} />
        <DetailRow
          label="Payment Status"
          value={<PaymentStatusBadge status={reg.paymentStatus} />}
        />
      </div>

      <EntryPasses reg={reg} />

      {lineItems.length > 0 ? (
        <div className="regform-price-table" style={{ marginTop: "14px" }}>
          <div className="regform-price-head">
            <div>Item</div>
            <div>Qty</div>
            <div>Price</div>
            <div>Total</div>
          </div>
          {lineItems.map((item, idx) => (
            <div className="regform-price-row" key={item.category || idx}>
              <div>{item.label || item.category}</div>
              <div>{item.quantity}</div>
              <div>{yen(item.unitPrice)}</div>
              <div>{yen(item.total)}</div>
            </div>
          ))}
          {Number(reg.discountApplied) > 0 ? (
            <div className="regform-price-row discount">
              <div>NPO discount applied</div>
              <div />
              <div />
              <div>−{yen(reg.discountApplied)}</div>
            </div>
          ) : null}
          {Number(reg.npoFee) > 0 ? (
            <div className="regform-price-row">
              <div>NPO membership fee</div>
              <div>1</div>
              <div>{yen(reg.npoFee)}</div>
              <div>{yen(reg.npoFee)}</div>
            </div>
          ) : null}
          <div className="regform-price-foot">
            <div>Total</div>
            <div />
            <div />
            <div>{yen(reg.totalAmount)}</div>
          </div>
          <PaymentBalanceRows reg={reg} />
        </div>
      ) : null}

      {membership.action && membership.action !== "none" ? (
        <p className="field-note" style={{ marginTop: "10px" }}>
          NPO membership {membership.action === "renew" ? "renewed" : "joined"}
          {membership.year ? ` for ${String(membership.year).replace("-", "–")}` : ""}.
        </p>
      ) : null}
    </>
  );
}

function StandardDetails({ reg }) {
  const attendee_cnts = reg.attendeeCounts || {};
  return (
    <>
      <div className="regform-review-grid" style={{ marginTop: "12px" }}>
        <DetailRow label="Name" value={reg.name} />
        <DetailRow label="Email" value={reg.email} />
        <DetailRow label="Phone" value={reg.phone} />
        {STANDARD_COUNT_FIELDS.map((f) => (
          <DetailRow key={f.key} label={f.label} value={String(attendee_cnts[f.key] ?? 0)} />
        ))}
        <DetailRow label="Vegetarians" value={String(reg.vegetarianCount ?? 0)} />
        <DetailRow
          label="Payment Status"
          value={<PaymentStatusBadge status={reg.paymentStatus} />}
        />
      </div>
      <EntryPasses reg={reg} />
    </>
  );
}

function Counter({ label, sub, value, min, onChange }) {
  return (
    <div className="attendee-row">
      <div className="attendee-label">
        {label}
        {sub ? <small>{sub}</small> : null}
      </div>
      <div className="attendee-count">
        <button
          type="button"
          className="counter-btn"
          onClick={() => onChange(Math.max(min, value - 1))}
        >
          −
        </button>
        <span className="counter-val">{value}</span>
        <button type="button" className="counter-btn" onClick={() => onChange(value + 1)}>
          +
        </button>
      </div>
    </div>
  );
}

function OnamEditForm({ reg, busy, onCancel, onSave }) {
  const [option, setOption] = useState(reg.registrationOption || "combo");
  const [name, setName] = useState(reg.name || "");
  const [phone, setPhone] = useState(reg.phone || "");
  const [paymentMethod, setPaymentMethod] = useState(
    normalizePaymentMethod(reg.paymentMethod) || "credit"
  );
  const [attendee_cnts, setCounts] = useState(() => ({
    adults: Number(reg.attendeeCounts?.adults ?? 1),
    kids914: Number(reg.attendeeCounts?.kids914 ?? 0),
    kids68: Number(reg.attendeeCounts?.kids68 ?? 0),
    kidsUnder6: Number(reg.attendeeCounts?.kidsUnder6 ?? 0),
  }));

  // Additional adults (adults 2…N), seeded from the stored record. Each carries
  // its own email + NPO status; the list length tracks (adults − 1).
  const registrantEmail = String(reg.email || "").trim().toLowerCase();
  const [additionalAdults, setAdditionalAdults] = useState(() =>
    (Array.isArray(reg.additionalAdults) ? reg.additionalAdults : []).map((a) => ({
      email: a?.email || "",
      status: a?.isMember ? "member" : a?.email ? "none" : "",
    }))
  );
  const [adultErrors, setAdultErrors] = useState({});
  const [phoneError, setPhoneError] = useState("");

  const totalAttendees = useMemo(
    () => Object.values(attendee_cnts).reduce((sum, n) => sum + (Number(n) || 0), 0),
    [attendee_cnts]
  );

  // Live "estimated total" as the option/counts/additional-adult emails
  // change — mirrors the server's pricing rules. The registrant's own tier
  // (adult 1) is fixed at whatever was resolved on the last save (this form
  // can't change membership status); additional adults use the on-blur NPO
  // check's result. The server re-verifies everything and recomputes the
  // authoritative amount on save.
  const registrantTier = ONAM_DISCOUNT_PCT[reg.pricingTier] ? reg.pricingTier : "none";
  const estimatedOrder = useMemo(() => {
    const base = ONAM_BASE_PRICES[option] || ONAM_BASE_PRICES.combo;
    const adultCount = Math.max(0, Number(attendee_cnts.adults) || 0);
    const byPct = new Map();
    for (let i = 0; i < adultCount; i += 1) {
      const tier = i === 0 ? registrantTier : additionalAdults[i - 1]?.status === "member" ? "member" : "none";
      const pct = ONAM_DISCOUNT_PCT[tier]?.[option] || 0;
      byPct.set(pct, (byPct.get(pct) || 0) + 1);
    }
    let ticketsTotal = 0;
    let discount = 0;
    byPct.forEach((qty, pct) => {
      const unit = Math.round((base.adult * (100 - pct)) / 100);
      ticketsTotal += unit * qty;
      discount += (base.adult - unit) * qty;
    });
    ["kids914", "kids68", "kidsUnder6"].forEach((key) => {
      ticketsTotal += base[key] * (Number(attendee_cnts[key]) || 0);
    });
    const npoFee = Number(reg.npoFee) || 0;
    return { total: ticketsTotal + npoFee, discount, npoFee };
  }, [option, attendee_cnts, additionalAdults, registrantTier, reg.npoFee]);

  const setCount = (key, val) => setCounts((prev) => ({ ...prev, [key]: val }));

  // Keep the additional-adults list length in sync with (adults − 1).
  useEffect(() => {
    const needed = Math.max(0, (Number(attendee_cnts.adults) || 1) - 1);
    setAdditionalAdults((prev) => {
      if (prev.length === needed) return prev;
      if (prev.length < needed) {
        return [
          ...prev,
          ...Array.from({ length: needed - prev.length }, () => ({ email: "", status: "" })),
        ];
      }
      return prev.slice(0, needed);
    });
  }, [attendee_cnts.adults]);

  const onAdultEmailChange = (index, value) => {
    setAdditionalAdults((prev) =>
      prev.map((adult, i) => (i === index ? { ...adult, email: value, status: "" } : adult))
    );
    setAdultErrors((prev) => ({ ...prev, [index]: "" }));
  };

  const checkAdult = async (index) => {
    const target = additionalAdults[index];
    if (!target) return;
    const email = target.email.trim().toLowerCase();
    if (!email || !EMAIL_REGEX.test(email)) return;

    // A reused email can't earn a second discount.
    const isDuplicate =
      email === registrantEmail ||
      additionalAdults.some(
        (adult, i) => i !== index && adult.email.trim().toLowerCase() === email
      );
    if (isDuplicate) {
      setAdditionalAdults((prev) =>
        prev.map((adult, i) => (i === index ? { ...adult, status: "duplicate" } : adult))
      );
      return;
    }

    setAdditionalAdults((prev) =>
      prev.map((adult, i) => (i === index ? { ...adult, status: "checking" } : adult))
    );
    const isMember = await checkNpoMembership(email);
    setAdditionalAdults((prev) =>
      prev.map((adult, i) =>
        i === index && adult.email.trim().toLowerCase() === email
          ? { ...adult, status: isMember ? "member" : "none" }
          : adult
      )
    );
  };

  const validateAdults = () => {
    const errs = {};
    const seen = new Set(registrantEmail ? [registrantEmail] : []);
    additionalAdults.forEach((adult, index) => {
      const value = adult.email.trim().toLowerCase();
      if (!value) return; // optional — skip blank entries
      if (!EMAIL_REGEX.test(value)) {
        errs[index] = "Enter a valid email address";
      } else if (seen.has(value)) {
        errs[index] = "This email is already used by another attendee";
      } else {
        seen.add(value);
      }
    });
    setAdultErrors(errs);
    return Object.keys(errs).length === 0;
  };

  const submit = () => {
    const normalizedPhone = phone.trim();
    if (normalizedPhone && !PHONE_REGEX.test(normalizedPhone)) {
      setPhoneError("Use format: +country code and number (e.g. +81 90-1234-5678)");
      return;
    }
    if (!validateAdults()) return;
    setPhoneError("");
    // The server re-prices from these trusted inputs; the membership action is
    // preserved from the existing record (self-service edits don't change it).
    onSave({
      registrationOption: option,
      name: name.trim(),
      phone: normalizedPhone,
      paymentMethod,
      attendeeCounts: attendee_cnts,
      additionalAdultEmails: additionalAdults.map((a) => a.email.trim().toLowerCase()),
      membership: { action: reg.membership?.action || "none" },
    });
  };

  return (
    <>
      <div className="section-label" style={{ marginTop: "12px" }}>Registration Option</div>
      <div className="regform-toggle-group">
        {ONAM_OPTIONS.map((o) => (
          <button
            type="button"
            key={o.key}
            className={`regform-toggle-pill ${option === o.key ? "selected" : ""}`}
            onClick={() => setOption(o.key)}
          >
            {o.label}
          </button>
        ))}
      </div>

      <div className="field-row" style={{ marginTop: "14px" }}>
        <div className="field">
          <label>Full Name *</label>
          <input type="text" value={name} onChange={(e) => setName(e.target.value)} />
        </div>
        <div className="field">
          <label>Phone Number</label>
          <input
            type="tel"
            className={phoneError ? "input-invalid" : ""}
            value={phone}
            onChange={(e) => {
              setPhone(e.target.value);
              setPhoneError("");
            }}
            placeholder="+81 90-1234-5678"
          />
          {phoneError ? <span className="field-error">{phoneError}</span> : null}
        </div>
      </div>

      <div className="section-label" style={{ marginTop: "6px" }}>Who is attending?</div>
      <div className="attendee-grid">
        {ONAM_COUNT_FIELDS.map((f) => (
          <Counter
            key={f.key}
            label={f.label}
            sub={f.sub}
            min={f.min}
            value={attendee_cnts[f.key]}
            onChange={(val) => setCount(f.key, val)}
          />
        ))}
        <div className="total-row">
          <div className="total-label">Total Attendees</div>
          <div className="total-val">{totalAttendees}</div>
        </div>
      </div>

      <p className="field-note" style={{ margin: "4px 0 12px" }}>
        <strong>Estimated total: {yen(estimatedOrder.total)}</strong>
        {estimatedOrder.discount > 0 ? ` (includes ${yen(estimatedOrder.discount)} NPO savings)` : ""}
        {" — recalculated by the server on save."}
      </p>

      {additionalAdults.length > 0 ? (
        <>
          <div className="section-label" style={{ marginTop: "6px" }}>Additional Adults</div>
          <p className="field-note" style={{ marginBottom: "10px" }}>
            Optionally enter each additional adult's email. Active NPO members get the 10%
            member discount on their ticket automatically.
          </p>
          <div className="regform-adult-emails">
            {additionalAdults.map((adult, index) => (
              <div className="field" key={index}>
                <label>Adult {index + 2} email</label>
                <input
                  type="email"
                  placeholder="adult@email.com (optional)"
                  className={adultErrors[index] ? "input-invalid" : ""}
                  value={adult.email}
                  onChange={(e) => onAdultEmailChange(index, e.target.value)}
                  onBlur={() => checkAdult(index)}
                />
                {adultErrors[index] ? (
                  <span className="field-error">{adultErrors[index]}</span>
                ) : adult.status === "checking" ? (
                  <span className="field-note">Checking membership…</span>
                ) : adult.status === "duplicate" ? (
                  <span className="field-error">
                    This email is already used by another attendee.
                  </span>
                ) : adult.status === "member" ? (
                  <span className="field-note regform-adult-member">
                    ✓ NPO member — 10% discount applies
                  </span>
                ) : adult.status === "none" ? (
                  <span className="field-note">Not an NPO member — standard price.</span>
                ) : null}
              </div>
            ))}
          </div>
        </>
      ) : null}

      <div className="section-label" style={{ marginTop: "6px" }}>Payment Method</div>
      <div className="regform-toggle-group">
        {PAYMENT_METHODS.map((p) => (
          <button
            type="button"
            key={p.key}
            className={`regform-toggle-pill ${paymentMethod === p.key ? "selected" : ""}`}
            onClick={() => setPaymentMethod(p.key)}
          >
            {p.label}
          </button>
        ))}
      </div>

      <p className="field-note" style={{ marginTop: "10px" }}>
        The updated total is recalculated automatically and shown once you save.
      </p>

      <div className="btn-row" style={{ marginTop: "14px" }}>
        <button type="button" className="btn-secondary" onClick={onCancel} disabled={busy}>
          Cancel
        </button>
        <button
          type="button"
          className="btn-primary"
          onClick={submit}
          disabled={busy || !name.trim() || totalAttendees < 1}
        >
          {busy ? "Saving…" : "Save Changes"}
        </button>
      </div>
    </>
  );
}

function StandardEditForm({ reg, busy, onCancel, onSave }) {
  const [name, setName] = useState(reg.name || "");
  const [phone, setPhone] = useState(reg.phone || "");
  const [phoneError, setPhoneError] = useState("");
  const [attendee_cnts, setCounts] = useState(() => {
    const src = reg.attendeeCounts || {};
    return {
      adult: Number(src.adults ?? 0),
      kids914: Number(src.kids914 ?? 0),
      kids68: Number(src.kids68 ?? 0),
      kidsUnder6: Number(src.kidsUnder6 ?? 0),
    };
  });
  const [vegetarianCount, setVegetarianCount] = useState(Number(reg.vegetarianCount ?? 0));

  const setCount = (key, val) => setCounts((prev) => ({ ...prev, [key]: val }));

  const submit = () => {
    const normalizedPhone = phone.trim();
    if (normalizedPhone && !PHONE_REGEX.test(normalizedPhone)) {
      setPhoneError("Use format: +country code and number (e.g. +81 90-1234-5678)");
      return;
    }
    setPhoneError("");
    onSave({
      name: name.trim(),
      phone: normalizedPhone,
      attendeeCounts: attendee_cnts,
      vegetarianCount,
    });
  };

  return (
    <>
      <div className="field-row" style={{ marginTop: "12px" }}>
        <div className="field">
          <label>Full Name *</label>
          <input type="text" value={name} onChange={(e) => setName(e.target.value)} />
        </div>
        <div className="field">
          <label>Phone Number</label>
          <input
            type="tel"
            className={phoneError ? "input-invalid" : ""}
            value={phone}
            onChange={(e) => {
              setPhone(e.target.value);
              setPhoneError("");
            }}
            placeholder="+81 90-1234-5678"
          />
          {phoneError ? <span className="field-error">{phoneError}</span> : null}
        </div>
      </div>

      <div className="section-label" style={{ marginTop: "6px" }}>Who is attending?</div>
      <div className="attendee-grid">
        {STANDARD_COUNT_FIELDS.map((f) => (
          <Counter
            key={f.key}
            label={f.label}
            min={0}
            value={attendee_cnts[f.key]}
            onChange={(val) => setCount(f.key, val)}
          />
        ))}
        <div className="attendee-row">
          <div className="attendee-label">Vegetarians</div>
          <div className="attendee-count">
            <button
              type="button"
              className="counter-btn"
              onClick={() => setVegetarianCount(Math.max(0, vegetarianCount - 1))}
            >
              −
            </button>
            <span className="counter-val">{vegetarianCount}</span>
            <button
              type="button"
              className="counter-btn"
              onClick={() => setVegetarianCount(vegetarianCount + 1)}
            >
              +
            </button>
          </div>
        </div>
      </div>

      <div className="btn-row" style={{ marginTop: "14px" }}>
        <button type="button" className="btn-secondary" onClick={onCancel} disabled={busy}>
          Cancel
        </button>
        <button
          type="button"
          className="btn-primary"
          onClick={submit}
          disabled={busy || !name.trim()}
        >
          {busy ? "Saving…" : "Save Changes"}
        </button>
      </div>
    </>
  );
}
