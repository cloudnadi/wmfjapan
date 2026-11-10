import { Fragment, useCallback, useEffect, useMemo, useState } from "react";
import "./publicEvents.css";
import DynamicForm from "./DynamicForm";
import { AddRegistration, EmailPanel } from "./RegistrationTools";
import { formatJpy, formatJst, t } from "./i18n";
import {
  downloadExport,
  editRegistration,
  exportRegistrations,
  getRegDashboard,
  getRegistration,
  listRegistrations,
  registrationAction,
  getRegistrationFileLink,
} from "./eventPlatformApi";

const STATUSES = ["confirmed", "pending_payment", "waitlisted", "cancelled"];
const PAYMENTS = ["none", "pending", "paid", "refund_pending", "refunding", "refunded"];

function Banner({ message, kind = "error" }) {
  return message ? <div className={kind === "error" ? "error-banner" : "success-banner"} role={kind === "error" ? "alert" : "status"}>{message}</div> : null;
}

function Pill({ value, prefix }) {
  return <span className={`pe-pill pe-pill-${value}`}>{t(`${prefix}.${value}`)}</span>;
}

function flatten(v) {
  if (v === null || v === undefined) return "";
  if (typeof v === "boolean") return v ? t("reg.yes") : t("reg.no");
  if (Array.isArray(v)) return v.map(flatten).join(" | ");
  if (typeof v === "object") return v.fileId ? v.name : Object.entries(v).map(([k, x]) => `${k}=${flatten(x)}`).join("; ");
  return String(v);
}

// ── dashboard ─────────────────────────────────────────────────────────────────

