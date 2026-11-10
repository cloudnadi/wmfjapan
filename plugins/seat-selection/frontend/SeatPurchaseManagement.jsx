import { useEffect, useMemo, useState } from "react";
import { seatDisplayLabel } from "./rowLabels";

// Admin view of Celebrity Event seat *purchases* for one event — the paid
// counterpart to SeatMapManagement's free block/unblock screen. A purchase
// already carries its own price breakdown and payment method (computed at
// checkout time, see seat_purchase_logic.create_purchase_request), so this
// screen is read-mostly: its only writes are the status transitions an
// admin is expected to make by hand — marking a bank-transfer request paid
// once the money's in, or confirming a refund once it's actually been sent
// back (the same admin-confirms pattern seat-upgrade uses).
//
// Host-agnostic, same pattern as SeatMapManagement: getEventSeatPurchases/
// updateSeatPurchaseStatus are injected props for the host's own fetch/auth.

const STATUS_LABELS = {
  pending: "Awaiting Payment",
  paid: "Paid",
  refund_requested: "Refund Requested",
  cancelled: "Cancelled",
};

const PAYMENT_METHOD_LABELS = { paypay: "PayPay", square: "Square", bank: "Bank Transfer" };

function formatYen(amount) {
  return `¥${Number(amount || 0).toLocaleString()}`;
}

