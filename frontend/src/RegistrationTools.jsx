import { useEffect, useState } from "react";
import DynamicForm from "./DynamicForm";
import { visibleFields } from "./formLogic";
import { formatJpy, t } from "./i18n";
import { addRegistration, emailRegistrants, getEventForm } from "./eventPlatformApi";

const AUDIENCES = ["active", "confirmed", "pending", "waitlisted", "not_checked_in", "checked_in", "cancelled"];
const PLACEHOLDERS = ["name", "event_title", "ticket_type", "registration_id", "event_date", "venue", "event_url"];

function newKey() {
  return globalThis.crypto?.randomUUID?.() || `${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

function Banner({ message, fields }) {
  if (!message) return null;
  return <div className="error-banner" role="alert"><strong>{message}</strong>{fields && Object.keys(fields).length > 0 && <ul>{Object.entries(fields).map(([k, v]) => <li key={k}>{k}: {v}</li>)}</ul>}</div>;
}

// ── Add a registration on someone's behalf ────────────────────────────────────

export function AddRegistration({ eventId, tickets, onDone, onCancel }) {
  const [form, setForm] = useState(null);
  const [qty, setQty] = useState({});
  const [answers, setAnswers] = useState({});
  const [memberEmail, setMemberEmail] = useState("");
  const [payment, setPayment] = useState("paid");
  const [paidVia, setPaidVia] = useState("cash");
  const [method, setMethod] = useState("bank");
  const [total, setTotal] = useState("");
  const [note, setNote] = useState("");
  const [mode, setMode] = useState("normal"); // normal | ignore | waitlist
  const [notify, setNotify] = useState(true);
  const [consent, setConsent] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState({ message: "", fields: {} });
  const [key] = useState(newKey);

  useEffect(() => {
    getEventForm(eventId).then((r) => setForm(r.form)).catch((e) => setError({ message: e.message, fields: {} }));
  }, [eventId]);

  const submit = async () => {
    setBusy(true);
    setError({ message: "", fields: {} });
    const withConsent = { ...answers, consentPrivacy: consent, consentTerms: consent };
    try {
      const res = await addRegistration(eventId, {
        tickets: Object.fromEntries(Object.entries(qty).filter(([, n]) => n > 0)), answers: withConsent,
        memberEmail: memberEmail.trim() || undefined, paymentStatus: payment, paidVia: payment === "paid" ? paidVia : undefined,
        paymentMethod: payment === "pending" ? method : undefined, totalJpy: total === "" ? undefined : Number(total),
        adminNote: note || undefined, ignoreCapacity: mode === "ignore", waitlist: mode === "waitlist", notify, idempotencyKey: key,
      });
      onDone(res.registration);
    } catch (e) {
      setError({ message: e.message, fields: e.fields || {} });
    } finally {
      setBusy(false);
    }
  };

  if (!form) return error.message ? <Banner message={error.message} /> : <p>{t("events.loading")}</p>;
  const consentKeys = ["consentPrivacy", "consentTerms"];
  const fields = form.fields.filter((f) => !consentKeys.includes(f.key));
  const answerErrors = error.fields;
  return (
    <section className="ep-card" aria-labelledby="reg-add-h">
      <h3 id="reg-add-h">{t("reg.add.title")}</h3>
      <p className="ep-note">{t("reg.add.intro")}</p>
      <Banner message={error.message} fields={error.fields} />
      <fieldset className="ep-fieldset">
        <legend>{t("pub.tickets")}</legend>
        {tickets.filter((x) => x.active !== false).map((x) => (
          <div className="field-row" key={x.ticketTypeId}>
            <div className="field">
              <label htmlFor={`add-qty-${x.ticketTypeId}`}>{x.name} <small>({formatJpy(x.nonMemberPriceJpy)} / {formatJpy(x.memberPriceJpy)})</small></label>
              <input id={`add-qty-${x.ticketTypeId}`} type="number" min="0" value={qty[x.ticketTypeId] ?? 0}
                onChange={(e) => setQty({ ...qty, [x.ticketTypeId]: Math.max(0, Number(e.target.value) || 0) })} />
            </div>
          </div>
        ))}
      </fieldset>
      <div className="field">
        <label htmlFor="add-member">{t("reg.add.memberEmail")}</label>
        <input id="add-member" type="email" value={memberEmail} onChange={(e) => setMemberEmail(e.target.value)} />
        <small>{t("reg.add.memberHelp")}</small>
      </div>
      <fieldset className="ep-fieldset">
        <legend>{t("reg.add.details")}</legend>
        <DynamicForm fields={fields} answers={memberEmail ? { ...answers } : answers} idPrefix="add" errors={answerErrors}
          system={{ membershipStatus: memberEmail ? t("reg.add.fromMember") : "none" }}
          onChange={(k, v) => setAnswers((a) => ({ ...a, [k]: v }))} />
      </fieldset>
      <div className="field ep-check">
        <label htmlFor="add-consent"><input id="add-consent" type="checkbox" checked={consent} onChange={(e) => setConsent(e.target.checked)} /> {t("reg.add.consent")}</label>
        {(answerErrors.consentPrivacy || answerErrors.consentTerms) && <span className="field-error" role="alert">{t("pub.required")}</span>}
      </div>
      <fieldset className="ep-fieldset">
        <legend>{t("reg.add.payment")}</legend>
        <div className="field-row">
          <div className="field">
            <label htmlFor="add-pay">{t("reg.add.paymentStatus")}</label>
            <select id="add-pay" value={payment} onChange={(e) => setPayment(e.target.value)}>
              <option value="paid">{t("reg.add.paid")}</option>
              <option value="pending">{t("reg.add.pending")}</option>
            </select>
          </div>
          {payment === "paid" ? (
            <div className="field">
              <label htmlFor="add-via">{t("reg.add.paidVia")}</label>
              <select id="add-via" value={paidVia} onChange={(e) => setPaidVia(e.target.value)}>
                {["cash", "bank", "paypay", "square", "other"].map((v) => <option key={v} value={v}>{t(`reg.via.${v}`)}</option>)}
              </select>
            </div>
          ) : (
            <div className="field">
              <label htmlFor="add-method">{t("reg.add.expectedVia")}</label>
              <select id="add-method" value={method} onChange={(e) => setMethod(e.target.value)}>
                <option value="bank">{t("reg.via.bank")}</option>
                <option value="paypay">{t("reg.via.paypay")}</option>
              </select>
            </div>
          )}
          <div className="field">
            <label htmlFor="add-total">{t("reg.add.total")}</label>
            <input id="add-total" type="number" min="0" step="1" value={total} onChange={(e) => setTotal(e.target.value)} placeholder={t("reg.add.totalAuto")} />
          </div>
        </div>
        <div className="field">
          <label htmlFor="add-note">{t("reg.add.note")}</label>
          <input id="add-note" value={note} maxLength={1000} onChange={(e) => setNote(e.target.value)} />
          {answerErrors.adminNote && <span className="field-error" role="alert">{answerErrors.adminNote}</span>}
        </div>
      </fieldset>
      <fieldset className="ep-fieldset">
        <legend>{t("reg.add.capacity")}</legend>
        {["normal", "ignore", "waitlist"].map((m) => (
          <label key={m} className="ep-choice"><input type="radio" name="add-mode" checked={mode === m} onChange={() => setMode(m)} /> {t(`reg.add.mode.${m}`)}</label>
        ))}
      </fieldset>
      <div className="field ep-check">
        <label htmlFor="add-notify"><input id="add-notify" type="checkbox" checked={notify} onChange={(e) => setNotify(e.target.checked)} /> {t("reg.add.notify")}</label>
      </div>
      <div className="btn-row">
        <button type="button" className="button primary" onClick={submit} disabled={busy || visibleFields(fields, answers).length === 0}>{t("reg.add.submit")}</button>
        <button type="button" className="button secondary" onClick={onCancel}>{t("events.cancel")}</button>
      </div>
    </section>
  );
}

// ── Bulk e-mail ───────────────────────────────────────────────────────────────

export function EmailPanel({ eventId, event, onClose }) {
  const [audience, setAudience] = useState("active");
  const [template, setTemplate] = useState("");
  const [subject, setSubject] = useState("");
  const [body, setBody] = useState("");
  const [busy, setBusy] = useState(false);
  const [progress, setProgress] = useState(null);
  const [result, setResult] = useState(null);
  const [error, setError] = useState({ message: "", fields: {} });

  const payload = (extra) => ({ audience, subject, body, useTemplate: template || undefined, ...extra });

  const sendTest = async () => {
    setBusy(true);
    setError({ message: "", fields: {} });
    try {
      const r = await emailRegistrants(eventId, payload({ test: true }));
      setResult({ test: true, sent: r.sent, failed: r.failed });
    } catch (e) {
      setError({ message: e.message, fields: e.fields || {} });
    } finally {
      setBusy(false);
    }
  };

  const sendAll = async () => {
    if (!window.confirm(t("reg.email.confirm", { audience: t(`reg.audience.${audience}`) }))) return;
    setBusy(true);
    setError({ message: "", fields: {} });
    setResult(null);
    let offset = 0;
    let sent = 0;
    const failed = [];
    try {
      for (;;) {
        // Batches keep each request short; the server returns where the next one starts.
        // eslint-disable-next-line no-await-in-loop
        const r = await emailRegistrants(eventId, payload({ offset }));
        sent += r.sent;
        failed.push(...r.failed);
        setProgress({ done: Math.min(offset + 100, r.total), total: r.total });
        if (r.nextOffset === null) break;
        offset = r.nextOffset;
      }
      setResult({ sent, failed });
    } catch (e) {
      setError({ message: e.message, fields: e.fields || {} });
      setResult({ sent, failed, interrupted: true });
    } finally {
      setBusy(false);
      setProgress(null);
    }
  };

  const reminder = event?.emails?.reminder?.body;
  return (
    <section className="ep-card" aria-labelledby="reg-email-h">
      <h3 id="reg-email-h">{t("reg.email.title")}</h3>
      <Banner message={error.message} fields={error.fields} />
      <div className="field-row">
        <div className="field">
          <label htmlFor="em-audience">{t("reg.email.audience")}</label>
          <select id="em-audience" value={audience} onChange={(e) => setAudience(e.target.value)}>
            {AUDIENCES.map((a) => <option key={a} value={a}>{t(`reg.audience.${a}`)}</option>)}
          </select>
        </div>
        <div className="field">
          <label htmlFor="em-template">{t("reg.email.template")}</label>
          <select id="em-template" value={template} onChange={(e) => setTemplate(e.target.value)}>
            <option value="">{t("reg.email.custom")}</option>
            <option value="reminder" disabled={!reminder}>{t("reg.email.reminderTemplate")}</option>
          </select>
        </div>
      </div>
      {!template && (
        <>
          <div className="field">
            <label htmlFor="em-subject">{t("events.emails.subject")}</label>
            <input id="em-subject" value={subject} maxLength={200} onChange={(e) => setSubject(e.target.value)} />
            {error.fields.subject && <span className="field-error" role="alert">{error.fields.subject}</span>}
          </div>
          <div className="field">
            <label htmlFor="em-body">{t("events.emails.body")}</label>
            <textarea id="em-body" rows={8} value={body} onChange={(e) => setBody(e.target.value)} />
            <small>{t("events.emails.placeholders")} {PLACEHOLDERS.map((p) => `{${p}}`).join("  ")}</small>
            {error.fields.body && <span className="field-error" role="alert">{error.fields.body}</span>}
          </div>
        </>
      )}
      {template && <p className="ep-note">{t("reg.email.usingTemplate")}</p>}
      {progress && <p role="status">{t("reg.email.progress", { done: progress.done, total: progress.total })}</p>}
      {result && (
        <div role="status" className={result.failed.length || result.interrupted ? "error-banner" : "success-banner"}>
          {result.test ? t("reg.email.testSent") : t("reg.email.sent", { sent: result.sent })}
          {result.failed.length > 0 && <> {t("reg.email.failed", { n: result.failed.length })}: {result.failed.join(", ")}</>}
          {result.interrupted && <> {t("reg.email.interrupted")}</>}
        </div>
      )}
      <div className="btn-row">
        <button type="button" className="button secondary" onClick={sendTest} disabled={busy}>{t("reg.email.test")}</button>
        <button type="button" className="button primary" onClick={sendAll} disabled={busy}>{t("reg.email.send")}</button>
        <button type="button" className="button secondary" onClick={onClose}>{t("reg.close")}</button>
      </div>
    </section>
  );
}
