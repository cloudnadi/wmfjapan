import { lazy, useEffect, useMemo, useState } from "react";
import Navbar from "./Navbar";
import { checkInSeatPurchase, resolveSeatCheckIn } from "./api";
import { isAuthenticated } from "./cognito";
import { seatDisplayLabel } from "seat-selection-plugin";

// Door check-in for a Celebrity Event seat purchase — same admin-gated,
// scan-a-QR pattern as EntryPassCheckIn, but against seat_purchase_logic's
// own checkInToken, a wholly separate ticket from that mechanism's
// registrationPassToken. Kept as its own screen/route rather than merged
// into EntryPassCheckIn, since a seat purchase and an entry pass are two
// unrelated admissions this app never lets gate on each other's state.
const AdminDashboard = lazy(() => import("./AdminDashboard"));

const PAYMENT_METHOD_LABELS = { paypay: "PayPay", square: "Credit/Debit Card (Square)", bank: "Bank Transfer" };

function formatYen(amount) {
  return `¥${Number(amount || 0).toLocaleString()}`;
}

export default function SeatPurchaseCheckIn({ navigateTo }) {
  const token = useMemo(() => new URLSearchParams(window.location.search).get("token") || "", []);
  const [authenticated, setAuthenticated] = useState(() => isAuthenticated());
  const [purchase, setPurchase] = useState(null);
  const [addons, setAddons] = useState(null);
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");

  const loadTicket = async () => {
    if (!token || !authenticated) return;
    setLoading(true);
    setError("");
    try {
      const data = await resolveSeatCheckIn(token);
      setPurchase(data.purchase);
      setAddons(data.addons || null);
    } catch (err) {
      setError(err?.message || "This ticket could not be loaded.");
      setPurchase(null);
      setAddons(null);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    loadTicket();
  }, [authenticated]);

  const handleCheckIn = async () => {
    if (!purchase || purchase.checkedIn || saving) return;
    setSaving(true);
    setError("");
    try {
      const data = await checkInSeatPurchase(token);
      setPurchase(data.purchase);
      setAddons(data.addons || null);
    } catch (err) {
      setError(err?.message || "Check-in could not be completed.");
      await loadTicket();
    } finally {
      setSaving(false);
    }
  };

  // Same consolidated view as the guest check-in ticket PDF this purchase's
  // QR is attached to — whichever of these this registrant has, if any.
  const zoneUpgrade = addons?.zoneUpgrade;
  const celebrityDinner = addons?.celebrityDinner;
  const dinnerGuestCount = celebrityDinner
    ? ["numberOfAdultsVeg", "numberOfAdultsNonVeg", "numberOfKids914Veg", "numberOfKids914NonVeg",
       "numberOfKids68Veg", "numberOfKids68NonVeg", "numberOfKidsUnder6Veg", "numberOfKidsUnder6NonVeg"]
        .reduce((total, key) => total + (Number(celebrityDinner[key]) || 0), 0)
    : 0;

  if (!authenticated) {
    return (
      <AdminDashboard
        embedded
        onAuthenticated={() => setAuthenticated(true)}
        onLogout={() => navigateTo("/")}
      />
    );
  }

  const canCheckIn = purchase && purchase.status === "paid" && !purchase.checkedIn;

  return (
    <>
      <Navbar navigateTo={navigateTo} brand="WMF Japan Check-in" />
      <main className="page-wrap entry-checkin-page">
        <header className="event-header">
          <div className="banner-title">Seat Ticket Check-in</div>
          <div className="sky-bar" />
        </header>

        {!token ? <div className="error-banner">This QR code does not contain a valid token.</div> : null}
        {loading ? <section className="form-card"><div className="form-body"><p className="field-note">Loading ticket...</p></div></section> : null}
        {error ? <div className="error-banner">{error}</div> : null}

        {purchase ? (
          <section className="form-card entry-checkin-card">
            <div className={`entry-checkin-state ${canCheckIn || purchase.checkedIn ? "valid" : "invalid"}`}>
              {purchase.checkedIn
                ? "Already Checked In"
                : purchase.status === "paid"
                  ? "Valid Ticket"
                  : purchase.status === "cancelled"
                    ? "Ticket Cancelled"
                    : purchase.status === "refund_requested"
                      ? "Refund In Progress"
                      : "Payment Not Complete"}
            </div>
            <div className="entry-checkin-details">
              <div><span>Name</span><strong>{purchase.name || "-"}</strong></div>
              <div><span>Email</span><strong>{purchase.email || "-"}</strong></div>
              <div className="entry-checkin-list-detail">
                <span>Seats</span>
                <strong>{(purchase.seatIds || []).map(seatDisplayLabel).join(", ") || "-"}</strong>
              </div>
              <div><span>Amount Paid</span><strong>{formatYen(purchase.totalAmount)}</strong></div>
              <div>
                <span>Payment</span>
                <strong>{PAYMENT_METHOD_LABELS[purchase.paymentMethod] || purchase.paymentMethod || "-"}</strong>
              </div>
              {purchase.checkedIn ? (
                <div><span>Checked in</span><strong>{purchase.checkedInAt ? new Date(purchase.checkedInAt).toLocaleString() : "-"}</strong></div>
              ) : null}
              {zoneUpgrade ? (
                <div className="entry-checkin-list-detail">
                  <span>Zone Upgrade</span>
                  <ul className="entry-checkin-list">
                    <li>
                      <strong>{zoneUpgrade.tierName || "Zone Upgrade"}</strong>
                      <span>
                        {zoneUpgrade.seatCount ? `${zoneUpgrade.seatCount} seat${zoneUpgrade.seatCount === 1 ? "" : "s"}` : ""}
                        {zoneUpgrade.totalAmount ? ` · ${formatYen(zoneUpgrade.totalAmount)}` : ""}
                        {zoneUpgrade.status ? ` · ${zoneUpgrade.status}` : ""}
                      </span>
                    </li>
                  </ul>
                </div>
              ) : null}
              {celebrityDinner ? (
                <div className="entry-checkin-list-detail">
                  <span>Dinner with Celebrity</span>
                  <ul className="entry-checkin-list">
                    <li>
                      <strong>{dinnerGuestCount} guest{dinnerGuestCount === 1 ? "" : "s"}</strong>
                      <span>
                        {celebrityDinner.totalAmount ? `${formatYen(celebrityDinner.totalAmount)} · ` : ""}
                        {celebrityDinner.status || ""}
                      </span>
                    </li>
                  </ul>
                </div>
              ) : null}
            </div>

            {purchase.status !== "paid" && !purchase.checkedIn ? (
              <div className="error-banner" style={{ marginTop: "12px" }}>
                This ticket is not paid, so entry cannot be granted. Send them to the registration desk.
              </div>
            ) : null}

            {canCheckIn ? (
              <div className="entry-checkin-action">
                <button type="button" className="button primary" onClick={handleCheckIn} disabled={saving}>
                  {saving ? "Checking in..." : `Check in ${purchase.seatIds?.length || 0} seat${purchase.seatIds?.length === 1 ? "" : "s"}`}
                </button>
              </div>
            ) : null}
          </section>
        ) : null}
      </main>
    </>
  );
}
