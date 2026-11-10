import { useEffect, useState } from "react";
import Navbar from "@app/Navbar";
import { getRegistrantEmail } from "@app/registrantAuth";
import {
  cancelSeatUpgrade,
  createSeatUpgradeCheckout,
  getMySeatUpgrades,
  getSeatUpgradeStatus,
  requestSeatUpgrade,
} from "./api";

const HOME_PATH = "/";
const MY_REGISTRATIONS_PATH = "/my/registrations";
const MY_SEAT_UPGRADE_PATH = "/my/seat-upgrade";

const yen = (value) => `¥${Number(value || 0).toLocaleString("en-US")}`;

function formatDateOnly(value) {
  if (!value) return "";
  const date = new Date(`${value}T00:00:00Z`);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleDateString(undefined, { month: "long", day: "numeric", year: "numeric", timeZone: "UTC" });
}

// Zones are ranked by admin display order, so a medal reads naturally as
// "the best seating" without needing an icon field on the tier itself.
const ZONE_ICONS = ["🥇", "🥈", "🥉"];

// "online: true" methods redirect to a hosted checkout immediately after the
// request is created (see handleRequest); bank transfer stays the existing
// show-account-details-and-wait-for-an-admin flow — see
// plugins/seat-upgrade/docs/PAYMENTS.md for why.
const PAYMENT_METHODS = [
  { key: "paypay", label: "PayPay", online: true },
  { key: "square", label: "Credit Card", online: true },
  { key: "bank", label: "Bank Transfer", online: false },
];

const STATUS_LABELS = {
  pending: "Payment Pending",
  paid: "Paid",
  refund_requested: "Refund Requested",
  cancelled: "Cancelled",
};

// Mirrors seat_upgrade_logic.price_seat_picks server-side — a client-side
// estimate only, the actual charge is always computed and enforced by the
// backend. Sorts the seats being bought most-expensive-first so the
// family's free-standing "first N full price" seats (counted across every
// prior active purchase, not just this one) land on the pricier zones and
// the discount lands on the cheaper ones.
function estimateSeatTotal(tiers, picks, existingUpgradedCount, familyDiscountPercent, fullPriceSeatCount) {
  const units = [];
  Object.entries(picks).forEach(([tierId, count]) => {
    const tier = tiers.find((t) => t.tierId === tierId);
    const n = Number(count) || 0;
    if (!tier || n <= 0) return;
    for (let i = 0; i < n; i++) units.push(Number(tier.upgradePrice) || 0);
  });
  units.sort((a, b) => b - a);
  const remainingFull = Math.max(fullPriceSeatCount - existingUpgradedCount, 0);
  return units.reduce((sum, price, i) => {
    const charged = i < remainingFull ? price : Math.round((price * (100 - familyDiscountPercent)) / 100);
    return sum + charged;
  }, 0);
}

// Copied from the host app's App.jsx/PartnersPage.jsx — same "copied
// component" convention this plugin already uses elsewhere (see this
// file's own STATUS_LABELS/PAYMENT_METHODS), so every page ends with the
// same small business-development footer.
function EventFooter() {
  return (
    <footer className="event-footer">
      Host Your Event With Us
      <br />
      For bookings and inquiries, contact <a href="mailto:hello@cloudnadi.com">hello@cloudnadi.com</a>.
    </footer>
  );
}

