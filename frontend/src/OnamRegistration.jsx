import { useEffect, useMemo, useState } from "react";
import Navbar from "./Navbar";
// import PartnersSection from "./PartnersSection";
import {
  submitRegistration,
  getMembershipStatus,
  saveMockRegistration,
  checkNpoMembership,
  getSiteConfig,
} from "./api";
import { getRegistrantEmail } from "./registrantAuth";

const HOME_PATH = "/";
const MY_REGISTRATIONS_PATH = "/my/registrations";
const TERMS_PATH = "/terms";
const PRIVACY_PATH = "/privacy";
const CANCELLATION_PATH = "/cancellation";
const SEAT_SELECTION_GUIDE_PATH = "/seat-selection-guide";
const ZONE_UPGRADE_GUIDE_PATH = "/zone-upgrade-guide";

const EMAIL_REGEX = /^[^@\s]+@[^@\s]+\.[^@\s]+$/;
// Require an explicit country code, e.g. +81 90-1234-5678
const PHONE_REGEX = /^\+\d{1,4}[\s-]?\d[\d\s-]{5,18}$/;

const ONAM_EVENT_TYPE = import.meta.env.VITE_EVENT_TYPE_ONAM || "onam_2026";

// Human-friendly event dates, shown in the membership messaging. Onam falls
// inside the current NPO year (covered by an active membership); the Event
// falls in the next NPO year, so it's only discounted after a renewal.
const ONAM_DATE = "September 12, 2026";
const EVENT_DATE = "October 3, 2026";

// The Onam backend endpoint isn't live yet. While mock mode is on, submission
// resolves locally so the full flow is usable. Set VITE_ONAM_MOCK="false" once
// the API accepts the `onam_2026` event type to switch to the real call.
const MOCK_SUBMISSION = import.meta.env.VITE_ONAM_MOCK !== "false";

/**
 * Participation options. Each option carries the base price table the system
 * uses to compute totals automatically — the customer never sees the raw
 * spreadsheet, only a friendly option card. NPO discounts are applied on top of
 * these base prices as a percentage (adults only — see DISCOUNT_PCT).
 *
 * These values MUST stay in sync with lambdas/register/onam_pricing.py (PRICES),
 * which is the authoritative source; the backend recomputes every total.
 */
const OPTIONS = [
  {
    key: "onam",
    label: "Onam (Aavani 2026) Only",
    tagline: "Enjoy the traditional Onam Sadhya and vibrant community cultural programs celebrating the spirit of Onam.",
    icon: "🌼",
    base: { adult: 2500, kids914: 1500, kids68: 1000, kidsUnder6: 0 },
  },
  {
    key: "event",
    label: "40th Anniversary Event-only",
    tagline: "40 Years Together – A Legacy of Community. Join us for a star-studded evening of entertainment, music, and celebration.",
    icon: "🎉",
    base: { adult: 3000, kids914: 2000, kids68: 2000, kidsUnder6: 0 },
  },
  {
    key: "combo",
    label: "Onam (Aavani 2026) & 40th Anniversary Celebration",
    tagline: "Experience both celebrations together.",
    icon: "🌼🎉",
    recommended: true,
    base: { adult: 4950, kids914: 3150, kids68: 2700, kidsUnder6: 0 },
  },
];

// Compact option names for the pricing reference table headers.
const OPTION_SHORT_LABELS = {
  onam: "Onam Only",
  event: "Celebrity Event Only",
  combo: "Onam + Celebrity Event",
};

// The two standalone options, used to compute the à la carte (buy-separately)
// price of the combo so the bundle saving can be shown struck through.
const ONAM_BASE = OPTIONS.find((option) => option.key === "onam");
const EVENT_BASE = OPTIONS.find((option) => option.key === "event");

/**
 * Adults-only percentage discount by membership tier × option. Mirrors
 * DISCOUNT_PCT in lambdas/register/onam_pricing.py.
 *   none   → not a member and not joining (no discount)
 *   member → valid member (Onam 10%, combo 10%, event full price)
 *   full   → joined/renewed for the target year (same rates as member)
 */
const DISCOUNT_PCT = {
  none: { onam: 0, event: 0, combo: 0 },
  member: { onam: 10, event: 0, combo: 10 },
  full: { onam: 10, event: 0, combo: 10 },
};

// Mirrors onam_pricing.resolve_discount_tier() — keep the rules identical.
function resolveDiscountTier({ validNow, coversRenewal, doJoinOrRenew }) {
  if (coversRenewal || doJoinOrRenew) return "full";
  if (validNow) return "member";
  return "none";
}

const CATEGORIES = [
  { key: "adult", label: "Adults", sub: "15 years & above", countField: "adults" },
  { key: "kids914", label: "Kids (9–14)", sub: "9 to 14 years", countField: "kids914" },
  { key: "kids68", label: "Kids (6–8)", sub: "6 to 8 years", countField: "kids68" },
  { key: "kidsUnder6", label: "Kids (under 6)", sub: "Below 6 years", countField: "kidsUnder6" },
];

const STEPS = [
  { n: 1, label: "Registration Type" },
  { n: 2, label: "Participant Details" },
  { n: 3, label: "Ticket Selection" },
  { n: 4, label: "Order Summary" },
  { n: 5, label: "Review & Payment" },
];

const yen = (value) => `¥${Number(value || 0).toLocaleString("en-US")}`;

// One-time fee to join or renew NPO membership, added to the order total.
const NPO_FEE = 1000;

// Turn a "2026-27" membership-year code into a friendly "2026–27" label.
const formatYear = (year) => (year ? String(year).replace("-", "–") : "");

const INITIAL_CONTACT = {
  fullName: "",
  email: "",
  phone: "",
};

const CELEBRITY_EVENT_SEATING_NOTE =
  "No separate seating will be provided for children under 6 years of age for the Celebrity Event.";

const INITIAL_COUNTS = {
  adults: 0,
  kids914: 0,
  kids68: 0,
  kidsUnder6: 0,
};

