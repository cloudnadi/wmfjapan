import { useCallback, useEffect, useRef, useState } from "react";
import Navbar from "./Navbar";
import {
  applyNpoMembership,
  checkSpouseMembership,
  getMembershipStatus,
  reportNpoPayment,
  retryNpoPayment,
  saveAgmRegistration,
} from "./api";
import { getRegistrantEmail } from "./registrantAuth";
import { AgmAttendeeFields, AgmDetails, EMPTY_AGM_COUNTS, YOUNG_GROUPS, useAdultChecks } from "./AgmFields";
import {
  ProfileRow,
  ProfileSection,
  Section,
  TransactionsSection,
  formatDate,
  formatYear,
  formatYen,
} from "./memberShared";

const METHOD_ICONS = { square: "💳", paypay: "📱", bank: "🏦" };
const EMAIL_REGEX = /^[^@\s]+@[^@\s]+\.[^@\s]+$/;
// How long the team takes to confirm a PayPay / bank payment by hand.
const CONFIRMATION_DAYS = 3;

// Turn bare URLs in admin-written instructions into short tappable links.
function linkify(text) {
  return String(text)
    .split(/(https?:\/\/[^\s)]+)/g)
    .map((part, index) => {
      if (!/^https?:\/\//.test(part)) return part;
      let label = part;
      try {
        const url = new URL(part);
        label = `${url.hostname}${url.pathname.length > 1 ? "/…" : ""}`;
      } catch {
        // keep the raw text
      }
      return (
        <a key={index} href={part} target="_blank" rel="noopener noreferrer">
          {label}
        </a>
      );
    });
}