export default function SeatUpgrade({ navigateTo, onLogout }) {
  const [registrations, setRegistrations] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [returnBanner, setReturnBanner] = useState(null); // {status, tierName} | null
  const email = getRegistrantEmail();

  async function load() {
    setLoading(true);
    setError("");
    try {
      setRegistrations(await getMySeatUpgrades());
    } catch (err) {
      setError(err.message || "Failed to load zone upgrade options.");
    } finally {
      setLoading(false);
    }
  }

  // A registrant lands back here with ?upgradeId=&provider= after a PayPay
  // or Square hosted checkout — see seat_upgrade/handler.py's redirect_url.
  // Reconcile that one request against the provider immediately (rather
  // than waiting on the webhook alone) before showing the page.
  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    const returningUpgradeId = params.get("upgradeId");
    if (!returningUpgradeId) {
      load();
      return;
    }
    window.history.replaceState({}, "", MY_SEAT_UPGRADE_PATH);
    (async () => {
      try {
        const upgrade = await getSeatUpgradeStatus(returningUpgradeId);
        setReturnBanner({ status: upgrade?.status || "pending", tierName: upgrade?.tierName || "" });
      } catch {
        setReturnBanner({ status: "pending", tierName: "" });
      } finally {
        load();
      }
    })();
  }, []);

  function updateRegistration(registrationId, patch) {
    setRegistrations((prev) =>
      prev.map((r) => (r.registrationId === registrationId ? { ...r, ...patch(r) } : r))
    );
  }

  async function startCheckout(upgrade, provider) {
    try {
      const { checkoutUrl } = await createSeatUpgradeCheckout(upgrade.upgradeId, provider);
      window.location.href = checkoutUrl;
    } catch (err) {
      throw new Error(err.message || `Could not start ${provider === "paypay" ? "PayPay" : "Square"} checkout.`);
    }
  }

  async function handleRequest(reg, picks, paymentMethod, dinnerGuestCounts) {
    updateRegistration(reg.registrationId, () => ({ error: "", busy: true }));
    try {
      const upgrade = await requestSeatUpgrade({
        registrationId: reg.registrationId,
        picks,
        paymentMethod,
        dinnerAdultVegGuests: dinnerGuestCounts.adultsVeg,
        dinnerAdultNonVegGuests: dinnerGuestCounts.adultsNonVeg,
        dinnerKids914VegGuests: dinnerGuestCounts.kids914Veg,
        dinnerKids914NonVegGuests: dinnerGuestCounts.kids914NonVeg,
        dinnerKids68VegGuests: dinnerGuestCounts.kids68Veg,
        dinnerKids68NonVegGuests: dinnerGuestCounts.kids68NonVeg,
        dinnerKidsUnder6VegGuests: dinnerGuestCounts.kidsUnder6Veg,
        dinnerKidsUnder6NonVegGuests: dinnerGuestCounts.kidsUnder6NonVeg,
      });
      updateRegistration(reg.registrationId, (r) => ({
        upgrades: [upgrade, ...(r.upgrades || [])],
        seatsAlreadyUpgraded: (r.seatsAlreadyUpgraded || 0) + (upgrade.seatCount || 0),
        busy: false,
      }));
      // PayPay/Square: go straight to hosted checkout — no separate "Pay
      // Now" click needed for the common case. Bank transfer just leaves
      // the request pending with its account details shown, as before.
      const method = PAYMENT_METHODS.find((m) => m.key === paymentMethod);
      if (method?.online) {
        await startCheckout(upgrade, paymentMethod);
      }
    } catch (err) {
      updateRegistration(reg.registrationId, () => ({
        error: err.message || "Could not request zone upgrade.",
        busy: false,
      }));
    }
  }

  async function handlePayNow(reg, upgrade) {
    updateRegistration(reg.registrationId, () => ({ error: "", busy: true }));
    try {
      await startCheckout(upgrade, upgrade.paymentProvider || upgrade.paymentMethod);
    } catch (err) {
      updateRegistration(reg.registrationId, () => ({ error: err.message, busy: false }));
    }
  }

  async function handleCancel(reg, upgradeId) {
    updateRegistration(reg.registrationId, () => ({ error: "", busy: true }));
    try {
      const updated = await cancelSeatUpgrade(upgradeId);
      updateRegistration(reg.registrationId, (r) => {
        const prior = (r.upgrades || []).find((u) => u.upgradeId === upgradeId);
        // Capacity is only actually freed once a cancellation is final — a
        // paid request that moved to "refund_requested" still holds its
        // seat until an admin confirms the refund (see seat_upgrade_logic's
        // cancel_upgrade_request docstring for why).
        const freedSeats = updated?.status === "cancelled" ? prior?.seatCount || 0 : 0;
        return {
          upgrades: (r.upgrades || []).map((u) => (u.upgradeId === upgradeId ? { ...u, ...updated } : u)),
          seatsAlreadyUpgraded: Math.max((r.seatsAlreadyUpgraded || 0) - freedSeats, 0),
          busy: false,
        };
      });
    } catch (err) {
      updateRegistration(reg.registrationId, () => ({
        error: err.message || "Could not cancel zone upgrade.",
        busy: false,
      }));
    }
  }

  const nav = (
    <Navbar
      navigateTo={navigateTo}
    />
  );

  return (
    <>
      {nav}
      <main className="page-wrap onam-reg">
        <header className="event-header">
          <div className="banner-title">Upgrade My Zone</div>
          <div className="banner-sub-title">
            {email ? `Signed in as ${email}` : "Upgrade your Celebrity Event seating"}
          </div>
          <div className="sky-bar" />
        </header>

        {returnBanner ? (
          <p className={returnBanner.status === "paid" ? "success-banner" : "field-note onam-capacity-note"}>
            {returnBanner.status === "paid"
              ? `Payment received — your ${returnBanner.tierName || "seat"} upgrade is confirmed.`
              : "We're still confirming your payment with the provider. This usually takes a moment — refresh in a bit if the status below doesn't update."}
          </p>
        ) : null}

        {error ? <p className="error-banner">{error}</p> : null}

        {loading ? (
          <section className="form-card">
            <div className="form-body">
              <p className="field-note">Loading zone upgrade options…</p>
            </div>
          </section>
        ) : registrations.length === 0 ? (
          <section className="form-card">
            <div className="form-body" style={{ textAlign: "center" }}>
              <p className="sub">
                No paid Celebrity Event registrations are eligible for a zone upgrade right now.
              </p>
              <div className="btn-row" style={{ justifyContent: "center" }}>
                <button type="button" className="btn-primary" onClick={() => navigateTo(MY_REGISTRATIONS_PATH)}>
                  View My Registrations
                </button>
              </div>
            </div>
          </section>
        ) : (
          registrations.map((reg) => (
            <RegistrationUpgradeCard
              key={reg.registrationId}
              reg={reg}
              onRequest={(picks, paymentMethod, dinnerGuestCounts) =>
                handleRequest(reg, picks, paymentMethod, dinnerGuestCounts)
              }
              onCancel={(upgradeId) => handleCancel(reg, upgradeId)}
              onPayNow={(upgrade) => handlePayNow(reg, upgrade)}
            />
          ))
        )}
        <EventFooter />
      </main>
    </>
  );
}

