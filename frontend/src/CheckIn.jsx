import { useCallback, useEffect, useRef, useState } from "react";
import { hasAdminSession } from "./adminSession";
import { formatJst, t } from "./i18n";
import { getEvent, getRegDashboard, listEvents, listRegistrations, registrationAction, scanCheckIn } from "./eventPlatformApi";
import "./publicEvents.css";
import "./checkIn.css";

const SAME_CODE_PAUSE_MS = 3000;
const RESULT_TONE = { ok: "good", undone: "good", already: "warn", payment_due: "warn", not_checked_in: "warn", cancelled: "bad", waitlisted: "bad", wrong_event: "bad", not_found: "bad" };

function beep(tone) {
  try {
    navigator.vibrate?.(tone === "good" ? 80 : [120, 60, 120]);
  } catch {
    // not supported
  }
}

/** Camera QR scanner: the browser's BarcodeDetector when present, otherwise jsQR (lazy-loaded). */
function Scanner({ onCode, paused }) {
  const videoRef = useRef(null);
  const [state, setState] = useState("idle"); // idle | starting | running | error
  const [message, setMessage] = useState("");
  const streamRef = useRef(null);
  const frameRef = useRef(0);
  const lastRef = useRef({ code: "", at: 0 });
  const pausedRef = useRef(paused);
  pausedRef.current = paused;

  const stop = useCallback(() => {
    cancelAnimationFrame(frameRef.current);
    streamRef.current?.getTracks().forEach((track) => track.stop());
    streamRef.current = null;
    setState("idle");
  }, []);

  const start = async () => {
    setState("starting");
    setMessage("");
    try {
      if (!navigator.mediaDevices?.getUserMedia) throw new Error(t("checkin.noCamera"));
      const stream = await navigator.mediaDevices.getUserMedia({ video: { facingMode: { ideal: "environment" } }, audio: false });
      streamRef.current = stream;
      const video = videoRef.current;
      video.srcObject = stream;
      await video.play();
      const detector = "BarcodeDetector" in window ? new window.BarcodeDetector({ formats: ["qr_code"] }) : null;
      const jsQR = detector ? null : (await import("jsqr")).default;
      const canvas = document.createElement("canvas");
      const ctx = canvas.getContext("2d", { willReadFrequently: true });
      setState("running");
      let busy = false;
      const tick = async () => {
        frameRef.current = requestAnimationFrame(tick);
        if (busy || pausedRef.current || video.readyState < 2) return;
        busy = true;
        try {
          let code = "";
          if (detector) {
            code = (await detector.detect(video))[0]?.rawValue || "";
          } else {
            canvas.width = video.videoWidth;
            canvas.height = video.videoHeight;
            ctx.drawImage(video, 0, 0);
            const image = ctx.getImageData(0, 0, canvas.width, canvas.height);
            code = jsQR(image.data, image.width, image.height, { inversionAttempts: "dontInvert" })?.data || "";
          }
          const now = Date.now();
          if (code && (code !== lastRef.current.code || now - lastRef.current.at > SAME_CODE_PAUSE_MS)) {
            lastRef.current = { code, at: now };
            onCode(code);
          }
        } catch {
          // a failed frame is ignored; the next one tries again
        } finally {
          busy = false;
        }
      };
      frameRef.current = requestAnimationFrame(tick);
    } catch (err) {
      stop();
      setState("error");
      setMessage(err.name === "NotAllowedError" ? t("checkin.cameraDenied") : err.message || t("checkin.noCamera"));
    }
  };

  useEffect(() => stop, [stop]);

  return (
    <div className="ci-scanner">
      <video ref={videoRef} muted playsInline aria-label={t("checkin.cameraView")} className={state === "running" ? "ci-video on" : "ci-video"} />
      {state !== "running" && (
        <button type="button" className="button primary ci-big" onClick={start} disabled={state === "starting"}>
          {state === "starting" ? t("pub.submitting") : t("checkin.startCamera")}
        </button>
      )}
      {state === "running" && <button type="button" className="button secondary" onClick={stop}>{t("checkin.stopCamera")}</button>}
      {message && <p className="error-banner" role="alert">{message}</p>}
    </div>
  );
}

function ResultCard({ outcome, onUndo }) {
  if (!outcome) return <div className="ci-result idle" aria-live="assertive"><p>{t("checkin.ready")}</p></div>;
  const tone = RESULT_TONE[outcome.result] || "bad";
  const reg = outcome.registration;
  return (
    <div className={`ci-result ${tone}`} role="status" aria-live="assertive">
      <p className="ci-headline">{t(`checkin.result.${outcome.result}`)}</p>
      {reg && (
        <>
          <p className="ci-name">{reg.fullName}</p>
          {reg.tickets && <p>{reg.tickets.map((x) => `${x.name} × ${x.qty}`).join(", ")}</p>}
          {reg.eventTitle && <p>{reg.eventTitle}</p>}
          {reg.checkedInAt && outcome.result === "already" && <p>{t("checkin.at", { when: formatJst(reg.checkedInAt) })}</p>}
          {outcome.result === "payment_due" && <p>{t("checkin.owes", { amount: `¥${Number(reg.totalJpy).toLocaleString("en-US")}` })}</p>}
          {reg.adminNote && <p className="ci-note">{reg.adminNote}</p>}
          {reg.registrationId && <p className="pe-id">{reg.registrationId}</p>}
        </>
      )}
      {outcome.result === "ok" && <button type="button" className="button secondary" onClick={() => onUndo(outcome.registration.registrationId)}>{t("checkin.undo")}</button>}
    </div>
  );
}

