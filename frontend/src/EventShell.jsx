import Navbar from "./Navbar";
import { t } from "./i18n";
import "./publicEvents.css";

/** Common frame for the public event pages: site navigation, content, footer. */
export default function EventShell({ navigateTo, accent, children, className = "" }) {
  return (
    <>
      <Navbar navigateTo={navigateTo} />
      <main className={`pe-page ${className}`} style={accent ? { "--pe-accent": accent } : undefined} id="top">
        {children}
        <footer className="pe-footer">
          <button type="button" onClick={() => navigateTo("/events")}>{t("pub.nav.allEvents")}</button>
          <button type="button" onClick={() => navigateTo("/terms")}>{t("pub.nav.terms")}</button>
          <button type="button" onClick={() => navigateTo("/privacy")}>{t("pub.nav.privacy")}</button>
          <button type="button" onClick={() => navigateTo("/cancellation")}>{t("pub.nav.cancellation")}</button>
        </footer>
      </main>
    </>
  );
}

export function Loading() {
  return <p role="status">{t("pub.loading")}</p>;
}

export function ErrorBox({ message }) {
  return message ? <div className="error-banner" role="alert">{message}</div> : null;
}

export function StatusPill({ status }) {
  return <span className={`pe-pill pe-pill-${status}`}>{t(`pub.status.${status}`)}</span>;
}
