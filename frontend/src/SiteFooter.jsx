import { t } from "./i18n";
import "./site.css";
import { siteConfig } from "./siteConfig";

/** Footer shared by the home page and the public event pages: the organisation's site, policies, contact. */
export default function SiteFooter({ navigateTo }) {
  return (
    <footer className="site-footer">
      <div>
        © {siteConfig.name} ·{" "}
        <a href={siteConfig.orgUrl} target="_blank" rel="noopener noreferrer" aria-label={t("site.orgLinkAria", { org: siteConfig.orgName })}>
          {t("site.orgLink", { org: siteConfig.orgName })}
        </a>
      </div>
      <nav className="site-footer-links" aria-label="Footer">
        <button type="button" onClick={() => navigateTo("/events")}>{t("pub.nav.allEvents")}</button>
        <button type="button" onClick={() => navigateTo("/terms")}>{t("pub.nav.terms")}</button>
        <button type="button" onClick={() => navigateTo("/privacy")}>{t("pub.nav.privacy")}</button>
        <button type="button" onClick={() => navigateTo("/cancellation")}>{t("pub.nav.cancellation")}</button>
        {siteConfig.contactEmail && <a href={`mailto:${siteConfig.contactEmail}`}>{t("site.footer.contact")}</a>}
        <button type="button" onClick={() => navigateTo("/admin")}>{t("site.footer.adminLogin")}</button>
      </nav>
    </footer>
  );
}
