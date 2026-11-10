import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import DynamicForm from "./DynamicForm";
import EventShell, { ErrorBox, Loading } from "./EventShell";
import AuthPanel from "./auth/AuthPanel";
import { tabStore } from "./auth/storage";
import { registerForEvent, getPublicEvent, uploadEventFile } from "./eventsApi";
import { getEventState, normalizeEvent } from "./eventModel";
import { visibleFields } from "./formLogic";
import { formatJpy, formatJst, t } from "./i18n";

// A checkout in progress is kept for the length of this tab visit so that it survives the full-page trip to a
// social sign-in provider. It holds ticket counts and the answers typed so far, nothing else, and is removed
// when it is restored, when the registration is submitted, or after 30 minutes.
const DRAFT_TTL_MS = 30 * 60 * 1000;
const draftKey = (slug) => `wmf-checkout.${slug}`;

export function saveCheckoutDraft(slug, draft) {
  tabStore.set(draftKey(slug), JSON.stringify({ v: 1, savedAt: Date.now(), ...draft }));
}

export function takeCheckoutDraft(slug) {
  const raw = tabStore.get(draftKey(slug));
  tabStore.remove(draftKey(slug));
  try {
    const draft = raw ? JSON.parse(raw) : null;
    return draft && draft.v === 1 && Date.now() - draft.savedAt < DRAFT_TTL_MS ? draft : null;
  } catch {
    return null;
  }
}

