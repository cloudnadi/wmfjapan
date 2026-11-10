import { useEffect, useState } from "react";
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

  return (
    <>
      <Navbar navigateTo={navigateTo} />
      <main id="top" className="pe-page-home">
        <section className="home-hero" aria-labelledby="home-title">
          <h1 id="home-title">{siteConfig.shortTitle}</h1>
          <p>{t("home.lead")}</p>
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
            <>
              <ul className="pe-grid">
                {events.slice(0, MAX_ON_HOME).map((ev) => <EventCard key={ev.eventId} ev={ev} navigateTo={navigateTo} />)}
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
