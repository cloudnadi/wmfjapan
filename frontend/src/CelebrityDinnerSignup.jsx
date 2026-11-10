import { useEffect, useState } from "react";
import Navbar from "./Navbar";
import {
  getMyGuestCelebrityDinner,
  saveMyGuestCelebrityDinner,
  cancelMyGuestCelebrityDinner,
  checkoutMyGuestCelebrityDinner,
} from "./api";
import { getRegistrantEmail } from "./registrantAuth";

const HOME_PATH = "/";
const MY_REGISTRATIONS_PATH = "/my/registrations";
const CELEBRITY_DINNER_SIGNUP_PATH = "/celebrity-dinner";
// Which event this sign-up is for comes from the link (?event=<eventType>), never from a build-time constant.
// A hosted-checkout return drops the query string, so the event is also remembered for this tab.
const EVENT_MEMORY_KEY = "wmf-dinner-event";
function readEventType() {
  const fromLink = (new URLSearchParams(window.location.search).get("event") || "").trim();
  try {
    if (fromLink) {
      sessionStorage.setItem(EVENT_MEMORY_KEY, fromLink);
      return fromLink;
    }
    return sessionStorage.getItem(EVENT_MEMORY_KEY) || "";
  } catch {
    return fromLink;
  }
}

const yen = (value) => `¥${Number(value || 0).toLocaleString("en-US")}`;

function formatDinnerDate(value) {
  if (!value) return "";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "";
  return date.toLocaleString(undefined, {
    month: "long", day: "numeric", year: "numeric", hour: "numeric", minute: "2-digit",
  });
}

// Copied from MyRegistrations.jsx's own CelebrityDinnerSection — same
// "copied constant" convention used throughout this app (see
// plugins/seat-upgrade/backend/lambdas/seat_upgrade_emailer.py) since these
// aren't shared modules.
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

/**
 * Standalone Dinner with Celebrity sign-up for someone with NO underlying event
 * registration — books a table by name/email alone, no attendee-count cap
 * (unlike the registration-scoped sign-up in MyRegistrations.jsx, which is
 * capped by that registration's own party size). Reached at /celebrity-dinner
 * once signed in via the same reg-token login as the rest of the app.
 */
