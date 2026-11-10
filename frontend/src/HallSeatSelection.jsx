import { useEffect, useMemo, useRef, useState } from "react";
import Navbar from "./Navbar";
import { SeatMapSelector, seatDisplayLabel } from "seat-selection-plugin";
import {
  getMyRegistrations,
  getAvailableSeats,
  getMySeatPurchases,
  createMySeatPurchase,
  getMySeatPurchase,
  checkoutMySeatPurchase,
} from "./api";
import { getRegistrantEmail } from "./registrantAuth";

const HOME_PATH = "/";
const MY_REGISTRATIONS_PATH = "/my/registrations";

// Celebrity Event seat purchases apply only to these registration options.
const SEATED_OPTIONS = ["event", "combo"];

// Set by MyRegistrations before navigating here, so a click from a specific
// registration's card lands on that registration rather than making the
// visitor pick again from a list. Read once and cleared on mount.
const PREFERRED_REGISTRATION_KEY = "wmf-seat-selection-registration-id";

const PAYMENT_METHOD_LABELS = { paypay: "PayPay", square: "Credit/Debit Card (Square)", bank: "Bank Transfer" };

// Same category labels seat-selection-plugin's SeatMapSelector uses internally
// (not exported from there) — needed here only for the free-selection order
// summary, which has no other source for a zone's display name once pricing
// (and its own admin-set displayName) is off.
const CATEGORY_LABELS = { zone1: "Zone 1", zone2: "Zone 2", zone3: "Zone 3", regular: "Regular" };

function isSeatEligible(reg) {
  return (
    SEATED_OPTIONS.includes(reg.registrationOption) &&
    String(reg.paymentStatus || "").toLowerCase() === "paid"
  );
}

function formatYen(amount) {
  return `¥${Number(amount || 0).toLocaleString()}`;
}

function buildSeatCategoryMap(layout) {
  const map = {};
  for (const row of layout || []) {
    for (const seat of row.seats || []) {
      map[seat.seatId] = seat.category;
    }
  }
  return map;
}

// Client-side mirror of seat_selection_logic.price_seats — an instant
// estimate while picking seats. The purchase-creation call recomputes this
// itself server-side (the actual source of truth), so a mismatch here can
// only ever under/overstate the *preview*, never the amount charged.
// Regular seats are always free, regardless of zonePricing — see
// seat_selection_logic.PRICEABLE_CATEGORIES. `free` (true when this event's
// eventInfo.seatSelection.pricingEnabled is off) treats every category the
// same as Regular — nothing here is ever priced or "unpriced" — since
// zone1/2/3 access is instead gated by categoryCaps (a zone upgrade),
// enforced separately by SeatMapSelector and the server, not by price.
function estimatePurchase(seatIds, pricingMode, zonePricing, seatCategoryMap, free = false) {
  const byCategory = {};
  const unpriced = [];
  for (const seatId of seatIds) {
    const category = seatCategoryMap[seatId];
    if (!category) {
      unpriced.push(seatId);
      continue;
    }
    if (free || category === "regular") {
      (byCategory[category] ||= []).push(seatId);
      continue;
    }
    const price = zonePricing?.[category];
    if (!price || price.status !== "ACTIVE") {
      unpriced.push(seatId);
      continue;
    }
    (byCategory[category] ||= []).push(seatId);
  }

  const breakdown = [];
  let totalAmount = 0;
  for (const [category, ids] of Object.entries(byCategory)) {
    if (free || category === "regular") {
      breakdown.push({
        category,
        displayName: free ? CATEGORY_LABELS[category] || category : "Regular",
        seatIds: ids,
        familySeats: 0,
        personSeats: ids.length,
        familyFee: 0,
        personFee: 0,
        subtotal: 0,
      });
      continue;
    }
    const price = zonePricing[category];
    const pricePerFamily = Number(price.pricePerFamily || 0);
    const pricePerPerson = Number(price.pricePerPerson || 0);
    const maxFamilySize = Number(price.maxFamilySize || 0);
    const seatCount = ids.length;

    let familySeats = 0;
    let personSeats = seatCount;
    let familyFee = 0;
    let personFee = seatCount * pricePerPerson;
    if (pricingMode === "family" && pricePerFamily > 0) {
      familySeats = maxFamilySize === 0 ? seatCount : Math.min(seatCount, maxFamilySize);
      personSeats = seatCount - familySeats;
      familyFee = pricePerFamily;
      personFee = personSeats * pricePerPerson;
    }

    const subtotal = familyFee + personFee;
    totalAmount += subtotal;
    breakdown.push({
      category,
      displayName: price.displayName || category,
      seatIds: ids,
      familySeats,
      personSeats,
      familyFee,
      personFee,
      subtotal,
    });
  }
  breakdown.sort((a, b) => a.category.localeCompare(b.category));
  return { totalAmount, breakdown, unpriced };
}

