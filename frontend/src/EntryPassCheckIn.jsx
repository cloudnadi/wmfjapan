import { lazy, useEffect, useMemo, useState } from "react";
import Navbar from "./Navbar";
import { checkInEntryPass, resolveEntryPass } from "./api";
import { isAuthenticated } from "./cognito";

// Borrowed purely as the sign-in gate below, so keep it out of the scanner's
// bundle: a gate device that is already signed in never loads it.
const AdminDashboard = lazy(() => import("./AdminDashboard"));

const PAYMENT_STATUS_LABELS = {
  pending: "Payment pending",
  paid: "Paid",
  refunded: "Refunded",
};

function formatYen(amount) {
  return `¥${Number(amount || 0).toLocaleString()}`;
}

const ATTENDEE_LABELS = {
  adult: "Adults",
  adults: "Adults",
  teen: "Teens",
  teens: "Teens",
  child: "Children",
  children: "Children",
  toddler: "Toddlers",
  toddlers: "Toddlers",
  kids914: "Kids (9-14)",
  kids68: "Kids (6-8)",
  kidsUnder6: "Kids (under 6)",
};

export default function EntryPassCheckIn({ navigateTo }) {
  const token = useMemo(() => new URLSearchParams(window.location.search).get("token") || "", []);
  const [authenticated, setAuthenticated] = useState(() => isAuthenticated());
  const [passData, setPassData] = useState(null);
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");

  const loadPass = async () => {
    if (!token || !authenticated) return;
    setLoading(true);
    setError("");
    try {
      const data = await resolveEntryPass(token);
      setPassData(data);
    } catch (err) {
      setError(err?.message || "This entry pass could not be loaded.");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    loadPass();
  }, [authenticated]);

  const allocations = passData?.allocations || [];
  const summary = passData?.summary || {};
  const registration = passData?.registration;
  const totalAllocated = Number(summary.totalAllocated) || 0;
  const totalCheckedIn = Number(summary.totalCheckedIn) || 0;
  const remaining = Math.max(totalAllocated - totalCheckedIn, 0);
  const fullyUsed = totalAllocated > 0 && remaining === 0;
  const cancelled = allocations.some((item) => item.status === "CANCELLED");
  // The backend is the authority (it rejects an unpaid check-in outright); this
  // only spares the volunteer a pointless tap and explains the hold-up.
  const paymentComplete = summary.paymentComplete !== false;
  const canCheckIn = !cancelled && !fullyUsed && paymentComplete && remaining > 0;
  const attendeeSummary = Object.entries(registration?.attendeeCounts || {})
    .filter(([, count]) => Number(count) > 0)
    .map(([key, count]) => ({ label: ATTENDEE_LABELS[key] || key, count }));
  const entrySlots = allocations
    .filter((item) => item.slotName)
    .map((item) => ({ name: item.slotName, seats: Number(item.seatCount) || 0 }));
  // Same consolidated view as the guest check-in ticket PDF this pass's QR
  // is attached to — whichever of these a registrant has, if any.
  const zoneUpgrade = passData?.addons?.zoneUpgrade;
  const celebrityDinner = passData?.addons?.celebrityDinner;
  const seatSelection = passData?.addons?.seatSelection;
  const dinnerGuestCount = celebrityDinner
    ? ["numberOfAdultsVeg", "numberOfAdultsNonVeg", "numberOfKids914Veg", "numberOfKids914NonVeg",
       "numberOfKids68Veg", "numberOfKids68NonVeg", "numberOfKidsUnder6Veg", "numberOfKidsUnder6NonVeg"]
        .reduce((total, key) => total + (Number(celebrityDinner[key]) || 0), 0)
    : 0;

  const handleCheckIn = async () => {
    if (!canCheckIn || saving) return;
    setSaving(true);
    setError("");
    try {
      const data = await checkInEntryPass(token);
      setPassData(data);
    } catch (err) {
      setError(err?.message || "Check-in could not be completed.");
      await loadPass();
    } finally {
      setSaving(false);
    }
  };

  if (!authenticated) {
    return (
      <AdminDashboard
        embedded
        onAuthenticated={() => setAuthenticated(true)}
        onLogout={() => navigateTo("/")}
      />
    );
  }

  return (
    <>
      <Navbar navigateTo={navigateTo} brand="WMF Japan Check-in" />
      <main className="page-wrap entry-checkin-page">
        <header className="event-header">
          <div className="banner-title">Entry Pass Check-in</div>
          <div className="sky-bar" />
        </header>

        {!token ? <div className="error-banner">This QR pass does not contain a valid token.</div> : null}
        {loading ? <section className="form-card"><div className="form-body"><p className="field-note">Loading entry pass...</p></div></section> : null}
        {error ? <div className="error-banner">{error}</div> : null}

        {passData ? (
          <section className="form-card entry-checkin-card">
            <div className={`entry-checkin-state ${fullyUsed || cancelled || !paymentComplete ? "invalid" : "valid"}`}>
              {fullyUsed
                ? "Entry Pass Fully Used"
                : cancelled
                  ? "Entry Pass Cancelled"
                  : !paymentComplete
                    ? "Payment Not Complete"
                    : "Valid Entry Pass"}
            </div>
            <div className="entry-checkin-details">
              <div><span>Registration</span><strong>{registration?.registrationId || "-"}</strong></div>
              <div><span>Name</span><strong>{registration?.name || "-"}</strong></div>
              <div><span>Email</span><strong>{registration?.email || "-"}</strong></div>
              <div className="entry-checkin-list-detail">
                <span>Attendees</span>
                {attendeeSummary.length ? (
                  <ul className="entry-checkin-list">
                    {attendeeSummary.map((attendee) => (
                      <li key={attendee.label}>
                        <strong>{attendee.label}</strong>
                        <span>{attendee.count}</span>
                      </li>
                    ))}
                  </ul>
                ) : <strong>-</strong>}
              </div>
              <div className="entry-checkin-list-detail">
                <span>Entry slots</span>
                {entrySlots.length ? (
                  <ul className="entry-checkin-list">
                    {entrySlots.map((slot, index) => (
                      <li key={`${slot.name}-${index}`}>
                        <strong>{slot.name}</strong>
                        <span>{slot.seats} {slot.seats === 1 ? "person" : "people"}</span>
                      </li>
                    ))}
                  </ul>
                ) : <strong>-</strong>}
              </div>
              <div><span>Allocated</span><strong>{totalAllocated} people</strong></div>
              <div><span>Checked in</span><strong>{totalCheckedIn} people</strong></div>
              <div><span>Remaining</span><strong>{remaining} people</strong></div>
              <div>
                <span>Payment</span>
                <strong>
                  {PAYMENT_STATUS_LABELS[summary.paymentStatus] || registration?.paymentStatus || "—"}
                </strong>
              </div>
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
              {seatSelection ? (
                <div className="entry-checkin-list-detail">
                  <span>Seat Selection</span>
                  <ul className="entry-checkin-list">
                    <li>
                      <strong>{(seatSelection.seatIds || []).join(", ") || "-"}</strong>
                      <span>
                        {seatSelection.pricingEnabled && seatSelection.totalAmount ? `${formatYen(seatSelection.totalAmount)} · ` : ""}
                        {seatSelection.status || ""}
                      </span>
                    </li>
                  </ul>
                </div>
              ) : null}
            </div>

            {!paymentComplete ? (
              <div className="error-banner" style={{ marginTop: "12px" }}>
                Payment for this registration is not complete, so entry cannot be granted.
                Send them to the registration desk to settle it first.
              </div>
            ) : null}

            {canCheckIn ? (
              <div className="entry-checkin-action">
                <button type="button" className="button primary" onClick={handleCheckIn} disabled={saving}>
                  {saving ? "Checking in..." : `Check in all ${remaining} people`}
                </button>
              </div>
            ) : null}
          </section>
        ) : null}
      </main>
    </>
  );
}
