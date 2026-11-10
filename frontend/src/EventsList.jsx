import { useEffect, useState } from "react";
import EventShell, { ErrorBox, Loading } from "./EventShell";
import { listPublicEvents } from "./eventsApi";
import { normalizeCapabilities } from "./eventModel";
import { formatJst, t } from "./i18n";

function dateParts(iso) {
  const when = iso ? formatJst(iso) : "";
  const m = /^(\d{4})-(\d{2})-(\d{2})/.exec(iso ? new Date(iso).toLocaleDateString("en-CA", { timeZone: "Asia/Tokyo" }) : "");
  if (!m) return null;
  const month = new Date(`${m[1]}-${m[2]}-01T00:00:00Z`).toLocaleString("en-US", { month: "short", timeZone: "UTC" });
  return { day: String(Number(m[3])), month, when };
}

export function EventCard({ ev, navigateTo }) {
  const open = () => navigateTo(`/events/${ev.slug}`);
  const caps = normalizeCapabilities(ev);
  const place = [ev.location?.venueName, ev.location?.address].filter(Boolean).join(", ");
  const date = dateParts(ev.startsAt);
  return (
    <li className="pe-card" style={ev.accentColor ? { "--pe-accent": ev.accentColor } : undefined}>
      <div className="pe-card-media">
        {ev.coverImage ? <img src={ev.coverImage} alt="" loading="lazy" className="pe-card-cover" /> : <div className="pe-card-cover pe-card-cover-empty" aria-hidden="true" />}
        {date && <span className="pe-datebadge" aria-hidden="true"><b>{date.day}</b>{date.month}</span>}
      </div>
      <div className="pe-card-body">
        {ev.category && <p className="pe-eyebrow">{ev.category}</p>}
        <h2><button type="button" className="pe-link pe-card-link" onClick={open}>{ev.title}</button></h2>
        <p className="pe-meta">{ev.startsAt ? formatJst(ev.startsAt) : t("pub.dateTbd")}</p>
        {place && <p className="pe-meta">{place}</p>}
        {ev.shortDescription && <p className="pe-card-text">{ev.shortDescription}</p>}
        <p className="pe-badges">
          {ev.membersOnly && <span className="pe-pill pe-pill-accent">{t("pub.membersOnly")}</span>}
          {!ev.membersOnly && !caps.guestCheckout.enabled && <span className="pe-pill pe-pill-accent">{t("pub.signInRequiredBadge")}</span>}
          {["cancelled", "canceled", "archived"].includes(ev.status) && <span className="pe-pill pe-pill-closed">{t("pub.state.cancelled")}</span>}
          {ev.status === "closed" && <span className="pe-pill pe-pill-closed">{t("pub.registrationClosed")}</span>}
        </p>
        <button type="button" className="button primary pe-card-cta" onClick={open}>{t("pub.viewEvent")}</button>
      </div>
    </li>
  );
}

export default function EventsList({ navigateTo }) {
  const [filter, setFilter] = useState("upcoming");
  const [category, setCategory] = useState("");
  const [data, setData] = useState(null);
  const [error, setError] = useState("");
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    let cancelled = false;
    setData(null);
    setError("");
    listPublicEvents(filter, category)
      .then((d) => !cancelled && setData({ events: [], categories: [], ...d }))
      .catch((e) => !cancelled && setError(e.message || t("pub.error.generic")));
    return () => {
      cancelled = true;
    };
  }, [filter, category, attempt]);

  return (
    <EventShell navigateTo={navigateTo}>
      <header className="pe-pagehead">
        <h1>{t("pub.list.title")}</h1>
        <p className="pe-subtitle">{t("pub.list.subtitle")}</p>
      </header>
      <div className="pe-toolbar">
        <div className="admin-tabs" role="tablist" aria-label={t("pub.list.when")}>
          {["upcoming", "past"].map((f) => (
            <button key={f} type="button" role="tab" aria-selected={filter === f} className={`admin-tab ${filter === f ? "active" : ""}`} onClick={() => setFilter(f)}>
              {t(`pub.list.${f}`)}
            </button>
          ))}
        </div>
        {data?.categories?.length > 0 && (
          <div className="field pe-inline">
            <label htmlFor="pe-category">{t("pub.list.category")}</label>
            <select id="pe-category" value={category} onChange={(e) => setCategory(e.target.value)}>
              <option value="">{t("pub.list.allCategories")}</option>
              {data.categories.map((c) => <option key={c} value={c}>{c}</option>)}
            </select>
          </div>
        )}
      </div>
      <ErrorBox message={error} />
      {error && <button type="button" className="button secondary" onClick={() => setAttempt((n) => n + 1)}>{t("pub.retry")}</button>}
      {!data && !error && <Loading />}
      {data && data.events.length === 0 && <p>{t(filter === "past" ? "pub.list.nonePast" : "pub.list.noneUpcoming")}</p>}
      {data && data.events.length > 0 && (
        <ul className="pe-grid" aria-live="polite">
          {data.events.map((ev) => <EventCard key={ev.eventId} ev={ev} navigateTo={navigateTo} />)}
        </ul>
      )}
    </EventShell>
  );
}