function Dashboard({ data, onFilter }) {
  if (!data) return <p>{t("events.loading")}</p>;
  const c = data.capacity;
  const a = data.needsAttention;
  return (
    <section aria-labelledby="reg-dash-h" className="reg-dash">
      <h3 id="reg-dash-h" className="ep-sr-only">{t("reg.dash.title")}</h3>
      <div className="reg-cards">
        <div className="reg-card">
          <p className="reg-card-label">{t("reg.dash.registrations")}</p>
          <p className="reg-card-value">{data.total}</p>
          <p className="ep-note">{STATUSES.map((s) => `${t(`pub.status.${s}`)} ${data.registrations[s] || 0}`).join(" · ")}</p>
        </div>
        <div className="reg-card">
          <p className="reg-card-label">{t("reg.dash.seats")}</p>
          <p className="reg-card-value">{c.used}{c.limit ? ` / ${c.limit}` : ""}</p>
          {c.limit ? <div className="reg-bar" role="progressbar" aria-valuemin="0" aria-valuemax={c.limit} aria-valuenow={c.used} aria-label={t("reg.dash.seats")}><span style={{ width: `${Math.min(100, c.percent)}%` }} /></div> : <p className="ep-note">{t("reg.dash.noLimit")}</p>}
          <p className="ep-note">{t("reg.dash.waitSeats", { n: data.seats.waitlisted })}</p>
        </div>
        <div className="reg-card">
          <p className="reg-card-label">{t("reg.dash.revenue")}</p>
          <p className="reg-card-value">{formatJpy(data.revenue.paidJpy)}</p>
          <p className="ep-note">{t("reg.dash.pending", { amount: formatJpy(data.revenue.pendingJpy) })}</p>
          {(data.revenue.refundPendingJpy > 0 || data.revenue.refundedJpy > 0) && (
            <p className="ep-note">{t("reg.dash.refunds", { pending: formatJpy(data.revenue.refundPendingJpy), done: formatJpy(data.revenue.refundedJpy) })}</p>
          )}
        </div>
        <div className="reg-card">
          <p className="reg-card-label">{t("reg.dash.checkedIn")}</p>
          <p className="reg-card-value">{data.checkedInSeats} / {data.confirmedSeats}</p>
          <p className="ep-note">{t("reg.dash.checkedInNote")}</p>
        </div>
      </div>
      {(a.refundPending > 0 || a.awaitingPayment > 0 || a.waitlisted > 0) && (
        <p className="reg-attention" aria-label={t("reg.dash.attention")}>
          {a.refundPending > 0 && <button type="button" className="pe-pill pe-pill-closed" onClick={() => onFilter({ payment: "refund_pending,refunding" })}>{t("reg.dash.refundPending", { n: a.refundPending })}</button>}
          {a.awaitingPayment > 0 && <button type="button" className="pe-pill pe-pill-pending_payment" onClick={() => onFilter({ status: "pending_payment" })}>{t("reg.dash.awaiting", { n: a.awaitingPayment })}</button>}
          {a.waitlisted > 0 && <button type="button" className="pe-pill pe-pill-waitlisted" onClick={() => onFilter({ status: "waitlisted" })}>{t("reg.dash.waiting", { n: a.waitlisted })}</button>}
        </p>
      )}
      <div className="table-container">
        <table className="registrations-table ep-table">
          <thead><tr><th>{t("pub.tickets")}</th><th>{t("pub.status.confirmed")}</th><th>{t("pub.status.pending_payment")}</th><th>{t("pub.status.waitlisted")}</th><th>{t("reg.dash.revenue")}</th></tr></thead>
          <tbody>
            {data.tickets.map((x) => (
              <tr key={x.ticketTypeId}>
                <td data-label={t("pub.tickets")}>{x.name}</td>
                <td data-label={t("pub.status.confirmed")}>{x.confirmed}</td>
                <td data-label={t("pub.status.pending_payment")}>{x.pending}</td>
                <td data-label={t("pub.status.waitlisted")}>{x.waitlisted}</td>
                <td data-label={t("reg.dash.revenue")}>{formatJpy(x.revenueJpy)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}

// ── detail ────────────────────────────────────────────────────────────────────

function Detail({ eventId, rid, roles = [], onClose, onChanged }) {
  const [data, setData] = useState(null);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [busy, setBusy] = useState(false);
  const [editing, setEditing] = useState(false);
  const [answers, setAnswers] = useState({});
  const [fieldErrors, setFieldErrors] = useState({});
  const [note, setNote] = useState("");
  const [via, setVia] = useState("bank");
  const [reference, setReference] = useState("");
  const [reason, setReason] = useState("");
  const [refundMode, setRefundMode] = useState("manual");
  const [refundNote, setRefundNote] = useState("");

  const load = useCallback(async () => {
    try {
      const d = await getRegistration(eventId, rid);
      setData(d);
      setAnswers(d.registration.answers);
      setNote(d.registration.adminNote || "");
    } catch (e) {
      setError(e.message);
    }
  }, [eventId, rid]);
  useEffect(() => { setData(null); setEditing(false); setNotice(""); setError(""); load(); }, [load]);

  const run = async (fn, okMessage) => {
    setBusy(true);
    setError("");
    setNotice("");
    try {
      const res = await fn();
      if (res.result && !["ok", "undone"].includes(res.result)) setError(t(`reg.checkin.${res.result}`));
      else setNotice(okMessage);
      await load();
      onChanged();
    } catch (e) {
      setError(e.message);
      setFieldErrors(e.fields || {});
    } finally {
      setBusy(false);
    }
  };

  if (!data) return error ? <Banner message={error} /> : <p>{t("events.loading")}</p>;
  const reg = data.registration;
  const act = (name, payload, msg) => run(() => registrationAction(eventId, rid, name, payload), msg);
  const labelOf = Object.fromEntries(data.form.fields.map((f) => [f.key, f.label]));
  const answerKeys = Object.keys(reg.answers);
  const isSuper = roles.includes("super_admin");
  const canErase = isSuper && !reg.anonymizedAt && !["refund_pending", "refunding"].includes(reg.paymentStatus);
  const openFile = async (fileId) => {
    try {
      const link = await getRegistrationFileLink(eventId, rid, fileId);
      window.open(link.url, "_blank", "noopener");
    } catch (e) {
      setError(e.message);
    }
  };
  const answerView = (v) => (v && typeof v === "object" && !Array.isArray(v) && v.fileId
    ? <button type="button" className="button secondary" onClick={() => openFile(v.fileId)}>{t("reg.detail.download", { name: v.name || "file" })}</button>
    : flatten(v) || "—");

  return (
    <section className="ep-card reg-detail" aria-labelledby="reg-detail-h">
      <div className="ep-toolbar">
        <h3 id="reg-detail-h">{reg.fullName} <small className="pe-id">{reg.registrationId}</small></h3>
        <div className="ep-spacer" />
        <button type="button" className="button secondary" onClick={onClose}>{t("reg.close")}</button>
      </div>
      <Banner message={error} />
      <Banner message={notice} kind="ok" />
      <p><Pill value={reg.status} prefix="pub.status" /> <Pill value={reg.paymentStatus || "none"} prefix="reg.payment" />{reg.checkedInAt && <span className="pe-pill pe-pill-confirmed">{t("reg.detail.checkedInAt", { when: formatJst(reg.checkedInAt) })}</span>}{reg.addedByAdmin && <span className="pe-pill">{t("reg.detail.addedByAdmin")}</span>}</p>
      {reg.paymentMismatch && <Banner message={t("reg.detail.mismatch", { expected: formatJpy(reg.paymentMismatch.expectedJpy), reported: formatJpy(reg.paymentMismatch.reportedJpy) })} />}
      {reg.anonymizedAt && <p className="ep-note" role="note">{t("reg.detail.erasedNote", { when: formatJst(reg.anonymizedAt) })}</p>}
      <dl className="pe-summary">
        <dt>{t("pub.tickets")}</dt><dd><ul>{reg.tickets.map((l) => <li key={l.ticketTypeId}>{l.name} × {l.qty} — {formatJpy(l.subtotalJpy)}</li>)}</ul></dd>
        <dt>{t("pub.total")}</dt><dd><strong>{formatJpy(reg.totalJpy)}</strong>{reg.paymentMethod && reg.paymentMethod !== "none" ? ` (${reg.paymentMethod})` : ""}</dd>
        <dt>{t("reg.detail.registered")}</dt><dd>{formatJst(reg.createdAt)}{reg.createdBy ? ` · ${reg.createdBy}` : ""}</dd>
        {reg.holdUntil && <><dt>{t("reg.detail.heldUntil")}</dt><dd>{formatJst(reg.holdUntil)}</dd></>}
        {reg.paidAt && <><dt>{t("reg.detail.paid")}</dt><dd>{formatJst(reg.paidAt)} · {reg.paidVia}{reg.paymentReference ? ` · ${reg.paymentReference}` : ""}</dd></>}
        {reg.refundedAt && <><dt>{t("reg.detail.refunded")}</dt><dd>{formatJst(reg.refundedAt)} · {reg.refundMode}{reg.refundNote ? ` · ${reg.refundNote}` : ""}{reg.refundReference ? ` · ${reg.refundReference}` : ""}</dd></>}
        {reg.cancelledAt && <><dt>{t("reg.detail.cancelled")}</dt><dd>{formatJst(reg.cancelledAt)} · {reg.cancelledBy} · {reg.cancelReason}</dd></>}
        {reg.memberEmail && <><dt>{t("reg.detail.member")}</dt><dd>{reg.memberEmail}</dd></>}
        <dt>{t("reg.detail.formVersion")}</dt><dd>v{reg.formVersion}</dd>
      </dl>

      <h4>{t("reg.detail.answers")}</h4>
      {editing ? (
        <>
          <DynamicForm fields={data.form.fields} answers={answers} idPrefix="rd" errors={fieldErrors} readOnlyKeys={reg.memberEmail ? ["email"] : []}
            system={reg.answers} onChange={(k, v) => setAnswers((a) => ({ ...a, [k]: v }))} />
          <div className="field">
            <label htmlFor="rd-note">{t("reg.add.note")}</label>
            <input id="rd-note" value={note} maxLength={1000} onChange={(e) => setNote(e.target.value)} />
          </div>
          <div className="btn-row">
            <button type="button" className="button primary" disabled={busy}
              onClick={() => run(async () => { const r = await editRegistration(eventId, rid, { revision: reg.revision, answers, adminNote: note }); setEditing(false); return r; }, t("reg.detail.saved"))}>{t("events.save")}</button>
            <button type="button" className="button secondary" onClick={() => { setEditing(false); setAnswers(reg.answers); setFieldErrors({}); }}>{t("events.cancel")}</button>
          </div>
        </>
      ) : (
        <>
          <dl className="pe-summary">
            {answerKeys.map((k) => <Fragment key={k}><dt>{labelOf[k] || k}</dt><dd>{answerView(reg.answers[k])}</dd></Fragment>)}
          </dl>
          {reg.adminNote && <p><strong>{t("reg.add.note")}:</strong> {reg.adminNote}</p>}
          <button type="button" className="button secondary" onClick={() => setEditing(true)}>{t("reg.detail.edit")}</button>
        </>
      )}

      <h4>{t("reg.detail.actions")}</h4>
      <div className="reg-actions">
        {reg.status === "confirmed" && !reg.checkedInAt && <button type="button" className="button primary" disabled={busy} onClick={() => act("check-in", {}, t("reg.detail.checkedIn"))}>{t("reg.detail.checkIn")}</button>}
        {reg.checkedInAt && <button type="button" className="button secondary" disabled={busy} onClick={() => act("check-in", { undo: true }, t("reg.detail.undone"))}>{t("reg.detail.undoCheckIn")}</button>}
        {reg.status === "waitlisted" && <button type="button" className="button primary" disabled={busy} onClick={() => act("promote", {}, t("reg.detail.promoted"))}>{t("reg.detail.promote")}</button>}
        {reg.status === "pending_payment" && reg.paymentStatus === "pending" && (
          <fieldset className="ep-fieldset reg-inline">
            <legend>{t("reg.detail.markPaid")}</legend>
            <select aria-label={t("reg.add.paidVia")} value={via} onChange={(e) => setVia(e.target.value)}>{["bank", "paypay", "cash", "square", "other"].map((v) => <option key={v} value={v}>{t(`reg.via.${v}`)}</option>)}</select>
            <input aria-label={t("reg.detail.reference")} placeholder={t("reg.detail.reference")} value={reference} onChange={(e) => setReference(e.target.value)} />
            <button type="button" className="button primary" disabled={busy} onClick={() => act("mark-paid", { via, reference }, t("reg.detail.markedPaid"))}>{t("reg.detail.markPaid")}</button>
          </fieldset>
        )}
        {["confirmed", "pending_payment", "waitlisted"].includes(reg.status) && (
          <fieldset className="ep-fieldset reg-inline">
            <legend>{t("reg.detail.cancel")}</legend>
            <input aria-label={t("reg.detail.reason")} placeholder={t("reg.detail.reason")} value={reason} onChange={(e) => setReason(e.target.value)} />
            <button type="button" className="button secondary" disabled={busy}
              onClick={() => window.confirm(t(reg.paymentStatus === "paid" ? "reg.detail.cancelPaidConfirm" : "reg.detail.cancelConfirm")) && act("cancel", { reason }, t("reg.detail.cancelledDone"))}>{t("reg.detail.cancel")}</button>
          </fieldset>
        )}
        {reg.paymentStatus === "refund_pending" && (
          <fieldset className="ep-fieldset reg-inline">
            <legend>{t("reg.detail.refund")}</legend>
            <select aria-label={t("reg.detail.refundHow")} value={refundMode} onChange={(e) => setRefundMode(e.target.value)}>
              <option value="manual">{t("reg.detail.refundManual")}</option>
              <option value="square" disabled={reg.paidVia !== "square"}>{t("reg.detail.refundSquare")}</option>
            </select>
            <input aria-label={t("reg.detail.refundNote")} placeholder={t("reg.detail.refundNote")} value={refundNote} onChange={(e) => setRefundNote(e.target.value)} />
            <button type="button" className="button primary" disabled={busy}
              onClick={() => window.confirm(t("reg.detail.refundConfirm", { amount: formatJpy(reg.totalJpy) })) && act("refund", { mode: refundMode, note: refundNote }, t("reg.detail.refundDone"))}>{t("reg.detail.refund")}</button>
          </fieldset>
        )}
        {canErase && (
          <button type="button" className="button secondary" disabled={busy}
            onClick={() => window.confirm(t("reg.detail.eraseConfirm")) && act("anonymize", {}, t("reg.detail.erased"))}>{t("reg.detail.erase")}</button>
        )}
        {reg.paymentStatus === "refunding" && <p className="ep-note" role="note">{t("reg.detail.refunding")}</p>}
      </div>

      <h4>{t("events.tab.history")}</h4>
      {data.history.length === 0 ? <p>{t("events.history.none")}</p> : (
        <ul className="ep-history">{data.history.map((h) => <li key={h.sortKey}><strong>{formatJst(h.at)}</strong> — {h.actor}: {h.summary}</li>)}</ul>
      )}
    </section>
  );
}

// ── tab ───────────────────────────────────────────────────────────────────────

export default function EventRegistrations({ eventId, event, tickets, roles }) {
  const [dash, setDash] = useState(null);
  const [data, setData] = useState(null);
  const [params, setParams] = useState({ sort: "createdAt", dir: "desc", page: 1, pageSize: 25 });
  const [search, setSearch] = useState("");
  const [fieldFilter, setFieldFilter] = useState({ key: "", value: "" });
  const [extra, setExtra] = useState([]);
  const [selected, setSelected] = useState("");
  const [panel, setPanel] = useState(""); // "" | add | email
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [busy, setBusy] = useState(false);

  const reloadDash = useCallback(() => { getRegDashboard(eventId).then((r) => setDash(r.dashboard)).catch((e) => setError(e.message)); }, [eventId]);
  const apiParams = useMemo(() => {
    const p = { ...params };
    if (fieldFilter.key && fieldFilter.value) p[`f.${fieldFilter.key}`] = fieldFilter.value;
    return p;
  }, [params, fieldFilter]);
  const reload = useCallback(() => {
    listRegistrations(eventId, apiParams).then((r) => { setData(r); setError(""); }).catch((e) => setError(e.message));
  }, [eventId, apiParams]);

  useEffect(() => { reloadDash(); }, [reloadDash]);
  useEffect(() => { reload(); }, [reload]);
  useEffect(() => {
    const id = setTimeout(() => setParams((p) => (p.q === (search || undefined) ? p : { ...p, q: search || undefined, page: 1 })), 300);
    return () => clearTimeout(id);
  }, [search]);

  const set = (patch) => setParams((p) => ({ ...p, page: 1, ...patch }));
  const sortBy = (id) => setParams((p) => ({ ...p, sort: id, dir: p.sort === id && p.dir === "asc" ? "desc" : "asc", page: 1 }));
  const sortMark = (id) => (params.sort === id ? (params.dir === "asc" ? " ▲" : " ▼") : "");
  const ariaSort = (id) => (params.sort === id ? (params.dir === "asc" ? "ascending" : "descending") : "none");
  const refreshAll = () => { reload(); reloadDash(); };
  const columns = data?.columns || [];
  const choiceColumns = columns.filter((c) => ["dropdown", "radio", "checkbox", "text", "multiselect"].includes(c.type));

  const doExport = async (format) => {
    setBusy(true);
    setError("");
    try {
      const { page, pageSize, ...filters } = apiParams;
      downloadExport(await exportRegistrations(eventId, { ...filters, format }));
    } catch (e) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  };

  const head = (id, label) => (
    <th key={id} scope="col" aria-sort={ariaSort(id)}>
      <button type="button" className="ep-link reg-sort" onClick={() => sortBy(id)}>{label}{sortMark(id)}</button>
    </th>
  );

  return (
    <section aria-labelledby="reg-h" className="reg-tab">
      <div className="ep-toolbar">
        <h3 id="reg-h">{t("reg.title")}</h3>
        <div className="ep-spacer" />
        <a className="button secondary" href={`/admin/check-in?event=${encodeURIComponent(eventId)}`}>{t("reg.checkin.open")}</a>
        <button type="button" className="button secondary" onClick={() => setPanel(panel === "email" ? "" : "email")} aria-expanded={panel === "email"}>{t("reg.email.open")}</button>
        <button type="button" className="button primary" onClick={() => setPanel(panel === "add" ? "" : "add")} aria-expanded={panel === "add"}>{t("reg.add.open")}</button>
      </div>
      <Banner message={error} />
      <Banner message={notice} kind="ok" />
      {panel === "add" && <AddRegistration eventId={eventId} tickets={tickets} onCancel={() => setPanel("")}
        onDone={(r) => { setPanel(""); setNotice(t("reg.add.done", { id: r.registrationId })); refreshAll(); setSelected(r.registrationId); }} />}
      {panel === "email" && <EmailPanel eventId={eventId} event={event} onClose={() => setPanel("")} />}

      <Dashboard data={dash} onFilter={(f) => { setSearch(""); setFieldFilter({ key: "", value: "" }); setParams({ sort: "createdAt", dir: "desc", page: 1, pageSize: params.pageSize, ...f }); }} />

      <form className="reg-filters" role="search" aria-label={t("reg.filters")} onSubmit={(e) => e.preventDefault()}>
        <div className="field"><label htmlFor="rf-q">{t("reg.search")}</label><input id="rf-q" type="search" value={search} onChange={(e) => setSearch(e.target.value)} placeholder={t("reg.searchHint")} /></div>
        <div className="field"><label htmlFor="rf-status">{t("reg.col.status")}</label>
          <select id="rf-status" value={params.status || ""} onChange={(e) => set({ status: e.target.value || undefined })}><option value="">{t("events.all")}</option>{STATUSES.map((s) => <option key={s} value={s}>{t(`pub.status.${s}`)}</option>)}</select></div>
        <div className="field"><label htmlFor="rf-pay">{t("reg.col.payment")}</label>
          <select id="rf-pay" value={params.payment || ""} onChange={(e) => set({ payment: e.target.value || undefined })}><option value="">{t("events.all")}</option>{PAYMENTS.map((s) => <option key={s} value={s}>{t(`reg.payment.${s}`)}</option>)}</select></div>
        <div className="field"><label htmlFor="rf-ticket">{t("pub.tickets")}</label>
          <select id="rf-ticket" value={params.ticket || ""} onChange={(e) => set({ ticket: e.target.value || undefined })}><option value="">{t("events.all")}</option>{(data?.tickets || []).map((x) => <option key={x.ticketTypeId} value={x.ticketTypeId}>{x.name}</option>)}</select></div>
        <div className="field"><label htmlFor="rf-ci">{t("reg.col.checkedIn")}</label>
          <select id="rf-ci" value={params.checkedIn || ""} onChange={(e) => set({ checkedIn: e.target.value || undefined })}><option value="">{t("events.all")}</option><option value="yes">{t("reg.yes")}</option><option value="no">{t("reg.no")}</option></select></div>
        <div className="field"><label htmlFor="rf-field">{t("reg.filterField")}</label>
          <select id="rf-field" value={fieldFilter.key} onChange={(e) => { setFieldFilter({ key: e.target.value, value: "" }); setParams((p) => ({ ...p, page: 1 })); }}><option value="">—</option>{choiceColumns.map((c) => <option key={c.key} value={c.key}>{c.label}</option>)}</select></div>
        {fieldFilter.key && <div className="field"><label htmlFor="rf-fval">{t("reg.filterValue")}</label><input id="rf-fval" value={fieldFilter.value} onChange={(e) => setFieldFilter({ ...fieldFilter, value: e.target.value })} /></div>}
      </form>

      <div className="ep-toolbar">
        <details className="reg-columns">
          <summary>{t("reg.columns")}</summary>
          <div>{columns.filter((c) => !["fullName", "email"].includes(c.key)).map((c) => (
            <label key={c.key} className="ep-choice"><input type="checkbox" checked={extra.includes(c.id)} onChange={(e) => setExtra(e.target.checked ? [...extra, c.id] : extra.filter((x) => x !== c.id))} /> {c.label}</label>
          ))}</div>
        </details>
        <div className="ep-spacer" />
        <button type="button" className="button secondary" disabled={busy} onClick={() => doExport("csv")}>{t("reg.export.csv")}</button>
        <button type="button" className="button secondary" disabled={busy} onClick={() => doExport("xlsx")}>{t("reg.export.xlsx")}</button>
      </div>

      {!data && !error && <p>{t("events.loading")}</p>}
      {data && (
        <>
          <p className="ep-note" role="status">{t("reg.count", { n: data.total })}</p>
          <div className="table-container">
            <table className="registrations-table ep-table reg-table">
              <thead>
                <tr>
                  {head("registrationId", t("reg.col.id"))}{head("fullName", t("reg.col.name"))}{head("email", t("reg.col.email"))}
                  {head("status", t("reg.col.status"))}{head("seats", t("pub.tickets"))}{head("totalJpy", t("pub.total"))}
                  {head("paymentStatus", t("reg.col.payment"))}{head("createdAt", t("reg.col.registered"))}{head("checkedInAt", t("reg.col.checkedIn"))}
                  {extra.map((id) => head(id, columns.find((c) => c.id === id)?.label || id))}
                </tr>
              </thead>
              <tbody>
                {data.rows.length === 0 && <tr><td colSpan={9 + extra.length}>{t("reg.none")}</td></tr>}
                {data.rows.map((r) => (
                  <tr key={r.registrationId} className={selected === r.registrationId ? "reg-selected" : ""}>
                    <td data-label={t("reg.col.id")}><button type="button" className="ep-link pe-id" onClick={() => setSelected(r.registrationId)}>{r.registrationId}</button></td>
                    <td data-label={t("reg.col.name")}>{r.fullName}</td>
                    <td data-label={t("reg.col.email")}>{r.email}</td>
                    <td data-label={t("reg.col.status")}><Pill value={r.status} prefix="pub.status" /></td>
                    <td data-label={t("pub.tickets")}>{r.tickets.map((l) => `${l.name} × ${l.qty}`).join(", ")}</td>
                    <td data-label={t("pub.total")}>{formatJpy(r.totalJpy)}</td>
                    <td data-label={t("reg.col.payment")}><Pill value={r.paymentStatus || "none"} prefix="reg.payment" /></td>
                    <td data-label={t("reg.col.registered")}>{formatJst(r.createdAt)}</td>
                    <td data-label={t("reg.col.checkedIn")}>{r.checkedInAt ? formatJst(r.checkedInAt) : "—"}</td>
                    {extra.map((id) => <td key={id} data-label={columns.find((c) => c.id === id)?.label}>{flatten(r.answers?.[id.slice(2)]) || "—"}</td>)}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <div className="ep-toolbar" role="navigation" aria-label={t("reg.pages")}>
            <button type="button" className="button secondary" disabled={data.page <= 1} onClick={() => setParams({ ...params, page: data.page - 1 })}>{t("reg.prev")}</button>
            <span>{t("reg.page", { page: data.page, pages: data.pages })}</span>
            <button type="button" className="button secondary" disabled={data.page >= data.pages} onClick={() => setParams({ ...params, page: data.page + 1 })}>{t("reg.next")}</button>
            <div className="ep-spacer" />
            <label htmlFor="rf-size" className="ep-inline-label">{t("reg.pageSize")}</label>
            <select id="rf-size" value={params.pageSize} onChange={(e) => set({ pageSize: Number(e.target.value) })}>{[10, 25, 50, 100, 200].map((n) => <option key={n} value={n}>{n}</option>)}</select>
          </div>
        </>
      )}
      {selected && <Detail eventId={eventId} rid={selected} roles={roles} onClose={() => setSelected("")} onChanged={refreshAll} />}
    </section>
  );
}
