import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import DynamicForm from "./DynamicForm";
import EventShell, { ErrorBox, Loading } from "./EventShell";
import RegistrationLogin from "./RegistrationLogin";
import { registerForEvent, getPublicEvent, uploadEventFile } from "./eventsApi";
import { visibleFields } from "./formLogic";
import { formatJpy, formatJst, t } from "./i18n";
import { clearRegistrantSession, getRegistrantToken } from "./registrantAuth";

const CORE_PREFILL = { fullName: "name", email: "email" };

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
  const idempotencyKey = useRef(newKey());
  const headingRef = useRef(null);

  const load = useCallback(async () => {
    try {
      const d = await getPublicEvent(slug);
      if (d.legacy) return navigateTo(d.event.legacyPath || "/events", { replace: true });
      setEv(d.event);
      setAnswers((a) => ({
        ...a,
        ...(d.event.viewer.loggedIn ? { email: d.event.viewer.email, fullName: a.fullName || d.event.viewer.name } : {}),
      }));
    } catch (e) {
      setError(e.status === 404 ? t("pub.notFound") : e.message);
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

  if (error) return <EventShell navigateTo={navigateTo}><ErrorBox message={error} /></EventShell>;
  if (!ev) return <EventShell navigateTo={navigateTo}><Loading /></EventShell>;

  const r = ev.registration;
  const back = () => navigateTo(`/events/${slug}`);

  // Not open / not allowed: explain, don't show a form.
  if (r.window !== "open" || (!r.allowed && !["login_required"].includes(r.reason))) {
    const message = r.window === "not_open" ? t("pub.reg.opensAt", { when: ev.registrationOpensAt ? formatJst(ev.registrationOpensAt) : "" })
      : r.window === "closed" ? t("pub.registrationClosed")
      : t(r.reason === "members_only" ? "pub.reg.membersOnlyBlocked" : "pub.reg.renewalBlocked");
    return (
      <EventShell navigateTo={navigateTo} accent={ev.design?.accentColor}>
        <h1>{ev.title}</h1>
        <p role="alert">{message}</p>
        {r.renewUrl && <button type="button" className="button primary" onClick={() => navigateTo("/member/membership")}>{t("pub.reg.renewNow")}</button>}
        <button type="button" className="button secondary" onClick={back}>{t("pub.back")}</button>
      </EventShell>
    );
  }

  const needLogin = !loggedIn && (ev.access.membersOnly || ev.access.guestsAllowed === false);
  if (step === "login" || (step === "start" && needLogin)) {
    return (
      <RegistrationLogin
        navigateTo={navigateTo}
        title={t("pub.login.title")}
        subtitle={ev.title}
        description={t("pub.login.description")}
        onAuthenticated={async () => { await load(); setStep("tickets"); }}
      />
    );
  }

  const startGuest = () => { setGuest(true); setStep("tickets"); };
  const hasMemberDiscount = ev.tickets.some((x) => x.memberPriceJpy < x.nonMemberPriceJpy);

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
      if (reg.status === "pending_payment" && res.payment.checkoutUrl) {
        window.location.assign(res.payment.checkoutUrl);
        return;
      }
      navigateTo(`/events/${slug}/registered?reg=${encodeURIComponent(reg.registrationId)}&token=${encodeURIComponent(reg.checkInToken)}`, { replace: true });
    } catch (e) {
      if (e.status === 401) {
        clearRegistrantSession();
        setError(e.message);
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

      {step === "start" && (
        <section className="pe-box">
          <h2 tabIndex={-1} ref={headingRef}>{t("pub.start.title")}</h2>
          {!loggedIn && (
            <>
              <p>{t("pub.start.choose")}</p>
              <div className="btn-row">
                <button type="button" className="button primary" onClick={() => setStep("login")}>{t("pub.start.signIn")}</button>
                <button type="button" className="button secondary" onClick={startGuest}>{t("pub.start.guest")}</button>
              </div>
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
          {guest && hasMemberDiscount && <p className="pe-note">{t("pub.reg.memberDiscountHint")}</p>}
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
    </EventShell>
  );
}