// Copied from App.jsx/PartnersPage.jsx — same "copied component" convention
// used throughout this app, so every page (not just the public ones) ends
// with the same small business-development footer.
function EventFooter() {
  return (
    <footer className="event-footer">
      Host Your Event With Us
      <br />
      For bookings and inquiries, contact <a href="mailto:hello@cloudnadi.com">hello@cloudnadi.com</a>.
    </footer>
  );
}

export default function OnamRegistration({ navigateTo, onLogout }) {
  const [step, setStep] = useState(1);
  const [regType, setRegType] = useState("combo");
  const [contact, setContact] = useState(() => ({
    ...INITIAL_CONTACT,
    email: getRegistrantEmail() || "",
  }));
  const [counts, setCounts] = useState(INITIAL_COUNTS);
  const [paymentMethod, setPaymentMethod] = useState("credit");
  const [agreeTerms, setAgreeTerms] = useState(false);
  const [agreeCancel, setAgreeCancel] = useState(false);
  const [agreePrivacy, setAgreePrivacy] = useState(false);

  // NPO membership — fetched from the backend for the signed-in email. Drives
  // whether member pricing applies and which upsell (join / renew) we show.
  const [membership, setMembership] = useState(null);
  const [membershipLoading, setMembershipLoading] = useState(true);
  // User's choice to buy/renew NPO membership (+¥1000) during this registration.
  const [joinNpo, setJoinNpo] = useState(false);

  // Additional adults (adults 2…N). Each carries their own email + resolved NPO
  // status so the discount is applied per adult. The array length is kept in
  // sync with (counts.adults - 1). status: "" | "checking" | "member" | "none".
  const [additionalAdults, setAdditionalAdults] = useState([]);

  const [fieldErrors, setFieldErrors] = useState({});
  const [error, setError] = useState("");
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [confirmation, setConfirmation] = useState(null);

  // Per-section availability, fetched from the public site-config endpoint:
  //   ""       → open
  //   "closed" → the admin switched this section off on the event record
  //   "full"   → the section's seat capacity is used up
  // Either value disables every option that draws on that section's seats.
  const [registrationClosed, setRegistrationClosed] = useState(false);
  const [onamStatus, setOnamStatus] = useState("");
  const [celebrityStatus, setCelebrityStatus] = useState("");
  // Kept true until the config fetch settles so we never flash the form before
  // finding out that registrations have closed.
  const [configLoading, setConfigLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;
    getSiteConfig(ONAM_EVENT_TYPE)
      .then(({ config }) => {
        if (cancelled || !config) return;
        const registration = config.registration || {};
        const sadhyaTotal = Number(registration.sadhyaAttendeeTotal) || 0;
        const sadhyaCapacity = Number(registration.sadhyaCapacity) || 0;
        const celebrityTotal = Number(registration.celebrityAttendeeTotal) || 0;
        const celebrityCapacity = Number(registration.celebrityCapacity) || 0;
        // The admin switch wins over capacity so the message names the real reason.
        setOnamStatus(
          registration.onamRegistrationClosed === true
            ? "closed"
            : sadhyaCapacity > 0 && sadhyaTotal >= sadhyaCapacity
              ? "full"
              : ""
        );
        setCelebrityStatus(
          registration.celebrityRegistrationClosed === true
            ? "closed"
            : celebrityCapacity > 0 && celebrityTotal >= celebrityCapacity
              ? "full"
              : ""
        );
        const expiry = String(registration.closesOn || config.onamRegistrationExpiry || "");
        setRegistrationClosed(Boolean(expiry && new Date(`${expiry}T23:59:59`) < new Date()));
      })
      .catch(() => {
        // Non-fatal: leave both options enabled if we can't fetch the count.
      })
      .finally(() => {
        if (!cancelled) setConfigLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  // If one section is unavailable, steer the default selection to the option
  // that's still bookable (both unavailable renders the closed page instead).
  useEffect(() => {
    if (onamStatus && celebrityStatus) {
      return;
    }
    if (onamStatus && regType !== "event") {
      setRegType("event");
    }
    if (celebrityStatus && regType !== "onam") {
      setRegType("onam");
    }
  }, [onamStatus, celebrityStatus, regType]);

  useEffect(() => {
    let cancelled = false;
    setMembershipLoading(true);
    getMembershipStatus()
      .then((status) => {
        if (cancelled) return;
        setMembership(status);
      })
      .catch(() => {
        // Non-fatal: fall back to non-member (normal pricing, offer to join).
        if (!cancelled) setMembership(null);
      })
      .finally(() => {
        if (!cancelled) setMembershipLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  // Keep the additional-adults list length in sync with (adults − 1). The
  // registrant is adult 1, so N adults means N−1 extra email slots.
  useEffect(() => {
    const needed = Math.max(0, (counts.adults || 0) - 1);
    setAdditionalAdults((prev) => {
      if (prev.length === needed) return prev;
      if (prev.length < needed) {
        const additions = Array.from({ length: needed - prev.length }, () => ({
          email: "",
          status: "",
        }));
        return [...prev, ...additions];
      }
      return prev.slice(0, needed);
    });
  }, [counts.adults]);

  const onAdditionalAdultChange = (index, value) => {
    setAdditionalAdults((prev) =>
      prev.map((adult, i) =>
        i === index ? { ...adult, email: value, status: "" } : adult
      )
    );
  };

  // Resolve one additional adult's NPO status on blur (once it looks valid).
  const checkAdditionalAdult = async (index) => {
    const target = additionalAdults[index];
    if (!target) return;
    const email = target.email.trim().toLowerCase();
    if (!email || !EMAIL_REGEX.test(email)) return;

    // A reused email (the registrant's or another adult's) can't earn a second
    // discount — flag it as a duplicate instead of previewing member pricing.
    const isDuplicate =
      email === contact.email.trim().toLowerCase() ||
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
        // Ignore the result if the email was edited while the check was inflight.
        i === index && adult.email.trim().toLowerCase() === email
          ? { ...adult, status: isMember ? "member" : "none" }
          : adult
      )
    );
  };

  const isExistingMember = !!membership?.isMember;
  const canJoin = membership ? membership.canJoin : true;
  const canRenew = !!membership?.canRenew;
  const coversRenewal = !!membership?.coversRenewal;

  // A join/renew only counts if the user opted in AND it's still available.
  const doJoinOrRenew = joinNpo && (canJoin || canRenew);
  // The ¥1000 fee applies only when the user actively joins or renews.
  const npoFeeApplies = doJoinOrRenew;

  // Discount tier drives the adults-only percentage off (mirrors the backend).
  const discountTier = resolveDiscountTier({
    validNow: isExistingMember,
    coversRenewal,
    doJoinOrRenew,
  });
  const activeOption = useMemo(
    () => OPTIONS.find((option) => option.key === regType) || OPTIONS[0],
    [regType]
  );

  // Per-adult discount percentages for the active option. Adult 1 (registrant)
  // uses the resolved order tier; adults 2…N use "member" if their email checked
  // out as a current NPO member, else "none". Mirrors the backend pricing.
  const adultPercents = useMemo(() => {
    const adultCount = counts.adults || 0;
    const percents = [];
    for (let i = 0; i < adultCount; i += 1) {
      const tier =
        i === 0
          ? discountTier
          : additionalAdults[i - 1]?.status === "member"
            ? "member"
            : "none";
      percents.push(DISCOUNT_PCT[tier][activeOption.key] || 0);
    }
    return percents;
  }, [counts.adults, discountTier, additionalAdults, activeOption]);

  const lineItems = useMemo(() => {
    const items = [];

    // Adults: group by discount percentage so each rate is its own line.
    const normalAdult = activeOption.base.adult;
    const byPct = new Map();
    adultPercents.forEach((pct) => byPct.set(pct, (byPct.get(pct) || 0) + 1));
    Array.from(byPct.keys())
      .sort((a, b) => b - a) // highest discount first
      .forEach((pct) => {
        const qty = byPct.get(pct);
        const unit = Math.round((normalAdult * (100 - pct)) / 100);
        items.push({
          key: `adult-${pct}`,
          label: pct > 0 ? `Adults (NPO −${pct}%)` : "Adults",
          qty,
          unit,
          normalUnit: normalAdult,
          discountPercent: pct,
          total: unit * qty,
          savings: (normalAdult - unit) * qty,
        });
      });

    // Kids: never discounted.
    CATEGORIES.filter((category) => category.key !== "adult").forEach((category) => {
      const qty = counts[category.countField] || 0;
      const normalUnit = activeOption.base[category.key];
      items.push({
        key: category.key,
        label: category.label,
        qty,
        unit: normalUnit,
        normalUnit,
        discountPercent: 0,
        total: normalUnit * qty,
        savings: 0,
      });
    });

    return items;
  }, [activeOption, counts, adultPercents]);

  const activeLineItems = lineItems.filter((item) => item.qty > 0);
  const ticketsTotal = lineItems.reduce((sum, item) => sum + item.total, 0);
  const npoFee = npoFeeApplies ? NPO_FEE : 0;
  const grandTotal = ticketsTotal + npoFee;
  const totalSavings = lineItems.reduce((sum, item) => sum + item.savings, 0);
  // NPO pricing is only real when it actually discounted something — Celebrity
  // Event Only never gets a discount, so a member registering for it alone
  // still pays normal pricing and shouldn't see the "NPO Pricing" framing.
  const isNpo = totalSavings > 0;
  const totalAttendees = lineItems.reduce((sum, item) => sum + item.qty, 0);
  // Label for the membership fee line, given whether it's a fresh join or renewal.
  const npoFeeLabel = isExistingMember
    ? `NPO membership renewal (${formatYear(membership?.renewalYear)})`
    : `NPO membership (${formatYear(membership?.renewalYear)})`;

  const changeCount = (field, delta) => {
    setCounts((prev) => ({ ...prev, [field]: Math.max(0, (prev[field] || 0) + delta) }));
  };

  const onContactChange = (event) => {
    const { name, value } = event.target;
    setContact((prev) => ({ ...prev, [name]: value }));
    setFieldErrors((prev) => ({ ...prev, [name]: "" }));
  };

  const validateDetails = () => {
    const errs = {};
    if (!contact.fullName.trim()) errs.fullName = "Full name is required";
    if (!EMAIL_REGEX.test(contact.email.trim())) errs.email = "Enter a valid email address";
    if (!PHONE_REGEX.test(contact.phone.trim()))
      errs.phone = "Use format: +country code and number (e.g. +81 90-1234-5678)";
    if (totalAttendees < 1) errs.counts = "Add at least one attendee";

    // Additional adult emails are optional, but if provided must be valid and unique.
    const seen = new Set([contact.email.trim().toLowerCase()]);
    additionalAdults.forEach((adult, index) => {
      const value = adult.email.trim().toLowerCase();
      if (!value) return; // optional — skip blank entries
      if (!EMAIL_REGEX.test(value)) {
        errs[`adultEmail${index}`] = "Enter a valid email address";
      } else if (seen.has(value)) {
        errs[`adultEmail${index}`] = "This email is already used above";
      } else {
        seen.add(value);
      }
    });

    setFieldErrors(errs);
    return Object.keys(errs).length === 0;
  };

  const goNext = () => {
    setError("");
    if (step === 2 && !validateDetails()) {
      setError("Please fix the highlighted fields to continue.");
      return;
    }
    setStep((prev) => Math.min(5, prev + 1));
    window.scrollTo({ top: 0, behavior: "smooth" });
  };

  const goBack = () => {
    setError("");
    setStep((prev) => Math.max(1, prev - 1));
    window.scrollTo({ top: 0, behavior: "smooth" });
  };

  const handleConfirm = async () => {
    if (!agreeTerms || !agreePrivacy || !agreeCancel) {
      setError("Please accept the terms, privacy, and cancellation policy to continue.");
      return;
    }
    setError("");
    setIsSubmitting(true);

    const membershipAction = doJoinOrRenew ? (isExistingMember ? "renew" : "join") : "none";

    const payload = {
      registrationOption: activeOption.key,
      registrationOptionLabel: activeOption.label,
      pricingTier: discountTier, // 'none' | 'member' | 'full'
      fullName: contact.fullName.trim(),
      email: contact.email.trim().toLowerCase(),
      phone: contact.phone.trim(),
      paymentMethod,
      counts: {
        adults: counts.adults,
        kids914: counts.kids914,
        kids68: counts.kids68,
        kidsUnder6: counts.kidsUnder6,
      },
      // Emails of adults 2…N, in order. The backend re-verifies each against the
      // members table and applies the per-adult discount — client status is
      // never trusted for the charge.
      additionalAdultEmails: additionalAdults.map((adult) =>
        adult.email.trim().toLowerCase()
      ),
      lineItems: activeLineItems.map((item) => ({
        category: item.label,
        quantity: item.qty,
        unitPrice: item.unit,
        total: item.total,
      })),
      // NPO membership context. The backend re-derives all of this from the DB
      // (email → memberid → dated transactions) and never trusts these values;
      // they're sent only to convey the user's join/renew intent.
      membership: {
        wasMember: isExistingMember,
        action: membershipAction, // 'join' | 'renew' | 'none'
        year: doJoinOrRenew ? membership?.renewalYear : null,
        fee: npoFee,
      },
      ticketsAmount: ticketsTotal,
      npoFee,
      discountApplied: totalSavings,
      totalAmount: grandTotal,
    };

    try {
      if (!MOCK_SUBMISSION) {
        const result = await submitRegistration(ONAM_EVENT_TYPE, payload);
        setConfirmation({ ...payload, allocations: result?.allocations || [] });
        return;
      }
      // Mock mode: persist locally so the one-per-member gate and the manage
      // page reflect this registration.
      saveMockRegistration({
        ...payload,
        eventType: ONAM_EVENT_TYPE,
        registrationType: "onam",
        name: payload.fullName,
        attendeeCounts: payload.counts,
        pricingTier: payload.pricingTier,
        lineItems: payload.lineItems,
      });
      setConfirmation(payload);
    } catch (submitError) {
      // Surface backend validation, but still let the user know if it's a
      // connectivity/unknown-event issue rather than silently failing.
      const message =
        submitError?.message || "We couldn't complete your registration. Please try again.";
      setError(message);
    } finally {
      setIsSubmitting(false);
    }
  };

  const nav = (
    <Navbar
      navigateTo={navigateTo}
    />
  );

  if (configLoading) {
    return (
      <>
        {nav}
        <main className="page-wrap onam-reg">
          <header className="event-header">
            <div className="banner-title">Aavani 2026 & 40th Anniversary Celebration</div>
            <div className="sky-bar" />
          </header>
          <section className="form-card">
            <div className="form-body">
              <p className="field-note">Checking your registration status…</p>
            </div>
          </section>
          <EventFooter />
        </main>
      </>
    );
  }

  if (confirmation) {
    return (
      <>
        {nav}
        <main className="page-wrap onam-reg">
          <div className="event-header">
            <div className="banner-title">Registration Confirmed</div>
            <div className="banner-sub-title">Aavani 2026 & 40th Anniversary Celebration</div>
            <div className="sky-bar" />
          </div>
          <section className="form-card success-card visible">
            <div className="success-icon">✓</div>
            <p className="sub">
              Thank you, <strong>{confirmation.fullName}</strong>! Your{" "}
              <strong>{confirmation.registrationOptionLabel}</strong> registration is confirmed.
            </p>
            <p className="sub">
              A confirmation and payment details will be sent to{" "}
              <strong>{confirmation.email}</strong>.
            </p>
            <p className="sub">
              Amount payable: <strong>{yen(confirmation.totalAmount)}</strong>
            </p>
            <div className="btn-row" style={{ justifyContent: "center" }}>
              <button type="button" className="btn-secondary" onClick={() => navigateTo(HOME_PATH)}>
                Back to Home
              </button>
              <button
                type="button"
                className="btn-primary"
                onClick={() => navigateTo(MY_REGISTRATIONS_PATH)}
              >
                View My Registrations →
              </button>
            </div>
          </section>
          <EventFooter />
        </main>
      </>
    );
  }

  // Nothing left to book — either the deadline has passed or both sections are
  // unavailable. Don't show the wizard at all: a fully disabled option grid
  // still let people walk through the remaining steps and press Register.
  if (registrationClosed || (onamStatus && celebrityStatus)) {
    return (
      <>
        {nav}
        <main className="page-wrap onam-reg">
          <header className="event-header">
            <div className="banner-title">Aavani 2026 & 40th Anniversary Celebration</div>
            <div className="sky-bar" />
          </header>
          <section className="form-card">
            <div className="form-body">
              <div className="section-label">Registrations are closed</div>
              <p className="field-note">
                {!registrationClosed && onamStatus === "full" && celebrityStatus === "full"
                  ? "Onam Sadhya and Celebrity Event seating are both full."
                  : "Registrations for this event have closed."}{" "}
                If you already registered, you can still view your booking under My Registrations.
              </p>
              <div className="btn-row onam-nav-row">
                <button
                  type="button"
                  className="btn-secondary"
                  onClick={() => navigateTo(HOME_PATH)}
                >
                  ← Home
                </button>
                <button
                  type="button"
                  className="btn-primary"
                  onClick={() => navigateTo(MY_REGISTRATIONS_PATH)}
                >
                  View My Registrations →
                </button>
              </div>
            </div>
          </section>
          <EventFooter />
        </main>
      </>
    );
  }

  return (
    <>
      {nav}
      <main className="page-wrap onam-reg">
        <header className="event-header">
          <div className="banner-title">Aavani 2026 & 40th Anniversary Celebration</div>
          <div className="banner-sub-title">Join us for a joyful Onam & 40th Anniversary Celebrity Event</div>
          <div className="sky-bar" />
        </header>

        <Stepper current={step} />

        <section className="form-card">
          <div className="form-body">
            {step === 1 && (
              <StepRegistrationType
                regType={regType}
                setRegType={setRegType}
                membership={membership}
                membershipLoading={membershipLoading}
                isExistingMember={isExistingMember}
                canJoin={canJoin}
                canRenew={canRenew}
                joinNpo={joinNpo}
                setJoinNpo={setJoinNpo}
                onamStatus={onamStatus}
                celebrityStatus={celebrityStatus}
              />
            )}

            {step === 2 && (
              <StepParticipantDetails
                contact={contact}
                onContactChange={onContactChange}
                isNpo={isNpo}
                isExistingMember={isExistingMember}
                emailLocked={!!getRegistrantEmail()}
                counts={counts}
                changeCount={changeCount}
                totalAttendees={totalAttendees}
                fieldErrors={fieldErrors}
                additionalAdults={additionalAdults}
                onAdditionalAdultChange={onAdditionalAdultChange}
                checkAdditionalAdult={checkAdditionalAdult}
              />
            )}

            {step === 3 && (
              <StepTicketSelection
                activeOption={activeOption}
                isNpo={isNpo}
                lineItems={lineItems}
                grandTotal={grandTotal}
              />
            )}

            {step === 4 && (
              <StepOrderSummary
                activeOption={activeOption}
                isNpo={isNpo}
                activeLineItems={activeLineItems}
                grandTotal={grandTotal}
                totalSavings={totalSavings}
                npoFee={npoFee}
                npoFeeLabel={npoFeeLabel}
              />
            )}

            {step === 5 && (
              <StepReviewPayment
                activeOption={activeOption}
                isNpo={isNpo}
                isExistingMember={isExistingMember}
                contact={contact}
                activeLineItems={activeLineItems}
                grandTotal={grandTotal}
                totalSavings={totalSavings}
                npoFee={npoFee}
                npoFeeLabel={npoFeeLabel}
                paymentMethod={paymentMethod}
                setPaymentMethod={setPaymentMethod}
                agreeTerms={agreeTerms}
                setAgreeTerms={setAgreeTerms}
                agreeCancel={agreeCancel}
                setAgreeCancel={setAgreeCancel}
                agreePrivacy={agreePrivacy}
                setAgreePrivacy={setAgreePrivacy}
              />
            )}

            {error ? <p className="error-banner">{error}</p> : null}

            <div className="btn-row onam-nav-row">
              {step > 1 ? (
                <button type="button" className="btn-secondary" onClick={goBack}>
                  ← Back
                </button>
              ) : (
                <button
                  type="button"
                  className="btn-secondary"
                  onClick={() => navigateTo(HOME_PATH)}
                >
                  ← Home
                </button>
              )}

              {step < 5 ? (
                <button type="button" className="btn-primary" onClick={goNext}>
                  {step === 4 ? "Review & Continue →" : "Continue →"}
                </button>
              ) : (
                // <button
                //   type="button"
                //   className="btn-primary"
                //   onClick={handleConfirm}
                //   disabled={isSubmitting}
                // >
                //   {isSubmitting ? "Processing…" : `Confirm & Pay ${yen(grandTotal)}`}
                // </button>
                <button
                  type="button"
                  className="btn-primary"
                  onClick={handleConfirm}
                  disabled={isSubmitting}
                >
                  {isSubmitting ? "Processing…" : "Register →"}
                </button>
              )}
            </div>
          </div>
        </section>

        {/* Persistent order summary — visible on every step after the first */}
        {step > 1 && totalAttendees > 0 ? (
          <OrderSummaryCard
            activeOption={activeOption}
            isNpo={isNpo}
            activeLineItems={activeLineItems}
            grandTotal={grandTotal}
            npoFee={npoFee}
            npoFeeLabel={npoFeeLabel}
          />
        ) : null}

        {/* <PartnersSection /> */}
        <EventFooter />
      </main>
    </>
  );
}

function Stepper({ current }) {
  return (
    <div className="onam-stepper">
      {STEPS.map((step, index) => {
        const state =
          step.n < current ? "done" : step.n === current ? "active" : "upcoming";
        return (
          <div className="onam-step" key={step.n}>
            <div className={`onam-step-node ${state}`}>
              {step.n < current ? "✓" : step.n}
            </div>
            <span className={`onam-step-label ${state}`}>{step.label}</span>
            {index < STEPS.length - 1 ? (
              <span className={`onam-step-line ${step.n < current ? "done" : ""}`} />
            ) : null}
          </div>
        );
      })}
    </div>
  );
}

// A static reference of base ticket prices (categories × options), shown on the
// registration-type step so guests can compare prices before choosing. NPO
// discounts (adults only) are applied on top of these base rates later.
function PricingReferenceTable() {
  return (
    <div className="onam-price-ref">
      <table className="onam-price-ref-table">
        <thead>
          <tr>
            <th>Category</th>
            {OPTIONS.map((option) => (
              <th key={option.key}>{OPTION_SHORT_LABELS[option.key]}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {CATEGORIES.map((category) => (
            <tr key={category.key}>
              <th scope="row">
                {category.label}
                <span className="onam-price-ref-sub">{category.sub}</span>
              </th>
              {OPTIONS.map((option) => {
                const price = option.base[category.key];
                if (price === 0) {
                  return <td key={option.key}>Free</td>;
                }
                // For the combo, show the à la carte price (Onam + Event bought
                // separately) struck through so the bundle saving is visible.
                const alaCarte =
                  option.key === "combo"
                    ? ONAM_BASE.base[category.key] + EVENT_BASE.base[category.key]
                    : 0;
                return (
                  <td key={option.key}>
                    {yen(price)}
                    {alaCarte > price ? (
                      <span className="onam-strike">{yen(alaCarte)}</span>
                    ) : null}
                  </td>
                );
              })}
            </tr>
          ))}
        </tbody>
      </table>
      <p className="onam-price-ref-note">
        - Prices per person. NPO members receive a 10% discount on Onam and Combo adult tickets.
      </p>
      <p className="onam-price-ref-note">
        - No separate seating will be provided for children under 6 years of age for the Celebrity Event.
      </p>
    </div>
  );
}

function StepRegistrationType({
  regType,
  setRegType,
  membership,
  membershipLoading,
  isExistingMember,
  canJoin,
  canRenew,
  joinNpo,
  setJoinNpo,
  onamStatus,
  celebrityStatus,
}) {
  return (
    <>
      <div className="section-label">How would you like to register?</div>

      {onamStatus ? (
        <p className="field-note onam-capacity-note">
          Onam Sadhya {onamStatus === "full" ? "seating is full" : "registration is closed"} —
          "Onam Only" and "Onam + Celebrity Event" are unavailable. You can still register for the
          Celebrity Event only.
        </p>
      ) : celebrityStatus ? (
        <p className="field-note onam-capacity-note">
          Celebrity Event {celebrityStatus === "full" ? "seating is full" : "registration is closed"}.
          You can still register for Onam only.
        </p>
      ) : null}

      <div className="onam-option-grid">
        {OPTIONS.map((option) => {
          // Each option draws on the sections it covers, so the combo is blocked
          // by either one. "" when the option is still bookable.
          const blockedBy =
            option.key === "onam"
              ? onamStatus
              : option.key === "event"
                ? celebrityStatus
                : onamStatus || celebrityStatus;
          const disabled = Boolean(blockedBy);
          return (
            <button
              type="button"
              key={option.key}
              className={`onam-option-card ${regType === option.key ? "selected" : ""} ${disabled ? "disabled" : ""}`}
              onClick={() => !disabled && setRegType(option.key)}
              disabled={disabled}
              title={
                blockedBy === "full"
                  ? "Seating is full"
                  : blockedBy
                    ? "Registration is closed"
                    : undefined
              }
            >
              {option.recommended && !disabled ? (
                <span className="onam-option-badge">Best value</span>
              ) : null}
              <span className="onam-option-icon" aria-hidden="true">
                {option.icon}
              </span>
              <span className="onam-option-title">{option.label}</span>
              <span className="onam-option-tagline">{option.tagline}</span>
              {disabled ? (
                <span className="onam-option-badge onam-option-badge-full">
                  {blockedBy === "full" ? "Full" : "Closed"}
                </span>
              ) : null}
            </button>
          );
        })}
      </div>

      <div className="divider" />

      <PricingReferenceTable />

      <div className="divider" />

      <div className="section-label">NPO Membership</div>
      <NpoMembershipPanel
        membership={membership}
        membershipLoading={membershipLoading}
        isExistingMember={isExistingMember}
        canJoin={canJoin}
        canRenew={canRenew}
        joinNpo={joinNpo}
        setJoinNpo={setJoinNpo}
      />
    </>
  );
}

/**
 * Membership status is looked up from the user database after login — the user
 * never self-selects "NPO pricing". Depending on the fetched status we either
 * (a) confirm they're a member (member pricing already applied), (b) offer to
 * join as a member for +¥1000 (which unlocks member pricing for this order), or
 * (c) offer to renew for the coming year for +¥1000.
 */
function NpoMembershipPanel({
  membership,
  membershipLoading,
  isExistingMember,
  canJoin,
  canRenew,
  joinNpo,
  setJoinNpo,
}) {
  if (membershipLoading) {
    return <p className="field-note onam-npo-loading">Checking your membership status…</p>;
  }

  // Already a current member — pricing is applied automatically. If a renewal
  // for the next year is available, offer it as an optional add-on.
  if (isExistingMember) {
    return (
      <>
        <div className="onam-npo-status is-member">
          <span className="onam-npo-badge">NPO Member</span>
          <div className="onam-npo-status-text">
            <strong>You're an active NPO member.</strong>
            <span>
              Your membership covers Onam ({ONAM_DATE}) and Celebrity Event ({EVENT_DATE}) — so a 10% discount is applied to
              your Onam and Combo tickets automatically.
            </span>
          </div>
        </div>

        {canRenew && (
          <>
            <label className="onam-npo-offer">
              <input
                type="checkbox"
                checked={joinNpo}
                onChange={(event) => setJoinNpo(event.target.checked)}
              />
              <span>
                <strong>Renew my NPO membership for {formatYear(membership?.renewalYear)}</strong>
                <small>
                  Add {yen(NPO_FEE)} to extend your membership, keeping it active for the coming year.
                </small>
              </span>
            </label>
          </>
        )}
      </>
    );
  }

  // Not a current member — offer to join. Joining applies member pricing now.
  return (
    <>
      <div className="onam-npo-status not-member">
        <div className="onam-npo-status-text">
          <strong>You're not registered as an NPO member.</strong>
          <span>Join now to unlock member pricing on your tickets.</span>
        </div>
      </div>

      {canJoin ? (
        <label className="onam-npo-offer">
          <input
            type="checkbox"
            checked={joinNpo}
            onChange={(event) => setJoinNpo(event.target.checked)}
          />
          <span>
            <strong>Register me as an NPO member for {formatYear(membership?.renewalYear)}</strong>
            <small>
              Add {yen(NPO_FEE)} — get 10% off both Onam and Combo tickets plus NPO benefits for the year.
            </small>
          </span>
        </label>
      ) : null}
    </>
  );
}

function CounterRow({ label, sub, value, onDec, onInc }) {
  return (
    <div className="attendee-row">
      <div className="attendee-label">
        {label}
        <small>{sub}</small>
      </div>
      <div className="attendee-count">
        <button type="button" className="counter-btn" onClick={onDec}>
          −
        </button>
        <span className="counter-val">{value}</span>
        <button type="button" className="counter-btn" onClick={onInc}>
          +
        </button>
      </div>
    </div>
  );
}

function StepParticipantDetails({
  contact,
  onContactChange,
  isNpo,
  isExistingMember,
  emailLocked,
  counts,
  changeCount,
  totalAttendees,
  fieldErrors,
  additionalAdults,
  onAdditionalAdultChange,
  checkAdditionalAdult,
}) {
  return (
    <>
      <div className="section-label">Primary Contact</div>
      <div className="field-row">
        <div className="field">
          <label htmlFor="fullName">Full Name *</label>
          <input
            id="fullName"
            name="fullName"
            type="text"
            placeholder="Enter your full name"
            className={fieldErrors.fullName ? "input-invalid" : ""}
            value={contact.fullName}
            onChange={onContactChange}
          />
          {fieldErrors.fullName ? (
            <span className="field-error">{fieldErrors.fullName}</span>
          ) : null}
        </div>
        <div className="field">
          <label htmlFor="email">Email Address *</label>
          <input
            id="email"
            name="email"
            type="email"
            placeholder="your@email.com"
            className={fieldErrors.email ? "input-invalid" : ""}
            value={contact.email}
            onChange={onContactChange}
            readOnly={emailLocked}
          />
          {emailLocked ? (
            <span className="field-note">Signed in as this address.</span>
          ) : null}
          {fieldErrors.email ? <span className="field-error">{fieldErrors.email}</span> : null}
        </div>
      </div>

      <div className="field-row full">
        <div className="field">
          <label htmlFor="phone">Phone Number *</label>
          <input
            id="phone"
            name="phone"
            type="tel"
            placeholder="+81 90-XXXX-XXXX"
            className={fieldErrors.phone ? "input-invalid" : ""}
            value={contact.phone}
            onChange={onContactChange}
          />
          {fieldErrors.phone ? <span className="field-error">{fieldErrors.phone}</span> : null}
        </div>
      </div>

      <div className="divider" />

      <div className="section-label">Who is attending?</div>
      <div className="onam-info-callout" role="note" aria-label="Celebrity Event seating note">
        <strong>Seating note:</strong> {CELEBRITY_EVENT_SEATING_NOTE}
      </div>
      <div className="attendee-grid">
        <div className="attendee-grid-header">
          <div>Category</div>
          <div>Count</div>
        </div>
        <CounterRow
          label="Adults"
          sub="15 years & above"
          value={counts.adults}
          onDec={() => changeCount("adults", -1)}
          onInc={() => changeCount("adults", 1)}
        />
        <CounterRow
          label="Kids (9–14)"
          sub="9 to 14 years"
          value={counts.kids914}
          onDec={() => changeCount("kids914", -1)}
          onInc={() => changeCount("kids914", 1)}
        />
        <CounterRow
          label="Kids (6–8)"
          sub="6 to 8 years"
          value={counts.kids68}
          onDec={() => changeCount("kids68", -1)}
          onInc={() => changeCount("kids68", 1)}
        />
        <CounterRow
          label="Kids (under 6)"
          sub="Below 6 years"
          value={counts.kidsUnder6}
          onDec={() => changeCount("kidsUnder6", -1)}
          onInc={() => changeCount("kidsUnder6", 1)}
        />
        <div className="total-row">
          <div className="total-label">Total Attendees</div>
          <div className="total-val">{totalAttendees}</div>
        </div>
      </div>
      {fieldErrors.counts ? (
        <p className="field-error field-error-block">{fieldErrors.counts}</p>
      ) : null}

      {additionalAdults.length > 0 ? (
        <>
          <div className="divider" />
          <div className="section-label">Additional Adults</div>
          <p className="field-note" style={{ marginBottom: "14px" }}>
            Optionally enter each additional adult's email. If they're an active NPO member, the
            10% member discount is applied to their ticket automatically for Onam and Combo tickets.
          </p>
          <div className="onam-adult-emails">
            {additionalAdults.map((adult, index) => {
              const errorKey = `adultEmail${index}`;
              return (
                <div className="field" key={index}>
                  <label htmlFor={errorKey}>Adult {index + 2} email</label>
                  <input
                    id={errorKey}
                    type="email"
                    placeholder="adult@email.com (optional)"
                    className={fieldErrors[errorKey] ? "input-invalid" : ""}
                    value={adult.email}
                    onChange={(event) => onAdditionalAdultChange(index, event.target.value)}
                    onBlur={() => checkAdditionalAdult(index)}
                  />
                  {fieldErrors[errorKey] ? (
                    <span className="field-error">{fieldErrors[errorKey]}</span>
                  ) : adult.status === "checking" ? (
                    <span className="field-note">Checking membership…</span>
                  ) : adult.status === "duplicate" ? (
                    <span className="field-error">
                      This email is already used by another attendee.
                    </span>
                  ) : adult.status === "member" ? (
                    <span className="field-note onam-adult-member">
                      ✓ NPO member — 10% discount applies
                    </span>
                  ) : adult.status === "none" ? (
                    <span className="field-note">Not an NPO member — standard price.</span>
                  ) : null}
                </div>
              );
            })}
          </div>
        </>
      ) : null}
    </>
  );
}

function StepTicketSelection({ activeOption, isNpo, lineItems, grandTotal }) {
  return (
    <>
      <div className="section-label">
        Ticket Selection — {activeOption.label} ({isNpo ? "NPO" : "Normal"})
      </div>
      <p className="field-note" style={{ marginBottom: "18px" }}>
        Prices are calculated automatically from your participation option and attendee counts.
      </p>

      <div className="onam-price-table">
        <div className="onam-price-head">
          <div>Category</div>
          <div>Qty</div>
          <div>Unit Price</div>
          <div>Total</div>
        </div>
        {lineItems.map((item) => (
          <div className={`onam-price-row ${item.qty === 0 ? "muted" : ""}`} key={item.key}>
            <div>{item.label}</div>
            <div>{item.qty}</div>
            <div>
              {yen(item.unit)}
              {item.savings > 0 ? (
                <span className="onam-strike">{yen(item.normalUnit)}</span>
              ) : null}
            </div>
            <div>{yen(item.total)}</div>
          </div>
        ))}
        <div className="onam-price-foot">
          <div>Subtotal</div>
          <div />
          <div />
          <div>{yen(grandTotal)}</div>
        </div>
      </div>
    </>
  );
}

function StepOrderSummary({
  activeOption,
  isNpo,
  activeLineItems,
  grandTotal,
  totalSavings,
  npoFee,
  npoFeeLabel,
}) {
  return (
    <>
      <div className="section-label">Order Summary</div>
      <div className="onam-summary-meta">
        <span className="onam-chip">{activeOption.label}</span>
        <span className={`onam-chip ${isNpo ? "npo" : ""}`}>
          {isNpo ? "NPO Pricing" : "Normal Pricing"}
        </span>
      </div>

      <div className="onam-price-table">
        <div className="onam-price-head">
          <div>Item</div>
          <div>Qty</div>
          <div>Price</div>
          <div>Total</div>
        </div>
        {activeLineItems.map((item) => (
          <div className="onam-price-row" key={item.key}>
            <div>{item.label}</div>
            <div>{item.qty}</div>
            <div>{yen(item.unit)}</div>
            <div>{yen(item.total)}</div>
          </div>
        ))}
        {totalSavings > 0 ? (
          <div className="onam-price-row discount">
            <div>NPO discount applied</div>
            <div />
            <div />
            <div>−{yen(totalSavings)}</div>
          </div>
        ) : null}
        {npoFee > 0 ? (
          <div className="onam-price-row">
            <div>{npoFeeLabel}</div>
            <div>1</div>
            <div>{yen(npoFee)}</div>
            <div>{yen(npoFee)}</div>
          </div>
        ) : null}
        <div className="onam-price-foot">
          <div>Total</div>
          <div />
          <div />
          <div>{yen(grandTotal)}</div>
        </div>
      </div>
    </>
  );
}

function StepReviewPayment({
  activeOption,
  isNpo,
  isExistingMember,
  contact,
  activeLineItems,
  grandTotal,
  totalSavings,
  npoFee,
  npoFeeLabel,
  paymentMethod,
  setPaymentMethod,
  agreeTerms,
  setAgreeTerms,
  agreeCancel,
  setAgreeCancel,
  agreePrivacy,
  setAgreePrivacy,
}) {
  const membershipNote = isExistingMember
    ? "Active member"
    : npoFee > 0
      ? "Joining now"
      : "Not a member";
  return (
    <>
      <div className="section-label">Review Your Registration</div>

      <div className="onam-review-grid">
        <ReviewRow label="Registration Type" value={activeOption.label} />
        <ReviewRow label="Pricing" value={isNpo ? "NPO (member)" : "Normal"} />
        <ReviewRow label="NPO Membership" value={membershipNote} />
        <ReviewRow label="Name" value={contact.fullName} />
        <ReviewRow label="Email" value={contact.email} />
        <ReviewRow label="Phone" value={contact.phone} />
      </div>

      <div className="onam-price-table" style={{ marginTop: "18px" }}>
        <div className="onam-price-head">
          <div>Item</div>
          <div>Qty</div>
          <div>Price</div>
          <div>Total</div>
        </div>
        {activeLineItems.map((item) => (
          <div className="onam-price-row" key={item.key}>
            <div>{item.label}</div>
            <div>{item.qty}</div>
            <div>{yen(item.unit)}</div>
            <div>{yen(item.total)}</div>
          </div>
        ))}
        {totalSavings > 0 ? (
          <div className="onam-price-row discount">
            <div>Discount applied</div>
            <div />
            <div />
            <div>−{yen(totalSavings)}</div>
          </div>
        ) : null}
        {npoFee > 0 ? (
          <div className="onam-price-row">
            <div>{npoFeeLabel}</div>
            <div>1</div>
            <div>{yen(npoFee)}</div>
            <div>{yen(npoFee)}</div>
          </div>
        ) : null}
        <div className="onam-price-foot">
          <div>Total amount</div>
          <div />
          <div />
          <div>{yen(grandTotal)}</div>
        </div>
      </div>

      <div className="divider" />

      <div className="section-label">Payment Method</div>
      <div className="onam-toggle-group">
        <button
          type="button"
          className={`onam-toggle-pill ${paymentMethod === "credit" ? "selected" : ""}`}
          onClick={() => setPaymentMethod("credit")}
        >
          Credit Card
        </button>
        <button
          type="button"
          className={`onam-toggle-pill ${paymentMethod === "paypay" ? "selected" : ""}`}
          onClick={() => setPaymentMethod("paypay")}
        >
          PayPay
        </button>
        <button
          type="button"
          className={`onam-toggle-pill ${paymentMethod === "bank" ? "selected" : ""}`}
          onClick={() => setPaymentMethod("bank")}
        >
          Bank Transfer
        </button>
      </div>

      <div className="onam-consents">
        <label className="onam-check">
          <input
            type="checkbox"
            checked={agreeTerms}
            onChange={(event) => setAgreeTerms(event.target.checked)}
          />
          <span>
            I accept the{" "}
            <a href={TERMS_PATH} target="_blank" rel="noopener noreferrer">terms &amp; conditions</a>{" "}
            of the event.
          </span>
        </label>
        <label className="onam-check">
          <input
            type="checkbox"
            checked={agreeCancel}
            onChange={(event) => setAgreeCancel(event.target.checked)}
          />
          <span>
            I accept the{" "}
            <a href={CANCELLATION_PATH} target="_blank" rel="noopener noreferrer">cancellation policy</a>{" "}
            of the event.
          </span>
        </label>
        <label className="onam-check">
          <input
            type="checkbox"
            checked={agreePrivacy}
            onChange={(event) => setAgreePrivacy(event.target.checked)}
          />
          <span>
            I consent to my details being used to process this registration, per the{" "}
            <a href={PRIVACY_PATH} target="_blank" rel="noopener noreferrer">privacy policy</a>.
          </span>
        </label>
      </div>
    </>
  );
}

function ReviewRow({ label, value }) {
  return (
    <div className="onam-review-row">
      <span className="onam-review-label">{label}</span>
      <span className="onam-review-value">{value || "—"}</span>
    </div>
  );
}

function OrderSummaryCard({ activeOption, isNpo, activeLineItems, grandTotal, npoFee, npoFeeLabel }) {
  return (
    <aside className="onam-summary-card">
      <div className="onam-summary-title">Registration Summary</div>
      <div className="onam-summary-sub">
        {activeOption.label} · {isNpo ? "NPO" : "Normal"}
      </div>
      <div className="onam-summary-lines">
        {activeLineItems.length === 0 ? (
          <div className="onam-summary-empty">No attendees selected yet.</div>
        ) : (
          activeLineItems.map((item) => (
            <div className="onam-summary-line" key={item.key}>
              <span>
                {item.label} × {item.qty}
              </span>
              <span>{yen(item.total)}</span>
            </div>
          ))
        )}
        {npoFee > 0 ? (
          <div className="onam-summary-line">
            <span>{npoFeeLabel}</span>
            <span>{yen(npoFee)}</span>
          </div>
        ) : null}
      </div>
      <div className="onam-summary-total">
        <span>Total</span>
        <span>{yen(grandTotal)}</span>
      </div>
    </aside>
  );
}
