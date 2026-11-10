import { useCallback, useEffect, useRef, useState } from "react";
import QRCode from "qrcode";
import EventShell, { ErrorBox, Loading, StatusPill } from "./EventShell";
import { getRegistrationByToken, payRegistration } from "./eventsApi";
import { formatJpy, formatJst, t } from "./i18n";
import { isRegistrantAuthenticated } from "./registrantAuth";

const POLL_MS = 5000;
const POLL_FOR_MS = 3 * 60 * 1000;

function Qr({ value }) {
  const ref = useRef(null);
  useEffect(() => {
    if (ref.current && value) QRCode.toCanvas(ref.current, value, { width: 220, margin: 2 }).catch(() => {});
  }, [value]);
  return <canvas ref={ref} role="img" aria-label={t("pub.done.qrAlt")} className="pe-qr" />;
}

export default function EventRegistered({ navigateTo, slug }) {
  const params = new URLSearchParams(window.location.search);
  const regId = params.get("reg") || "";
  const token = params.get("token") || "";
  const [data, setData] = useState(null);
  const [error, setError] = useState("");
  const [payBusy, setPayBusy] = useState(false);
  const started = useRef(Date.now());

  const load = useCallback(async () => {
    try {
      setData(await getRegistrationByToken(regId, token));
      setError("");
    } catch (e) {
      setError(e.status === 404 ? t("pub.done.notFound") : e.message);
    }
  }, [regId, token]);

  useEffect(() => { load(); }, [load]);

  // A card payment can finish a moment after the redirect: keep checking for a while.
  const status = data?.registration.status;
  useEffect(() => {
    if (status !== "pending_payment" || data?.registration.paymentMethod !== "square") return undefined;
    const id = setInterval(() => {
      if (Date.now() - started.current > POLL_FOR_MS) return clearInterval(id);
      return load();
    }, POLL_MS);
    return () => clearInterval(id);
  }, [status, data?.registration.paymentMethod, load]);

  const pay = async (method) => {
    setPayBusy(true);
    try {
      const res = await payRegistration(regId, token, method);
      if (res.payment.checkoutUrl && method === "square") return window.location.assign(res.payment.checkoutUrl);
      setData(res);
    } catch (e) {
      setError(e.message);
    } finally {
      setPayBusy(false);
    }
    return undefined;
  };

  if (error && !data) return <EventShell navigateTo={navigateTo}><ErrorBox message={error} /></EventShell>;
  if (!data) return <EventShell navigateTo={navigateTo}><Loading /></EventShell>;

  const { registration: reg, payment, event: ev } = data;
  const place = [ev?.location?.venueName, ev?.location?.address].filter(Boolean).join(", ");
  return (
    <EventShell navigateTo={navigateTo}>
      <h1>{reg.eventTitle}</h1>
      <ErrorBox message={error} />
      <section className="pe-box" aria-live="polite">
        <p><StatusPill status={reg.status} /></p>
        {reg.status === "confirmed" && <h2>{t("pub.done.confirmed")}</h2>}
        {reg.status === "pending_payment" && <h2>{t("pub.done.pending")}</h2>}
        {reg.status === "waitlisted" && <h2>{t("pub.done.waitlisted")}</h2>}
        {reg.status === "cancelled" && <h2>{t("pub.done.cancelled")}</h2>}
        <p>{t("pub.done.idLabel")}: <strong className="pe-id">{reg.registrationId}</strong></p>
        <p className="pe-note">{t("pub.done.emailed", { email: reg.email })}</p>

        {reg.status === "confirmed" && data.qr && (
          <div className="pe-qr-wrap"><Qr value={data.qr} /><p className="pe-note">{t("pub.done.showQr")}</p></div>
        )}

        {reg.status === "pending_payment" && (
          <div>
            <p>{t("pub.done.amountDue")}: <strong>{formatJpy(reg.totalJpy)}</strong></p>
            {payment.holdUntil && <p>{t("pub.done.heldUntil", { when: formatJst(payment.holdUntil) })}</p>}
            {payment.method === "square" && (
              <>
                <p role="status">{t("pub.done.confirmingCard")}</p>
                {payment.checkoutUrl && <a className="button primary" href={payment.checkoutUrl}>{t("pub.done.payNow")}</a>}
              </>
            )}
            {(payment.method === "bank" || payment.method === "paypay") && (
              <div className="pe-instructions">
                <h3>{t(payment.method === "bank" ? "pub.done.bank" : "pub.done.paypay")}</h3>
                {payment.instructions && <p style={{ whiteSpace: "pre-line" }}>{payment.instructions}</p>}
                {payment.url && <p><a href={payment.url} target="_blank" rel="noopener noreferrer">{payment.url}</a></p>}
                <p>{t("pub.done.quoteId", { id: reg.registrationId })}</p>
              </div>
            )}
            <div className="btn-row">
              <button type="button" className="button secondary" disabled={payBusy} onClick={() => pay("square")}>{t("pub.done.payByCard")}</button>
            </div>
          </div>
        )}

        {reg.status === "waitlisted" && <p>{t("pub.done.waitlistNote")}</p>}
      </section>

      <section className="pe-box">
        <h2>{t("pub.done.details")}</h2>
        <dl className="pe-summary">
          {ev?.startsAt && <><dt>{t("pub.when")}</dt><dd>{formatJst(ev.startsAt)}</dd></>}
          {place && <><dt>{t("pub.where")}</dt><dd>{place}</dd></>}
          {ev?.location?.onlineUrl && <><dt>{t("pub.done.online")}</dt><dd><a href={ev.location.onlineUrl} target="_blank" rel="noopener noreferrer">{ev.location.onlineUrl}</a></dd></>}
          <dt>{t("pub.tickets")}</dt>
          <dd><ul>{reg.tickets.map((l) => <li key={l.ticketTypeId}>{l.name} × {l.qty} — {formatJpy(l.subtotalJpy)}</li>)}</ul></dd>
          <dt>{t("pub.total")}</dt><dd><strong>{formatJpy(reg.totalJpy)}</strong></dd>
        </dl>
        <div className="btn-row">
          {ev && <button type="button" className="button secondary" onClick={() => navigateTo(`/events/${ev.slug}`)}>{t("pub.done.eventPage")}</button>}
          {reg.memberEmail && isRegistrantAuthenticated() && (
            <button type="button" className="button secondary" onClick={() => navigateTo("/my/events")}>{t("pub.done.myRegistrations")}</button>
          )}
          <button type="button" className="button secondary" onClick={() => window.print()}>{t("pub.done.print")}</button>
        </div>
      </section>
    </EventShell>
  );
}