export default function HallSeatSelection({ navigateTo, onLogout }) {
  const [registrations, setRegistrations] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [selectedRegistrationId, setSelectedRegistrationId] = useState("");

  const [config, setConfig] = useState(null);
  const [configLoading, setConfigLoading] = useState(false);
  const [configError, setConfigError] = useState("");
  const [returnNotice, setReturnNotice] = useState("");
  // Jumps straight into the wizard the first time a registration's config
  // is ready, so there's no separate landing step to click through —
  // existing purchases/allowance now show inline on My Registrations'
  // registration card instead. Tracked per registrationId so cancelling
  // or submitting the wizard doesn't immediately reopen it.
  const autoOpenedForRef = useRef("");

  const [seatData, setSeatData] = useState(null);
  const [seatLoading, setSeatLoading] = useState(false);
  const [seatError, setSeatError] = useState("");

  const [wizardOpen, setWizardOpen] = useState(false);
  const [wizardStep, setWizardStep] = useState("pick"); // pick | review
  const [pricingMode, setPricingMode] = useState("");
  const [pendingSeatIds, setPendingSeatIds] = useState([]);
  const [paymentMethod, setPaymentMethod] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [submitError, setSubmitError] = useState("");

  const email = getRegistrantEmail() || "";

  useEffect(() => {
    let preferredId = "";
    try {
      preferredId = sessionStorage.getItem(PREFERRED_REGISTRATION_KEY) || "";
      sessionStorage.removeItem(PREFERRED_REGISTRATION_KEY);
    } catch {
      preferredId = "";
    }

    getMyRegistrations()
      .then((items) => {
        const eligible = (items || []).filter(isSeatEligible);
        setRegistrations(eligible);
        if (preferredId && eligible.some((r) => r.registrationId === preferredId)) {
          setSelectedRegistrationId(preferredId);
        } else if (eligible.length === 1) {
          setSelectedRegistrationId(eligible[0].registrationId);
        }
      })
      .catch((err) => setError(err?.message || "Could not load your registrations."))
      .finally(() => setLoading(false));
  }, []);

  const selectedRegistration = useMemo(
    () => registrations.find((r) => r.registrationId === selectedRegistrationId) || null,
    [registrations, selectedRegistrationId]
  );

  const seatCategoryMap = useMemo(() => buildSeatCategoryMap(seatData?.layout), [seatData]);
  const zonePricing = seatData?.zonePricing || {};
  const pricingEnabled = config?.pricingEnabled !== false;
  const estimate = useMemo(
    () => estimatePurchase(pendingSeatIds, pricingMode, zonePricing, seatCategoryMap, !pricingEnabled),
    [pendingSeatIds, pricingMode, zonePricing, seatCategoryMap, pricingEnabled]
  );

  const loadConfig = (registrationId) => {
    if (!registrationId) return;
    setConfigLoading(true);
    setConfigError("");
    getMySeatPurchases(registrationId)
      .then((data) => {
        setConfig({
          enabled: data.enabled,
          pricingEnabled: data.pricingEnabled !== false,
          enabledPaymentMethods: data.enabledPaymentMethods || [],
          seatSelectionWindow: data.seatSelectionWindow || { status: "open" },
          seatsAllowed: data.seatsAllowed ?? 0,
          seatsClaimed: data.seatsClaimed ?? 0,
          seatsRemaining: data.seatsRemaining ?? 0,
          categoryCaps: data.categoryCaps || null,
          purchases: data.purchases || [],
        });
      })
      .catch((err) => setConfigError(err?.message || "Could not load seat selection settings."))
      .finally(() => setConfigLoading(false));
  };

  const loadSeatMap = () => {
    if (!selectedRegistration) return;
    setSeatLoading(true);
    setSeatError("");
    getAvailableSeats(selectedRegistration.eventType)
      .then((data) => {
        setSeatData(data);
        const validIds = new Set(
          (data.layout || []).flatMap((row) => row.seats.map((seat) => seat.seatId))
        );
        setPendingSeatIds((prev) => prev.filter((id) => validIds.has(id)));
      })
      .catch((err) => setSeatError(err?.message || "Could not load the seat map."))
      .finally(() => setSeatLoading(false));
  };

  useEffect(() => {
    if (!selectedRegistration) {
      setSeatData(null);
      setConfig(null);
      return;
    }
    setWizardOpen(false);
    setPendingSeatIds([]);
    loadConfig(selectedRegistration.registrationId);
    loadSeatMap();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedRegistration?.registrationId]);

  // Returning from a PayPay/Square hosted checkout redirect.
  useEffect(() => {
    if (!selectedRegistration) return;
    const params = new URLSearchParams(window.location.search);
    const purchaseId = params.get("purchaseId");
    if (!purchaseId) return;

    getMySeatPurchase(selectedRegistration.registrationId, purchaseId)
      .then((purchase) => {
        setReturnNotice(
          purchase.status === "paid"
            ? "✅ Payment received — your seats are confirmed!"
            : "We're still confirming your payment. This can take a minute — refresh if it doesn't update."
        );
      })
      .catch(() => {})
      .finally(() => {
        const url = new URL(window.location.href);
        url.searchParams.delete("purchaseId");
        url.searchParams.delete("provider");
        window.history.replaceState({}, "", url.toString());
      });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedRegistration?.registrationId]);

  // Jump straight into the wizard the first time this registration's
  // config is ready — see autoOpenedForRef's comment above.
  useEffect(() => {
    if (!selectedRegistration || !config) return;
    if (autoOpenedForRef.current === selectedRegistration.registrationId) return;
    autoOpenedForRef.current = selectedRegistration.registrationId;
    // Someone who already holds seats lands on "Your Seats" instead; they
    // can still choose to pick more from there.
    if (config.seatsClaimed > 0) return;
    if (Boolean(config.enabled) && (config.seatSelectionWindow?.status ?? "open") === "open" && config.seatsRemaining > 0) {
      startWizard();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedRegistration?.registrationId, config]);

  const startWizard = () => {
    setWizardOpen(true);
    setWizardStep("pick");
    // Family/person pricing is meaningless once pricing is off — pick a
    // harmless default instead of making the registrant choose between two
    // options that no longer affect anything (see the "pick" step's JSX,
    // which skips rendering that choice entirely in this case).
    setPricingMode(config?.pricingEnabled === false ? "person" : "");
    setPendingSeatIds([]);
    setPaymentMethod(config?.enabledPaymentMethods?.[0] || "");
    setSubmitError("");
  };

  const cancelWizard = () => {
    navigateTo(MY_REGISTRATIONS_PATH);
  };

  const handleSubmit = async () => {
    if (submitting || !selectedRegistration) return;
    if (pendingSeatIds.length === 0) {
      setSubmitError("Select at least one seat.");
      return;
    }
    const isFree = estimate.totalAmount === 0;
    if (!isFree && !paymentMethod) {
      setSubmitError("Please choose a payment method.");
      return;
    }
    setSubmitting(true);
    setSubmitError("");
    try {
      const purchase = await createMySeatPurchase(selectedRegistration.registrationId, {
        seatIds: pendingSeatIds,
        pricingMode,
        paymentMethod: isFree ? "" : paymentMethod,
      });
      if (!isFree && (paymentMethod === "paypay" || paymentMethod === "square")) {
        const { checkoutUrl } = await checkoutMySeatPurchase(
          selectedRegistration.registrationId,
          purchase.purchaseId,
          paymentMethod
        );
        window.location.href = checkoutUrl;
        return;
      }
      setWizardOpen(false);
      setPendingSeatIds([]);
      // Purchase history/allowance now lives on My Registrations' card —
      // just take the registrant back there to see it confirmed.
      navigateTo(MY_REGISTRATIONS_PATH);
    } catch (err) {
      setSubmitError(err?.message || "Could not submit your seat purchase.");
    } finally {
      setSubmitting(false);
    }
  };

  // ── Seats this registration already holds (view-only) ──
  const myPurchases = (config?.purchases || []).filter(
    (p) => ["pending", "paid", "refund_requested"].includes(p.status) && (p.seatIds || []).length > 0
  );
  const mySeatIds = myPurchases.flatMap((p) => p.seatIds);

  const window_ = config?.seatSelectionWindow || { status: "open" };
  const canStartWizard = Boolean(config?.enabled) && window_.status === "open";

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
          <div className="banner-title">💺 Select Your Seats</div>
          <div className="banner-sub-title">{email ? `Welcome, ${email}` : "Pick and pay for your seats"}</div>
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
              <p className="sub">
                Seat selection is available for paid Celebrity Event or Combo registrations.
                We couldn't find one for your account yet.
              </p>
              <div className="btn-row" style={{ justifyContent: "center" }}>
                <button type="button" className="btn-primary" onClick={() => navigateTo(MY_REGISTRATIONS_PATH)}>
                  Go to My Registrations →
                </button>
              </div>
            </div>
          </section>
        ) : (
          <section className="form-card event-block-seats-page">
            <div className="form-body">
              {registrations.length > 1 ? (
                <div className="field" style={{ marginBottom: "16px" }}>
                  <label htmlFor="hallSeatRegistration">Registration</label>
                  <select
                    id="hallSeatRegistration"
                    value={selectedRegistrationId}
                    onChange={(e) => setSelectedRegistrationId(e.target.value)}
                  >
                    <option value="">Select a registration...</option>
                    {registrations.map((reg) => (
                      <option key={reg.registrationId} value={reg.registrationId}>
                        {reg.name || reg.email} — {reg.registrationOptionLabel || reg.registrationOption}
                      </option>
                    ))}
                  </select>
                </div>
              ) : null}

              {!selectedRegistration ? (
                <p className="field-note">Select a registration above to continue.</p>
              ) : returnNotice ? (
                <p className="success-banner">{returnNotice}</p>
              ) : null}

              {selectedRegistration && !wizardOpen && (
                <>
                  {configLoading ? (
                    <p className="field-note">Loading seat selection…</p>
                  ) : configError ? (
                    <p className="error-banner">{configError}</p>
                  ) : !config?.enabled ? (
                    <p className="field-note">Seat selection isn't open for this event yet. Please check back later.</p>
                  ) : window_.status === "not_started" ? (
                    <p className="field-note">Seat selection opens on {window_.startDate}.</p>
                  ) : window_.status === "ended" ? (
                    <p className="field-note">The seat selection window closed on {window_.endDate}.</p>
                  ) : config?.seatsRemaining <= 0 ? (
                    <p className="field-note">
                      This registration's seat allowance is fully claimed. Manage or cancel existing seats from My
                      Registrations.
                    </p>
                  ) : null}

                  {mySeatIds.length > 0 ? (
                    <div style={{ marginTop: "16px" }}>
                      <div className="section-label">💺 Your Seats</div>
                      <div className="seat-chip-list" style={{ marginBottom: "12px" }}>
                        {mySeatIds.map((seatId) => (
                          <span key={seatId} className="seat-chip">{seatDisplayLabel(seatId)}</span>
                        ))}
                      </div>
                      <SeatMapSelector
                        layout={seatData?.layout || []}
                        bookedSeatIds={seatData?.bookedSeatIds || []}
                        specialSeats={seatData?.specialSeats || {}}
                        walkwayAfterRow={seatData?.walkwayAfterRow ?? 10}
                        mySeatIds={mySeatIds}
                        readOnly
                        loading={seatLoading}
                        loaded={!seatLoading}
                        error={seatError}
                      />
                    </div>
                  ) : null}

                  <div className="btn-row" style={{ marginTop: "16px" }}>
                    <button type="button" className="btn-secondary" onClick={() => navigateTo(MY_REGISTRATIONS_PATH)}>
                      Back to My Registrations
                    </button>
                    {canStartWizard && config?.seatsRemaining > 0 ? (
                      <button type="button" className="btn-primary" onClick={startWizard}>
                        {mySeatIds.length > 0 ? "Select More Seats" : "Select Seats"}
                      </button>
                    ) : null}
                  </div>
                </>
              )}

              {selectedRegistration && wizardOpen && wizardStep === "pick" && (
                <>
                  {pricingEnabled ? (
                    <>
                      <div className="section-label">Choose Your Pricing</div>
                      <p className="field-note">
                        Family pricing charges one flat fee per zone (covering a limited number of seats in that
                        zone); person pricing charges each seat individually. You'll see the exact price as you pick seats.
                      </p>
                      <div className="btn-row" style={{ marginTop: "12px" }}>
                        <button
                          type="button"
                          className={pricingMode === "family" ? "btn-primary" : "btn-secondary"}
                          onClick={() => setPricingMode("family")}
                        >
                          Family Pricing
                        </button>
                        <button
                          type="button"
                          className={pricingMode === "person" ? "btn-primary" : "btn-secondary"}
                          onClick={() => setPricingMode("person")}
                        >
                          Person Pricing
                        </button>
                      </div>
                    </>
                  ) : (
                    <>
                      <div className="section-label">Select Your Seats</div>
                      <p className="field-note">
                        Seat selection is free for this event. You may pick Regular seats freely; a premium zone
                        (Zone 1/2/3) only opens up if you've separately paid to upgrade into it — those seats show a
                        🔒 until then.
                      </p>
                    </>
                  )}

                  <div className="btn-row" style={{ justifyContent: "flex-end", marginTop: "16px", marginBottom: "8px" }}>
                    <button type="button" className="btn-secondary" onClick={loadSeatMap} disabled={seatLoading}>
                      {seatLoading ? "Refreshing…" : "🔄 Refresh"}
                    </button>
                  </div>

                  {config?.seatsAllowed ? (
                    <p className="field-note">
                      You can select up to {config.seatsRemaining} more seat{config.seatsRemaining === 1 ? "" : "s"}
                      {" "}({config.seatsClaimed} of {config.seatsAllowed} already claimed for this registration,
                      one seat per attendee excluding children under 6).
                    </p>
                  ) : null}

                  <SeatMapSelector
                    layout={seatData?.layout || []}
                    bookedSeatIds={seatData?.bookedSeatIds || []}
                    specialSeats={seatData?.specialSeats || {}}
                    zoneAvailable={seatData?.zoneAvailable || null}
                    zonePricing={pricingEnabled ? zonePricing : null}
                    categoryCaps={pricingEnabled ? null : config?.categoryCaps || {}}
                    walkwayAfterRow={seatData?.walkwayAfterRow ?? 10}
                    maxSeats={config?.seatsRemaining}
                    selectedSeatIds={pendingSeatIds}
                    onSelectionChange={setPendingSeatIds}
                    loading={seatLoading}
                    loaded={!seatLoading}
                    error={seatError}
                  />

                  {pricingEnabled ? (
                    <p className="field-note" style={{ marginTop: "8px" }}>
                      Estimated total: <strong>{formatYen(estimate.totalAmount)}</strong>
                    </p>
                  ) : null}

                  <div className="btn-row" style={{ marginTop: "16px" }}>
                    <button type="button" className="btn-secondary" onClick={cancelWizard}>
                      Cancel
                    </button>
                    <button
                      type="button"
                      className="btn-primary"
                      onClick={() => setWizardStep("review")}
                      disabled={!pricingMode || pendingSeatIds.length === 0}
                    >
                      Review Order →
                    </button>
                  </div>
                </>
              )}

              {selectedRegistration && wizardOpen && wizardStep === "review" && (
                <>
                  <div className="section-label">Order Summary</div>
                  <div className="regform-review-grid">
                    {estimate.breakdown.map((row) => (
                      <div key={row.category} style={{ gridColumn: "1 / -1" }}>
                        <div className="btn-row" style={{ justifyContent: "space-between" }}>
                          <span>
                            {row.displayName} — {row.seatIds.length} seat{row.seatIds.length === 1 ? "" : "s"}
                            {row.familySeats > 0 ? ` (${row.familySeats} family` : ""}
                            {row.familySeats > 0 && row.personSeats > 0 ? ` + ${row.personSeats} person)` : row.familySeats > 0 ? ")" : ""}
                          </span>
                          <span>{formatYen(row.subtotal)}</span>
                        </div>
                        <p className="field-note" style={{ margin: "2px 0 10px" }}>{row.seatIds.map(seatDisplayLabel).join(", ")}</p>
                      </div>
                    ))}
                  </div>
                  {estimate.unpriced.length > 0 ? (
                    <p className="error-banner">
                      These seats aren't available for purchase: {estimate.unpriced.join(", ")}. Please go back and
                      deselect them.
                    </p>
                  ) : null}
                  <div className="btn-row" style={{ justifyContent: "space-between", marginTop: "8px", fontWeight: 700 }}>
                    <span>Total</span>
                    <span>{formatYen(estimate.totalAmount)}</span>
                  </div>

                  {estimate.totalAmount === 0 ? (
                    <p className="field-note" style={{ marginTop: "16px" }}>
                      {pricingEnabled
                        ? "These seats are Regular seating — included with your registration at no extra charge."
                        : "Seat selection is free for this event."}{" "}
                      Confirming reserves them immediately; no payment is needed.
                    </p>
                  ) : (
                    <>
                      <div className="field" style={{ marginTop: "16px" }}>
                        <label htmlFor="hallSeatPaymentMethod">Payment Method</label>
                        <select
                          id="hallSeatPaymentMethod"
                          value={paymentMethod}
                          onChange={(e) => setPaymentMethod(e.target.value)}
                        >
                          <option value="">Select a payment method...</option>
                          {(config?.enabledPaymentMethods || []).map((method) => (
                            <option key={method} value={method}>
                              {PAYMENT_METHOD_LABELS[method] || method}
                            </option>
                          ))}
                        </select>
                      </div>
                      {paymentMethod === "bank" ? (
                        <p className="field-note">
                          You'll receive bank transfer instructions by email. Your seats are held while we wait for
                          payment — an admin will confirm once it's received.
                        </p>
                      ) : null}
                    </>
                  )}

                  {submitError ? <p className="error-banner">{submitError}</p> : null}

                  <div className="btn-row" style={{ marginTop: "16px" }}>
                    <button type="button" className="btn-secondary" onClick={() => setWizardStep("pick")} disabled={submitting}>
                      ← Back
                    </button>
                    <button
                      type="button"
                      className="btn-primary"
                      onClick={handleSubmit}
                      disabled={submitting || estimate.unpriced.length > 0}
                    >
                      {submitting ? "Processing…" : estimate.totalAmount === 0 ? "Confirm Seats" : "Confirm and Pay"}
                    </button>
                  </div>
                </>
              )}
            </div>
          </section>
        )}
      </main>
    </>
  );
}