export default function SeatPurchaseManagement({
  events = [],
  initialEventType = "",
  getEventSeatPurchases,
  updateSeatPurchaseStatus,
}) {
  const selectableEvents = useMemo(
    () =>
      events
        .filter((event) => event.eventType)
        .sort((a, b) => String(a.eventName || a.eventType).localeCompare(String(b.eventName || b.eventType))),
    [events]
  );
  const fallbackEventType = selectableEvents[0]?.eventType || "";
  const [eventType, setEventType] = useState(initialEventType || fallbackEventType);
  const [purchases, setPurchases] = useState([]);
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState("");
  const [success, setSuccess] = useState("");
  const [busyId, setBusyId] = useState("");

  useEffect(() => {
    if (!eventType && fallbackEventType) {
      setEventType(fallbackEventType);
    }
  }, [eventType, fallbackEventType]);

  useEffect(() => {
    if (initialEventType && initialEventType !== eventType) {
      setEventType(initialEventType);
    }
  }, [initialEventType, eventType]);

  useEffect(() => {
    if (eventType) {
      loadPurchases(eventType);
    } else {
      setPurchases([]);
    }
  }, [eventType]);

  async function loadPurchases(nextEventType = eventType) {
    if (!nextEventType) return;
    setIsLoading(true);
    setError("");
    try {
      const items = await getEventSeatPurchases(nextEventType);
      setPurchases(
        [...items].sort((a, b) => String(b.requestedAt || "").localeCompare(String(a.requestedAt || "")))
      );
    } catch (err) {
      setError(err.message || "Failed to load seat purchases.");
      setPurchases([]);
    } finally {
      setIsLoading(false);
    }
  }

  async function setStatus(purchase, status, confirmMessage) {
    if (confirmMessage && !window.confirm(confirmMessage)) return;
    setError("");
    setSuccess("");
    setBusyId(purchase.purchaseId);
    try {
      await updateSeatPurchaseStatus(eventType, purchase.purchaseId, status);
      setSuccess(`${purchase.name || purchase.email || purchase.purchaseId} — marked ${STATUS_LABELS[status].toLowerCase()}.`);
      await loadPurchases(eventType);
    } catch (err) {
      setError(err.message || "Failed to update this purchase.");
    } finally {
      setBusyId("");
    }
  }

  const totals = purchases.reduce(
    (acc, p) => {
      acc.count += 1;
      if (p.status === "paid") acc.paidAmount += Number(p.totalAmount || 0);
      if (p.status === "pending") acc.pendingCount += 1;
      if (p.status === "refund_requested") acc.refundCount += 1;
      return acc;
    },
    { count: 0, paidAmount: 0, pendingCount: 0, refundCount: 0 }
  );

  return (
    <>
      <div className="admin-stats entry-slot-stats">
        <div className="stat-box">
          <div className="stat-value">{totals.count}</div>
          <div className="stat-label">Purchases</div>
        </div>
        <div className="stat-box">
          <div className="stat-value">{formatYen(totals.paidAmount)}</div>
          <div className="stat-label">Collected</div>
        </div>
        <div className="stat-box">
          <div className="stat-value">{totals.pendingCount}</div>
          <div className="stat-label">Awaiting Payment</div>
        </div>
        <div className="stat-box">
          <div className="stat-value">{totals.refundCount}</div>
          <div className="stat-label">Refund Requested</div>
        </div>
      </div>

      <div className="admin-controls">
        <select
          value={eventType}
          onChange={(event) => setEventType(event.target.value)}
          className="search-input"
          aria-label="Select event for seat purchases"
        >
          <option value="">Select event...</option>
          {selectableEvents.map((event) => (
            <option key={event.eventType} value={event.eventType}>
              {event.eventName || event.eventType}
            </option>
          ))}
        </select>
        <button className="button secondary" type="button" onClick={() => loadPurchases()} disabled={!eventType || isLoading}>
          {isLoading ? "Refreshing..." : "Refresh"}
        </button>
      </div>

      {error ? <div className="error-banner">{error}</div> : null}
      {success ? <div className="success-banner">{success}</div> : null}

      {!eventType ? (
        <div className="empty-state">Select an event to view its seat purchases.</div>
      ) : isLoading ? (
        <div className="loading">Loading seat purchases...</div>
      ) : purchases.length === 0 ? (
        <div className="empty-state">No seat purchases yet for this event.</div>
      ) : (
        <div style={{ overflowX: "auto" }}>
          <table className="registrations-table">
            <thead>
              <tr>
                <th>Registrant</th>
                <th>Seats</th>
                <th>Pricing</th>
                <th>Amount</th>
                <th>Payment</th>
                <th>Status</th>
                <th>Requested</th>
                <th>Actions</th>
              </tr>
            </thead>
            <tbody>
              {purchases.map((purchase) => {
                const busy = busyId === purchase.purchaseId;
                return (
                  <tr key={purchase.purchaseId}>
                    <td>
                      {purchase.name || "—"}
                      <div className="field-note">{purchase.email}</div>
                    </td>
                    <td>{(purchase.seatIds || []).map(seatDisplayLabel).join(", ")}</td>
                    <td>{purchase.pricingMode === "family" ? "Family" : "Person"}</td>
                    <td>{formatYen(purchase.totalAmount)}</td>
                    <td>
                      {PAYMENT_METHOD_LABELS[purchase.paymentMethod] || purchase.paymentMethod || "—"}
                      {purchase.paymentReference ? (
                        <div className="field-note" title="Provider transaction ID">
                          Txn: {purchase.paymentReference}
                        </div>
                      ) : null}
                    </td>
                    <td>
                      <span className={`seat-purchase-status ${purchase.status}`}>
                        {STATUS_LABELS[purchase.status] || purchase.status}
                      </span>
                      {purchase.checkedIn ? <div className="field-note">✅ Checked in</div> : null}
                    </td>
                    <td>{purchase.requestedAt ? new Date(purchase.requestedAt).toLocaleDateString() : "—"}</td>
                    <td>
                      <div className="btn-row">
                        {purchase.status === "pending" ? (
                          <button
                            className="button secondary"
                            type="button"
                            disabled={busy}
                            onClick={() =>
                              setStatus(
                                purchase,
                                "paid",
                                `Mark this purchase paid? Only do this once you've confirmed the ${
                                  PAYMENT_METHOD_LABELS[purchase.paymentMethod] || "payment"
                                } was received.`
                              )
                            }
                          >
                            Mark Paid
                          </button>
                        ) : null}
                        {purchase.status === "refund_requested" ? (
                          <button
                            className="button secondary"
                            type="button"
                            disabled={busy}
                            onClick={() =>
                              setStatus(
                                purchase,
                                "cancelled",
                                "Confirm the refund has actually been sent? This releases the seats and notifies the registrant."
                              )
                            }
                          >
                            Confirm Refund Sent
                          </button>
                        ) : null}
                        {(purchase.status === "pending" || purchase.status === "paid") && !purchase.checkedIn ? (
                          <button
                            className="button danger"
                            type="button"
                            disabled={busy}
                            onClick={() =>
                              setStatus(
                                purchase,
                                "cancelled",
                                purchase.status === "paid"
                                  ? "Cancel this paid purchase and release its seats? This does not send a refund automatically — arrange that separately."
                                  : "Cancel this purchase and release its seats?"
                              )
                            }
                          >
                            Cancel
                          </button>
                        ) : null}
                      </div>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </>
  );
}