/** Pending-payment card: what is owed, how to pay, and when membership starts. */
function PendingPayment({ pending, year, onRefresh, returning = false }) {
  const method = pending.paymentMethod || {};
  const [retrying, setRetrying] = useState(false);
  const [retryError, setRetryError] = useState("");
  // PayPay / bank transfer can't be seen by the system, so the member tells us
  // they've paid; the committee still confirms it.
  const manual = method.key !== "square" && !pending.paidByOther;
  const reported = manual && !!pending.reportedAt;
  const [reporting, setReporting] = useState(false);
  const [reportError, setReportError] = useState("");
  const setReported = async (value) => {
    setReporting(true);
    setReportError("");
    try {
      await reportNpoPayment(value);
      await onRefresh({ fresh: true });
    } catch (err) {
      setReportError(err.message || "Could not save that. Please try again.");
    }
    setReporting(false);
  };

  // Coming back from Square's checkout: the payment is usually confirmed a few
  // seconds later, so say so (and keep checking) instead of offering "Pay now" again.
  const isCard = method.key === "square" && !pending.paidByOther && !pending.attemptFailed;
  const confirming = returning && isCard;
  const [giveUp, setGiveUp] = useState(false);
  const [checkRound, setCheckRound] = useState(0);

  useEffect(() => {
    if (!confirming) return undefined;
    setGiveUp(false);
    let tries = 0;
    const timer = setInterval(async () => {
      tries += 1;
      try {
        await onRefresh({ fresh: true });
      } catch {
        // keep trying; the page-level error handling covers a real outage
      }
      if (tries >= 10) {
        clearInterval(timer);
        setGiveUp(true);
      }
    }, 4000);
    return () => clearInterval(timer);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [confirming, checkRound]);

  // Paid in another tab / window? Re-check as soon as this one is looked at again.
  useEffect(() => {
    if (!isCard) return undefined;
    const onVisible = () => {
      if (document.visibilityState === "visible") onRefresh({ fresh: true }).catch(() => {});
    };
    document.addEventListener("visibilitychange", onVisible);
    return () => document.removeEventListener("visibilitychange", onVisible);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [isCard]);

  // A failed card attempt: open a fresh Square checkout for the same request.
  const retry = async () => {
    setRetrying(true);
    setRetryError("");
    try {
      const result = await retryNpoPayment();
      if (result.alreadyPaid) {
        await onRefresh();
      } else if (result.checkoutUrl) {
        window.location.href = result.checkoutUrl;
        return;
      }
    } catch (err) {
      setRetryError(err.message || "We couldn't restart the payment. Please try again.");
    }
    setRetrying(false);
  };

  if (pending.paidByOther) {
    return (
      <div className="npo-pay-card">
        <div className="npo-pay-card-title">Your membership is on its way 🎉</div>
        <p className="npo-pay-card-text">
          <strong>{pending.paidBy}</strong> has signed you up for {year}. It starts as soon as their payment is
          confirmed.
        </p>
      </div>
    );
  }

  // One short heading + one line of context; the action comes next and the
  // admin's instructions (which can be long) sit underneath it.
  const forSpouse = pending.kind === "spouse-payment";
  const title = `Complete your ¥${Number(pending.fee).toLocaleString()} payment`;
  const detail = [
    `${forSpouse ? "Spouse's " : ""}${year} membership`,
    method.label,
    method.key === "square" ? "starts as soon as it's paid" : `confirmed within ${CONFIRMATION_DAYS} days`,
  ]
    .filter(Boolean)
    .join(" · ");

  if (reported) {
    return (
      <div className="npo-pay-card" role="status">
        <div className="npo-pay-card-title">Thanks — we're confirming your payment</div>
        <p className="npo-pay-card-text">
          You told us you paid ¥{Number(pending.fee).toLocaleString()} via {method.label} on{" "}
          {formatDate(pending.reportedAt.slice(0, 10))}. We'll confirm it within {CONFIRMATION_DAYS} days and
          email you. Nothing more to do.
        </p>
        {reportError ? <p className="error-banner" style={{ margin: 0 }}>{reportError}</p> : null}
        <button type="button" className="npo-link-button" onClick={() => setReported(false)} disabled={reporting}>
          {reporting ? "Please wait…" : "I haven't paid yet"}
        </button>
      </div>
    );
  }

  if (confirming) {
    return (
      <div className="npo-pay-card" role="status">
        <div className="npo-pay-card-title">
          {giveUp ? "Still confirming your payment" : `Confirming your ¥${Number(pending.fee).toLocaleString()} payment…`}
        </div>
        <p className="npo-pay-card-text">
          {giveUp
            ? "We haven't heard back from Square yet. If you completed the payment, please don't pay again — it will be confirmed shortly and we'll email you."
            : "Thank you! We're waiting for Square to confirm your payment. This usually takes a few seconds."}
        </p>
        {giveUp ? (
          <button type="button" className="npo-pay-button" onClick={() => setCheckRound((n) => n + 1)}>
            Check again
          </button>
        ) : null}
      </div>
    );
  }

  return (
    <div className="npo-pay-card">
      <div className="npo-pay-card-title">{title}</div>
      <p className="npo-pay-card-text">{detail}</p>
      {pending.attemptFailed ? (
        <>
          <p className="error-banner" role="alert" style={{ margin: 0 }}>
            Your card payment didn't go through. You can try again with the button below.
          </p>
          {retryError ? <p className="error-banner" style={{ margin: 0 }}>{retryError}</p> : null}
          <button type="button" className="npo-pay-button" onClick={retry} disabled={retrying}>
            {retrying ? "Please wait…" : "Retry Payment →"}
          </button>
        </>
      ) : method.url ? (
        <a className="npo-pay-button" href={method.url} target="_blank" rel="noopener noreferrer">
          Pay now →
        </a>
      ) : null}
      {method.instructions ? <p className="npo-pay-card-instructions">{linkify(method.instructions)}</p> : null}
      {manual ? (
        <>
          {reportError ? <p className="error-banner" style={{ margin: 0 }}>{reportError}</p> : null}
          <button type="button" className="btn-secondary npo-paid-button" onClick={() => setReported(true)} disabled={reporting}>
            {reporting ? "Please wait…" : "I've completed the payment ✓"}
          </button>
        </>
      ) : null}
    </div>
  );
}

/** Spouse email with live validation: is it a new or existing member, and what does it cost? */
function SpouseField({ enabled, onToggle, email, onEmail, check }) {
  return (
    <div className="mypage-option">
      <label className="checkbox-inline">
        <input type="checkbox" checked={enabled} onChange={(event) => onToggle(event.target.checked)} /> Also{" "}
        join / renew my spouse's NPO membership
      </label>
      {enabled ? (
        <div className="field" style={{ marginTop: 12 }}>
          <label htmlFor="spouse-email">Spouse's email address</label>
          <input
            id="spouse-email"
            type="email"
            inputMode="email"
            autoComplete="off"
            placeholder="spouse@email.com"
            value={email}
            className={check.state === "error" ? "input-invalid" : ""}
            onChange={(event) => onEmail(event.target.value)}
          />
          {check.state === "checking" ? <span className="field-note">Checking membership…</span> : null}
          {check.state === "ok" ? (
            <span className="field-note mypage-ok">
              ✓ {check.status === "renew" ? "Existing member — renewal" : "New member — will be added"} (
              {formatYen(check.fee)})
            </span>
          ) : null}
          {check.state === "error" ? (
            <span className="field-note mypage-error">
              {check.message}
              {check.registerUrl ? (
                <>
                  {" "}
                  <a href={check.registerUrl} target="_blank" rel="noopener noreferrer">
                    Register as a member →
                  </a>
                </>
              ) : null}
            </span>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}

// "In person · 2 adults, 1 child · payment pending for 1"
const SINGULAR = { teens: "teenager", kids: "child", preschoolers: "preschooler", toddlers: "toddler" };
function agmSummaryLine(registration) {
  const plural = (n, one, many) => `${n} ${n === 1 ? one : many}`;
  const groups = [
    plural(registration.adults, "adult", "adults"),
    ...YOUNG_GROUPS.filter(({ key }) => Number(registration[key]) > 0).map(({ key, label }) =>
      plural(registration[key], SINGULAR[key] || label.toLowerCase(), label.toLowerCase())
    ),
  ];
  const pending = (registration.adultAttendees || []).filter((a) => a.status === "pending").length;
  return [
    registration.attendance === "remote" ? "Remote" : "In person",
    groups.join(", "),
    pending ? `payment pending for ${pending}` : "",
  ]
    .filter(Boolean)
    .join(" · ");
}

/** Standalone AGM registration for members: details + register / update / cancel. */
function AgmCard({ agm, onSaved }) {
  const registration = agm.registration;
  const [editing, setEditing] = useState(false);
  const [counts, setCounts] = useState(EMPTY_AGM_COUNTS);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const { checks, allValid } = useAdultChecks(counts.adultEmails);

  const startEditing = () => {
    setCounts(
      registration
        ? {
            ...EMPTY_AGM_COUNTS,
            attendance: registration.attendance || "in_person",
            adultEmails: (registration.adultAttendees || [])
              .map((a) => a.email)
              .filter((e) => e && e !== getRegistrantEmail()),
            teens: registration.teens || 0,
            kids: registration.kids || 0,
            preschoolers: registration.preschoolers || 0,
            toddlers: registration.toddlers || 0,
          }
        : EMPTY_AGM_COUNTS
    );
    setError("");
    setEditing(true);
  };

  const run = async (payload) => {
    setBusy(true);
    setError("");
    try {
      await saveAgmRegistration(payload);
      setEditing(false);
      await onSaved();
    } catch (err) {
      setError(err.message || "Something went wrong. Please try again.");
    } finally {
      setBusy(false);
    }
  };

  const save = () => {
    if (counts.adultEmails.some((e) => !e.trim())) {
      setError("Enter an email for each additional adult, or remove the empty row.");
      return;
    }
    if (!allValid) {
      setError("Every additional adult must be a valid NPO member. Check the emails above.");
      return;
    }
    run({ ...counts, adultEmails: counts.adultEmails.map((e) => e.trim().toLowerCase()) });
  };

  return (
    <Section id="agm" title="Annual General Meeting">
      <AgmDetails agm={agm} />

      {!agm.eligible ? (
        <p className="field-note">{agm.eligibilityMessage || "AGM registration is for NPO members whose membership covers the AGM date — join or renew above to register."}</p>
      ) : editing ? (
        <>
          <div className="section-label">Who is attending?</div>
          <AgmAttendeeFields value={counts} onChange={setCounts} disabled={busy} checks={checks} />
          {error ? <p className="error-banner">{error}</p> : null}
          <div className="btn-row">
            <button type="button" className="btn-primary" onClick={save} disabled={busy}>
              {busy ? "Saving…" : registration ? "Update registration" : "Register for the AGM →"}
            </button>
            <button type="button" className="btn-secondary" onClick={() => setEditing(false)} disabled={busy}>
              Cancel
            </button>
          </div>
        </>
      ) : registration ? (
        <>
          <div className="success-banner">
            <strong>You're registered for the AGM ✓</strong>
            <p className="agm-summary-line">{agmSummaryLine(registration)}</p>
            {(registration.adultAttendees || []).length > 1 ? (
              <p className="agm-summary-names">
                {registration.adultAttendees.map((a) => a.name || a.email).join(", ")}
              </p>
            ) : null}
          </div>
          {error ? <p className="error-banner">{error}</p> : null}
          <div className="btn-row">
            <button type="button" className="btn-secondary" onClick={startEditing} disabled={busy}>
              Change
            </button>
            <button type="button" className="btn-secondary" onClick={() => run({ cancel: true })} disabled={busy}>
              Cancel registration
            </button>
          </div>
        </>
      ) : (
        <div className="btn-row">
          <button type="button" className="btn-primary" onClick={startEditing}>
            Register for the AGM →
          </button>
        </div>
      )}
    </Section>
  );
}

/**
 * Member → WMF Japan Foundation Membership (/member/membership): profile,
 * NPO membership status, join / renew (optionally with a spouse and AGM
 * attendance), AGM registration and transaction history. Payment methods and AGM details come from the admin
 * dashboard.
 */
export default function MemberMembership({ navigateTo }) {
  const email = getRegistrantEmail();
  const [status, setStatus] = useState(null);
  const [error, setError] = useState("");
  const [method, setMethod] = useState("");
  const [isBusy, setIsBusy] = useState(false);

  const [spouseOn, setSpouseOn] = useState(false);
  const [spouseEmail, setSpouseEmail] = useState("");
  const [spouseCheck, setSpouseCheck] = useState({ state: "idle" });
  const spouseRequest = useRef(0);

  const [agmOn, setAgmOn] = useState(false);
  const [agreeTerms, setAgreeTerms] = useState(false);
  const [agreeCancel, setAgreeCancel] = useState(false);
  const [agreePrivacy, setAgreePrivacy] = useState(false);
  const [agmCounts, setAgmCounts] = useState(EMPTY_AGM_COUNTS);

  // Square sends the member back here with ?payment=return once checkout is done.
  const [returning, setReturning] = useState(
    () => new URLSearchParams(window.location.search).get("payment") === "return"
  );

  const load = useCallback(async (options) => {
    const data = await getMembershipStatus({ fresh: options?.fresh === true });
    setStatus(data);
    const methods = data.paymentMethods || [];
    setMethod((current) =>
      methods.some((m) => m.key === current) ? current : methods.length === 1 ? methods[0].key : ""
    );
    return data;
  }, []);

  useEffect(() => {
    load().catch((err) => setError(err.message || "Could not load your membership."));
  }, [load]);

  // Once the payment is confirmed (nothing pending any more), drop ?payment=return.
  useEffect(() => {
    if (returning && status && !status.pendingPayment) {
      window.history.replaceState({}, "", window.location.pathname + window.location.hash);
      setReturning(false);
    }
  }, [returning, status]);

  // Menu links like /mypage#membership scroll to their section once it renders.
  useEffect(() => {
    if (!status) return;
    const id = window.location.hash.replace("#", "");
    if (id) document.getElementById(id)?.scrollIntoView({ behavior: "smooth" });
  }, [status]);

  // Validate the spouse's email shortly after typing stops.
  useEffect(() => {
    const value = spouseEmail.trim().toLowerCase();
    const requestId = ++spouseRequest.current;
    if (!spouseOn || !value) {
      setSpouseCheck({ state: "idle" });
      return undefined;
    }
    if (!EMAIL_REGEX.test(value)) {
      setSpouseCheck({ state: "error", message: "Enter a valid email address for your spouse." });
      return undefined;
    }
    setSpouseCheck({ state: "checking" });
    const timer = setTimeout(async () => {
      try {
        const result = await checkSpouseMembership(value);
        if (requestId !== spouseRequest.current) return;
        setSpouseCheck(
          result.valid
            ? { state: "ok", status: result.status, fee: result.fee }
            : {
                state: "error",
                message: result.message || "That email can't be added.",
                registerUrl: result.registerUrl || "",
              }
        );
      } catch (err) {
        if (requestId === spouseRequest.current) setSpouseCheck({ state: "error", message: err.message });
      }
    }, 450);
    return () => clearTimeout(timer);
  }, [spouseOn, spouseEmail]);

  const methods = status?.paymentMethods || [];
  const profile = status?.profile || {};
  const transactions = status?.transactions || [];
  const year = formatYear(status?.renewalYear);
  const covered = !!status?.coversRenewal;
  const isMember = !!status?.isMember;
  const pending = status?.pendingPayment || null;
  const agm = status?.agm?.enabled ? status.agm : null;
  const fee = Number(status?.fee ?? 0);

  // Order summary — mirrors what the server will charge.
  const spouseCounted = spouseOn && spouseCheck.state === "ok";
  const lines = [
    ...(covered ? [] : [{ label: isMember ? "Your membership (renewal)" : "Your membership (new)", amount: fee }]),
    ...(spouseCounted ? [{ label: "Spouse membership", amount: Number(spouseCheck.fee ?? fee) }] : []),
  ];
  const total = lines.reduce((sum, line) => sum + line.amount, 0);
  const spouseKey = spouseCounted ? spouseEmail.trim().toLowerCase() : "";
  const lockedAdults = spouseKey ? [spouseKey] : [];
  const { checks: agmChecks, allValid: agmAdultsValid } = useAdultChecks(agmCounts.adultEmails, lockedAdults);
  const agmReady = !agmOn || agmAdultsValid;
  const canSubmit = lines.length > 0 && !isBusy && (!spouseOn || spouseCounted) && agmReady;
  const agreedAll = agreeTerms && agreeCancel && agreePrivacy;

  // A spouse ordered together with the membership is added to the AGM as an
  // additional adult — once. The member can remove them again (they stay in the
  // membership order); changing or unticking the spouse takes the auto-added row out.
  const autoAddedSpouse = useRef("");
  useEffect(() => {
    if (!agmOn) {
      autoAddedSpouse.current = "";
      return;
    }
    const previous = autoAddedSpouse.current;
    if (previous && previous !== spouseKey) {
      setAgmCounts((prev) => ({ ...prev, adultEmails: prev.adultEmails.filter((e) => e.trim().toLowerCase() !== previous) }));
      autoAddedSpouse.current = "";
    }
    if (spouseKey && autoAddedSpouse.current !== spouseKey) {
      autoAddedSpouse.current = spouseKey;
      setAgmCounts((prev) =>
        prev.adultEmails.some((e) => e.trim().toLowerCase() === spouseKey)
          ? prev
          : { ...prev, adultEmails: [spouseKey, ...prev.adultEmails] }
      );
    }
  }, [agmOn, spouseKey]);

  const submit = async () => {
    setError("");
    if (!method) {
      setError("Please choose how you'd like to pay.");
      return;
    }
    if (!agreedAll) {
      setError("Please accept the terms & conditions, cancellation policy and privacy policy to continue.");
      return;
    }
    if (spouseOn && !spouseCounted) {
      setError("Check your spouse's email address, or untick the spouse option.");
      return;
    }
    if (agmOn && agmCounts.adultEmails.some((e) => !e.trim())) {
      setError("Enter an email for each additional AGM adult, or remove the empty row.");
      return;
    }
    if (agmOn && !agmAdultsValid) {
      setError("Every additional AGM adult must be an NPO member. Check the emails in the AGM section.");
      return;
    }
    setIsBusy(true);
    try {
      const data = await applyNpoMembership(method, {
        spouseEmail: spouseOn ? spouseEmail.trim().toLowerCase() : "",
        consent: { terms: agreeTerms, cancellation: agreeCancel, privacy: agreePrivacy },
        agm: agmOn ? { ...agmCounts, adultEmails: agmCounts.adultEmails.map((e) => e.trim().toLowerCase()) } : null,
      });
      // Card payments continue on Square's hosted checkout.
      if (method === "square" && data.paymentMethod?.url) {
        window.location.href = data.paymentMethod.url;
        return;
      }
      setSpouseOn(false);
      setSpouseEmail("");
      setAgmOn(false);
      setAgreeTerms(false);
      setAgreeCancel(false);
      setAgreePrivacy(false);
      await load();
    } catch (err) {
      setError(err.message || "Something went wrong. Please try again.");
    } finally {
      setIsBusy(false);
    }
  };

  const submitLabel = isBusy
    ? "Please wait…"
    : covered
      ? "Add spouse membership →"
      : isMember
        ? "Renew & Keep Giving Back →"
        : "Join the Family →";

  return (
    <>
      <Navbar navigateTo={navigateTo} />
      <main className="page-wrap onam-reg">
        <header className="event-header event-header--standalone">
          <div className="banner-title">WMF Japan Foundation Membership</div>
          <div className="banner-sub-title">{email ? `Signed in as ${email}` : "Your WMF Japan"}</div>
          <div className="sky-bar" />
        </header>

        <div className="mypage-stack">
          <ProfileSection status={status} email={email} error={error} />

          <Section id="membership" title="NPO Membership">
            {!status ? <p className="field-note">Loading your membership…</p> : null}
            {status ? (
              <>
                <p className="field-note" style={{ marginBottom: 16 }}>
                  Status:{" "}
                  <strong>
                    {isMember
                      ? `Active member until ${formatDate(status.validUntil)}`
                      : status.validUntil
                        ? `Lapsed on ${formatDate(status.validUntil)}`
                        : "Not a member yet"}
                  </strong>
                  {covered ? ` · covered for ${year}` : ""}
                </p>

                {pending ? (
                  <PendingPayment pending={pending} year={year} onRefresh={load} returning={returning} />
                ) : (
                  <>
                    {covered ? (
                      <div className="success-banner">
                        <strong>You're part of the WMF Japan family for {year}. 🎉</strong>
                        <p style={{ margin: "8px 0 0" }}>
                          Thank you for supporting our community
                          {status.renewalTo ? ` — your membership runs until ${formatDate(status.renewalTo)}.` : "."}
                        </p>
                      </div>
                    ) : (
                      <>
                        <h3 style={{ margin: "0 0 6px" }}>
                          {isMember ? `Keep the journey going in ${year}` : "Be part of the WMF Japan family"}
                        </h3>
                        <p className="field-note" style={{ marginBottom: 18 }}>
                          {isMember
                            ? `Your membership is active until ${formatDate(status.validUntil)}. Renew now to stay with us through ${formatDate(status.renewalTo)} and keep enjoying member benefits.`
                            : `Join the WMF Japan NPO for ${year} and enjoy member pricing at our events while supporting Malayali culture in Japan.`}{" "}
                          Membership fee: <strong>{formatYen(fee)}</strong> per person.
                        </p>
                      </>
                    )}

                    {methods.length === 0 ? (
                      <p className="field-note">
                        Online sign-up isn't open just yet. Please check back soon or write to{" "}
                        <a href="mailto:info@wmfjapan.com">info@wmfjapan.com</a>.
                      </p>
                    ) : (
                      <>
                        <SpouseField
                          enabled={spouseOn}
                          onToggle={setSpouseOn}
                          email={spouseEmail}
                          onEmail={setSpouseEmail}
                          check={spouseCheck}
                        />

                        {agm && !covered ? (
                          <div className="mypage-option">
                            <label className="checkbox-inline">
                              <input
                                type="checkbox"
                                checked={agmOn}
                                onChange={(event) => setAgmOn(event.target.checked)}
                              />{" "}
                              Also register me for the AGM ({agm.title || "Annual General Meeting"})
                            </label>
                            {agmOn ? (
                              <div style={{ marginTop: 12 }}>
                                <AgmAttendeeFields value={agmCounts} onChange={setAgmCounts} disabled={isBusy} checks={agmChecks} lockedAdults={lockedAdults} />
                              </div>
                            ) : null}
                          </div>
                        ) : null}

                        {lines.length > 0 ? (
                          <>
                            <div className="section-label" style={{ marginTop: 22 }}>
                              Order summary
                            </div>
                            <div className="mypage-rows">
                              {lines.map((line) => (
                                <ProfileRow key={line.label} label={line.label} value={formatYen(line.amount)} />
                              ))}
                              <div className="mypage-row mypage-row--total">
                                <span className="mypage-row-label">Total</span>
                                <span className="mypage-row-value">{formatYen(total)}</span>
                              </div>
                            </div>

                            <div className="section-label" style={{ marginTop: 22 }}>
                              How would you like to pay?
                            </div>
                            <div className="onam-toggle-group" role="radiogroup" aria-label="Payment method">
                              {methods.map((m) => (
                                <button
                                  key={m.key}
                                  type="button"
                                  role="radio"
                                  aria-checked={method === m.key}
                                  className={`onam-toggle-pill ${method === m.key ? "selected" : ""}`}
                                  onClick={() => setMethod(m.key)}
                                >
                                  {METHOD_ICONS[m.key] || "💴"} {m.label}
                                </button>
                              ))}
                            </div>

                            <div className="onam-consents">
                              <label className="onam-check">
                                <input type="checkbox" checked={agreeTerms} onChange={(e) => setAgreeTerms(e.target.checked)} />
                                <span>
                                  I accept the{" "}
                                  <a href="/terms" target="_blank" rel="noopener noreferrer">terms &amp; conditions</a>.
                                </span>
                              </label>
                              <label className="onam-check">
                                <input type="checkbox" checked={agreeCancel} onChange={(e) => setAgreeCancel(e.target.checked)} />
                                <span>
                                  I accept the{" "}
                                  <a href="/cancellation" target="_blank" rel="noopener noreferrer">cancellation policy</a>.
                                </span>
                              </label>
                              <label className="onam-check">
                                <input type="checkbox" checked={agreePrivacy} onChange={(e) => setAgreePrivacy(e.target.checked)} />
                                <span>
                                  I consent to my details being used to process this membership, per the{" "}
                                  <a href="/privacy" target="_blank" rel="noopener noreferrer">privacy policy</a>.
                                </span>
                              </label>
                            </div>

                            {error ? <p className="error-banner">{error}</p> : null}

                            <div className="btn-row">
                              <button type="button" className="btn-primary" onClick={submit} disabled={!canSubmit || !agreedAll}>
                                {submitLabel}
                              </button>
                            </div>
                          </>
                        ) : covered ? (
                          <p className="field-note" style={{ marginTop: 14 }}>
                            Tick the spouse option above to add their membership.
                          </p>
                        ) : null}
                      </>
                    )}
                  </>
                )}
              </>
            ) : null}
          </Section>

          {agm ? <AgmCard agm={agm} onSaved={load} /> : null}

          <TransactionsSection status={status} />
        </div>
      </main>
    </>
  );
}
