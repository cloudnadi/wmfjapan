import { useEffect, useMemo, useState } from "react";
import { EventCard } from "./EventsList";
import Navbar from "./Navbar";
import SiteFooter from "./SiteFooter";
import { listPublicEvents } from "./eventsApi";
import { t } from "./i18n";
import { siteConfig } from "./siteConfig";
import "./publicEvents.css";
import "./site.css";

const MAX_ON_HOME = 6;

/** Home: the events the platform is currently offering (nothing is hard-coded). */
export default function HomePage({ navigateTo }) {
  const [events, setEvents] = useState(null);
  const [error, setError] = useState("");
  const [attempt, setAttempt] = useState(0);
  const [query, setQuery] = useState("");
  const [category, setCategory] = useState("");

  useEffect(() => {
    let cancelled = false;
    setEvents(null);
    setError("");
    listPublicEvents("upcoming")
      .then((d) => !cancelled && setEvents(Array.isArray(d.events) ? d.events : []))
      .catch(() => !cancelled && setError(t("home.error")));
    return () => {
      cancelled = true;
    };
  }, [attempt]);

  const categories = useMemo(() => [...new Set((events || []).map((e) => e.category).filter(Boolean))].sort(), [events]);
  const shown = useMemo(() => {
    const q = query.trim().toLowerCase();
    return (events || []).filter(
      (e) =>
        (!category || e.category === category) &&
        (!q || [e.title, e.category, e.shortDescription, e.location?.venueName, e.location?.address].some((v) => String(v || "").toLowerCase().includes(q)))
    );
  }, [events, query, category]);
  const openCount = (events || []).filter((e) => e.status === "published").length;

  return (
    <>
      <Navbar navigateTo={navigateTo} />
      <main id="top" className="pe-page-home">
        <section className="home-hero" aria-labelledby="home-title">
          <div className="home-hero-inner">
            <p className="home-eyebrow">{t("home.eyebrow", { org: siteConfig.orgName })}</p>
            <h1 id="home-title">{siteConfig.shortTitle}</h1>
            <p className="home-lead">{t("home.lead")}</p>
            {events && (
              <dl className="home-stats">
                <div><dt>{t("home.stats.upcoming")}</dt><dd>{events.length}</dd></div>
                <div><dt>{t("home.stats.open")}</dt><dd>{openCount}</dd></div>
              </dl>
            )}
          </div>
        </section>

        <section className="home-section" aria-labelledby="home-upcoming">
          <h2 id="home-upcoming">{t("home.upcoming")}</h2>
          {error && (
            <>
              <div className="error-banner" role="alert">{error}</div>
              <button type="button" className="button secondary" onClick={() => setAttempt((n) => n + 1)}>{t("home.retry")}</button>
            </>
          )}
          {!events && !error && <p role="status">{t("pub.loading")}</p>}
          {events && events.length === 0 && <p>{t("home.empty")}</p>}
          {events && events.length > 0 && (
            <div className="pe-toolbar home-filters">
              <div className="field pe-inline pe-grow">
                <label htmlFor="home-search">{t("home.search.label")}</label>
                <input id="home-search" type="search" value={query} placeholder={t("home.search.placeholder")} onChange={(e) => setQuery(e.target.value)} />
              </div>
              {categories.length > 0 && (
                <div className="field pe-inline">
                  <label htmlFor="home-category">{t("home.filter.category")}</label>
                  <select id="home-category" value={category} onChange={(e) => setCategory(e.target.value)}>
                    <option value="">{t("home.filter.all")}</option>
                    {categories.map((c) => <option key={c} value={c}>{c}</option>)}
                  </select>
                </div>
              )}
            </div>
          )}
          {events && events.length > 0 && shown.length === 0 && <p role="status">{t("home.noMatches")}</p>}
          {shown.length > 0 && (
            <>
              <ul className="pe-grid" aria-live="polite">
                {shown.slice(0, MAX_ON_HOME).map((ev) => <EventCard key={ev.eventId} ev={ev} navigateTo={navigateTo} />)}
              </ul>
              <p><button type="button" className="button secondary" onClick={() => navigateTo("/events")}>{t("home.viewAll")}</button></p>
            </>
          )}
        </section>

      </main>
      <SiteFooter navigateTo={navigateTo} />
    </>
  );
}