function newKey() {
  return globalThis.crypto?.randomUUID?.() || `${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

/** Cheap pre-check so obvious gaps are caught before the round trip; the server stays the authority. */
function requiredErrors(fields, answers, prefix = "") {
  const errors = {};
  visibleFields(fields, answers).forEach((f) => {
    if (["heading", "info"].includes(f.type) || f.source === "system" || !f.required) return;
    const v = answers[f.key];
    const blank = v === undefined || v === null || v === "" || v === false || (Array.isArray(v) && v.length === 0);
    if (blank) errors[`${prefix}${f.key}`] = t("pub.required");
  });
  return errors;
}

function OrderSummary({ ev, lines, total }) {
  const place = [ev.location?.venueName, ev.location?.address].filter(Boolean).join(", ");
  return (
    <aside className="pe-summary-card" aria-labelledby="pe-summary-h">
      <h2 id="pe-summary-h">{t("pub.summary.title")}</h2>
      <p className="pe-summary-event">{ev.title}</p>
      <dl className="pe-summary-facts">
        {ev.startsAt && <><dt>{t("pub.summary.when")}</dt><dd>{formatJst(ev.startsAt)}</dd></>}
        {place && <><dt>{t("pub.summary.where")}</dt><dd>{place}</dd></>}
      </dl>
      {lines.length === 0 ? <p className="pe-meta">{t("pub.summary.empty")}</p> : (
        <ul className="pe-summary-lines">
          {lines.map((l) => <li key={l.ticketTypeId}><span>{l.name}</span><span>×{l.qty}</span><span>{formatJpy(l.yourPriceJpy * l.qty)}</span></li>)}
        </ul>
      )}
      <p className="pe-summary-total"><span>{t("pub.total")}</span><strong>{formatJpy(total)}</strong></p>
    </aside>
  );
}

export default function EventRegister({ navigateTo, slug }) {
  const [ev, setEv] = useState(null);
  const [error, setError] = useState("");
  const [step, setStep] = useState("start"); // start | login | tickets | details | review
  const [qty, setQty] = useState({});
  const [answers, setAnswers] = useState({});
  const [fieldErrors, setFieldErrors] = useState({});
  const [method, setMethod] = useState("");
  const [busy, setBusy] = useState(false);
  const [guest, setGuest] = useState(false);
  const [restored, setRestored] = useState(false);
  const resumeStep = useRef("tickets");
  const draftApplied = useRef(false);
  const idempotencyKey = useRef(newKey());
  const headingRef = useRef(null);

  const load = useCallback(async () => {
    try {
      const d = await getPublicEvent(slug);
      if (d.legacy) return navigateTo(`/events/${slug}`, { replace: true });
      const event = normalizeEvent(d.event);
      setEv(event);
      setAnswers((a) => ({
        ...a,
        ...(event.viewer.loggedIn ? { email: event.viewer.email, fullName: a.fullName || event.viewer.name } : {}),
      }));
      if (!draftApplied.current) {
        draftApplied.current = true;
        const draft = takeCheckoutDraft(slug);
        if (draft) {
          const known = new Set(event.tickets.map((x) => x.ticketTypeId));
          setQty(Object.fromEntries(Object.entries(draft.qty || {}).filter(([id, n]) => known.has(id) && Number(n) > 0)));
          setAnswers((a) => ({ ...(draft.answers || {}), ...a, ...(event.viewer.loggedIn ? { email: event.viewer.email } : {}) }));
          setGuest(!!draft.guest);
          if (event.viewer.loggedIn || draft.guest) setStep(["tickets", "details", "review"].includes(draft.step) ? draft.step : "tickets");
          setRestored(true);
        }
      }
    } catch (e) {
      setError(e.status === 404 ? t("pub.notFound") : e.message || t("pub.error.generic"));
    }
    return undefined;
  }, [slug]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => { load(); }, [load]);
  useEffect(() => { headingRef.current?.focus(); }, [step]);

  const loggedIn = !!ev?.viewer.loggedIn;
  const lines = useMemo(
    () => (ev ? ev.tickets.filter((x) => (qty[x.ticketTypeId] || 0) > 0).map((x) => ({ ...x, qty: qty[x.ticketTypeId] })) : []),
    [ev, qty]
  );
  const total = lines.reduce((sum, l) => sum + l.yourPriceJpy * l.qty, 0);
  const seats = lines.reduce((sum, l) => sum + l.qty, 0);

  // Only a failure to load the event replaces the page. A submit error is shown above the form so nothing typed is lost.
  if (error && !ev) {
    return (
      <EventShell navigateTo={navigateTo}>
        <ErrorBox message={error} />
        <button type="button" className="button secondary" onClick={() => navigateTo(`/events/${slug}`)}>{t("pub.back")}</button>
      </EventShell>
    );
  }
  if (!ev) return <EventShell navigateTo={navigateTo}><Loading /></EventShell>;

  const r = ev.registration;
  const caps = ev.capabilities;
  const state = getEventState(ev);
  const back = () => navigateTo(`/events/${slug}`);

  // Not open / sold out / cancelled / not allowed: explain, don't show a form.
  if (["cancelled", "unavailable", "closed", "not_open", "sold_out", "members_only", "renewal_required"].includes(state)) {
    const messages = {
      cancelled: t("pub.state.cancelled"),
      unavailable: t("pub.state.registrationUnavailable"),
      closed: t("pub.registrationClosed"),
      not_open: t("pub.reg.opensAt", { when: ev.registrationOpensAt ? formatJst(ev.registrationOpensAt) : "" }),
      sold_out: t("pub.state.soldOut"),
      members_only: t("pub.reg.membersOnlyBlocked"),
      renewal_required: t("pub.reg.renewalBlocked"),
    };
    return (
      <EventShell navigateTo={navigateTo} accent={ev.design?.accentColor}>
        <h1>{ev.title}</h1>
        <p role="alert">{messages[state]}</p>
        {r.renewUrl && <button type="button" className="button primary" onClick={() => navigateTo("/member/membership")}>{t("pub.reg.renewNow")}</button>}
        <button type="button" className="button secondary" onClick={back}>{t("pub.back")}</button>
      </EventShell>
    );
  }

  // Guest checkout can be switched off per event; members-only events always need an account.
  const needLogin = !loggedIn && (ev.access.membersOnly || !caps.guestCheckout.enabled);
  const draftNow = () => ({ qty, answers, step: step === "login" ? resumeStep.current : step, guest });
  const afterSignIn = async () => {
    await load();
    setGuest(false);
    setError("");
    setStep(["tickets", "details", "review"].includes(resumeStep.current) ? resumeStep.current : "tickets");
  };
  const openSignIn = () => {
    resumeStep.current = ["tickets", "details", "review"].includes(step) ? step : "tickets";
    setStep("login");
  };
  if (!loggedIn && (step === "login" || (step === "start" && needLogin))) {
    return (
      <EventShell navigateTo={navigateTo} accent={ev.design?.accentColor}>
        <h1>{ev.title}</h1>
        <ErrorBox message={error} />
        {needLogin && <p className="pe-note" role="status">{t(ev.access.membersOnly ? "pub.reg.membersOnlySignIn" : "pub.start.signInRequired")}</p>}
        {restored && <p className="pe-note" role="status">{t("pub.start.draftRestored")}</p>}
        <AuthPanel
          returnTo={`/events/${slug}/register`}
          onAuthenticated={afterSignIn}
          onGuest={needLogin ? undefined : () => { setGuest(true); setError(""); setStep(resumeStep.current); }}
          beforeSocialRedirect={() => saveCheckoutDraft(slug, draftNow())}
        />
      </EventShell>
    );
  }

  const hasMemberDiscount = ev.tickets.some((x) => x.memberPriceJpy < x.nonMemberPriceJpy);
  const startGuest = () => { setGuest(true); setStep("tickets"); };
  const signInPrompt = !loggedIn && (
    <p className="pe-note">
      {hasMemberDiscount && <>{t("pub.reg.memberDiscountHint")} </>}
      <button type="button" className="pe-link" onClick={openSignIn}>{t("pub.start.signInAccount")}</button>
    </p>
  );

  const toDetails = () => {
    if (seats === 0) return setFieldErrors({ tickets: t("pub.tickets.choose") });
    setFieldErrors({});
    setStep("details");
  };

  const toReview = () => {
    const errs = requiredErrors(ev.form.fields, answers);
    setFieldErrors(errs);
    if (Object.keys(errs).length) return;
    setStep("review");
  };

  const submit = async () => {
    if (total > 0 && !method) return setError(t("pub.pay.choose"));
    setBusy(true);
    setError("");
    try {
      const res = await registerForEvent(slug, {
        tickets: Object.fromEntries(lines.map((l) => [l.ticketTypeId, l.qty])),
        answers, paymentMethod: total > 0 ? method : undefined, idempotencyKey: idempotencyKey.current,
      });
      const reg = res.registration;
      takeCheckoutDraft(slug); // nothing left to restore
      if (reg.status === "pending_payment" && res.payment.checkoutUrl) {
        window.location.assign(res.payment.checkoutUrl);
        return;
      }
      navigateTo(`/events/${slug}/registered?reg=${encodeURIComponent(reg.registrationId)}&token=${encodeURIComponent(reg.checkInToken)}`, { replace: true });
    } catch (e) {
      if (e.status === 401 && e.code !== "email_unverified") {
        // The session ended while the form was open: keep everything typed, ask for a new sign-in, then resume here.
        resumeStep.current = step === "start" ? "tickets" : step;
        setError(e.message);
        await load();
        setStep("login");
      } else if (e.status === 400 && Object.keys(e.fields || {}).length) {
        setFieldErrors(e.fields);
        const labels = Object.fromEntries(ev.form.fields.map((f) => [f.key, f.label]));
        const details = Object.entries(e.fields).map(([k, msg]) => `${labels[k] || k}: ${msg}`).join(" · ");
        setError(`${e.message} — ${details}`);
        setStep(e.fields.tickets || Object.keys(e.fields).every((k) => ev.tickets.some((x) => x.ticketTypeId === k)) ? "tickets" : "details");
      } else {
        setError(e.message);
        if (e.code === "sold_out") load();
      }
    } finally {
      setBusy(false);
    }
  };

  const stepNames = ["tickets", "details", "review"];
  const stepIndex = stepNames.indexOf(step);

  return (
    <EventShell navigateTo={navigateTo} accent={ev.design?.accentColor}>
      <h1>{ev.title}</h1>
      {step !== "start" && (
        <ol className="pe-steps" aria-label={t("pub.steps")}>
          {stepNames.map((s, i) => (
            <li key={s} aria-current={i === stepIndex ? "step" : undefined} className={i <= stepIndex ? "done" : ""}>{i + 1}. {t(`pub.step.${s}`)}</li>
          ))}
        </ol>
      )}
      <ErrorBox message={error} />

      <div className="pe-checkout">
        <div className="pe-checkout-main">
      {step === "start" && (
        <section className="pe-box">
          <h2 tabIndex={-1} ref={headingRef}>{t("pub.start.title")}</h2>
          {!loggedIn && (
            <>
              <p>{t("pub.start.choose")}</p>
              <div className="btn-row">
                <button type="button" className="button primary" onClick={openSignIn}>{t("pub.start.signInAccount")}</button>
                <button type="button" className="button secondary" onClick={startGuest}>{t("pub.start.guest")}</button>
              </div>
              <p className="pe-note">{t("pub.start.guestNote")}</p>
              {hasMemberDiscount && <p className="pe-note">{t("pub.reg.memberDiscountHint")}</p>}
              <p className="pe-note"><button type="button" className="pe-link" onClick={() => navigateTo("/member/membership")}>{t("pub.reg.joinMembership")}</button></p>
            </>
          )}
          {loggedIn && (
            <>
              <p>{t("pub.start.signedIn", { email: ev.viewer.email })}</p>
              <button type="button" className="button primary" onClick={() => setStep("tickets")}>{t("pub.continue")}</button>
            </>
          )}
        </section>
      )}

      {step === "tickets" && (
        <section className="pe-box">
          <h2 tabIndex={-1} ref={headingRef}>{t("pub.step.tickets")}</h2>
          {loggedIn && !ev.viewer.memberPricing && hasMemberDiscount && (
            <p className="pe-note" role="status">{t("pub.tickets.expired")}{" "}
              <button type="button" className="pe-link" onClick={() => navigateTo("/member/membership")}>{t("pub.reg.renewNow")}</button></p>
          )}
          {restored && <p className="pe-note" role="status">{t("pub.start.draftRestored")}</p>}
          {guest && signInPrompt}
          {r.waitlist && <p className="pe-note" role="status">{t("pub.reg.full")}</p>}
          <ul className="pe-tickets">
            {ev.tickets.map((x) => {
              const max = Math.min(x.perPersonCap || 50, x.remaining === null || r.waitlist ? 50 : x.remaining);
              const value = qty[x.ticketTypeId] || 0;
              const soldOut = !r.waitlist && x.remaining === 0;
              return (
                <li key={x.ticketTypeId}>
                  <div>
                    <label htmlFor={`qty-${x.ticketTypeId}`}><strong>{x.name}</strong></label>
                    {x.description && <p className="pe-meta">{x.description}</p>}
                    <p className="pe-price">{formatJpy(x.yourPriceJpy)}{x.yourPriceIsEarlyBird && <small> {t("pub.earlyBirdApplied")}</small>}</p>
                    {soldOut && <p className="pe-warn">{t("pub.soldOut")}</p>}
                  </div>
                  <div className="pe-qty">
                    <button type="button" className="button secondary" aria-label={t("pub.tickets.less", { name: x.name })} disabled={value === 0}
                      onClick={() => setQty({ ...qty, [x.ticketTypeId]: value - 1 })}>−</button>
                    <input id={`qty-${x.ticketTypeId}`} type="number" min="0" max={max} value={value} disabled={soldOut} inputMode="numeric"
                      onChange={(e) => setQty({ ...qty, [x.ticketTypeId]: Math.max(0, Math.min(max, Number(e.target.value) || 0)) })} />
                    <button type="button" className="button secondary" aria-label={t("pub.tickets.more", { name: x.name })} disabled={soldOut || value >= max}
                      onClick={() => setQty({ ...qty, [x.ticketTypeId]: value + 1 })}>+</button>
                  </div>
                  {fieldErrors[x.ticketTypeId] && <span className="field-error" role="alert">{fieldErrors[x.ticketTypeId]}</span>}
                </li>
              );
            })}
          </ul>
          {fieldErrors.tickets && <span className="field-error" role="alert">{fieldErrors.tickets}</span>}
          <p className="pe-total" aria-live="polite">{t("pub.total")}: <strong>{formatJpy(total)}</strong></p>
          <div className="btn-row">
            <button type="button" className="button secondary" onClick={back}>{t("pub.back")}</button>
            <button type="button" className="button primary" onClick={toDetails}>{t("pub.continue")}</button>
          </div>
        </section>
      )}

      {step === "details" && (
        <section className="pe-box">
          <h2 tabIndex={-1} ref={headingRef}>{t("pub.step.details")}</h2>
          {Object.keys(fieldErrors).length > 0 && <div className="error-banner" role="alert">{t("pub.fixErrors")}</div>}
          <DynamicForm fields={ev.form.fields} answers={answers} idPrefix="reg" errors={fieldErrors}
            system={ev.viewer.system} readOnlyKeys={loggedIn ? ["email"] : []}
            onChange={(k, v) => { setAnswers((a) => ({ ...a, [k]: v })); setFieldErrors((e) => { const { [k]: _gone, ...rest } = e; return rest; }); }}
            uploadFile={(field, file) => uploadEventFile(slug, field.key, file)} />
          <div className="btn-row">
            <button type="button" className="button secondary" onClick={() => setStep("tickets")}>{t("pub.back")}</button>
            <button type="button" className="button primary" onClick={toReview}>{t("pub.continue")}</button>
          </div>
        </section>
      )}

      {step === "review" && (
        <section className="pe-box">
          <h2 tabIndex={-1} ref={headingRef}>{t("pub.step.review")}</h2>
          <dl className="pe-summary">
            <dt>{t("pub.review.who")}</dt><dd>{answers.fullName} · {answers.email}</dd>
            <dt>{t("pub.tickets")}</dt>
            <dd>
              <ul>{lines.map((l) => <li key={l.ticketTypeId}>{l.name} × {l.qty} — {formatJpy(l.yourPriceJpy * l.qty)}</li>)}</ul>
            </dd>
            <dt>{t("pub.total")}</dt><dd><strong>{formatJpy(total)}</strong></dd>
          </dl>
          {r.waitlist && <p className="pe-note" role="status">{t("pub.review.waitlistNote")}</p>}
          {total > 0 && (
            <fieldset className="pe-pay">
              <legend>{t("pub.pay.title")}</legend>
              {ev.paymentMethods.length === 0 && <p role="alert">{t("pub.pay.none")}</p>}
              {ev.paymentMethods.map((m) => (
                <label key={m.key} className="ep-choice">
                  <input type="radio" name="pay" value={m.key} checked={method === m.key} onChange={() => setMethod(m.key)} /> {m.label}
                </label>
              ))}
              <p className="pe-note">{t(method === "square" ? "pub.pay.cardNote" : "pub.pay.manualNote")}</p>
            </fieldset>
          )}
          <div className="btn-row">
            <button type="button" className="button secondary" onClick={() => setStep("details")} disabled={busy}>{t("pub.back")}</button>
            <button type="button" className="button primary" onClick={submit} disabled={busy || (total > 0 && ev.paymentMethods.length === 0)}>
              {busy ? t("pub.submitting") : t(r.waitlist ? "pub.review.joinWaitlist" : total > 0 ? "pub.review.payRegister" : "pub.review.register")}
            </button>
          </div>
        </section>
      )}
        </div>
        {step !== "start" && <OrderSummary ev={ev} lines={lines} total={total} />}
      </div>
    </EventShell>
  );
}
