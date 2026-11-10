import { useCallback, useEffect, useRef, useState } from "react";
import DynamicForm from "./DynamicForm";
import EventShell, { ErrorBox, Loading, StatusPill } from "./EventShell";
import {
  cancelMyEventRegistration,
  editMyEventRegistration,
  myEventRegistration,
  myEventRegistrations,
  uploadEventFile,
} from "./eventsApi";
import { formatJpy, formatJst, t } from "./i18n";
import QRCode from "qrcode";
import { RegistrationAddons } from "./AddonsStep";

function Qr({ value }) {
  const ref = useRef(null);
  useEffect(() => {
    if (ref.current && value) QRCode.toCanvas(ref.current, value, { width: 180, margin: 2 }).catch(() => {});
  }, [value]);
  return <canvas ref={ref} role="img" aria-label={t("pub.done.qrAlt")} className="pe-qr" />;
}

function EditPanel({ id, onClose, onSaved }) {
  const [view, setView] = useState(null);
  const [answers, setAnswers] = useState({});
  const [errors, setErrors] = useState({});
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    myEventRegistration(id).then((v) => { setView(v); setAnswers(v.registration.answers); }).catch((e) => setError(e.message));
  }, [id]);

  const save = async () => {
    setBusy(true);
    setError("");
    setErrors({});
    try {
      await editMyEventRegistration(id, answers);
      onSaved(t("pub.my.saved"));
    } catch (e) {
      setErrors(e.fields || {});
      setError(e.message);
    } finally {
      setBusy(false);
    }
  };

  if (!view) return error ? <ErrorBox message={error} /> : <Loading />;
  const eventSlug = view.event.slug;
  return (
    <section className="pe-box" aria-labelledby="pe-edit-h">
      <h2 id="pe-edit-h">{t("pub.my.editTitle", { title: view.registration.eventTitle })}</h2>
      <p className="pe-note">{t("pub.my.editNote")}</p>
      <ErrorBox message={error} />
      <DynamicForm fields={view.form.fields} answers={answers} idPrefix="edit" errors={errors} system={answers} readOnlyKeys={["email"]}
        onChange={(k, v) => setAnswers((a) => ({ ...a, [k]: v }))} uploadFile={(field, file) => uploadEventFile(eventSlug, field.key, file)} />
      <div className="btn-row">
        <button type="button" className="button primary" onClick={save} disabled={busy}>{t("pub.my.save")}</button>
        <button type="button" className="button secondary" onClick={onClose}>{t("pub.cancel")}</button>
      </div>
    </section>
  );
}

export default function MyEventRegistrations({ navigateTo }) {
  const [rows, setRows] = useState(null);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [editing, setEditing] = useState("");
  const [busyId, setBusyId] = useState("");

  const load = useCallback(() => {
    myEventRegistrations().then((d) => setRows(d.registrations)).catch((e) => setError(e.message));
  }, []);
  useEffect(load, [load]);

  const cancel = async (row) => {
    const paid = row.registration.paymentStatus === "paid";
    if (!window.confirm(t(paid ? "pub.my.cancelPaidConfirm" : "pub.my.cancelConfirm", { title: row.registration.eventTitle }))) return;
    setBusyId(row.registration.registrationId);
    setError("");
    try {
      await cancelMyEventRegistration(row.registration.registrationId);
      setNotice(t(paid ? "pub.my.cancelledPaid" : "pub.my.cancelled"));
      load();
    } catch (e) {
      setError(e.message);
    } finally {
      setBusyId("");
    }
  };

  return (
    <EventShell navigateTo={navigateTo}>
      <h1>{t("pub.my.title")}</h1>
      <ErrorBox message={error} />
      {notice && <div className="success-banner" role="status">{notice}</div>}
      {editing && <EditPanel id={editing} onClose={() => setEditing("")} onSaved={(m) => { setEditing(""); setNotice(m); load(); }} />}
      {!rows && !error && <Loading />}
      {rows && rows.length === 0 && (
        <p>{t("pub.my.none")} <button type="button" className="pe-link" onClick={() => navigateTo("/events")}>{t("pub.my.browse")}</button></p>
      )}
      {rows && rows.length > 0 && (
        <ul className="pe-grid">
          {rows.map((row) => {
            const reg = row.registration;
            const active = ["confirmed", "pending_payment", "waitlisted"].includes(reg.status);
            return (
              <li key={reg.registrationId} className="pe-card">
                <div className="pe-card-body">
                  <h2>{reg.eventTitle}</h2>
                  <p><StatusPill status={reg.status} /> {reg.paymentStatus === "refund_pending" && <span className="pe-pill pe-pill-closed">{t("pub.my.refundPending")}</span>}</p>
                  <p className="pe-meta">{row.event?.startsAt ? formatJst(row.event.startsAt) : ""}</p>
                  <p>{t("pub.done.idLabel")}: <strong className="pe-id">{reg.registrationId}</strong></p>
                  <ul>{reg.tickets.map((l) => <li key={l.ticketTypeId}>{l.name} × {l.qty}</li>)}</ul>
                  <RegistrationAddons reg={reg} />
                  <p>{t("pub.total")}: <strong>{formatJpy(reg.totalJpy)}</strong></p>
                  {row.qr && <Qr value={row.qr} />}
                  {reg.status === "pending_payment" && (
                    <button type="button" className="button secondary"
                      onClick={() => navigateTo(`/events/${reg.eventSlug}/registered?reg=${encodeURIComponent(reg.registrationId)}&token=${encodeURIComponent(reg.checkInToken)}`)}>
                      {t("pub.my.paymentDetails")}
                    </button>
                  )}
                  {active && (
                    <div className="btn-row">
                      <button type="button" className="button secondary" disabled={!row.canChange} onClick={() => { setNotice(""); setEditing(reg.registrationId); }}>{t("pub.my.edit")}</button>
                      <button type="button" className="button secondary" disabled={!row.canChange || busyId === reg.registrationId} onClick={() => cancel(row)}>{t("pub.my.cancel")}</button>
                    </div>
                  )}
                  {active && !row.canChange && <p className="pe-note">{t("pub.my.closedNote")}</p>}
                </div>
              </li>
            );
          })}
        </ul>
      )}
    </EventShell>
  );
}
