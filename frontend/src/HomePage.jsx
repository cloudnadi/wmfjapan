import { useEffect, useMemo, useState } from "react";
import AgmBanner from "./AgmBanner";
import { EventCard } from "./EventsList";
import Navbar from "./Navbar";
import SiteFooter from "./SiteFooter";
import { getSiteConfig } from "./api";
import { listPublicEvents } from "./eventsApi";
import { t } from "./i18n";
import { siteConfig } from "./siteConfig";
import "./publicEvents.css";
import "./site.css";

const MAX_ON_HOME = 6;

/** Home: the events the platform is currently offering (nothing is hard-coded), plus membership / AGM notices. */
export default function HomePage({ navigateTo }) {
  const [events, setEvents] = useState(null);
  const [error, setError] = useState("");
  const [agm, setAgm] = useState(null);
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

  // The AGM notice is optional extra content: a failure here is silent.
  useEffect(() => {
    let cancelled = false;
    getSiteConfig()
      .then(({ config }) => !cancelled && setAgm(config?.agm || null))
      .catch(() => {});
    return () => {
      cancelled = true;
    };
  }, []);

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

        <AgmBanner agm={agm} navigateTo={navigateTo} />

        <section className="home-section" aria-labelledby="home-membership">
          <div className="home-membership">
            <h2 id="home-membership">{t("home.membership.title")}</h2>
            <p>{t("home.membership.text")}</p>
            <button type="button" className="button primary" onClick={() => navigateTo("/member/membership")}>{t("home.membership.cta")}</button>
          </div>
        </section>
      </main>
      <SiteFooter navigateTo={navigateTo} />
    </>
  );
}