export default function CheckIn({ navigateTo }) {
  const eventId = new URLSearchParams(window.location.search).get("event") || "";
  const signedIn = hasAdminSession();
  const [event, setEvent] = useState(null);
  const [events, setEvents] = useState(null);
  const [stats, setStats] = useState(null);
  const [outcome, setOutcome] = useState(null);
  const [error, setError] = useState("");
  const [code, setCode] = useState("");
  const [query, setQuery] = useState("");
  const [matches, setMatches] = useState(null);
  const [busy, setBusy] = useState(false);

  const loadStats = useCallback(() => {
    if (eventId) getRegDashboard(eventId).then((r) => setStats(r.dashboard)).catch(() => {});
  }, [eventId]);

  useEffect(() => {
    if (!signedIn) return;
    if (eventId) {
      getEvent(eventId).then((r) => setEvent(r.event)).catch((e) => setError(e.message));
      loadStats();
    } else {
      listEvents().then((r) => setEvents(r.events.filter((e) => ["published", "unlisted", "closed"].includes(e.status)))).catch((e) => setError(e.message));
    }
  }, [signedIn, eventId, loadStats]);

  const handle = useCallback(async (raw, undo = false) => {
    setBusy(true);
    setError("");
    try {
      const result = await scanCheckIn(eventId, raw, undo);
      setOutcome(result);
      beep(RESULT_TONE[result.result] || "bad");
      if (["ok", "undone"].includes(result.result)) loadStats();
    } catch (e) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  }, [eventId, loadStats]);

  const search = async (e) => {
    e.preventDefault();
    if (!query.trim()) return setMatches(null);
    try {
      setMatches((await listRegistrations(eventId, { q: query, pageSize: 20, status: "confirmed,pending_payment" })).rows);
    } catch (err) {
      setError(err.message);
    }
    return undefined;
  };

  const checkInRow = async (row) => {
    setBusy(true);
    try {
      const result = await registrationAction(eventId, row.registrationId, "check-in", {});
      setOutcome(result);
      beep(RESULT_TONE[result.result] || "bad");
      if (result.result === "ok") { loadStats(); setMatches(null); setQuery(""); }
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  };

  const shell = (children) => (
    <main className="ci-page admin-dashboard">
      <div className="ep-toolbar">
        <h1>{t("checkin.title")}</h1>
        <div className="ep-spacer" />
        <button type="button" className="button secondary" onClick={() => navigateTo(eventId ? "/admin/event-platform" : "/admin")}>{t("checkin.back")}</button>
      </div>
      {children}
    </main>
  );

  if (!signedIn) {
    return shell(<div className="ep-card"><p>{t("events.signInRequired")}</p><button type="button" className="button primary" onClick={() => navigateTo("/admin")}>{t("events.signIn")}</button></div>);
  }
  if (!eventId) {
    return shell(
      <>
        {error && <p className="error-banner" role="alert">{error}</p>}
        <p>{t("checkin.chooseEvent")}</p>
        {!events && !error && <p>{t("events.loading")}</p>}
        {events && events.length === 0 && <p>{t("events.none")}</p>}
        <ul className="ci-events">
          {(events || []).map((ev) => <li key={ev.eventId}><a className="button secondary" href={`/admin/check-in?event=${encodeURIComponent(ev.eventId)}`}>{ev.title}</a></li>)}
        </ul>
      </>
    );
  }
  return shell(
    <>
      <h2>{event?.title}</h2>
      {stats && (
        <p className="ci-counter" role="status" aria-label={t("checkin.counter")}>
          <strong>{stats.checkedInSeats}</strong> / {stats.confirmedSeats} {t("checkin.checkedIn")}
          {stats.needsAttention.awaitingPayment > 0 && <span className="pe-pill pe-pill-pending_payment">{t("checkin.unpaid", { n: stats.needsAttention.awaitingPayment })}</span>}
        </p>
      )}
      {error && <p className="error-banner" role="alert">{error}</p>}
      <ResultCard outcome={outcome} onUndo={(id) => handle(id, true)} />
      <Scanner onCode={(c) => handle(c)} paused={busy} />
      <form className="ci-manual" onSubmit={(e) => { e.preventDefault(); if (code.trim()) { handle(code.trim()); setCode(""); } }}>
        <div className="field">
          <label htmlFor="ci-code">{t("checkin.typeId")}</label>
          <input id="ci-code" value={code} onChange={(e) => setCode(e.target.value)} autoCapitalize="characters" autoComplete="off" placeholder="WMF-XXXX-XXXX" />
        </div>
        <button type="submit" className="button primary" disabled={busy}>{t("checkin.submit")}</button>
      </form>
      <form className="ci-manual" role="search" onSubmit={search}>
        <div className="field">
          <label htmlFor="ci-search">{t("checkin.search")}</label>
          <input id="ci-search" type="search" value={query} onChange={(e) => setQuery(e.target.value)} placeholder={t("checkin.searchHint")} />
        </div>
        <button type="submit" className="button secondary">{t("reg.search")}</button>
      </form>
      {matches && (
        <ul className="ci-matches">
          {matches.length === 0 && <li>{t("reg.none")}</li>}
          {matches.map((r) => (
            <li key={r.registrationId}>
              <div><strong>{r.fullName}</strong> <span className="pe-id">{r.registrationId}</span><br /><small>{r.email} · {r.tickets.map((x) => `${x.name} × ${x.qty}`).join(", ")}{r.checkedInAt ? ` · ${t("checkin.alreadyIn")}` : ""}</small></div>
              <button type="button" className="button primary" disabled={busy} onClick={() => checkInRow(r)}>{t("checkin.checkInButton")}</button>
            </li>
          ))}
        </ul>
      )}
    </>
  );
}