export default function CelebrityDinnerSignup({ navigateTo, onLogout }) {
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [success, setSuccess] = useState("");
  const [saving, setSaving] = useState(false);
  const [config, setConfig] = useState(null);
  const [windowStatus, setWindowStatus] = useState("");
  const [dinnerReg, setDinnerReg] = useState(null);
  const [remainingCapacity, setRemainingCapacity] = useState(null);
  const [name, setName] = useState("");
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
  const email = getRegistrantEmail();
  const [eventType] = useState(readEventType);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError("");
    if (!eventType) {
      setError("This sign-up needs an event. Please open it from the link on your registration page.");
      setLoading(false);
      return undefined;
    }
    getMyGuestCelebrityDinner(eventType)
      .then((data) => {
        if (cancelled) return;
        const availableMethods = Array.isArray(data.config?.paymentMethods) ? data.config.paymentMethods : [];
        setConfig(data.config || null);
        setWindowStatus(data.windowStatus || "");
        setRemainingCapacity(data.remainingCapacity ?? null);
        setDinnerReg(data.registration || null);
        setName(data.registration?.name || "");
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

        // Returning here with ?celebrityDinnerId=&provider= after a PayPay
        // or Square hosted checkout — see my_registrations/handler.py's
        // checkout_my_guest_celebrity_dinner redirect_url. The GET above
        // already reconciled it against the provider.
        const params = new URLSearchParams(window.location.search);
        const returningId = params.get("celebrityDinnerId");
        if (returningId && data.registration?.dinnerRegistrationId === returningId) {
          window.history.replaceState({}, "", `${CELEBRITY_DINNER_SIGNUP_PATH}?event=${encodeURIComponent(eventType)}`);
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
  }, []);

  const nav = (
    <Navbar
      navigateTo={navigateTo}
    />
  );

  if (loading) {
    return (
      <>
        {nav}
        <main className="page-wrap regform-reg">
          <section className="form-card">
            <div className="form-body">
              <p className="field-note">Loading Dinner with Celebrity details…</p>
            </div>
          </section>
        </main>
      </>
    );
  }

  if (!config?.enabled) {
    return (
      <>
        {nav}
        <main className="page-wrap regform-reg">
          <header className="event-header">
            <div className="banner-title">Dinner with Celebrity</div>
            <div className="sky-bar" />
          </header>
          <section className="form-card">
            <div className="form-body" style={{ textAlign: "center" }}>
              <p className="sub">The Dinner with Celebrity isn't open for sign-ups right now.</p>
              <div className="btn-row" style={{ justifyContent: "center" }}>
                <button type="button" className="btn-primary" onClick={() => navigateTo(HOME_PATH)}>
                  Back Home
                </button>
              </div>
            </div>
          </section>
        </main>
      </>
    );
  }

  const canEdit = !dinnerReg || dinnerReg.status === "pending";
  const showPriceBreakdown = canEdit;
  const availableMethods = DINNER_PAYMENT_METHODS.filter((m) => (config.paymentMethods || []).includes(m.key));
  const payableOnline = dinnerReg && ["paypay", "square"].includes(dinnerReg.paymentProvider || dinnerReg.paymentMethod);
  // Admin-configurable: the Cancel/Cancel & Request Refund button disappears
  // once cancellations are turned off, or once the refund window has closed
  // (empty refundWindowEndsAt = no cutoff) — see CelebrityDinnerManagement.jsx
  // and celebrity_dinner_logic.cancellation_allowed, which the backend
  // enforces independently of this — hiding the button is a UI convenience,
  // not the actual enforcement.
  const canCancelBooking =
    config.cancellationEnabled !== false && (!config.refundWindowEndsAt || new Date().toISOString() <= config.refundWindowEndsAt);

  const startCheckout = async (provider) => {
    const { checkoutUrl } = await checkoutMyGuestCelebrityDinner(eventType, provider);
    window.location.href = checkoutUrl;
  };

  const handleSave = async (event) => {
    event.preventDefault();
    setError("");
    setSuccess("");
    const trimmedName = name.trim();
    if (!trimmedName) {
      setError("Enter your name.");
      return;
    }
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
    if (remainingCapacity !== null && totalGuests > remainingCapacity) {
      setError(`Only ${remainingCapacity} Dinner with Celebrity seat(s) left.`);
      return;
    }
    if (!paymentMethod) {
      setError("Select a payment method.");
      return;
    }
    setSaving(true);
    try {
      const saved = await saveMyGuestCelebrityDinner(eventType, trimmedName, counts, paymentMethod);
      setDinnerReg(saved);
      const method = DINNER_PAYMENT_METHODS.find((m) => m.key === paymentMethod);
      if (method?.online) {
        // Go straight to hosted checkout — no separate "Pay Now" click
        // needed for the common case (mirrors SeatUpgrade.jsx's own flow).
        await startCheckout(paymentMethod);
        return;
      }
      setSuccess(dinnerReg ? "Dinner with Celebrity booking updated." : "Booked for the Dinner with Celebrity.");
    } catch (err) {
      setError(err?.message || "Could not save your Dinner with Celebrity booking.");
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
        ? "Cancel your Dinner with Celebrity booking and request a refund?"
        : "Cancel your Dinner with Celebrity booking?";
    if (!window.confirm(confirmMessage)) return;
    setError("");
    setSuccess("");
    setSaving(true);
    try {
      const { registration } = await cancelMyGuestCelebrityDinner(eventType);
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
        setSuccess("Dinner with Celebrity booking cancelled.");
      } else {
        setDinnerReg(registration);
        setSuccess("Cancellation requested — your refund is being processed.");
      }
    } catch (err) {
      setError(err?.message || "Could not cancel your Dinner with Celebrity booking.");
    } finally {
      setSaving(false);
    }
  };

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
    <>
      {nav}
      <main className="page-wrap regform-reg">
        <header className="event-header">
          <div className="banner-title">Dinner with Celebrity</div>
          <div className="banner-sub-title">{email ? `Signed in as ${email}` : "Book your Dinner with Celebrity seats"}</div>
          <div className="sky-bar" />
        </header>

        <section className="form-card">
          <div className="form-body">
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
            {showPriceBreakdown ? (
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
              </>
            ) : null}
            {config.conditions && !dinnerReg ? <p className="field-note" style={{ marginTop: "6px" }}>{config.conditions}</p> : null}

            {returnBanner ? (
              <p className={returnBanner.status === "paid" ? "success-banner" : "field-note regform-capacity-note"}>
                {returnBanner.status === "paid"
                  ? "Payment received — your Dinner with Celebrity booking is confirmed."
                  : "We're still confirming your payment with the provider. This usually takes a moment — refresh in a bit if the status below doesn't update."}
              </p>
            ) : null}

            {windowStatus === "not_open" ? (
              <p className="regform-note" style={{ marginTop: "10px" }}>Registration for the Dinner with Celebrity has not opened yet.</p>
            ) : windowStatus === "closed" && !dinnerReg ? (
              <p className="regform-note" style={{ marginTop: "10px" }}>Registration for the Dinner with Celebrity has closed.</p>
            ) : remainingCapacity === 0 && !dinnerReg ? (
              <p className="regform-note" style={{ marginTop: "10px" }}>The Dinner with Celebrity is fully booked.</p>
            ) : null}

            {error ? <div className="error-banner" style={{ marginTop: "10px" }}>{error}</div> : null}
            {success ? <div className="success-banner" style={{ marginTop: "10px" }}>{success}</div> : null}

            {dinnerReg ? (
              <>
                <div className="regform-review-grid" style={{ marginTop: "10px" }}>
                  <div className="regform-review-row">
                    <span className="regform-review-label">Name</span>
                    <span className="regform-review-value">{dinnerReg.name || "—"}</span>
                  </div>
                  <div className="regform-review-row">
                    <span className="regform-review-label">Email</span>
                    <span className="regform-review-value">{dinnerReg.email || "—"}</span>
                  </div>
                  <div className="regform-review-row">
                    <span className="regform-review-label">Payment Method</span>
                    <span className="regform-review-value">
                      {DINNER_PAYMENT_METHODS.find((m) => m.key === (dinnerReg.paymentProvider || dinnerReg.paymentMethod))?.label ||
                        dinnerReg.paymentMethod ||
                        "—"}
                    </span>
                  </div>
                  <div className="regform-review-row">
                    <span className="regform-review-label">Payment Status</span>
                    <span className="regform-review-value">
                      <span className={`payment-badge ${dinnerReg.status}`}>{DINNER_STATUS_LABELS[dinnerReg.status] || dinnerReg.status}</span>
                    </span>
                  </div>
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
              <p className="field-note">We'll mark your bank transfer as paid once we see it arrive — no further action needed here.</p>
            ) : null}
            {dinnerReg?.status === "refund_requested" ? (
              <p className="field-note">
                Your refund is being processed — no further action needed here. Card and PayPay refunds are requested
                automatically; a bank transfer refund is sent by hand and may take a little longer.
              </p>
            ) : null}

            {canEdit && (windowStatus === "open" || dinnerReg) && (remainingCapacity !== 0 || dinnerReg) ? (
              <form style={{ marginTop: "14px" }} onSubmit={handleSave}>
                <div className="field" style={{ marginBottom: "14px" }}>
                  <label htmlFor="dinnerGuestName">Name</label>
                  <input
                    id="dinnerGuestName"
                    type="text"
                    value={name}
                    onChange={(event) => setName(event.target.value)}
                    disabled={saving || windowStatus !== "open"}
                  />
                </div>
                <div className="field" style={{ marginBottom: "14px" }}>
                  <label>Guests</label>
                  {remainingCapacity !== null ? (
                    <p className="field-note" style={{ marginTop: 0, marginBottom: "8px" }}>
                      {remainingCapacity > 0
                        ? `${remainingCapacity} seat${remainingCapacity === 1 ? "" : "s"} left for the Dinner with Celebrity.`
                        : "The Dinner with Celebrity is fully booked."}
                    </p>
                  ) : null}
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
                            <th scope="row">{label}</th>
                            <td>
                              <input
                                type="number"
                                min="0"
                                aria-label={`${label} — Veg`}
                                value={guestCounts[`${key}Veg`]}
                                onChange={(event) => setGuestCounts((prev) => ({ ...prev, [`${key}Veg`]: event.target.value }))}
                                disabled={saving || windowStatus !== "open"}
                              />
                            </td>
                            <td>
                              <input
                                type="number"
                                min="0"
                                aria-label={`${label} — Non-Veg`}
                                value={guestCounts[`${key}NonVeg`]}
                                onChange={(event) => setGuestCounts((prev) => ({ ...prev, [`${key}NonVeg`]: event.target.value }))}
                                disabled={saving || windowStatus !== "open"}
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
                    <label htmlFor="dinnerGuestPaymentMethod">Payment Method</label>
                    <select
                      id="dinnerGuestPaymentMethod"
                      value={paymentMethod}
                      onChange={(event) => setPaymentMethod(event.target.value)}
                      disabled={saving || windowStatus !== "open"}
                    >
                      {availableMethods.map((m) => (
                        <option key={m.key} value={m.key}>{m.label}</option>
                      ))}
                    </select>
                  </div>
                </div>
                <div className="btn-row" style={{ marginTop: "10px" }}>
                  {windowStatus === "open" ? (
                    <button type="submit" className="btn-primary" disabled={saving}>
                      {saving ? "Saving…" : dinnerReg ? "Update" : "Book Now"}
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
          </div>
        </section>
      </main>
    </>
  );
}