function RegistrationUpgradeCard({ reg, onRequest, onCancel, onPayNow }) {
  const tiers = reg.tiers || [];
  const seatsInParty = reg.seatsInParty || 0;
  const seatsAlreadyUpgraded = reg.seatsAlreadyUpgraded || 0;
  const remainingSeats = Math.max(seatsInParty - seatsAlreadyUpgraded, 0);
  const familyDiscountPercent = reg.familyDiscountPercent || 0;
  const fullPriceSeatCount = reg.fullPriceSeatCount || 0;
  // The admin restricts which methods this event accepts (Events tab, per
  // plugins/seat-upgrade/docs/SEAT_UPGRADE_FEATURE.md); default to all three
  // if the API didn't send a restriction (shouldn't happen once a plugin is
  // enabled, but keeps this resilient rather than empty).
  const availableMethods = PAYMENT_METHODS.filter((m) =>
    (reg.enabledPaymentMethods || ["paypay", "square", "bank"]).includes(m.key)
  );

  // { [tierId]: "count" } — how many seats to upgrade into each zone. A
  // purchase can span several zones at once (the Family Upgrade Discount is
  // "valid in all zones") — there's no single "selected tier" anymore.
  const [picks, setPicks] = useState({});
  const [paymentMethod, setPaymentMethod] = useState(availableMethods[0]?.key || PAYMENT_METHODS[0].key);
  const [dinnerGuestCounts, setDinnerGuestCounts] = useState({
    adultsVeg: "0",
    adultsNonVeg: "0",
    kids914Veg: "0",
    kids914NonVeg: "0",
    kids68Veg: "0",
    kids68NonVeg: "0",
    kidsUnder6Veg: "0",
    kidsUnder6NonVeg: "0",
  });
  const activeUpgrades = (reg.upgrades || []).filter((u) => u.status !== "cancelled");

  const totalPicked = Object.values(picks).reduce((sum, c) => sum + (Number(c) || 0), 0);

  function setPickCount(tierId, rawValue) {
    const tier = tiers.find((t) => t.tierId === tierId);
    if (!tier) return;
    const current = Number(picks[tierId]) || 0;
    const budgetForThisZone = Math.min(tier.remainingCapacity, remainingSeats - totalPicked + current);
    const clamped = Math.max(0, Math.min(Number(rawValue) || 0, Math.max(budgetForThisZone, 0)));
    setPicks((prev) => ({ ...prev, [tierId]: String(clamped) }));
  }

  // Clamps veg/non-veg entry so the category's combined total can never
  // exceed dinnerRemaining[key] — mirrors setPickCount's zone-capacity
  // clamping above. Without this, a value typed past the limit left the
  // form silently refusing to submit (handleSubmit's dinnerRemaining guard
  // below) with no feedback at all.
  function setDinnerGuestCount(key, field, rawValue) {
    const otherField = field === "Veg" ? "NonVeg" : "Veg";
    const other = Number(dinnerGuestCounts[`${key}${otherField}`]) || 0;
    const budget = Math.max((dinnerRemaining[key] || 0) - other, 0);
    const clamped = Math.max(0, Math.min(Number(rawValue) || 0, budget));
    setDinnerGuestCounts((prev) => ({ ...prev, [`${key}${field}`]: String(clamped) }));
  }

  const effectiveDinnerGuestCounts = Object.fromEntries(
    Object.keys(dinnerGuestCounts).map((key) => [key, Math.max(Number(dinnerGuestCounts[key]) || 0, 0)])
  );
  const dinnerCategoryTotals = {
    adults: effectiveDinnerGuestCounts.adultsVeg + effectiveDinnerGuestCounts.adultsNonVeg,
    kids914: effectiveDinnerGuestCounts.kids914Veg + effectiveDinnerGuestCounts.kids914NonVeg,
    kids68: effectiveDinnerGuestCounts.kids68Veg + effectiveDinnerGuestCounts.kids68NonVeg,
    kidsUnder6: effectiveDinnerGuestCounts.kidsUnder6Veg + effectiveDinnerGuestCounts.kidsUnder6NonVeg,
  };
  const dinnerRemaining = {
    adults: reg.dinnerAdultRemaining || 0,
    kids914: reg.dinnerKids914Remaining || 0,
    kids68: reg.dinnerKids68Remaining || 0,
    kidsUnder6: reg.dinnerKidsUnder6Remaining || 0,
  };
  // No dinnerPriceKidsUnder6 — that category is never priced, only counted.
  const dinnerPrices = {
    adults: reg.dinnerPriceAdults || 0,
    kids914: reg.dinnerPriceKids914 || 0,
    kids68: reg.dinnerPriceKids68 || 0,
  };
  const dinnerTotal = Object.keys(dinnerPrices).reduce(
    (sum, category) => sum + dinnerCategoryTotals[category] * dinnerPrices[category],
    0
  );
  const anyDinnerRemaining = Object.values(dinnerRemaining).some((v) => v > 0);
  const dinnerOverLimit = Object.keys(dinnerRemaining).some(
    (category) => dinnerCategoryTotals[category] > dinnerRemaining[category]
  );
  const seatTotal = estimateSeatTotal(tiers, picks, seatsAlreadyUpgraded, familyDiscountPercent, fullPriceSeatCount);
  const estimatedTotal = seatTotal + dinnerTotal;

  const handleSubmit = (event) => {
    event.preventDefault();
    const pickList = Object.entries(picks)
      .map(([tierId, count]) => ({ tierId, count: Number(count) || 0 }))
      .filter((p) => p.count > 0);
    if (pickList.length === 0 || dinnerOverLimit) return;
    onRequest(pickList, paymentMethod, effectiveDinnerGuestCounts);
    setPicks({});
    setDinnerGuestCounts({
      adultsVeg: "0",
      adultsNonVeg: "0",
      kids914Veg: "0",
      kids914NonVeg: "0",
      kids68Veg: "0",
      kids68NonVeg: "0",
      kidsUnder6Veg: "0",
      kidsUnder6NonVeg: "0",
    });
  };

  return (
    <section className="form-card" style={{ marginBottom: "18px" }}>
      <div className="form-body">
        <div className="onam-summary-meta" style={{ justifyContent: "space-between", alignItems: "center" }}>
          <div>
            <div className="section-label" style={{ marginBottom: "4px" }}>{reg.eventName || reg.eventType}</div>
            <div className="onam-summary-sub">{reg.registrantName}</div>
          </div>
          <span className="onam-chip">{seatsInParty} seat{seatsInParty === 1 ? "" : "s"} in party</span>
        </div>

        {reg.error ? <p className="error-banner">{reg.error}</p> : null}

        {activeUpgrades.length > 0 ? (
          <div style={{ marginTop: "14px" }}>
            <div className="section-label" style={{ marginBottom: "8px" }}>Your Requests</div>
            {activeUpgrades.map((u) => {
              const payableOnline = ["paypay", "square"].includes(u.paymentProvider || u.paymentMethod);
              const dinnerTotalGuests =
                (u.dinnerAdultVegGuests || 0) +
                (u.dinnerAdultNonVegGuests || 0) +
                (u.dinnerKids914VegGuests || 0) +
                (u.dinnerKids914NonVegGuests || 0) +
                (u.dinnerKids68VegGuests || 0) +
                (u.dinnerKids68NonVegGuests || 0) +
                (u.dinnerKidsUnder6VegGuests || 0) +
                (u.dinnerKidsUnder6NonVegGuests || 0);
              return (
                <div key={u.upgradeId} className="onam-price-row">
                  <div>
                    {u.tierName} ({u.seatCount} seat{u.seatCount === 1 ? "" : "s"})
                    {dinnerTotalGuests > 0 ? ` + Celebrity Dinner (${dinnerTotalGuests} guest${dinnerTotalGuests === 1 ? "" : "s"})` : ""}
                  </div>
                  <div />
                  <div>
                    <span className={`payment-badge ${u.status}`}>{STATUS_LABELS[u.status] || u.status}</span>
                  </div>
                  <div>
                    {yen(u.totalAmount)}
                    {u.status === "pending" && payableOnline ? (
                      <button
                        type="button"
                        className="btn-primary"
                        style={{ marginLeft: "10px" }}
                        disabled={reg.busy}
                        onClick={() => onPayNow(u)}
                      >
                        Pay Now
                      </button>
                    ) : null}
                    {u.status === "pending" ? (
                      <button
                        type="button"
                        className="btn-secondary"
                        style={{ marginLeft: "10px" }}
                        disabled={reg.busy}
                        onClick={() => onCancel(u.upgradeId)}
                      >
                        {u.status === "paid" ? "Cancel & Request Refund" : "Cancel"}
                      </button>
                    ) : null}
                  </div>
                </div>
              );
            })}
            {activeUpgrades.some((u) => u.status === "pending" && (u.paymentProvider || u.paymentMethod) === "bank") ? (
              <p className="field-note">
                We'll mark your bank transfer as paid once we see it arrive — no further action needed here.
              </p>
            ) : null}
            {activeUpgrades.some((u) => u.status === "refund_requested") ? (
              <p className="field-note">
                Your refund is being processed — no further action needed here. Card and PayPay refunds are
                requested automatically; a bank transfer refund is sent by hand and may take a little longer.
              </p>
            ) : null}
          </div>
        ) : null}

        {tiers.length === 0 ? (
          <p className="field-note" style={{ marginTop: "14px" }}>
            {reg.upgradeWindow?.status === "not_started"
              ? `Premium seating upgrades open ${formatDateOnly(reg.upgradeWindow.startDate)}. Check back then!`
              : reg.upgradeWindow?.status === "ended"
                ? `The zone upgrade window for this event closed ${formatDateOnly(reg.upgradeWindow.endDate)}.`
                : "No premium seating zones are available for this event at the moment."}
          </p>
        ) : remainingSeats <= 0 ? (
          <p className="field-note" style={{ marginTop: "14px" }}>
            All seats on this registration already have an upgrade request.
          </p>
        ) : (
          <>
            <div style={{ marginTop: "18px" }}>
              <div className="section-label" style={{ marginBottom: "8px" }}>Premium Seating Zone Upgrade</div>
              {fullPriceSeatCount > 0 ? (
                <p className="field-note" style={{ marginTop: 0, marginBottom: "10px" }}>
                  Buy {fullPriceSeatCount} zone upgrade{fullPriceSeatCount === 1 ? "" : "s"} at regular price and get{" "}
                  {familyDiscountPercent}% off any more — valid across every zone below.
                  {seatsAlreadyUpgraded > 0
                    ? ` You already have ${seatsAlreadyUpgraded} upgraded seat${seatsAlreadyUpgraded === 1 ? "" : "s"}, so ${
                        Math.max(fullPriceSeatCount - seatsAlreadyUpgraded, 0) > 0
                          ? `${Math.max(fullPriceSeatCount - seatsAlreadyUpgraded, 0)} more at regular price`
                          : "the rest"
                      } get the discount.`
                    : ""}
                </p>
              ) : null}
              <p className="field-note" style={{ marginTop: 0, marginBottom: "10px" }}>
                Support WMF Japan by transparently sharing the programme expenses and working towards a sustainable model.
              </p>
              {reg.upgradeWindow?.endDate ? (
                <p className="field-note" style={{ marginTop: 0, marginBottom: "10px" }}>
                  Offer available through {formatDateOnly(reg.upgradeWindow.endDate)}.
                </p>
              ) : null}
              {reg.seatMapImageUrl ? (
                <div className="form-card" style={{ marginBottom: "14px", padding: "10px" }}>
                  <div style={{ position: "relative", lineHeight: 0 }}>
                    <img
                      src={reg.seatMapImageUrl}
                      alt="Venue seat map"
                      style={{ maxWidth: "100%", borderRadius: "4px", display: "block" }}
                    />
                    {tiers
                      .filter((tier) => tier.mapRegion)
                      .map((tier) => {
                        const soldOut = tier.remainingCapacity <= 0;
                        const selected = (Number(picks[tier.tierId]) || 0) > 0;
                        return (
                          <button
                            key={tier.tierId}
                            type="button"
                            title={`${tier.name}${soldOut ? " (Full)" : ""}`}
                            aria-label={`Add a seat in ${tier.name}${soldOut ? ", full" : ""}`}
                            onClick={() => !soldOut && setPickCount(tier.tierId, (Number(picks[tier.tierId]) || 0) + 1)}
                            disabled={soldOut || remainingSeats - totalPicked <= 0}
                            style={{
                              position: "absolute",
                              left: `${tier.mapRegion.x * 100}%`,
                              top: `${tier.mapRegion.y * 100}%`,
                              width: `${tier.mapRegion.width * 100}%`,
                              height: `${tier.mapRegion.height * 100}%`,
                              border: selected ? "2px solid #3d7258" : "2px solid rgba(61,114,88,0.55)",
                              background: selected ? "rgba(61,114,88,0.32)" : "rgba(61,114,88,0.14)",
                              borderRadius: "4px",
                              cursor: soldOut ? "not-allowed" : "pointer",
                              padding: 0,
                            }}
                          />
                        );
                      })}
                  </div>
                  {tiers.some((tier) => tier.mapRegion) ? (
                    <p className="field-note" style={{ margin: "8px 0 0" }}>
                      Tap a highlighted area on the map to add a seat there, or use the quantity fields below.
                    </p>
                  ) : null}
                </div>
              ) : null}
              <form onSubmit={handleSubmit}>
                <div className="onam-option-grid">
                  {tiers.map((tier, index) => {
                    const soldOut = tier.remainingCapacity <= 0;
                    const count = Number(picks[tier.tierId]) || 0;
                    const budgetForThisZone = Math.max(
                      0,
                      Math.min(tier.remainingCapacity, remainingSeats - totalPicked + count)
                    );
                    return (
                      <div
                        className={`onam-option-card ${count > 0 ? "selected" : ""} ${soldOut ? "disabled" : ""}`}
                        key={tier.tierId}
                      >
                        {soldOut ? <span className="onam-option-badge onam-option-badge-full">Full</span> : null}
                        <span className="onam-option-icon" aria-hidden="true">{ZONE_ICONS[index] || "🎟️"}</span>
                        <span className="onam-option-title">{tier.name}</span>
                        <span className="onam-option-tagline">
                          {tier.description || "Premium reserved seating"}
                        </span>
                        <span className="onam-option-price-row">
                          <span>{yen(tier.upgradePrice)} <small>/ seat</small></span>
                        </span>
                        <div className="field" style={{ marginTop: "10px" }}>
                          <label htmlFor={`zoneCount-${tier.tierId}`} style={{ fontSize: "12px" }}>
                            Seats to this zone
                          </label>
                          <input
                            id={`zoneCount-${tier.tierId}`}
                            type="number"
                            min="0"
                            max={count + budgetForThisZone}
                            value={picks[tier.tierId] || "0"}
                            onChange={(e) => setPickCount(tier.tierId, e.target.value)}
                            disabled={soldOut || reg.busy}
                          />
                        </div>
                      </div>
                    );
                  })}
                </div>

                {reg.dinnerEnabled && reg.hasStandaloneCelebrityDinner ? (
                  <p className="field-note" style={{ marginTop: "16px" }}>
                    This registration already has a standalone Celebrity Dinner sign-up — manage it from My
                    Registrations instead of bundling it here.
                  </p>
                ) : reg.dinnerEnabled && anyDinnerRemaining ? (
                  <div className="field-row full" style={{ marginTop: "16px" }}>
                    <div className="field">
                      <div className="section-label" style={{ marginBottom: "6px" }}>
                        Celebrity Dinner
                      </div>
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
                          ]
                            .filter(({ key }) => dinnerRemaining[key] > 0)
                            .map(({ key, label }) => {
                              const veg = Number(dinnerGuestCounts[`${key}Veg`]) || 0;
                              const nonVeg = Number(dinnerGuestCounts[`${key}NonVeg`]) || 0;
                              return (
                                <tr key={key}>
                                  <th scope="row">
                                    {label}
                                    <span className="dinner-signup-table-sub">
                                      {dinnerPrices[key] !== undefined ? `${yen(dinnerPrices[key])} each` : "free"} · up to{" "}
                                      {dinnerRemaining[key]} left
                                    </span>
                                  </th>
                                  <td>
                                    <input
                                      type="number"
                                      min="0"
                                      max={dinnerRemaining[key]}
                                      aria-label={`${label} — Veg`}
                                      value={dinnerGuestCounts[`${key}Veg`]}
                                      onChange={(e) => setDinnerGuestCount(key, "Veg", e.target.value)}
                                      disabled={reg.busy}
                                    />
                                  </td>
                                  <td>
                                    <input
                                      type="number"
                                      min="0"
                                      max={dinnerRemaining[key]}
                                      aria-label={`${label} — Non-Veg`}
                                      value={dinnerGuestCounts[`${key}NonVeg`]}
                                      onChange={(e) => setDinnerGuestCount(key, "NonVeg", e.target.value)}
                                      disabled={reg.busy}
                                    />
                                  </td>
                                  <td className="dinner-signup-table-total">{veg + nonVeg}</td>
                                </tr>
                              );
                            })}
                        </tbody>
                      </table>
                    </div>
                  </div>
                ) : reg.dinnerEnabled ? (
                  <p className="field-note" style={{ marginTop: "16px" }}>
                    All eligible guests on this registration are already booked for the Celebrity Dinner on
                    another request.
                  </p>
                ) : null}

                <div className="field-row full" style={{ marginTop: "16px" }}>
                  <div className="field">
                    <label>Payment Method</label>
                    <select value={paymentMethod} onChange={(e) => setPaymentMethod(e.target.value)} disabled={reg.busy}>
                      {availableMethods.map((m) => (
                        <option key={m.key} value={m.key}>{m.label}</option>
                      ))}
                    </select>
                  </div>
                </div>

                <div className="onam-price-row" style={{ marginTop: "6px" }}>
                  <div>Estimated total</div>
                  <div />
                  <div />
                  <div><strong>{yen(estimatedTotal)}</strong></div>
                </div>

                <div className="btn-row" style={{ marginTop: "12px" }}>
                  <button type="submit" className="btn-primary" disabled={reg.busy || totalPicked === 0 || dinnerOverLimit}>
                    {reg.busy ? "Requesting…" : "Request Upgrade"}
                  </button>
                </div>
              </form>
            </div>
          </>
        )}
      </div>
    </section>
  );
}
